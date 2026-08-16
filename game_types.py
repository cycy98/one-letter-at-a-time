from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

import discord


class GameSession(Protocol):
    players: list[discord.Member | discord.User]

    async def handle_message(self, message: discord.Message) -> bool: ...


GameFactory = Callable[[list[discord.Member | discord.User]], GameSession]
