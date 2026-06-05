"""
main.py — Discord Bot Entry Point
Loads cogs, initializes DB, and starts the bot.
"""

import asyncio
import discord
from discord.ext import commands

from config import DISCORD_TOKEN
from database import init_db

# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True  # Required for prefix-command parsing
intents.guilds = True
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,  # Suppress default help; we define our own UX
)

COGS = [
    "cogs.account",    # !link, !main
    "cogs.rankings",   # !rankings
    "cogs.tilt",       # background tilt-tracker loop
]


# ---------------------------------------------------------------------------
# Lifecycle hooks
# ---------------------------------------------------------------------------

# main.py — fix on_message and add on_ready
@bot.event
async def on_ready() -> None:
    init_db()
    print(f"[BOT] Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"[BOT] Synced {len(synced)} application command(s)")
    except Exception as exc:
        print(f"[BOT] Failed to sync app commands: {exc}")

@bot.event
async def on_message(message: discord.Message) -> None:
    if message.author == bot.user:
        return
    await bot.process_commands(message)


@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    """Persist every new server the bot joins."""
    from database import upsert_server
    upsert_server(guild.id, guild.name)
    print(f"[BOT] Joined guild: {guild.name} ({guild.id})")


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

async def main() -> None:
    async with bot:
        for cog in COGS:
            try:
                await bot.load_extension(cog)
                print(f"[COG] Loaded: {cog}")
            except Exception as exc:
                import traceback
                print(f"[COG] Failed to load {cog}: {exc}")
                traceback.print_exc()
        await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[BOT] Shutting down...")