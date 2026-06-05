"""
riot_api.py — Async Riot Games API wrapper (aiohttp).

Covers:
  • Account-V1  : PUUID resolution
  • Summoner-V4 : summoner lookup by PUUID
  • League-V4   : ranked stats
  • Match-V5    : match history + match detail
  • Champion-Mastery-V4 : mastery data
"""

from __future__ import annotations

import asyncio
from urllib.parse import quote
from typing import Any, Optional

import aiohttp

from config import RIOT_API_KEY, ACCOUNT_REGION, PLATFORM_REGION

# ---------------------------------------------------------------------------
# Base URL builders
# ---------------------------------------------------------------------------

def _account_url(path: str) -> str:
    """Continent-level endpoint (account, match-v5)."""
    return f"https://{ACCOUNT_REGION}.api.riotgames.com{path}"


def _platform_url(path: str) -> str:
    """Platform-level endpoint (summoner, league, mastery)."""
    return f"https://{PLATFORM_REGION}.api.riotgames.com{path}"


_HEADERS = {"X-Riot-Token": RIOT_API_KEY}

# ---------------------------------------------------------------------------
# Low-level HTTP helper
# ---------------------------------------------------------------------------

async def _get(url: str, params: Optional[dict] = None) -> Any:
    # FIX: pass headers directly to session.get() instead of ClientSession
    # constructor — aiohttp doesn't reliably send session-level headers on all versions
    async with aiohttp.ClientSession() as session:
        for attempt in range(3):
            try:
                async with session.get(url, params=params, headers=_HEADERS) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    if resp.status == 429:
                        retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status == 404:
                        return None
                    print(f"[RIOT] Unexpected status {resp.status} for {url}")
                    return None
            except aiohttp.ClientError as exc:
                print(f"[RIOT] Request error on attempt {attempt}: {exc}")
                await asyncio.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# Account-V1
# ---------------------------------------------------------------------------

async def get_puuid(game_name: str, tag_line: str) -> Optional[str]:
    """
    Resolve a Riot ID (gameName#tagLine) to an encrypted PUUID.
    Endpoint: GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}
    
    FIX: URL-encode both parts so names with spaces/special chars work correctly.
    """
    encoded_name = quote(game_name, safe="")
    encoded_tag  = quote(tag_line, safe="")
    url = _account_url(f"/riot/account/v1/accounts/by-riot-id/{encoded_name}/{encoded_tag}")
    data = await _get(url)
    return data["puuid"] if data else None


# ---------------------------------------------------------------------------
# Summoner-V4
# ---------------------------------------------------------------------------

async def get_summoner_by_puuid(puuid: str) -> Optional[dict]:
    """
    Endpoint: GET /lol/summoner/v4/summoners/by-puuid/{encryptedPUUID}
    Returns summoner object including `id` (encrypted summoner ID).
    """
    url = _platform_url(f"/lol/summoner/v4/summoners/by-puuid/{puuid}")
    return await _get(url)


# ---------------------------------------------------------------------------
# League-V4
# ---------------------------------------------------------------------------

async def get_ranked_stats(summoner_id: str) -> Optional[dict]:
    """
    Endpoint: GET /lol/league/v4/entries/by-summoner/{encryptedSummonerId}
    Returns a list of queue-entry objects; we extract RANKED_SOLO_5x5.
    Returns None if unranked.
    """
    url = _platform_url(f"/lol/league/v4/entries/by-summoner/{summoner_id}")
    entries: list[dict] = await _get(url) or []
    for entry in entries:
        if entry.get("queueType") == "RANKED_SOLO_5x5":
            return entry
    return None


async def get_ranked_stats_by_puuid(puuid: str) -> Optional[dict]:
    """
    Alternative endpoint using PUUID (if Summoner-V4 is unavailable).
    Endpoint: GET /lol/league/v4/entries/by-puuid/{encryptedPUUID}
    Returns a list of queue-entry objects; we extract RANKED_SOLO_5x5.
    Returns None if unranked.
    """
    url = _platform_url(f"/lol/league/v4/entries/by-puuid/{puuid}")
    entries: list[dict] = await _get(url) or []
    for entry in entries:
        if entry.get("queueType") == "RANKED_SOLO_5x5":
            return entry
    return None


def compute_lp(entry: dict) -> int:
    """Convert a league entry to a single absolute-LP integer for ranking."""
    tier_weights = {
        "IRON": 0, "BRONZE": 400, "SILVER": 800, "GOLD": 1200,
        "PLATINUM": 1600, "EMERALD": 2000, "DIAMOND": 2400,
        "MASTER": 2800, "GRANDMASTER": 2800, "CHALLENGER": 2800,
    }
    division_weights = {"IV": 0, "III": 100, "II": 200, "I": 300}
    tier = entry.get("tier", "IRON").upper()
    division = entry.get("rank", "IV").upper()
    lp = entry.get("leaguePoints", 0)
    return tier_weights.get(tier, 0) + division_weights.get(division, 0) + lp


# ---------------------------------------------------------------------------
# Match-V5
# ---------------------------------------------------------------------------

async def get_match_ids(puuid: str, count: int = 10, queue: int = 420) -> list[str]:
    """
    Endpoint: GET /lol/match/v5/matches/by-puuid/{puuid}/ids
    queue=420 → Ranked Solo/Duo

    Note: Match-V5 endpoints must use continent-level routing (_account_url)
    regardless of PLATFORM_REGION, as match data is region-agnostic.
    """
    url = _account_url(f"/lol/match/v5/matches/by-puuid/{puuid}/ids")
    params = {"queue": queue, "count": count}
    result = await _get(url, params=params)
    return result or []


async def get_match(match_id: str) -> Optional[dict]:
    """
    Endpoint: GET /lol/match/v5/matches/{matchId}
    """
    url = _account_url(f"/lol/match/v5/matches/{match_id}")
    return await _get(url)


def extract_participant(match: dict, puuid: str) -> Optional[dict]:
    """Pull the participant sub-object for the given PUUID from a match."""
    for p in match.get("info", {}).get("participants", []):
        if p.get("puuid") == puuid:
            return p
    return None


# ---------------------------------------------------------------------------
# Champion-Mastery-V4
# ---------------------------------------------------------------------------

async def get_champion_mastery(puuid: str, champion_id: int) -> Optional[dict]:
    """
    Endpoint: GET /lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/by-champion/{championId}
    """
    url = _platform_url(
        f"/lol/champion-mastery/v4/champion-masteries"
        f"/by-puuid/{puuid}/by-champion/{champion_id}"
    )
    return await _get(url)


# ---------------------------------------------------------------------------
# Static data helper (champion name → champion ID)
# ---------------------------------------------------------------------------

_CHAMPION_DATA: Optional[dict] = None
_LATEST_VERSION: Optional[str] = None


async def _fetch_latest_version() -> str:
    global _LATEST_VERSION
    if _LATEST_VERSION:
        return _LATEST_VERSION
    data = await _get("https://ddragon.leagueoflegends.com/api/versions.json")
    _LATEST_VERSION = data[0] if data else "14.9.1"
    return _LATEST_VERSION


async def get_champion_id_by_name(champion_name: str) -> Optional[int]:
    """
    Resolve a champion's display name to its integer ID using Data Dragon.
    Case-insensitive partial matching is supported.
    """
    global _CHAMPION_DATA
    if not _CHAMPION_DATA:
        version = await _fetch_latest_version()
        url = f"https://ddragon.leagueoflegends.com/cdn/{version}/data/en_US/champion.json"
        raw = await _get(url)
        _CHAMPION_DATA = raw.get("data", {}) if raw else {}

    name_lower = champion_name.lower()
    for champ_key, champ_val in _CHAMPION_DATA.items():
        if (
            champ_key.lower() == name_lower
            or champ_val.get("name", "").lower() == name_lower
        ):
            return int(champ_val["key"])
    return None