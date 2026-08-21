from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from random import Random
from types import MappingProxyType
from typing import NewType

from kernel.rules import CHARGES, ROUND_OVER, Axis, Config, Out, Pos
from kernel.words import Lex, Word

PlayerId = NewType("PlayerId", int)

type Seat = tuple[PlayerId, ...]
type Counts = Mapping[PlayerId, Mapping[Axis, int]]


def rotate(seat: Seat) -> Seat:
    return seat[1:] + seat[:1]


def retire(seat: Seat, p: PlayerId) -> Seat:
    return tuple(q for q in seat if q != p)


@dataclass(frozen=True, slots=True)
class Game:
    cfg: Config
    lex: Lex
    seat: Seat
    pos: Pos
    seed: str
    counts: Counts = field(default_factory=dict)
    turn: int = 0

    def __post_init__(self) -> None:
        counts = {p: MappingProxyType(dict(row)) for p, row in self.counts.items()}
        object.__setattr__(self, "counts", MappingProxyType(counts))

    @property
    def roster(self) -> tuple[PlayerId, ...]:
        return tuple(self.counts)


def start(cfg: Config, lex: Lex, players: list[PlayerId], seed: str) -> Game:
    seat = tuple(players)
    if len(seat) < 2 or len(set(seat)) != len(seat):
        raise ValueError("at least two unique players are required")
    return Game(
        cfg=cfg,
        lex=lex,
        seat=seat,
        pos=cfg.rules.open(lex, Random(f"{seed}:0")),
        seed=seed,
        counts={p: {} for p in players},
    )


def legal(cfg: Config, lex: Lex, pos: Pos, w: Word) -> bool:
    return cfg.rules.addressed(pos, w) and cfg.rules.valid(lex, pos, w)


def play(g: Game, p: PlayerId, w: Word) -> tuple[Game, Out]:
    if not g.seat or p != g.seat[0]:
        raise ValueError(f"{p} is not to play (seat {g.seat})")
    r = g.cfg.rules
    if legal(g.cfg, g.lex, g.pos, w):
        pos = r.extend(g.pos, w)
        out = r.classify(g.lex, pos)
    elif hasattr(r, "retry") and not r.retry_on_strike:
        pos = g.pos
        out = r.retry(g.lex, g.pos, w)  # type: ignore[attr-defined]
    else:
        pos, out = g.pos, Out.STRIKE

    counts = g.counts
    # Ignore disabled sanction axes.
    if (axis := CHARGES.get(out)) is not None and axis in g.cfg.limits:
        row = counts[p]
        counts = {**counts, p: {**row, axis: row.get(axis, 0) + 1}}

    turn = g.turn + 1
    doomed = any(counts[p].get(a, 0) >= lim for a, lim in g.cfg.limits.items())
    keeps = out is Out.STRIKE and r.retry_on_strike or out is Out.RETRY
    seat = retire(g.seat, p) if doomed or out is Out.REPEAT else g.seat if keeps else rotate(g.seat)

    if out in ROUND_OVER:
        pos = r.restart(g.lex, Random(f"{g.seed}:{turn}"))

    return replace(g, seat=seat, pos=pos, counts=counts, turn=turn), out


def forfeit(g: Game, p: PlayerId) -> Game:
    return replace(g, seat=retire(g.seat, p))


def stalled(g: Game) -> bool:
    return not g.cfg.rules.playable(g.lex, g.pos)


def winner(g: Game) -> PlayerId | None:
    return g.seat[0] if len(g.seat) == 1 else None


def over(g: Game) -> bool:
    return len(g.seat) <= 1


def choose(g: Game, hard: bool) -> Word | None:
    return g.cfg.rules.choose(g.lex, g.pos, hard)
