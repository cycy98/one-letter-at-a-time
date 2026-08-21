# Word-game engine

Turn-based word games for Discord: One Letter at a Time, Three Thirds of a
Ghost, and Shiritori. Requires Python 3.12+ and `discord.py`.

## Running

```sh
uv sync --locked
cp .env.example .env
uv run --env-file .env python -m bot
```

Set `DISCORD_TOKEN` in `.env`. Optional settings are documented in
`.env.example`. Word additions follow [DICTIONARY.md](DICTIONARY.md).

## Commands

`!queue`/`!q [game]` opens a lobby; players type `join`, and the host types
`start`. Other commands are `!addbot easy|hard`, `!leave`, `!abort`, `!status`,
`!forfeit`/`!ff`, `!check`/`!c <word>`, `!hint`, `!rules [game]`, `!top`, and
`!diag`. Curators can use `!add`/`!a`, `!remove`/`!r`, and `!reload`.

Games are server-only; `!check` and `!rules` also work in direct messages.
`!hint` is rate-limited but available to any player.
`!solve` exists as a disabled debug command and cannot be used during live
games.

## Checks

```sh
uv run --locked ./run-checks.sh
```

## Deployment

`wge.service` expects the checkout and virtual environment at `/srv/wge` and a
dedicated `wge` system user and group.

## Design

- The kernel returns new game states and has no I/O.
- The sorted lexicon provides indexed prefix lookup.
- Per-channel locks protect sessions; the lexicon store serializes curator edits.
