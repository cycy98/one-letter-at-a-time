import re
import unicodedata
from bisect import bisect_left
from dataclasses import dataclass
from typing import NewType

Word = NewType("Word", str)
type Prefix = str

_QUOTES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'"})

LENGTH_LIMIT = 100

_ADMISSIBLE = re.compile(rf"[a-z'-]{{1,{LENGTH_LIMIT}}}")


def normalise(s: str) -> str:
    """Fold quotes, strip combining marks, and casefold."""
    folded = s.translate(_QUOTES)
    bare = "".join(c for c in unicodedata.normalize("NFD", folded) if not unicodedata.combining(c))
    return unicodedata.normalize("NFC", bare).casefold()


def admissible(s: str) -> bool:
    return _ADMISSIBLE.fullmatch(s) is not None


def mint(raw: str) -> Word | None:
    s = normalise(raw)
    return Word(s) if admissible(s) else None


def parse(text: str) -> tuple[Word, ...]:
    return tuple(sorted({w for raw in text.split() if (w := mint(raw)) is not None}))


@dataclass(frozen=True, slots=True)
class Span:
    lo: int
    hi: int
    exact: bool

    @property
    def press(self) -> int:
        return self.hi - self.lo

    @property
    def live(self) -> bool:
        return self.hi > self.lo

    @property
    def can_extend(self) -> bool:
        return self.press > (1 if self.exact else 0)


def _successor(p: Prefix) -> str:
    return p[:-1] + chr(ord(p[-1]) + 1)


@dataclass(frozen=True, slots=True)
class Lex:
    words: tuple[Word, ...]
    version: int = 0

    def __post_init__(self) -> None:
        previous: Word | None = None
        for word in self.words:
            if not admissible(word) or (previous is not None and previous >= word):
                raise ValueError("lexicon words must be admissible, sorted, and unique")
            previous = word

    def span(self, p: Prefix) -> Span:
        if not p:
            return Span(0, len(self.words), False)
        lo = bisect_left(self.words, p)
        hi = bisect_left(self.words, _successor(p))
        return Span(lo, hi, lo < hi and self.words[lo] == p)

    def slice(self, p: Prefix) -> tuple[Word, ...]:
        s = self.span(p)
        return self.words[s.lo : s.hi]

    def next_letters(self, p: Prefix) -> tuple[str, ...]:
        s = self.span(p)
        out: list[str] = []
        i = s.lo + s.exact
        while i < s.hi:
            out.append(c := self.words[i][len(p)])
            i = bisect_left(self.words, _successor(p + c), i, s.hi)
        return tuple(out)
