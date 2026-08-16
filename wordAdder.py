from __future__ import annotations

import pathlib
from itertools import chain

from sort import extract_words


def sort_key(word: str) -> tuple[int, str]:
    return (-len(word), "".join(chr(0x10FFFF - ord(char)) for char in word))

def addwords(words_to_add: set[str] | None = None) -> set[str]:
    output_file = pathlib.Path("wordlist.txt")

    if words_to_add is None:
        input_file = pathlib.Path("wordstoadd.txt")
        with input_file.open("r", encoding="utf-8") as f:
            words_to_add = {word for word in extract_words(f.read()) if word}

    with output_file.open("r", encoding="utf-8") as f:
        existing_words = [word for word in f.read().split() if word]

    existing_set = set(existing_words)
    new_words = sorted(words_to_add - existing_set, key=sort_key)
    merged_words = []
    left = iter(existing_words)
    right = iter(new_words)
    left_word = next(left, None)
    right_word = next(right, None)

    while left_word is not None and right_word is not None:
        if sort_key(left_word) <= sort_key(right_word):
            merged_words.append(left_word)
            left_word = next(left, None)
        else:
            merged_words.append(right_word)
            right_word = next(right, None)

    merged_words.extend(chain([left_word], left) if left_word is not None else left)
    merged_words.extend(chain([right_word], right) if right_word is not None else right)
    output_file.write_text("\n".join(merged_words), encoding="utf-8")
    return set(new_words)

if __name__ == "__main__":
    addwords()
