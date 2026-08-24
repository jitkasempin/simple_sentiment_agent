"""Forex sentiment tools for LangChain / Managed Deep Agents."""

import json
from langchain_core.tools import tool
from tools.providers.myfxbook import fetch_myfxbook
from tools.providers.base import ProviderError


@tool
async def get_eurusd_sentiment() -> str:
    """Fetch the latest EURUSD retail sentiment directly from Myfxbook.

    Returns:
        JSON string containing the sentiment observation metrics (long_pct, short_pct, score, sample_size, fetched_at, etc.).
    """
    try:
        observations = await fetch_myfxbook("EURUSD")
        if not observations:
            return json.dumps({"error": "No sentiment observation returned for EURUSD."})
        obs = observations[0]
        return json.dumps({
            "pair": obs.pair,
            "provider": obs.provider,
            "long_percentage": obs.long_pct,
            "short_percentage": obs.short_pct,
            "score": obs.score,
            "sample_size": obs.sample_size,
            "fetched_at": obs.fetched_at.isoformat(),
            "as_of": obs.as_of.isoformat(),
            "warnings": obs.warnings,
        }, indent=2)
    except ProviderError as e:
        return json.dumps({"error": f"Provider error fetching Myfxbook sentiment: {str(e)}"})
    except KeyError as e:
        return json.dumps({"error": f"Missing environment variable: {str(e)}"})
    except Exception as e:
        return json.dumps({"error": f"Unexpected error: {str(e)}"})
