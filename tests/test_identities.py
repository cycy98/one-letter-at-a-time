import unittest

from kernel.game import legal
from kernel.rules import GAMES, Config, Pos
from kernel.words import Word
from tests.corpora import LEX

ALPHABET = "abcdefghijklmnopqrstuvwxyz'-"
CONFIGS = (GAMES["oneletteratatime"], GAMES["shiritori"])


def positions(cfg: Config) -> list[Pos]:
    if cfg.rules.may_stall:
        # Include a used continuation to exercise filtering.
        return [
            Pos(Word("cat"), frozenset({Word("cat"), Word("to")})),
            Pos(Word("not"), frozenset()),
        ]
    return [Pos(""), Pos("a"), Pos("an"), Pos("ante"), Pos("not")]


class Identities(unittest.TestCase):
    def test_every_generated_move_is_legal(self) -> None:
        for cfg in CONFIGS:
            for pos in positions(cfg):
                for m in cfg.rules.moves(LEX, pos):
                    with self.subTest(game=cfg.label, pos=pos.text, move=m):
                        assert legal(cfg, LEX, pos, m)

    def test_moves_are_complete(self) -> None:
        for cfg in CONFIGS:
            for pos in positions(cfg):
                candidates = set(LEX.words) | {Word(pos.text + c) for c in ALPHABET}
                want = {c for c in candidates if legal(cfg, LEX, pos, c)}
                with self.subTest(game=cfg.label, pos=pos.text):
                    assert set(cfg.rules.moves(LEX, pos)) == want

    def test_playable_agrees_with_moves(self) -> None:
        for cfg in CONFIGS:
            for pos in positions(cfg):
                with self.subTest(game=cfg.label, pos=pos.text):
                    assert cfg.rules.playable(LEX, pos) == bool(list(cfg.rules.moves(LEX, pos)))

    def test_moves_are_ordered(self) -> None:
        for cfg in CONFIGS:
            for pos in positions(cfg):
                got = list(cfg.rules.moves(LEX, pos))
                with self.subTest(game=cfg.label, pos=pos.text):
                    assert got == sorted(got)

    def test_chain_room_counts_unused_continuations(self) -> None:
        cfg = GAMES["shiritori"]
        for pos in positions(cfg):
            moves = list(cfg.rules.moves(LEX, pos))
            for move in moves:
                used = pos.used | {move}
                want = sum(word not in used for word in LEX.slice(move[-1]))
                with self.subTest(pos=pos.text, move=move):
                    assert cfg.rules.room(LEX, pos, move) == want
