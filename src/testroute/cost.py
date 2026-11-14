"""API cost and local energy estimates.

Component: new module ``cost.py`` (spec Section 5).

Responsibility:
    * Cloud cost of a call from its token counts, in two versions (spec
      Section 10, runs.jsonl): the list-price cost, and a time-of-day estimate
      that uses off-peak rates for calls outside DeepSeek's peak window.
      Input tokens are split into cache hits and misses, which DeepSeek bills
      differently.
    * Whether a call started in the peak window (the per-call ``peak`` flag of
      spec Section 7).
    * A rough local energy estimate: local compute seconds x assumed laptop
      power, converted to kWh and to money with an assumed electricity price.
      It is labelled rough everywhere and is not a claim that local
      generation is cheaper overall (FB10).
    All rates come from config/pricing.yaml.

Receives: token counts, UTC timestamps, local compute seconds, pricing dict.
Produces: floats (USD, kWh) and booleans.
Talks to: config/pricing.yaml (through ``settings``); used by C11 records.
Spec sections: 7 (peak flag), 9.6 (pricing config), 10 (cost fields), 11
    (RQ3 cost comparisons).
Feedback IDs: FB7 (cost of routing), FB10 (local compute is not free).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from testroute.settings import load_config

MILLION = 1_000_000


def load_pricing() -> dict[str, Any]:
    """Load config/pricing.yaml.

    Raises:
        ConfigError: if the file is missing or malformed.
    """
    return load_config("pricing.yaml")


def is_peak(utc_iso: str, pricing: dict[str, Any]) -> bool:
    """True when a UTC timestamp falls in the configured peak window.

    Args:
        utc_iso: ISO-8601 timestamp with a UTC offset, as written by providers.
        pricing: Loaded pricing config.

    Returns:
        True if both the UTC hour and the UTC weekday (0 = Monday) are listed
        as peak (config/pricing.yaml: 01:00 to 04:00 and 06:00 to 10:00 UTC,
        Monday to Friday; Chinese public holidays are not modeled). An empty or unparsable timestamp counts as peak, so the
        list-price assumption is used whenever the time is unknown.
    """
    try:
        moment = datetime.fromisoformat(utc_iso)
    except (TypeError, ValueError):
        return True
    # Convert to UTC first: the window is defined in UTC hours and weekdays.
    # A timestamp without an offset is taken to be UTC already.
    moment = moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    cloud = pricing["cloud"]
    return moment.hour in cloud["peak_hours_utc"] and moment.weekday() in cloud["peak_weekdays_utc"]


def call_cost(cache_miss_tokens: int, cache_hit_tokens: int, output_tokens: int, rates: dict[str, float]) -> float:
    """Cost in USD of one call at the given per-million rates.

    Args:
        cache_miss_tokens: Input tokens not served from the context cache.
        cache_hit_tokens: Input tokens served from the cache.
        output_tokens: Generated tokens.
        rates: A ``list`` or ``off_peak`` block of pricing.yaml.

    Returns:
        Cost in USD (not rounded; callers round for display).
    """
    return (
        cache_miss_tokens * rates["input_cache_miss_per_million"]
        + cache_hit_tokens * rates["input_cache_hit_per_million"]
        + output_tokens * rates["output_per_million"]
    ) / MILLION


def cloud_call_costs(call: dict[str, Any], pricing: dict[str, Any]) -> tuple[float, float]:
    """List-price and time-of-day cost of one recorded cloud call.

    Args:
        call: A calls.jsonl record (uses the token fields and ``started_utc``).
        pricing: Loaded pricing config.

    Returns:
        (list_cost_usd, time_of_day_cost_usd).
    """
    miss = int(call.get("cache_miss_tokens") or call.get("input_tokens") or 0)
    hit = int(call.get("cache_hit_tokens") or 0)
    out = int(call.get("output_tokens") or 0)
    list_cost = call_cost(miss, hit, out, pricing["cloud"]["list"])
    peak = is_peak(str(call.get("started_utc", "")), pricing)
    tod_rates = pricing["cloud"]["list"] if peak else pricing["cloud"]["off_peak"]
    return list_cost, call_cost(miss, hit, out, tod_rates)


def energy_estimate(local_compute_s: float, pricing: dict[str, Any]) -> tuple[float, float]:
    """ROUGH energy and electricity cost of local compute.

    Args:
        local_compute_s: Seconds of local model compute.
        pricing: Loaded pricing config.

    Returns:
        (kWh, USD), from assumed constant power and price.
    """
    kwh = local_compute_s * pricing["local"]["assumed_laptop_power_watts"] / 3600.0 / 1000.0
    return kwh, kwh * pricing["local"]["electricity_price_per_kwh"]
