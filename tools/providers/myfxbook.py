"""Myfxbook sentiment provider."""

import os
from datetime import datetime, timezone
from urllib.parse import unquote

import httpx
from models import SentimentObservation
from .base import ProviderError

BASE = "https://www.myfxbook.com/api"


async def _get(client: httpx.AsyncClient, path: str, params: dict) -> dict:
    response = await client.get(f"{BASE}/{path}", params=params)
    # Avoid raise_for_status(): its exception can include secret query parameters.
    if response.status_code != 200:
        raise ProviderError(f"myfxbook HTTP status {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProviderError("myfxbook returned invalid JSON") from exc
    if payload.get("error"):
        raise ProviderError("myfxbook reported an API error")
    return payload


async def fetch_myfxbook(pair: str) -> list[SentimentObservation]:
    now = datetime.now(timezone.utc)
    timeout = httpx.Timeout(10.0, connect=5.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        login = await _get(
            client,
            "login.json",
            {
                "email": os.environ["MYFXBOOK_EMAIL"],
                "password": os.environ["MYFXBOOK_PASSWORD"],
            },
        )
        session = unquote(login["session"])
        try:
            data = await _get(client, "get-community-outlook.json", {"session": session})
        finally:
            try:
                await _get(client, "logout.json", {"session": session})
            except ProviderError:
                pass

    wanted = pair.upper()
    inverse = wanted[3:] + wanted[:3]
    row = next((x for x in data["symbols"] if x["name"].upper() in {wanted, inverse}), None)
    if row is None:
        raise ProviderError(f"myfxbook has no outlook row for {wanted}")
    native = row["name"].upper()
    native_long, native_short = float(row["longPercentage"]), float(row["shortPercentage"])
    orientation = 1 if native == wanted else -1
    long_pct, short_pct = (
        (native_long, native_short) if orientation == 1 else (native_short, native_long)
    )
    return [
        SentimentObservation(
            pair=wanted,
            native_pair=native,
            provider="myfxbook",
            channel="retail_positioning",
            score=(long_pct - short_pct) / 100,
            long_pct=long_pct,
            short_pct=short_pct,
            sample_size=int(row.get("totalPositions") or 0),
            as_of=now,
            fetched_at=now,
            half_life_seconds=900,
            max_age_seconds=3600,
            reliability=0.80,
            independence_group="myfxbook",
            source_url="https://www.myfxbook.com/api",
            extraction_method="documented_api",
            warnings=["API exposes retrieval time, not a separate observation timestamp."],
        )
    ]
