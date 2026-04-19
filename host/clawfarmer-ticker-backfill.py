#!/usr/bin/env python3
"""One-shot backfill for the BASIL.X ticker.

Generates N days × 24 hourly ticks of simulated plant history and writes them
to memory/basil-ticker.json. Health follows a regime-shift pattern (a few
healthy stretches, a rough patch or two) so the resulting candlestick chart
has visible green/red drama instead of a boring monotonic line.

The newest generated tick is placed ~1h before now, so the next real sensor
sweep via clawfarmer-host-tick appends cleanly onto the backfilled tail with
no seam or overwrite.

Usage:
    clawfarmer-ticker-backfill.py                     # 30 days, random seed
    clawfarmer-ticker-backfill.py --days 14
    clawfarmer-ticker-backfill.py --seed 42           # reproducible run
    clawfarmer-ticker-backfill.py --force             # overwrite existing
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Constants mirror clawfarmer-host-tick.py so backfilled ticks are
# indistinguishable from live ones once the sensor timer fires.
WORKSPACE = Path(os.getenv(
    "CLAWFARMER_WORKSPACE",
    "/var/lib/openclaw/.openclaw/workspace-plant",
))
TICKER_FILE = WORKSPACE / "memory/basil-ticker.json"
NOMINAL_DAILY_G = 30.0 / 56
BASE_PRICE_PER_G = 0.20


def _growth_multiplier(health: int) -> float:
    return max(-0.2, min(1.0, (health - 3) / 7))


def _price_multiplier(rolling_daily_rate_g: float | None) -> float:
    if rolling_daily_rate_g is None:
        return 1.0
    ratio = rolling_daily_rate_g / NOMINAL_DAILY_G
    return max(0.70, min(1.30, 0.85 + 0.30 * ratio))


def _rolling_rate(ticks: list, now_dt: datetime,
                  current_mass: float) -> float | None:
    if not ticks:
        return None
    cutoff = now_dt - timedelta(hours=24)
    for t in ticks:
        t_dt = datetime.fromisoformat(t["at"])
        if t_dt >= cutoff:
            hours = (now_dt - t_dt).total_seconds() / 3600
            if hours < 1.0:
                return None
            return (current_mass - t["mass_g"]) * 24.0 / hours
    return None


def _regime_plan(days: int, rng: random.Random) -> list[float]:
    """Per-day mean health across the window, shaped into 4-6 regime blocks.
    Pulls means from a pool weighted toward healthy with occasional rough
    stretches — produces the narrative arc of a real plant."""
    regime_count = max(3, min(6, days // 5 or 3))
    block_sizes = [days // regime_count] * regime_count
    for i in range(days - sum(block_sizes)):
        block_sizes[i % regime_count] += 1

    # Weighted pool — most regimes are good, but a couple are genuinely bad
    # so the chart has red candles worth looking at.
    pool = [9.0, 8.5, 8.0, 8.0, 7.5, 7.0, 6.0, 5.0, 4.0, 3.5]

    means: list[float] = []
    last_mean: float | None = None
    for size in block_sizes:
        # Avoid picking the exact same regime back-to-back — keep it moving.
        while True:
            pick = rng.choice(pool)
            if pick != last_mean:
                break
        last_mean = pick
        means.extend([pick] * size)
    return means[:days]


def _simulate_health(day_means: list[float],
                     rng: random.Random) -> list[int]:
    """Hourly health series (1..10 ints). Each hour nudges a random walk
    around the day's regime mean, with small jitter on top."""
    out: list[int] = []
    drift = 0.0
    for day_mean in day_means:
        for _ in range(24):
            drift = drift * 0.85 + rng.uniform(-0.4, 0.4)
            value = day_mean + drift + rng.uniform(-0.3, 0.3)
            out.append(max(1, min(10, int(round(value)))))
    return out


def generate_ticker(days: int, seed: int | None) -> dict:
    rng = random.Random(seed)
    n_hours = days * 24

    # Anchor the newest tick ≥65min before now so the next real sweep from
    # clawfarmer-host-tick (which rate-limits at ~55min) reliably appends on
    # top. Floor-to-hour + 2h offset keeps ticks on clean :00 marks while
    # avoiding the edge case where floor-to-hour at XX:53 leaves only a 53min
    # gap and the first real tick gets rate-limited away.
    now = datetime.now(timezone.utc).astimezone()
    newest = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2)

    day_means = _regime_plan(days, rng)
    health_series = _simulate_health(day_means, rng)

    ticks: list[dict] = []
    mass = 0.0

    for i in range(n_hours):
        tick_dt = newest - timedelta(hours=(n_hours - 1 - i))
        health = health_series[i]

        # First tick has no prior interval — just records the starting state
        if ticks:
            delta = NOMINAL_DAILY_G * (1.0 / 24) * _growth_multiplier(health)
            mass = max(0.0, mass + delta)

        rolling = _rolling_rate(ticks, tick_dt, mass)
        price_per_g = BASE_PRICE_PER_G * _price_multiplier(rolling)

        ticks.append({
            "at": tick_dt.isoformat(timespec="seconds"),
            "mass_g": round(mass, 4),
            "health": health,
            "price_per_g": round(price_per_g, 5),
        })

    return {
        "version": 1,
        "inception_at": ticks[0]["at"] if ticks else now.isoformat(timespec="seconds"),
        "base_price_per_g": BASE_PRICE_PER_G,
        "nominal_daily_g": NOMINAL_DAILY_G,
        "mass_grams": mass,
        "ticks": ticks,
    }


def main() -> int:
    p = argparse.ArgumentParser(
        description="Backfill simulated BASIL.X ticker history."
    )
    p.add_argument("--days", type=int, default=30,
                   help="days of history to synthesize (default 30)")
    p.add_argument("--seed", type=int, default=None,
                   help="random seed for a reproducible run")
    p.add_argument("--force", action="store_true",
                   help="overwrite an existing ticker file")
    args = p.parse_args()

    if args.days < 1 or args.days > 90:
        print(f"--days must be 1..90, got {args.days}", file=sys.stderr)
        return 2

    if TICKER_FILE.exists() and not args.force:
        print(f"refusing to overwrite {TICKER_FILE} — pass --force to replace",
              file=sys.stderr)
        return 1

    ticker = generate_ticker(args.days, args.seed)
    TICKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TICKER_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(ticker, f, indent=2)
    tmp.replace(TICKER_FILE)

    ticks = ticker["ticks"]
    latest = ticks[-1]
    print(f"wrote {TICKER_FILE}")
    print(f"  days={args.days}  ticks={len(ticks)}  seed={args.seed}")
    print(f"  inception  {ticker['inception_at']}")
    print(f"  final mass {ticker['mass_grams']:.2f} g")
    print(f"  latest     ${latest['price_per_g']:.4f}/g  "
          f"health {latest['health']}  at {latest['at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
