import pathlib

WORDLIST_PATH = pathlib.Path("wordlist.txt")

with WORDLIST_PATH.open() as f:
    WORDLIST = set(f.read().split())

WORDLIST_LIST = list(WORDLIST)

PREFIXES = {word[:i] for word in WORDLIST for i in range(1, len(word) + 1)}


def update_prefixes(new_words: set[str]) -> None:
    PREFIXES.update(word[:i] for word in new_words for i in range(1, len(word) + 1))


def update_wordlist_list(new_words: set[str]) -> None:
    WORDLIST_LIST.extend(new_words)


def remove_words_from_wordlist(removed_words: set[str]) -> None:
    WORDLIST.difference_update(removed_words)
    WORDLIST_LIST.clear()
    WORDLIST_LIST.extend(WORDLIST)
    PREFIXES.clear()
    PREFIXES.update(word[:i] for word in WORDLIST for i in range(1, len(word) + 1))
