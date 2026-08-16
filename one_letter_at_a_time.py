from __future__ import annotations

from random import choice
from typing import TYPE_CHECKING

from wordconstants import PREFIXES, WORDLIST

if TYPE_CHECKING:
    import discord


def isfinal(prompt: str) -> bool:
    prompt = prompt.casefold()
    return prompt in WORDLIST and not any(word != prompt and word.startswith(prompt) for word in WORDLIST)


class OneLetterAtATime:
    def __init__(self, players: list[discord.Member | discord.User], max_mistakes: int = 3) -> None:
        self.players = players
        self.current_prompt = ""
        self.turn = 0
        self.mistakes = dict.fromkeys(players, 0)
        self.max_mistakes = max_mistakes

    def current_player(self) -> discord.Member | discord.User:
        return self.players[self.turn]

    def next_turn(self) -> None:
        self.turn = (self.turn + 1) % len(self.players)

    def is_message_valid(self, user_input: str, player: discord.Member | discord.User) -> bool:
        user_input = user_input.casefold()
        current_prompt = self.current_prompt.casefold()
        return (
            player == self.current_player()
            and len(user_input) == len(current_prompt) + 1
            and user_input.startswith(current_prompt)
        )

    def is_input_valid(self, user_input: str) -> bool:
        return user_input.casefold() in PREFIXES

    def eliminate(self, player: discord.Member | discord.User) -> None:
        self.players.remove(player)
        if self.players:
            self.turn %= len(self.players)
        else:
            self.turn = 0

    def add_mistake(self, player: discord.Member | discord.User) -> bool:
        self.mistakes[player] += 1
        return self.mistakes[player] >= self.max_mistakes

    def reset_prompt(self) -> str:
        new_prompt = choice("abcdefghijklmnopqrstuvwxyz")
        self.current_prompt = new_prompt
        return new_prompt

    async def handle_message(self, message: discord.Message) -> bool:
        content = message.content.strip().casefold()
        author = message.author

        if self.is_message_valid(content, author):
            if self.is_input_valid(content):
                self.current_prompt = content
                await message.add_reaction("\u2705")
                if isfinal(self.current_prompt):
                    await message.channel.send(f"No words start with {self.current_prompt}, resetting to {self.reset_prompt()}")
                self.next_turn()
            else:
                eliminated = self.add_mistake(author)
                await message.add_reaction("\u274c")
                if eliminated:
                    await message.channel.send(f"{author.mention} has been eliminated!")
                    self.eliminate(author)
                    if len(self.players) == 1:
                        await message.channel.send(f"{self.current_player().mention} wins!")
                        return True
        elif content == "!suicide":
            self.eliminate(author)
            await message.channel.send(f"{author.mention} has chosen to eliminate themselves!")
            if len(self.players) == 1:
                await message.channel.send(f"{self.current_player().mention} wins!")
                return True

        return False
