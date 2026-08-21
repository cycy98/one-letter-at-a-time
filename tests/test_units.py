import stat
import tempfile
import unittest
import os
from dataclasses import replace
from functools import partial
from pathlib import Path
from random import Random
from typing import cast

import bot as bot_module
from kernel.game import (
    Game,
    PlayerId,
    Seat,
    choose,
    forfeit,
    over,
    play,
    retire,
    rotate,
    stalled,
    start,
    winner,
)
from kernel.rules import (
    GAMES,
    ROUND_OVER,
    Axis,
    Chained,
    Config,
    DegenerateError,
    Out,
    Pos,
    Prefixed,
)
from kernel.words import Lex, Word, admissible, mint, normalise
from lexicon import Store
from tests.corpora import BRANCH, CHAINY, DEAD, LEX, ONE_SEED, PROBES, THIN


def w(s: str) -> Word:
    got = mint(s)
    assert got is not None, s
    return got


def strike(g: Game) -> Word:
    return w(g.pos.text[-1] + "zzz") if isinstance(g.cfg.rules, Chained) else w(g.pos.text + "z")


MINT: tuple[tuple[str, str | None], ...] = (
    ("cat", "cat"),
    ("don't", "don't"),
    ("co-op", "co-op"),
    ("ANT", "ant"),
    ("naïve", "naive"),
    ("café", "cafe"),
    ("coöp", "coop"),
    ("DON\u2019T", "don't"),
    ("école", "ecole"),
    ("co\u2010op", None),  # a Unicode hyphen is not folded, unlike a Unicode apostrophe
    ("a b", None),
    ("42", None),
    ("", None),
    ("a" * 100, "a" * 100),
    ("a" * 101, None),
)

EXPECTED_RETRY = {"oneletteratatime": True, "threethirdsofaghost": True, "shiritori": False}

CLASSIFY: tuple[tuple[str, str, Out], ...] = (
    ("oneletteratatime", "an", Out.OK),
    ("oneletteratatime", "ant", Out.OK),
    ("oneletteratatime", "note", Out.DEADEND),
    ("oneletteratatime", "anted", Out.DEADEND),
    ("threethirdsofaghost", "an", Out.OK),
    ("threethirdsofaghost", "ant", Out.OK),  # free: exact, but not past free_len
    ("threethirdsofaghost", "note", Out.COMPLETE),
    ("threethirdsofaghost", "anted", Out.COMPLETE),  # completion outranks the dead end
    ("shiritori", "cat", Out.OK),
)


class Alphabet(unittest.TestCase):
    def test_mint_normalises_and_filters(self) -> None:
        for raw, want in MINT:
            with self.subTest(raw=raw):
                got = mint(raw)
                assert got == want
                if got is not None:
                    assert admissible(got)
                    assert normalise(got) == got

    def test_normalise_is_idempotent(self) -> None:
        for raw, _ in MINT:
            with self.subTest(raw=raw):
                once = normalise(raw)
                assert normalise(once) == once


class Settings(unittest.TestCase):
    def test_settings_are_parsed(self) -> None:
        assert bot_module._settings(
            {"CURATORS": "1, 2", "IDLE_SECONDS": "0", "PACE": "1.25"},
        ) == ({1, 2}, 0.0, 1.25)

    def test_invalid_settings_exit_with_the_setting_name(self) -> None:
        cases = (
            ({"CURATORS": "one"}, "CURATORS"),
            ({"IDLE_SECONDS": "nan"}, "IDLE_SECONDS"),
            ({"PACE": "-1"}, "PACE"),
            ({"PACE": "soon"}, "PACE"),
        )
        for env, name in cases:
            with self.subTest(env=env), self.assertRaisesRegex(SystemExit, name):
                bot_module._settings(env)


class Lexicon(unittest.TestCase):
    def test_views_match_linear_scan(self) -> None:
        for p in PROBES:
            hits = tuple(x for x in LEX.words if x.startswith(p))
            s = LEX.span(p)
            with self.subTest(p=p):
                assert LEX.words[s.lo : s.hi] == hits
                assert s.press == len(hits)
                assert s.exact == (p in LEX.words)
                assert s.live == bool(hits)
                assert s.can_extend == any(x != p for x in hits)
                assert LEX.slice(p) == hits
                assert LEX.next_letters(p) == tuple(
                    sorted({x[len(p)] for x in hits if len(x) > len(p)}),
                )

    def test_direct_construction_rejects_broken_invariants(self) -> None:
        for words in (
            (Word("bat"), Word("ant")),  # unsorted
            (Word("ant"), Word("ant")),  # duplicated
            (Word("Cat"),),  # inadmissible
            (Word("a b"),),
        ):
            with (
                self.subTest(words=words),
                self.assertRaisesRegex(ValueError, "admissible, sorted, and unique"),
            ):
                Lex(words)


class Seating(unittest.TestCase):
    def test_rotate_moves_the_head_to_the_tail(self) -> None:
        seat = tuple(PlayerId(i) for i in (5, 3, 9))
        assert rotate(seat) == (PlayerId(3), PlayerId(9), PlayerId(5))

    def test_retire_preserves_order(self) -> None:
        # Unsorted input distinguishes filtering from sorting.
        for n in range(6):
            seat: Seat = tuple(PlayerId(i) for i in (5, 3, 9, 1, 7)[:n])
            with self.subTest(n=n):
                for target in (*seat, PlayerId(99)):
                    out = retire(seat, target)
                    assert target not in out
                    assert len(out) == n - (target in seat)
                    kept = [seat.index(q) for q in out]
                    assert kept == sorted(kept)

    def test_forfeit_retires_head_or_not(self) -> None:
        g = start(GAMES["shiritori"], LEX, [PlayerId(i) for i in (1, 2, 3)], "beef" * 4)
        g = replace(g, seat=(PlayerId(1), PlayerId(2), PlayerId(3)))
        assert forfeit(g, PlayerId(1)).seat == (PlayerId(2), PlayerId(3))
        assert forfeit(g, PlayerId(3)).seat == (PlayerId(1), PlayerId(2))
        assert forfeit(g, PlayerId(99)).seat == g.seat


class Legality(unittest.TestCase):
    def test_addressed_and_valid_are_separate_gates(self) -> None:
        prefix, chain = Pos("an"), Pos(w("on"), frozenset({w("on"), w("no")}))
        for rules, pos, raw, addressed, valid in (
            (Prefixed(), prefix, "ant", True, True),
            (Prefixed(), prefix, "anz", True, False),  # addressed, invalid: a strike
            (Prefixed(), prefix, "lol", False, False),  # not a move at all
            (Prefixed(), prefix, "ante", False, True),  # two letters on
            (Prefixed(), prefix, "a", False, True),
            (Chained(), chain, "not", True, True),
            (Chained(), chain, "dog", False, True),  # wrong letter: ignored
            (Chained(), chain, "no", True, False),  # a repeat: a retry
        ):
            with self.subTest(rules=type(rules).__name__, raw=raw):
                assert rules.addressed(pos, w(raw)) is addressed
                assert rules.valid(LEX, pos, w(raw)) is valid


class Transition(unittest.TestCase):
    def test_opening_by_rule(self) -> None:
        for key, cfg in GAMES.items():
            g = start(cfg, LEX, [PlayerId(1), PlayerId(2)], "beef" * 4)
            with self.subTest(game=key):
                if isinstance(cfg.rules, Chained):
                    assert g.pos.text in g.pos.used
                    assert cfg.rules.playable(g.lex, g.pos)
                else:
                    assert g.pos.text == ""

    def test_strike_retries_by_rule(self) -> None:
        for key, cfg in GAMES.items():
            g = start(cfg, LEX, [PlayerId(1), PlayerId(2)], "beef" * 4)
            mover = g.seat[0]
            g, out = play(g, mover, strike(g))
            with self.subTest(game=key):
                assert out is (Out.RETRY if key == "shiritori" else Out.STRIKE)
                assert cfg.rules.retry_on_strike is EXPECTED_RETRY[key]
                assert g.seat[0] == mover

    def test_shiritori_retry_keeps_the_turn(self) -> None:
        g = start(GAMES["shiritori"], LEX, [PlayerId(1), PlayerId(2)], "beef" * 4)
        mover = g.seat[0]
        g2, out = play(g, mover, w(g.pos.text[-1] + "zzz"))
        assert out is Out.RETRY
        assert g2.seat[0] == mover
        assert g2.pos == g.pos

    def test_shiritori_repeat_marks_the_repeat(self) -> None:
        g = start(GAMES["shiritori"], LEX, [PlayerId(1), PlayerId(2)], "beef" * 4)
        mover = g.seat[0]
        repeat = next(iter(GAMES["shiritori"].rules.moves(LEX, g.pos)))
        g2, out = play(g, mover, repeat)
        assert out is Out.OK
        repeater = g2.seat[0]
        g3, out = play(g2, repeater, repeat)
        assert out is Out.REPEAT
        assert repeater not in g3.seat
        assert g3.pos == g2.pos

    def test_classify_maps_position_to_outcome(self) -> None:
        for key, text, want in CLASSIFY:
            with self.subTest(game=key, text=text):
                assert GAMES[key].rules.classify(LEX, Pos(text)) is want

    def test_round_over_restarts(self) -> None:
        for key, text, want in CLASSIFY:
            if want not in ROUND_OVER:
                continue
            cfg = GAMES[key]
            g = start(cfg, LEX, [PlayerId(1), PlayerId(2)], "cafe" * 4)
            g, out = play(replace(g, pos=Pos(text[:-1])), g.seat[0], w(text))
            with self.subTest(game=key, text=text):
                assert out is want
                assert len(g.pos.text) == 1
                assert cfg.rules.playable(g.lex, g.pos)

    def test_limit_retires_player(self) -> None:
        p, q = PlayerId(1), PlayerId(2)
        for key, cfg in GAMES.items():
            if key == "shiritori":
                continue
            for axis, limit in cfg.limits.items():
                g = start(cfg, LEX, [p, q], "cafe" * 4)
                for i in range(limit):
                    g = replace(g, seat=(p, q))
                    if axis is Axis.THIRD:
                        g, _ = play(replace(g, pos=Pos("not")), p, w("note"))
                    else:
                        g, _ = play(g, p, strike(g))
                    with self.subTest(game=key, axis=axis, charge=i + 1):
                        assert (p in g.seat) is (i + 1 < limit)
                with self.subTest(game=key, axis=axis):
                    assert g.counts[p][axis] == limit

    def test_charges_only_scored_axes(self) -> None:
        for key, cfg in GAMES.items():
            g = start(cfg, LEX, [PlayerId(1), PlayerId(2)], "cafe" * 4)
            mover = g.seat[0]
            g, _ = play(g, mover, strike(g))
            with self.subTest(game=key):
                assert set(g.counts[mover]) <= set(cfg.limits)

        g = start(GAMES["oneletteratatime"], LEX, [PlayerId(1), PlayerId(2)], "cafe" * 4)
        mover = g.seat[0]
        g, out = play(replace(g, pos=Pos("ante")), mover, w("anted"))
        assert out is Out.DEADEND
        assert Axis.THIRD not in g.counts[mover]

    def test_winner_and_over(self) -> None:
        g = start(GAMES["shiritori"], LEX, [PlayerId(1), PlayerId(2)], "beef" * 4)
        live = replace(g, pos=Pos(w("cat")))
        stuck = replace(g, pos=Pos(w("cat"), frozenset(LEX.slice("t"))))
        assert not stalled(live)
        assert stalled(stuck)
        for n in range(4):
            seat: Seat = tuple(PlayerId(7 + i) for i in range(n))
            with self.subTest(n=n):
                assert winner(replace(live, seat=seat)) == (PlayerId(7) if n == 1 else None)
                assert over(replace(live, seat=seat)) is (n <= 1)
                assert over(replace(stuck, seat=seat)) is (n <= 1)


class Difficulty(unittest.TestCase):
    def test_choose_optimises_room(self) -> None:
        olaat, shiritori = GAMES["oneletteratatime"], GAMES["shiritori"]
        cases: list[tuple[Config, Lex, Pos, tuple[Word, Word] | None]] = [
            (olaat, BRANCH, Pos(text), None) for text in ("b", "ba", "be", "bi")
        ]
        cases += [
            (
                shiritori,
                CHAINY,
                Pos(Word("xa"), frozenset({Word("xa")})),
                (Word("ad"), Word("ab")),
            ),
            (shiritori, LEX, Pos(w("cat"), frozenset(LEX.slice("t"))), None),
        ]
        for cfg, lex, pos, pins in cases:
            g = replace(start(cfg, lex, [PlayerId(1), PlayerId(2)], "cafe" * 4), pos=pos)
            opts = list(cfg.rules.moves(lex, pos))
            hard, easy = choose(g, hard=True), choose(g, hard=False)
            with self.subTest(game=cfg.label, pos=pos.text):
                if not opts:
                    assert hard is None
                    assert easy is None
                    continue
                assert hard is not None
                assert easy is not None
                room = partial(cfg.rules.room, lex, pos)
                assert len({room(m) for m in opts}) > 1, "position does not branch"
                assert room(easy) == max(map(room, opts))
                assert len(hard) >= len(easy)
                if pins is not None:
                    assert (hard, easy) == pins


class Dealers(unittest.TestCase):
    def test_dealer_avoids_dead_positions(self) -> None:
        for rules, lex, pin in (
            (Prefixed(), THIN, "b"),
            (Chained(), ONE_SEED, "ba"),
        ):
            pos = rules.restart(lex, Random(0))
            with self.subTest(rules=type(rules).__name__):
                assert rules.playable(lex, pos)
                assert pos.text == pin

        for rules in (Prefixed(), Chained()):
            for i in range(10):
                pos = rules.restart(LEX, Random(i))
                with self.subTest(rules=type(rules).__name__, seed=i):
                    assert rules.playable(LEX, pos)

    def test_dealer_raises_without_viable_seed(self) -> None:
        for rules, lex in ((Prefixed(), DEAD), (Chained(), Lex(()))):
            with (
                self.subTest(rules=type(rules).__name__),
                self.assertRaises(DegenerateError),
            ):
                rules.restart(lex, Random(0))


class Persistence(unittest.TestCase):
    def store(self, body: str, name: str = "w.txt") -> Store:
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / name
        path.write_text(body, encoding="utf-8")
        return Store(path)

    def test_degenerate_wordlist_rejected_at_startup(self) -> None:
        for body, why in (
            ("", "at least two"),
            ("solo", "at least two"),
            ("!!! ???", "at least two"),
            ("a i", "extendable opening letter"),
        ):
            with self.subTest(body=body), self.assertRaisesRegex(SystemExit, why):
                self.store(body)

    def test_refused_rebuild_preserves_snapshot(self) -> None:
        s = self.store("dog cat ant")
        before, on_disk = s.lex, s.path.read_text()
        with self.assertRaisesRegex(ValueError, "at least two admissible words"):
            s.mutate(remove=[w("dog"), w("cat")])
        assert s.lex is before
        assert s.path.read_text() == on_disk

        s = self.store("dog cat ant")
        before = s.lex
        s.path.write_text("solo", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "at least two admissible words"):
            s.reload()
        assert s.lex is before

    def test_published_mutation_is_sorted_and_preserves_snapshots(self) -> None:
        for name in ("w.txt", "words.tmp"):  # the destination must not steer the temp file
            with self.subTest(name=name):
                s = self.store("dog cat ant", name)
                s.path.chmod(0o640)
                held = s.lex
                s.mutate(add=[w("bee")])
                assert list(s.lex.words) == ["ant", "bee", "cat", "dog"]
                assert s.path.read_text().split() == ["ant", "bee", "cat", "dog"]
                if os.name != "nt":
                    assert stat.S_IMODE(s.path.stat().st_mode) == 0o640

                s.mutate(add=[w("elk")])  # a delta, not a snapshot: both edits survive
                assert list(s.lex.words) == ["ant", "bee", "cat", "dog", "elk"]

                assert held.words == ("ant", "cat", "dog")
                assert held is not s.lex
                assert s.lex.version == held.version + 2

    def test_reload_reads_the_file(self) -> None:
        s = self.store("dog cat ant")
        s.path.write_text("ant bee cat", encoding="utf-8")
        assert s.reload().words == ("ant", "bee", "cat")


class Regressions(unittest.TestCase):
    def test_elimination_preserves_next_turn(self) -> None:
        players = [PlayerId(1), PlayerId(2), PlayerId(3)]
        g = start(GAMES["threethirdsofaghost"], LEX, players, "cafe" * 4)
        g = replace(
            g,
            pos=Pos("not"),
            counts={**g.counts, PlayerId(1): {Axis.THIRD: 2}},
        )
        g, out = play(g, PlayerId(1), w("note"))
        assert out is Out.COMPLETE
        assert PlayerId(1) not in g.seat
        assert g.seat[0] == PlayerId(2), "the eliminated player's neighbour was skipped"


class Preconditions(unittest.TestCase):
    def test_start_requires_two_unique_players(self) -> None:
        for players in ([], [PlayerId(1)], [PlayerId(1), PlayerId(1)]):
            with self.subTest(players=players), self.assertRaisesRegex(ValueError, "two unique"):
                start(GAMES["oneletteratatime"], LEX, players, "cafe" * 4)

    def test_game_mappings_are_immutable(self) -> None:
        p = PlayerId(1)
        g = start(GAMES["oneletteratatime"], LEX, [p, PlayerId(2)], "cafe" * 4)
        with self.assertRaises(TypeError):
            cast("dict[PlayerId, dict[Axis, int]]", g.counts)[p] = {}
        with self.assertRaises(TypeError):
            cast("dict[Axis, int]", g.counts[p])[Axis.STRIKE] = 1
        with self.assertRaises(TypeError):
            cast("dict[Axis, int]", g.cfg.limits)[Axis.THIRD] = 3

    def test_play_requires_head(self) -> None:
        g = start(
            GAMES["oneletteratatime"],
            LEX,
            [PlayerId(1), PlayerId(2), PlayerId(3)],
            "cafe" * 4,
        )
        g = replace(g, seat=(PlayerId(1), PlayerId(2), PlayerId(3)))
        play(g, PlayerId(1), w("a"))

        for seat, mover, why in (
            (g.seat, PlayerId(2), "seated, not to play"),
            (g.seat, PlayerId(99), "never in the roster"),
            ((PlayerId(1), PlayerId(2)), PlayerId(3), "retired, still in the roster"),
            ((), PlayerId(1), "nobody is seated"),
        ):
            with self.subTest(why=why), self.assertRaises(ValueError):
                play(replace(g, seat=seat), mover, w("a"))
