from __future__ import annotations

import re

from wordconstants import PREFIXES, WORDLIST


def solver_wordbomb(prompt: str) -> list[str]:
    prompt = prompt.casefold()
    return [word for word in WORDLIST if prompt in word]


def solver_ghost(prompt: str) -> list[str]:
    prompt = prompt.casefold()
    return [
        prefix
        for prefix in PREFIXES
        if prefix.startswith(prompt) and len(prefix) == len(prompt) + 1 and (len(prefix) <= 3 or prefix not in WORDLIST)
    ]


def solver_olaat(prompt: str) -> list[str]:
    prompt = prompt.casefold()
    return [prefix for prefix in PREFIXES if prefix.startswith(prompt) and len(prefix) == len(prompt) + 1]


def solve_with_regex(expr: str) -> list[str]:
    pattern = re.compile(expr)
    return [word for word in WORDLIST if pattern.fullmatch(word)]
