import unittest
from random import Random

from kernel.game import Game, PlayerId, play, start
from kernel.rules import GAMES
from kernel.words import Word, mint
from tests.corpora import LEX

JUNK = ("lol", "gg", "wait", "zzq", "")


def pool(g: Game) -> list[Word]:
    good = list(g.cfg.rules.moves(g.lex, g.pos))
    junk = [w for w in (mint(x) for x in (*JUNK, g.pos.text + "q")) if w is not None]
    return good * 3 + junk if good else junk


class Fuzz(unittest.TestCase):
    RUNS_PER_GAME = 32
    TURNS = 60

    def invariants(self, g: Game, before: int) -> None:
        assert len(g.seat) in (before, before - 1), "the seat changed by more than one"
        assert len(set(g.seat)) == len(g.seat), "duplicate in seat"
        assert set(g.seat) <= set(g.roster), "seat escaped the roster"
        for p in g.seat:
            for axis, limit in g.cfg.limits.items():
                assert g.counts[p].get(axis, 0) < limit, f"{p} seated past {axis}"

    def test_invariants_hold_after_every_transition(self) -> None:
        rng = Random(20260817)
        for key, cfg in GAMES.items():
            for run in range(self.RUNS_PER_GAME):
                players = [PlayerId(n) for n in range(1, rng.randint(2, 4) + 1)]
                g = start(cfg, LEX, players, f"{key}:{run}")
                with self.subTest(game=key, run=run):
                    for _ in range(self.TURNS):
                        if len(g.seat) <= 1 or (
                            cfg.rules.may_stall and not cfg.rules.playable(g.lex, g.pos)
                        ):
                            break
                        before = len(g.seat)
                        g, _ = play(g, g.seat[0], rng.choice(pool(g)))
                        self.invariants(g, before)

    def test_chain_terminates(self) -> None:
        cfg = GAMES["shiritori"]
        g = start(cfg, LEX, [PlayerId(1), PlayerId(2)], "beef" * 4)
        for _ in range(len(LEX.words)):
            if len(g.seat) <= 1 or not cfg.rules.playable(g.lex, g.pos):
                break
            move = next(cfg.rules.moves(g.lex, g.pos))
            g, _ = play(g, g.seat[0], move)
        assert len(g.seat) <= 1 or not cfg.rules.playable(g.lex, g.pos)
