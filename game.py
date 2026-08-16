from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

import discord
from discord.ext import commands

import one_letter_at_a_time
import shiritori
import three_thirds_of_a_ghost
import wordAdder
from bot_strategies import BotPlayer, available_bots_text, get_bot_strategy
from solver import solve_with_regex, solver_ghost, solver_olaat, solver_wordbomb
from wordconstants import WORDLIST, remove_words_from_wordlist, update_prefixes, update_wordlist_list

OneLetterAtATime = one_letter_at_a_time.OneLetterAtATime
ThreeThirdsOfAGhost = three_thirds_of_a_ghost.ThreeThirdsOfAGhost
Shiritori = shiritori.Shiritori

if TYPE_CHECKING:
    from game_types import GameFactory, GameSession

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

WORDLIST_PATH = Path("wordlist.txt")
TOKEN_PATH = Path("token")

def update_wordlist(new_words: set[str]) -> None:
    WORDLIST.update(new_words)
    update_wordlist_list(new_words)
    update_prefixes(new_words)


def remove_from_wordlist(words_to_remove: set[str]) -> None:
    WORDLIST.difference_update(words_to_remove)
    remove_words_from_wordlist(words_to_remove)

type Player = discord.Member | discord.User | BotPlayer

CHESSGUYYY_USER_ID = 1168452148049231934
GHOST_USER_ID = 956345979500634173

class Lobby(TypedDict):
    players: list[Player]
    game_key: str
    bots: list[BotPlayer]

GAME_REGISTRY: dict[str, tuple[str, GameFactory]] = {
    "oneletteratatime": ("One Letter at a Time", OneLetterAtATime),
    "threethirdsofaghost": ("Three Thirds of a Ghost", ThreeThirdsOfAGhost),
    "shiritori": ("Shiritori", Shiritori),
}

RULES: dict[str, str] = {
    "oneletteratatime": (
        "Players take turns extending the current string by one letter. "
        "Since the bot requires you to type the entire updated string, "
        "your input must always include the full current string.\n"
        "For example, if the current string is FRU, "
        "a valid submission would be FRUT.\n\n"
        "The current string must always remain the prefix of a valid English word. "
        "If not, you receive a strike. After 3 strikes, you are eliminated. "
        "In our example, submitting FRUK would get you a strike "
        "as no English word starts with FRUK.\n\n"
        "If a player completes a word that cannot be extended, "
        "(for example, FRUTIFYING, as no word starts with FRUTIFYING except for itself), "
        "the bot randomly chooses a new starting letter, and play continues from that letter."
    ),
    "threethirdsofaghost": (
        "Players take turns extending the current string by one letter. "
        "Since the bot requires you to type the entire updated string, "
        "your input must always include the full current string.\n"
        "For example, if the current string is FRU, "
        "a valid submission would be FRUT.\n\n"
        "The current string must always remain the prefix of a valid English word. "
        "If not, you receive a strike. After 5 strikes, you are eliminated. "
        "In our example, submitting FRUK would get you a strike "
        "as no English word starts with FRUK.\n\n"
        "If a player completes a word longer than 3 letters, or plays a valid word that doesn't start any other, "
        'the player gains a "third of a ghost", if you get three of those, you turn into a ghost , i.e., you get eliminated. '
        "The bot randomly chooses a new starting letter, and play continues from that letter."
    ),
    "shiritori": (
        "Players take turns submitting words. "
        "The first letter of the submitted word must match the last letter of the previous word. "
        "For example, if the current word is FRUIT, a valid submission would be TANGY.\n\n"
        "The submitted word must be a valid English word and cannot have been used previously in the game. "
        "If not, the game ends."
    ),
}

LOBBIES: dict[int, Lobby] = {}
ACTIVE_GAMES: dict[int, GameSession] = {}
BOT_TURN_LOCKS: dict[int, asyncio.Lock] = {}


class BotMessage:
    def __init__(self, channel: discord.abc.MessageableChannel, author: BotPlayer, content: str) -> None:
        self.channel = channel
        self.author = author
        self.content = content

    async def add_reaction(self, _: str) -> None:
        return None

def normalize_apostrophe(text: str) -> str:
    return text.replace("’", "'")  # ruff:ignore[ambiguous-unicode-character-string]

def normalize_game_name(game_name: str) -> str:
    return game_name.strip().casefold().replace(" ", "").replace("-", "")


def get_game_label(game_key: str) -> str:
    return GAME_REGISTRY[game_key][0]


def create_game(game_key: str, players: list[Player]) -> GameSession:
    return GAME_REGISTRY[game_key][1](players)


def available_games_text() -> str:
    return ", ".join(sorted(label for label, _ in GAME_REGISTRY.values()))


def is_bot_player(player: Player) -> bool:
    return isinstance(player, BotPlayer)


def current_player(game: GameSession) -> Player:
    return game.current_player()


async def maybe_play_bot_turns(channel: discord.abc.MessageableChannel, channel_id: int) -> bool:
    lock = BOT_TURN_LOCKS.setdefault(channel_id, asyncio.Lock())
    if lock.locked():
        return False

    async with lock:
        while channel_id in ACTIVE_GAMES:
            game = ACTIVE_GAMES[channel_id]
            player = current_player(game)
            if not is_bot_player(player):
                return False

            strategy_info = get_bot_strategy(get_game_key_for_game(game), player.strategy_key)
            if strategy_info is None:
                await channel.send(f"No strategy found for bot '{player.name}'.")
                return False

            _, strategy = strategy_info
            move = strategy(game, player)
            if move is None:
                await channel.send(f"{player.mention} has no legal move and is eliminated!")
                game.eliminate(player)
                if not any(is_bot_player(p) for p in game.players):
                    await channel.send("Players win!")
                    del ACTIVE_GAMES[channel_id]
                    return True
                continue

            await channel.send(f"{player.name} plays: {move}")
            finished = await game.handle_message(BotMessage(channel, player, move))
            if finished:
                del ACTIVE_GAMES[channel_id]
                return True

        return False


def get_game_key_for_game(game: GameSession) -> str:
    for game_key, (_, factory) in GAME_REGISTRY.items():
        if isinstance(game, factory):
            return game_key
    raise ValueError(f"Unknown game type: {type(game)!r}")


@bot.event
async def on_ready() -> None:
    print(f"Logged in as {bot.user}")


@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author == bot.user or message.guild is None:
        return

    channel_id = message.channel.id
    content = normalize_apostrophe(message.content.strip().lower())

    if channel_id in ACTIVE_GAMES:
        game = ACTIVE_GAMES[channel_id]
        finished = await game.handle_message(message)
        if finished:
            del ACTIVE_GAMES[channel_id]
        elif channel_id in ACTIVE_GAMES:
            await maybe_play_bot_turns(message.channel, channel_id)
        await bot.process_commands(message)
        return

    if channel_id in LOBBIES:
        lobby = LOBBIES[channel_id]
        players = lobby["players"]
        game_key = lobby["game_key"]

        if content == "join":
            if message.author not in players:
                players.append(message.author)
                await message.channel.send(f"{message.author.mention} has joined the game!")
        elif content == "start":
            if len(players) < 2:
                await message.channel.send("Not enough players to start the game.")
            else:
                await message.channel.send("The game has started!")
                ACTIVE_GAMES[channel_id] = create_game(game_key, players.copy())
                del LOBBIES[channel_id]
                if game_key == "shiritori":
                    await message.channel.send(f"The current word is {ACTIVE_GAMES[channel_id].current_word}")
                await maybe_play_bot_turns(message.channel, channel_id)

    await bot.process_commands(message)


@bot.command(aliases=["c", "check"])
async def isvalid(ctx: commands.Context, *, word: str) -> None:
    """Check if a word is in the dictionary."""
    word = normalize_apostrophe(word)
    status = "not " if word.lower() not in WORDLIST else ""
    await ctx.send(f"This is a {status}valid word.")


@bot.command()
async def oneletteratatime(ctx: commands.Context) -> None:
    """Queue a game of One Letter at a Time."""
    await queue_game_lobby(ctx, "oneletteratatime")

@bot.command()
async def ghost(ctx: commands.Context) -> None:
    """Queue a game of Three Thirds of a Ghost."""
    await queue_game_lobby(ctx, "threethirdsofaghost")

@bot.command(aliases=["q"])
async def queuegame(ctx: commands.Context, game_name: str = "oneletteratatime") -> None:
    """Queue a chosen game."""
    await queue_game_lobby(ctx, game_name)


async def queue_game_lobby(ctx: commands.Context, game_name: str) -> None:
    """Queue a chosen game."""
    channel_id = ctx.channel.id
    if channel_id in ACTIVE_GAMES:
        await ctx.send("A game is already in progress in this channel.")
        return

    game_key = normalize_game_name(game_name)
    if game_key not in GAME_REGISTRY:
        await ctx.send(f"Unknown game '{game_name}'. Available games: {available_games_text()}.")
        return

    LOBBIES[channel_id] = {"players": [], "game_key": game_key, "bots": []}
    await ctx.send(
        f"Queueing a new game of {get_game_label(game_key)}! "
        "Type 'join' to join the game. Type 'start' to start the game. "
        "Use !addbot <bot> to add a bot.",
    )

@bot.command()
async def rules(ctx: commands.Context, game_name: str = "oneletteratatime") -> None:
    """Check the rules for a specific game."""
    game_key = normalize_game_name(game_name)
    if game_key not in GAME_REGISTRY:
        await ctx.send(f"Unknown game '{game_name}'. Available games: {available_games_text()}.")
        return

    rules_text = RULES[game_key]
    await ctx.send(
        f"Rules for {get_game_label(game_key)}:\n{rules_text}\n\nAvailable bots: {available_bots_text(game_key)}"
    )


@bot.command()
async def bots(ctx: commands.Context, game_name: str = "oneletteratatime") -> None:
    """List available bots for a game."""
    game_key = normalize_game_name(game_name)
    if game_key not in GAME_REGISTRY:
        await ctx.send(f"Unknown game '{game_name}'. Available games: {available_games_text()}.")
        return
    await ctx.send(f"Available bots for {get_game_label(game_key)}: {available_bots_text(game_key)}")


@bot.command()
async def addbot(ctx: commands.Context, bot_name: str, *, game_name: str | None = None) -> None:
    """Add a bot to the lobby for the current or chosen game."""
    channel_id = ctx.channel.id
    if channel_id in ACTIVE_GAMES:
        await ctx.send("Bots can only be added before the game starts.")
        return

    if channel_id not in LOBBIES:
        target_game_name = game_name or "oneletteratatime"
        await queue_game_lobby(ctx, target_game_name)

    lobby = LOBBIES[channel_id]
    game_key = lobby["game_key"]
    bot_key = normalize_game_name(bot_name)
    strategy_info = get_bot_strategy(game_key, bot_key)
    if strategy_info is None:
        await ctx.send(
            f"Unknown bot '{bot_name}' for {get_game_label(game_key)}. "
            f"Available bots: {available_bots_text(game_key)}."
        )
        return

    bot_label, _ = strategy_info
    bot_player = BotPlayer(name=bot_label, strategy_key=bot_key)
    lobby["players"].append(bot_player)
    lobby["bots"].append(bot_player)
    await ctx.send(f"Added bot {bot_player.name} to the lobby.")

@bot.command(aliases=["a"])
async def addwords(ctx: commands.Context, *, words: str | None = None) -> None:
    """Add a word to the dictionary."""
    if ctx.author.id in (CHESSGUYYY_USER_ID, GHOST_USER_ID):
        if words is None:
            if ctx.message.attachments:
                words = (await ctx.message.attachments[0].read()).decode("utf-8", errors="ignore")
            else:
                await ctx.send("Please provide words to add.")
                return
        words = normalize_apostrophe(words.casefold())
        new_words = set(words.split())
        words_already_there = set()
        invalid_words = set()
        words_to_not_add = set()
        for new_word in new_words:
            if new_word in WORDLIST:
                words_already_there.add(new_word)
                words_to_not_add.add(new_word)
            if not new_word.replace("-", "").replace("'", "").isalpha():
                invalid_words.add(new_word)
                words_to_not_add.add(new_word)
        for word in words_to_not_add:
            new_words.remove(word)

        added_words = wordAdder.addwords(new_words)
        update_wordlist(added_words)
        await ctx.send(f"""Added {len(new_words)} words to the wordlist.
{len(words_already_there)} words were already in the dict: {' ,'.join(words_already_there)}
{len(invalid_words)} Invalid words: {', '.join(invalid_words)}""")


@bot.command(aliases=["r"])
async def removeword(ctx: commands.Context, *, words: str | None = None) -> None:
    """Remove one or more words from the dictionary."""
    if ctx.author.id not in (CHESSGUYYY_USER_ID, GHOST_USER_ID):
        await ctx.send("You are not authorized to use this command.")
        return

    if words is None:
        if ctx.message.attachments:
            words = (await ctx.message.attachments[0].read()).decode("utf-8", errors="ignore")
        else:
            await ctx.send("Please provide words to remove.")
            return

    words = normalize_apostrophe(words.casefold())
    words_to_remove = set(words.split())

    removed_words = words_to_remove & WORDLIST
    missing_words = words_to_remove - WORDLIST

    if removed_words:
        remaining_words = WORDLIST - removed_words
        wordlist_text = "\n".join(sorted(remaining_words, key=wordAdder.sort_key))
        WORDLIST_PATH.write_text(wordlist_text, encoding="utf-8")
        remove_from_wordlist(removed_words)

    await ctx.send(
        f"""Removed {len(removed_words)} words from the wordlist.
{len(missing_words)} words were not in the dict: {', '.join(sorted(missing_words))}"""
    )

@bot.command(aliases=["s"])
async def solve(ctx: commands.Context, game_type: str, prompt: str) -> None:
    """Solve a word puzzle."""
    game_type = game_type.lower()
    if game_type == "wordbomb":
        results = solver_wordbomb(prompt)
    elif game_type == "ghost":
        results = solver_ghost(prompt)
    elif game_type == "olaat":
        results = solver_olaat(prompt)
    elif game_type == "regex":
        results = solve_with_regex(prompt)
    else:
        await ctx.send(f"Unknown solver type '{game_type}'. Available types: wordbomb, ghost, olaat, regex.")
        return

    if not results:
        await ctx.send("No results found.")
    else:
        await ctx.send(
            f"```\n{'\n'.join(results[:10])}" + (f"\n and {len(results) - 10} more results...```" if len(results) > 10 else "```")
        )

with TOKEN_PATH.open(encoding="utf-8") as f:
    token = f.read().strip()

bot.run(token)
