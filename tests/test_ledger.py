import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import ledger
from kernel.game import PlayerId


class Leaderboard(unittest.TestCase):
    def setUp(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(ledger, "DB", directory / "results.db"))
        ledger.init()

    def test_complete_ties_are_ordered_by_player(self) -> None:
        ledger.record(7, [PlayerId(2), PlayerId(1)], None)
        assert ledger.leaderboard(7) == [
            (PlayerId(1), 0, 1),
            (PlayerId(2), 0, 1),
        ]

    def test_leaderboard_orders_by_wins_then_games_played(self) -> None:
        one, two, three = PlayerId(1), PlayerId(2), PlayerId(3)
        ledger.record(7, [one, three], one)
        ledger.record(7, [one], one)
        ledger.record(7, [one], None)
        for _ in range(2):
            ledger.record(7, [two], two)
        assert ledger.leaderboard(7) == [
            (two, 2, 2),
            (one, 2, 3),
            (three, 0, 1),
        ]

    def test_ai_players_are_excluded(self) -> None:
        ledger.record(7, [PlayerId(-1), PlayerId(5)], PlayerId(-1))
        assert ledger.leaderboard(7) == [(PlayerId(5), 0, 1)]

        ledger.record(7, [PlayerId(-2), PlayerId(-1)], PlayerId(-1))
        assert ledger.leaderboard(7) == [(PlayerId(5), 0, 1)]
