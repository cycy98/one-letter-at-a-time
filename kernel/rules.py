from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum
from random import Random
from types import MappingProxyType
from typing import ClassVar

from kernel.words import Lex, Prefix, Word


class DegenerateError(LookupError):
    """Raised when a lexicon cannot provide an opening."""


class Out(StrEnum):
    OK = "ok"
    RETRY = "retry"
    REPEAT = "repeat"
    COMPLETE = "complete"
    DEADEND = "deadend"
    STRIKE = "strike"


class Axis(StrEnum):
    STRIKE = "strike"
    THIRD = "third"


CHARGES: Mapping[Out, Axis] = {
    Out.STRIKE: Axis.STRIKE,
    Out.COMPLETE: Axis.THIRD,
    Out.DEADEND: Axis.THIRD,
}
ROUND_OVER = frozenset({Out.COMPLETE, Out.DEADEND})


@dataclass(frozen=True, slots=True)
class Pos:
    text: Prefix
    used: frozenset[Word] = frozenset()


def _choose(moves: Iterable[Word], score: Callable[[Word], int], hard: bool) -> Word | None:
    if hard:
        return min(moves, key=score, default=None)
    return max(moves, key=score, default=None)


@dataclass(frozen=True)
class Prefixed:
    free_len: int | None = None

    retry_on_strike: ClassVar[bool] = True
    may_stall: ClassVar[bool] = False

    def open(self, lex: Lex, rng: Random) -> Pos:
        return Pos("")  # the first player chooses the opening letter

    def restart(self, lex: Lex, rng: Random) -> Pos:
        live = [(c, span) for c in lex.next_letters("") if (span := lex.span(c)).can_extend]
        if not live:
            raise DegenerateError("lexicon has no extendable opening letter")
        return Pos(rng.choices([c for c, _ in live], weights=[span.press for _, span in live])[0])

    def addressed(self, pos: Pos, w: Word) -> bool:
        return len(w) == len(pos.text) + 1 and w.startswith(pos.text)

    def valid(self, lex: Lex, pos: Pos, w: Word) -> bool:
        return lex.span(w).live

    def extend(self, pos: Pos, w: Word) -> Pos:
        return Pos(w)

    def classify(self, lex: Lex, pos: Pos) -> Out:
        s = lex.span(pos.text)
        if self.free_len is not None and s.exact and len(pos.text) > self.free_len:
            return Out.COMPLETE
        return Out.OK if s.can_extend else Out.DEADEND

    def playable(self, lex: Lex, pos: Pos) -> bool:
        return lex.span(pos.text).can_extend

    def moves(self, lex: Lex, pos: Pos) -> Iterator[Word]:
        return (Word(pos.text + c) for c in lex.next_letters(pos.text))

    def room(self, lex: Lex, pos: Pos, w: Word) -> int:
        return lex.span(w).press

    def choose(self, lex: Lex, pos: Pos, hard: bool) -> Word | None:
        if hard:
            return max(self.moves(lex, pos), key=lambda word: (len(word), self.room(lex, pos, word)), default=None)
        return max(self.moves(lex, pos), key=lambda word: self.room(lex, pos, word), default=None)


@dataclass(frozen=True)
class Chained:
    retry_on_strike: ClassVar[bool] = False
    may_stall: ClassVar[bool] = True

    def open(self, lex: Lex, rng: Random) -> Pos:
        starters = Counter(w[0] for w in lex.words)
        viable = [w for w in lex.words if starters[w[-1]] > (w[0] == w[-1])]
        if viable:
            w = rng.choice(viable)
            return Pos(w, frozenset({w}))
        raise DegenerateError("lexicon has no viable chain seed")

    def restart(self, lex: Lex, rng: Random) -> Pos:
        return self.open(lex, rng)

    def addressed(self, pos: Pos, w: Word) -> bool:
        return w[:1] == pos.text[-1:]

    def valid(self, lex: Lex, pos: Pos, w: Word) -> bool:
        return lex.span(w).exact and w not in pos.used

    def extend(self, pos: Pos, w: Word) -> Pos:
        return Pos(w, pos.used | {w})

    def classify(self, lex: Lex, pos: Pos) -> Out:
        return Out.OK

    def retry(self, lex: Lex, pos: Pos, w: Word) -> Out:
        return Out.OK if self.valid(lex, pos, w) else (Out.REPEAT if w in pos.used else Out.RETRY)

    def moves(self, lex: Lex, pos: Pos) -> Iterator[Word]:
        return (w for w in lex.slice(pos.text[-1:]) if w not in pos.used)

    def playable(self, lex: Lex, pos: Pos) -> bool:
        return next(self.moves(lex, pos), None) is not None

    @staticmethod
    def _score(lex: Lex, used: Mapping[str, int], w: Word, *, new: bool = True) -> int:
        return lex.span(w[-1]).press - used.get(w[-1], 0) - (new and w[0] == w[-1])

    def room(self, lex: Lex, pos: Pos, w: Word) -> int:
        return self._score(lex, Counter(word[0] for word in pos.used), w, new=w not in pos.used)

    def choose(self, lex: Lex, pos: Pos, hard: bool) -> Word | None:
        used = Counter(word[0] for word in pos.used)
        if hard:
            return max(
                self.moves(lex, pos),
                key=lambda word: (len(word), -self._score(lex, used, word)),
                default=None,
            )
        return _choose(self.moves(lex, pos), lambda word: self._score(lex, used, word), hard)


type Rules = Prefixed | Chained


@dataclass(frozen=True, slots=True)
class Config:
    label: str
    rules: Rules
    limits: Mapping[Axis, int]

    def __post_init__(self) -> None:
        object.__setattr__(self, "limits", MappingProxyType(dict(self.limits)))


GAMES: Mapping[str, Config] = {
    "oneletteratatime": Config("One Letter at a Time", Prefixed(), {Axis.STRIKE: 3}),
    "threethirdsofaghost": Config(
        "Three Thirds of a Ghost",
        Prefixed(free_len=3),
        {Axis.STRIKE: 5, Axis.THIRD: 3},
    ),
    "shiritori": Config("Shiritori", Chained(), {Axis.STRIKE: 3}),
}
ALIASES: Mapping[str, str] = {
    "olaat": "oneletteratatime",
    "ghost": "threethirdsofaghost",
}


def resolve(name: str) -> str | None:
    key = name.strip().casefold().replace(" ", "").replace("-", "")
    key = ALIASES.get(key, key)
    return key if key in GAMES else None
