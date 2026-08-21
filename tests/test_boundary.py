import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Literal, cast
from unittest.mock import patch

import bot as bot_module
import ledger
from bot import MAX_PLAYERS, Live, Lobby
from kernel.game import PlayerId, forfeit, start
from kernel.rules import GAMES
from lexicon import Store
from tests.corpora import LEX

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class Chan:
    def __init__(self, pause: tuple[asyncio.Event, asyncio.Event] | None = None) -> None:
        self.sent: list[str] = []
        self.pause = pause

    async def send(self, content: str) -> None:
        self.sent.append(content)
        if self.pause is not None and " plays " in content:
            reached, release = self.pause
            reached.set()
            await release.wait()


class Ctx:
    def __init__(self, author: PlayerId) -> None:
        self.author = SimpleNamespace(id=author, mention=f"<@{author}>")
        self.channel = SimpleNamespace(id=1)
        self.sent: list[str] = []

    async def send(self, content: str) -> None:
        self.sent.append(content)


class NotifyingLock(asyncio.Lock):
    def __init__(self, waiting: asyncio.Event) -> None:
        super().__init__()
        self.waiting = waiting

    async def acquire(self) -> Literal[True]:
        if self.locked():
            self.waiting.set()
        return await super().acquire()


class Boundary(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        bot_module.SESSIONS.clear()
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "wordlist.txt"
        path.write_text("\n".join(LEX.words), encoding="utf-8")
        lexicon = Store(path)
        self.records: list[tuple[object, ...]] = []
        self.enterContext(patch.object(bot_module, "store", lambda: lexicon))
        self.enterContext(patch.object(bot_module, "PACE", 0.0))
        self.enterContext(patch.object(ledger, "record", lambda *a: self.records.append(a)))

    def live(self, seat: tuple[PlayerId, ...], ai: dict[PlayerId, bool]) -> Live:
        players = list(seat)
        if len(players) < 2:
            players.append(PlayerId(-999))
        g = start(GAMES["shiritori"], LEX, players, "beef" * 4)
        g = replace(g, seat=seat)
        s = Live(game=g, guild=9, ai=ai, names={p: f"Bot{-p}" for p in ai})
        bot_module.SESSIONS[1] = s
        return s

    async def test_concurrent_command_finishes_once(self) -> None:
        human, ai = PlayerId(1), PlayerId(-1)
        s = self.live((ai, human), {ai: False})
        reached, release, waiting = asyncio.Event(), asyncio.Event(), asyncio.Event()
        s.lock = NotifyingLock(waiting)
        chan = Chan((reached, release))

        async def driver() -> None:
            async with bot_module.held(1, Live) as held:
                if held is not None:
                    await bot_module.drive(chan, 1, held)

        async def quitter() -> None:
            async with bot_module.held(1, Live) as held:
                if held is None:
                    return
                held.game = forfeit(held.game, human)
                await chan.send("forfeits")
                await bot_module.finish(chan, 1, held)

        driver_task = asyncio.create_task(driver())
        await reached.wait()
        quitter_task = asyncio.create_task(quitter())
        await waiting.wait()
        release.set()
        await asyncio.gather(driver_task, quitter_task)
        assert len(self.records) == 1, "the game was recorded more than once"
        assert sum("wins!" in m for m in chan.sent) == 1, chan.sent
        assert 1 not in bot_module.SESSIONS

    async def test_finish_is_idempotent(self) -> None:
        s = self.live((PlayerId(-1),), {PlayerId(-1): False})
        chan = Chan()
        assert await bot_module.finish(chan, 1, s)
        assert await bot_module.finish(chan, 1, s), "a second call must be a no-op"
        assert len(self.records) == 1

    async def test_bot_only_game_ends_without_driving_forever(self) -> None:
        one, two = PlayerId(-1), PlayerId(-2)
        game = start(GAMES["oneletteratatime"], LEX, [one, two], "beef" * 4)
        s = Live(
            game=game,
            guild=9,
            ai={one: False, two: True},
            names={one: "Novice", two: "Sesquipedalian"},
        )
        bot_module.SESSIONS[1] = s
        chan = Chan()

        await bot_module.drive(chan, 1, s)

        assert chan.sent == ["Game over, no human players remain."]
        assert len(self.records) == 1
        assert self.records[0][2] is None
        assert 1 not in bot_module.SESSIONS

    async def test_record_failure_preserves_terminal_session(self) -> None:
        s = self.live((PlayerId(-1),), {PlayerId(-1): False})
        chan = Chan()

        def fail(*_args: object) -> None:
            raise RuntimeError("database unavailable")

        self.enterContext(patch.object(ledger, "record", fail))
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            await bot_module.finish(chan, 1, s)
        assert bot_module.SESSIONS[1] is s
        assert chan.sent == []

    async def test_hold_rejects_a_replaced_session(self) -> None:
        first = self.live((PlayerId(1), PlayerId(2)), {})
        seen: list[object] = []
        waiting = asyncio.Event()

        async def waiter() -> None:
            async with bot_module.hold(1) as held:
                seen.append(held)

        first.lock = NotifyingLock(waiting)
        await first.lock.acquire()
        task = asyncio.create_task(waiter())
        await waiting.wait()
        replacement = self.live((PlayerId(3), PlayerId(4)), {})
        first.lock.release()
        await task

        assert seen == [None]
        assert bot_module.SESSIONS[1] is replacement

    def test_lobby_enforces_capacity(self) -> None:
        players = [PlayerId(i) for i in range(1, MAX_PLAYERS + 1)]
        lob = Lobby(key="shiritori", host=players[0], players=players)
        assert lob.full
        assert not lob.add_player(PlayerId(MAX_PLAYERS + 1))
        with self.assertRaisesRegex(ValueError, "full"):
            lob.add_ai(hard=False)

    async def test_host_validates_bot_level(self) -> None:
        host = PlayerId(1)
        lob = Lobby(key="shiritori", host=host, players=[host, PlayerId(2)])
        bot_module.SESSIONS[1] = lob
        callback = cast(
            "Callable[[object, str], Awaitable[None]]",
            bot_module.addbot.callback,
        )

        outsider = Ctx(PlayerId(2))
        await callback(outsider, "hard")
        assert outsider.sent == ["Only the host can add bots."]
        assert not lob.ai

        owner = Ctx(host)
        await callback(owner, "heroic")
        assert owner.sent == ["Usage: `!addbot easy|hard`"]
        assert not lob.ai

        await callback(owner, "hard")
        assert list(lob.ai.values()) == [True]

    async def test_leave_refreshes_lobby(self) -> None:
        host, leaver = PlayerId(1), PlayerId(2)
        lob = Lobby(key="shiritori", host=host, players=[host, leaver])
        bot_module.SESSIONS[1] = lob
        callback = cast("Callable[[object], Awaitable[None]]", bot_module.leave.callback)

        with patch.object(bot_module, "touch", lambda session: setattr(session, "touched", 123.0)):
            await callback(Ctx(leaver))
        assert lob.touched == 123.0

    async def test_lobby_lock_survives_transition(self) -> None:
        host = PlayerId(1)
        lob = Lobby(key="shiritori", host=host, players=[host, PlayerId(2)])
        bot_module.SESSIONS[1] = lob
        chan = Chan()
        async with bot_module.hold(1) as s:
            assert isinstance(s, Lobby)
            await bot_module.begin(chan, 1, s, host, 9)
            live = bot_module.SESSIONS[1]
            assert isinstance(live, Live)
            assert live.lock is lob.lock
            assert live.lock.locked()
