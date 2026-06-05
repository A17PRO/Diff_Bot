"""
cogs/tilt.py — Background tilt-tracker task.

Every 15 minutes, for every linked user across all guilds:
  1. Fetch their last 5 ranked match IDs.
  2. Check if the 3 most recent results are all losses.
  3. If yes, generate a dynamic NIM roast and post it in the
     configured TILT_CHANNEL_NAME channel.

State is kept in-process (set of already-roasted match windows)
so the same losing streak doesn't trigger multiple roasts.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Optional

import discord
from discord.ext import commands, tasks

import database as db
import riot_api as riot
from config import TILT_CHANNEL_NAME, TILT_POLL_INTERVAL
from nim_roast import generate_roast


class TiltTrackerCog(commands.Cog, name="TiltTracker"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Track (discord_id, losing_streak_anchor_match_id) pairs that
        # have already been roasted to avoid spamming the same streak.
        # Use deque(maxlen=1000) for proper FIFO eviction when capacity is reached.
        self._roasted: deque[tuple[int, str]] = deque(maxlen=1000)
        self.tilt_loop.change_interval(seconds=TILT_POLL_INTERVAL)

    # ------------------------------------------------------------------
    # Cog lifecycle
    # ------------------------------------------------------------------

    def cog_load(self) -> None:
        self.tilt_loop.start()

    def cog_unload(self) -> None:
        self.tilt_loop.cancel()

    # ------------------------------------------------------------------
    # Background task
    # ------------------------------------------------------------------

    @tasks.loop(seconds=TILT_POLL_INTERVAL)
    async def tilt_loop(self) -> None:
        """Poll every linked user and roast them if they're on a 3-game losing streak."""
        print("[TILT] Running tilt check …")

        # Collect all guilds the bot is in
        for guild in self.bot.guilds:
            tilt_channel = _find_tilt_channel(guild)
            if tilt_channel is None:
                # No tilt-tracker channel in this guild — skip silently
                continue

            users = db.get_server_users(guild.id)
            # Run all user checks concurrently within the guild
            await asyncio.gather(
                *[
                    self._check_user(guild, tilt_channel, user_row)
                    for user_row in users
                ],
                return_exceptions=True,  # Don't let one bad user kill the whole batch
            )

        print("[TILT] Tilt check complete.")

    @tilt_loop.before_loop
    async def before_tilt_loop(self) -> None:
        """Wait until the bot is fully ready before the first poll."""
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------
    # Per-user logic
    # ------------------------------------------------------------------

    async def _check_user(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
        user_row,
    ) -> None:
        discord_id: int = user_row["discord_id"]
        puuid: str      = user_row["riot_puuid"]

        # ── Fetch last 5 ranked match IDs ──────────────────────────────
        match_ids = await riot.get_match_ids(puuid, count=5, queue=420)
        if len(match_ids) < 3:
            return  # Not enough history to evaluate

        # The three most recent matches are the first three IDs
        recent_ids = match_ids[:3]
        anchor_id  = recent_ids[0]  # Most recent match — used as dedup key

        # Already roasted this exact streak window
        if (discord_id, anchor_id) in self._roasted:
            return

        # ── Fetch full match objects concurrently ──────────────────────
        matches = await asyncio.gather(*[riot.get_match(mid) for mid in recent_ids])

        # ── Check for 3 consecutive losses ────────────────────────────
        results: list[Optional[bool]] = []  # True = win, False = loss, None = error
        for match in matches:
            if match is None:
                results.append(None)
                continue
            participant = riot.extract_participant(match, puuid)
            if participant is None:
                results.append(None)
                continue
            results.append(bool(participant.get("win", True)))

        # We need all three to be confirmed losses
        if not all(r is False for r in results):
            return

        # ── Extract stats from the most recent (worst) game ───────────
        most_recent_match = matches[0]
        participant       = riot.extract_participant(most_recent_match, puuid) if most_recent_match else None

        if participant is None:
            return

        champion      = participant.get("championName", "Unknown")
        kills         = participant.get("kills", 0)
        deaths        = participant.get("deaths", 0)
        assists       = participant.get("assists", 0)
        duration_secs = most_recent_match.get("info", {}).get("gameDuration", 0)

        # ── Generate NIM roast (blocking IO → thread) ──────────────────
        roast_text = await asyncio.to_thread(
            generate_roast,
            champion=champion,
            kills=kills,
            deaths=deaths,
            assists=assists,
            match_duration_seconds=duration_secs,
            win=False,
        )

        # ── Post roast in tilt channel ─────────────────────────────────
        member = guild.get_member(discord_id)
        mention = member.mention if member else f"<@{discord_id}>"

        embed = discord.Embed(
            title="📉 TILT ALERT — 3 Losses in a Row",
            description=roast_text,
            color=discord.Color.red(),
        )
        embed.add_field(name="Champion", value=champion, inline=True)
        embed.add_field(name="KDA",      value=f"{kills}/{deaths}/{assists}", inline=True)
        embed.add_field(
            name="Game Duration",
            value=f"{duration_secs // 60}m {duration_secs % 60}s",
            inline=True,
        )
        embed.set_footer(text="Powered by NVIDIA NIM · Stay hydrated and take a break 💧")

        try:
            await channel.send(content=f"👁️ {mention}", embed=embed)
        except discord.Forbidden:
            print(f"[TILT] Missing permissions to post in #{channel.name} ({guild.name})")
            return

        # ── Mark streak as seen ────────────────────────────────────────
        # deque with maxlen=1000 automatically evicts the oldest entry when full
        self._roasted.append((discord_id, anchor_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_tilt_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Return the first text channel whose name matches TILT_CHANNEL_NAME."""
    return discord.utils.get(guild.text_channels, name=TILT_CHANNEL_NAME)


# ---------------------------------------------------------------------------
# Cog registration
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TiltTrackerCog(bot))
