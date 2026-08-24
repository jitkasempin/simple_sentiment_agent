"""Data models for sentiment observations."""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class SentimentObservation(BaseModel):
    pair: str
    native_pair: str
    provider: str
    channel: str
    score: float
    long_pct: float
    short_pct: float
    sample_size: int = 0
    as_of: datetime
    fetched_at: datetime
    half_life_seconds: int = 900
    max_age_seconds: int = 3600
    reliability: float = 0.80
    independence_group: str = "myfxbook"
    source_url: str = "https://www.myfxbook.com/api"
    extraction_method: str = "documented_api"
    warnings: List[str] = Field(default_factory=list)
