"""Thread-safe persistence for the in-memory lexicon."""

import stat
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from kernel.words import Lex, Word, parse


@dataclass(frozen=True, slots=True)
class Mutation:
    lex: Lex
    added: int
    removed: int


class Store:
    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise SystemExit(f"wordlist not found: {path} (set WORDLIST=)")
        try:
            self.lex = self._build(path.read_text("utf-8"), 0)
        except ValueError as exc:
            raise SystemExit(f"{path}: {exc}") from exc
        self.path = path
        self._lock = threading.Lock()

    @staticmethod
    def _build(text: str, version: int) -> Lex:
        lex = Lex(parse(text), version)
        if len(lex.words) < 2:
            raise ValueError("wordlist must contain at least two admissible words")
        if not any(lex.span(c).can_extend for c in lex.next_letters("")):
            raise ValueError("wordlist has no extendable opening letter")
        return lex

    def mutate(self, *, add: Iterable[Word] = (), remove: Iterable[Word] = ()) -> Mutation:
        """Apply a delta; call from a worker thread."""
        with self._lock:
            have = set(self.lex.words)
            new_words, gone = set(add) - have, set(remove) & have
            if not new_words and not gone:
                return Mutation(self.lex, 0, 0)
            body = "\n".join(sorted((have | new_words) - gone))
            fresh = self._build(body, self.lex.version + 1)
            mode = stat.S_IMODE(self.path.stat().st_mode)
            tmp: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=f".{self.path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    tmp = Path(stream.name)
                    stream.write(body)
                tmp.chmod(mode)
                tmp.replace(self.path)
            finally:
                if tmp is not None:
                    tmp.unlink(missing_ok=True)
            self.lex = fresh
            return Mutation(fresh, len(new_words), len(gone))

    def reload(self) -> Lex:
        with self._lock:
            self.lex = self._build(self.path.read_text("utf-8"), self.lex.version + 1)
            return self.lex
