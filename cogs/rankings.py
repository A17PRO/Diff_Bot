"""
cogs/rankings.py — !rankings command.

Fetches current Solo/Duo LP for every user linked to the invoking server,
uses Polars for fast in-memory sorting, and renders a formatted embed
leaderboard.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import discord
import polars as pl
from discord.ext import commands

import database as db
import riot_api as riot

# Tier display metadata (emoji + colour)
_TIER_META: dict[str, tuple[str, int]] = {
    "IRON":        ("🔩", 0x8B7355),
    "BRONZE":      ("🥉", 0xCD7F32),
    "SILVER":      ("🥈", 0xC0C0C0),
    "GOLD":        ("🥇", 0xFFD700),
    "PLATINUM":    ("💎", 0x00B4D8),
    "EMERALD":     ("💚", 0x50C878),
    "DIAMOND":     ("💠", 0xB9F2FF),
    "MASTER":      ("🔮", 0x9B59B6),
    "GRANDMASTER": ("🏆", 0xE74C3C),
    "CHALLENGER":  ("👑", 0xF1C40F),
}


class RankingsCog(commands.Cog, name="Rankings"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="rankings")
    @commands.guild_only()
    async def rankings(self, ctx: commands.Context) -> None:
        """Display the Solo/Duo LP leaderboard for all linked users in this server."""
        server_users = db.get_server_users(ctx.guild.id)
        print(f"[DEBUG] Rankings fired — {len(server_users)} users in DB for guild {ctx.guild.id}")
        if not server_users:
            await ctx.reply("📭 No users are linked yet. Use `!link <gameName>#<tagLine>`.")
            return

        async with ctx.typing():
            try:
                rows = await _build_ranking_rows(server_users, ctx.guild)
            except Exception as e:
                import traceback
                traceback.print_exc()
                await ctx.reply(f"Error building rankings:\n```{e}```")
                return

        if not rows:
            await ctx.reply("⚠️ Could not retrieve ranked data for any linked user.")
            return

        # ── Polars aggregation & sort ──────────────────────────────────
        df = pl.DataFrame(rows)
        df = df.sort("absolute_lp", descending=True)

        # ── Build embed ────────────────────────────────────────────────
        embed = _build_leaderboard_embed(df, ctx.guild)
        await ctx.reply(embed=embed)

    @commands.command()
    async def testrank(self, ctx):
        await ctx.send("Rankings cog loaded")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _build_ranking_rows(
    server_users: list,
    guild: discord.Guild,
) -> list[dict]:
    """
    Concurrently resolve ranked stats for every linked user.
    Returns a list of row dicts suitable for a Polars DataFrame.
    """
    async def _fetch_one(row) -> Optional[dict]:
        puuid      = row["riot_puuid"]
        discord_id = row["discord_id"]

        # Skip Summoner-V4 which may be deprecated; use PUUID directly with League-V4
        entry = await riot.get_ranked_stats_by_puuid(puuid)

        # Unranked users — include them with 0 LP so they appear at the bottom
        if entry is None:
            member = guild.get_member(discord_id)
            display = member.display_name if member else f"User {discord_id}"
            return {
                "discord_id":  discord_id,
                "display_name": display,
                "tier":        "UNRANKED",
                "rank":        "",
                "lp":          0,
                "absolute_lp": 0,
                "wins":        0,
                "losses":      0,
            }

        absolute_lp = riot.compute_lp(entry)

        # Cache LP in DB for tilt-tracker baseline
        db.update_user_lp(discord_id, absolute_lp)

        member = guild.get_member(discord_id)
        display = member.display_name if member else f"User {discord_id}"

        return {
            "discord_id":   discord_id,
            "display_name": display,
            "tier":         entry.get("tier", "UNRANKED"),
            "rank":         entry.get("rank", ""),
            "lp":           entry.get("leaguePoints", 0),
            "absolute_lp":  absolute_lp,
            "wins":         entry.get("wins", 0),
            "losses":       entry.get("losses", 0),
        }

    results = await asyncio.gather(
        *[_fetch_one(u) for u in server_users],
        return_exceptions=True
    )

    clean = []

    for r in results:
        if isinstance(r, Exception):
            print(f"[RANKINGS ERROR] {r}")
            continue
        if r is not None:
            clean.append(r)

    return clean

def _build_leaderboard_embed(df: pl.DataFrame, guild: discord.Guild) -> discord.Embed:
    """Render a Polars DataFrame into a styled Discord embed leaderboard."""
    embed = discord.Embed(
        title=f"🏆 {guild.name} — Solo/Duo Leaderboard",
        color=discord.Color.gold(),
    )

    lines: list[str] = []
    medal = {1: "🥇", 2: "🥈", 3: "🥉"}

    for position, row in enumerate(df.iter_rows(named=True), start=1):
        tier  = row["tier"]
        rank  = row["rank"]
        lp    = row["lp"]
        wins  = row["wins"]
        losses = row["losses"]
        total = wins + losses
        wr    = f"{wins / total * 100:.0f}%" if total else "—"

        tier_emoji, _ = _TIER_META.get(tier, ("❓", 0x95A5A6))
        pos_str       = medal.get(position, f"`#{position}`")

        if tier == "UNRANKED":
            rank_str = "Unranked"
        else:
            rank_str = f"{tier.capitalize()} {rank} — {lp} LP"

        lines.append(
            f"{pos_str} **{row['display_name']}** {tier_emoji}\n"
            f"    {rank_str} · {wins}W/{losses}L ({wr})"
        )

    # Discord embed field value cap = 1024 chars; chunk if necessary
    CHUNK = 10
    for i in range(0, len(lines), CHUNK):
        chunk = lines[i : i + CHUNK]
        embed.add_field(
            name=f"Players {i + 1}–{i + len(chunk)}",
            value="\n".join(chunk),
            inline=False,
        )

    embed.set_footer(
        text=f"{len(df)} linked players · LP includes tier/division weighting"
    )
    return embed


# ---------------------------------------------------------------------------
# Cog registration
# ---------------------------------------------------------------------------

async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RankingsCog(bot))
