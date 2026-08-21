import unittest
from dataclasses import replace

from kernel.game import Game, PlayerId, play, start
from kernel.rules import GAMES, Out
from kernel.words import Word
from tests.corpora import LEX


def run(seed: str) -> tuple[Game, tuple[Out, ...]]:
    g = start(GAMES["threethirdsofaghost"], LEX, [PlayerId(1), PlayerId(2)], seed)
    outs: list[Out] = []
    for raw in ("a", "an", "ant", "ante"):
        g, out = play(g, g.seat[0], Word(raw))
        outs.append(out)
    return g, tuple(outs)


class Replay(unittest.TestCase):
    def test_same_seed_same_game(self) -> None:
        a, outs = run("ab" * 8)
        b, _ = run("ab" * 8)
        assert outs[-1] is Out.COMPLETE
        assert a == b

    def test_different_seed_different_game(self) -> None:
        a, _ = run("ab" * 8)
        c, _ = run("cd" * 8)
        assert replace(a, seed="") != replace(c, seed="")

    def test_earlier_state_is_unchanged(self) -> None:
        g = start(GAMES["shiritori"], LEX, [PlayerId(1), PlayerId(2)], "beef" * 4)
        first = g
        for _ in range(3):
            g, _ = play(g, g.seat[0], next(g.cfg.rules.moves(g.lex, g.pos)))
        assert first.turn == 0
        assert len(first.pos.used) == 1
        assert first.pos != g.pos
