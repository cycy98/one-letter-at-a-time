from __future__ import annotations

from random import choice
from typing import TYPE_CHECKING

from wordconstants import WORDLIST

if TYPE_CHECKING:
    import discord


class Shiritori:
    def __init__(self, players: list[discord.Member | discord.User]) -> None:
        self.current_word = choice(list(WORDLIST))
        self.played_words = set()
        self.players = players
        self.turn = 0

    def current_player(self) -> discord.Member | discord.User:
        return self.players[self.turn]

    def next_turn(self) -> None:
        self.turn = (self.turn + 1) % len(self.players)

    def eliminate(self, player: discord.Member | discord.User) -> None:
        self.players.remove(player)
        if self.players:
            self.turn %= len(self.players)
        else:
            self.turn = 0

    def is_message_valid(self, user_input: str, player: discord.Member | discord.User) -> bool:
        user_input = user_input.casefold()
        current_word = self.current_word.casefold()
        return len(user_input) > 0 and user_input[0] == current_word[-1] and player == self.current_player()

    def is_input_valid(self, user_input: str) -> bool:
        return user_input.casefold() in WORDLIST

    def is_input_played(self, user_input: str) -> bool:
        return user_input.casefold() in self.played_words

    async def handle_message(self, message: discord.Message) -> bool:
        content = message.content.strip().casefold()
        author = message.author

        if self.is_message_valid(content, author):
            if self.is_input_valid(content):
                if self.is_input_played(content):
                    await message.add_reaction("🔁")
                    await message.channel.send(f"Game over! {author.mention} played word already played.")
                    return True  # ends the game if a word is repeated
                self.played_words.add(content)
                self.current_word = content
                await message.add_reaction("✅")
                self.next_turn()
            else:
                await message.add_reaction("❌")
        elif content == "!suicide":
            self.eliminate(author)
            await message.channel.send(f"{author.mention} has chosen to eliminate themselves!")
            if len(self.players) == 1:
                await message.channel.send(f"{self.current_player().mention} is lonely :(")
                return True
        return False
