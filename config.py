"""
config.py — Centralised configuration & secrets.
Replace every placeholder below before deploying.
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ── API Keys & Secrets ────────────────────────────────────────────────────────
# These are pulled from .env file for security
DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
RIOT_API_KEY: str = os.getenv("RIOT_API_KEY", "")

# Regional routing values:
#   Americas → na1, br1, la1, la2
#   Asia     → kr, jp1
#   Europe   → euw1, eun1, tr1, ru
#   SEA      → oc1, ph2, sg2, th2, tw2, vn2
#
# ACCOUNT_REGION maps to the continent-level host used for account/PUUID lookups.
# PLATFORM_REGION is the per-datacenter host used for match/summoner endpoints.
# config.py
ACCOUNT_REGION: str = "asia"    # ← was "sea" — INVALID. Must be: americas | asia | europe | esports
PLATFORM_REGION: str = "sg2"    # ← correct, keep this      


NVIDIA_MODEL: str = "nvidia/nemotron-3-super-120b-a12b"

# ── Bot behaviour ────────────────────────────────────────────────────────────
# Channel name the tilt tracker will send roasts to.
TILT_CHANNEL_NAME: str = "tilt-tracker"

# How often (seconds) the tilt-tracker background task polls Riot.
TILT_POLL_INTERVAL: int = 900  # 15 minutes

# SQLite database path (relative to project root).
DATABASE_PATH: str = "lol_bot.db"
