import asyncio
import contextlib
import logging
import math
import os
import random
import time
import tomllib
from collections import Counter
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from functools import cache
from itertools import islice
from pathlib import Path
from typing import Protocol, assert_never

import discord
from discord.ext import commands, tasks

import ledger
from kernel.game import Game, PlayerId, choose, forfeit, over, play, start, winner
from kernel.rules import GAMES, Axis, Config, DegenerateError, Out, resolve
from kernel.words import Word, mint
from lexicon import Store

log = logging.getLogger("wge")
STATS: Counter[str] = Counter()


def _settings(env: Mapping[str, str]) -> tuple[set[int], float, float]:
    try:
        curators = {int(x) for x in env.get("CURATORS", "").split(",") if x.strip()}
    except ValueError as exc:
        raise SystemExit("CURATORS must be a comma-separated list of integer user IDs") from exc

    values: dict[str, float] = {}
    for name, default in (("IDLE_SECONDS", "3600"), ("PACE", "0.6")):
        try:
            value = float(env.get(name, default))
        except ValueError as exc:
            raise SystemExit(f"{name} must be a finite, non-negative number") from exc
        if not math.isfinite(value) or value < 0:
            raise SystemExit(f"{name} must be a finite, non-negative number")
        values[name] = value
    return curators, values["IDLE_SECONDS"], values["PACE"]


CURATORS, IDLE_SECONDS, PACE = _settings(os.environ)
MAX_PLAYERS = 16


@cache
def store() -> Store:
    return Store(Path(os.environ.get("WORDLIST", "wordlist.txt")))


RULES: dict[str, str] = tomllib.loads((Path(__file__).parent / "rules.toml").read_text("utf-8"))
if RULES.keys() != GAMES.keys():
    missing = GAMES.keys() - RULES.keys()
    extra = RULES.keys() - GAMES.keys()
    raise RuntimeError(
        f"rules.toml does not match games (missing={sorted(missing)}, extra={sorted(extra)})"
    )

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_mentions=discord.AllowedMentions.none(),
)


class Sender(Protocol):
    async def send(self, content: str, /) -> object: ...


@dataclass(slots=True)
class Base:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, kw_only=True)
    touched: float = field(default=0.0, kw_only=True)
    prompted: float = field(default=0.0, kw_only=True)
    ai: dict[PlayerId, bool] = field(default_factory=dict, kw_only=True)
    names: dict[PlayerId, str] = field(default_factory=dict, kw_only=True)


@dataclass(slots=True)
class Lobby(Base):
    key: str
    host: PlayerId
    players: list[PlayerId]

    @property
    def full(self) -> bool:
        return len(self.players) >= MAX_PLAYERS

    def add_player(self, player: PlayerId) -> bool:
        if player in self.players or self.full:
            return False
        self.players.append(player)
        return True

    def add_ai(self, hard: bool) -> PlayerId:
        if self.full:
            raise ValueError("lobby is full")
        pid = PlayerId(-(len(self.ai) + 1))  # lobby-local, always negative
        self.ai[pid] = hard
        self.names[pid] = "Sesquipedalian" if hard else "Novice"
        self.players.append(pid)
        return pid


@dataclass(slots=True)
class Live(Base):
    game: Game
    guild: int


type Session = Lobby | Live
SESSIONS: dict[int, Session] = {}


@asynccontextmanager
async def hold(cid: int) -> AsyncIterator[Session | None]:
    """Lock a session, yielding `None` if it was replaced while waiting."""
    s = SESSIONS.get(cid)
    if s is None:
        yield None
        return
    async with s.lock:
        yield s if SESSIONS.get(cid) is s else None


@asynccontextmanager
async def held[S: Session](cid: int, kind: type[S]) -> AsyncIterator[S | None]:
    async with hold(cid) as s:
        yield s if isinstance(s, kind) else None


def who(s: Session, p: PlayerId | None) -> str:
    if p is None:
        return "nobody"
    return s.names.get(p, "?") if p < 0 else f"<@{p}>"


def touch(s: Session) -> None:
    s.touched = time.monotonic()


def emoji(out: Out) -> str:
    match out:
        case Out.OK:
            return "\u2705" # check mark
        case Out.RETRY:
            return "\u27f3" # clockwise circle arrow
        case Out.REPEAT:
            return "\U0001f501" # clockwise rightwards and leftwards open circle arrows
        case Out.STRIKE:
            return "\u274c" # cross mark
        case Out.COMPLETE:
            return "\U0001f47b" # ghost
        case Out.DEADEND:
            return "\U0001f504" # anticlockwise downwards and upwards open circle arrows
    assert_never(out)


def move_emoji(g: Game, out: Out, w: Word) -> str:
    match g.cfg.label:
        case "One Letter at a Time":
            return "\u2705" if out is Out.OK else "\u274c"
        case "Shiritori":
            if out is Out.OK:
                return "\u2705"
            if out is Out.REPEAT:
                return "\U0001f501"
            return "\u274c"
        case "Three Thirds of a Ghost":
            if out is Out.COMPLETE or (out is Out.OK and len(w) > 3):
                return "\U0001f47b"
            if out is Out.OK:
                return "\u2705"
            return "\u274c"
    assert_never(g.cfg.label)


def news(out: Out, w: Word, pos: str) -> str | None:
    match out:
        case Out.OK | Out.STRIKE:
            return None
        case Out.COMPLETE:
            return f"**{w}** finishes a word, a third of a ghost. New letter: **{pos}**"
        case Out.DEADEND:
            return f"Nothing extends **{w}**. New letter: **{pos}**"
        case _:
            assert_never(out)


def sanctions(cfg: Config, counts: Mapping[Axis, int]) -> str:
    """Render two strikes and one third as `2s/1t`."""
    return "/".join(f"{counts.get(a, 0)}{a[0]}" for a in cfg.limits)


async def report(channel: Sender, s: Live, p: PlayerId, out: Out, w: Word) -> None:
    if (line := news(out, w, s.game.pos.text)) is not None:
        await channel.send(line)
    if p not in s.game.seat:
        await channel.send(f"{who(s, p)} is eliminated.")


async def finish(channel: Sender, cid: int, s: Live, *, reason: str | None = None) -> bool:
    """Finish a terminal game while the caller holds its session lock."""
    if SESSIONS.get(cid) is not s:
        return True
    g = s.game
    if reason is None and not over(g):
        return False
    win = None if reason is not None else winner(g)
    await asyncio.to_thread(ledger.record, s.guild, g.roster, win)
    SESSIONS.pop(cid, None)
    STATS["games_finished"] += 1
    await channel.send(
        reason
        or (f"{who(s, win)} wins!" if win is not None else "Stalemate, no legal continuation."),
    )
    return True


async def drive(channel: Sender, cid: int, s: Live) -> None:
    """Advance consecutive AIs under ``s.lock``."""
    while SESSIONS.get(cid) is s and s.game.seat and s.game.seat[0] in s.ai:
        if len(s.game.seat) > 1 and all(p in s.ai for p in s.game.seat):
            await finish(channel, cid, s, reason="Game over, no human players remain.")
            return
        if not s.game.cfg.rules.playable(s.game.lex, s.game.pos):
            loser = s.game.seat[0]
            s.game = forfeit(s.game, loser)
            await channel.send(f"{who(s, loser)} is stuck and is eliminated.")
            touch(s)
            if await finish(channel, cid, s):
                return
            continue
        p = s.game.seat[0]
        w = choose(s.game, s.ai[p])
        if w is None:
            s.game = forfeit(s.game, p)
            await channel.send(f"{who(s, p)} has no legal move and is eliminated.")
        else:
            s.game, out = play(s.game, p, w)
            STATS[out.value] += 1
            if out is Out.STRIKE:
                # Prevent a faulty retrying strategy from spinning.
                STATS["strategy_bug"] += 1
                log.error("strategy struck at %r", s.game.pos.text)
                s.game = forfeit(s.game, p)
            else:
                await channel.send(f"{who(s, p)} plays **{w}** {emoji(out)}")
                await asyncio.sleep(PACE)
                await report(channel, s, p, out, w)
        touch(s)
        if await finish(channel, cid, s):
            return


async def move(m: discord.Message, cid: int, s: Live) -> None:
    g = s.game
    if not g.seat or m.author.id != g.seat[0]:
        return
    w = mint(m.content.strip())
    if w is None or not g.cfg.rules.addressed(g.pos, w):
        return
    p = PlayerId(m.author.id)
    touch(s)
    s.game, out = play(g, p, w)
    STATS[out.value] += 1
    with contextlib.suppress(discord.HTTPException):
        await m.add_reaction(move_emoji(s.game, out, w))
    if not await finish(m.channel, cid, s):
        await drive(m.channel, cid, s)


async def begin(channel: Sender, cid: int, lob: Lobby, actor: PlayerId, guild: int) -> None:
    if actor != lob.host:
        await channel.send("Only the host can start.")
        return
    if len(lob.players) < 2:
        await channel.send("Need at least two players.")
        return
    try:
        game = start(GAMES[lob.key], store().lex, lob.players, f"{random.getrandbits(64):016x}")
    except DegenerateError as exc:
        await channel.send(f"Cannot deal an opening position: {exc}. The lobby stands.")
        return
    # Reuse the lock so the lobby-to-game transition stays atomic.
    live = Live(game=game, guild=guild, lock=lob.lock, ai=lob.ai, names=lob.names)
    SESSIONS[cid] = live
    touch(live)
    where = f" Opening word: **{game.pos.text}**" if game.pos.text else ""
    await channel.send(f"Go, {who(live, game.seat[0])} to play.{where}")
    await drive(channel, cid, live)


@bot.event
async def on_message(m: discord.Message) -> None:
    if m.author.bot:
        return
    if m.guild is None:
        await bot.process_commands(m)
        return
    cid = m.channel.id
    async with hold(cid) as s:
        match s:
            case Live():
                await move(m, cid, s)
            case Lobby() as lob:
                low = m.content.strip().casefold()
                author = PlayerId(m.author.id)
                if low == "join" and author not in lob.players:
                    if lob.add_player(author):
                        touch(lob)
                        await m.channel.send(f"{m.author.mention} joined ({len(lob.players)} in).")
                    else:
                        await m.channel.send(f"Lobby is full ({MAX_PLAYERS} players).")
                elif low == "start":
                    await begin(m.channel, cid, lob, author, m.guild.id)
            case None:
                pass
    await bot.process_commands(m)


@bot.command(aliases=["q"])
@commands.guild_only()
async def queue(ctx: commands.Context[commands.Bot], *, game: str = "oneletteratatime") -> None:
    """Open a lobby. Players type `join`, the host types `start`."""
    cid = ctx.channel.id
    if (existing := SESSIONS.get(cid)) is not None:
        kind = "game" if isinstance(existing, Live) else "lobby"
        await ctx.send(f"A {kind} is already open here. `!abort` to cancel it.")
        return
    key = resolve(game)
    if key is None:
        await ctx.send(f"Unknown game. Available: {', '.join(sorted(GAMES))}")
        return
    host = PlayerId(ctx.author.id)
    lob = Lobby(key=key, host=host, players=[host])
    SESSIONS[cid] = lob
    touch(lob)
    await ctx.send(
        f"Queueing **{GAMES[key].label}**. Type `join` to join, `start` to begin. "
        f"`!addbot easy|hard` adds an opponent.",
    )


@bot.command()
@commands.guild_only()
async def addbot(ctx: commands.Context[commands.Bot], level: str = "easy") -> None:
    """Add an AI opponent (easy | hard) to the open lobby."""
    async with held(ctx.channel.id, Lobby) as lob:
        if lob is None:
            await ctx.send("No open lobby. Use `!queue` first.")
            return
        if PlayerId(ctx.author.id) != lob.host:
            await ctx.send("Only the host can add bots.")
            return
        level = level.casefold()
        if level not in {"easy", "hard"}:
            await ctx.send("Usage: `!addbot easy|hard`")
            return
        if lob.full:
            await ctx.send(f"Lobby is full ({MAX_PLAYERS} players).")
            return
        pid = lob.add_ai(level == "hard")
        touch(lob)
        await ctx.send(f"Added **{lob.names[pid]}**.")


@bot.command()
@commands.guild_only()
async def leave(ctx: commands.Context[commands.Bot]) -> None:
    """Leave an open lobby. If the host leaves, the host passes on."""
    async with held(ctx.channel.id, Lobby) as lob:
        author = PlayerId(ctx.author.id)
        if lob is None or author not in lob.players:
            return
        lob.players.remove(author)
        humans = [p for p in lob.players if p > 0]
        if not humans:
            SESSIONS.pop(ctx.channel.id, None)
            await ctx.send("Lobby closed.")
        else:
            touch(lob)
            if author == lob.host:
                lob.host = humans[0]
                await ctx.send(f"{ctx.author.mention} left. {who(lob, lob.host)} is now host.")
            else:
                await ctx.send(f"{ctx.author.mention} left.")


@bot.command()
@commands.guild_only()
async def abort(ctx: commands.Context[commands.Bot]) -> None:
    """Cancel the lobby or game in this channel."""
    async with hold(ctx.channel.id) as s:
        author = PlayerId(ctx.author.id)
        match s:
            case Lobby() as lob if author == lob.host:
                SESSIONS.pop(ctx.channel.id, None)
                await ctx.send("Lobby cancelled.")
            case Live() as live if author in live.game.roster:
                SESSIONS.pop(ctx.channel.id, None)
                await ctx.send("Game cancelled.")
            case None:
                await ctx.send("Nothing to cancel.")
            case _:
                await ctx.send("Only the host or a player can abort.")


@bot.command(name="forfeit", aliases=["ff"])
@commands.guild_only()
async def forfeit_cmd(ctx: commands.Context[commands.Bot]) -> None:
    """Leave a game in progress."""
    cid = ctx.channel.id
    async with held(cid, Live) as s:
        author = PlayerId(ctx.author.id)
        if s is None or author not in s.game.seat:
            return
        s.game = forfeit(s.game, author)
        touch(s)
        await ctx.send(f"{ctx.author.mention} forfeits.")
        if not await finish(ctx.channel, cid, s):
            await drive(ctx.channel, cid, s)


@bot.command()
@commands.guild_only()
async def status(ctx: commands.Context[commands.Bot]) -> None:
    """Show the current position, turn order, and sanctions."""
    async with hold(ctx.channel.id) as s:
        match s:
            case Lobby() as lob:
                roster = ", ".join(who(lob, p) for p in lob.players)
                await ctx.send(
                    f"**{GAMES[lob.key].label}** lobby, {roster} (host {who(lob, lob.host)})",
                )
            case Live() as live:
                g = live.game
                order = " → ".join(
                    f"{who(live, p)}[{sanctions(g.cfg, g.counts[p])}]" for p in g.seat
                )
                await ctx.send(
                    f"**{g.cfg.label}**, position **{g.pos.text or '(empty)'}** "
                    f"(press {g.lex.span(g.pos.text).press})\n{order}",
                )
            case None:
                await ctx.send("Nothing running here. `!queue` to start.")


@bot.command(aliases=["c"])
@commands.cooldown(5, 60, commands.BucketType.user)
async def check(ctx: commands.Context[commands.Bot], *, word: str) -> None:
    """Check whether this is a word."""
    raw = word.strip()
    w = mint(raw)
    ok = w is not None and store().lex.span(w).exact
    await ctx.send(f"**{w or raw}** is {'a valid' if ok else 'not a'} word.")


@bot.command()
@commands.guild_only()
@commands.cooldown(3, 60, commands.BucketType.user)
async def hint(ctx: commands.Context[commands.Bot]) -> None:
    """Legal continuations at the current position. Rate-limited: it weakens play."""
    async with held(ctx.channel.id, Live) as s:
        if s is None:
            return
        g = s.game
        shown = 12
        sample = list(islice(g.cfg.rules.moves(g.lex, g.pos), shown + 1))
        more = " …" if len(sample) > shown else ""
        await ctx.send(f"`{' '.join(sample[:shown]) or '(none)'}`{more}")


@bot.command(hidden=True)
@commands.guild_only()
async def solve(ctx: commands.Context[commands.Bot]) -> None:
    """Debug helper: do not use it during a live game."""
    async with hold(ctx.channel.id) as s:
        if isinstance(s, Live):
            await ctx.send("`!solve` is disabled during live games.")
            return
    await ctx.send("No live game here.")


@bot.command()
async def rules(ctx: commands.Context[commands.Bot], *, game: str = "oneletteratatime") -> None:
    """Rules text for a game."""
    key = resolve(game)
    if key is None:
        await ctx.send(f"Unknown game. Available: {', '.join(sorted(GAMES))}")
        return
    await ctx.send(RULES[key])


@bot.command()
@commands.guild_only()
async def top(ctx: commands.Context[commands.Bot]) -> None:
    """Wins and games played, this server."""
    guild = ctx.guild
    if guild is None:
        return
    rows = await asyncio.to_thread(ledger.leaderboard, guild.id)
    body = "\n".join(f"{i}. <@{p}> — {w}/{n}" for i, (p, w, n) in enumerate(rows, 1))
    await ctx.send(body or "No games recorded yet.")


@bot.command()
async def diag(ctx: commands.Context[commands.Bot]) -> None:
    """Lexicon size, live sessions, and outcome counters."""
    lex = store().lex
    head = (
        f"lex={len(lex.words)} v{lex.version} sessions={len(SESSIONS)} "
        f"live={sum(isinstance(s, Live) for s in SESSIONS.values())}"
    )
    body = "\n".join(f"{k}={v}" for k, v in sorted(STATS.items()))
    await ctx.send(f"```\n{head}\n{body}\n```")


async def _curate(
    ctx: commands.Context[commands.Bot],
    words: str | None,
    *,
    removing: bool,
) -> None:
    if ctx.author.id not in CURATORS:
        return
    if words is None and ctx.message.attachments:
        words = (await ctx.message.attachments[0].read()).decode("utf-8", "ignore")
    raw = words.split() if words else []
    minted = [mint(x) for x in raw]
    valid = {w for w in minted if w is not None}
    rejected = [raw[i] for i, w in enumerate(minted) if w is None]
    if not valid:
        await ctx.send(f"Nothing to {'remove' if removing else 'add'}.")
        return
    have = set(store().lex.words)
    if removing:
        target = valid
        extra = [w for w in raw if (mw := mint(w)) is not None and mw not in have]
        lead = "Removed"
    else:
        target = valid
        extra = [w for w in raw if (mw := mint(w)) is not None and mw in have]
        lead = "Added"
    try:
        result = await asyncio.to_thread(
            store().mutate,
            add=() if removing else target,
            remove=target if removing else (),
        )
    except ValueError as exc:
        await ctx.send(f"Refused: {exc}. Lexicon unchanged.")
        return
    parts = [f"✅ {lead} {result.removed if removing else result.added} words."]
    if removing and extra:
        missing = ", ".join(f"`{w}`" for w in extra)
        parts.append(f"\u2139\uFE0F {len(extra)} words were not in the dict. {missing}")
    if not removing and extra:
        present = ", ".join(f"`{w}`" for w in extra)
        parts.append(f"\u2139\uFE0F {len(extra)} words were already in the dict: {present}")
    if not removing and rejected:
        invalid = ", ".join(f"`{w}`" for w in rejected)
        parts.append(f"\u2139\uFE0F {len(rejected)} invalid words: {invalid}")
    await ctx.send("\n".join(parts))


@bot.command(aliases=["a"])
async def add(ctx: commands.Context[commands.Bot], *, words: str | None = None) -> None:
    """Curator: add words. Accepts an attachment."""
    await _curate(ctx, words, removing=False)


@bot.command(aliases=["r"])
async def remove(ctx: commands.Context[commands.Bot], *, words: str | None = None) -> None:
    """Curator: remove words. Accepts an attachment."""
    await _curate(ctx, words, removing=True)


@bot.command()
async def reload(ctx: commands.Context[commands.Bot]) -> None:
    """Curator: re-read the wordlist from disk after an external edit."""
    if ctx.author.id not in CURATORS:
        return
    try:
        lex = await asyncio.to_thread(store().reload)
    except ValueError as exc:
        await ctx.send(f"Refused: {exc}. Lexicon unchanged.")
        return
    await ctx.send(
        f"Reloaded: {len(lex.words)} words (v{lex.version}). "
        "Games in progress keep their snapshot.",
    )


@bot.event
async def on_error(event: str, *_args: object, **_kw: object) -> None:
    STATS["errors"] += 1
    # discord.py calls this inside the active exception.
    log.error("event %s failed", event, exc_info=True)  # noqa: LOG014


@bot.event
async def on_command_error(ctx: commands.Context[commands.Bot], exc: Exception) -> None:
    match exc:
        case commands.CommandNotFound():
            return
        case commands.CommandOnCooldown():
            await ctx.send(f"Slow down, try again in {exc.retry_after:.0f}s.")
        case commands.NoPrivateMessage():
            await ctx.send("That only works in a server channel.")
        case commands.MissingRequiredArgument() | commands.BadArgument():
            sig = ctx.command.signature if ctx.command else ""
            await ctx.send(f"Usage: `!{ctx.command} {sig}`")
        case _:
            STATS["errors"] += 1
            log.error("command %s failed", ctx.command, exc_info=exc)
            with contextlib.suppress(discord.HTTPException):
                await ctx.send("Something went wrong; it has been logged.")


@tasks.loop(minutes=5)
async def sweep() -> None:
    """Retire abandoned sessions. A session whose lock is held is not idle."""
    for cid, s in list(SESSIONS.items()):
        if s.lock.locked() or time.monotonic() - s.touched < IDLE_SECONDS:
            continue
        async with hold(cid) as held_s:
            if held_s is None or time.monotonic() - held_s.touched < IDLE_SECONDS:
                continue
            SESSIONS.pop(cid, None)
            STATS["idle_expired"] += 1
        if (channel := bot.get_channel(cid)) is not None and isinstance(
            channel,
            discord.abc.Messageable,
        ):
            with contextlib.suppress(discord.HTTPException):
                await channel.send("Idle, cleared.")


@tasks.loop(seconds=5)
async def remind() -> None:
    """Nudge the current player after inactivity instead of every move."""
    now = time.monotonic()
    for cid, s in list(SESSIONS.items()):
        if not isinstance(s, Live) or s.lock.locked() or not s.game.seat:
            continue
        if now - s.touched < 30 or s.prompted >= s.touched:
            continue
        async with hold(cid) as held_s:
            if not isinstance(held_s, Live) or not held_s.game.seat:
                continue
            if now - held_s.touched < 30 or held_s.prompted >= held_s.touched:
                continue
            if (channel := bot.get_channel(cid)) is None or not isinstance(
                channel,
                discord.abc.Messageable,
            ):
                continue
            with contextlib.suppress(discord.HTTPException):
                await channel.send(f"{who(held_s, held_s.game.seat[0])} to play — **{held_s.game.pos.text}**")
            held_s.prompted = now


@bot.event
async def on_ready() -> None:
    if not sweep.is_running():
        sweep.start()
    if not remind.is_running():
        remind.start()
    log.info("ready as %s, lexicon %d words", bot.user, len(store().lex.words))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store()
    ledger.init()
    if not CURATORS:
        log.warning("CURATORS unset — !add/!remove/!reload are disabled")
    bot.run(os.environ["DISCORD_TOKEN"])


if __name__ == "__main__":
    main()
