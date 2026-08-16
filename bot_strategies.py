from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from wordconstants import PREFIXES, WORDLIST


class BotStrategy(Protocol):
    def __call__(self, game: Any, bot: BotPlayer) -> str | None: ...


@dataclass(eq=False)
class BotPlayer:
    name: str
    strategy_key: str
    mention: str = field(init=False)
    display_name: str = field(init=False)
    id: int = field(init=False)

    def __post_init__(self) -> None:
        self.mention = f"@{self.name}"
        self.display_name = self.name
        self.id = hash((self.name, self.strategy_key)) & 0x7FFFFFFF


def _suffixes_with_prefix(prompt: str) -> list[str]:
    prompt = prompt.casefold()
    return [word for word in WORDLIST if word.startswith(prompt)]


def _pick_best_extension(prefix: str, candidates: list[str]) -> str | None:
    prefix = prefix.casefold()
    if not candidates:
        return None

    playable = [word for word in candidates if word in PREFIXES]
    if playable:
        shortest = min(playable, key=len)
        if len(shortest) > len(prefix):
            return shortest[: len(prefix) + 1]

    longer = [word for word in candidates if len(word) > len(prefix)]
    if longer:
        shortest = min(longer, key=len)
        return shortest[: len(prefix) + 1]

    return None


def one_letter_safe(game: Any, bot: BotPlayer) -> str | None:
    prompt = getattr(game, "current_prompt", "")
    return _pick_best_extension(prompt, _suffixes_with_prefix(prompt))


def one_letter_aggressive(game: Any, bot: BotPlayer) -> str | None:
    prompt = getattr(game, "current_prompt", "")
    options = _suffixes_with_prefix(prompt)
    if not options:
        return None
    longest = max(options, key=len)
    if len(longest) <= len(prompt):
        return None
    return longest[: len(prompt) + 1]


def shiritori_safe(game: Any, bot: BotPlayer) -> str | None:
    current_word = getattr(game, "current_word", "")
    if not current_word:
        return None
    last_letter = current_word.casefold()[-1]
    played_words = getattr(game, "played_words", set())
    candidates = [word for word in WORDLIST if word.startswith(last_letter) and word not in played_words]
    if not candidates:
        return None
    return min(candidates, key=len)


def shiritori_long(game: Any, bot: BotPlayer) -> str | None:
    current_word = getattr(game, "current_word", "")
    if not current_word:
        return None
    last_letter = current_word.casefold()[-1]
    played_words = getattr(game, "played_words", set())
    candidates = [word for word in WORDLIST if word.startswith(last_letter) and word not in played_words]
    if not candidates:
        return None
    return max(candidates, key=len)


BOT_REGISTRY: dict[str, dict[str, tuple[str, BotStrategy]]] = {
    "shiritori": {
        "sesquipedalian": ("Sesquipedalian", shiritori_long),
    },
}


def available_bots_text(game_key: str | None = None) -> str:
    if game_key is None:
        return "; ".join(
            f"{key}: {', '.join(sorted(bot_name for bot_name, _ in bots.values()))}" for key, bots in sorted(BOT_REGISTRY.items())
        )
    bots = BOT_REGISTRY.get(game_key, {})
    return ", ".join(sorted(bot_name for bot_name, _ in bots.values()))


def get_bot_strategy(game_key: str, strategy_key: str) -> tuple[str, BotStrategy] | None:
    return BOT_REGISTRY.get(game_key, {}).get(strategy_key)
