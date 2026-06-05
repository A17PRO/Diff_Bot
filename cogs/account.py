"""
cogs/account.py — !link and !main commands.

!link <gameName>#<tagLine>
    Resolves the Riot ID to a PUUID and persists it in the DB,
    linking the user to the current server.

!main <championName>
    Fetches the caller's mastery for the given champion and
    calculates their win-rate + KDA over the last 10 ranked games
    played on that champion.
"""

from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

import database as db
import riot_api as riot


class AccountCog(commands.Cog, name="Account"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # !link
    # ------------------------------------------------------------------

    @commands.command(name="link")
    @commands.guild_only()
    async def link(self, ctx: commands.Context, *, riot_id: str) -> None:
        """
        Link your Discord account to a Riot ID.
        Usage: !link <gameName>#<tagLine>
        Example: !link Faker#KR1
        """
        if "#" not in riot_id:
            await ctx.reply(
                "❌ Invalid format. Use `!link <gameName>#<tagLine>`\n"
                "Example: `!link Faker#KR1`"
            )
            return

        game_name, tag_line = riot_id.split("#", 1)
        game_name, tag_line = game_name.strip(), tag_line.strip()

        if not game_name or not tag_line:
            await ctx.reply("❌ Both game name and tag line must be non-empty.")
            return

        async with ctx.typing():
            puuid = await riot.get_puuid(game_name, tag_line)

        if not puuid:
            await ctx.reply(
                f"❌ Could not find Riot account **{game_name}#{tag_line}**. "
                "Double-check the spelling and region."
            )
            return

        db.upsert_server(ctx.guild.id, ctx.guild.name)
        db.upsert_user(ctx.author.id, puuid)
        db.link_user_to_server(ctx.guild.id, ctx.author.id)

        embed = discord.Embed(
            title="✅ Account Linked",
            color=discord.Color.green(),
        )
        embed.add_field(name="Discord", value=ctx.author.mention, inline=True)
        embed.add_field(name="Riot ID", value=f"{game_name}#{tag_line}", inline=True)
        embed.add_field(name="PUUID (truncated)", value=f"`{puuid[:16]}…`", inline=False)
        embed.set_footer(text="Your stats will be tracked going forward.")
        await ctx.reply(embed=embed)

    # ------------------------------------------------------------------
    # !main
    # ------------------------------------------------------------------

    @commands.command(name="main")
    @commands.guild_only()
    async def main_champion(self, ctx: commands.Context, *, champion_name: str) -> None:
        """
        Show your mastery and recent performance on a champion.
        Usage: !main <championName>
        Example: !main Jinx
        """
        user_row = db.get_user_by_discord_id(ctx.author.id)
        if not user_row:
            await ctx.reply(
                "❌ You haven't linked a Riot account yet. Use `!link <gameName>#<tagLine>` first."
            )
            return

        puuid: str = user_row["riot_puuid"]

        async with ctx.typing():
            champion_id = await riot.get_champion_id_by_name(champion_name)
            if champion_id is None:
                await ctx.reply(
                    f"❌ Champion **{champion_name}** not found. "
                    "Check the spelling (e.g. `!main MissFortune`)."
                )
                return

            mastery = await riot.get_champion_mastery(puuid, champion_id)

            match_ids = await _get_champion_match_ids(puuid, champion_id, count=10)

            matches_details = await asyncio.gather(
                *[riot.get_match(mid) for mid in match_ids],
                return_exceptions=True
            )

            # ── Aggregate win-rate and KDA ──────────────────────────────
            # FIX: also skip Exception objects returned by gather, not just None
            wins = kills = deaths = assists = 0
            played = 0
            for match in matches_details:
                if match is None or isinstance(match, Exception):  # ← FIXED
                    continue
                p = riot.extract_participant(match, puuid)
                if p is None:
                    continue
                played += 1
                wins    += int(p.get("win", False))
                kills   += p.get("kills", 0)
                deaths  += p.get("deaths", 0)
                assists += p.get("assists", 0)

        embed = discord.Embed(
            title=f"📊 {ctx.author.display_name} — {champion_name.title()} Stats",
            color=discord.Color.blue(),
        )

        if mastery:
            level  = mastery.get("championLevel", 0)
            points = mastery.get("championPoints", 0)
            embed.add_field(
                name="Mastery",
                value=f"Level **{level}** · {points:,} pts",
                inline=False,
            )
        else:
            embed.add_field(name="Mastery", value="No mastery data found.", inline=False)

        if played > 0:
            win_rate = (wins / played) * 100
            avg_kda  = (kills + assists) / max(deaths, 1)
            embed.add_field(
                name=f"Last {played} Ranked Games",
                value=(
                    f"Win-rate : **{win_rate:.1f}%** ({wins}W/{played - wins}L)\n"
                    f"KDA      : **{avg_kda:.2f}** "
                    f"({kills}/{deaths}/{assists} total)"
                ),
                inline=False,
            )
        else:
            embed.add_field(
                name="Recent Performance",
                value="No ranked games found on this champion recently.",
                inline=False,
            )

        embed.set_footer(text="Stats based on last 10 ranked Solo/Duo games.")
        await ctx.reply(embed=embed)


# ---------------------------------------------------------------------------
# Helper — match IDs filtered by champion
# ---------------------------------------------------------------------------

async def _get_champion_match_ids(puuid: str, champion_id: int, count: int) -> list[str]:
    """
    The Match-V5 /ids endpoint does NOT support a championId filter param —
    the API silently ignores it. We must fetch a wider pool of recent ranked
    matches, resolve each one, and filter client-side by champion.
    """
    FETCH_BATCH = 50

    all_ids = await riot.get_match_ids(puuid, count=FETCH_BATCH, queue=420)
    if not all_ids:
        return []

    matches = await asyncio.gather(
        *[riot.get_match(mid) for mid in all_ids],
        return_exceptions=True
    )

    filtered: list[str] = []
    for match_id, match in zip(all_ids, matches):
        # FIX: skip both None and Exception objects from gather
        if match is None or isinstance(match, Exception):  # ← FIXED
            continue
        participant = riot.extract_participant(match, puuid)
        if participant is None:
            continue
        if participant.get("championId") == champion_id:
            filtered.append(match_id)
            if len(filtered) >= count:
                break

    return filtered


# ---------------------------------------------------------------------------
# Cog registration
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AccountCog(bot))