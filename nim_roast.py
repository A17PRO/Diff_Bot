"""
nim_roast.py — NVIDIA NIM integration for dynamic player roasts.

Uses the standard OpenAI Python SDK pointed at NVIDIA's compatible endpoint.
"""

from openai import OpenAI

from config import NVIDIA_API_KEY, NVIDIA_MODEL

# ---------------------------------------------------------------------------
# Client (module-level singleton; thread-safe for read-only usage)
# ---------------------------------------------------------------------------

_client = OpenAI(
    base_url="https://integrate.api.nvidia.com/v1",
    api_key=NVIDIA_API_KEY,
)

_SYSTEM_PROMPT = (
    "You are a sarcastic, ruthless esports commentator. "
    "Roast the provided League of Legends player based on their recent terrible match stats. "
    "The roast MUST be strictly PG-13. "
    "Do not use extreme profanity, slurs, or real-world threats. "
    "Focus entirely on their in-game mechanical incompetence, terrible item builds, "
    "and lack of macro awareness. Keep it under two sentences."
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_roast(
    champion: str,
    kills: int,
    deaths: int,
    assists: int,
    match_duration_seconds: int,
    win: bool,
) -> str:
    """
    Call the NVIDIA NIM endpoint synchronously (the OpenAI SDK is blocking;
    this function is called inside `asyncio.to_thread` from the cog).

    Returns a roast string, or a fallback message on error.
    """
    duration_min = match_duration_seconds // 60
    kda = f"{kills}/{deaths}/{assists}"
    outcome = "lost" if not win else "somehow won but still played terribly"

    user_prompt = (
        f"Player stats from their last ranked game:\n"
        f"  Champion : {champion}\n"
        f"  KDA      : {kda}\n"
        f"  Duration : {duration_min} minutes\n"
        f"  Result   : {outcome}\n\n"
        "Roast them mercilessly based on these stats."
    )

    try:
        response = _client.chat.completions.create(
            model=NVIDIA_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            temperature=0.85,
            max_tokens=120,
        )
        return response.choices[0].message.content.strip()
    except Exception as exc:
        # Graceful degradation — never let NIM failures crash the bot
        print(f"[NIM] Roast generation failed: {exc}")
        return (
            f"Even my AI circuits are too embarrassed to describe "
            f"what {champion} just did with a {kda} scoreline."
        )
