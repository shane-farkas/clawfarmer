#!/usr/bin/env python3
"""Host-side sensor + photo tick for clawfarmer.

Runs as the openclaw service user via systemd timer. Does the SSH work that
the plant agent's sandboxed cron can't reach, then writes results into the
agent's workspace so it can reason over them.

Usage:
    clawfarmer-host-tick sensors          # read all Pi sensors, update state
    clawfarmer-host-tick photo            # capture photo on Jetson, pull to workspace

Why this exists: OpenClaw's non-default agents run cron in an isolated
sandbox that does not have ssh in its execution environment. The agent's
chat session does, but cron does not. This host-side script bridges the
gap: it runs on the host (where ssh works fine as the openclaw user) and
writes state files into the agent's workspace. The agent's cron jobs then
become pure reasoning over that state.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


# Defaults can be overridden via environment (set in the systemd unit).
WORKSPACE = Path(os.getenv(
    "CLAWFARMER_WORKSPACE",
    "/var/lib/openclaw/.openclaw/workspace-plant",
))

PI_HOST = os.getenv("PI_HOST", "clawpi.local")
PI_USER = os.getenv("PI_USER", "pi")
PI_KEY = os.getenv("PI_KEY", "/var/lib/openclaw/.ssh/id_ed25519_plantpi")
PI_VENV_PYTHON = os.getenv("PI_VENV_PYTHON", "~/clawfarmer-venv/bin/python3")

JETSON_HOST = os.getenv("JETSON_HOST", "orin-nano.local")
JETSON_USER = os.getenv("JETSON_USER", "{{JETSON_USER}}")
JETSON_KEY = os.getenv("JETSON_KEY", "/var/lib/openclaw/.ssh/id_ed25519_plantjetson")
JETSON_VENV_PYTHON = os.getenv("JETSON_VENV_PYTHON", "~/clawfarmer-venv/bin/python3")
JETSON_PHOTO_DIR = os.getenv("JETSON_PHOTO_DIR", "/var/lib/clawfarmer/photos")

# Soil calibration — env-override once the probe is calibrated
SOIL_CHANNEL = int(os.getenv("SOIL_CHANNEL", "0"))
SOIL_DRY_RAW = int(os.getenv("SOIL_DRY_RAW", "26000"))
SOIL_WET_RAW = int(os.getenv("SOIL_WET_RAW", "12000"))

# Optional — if set, the photo action triggers the given OpenClaw cron job
# right after a successful capture + analyze. Used to chain the rich
# photo-review synthesis so manual button presses and scheduled captures
# both refresh the dashboard's rich_analysis.md.
PHOTO_REVIEW_CRON_ID = os.getenv("PHOTO_REVIEW_CRON_ID", "").strip()

# When set to "1", the photo action runs rich analysis on the Jetson (Gemma
# over the same image + sensor context) and writes last_rich_analysis.md
# directly. Skips the OpenClaw cron chain entirely. When unset, falls back
# to the OpenClaw cron chain (Kimi/Qwen via Together).
USE_JETSON_RICH_ANALYSIS = os.getenv("USE_JETSON_RICH_ANALYSIS", "").strip() == "1"

# Optional Telegram delivery — if both env vars are set, host-tick posts
# the rich analysis directly to a Telegram chat via the Bot API after it
# lands in last_rich_analysis.md. Only engaged in the USE_JETSON_RICH_ANALYSIS
# path. Token is kept in the systemd override, never in the repo.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

STATE_FILE = WORKSPACE / "memory/sensor-state.json"
PHOTOS_DIR = WORKSPACE / "photos"
TICKER_FILE = WORKSPACE / "memory/basil-ticker.json"

# BASIL.X pricing model — plant grown as a "stock" whose price per gram
# floats around a Whole Foods baseline. Growth is path-dependent: each tick
# accumulates mass based on current health, and price per gram reflects the
# rolling 24h growth rate vs. the nominal rate of an ideal plant.
NOMINAL_DAILY_G = 30.0 / 56          # ~0.536 g/day to reach 30g in 8 weeks
BASE_PRICE_PER_G = 0.20              # Whole Foods organic basil, ~$0.20/g
TICKER_MIN_TICK_HOURS = 0.92         # ~55 min — avoids double-appending
TICKER_RETAIN_DAYS = 30              # trim tick history beyond 30 days


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _today_start() -> str:
    return (
        datetime.now()
        .astimezone()
        .replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat(timespec="seconds")
    )


def _ssh(key: str, user: str, host: str, remote_cmd: str, timeout: int = 30):
    argv = [
        "/usr/bin/ssh",
        "-i", key,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"ConnectTimeout={min(timeout, 10)}",
        f"{user}@{host}",
        remote_cmd,
    ]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _scp(key: str, user: str, host: str, remote_path: str, local_path: Path, timeout: int = 60):
    argv = [
        "/usr/bin/scp",
        "-i", key,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{user}@{host}:{remote_path}",
        str(local_path),
    ]
    r = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout, r.stderr


def _default_state() -> dict:
    return {
        "version": 1,
        "updated_at": None,
        "readings": {
            "soil_moisture": {"value": None, "unit": "pct_vwc", "at": None, "stale": True},
            "temp_f": {"value": None, "unit": "fahrenheit", "at": None, "stale": True},
            "humidity_pct": {"value": None, "unit": "pct_rh", "at": None, "stale": True},
            "lux": {"value": None, "unit": "lux", "at": None, "stale": True},
        },
        "day_ranges": {
            "soil_moisture": {"min": None, "max": None, "window_start": None},
            "temp_f": {"min": None, "max": None, "window_start": None},
            "humidity_pct": {"min": None, "max": None, "window_start": None},
        },
        "grow_light": {"state": "unknown", "last_toggled_at": None},
        "watering_history": [],
        "readings_history": [],
        "last_photo": {"filename": None, "at": None},
        "active_detections": [],
        "last_errors": [],
    }


def _extract_json(raw: str) -> dict | None:
    """Parse a JSON object out of mixed stdout (OpenClaw CLI prints a banner
    before the JSON payload). Returns None if no object can be parsed."""
    if not raw:
        return None
    # find the first '{' at line start — that's the JSON body
    for i, line in enumerate(raw.splitlines()):
        if line.lstrip().startswith("{"):
            tail = "\n".join(raw.splitlines()[i:])
            try:
                return json.loads(tail)
            except json.JSONDecodeError:
                return None
    return None


def _wait_and_capture_review_summary(
    cron_id: str, triggered_at: float, timeout_s: int = 60,
    poll_interval_s: int = 3,
) -> str | None:
    """Poll the OpenClaw cron runs API until the most recent run finishes
    (after `triggered_at`) and return its summary text. Returns None on
    timeout or if no fresh run is found."""
    deadline = triggered_at + timeout_s
    while time.time() < deadline:
        time.sleep(poll_interval_s)
        try:
            r = subprocess.run(
                ["openclaw", "cron", "runs", "--id", cron_id,
                 "--limit", "1"],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            continue
        if r.returncode != 0:
            continue
        data = _extract_json(r.stdout)
        if not data:
            continue
        entries = data.get("entries") or []
        if not entries:
            continue
        entry = entries[0]
        if entry.get("action") != "finished":
            continue
        run_at_s = (entry.get("runAtMs") or 0) / 1000
        # require the entry to be from our trigger (not a stale older run)
        if run_at_s < triggered_at - 5:
            continue
        summary = entry.get("summary")
        if summary:
            return summary
    return None


def _compose_rich_prompt(state: dict) -> str:
    """Build a single text prompt for the Jetson's vision model that includes
    sensor context + AGENTS.md thresholds alongside the image. Mirrors the
    structure the OpenClaw photo-review cron produces."""
    r = state.get("readings", {}) or {}
    dr = state.get("day_ranges", {}) or {}
    hist = state.get("readings_history", []) or []

    def _v(key):
        return (r.get(key) or {}).get("value")

    def _trend(h_key: str) -> str:
        values = [h.get(h_key) for h in hist if h.get(h_key) is not None]
        if len(values) < 2:
            return "no trend yet"
        first, last = values[0], values[-1]
        delta = last - first
        if abs(delta) < (abs(first) * 0.02):
            return "stable"
        return f"{'rising' if delta > 0 else 'falling'} from {first:.1f}"

    def _range(key: str, fmt: str = "{:.1f}") -> str:
        d = dr.get(key) or {}
        mn, mx = d.get("min"), d.get("max")
        if mn is None or mx is None:
            return "no range yet"
        return f"{fmt.format(mn)}–{fmt.format(mx)}"

    soil = _v("soil_moisture")
    temp = _v("temp_f")
    hum = _v("humidity_pct")
    lux = _v("lux")

    lp = state.get("last_photo") or {}
    analyzed_at = lp.get("at") or _now_iso()
    try:
        local_time = datetime.fromisoformat(analyzed_at).strftime("%I:%M %p").lstrip("0")
    except Exception:
        local_time = "now"

    return f"""You are helping monitor a basil plant. Produce a concise plant check based on the attached image AND the sensor readings below.

CURRENT SENSOR READINGS:
• soil moisture: {soil:.1f}% VWC (trend: {_trend('soil_moisture')}, today range {_range('soil_moisture')})
• temperature: {temp:.1f}°F (today range {_range('temp_f')})
• humidity: {hum:.1f}% RH (today range {_range('humidity_pct')})
• light: {lux:.0f} lux

BASIL CARE THRESHOLDS:
- soil: 40–70% ideal, water below 35, flag below 20 or above 85
- temp: 70–85°F ideal, flag below 55 or above 95
- humidity: 40–60% ideal, flag below 30 or above 80
- light: want 14–16 hours under grow lights or 6–8 hours direct sun

IMPORTANT — dark/nighttime images: if the image is too dark to make out the plant (mostly black, no discernible leaves or detail), DO NOT invent a description. Say something like "Image is too dark to assess — looks like nighttime." on the 📸 line and skip visual claims in the Assessment. Base the Assessment on sensor readings only and note that a visual check will be possible once lights are on.

Output EXACTLY this structure, 15 lines max, no preamble, no meta-commentary:

🌿 Plant check — {local_time}
📸 <1-2 sentence paraphrase of what you see in the image, OR "Image is too dark to assess — looks like nighttime." if the frame is effectively black>

📊 Last 12h:
• soil: {soil:.1f}% (trend description, target 40–70%)
• temp: {temp:.1f}°F (today range, target 70–85°F)
• humidity: {hum:.1f}% (today range, target 40–60%)
• light: <note only if unusually low or high>

🔍 Assessment:
2-3 sentences that SYNTHESIZE the photo and the sensor readings. Cross-reference visible symptoms with sensor data. Example: wilting + wet soil → root issue from waterlogging (not dehydration); yellowing + low humidity → transpiration stress. If the image is too dark to see the plant, say so and assess from sensors only.

💡 Suggestions:
• 1-3 concrete actions, or "Plant looks healthy — no action needed." if all readings in band and photo is clean.

End at the last suggestion bullet. No signoff, no Telegram tag, no "here is".
"""


def _post_to_telegram(text: str) -> str | None:
    """POST `text` to the configured Telegram chat via the Bot API. Returns
    None on success or a short error string. No-op (returns None) when the
    bot token or chat id are not configured."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return None
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    # Telegram limits a message body to 4096 chars. Our rich analyses are
    # well under that but trim defensively.
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text[:4000],
        "disable_web_page_preview": True,
    }
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}"
    except urllib.error.URLError as exc:
        return f"URL error: {exc.reason}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    if not result.get("ok"):
        return f"Telegram API error: {result.get('description', 'unknown')}"
    return None


def _run_jetson_rich_analyze(remote_image_path: str, prompt: str) -> tuple[str | None, str | None]:
    """SSH to the Jetson and invoke `clawfarmer_jetson rich-analyze`, piping
    the prompt over stdin. Returns (text, error) — exactly one is non-None."""
    argv = [
        "/usr/bin/ssh",
        "-i", JETSON_KEY,
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=10",
        f"{JETSON_USER}@{JETSON_HOST}",
        f"{JETSON_VENV_PYTHON} -m clawfarmer_jetson rich-analyze "
        f"--image {remote_image_path}",
    ]
    try:
        r = subprocess.run(
            argv, input=prompt, capture_output=True, text=True, timeout=240,
        )
    except subprocess.TimeoutExpired:
        return None, "ssh rich-analyze timed out after 240s"
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"

    if r.returncode != 0:
        return None, f"ssh/rich-analyze exit {r.returncode}: {(r.stderr or r.stdout).strip()[:300]}"

    try:
        payload = json.loads(r.stdout.strip())
    except json.JSONDecodeError as exc:
        return None, f"bad JSON from jetson: {exc}; out={r.stdout[:300]!r}"

    if not payload.get("ok"):
        return None, payload.get("error", "rich-analyze ok:false")

    text = (payload.get("rich_analysis") or "").strip()
    if not text:
        return None, "rich-analyze returned empty text"
    return text, None


def _archive_rich_analysis(state: dict) -> None:
    """Before the photo-review cron overwrites last_rich_analysis.md, append
    its current contents to growth-log.md as a timestamped entry. This keeps
    a rolling history of every rich photo-review analysis for later lookback,
    regardless of how often new photos trigger overwrites.

    No-op when last_rich_analysis.md is missing or empty — that's the first-
    run case where there's nothing to archive yet.
    """
    rich_file = WORKSPACE / "memory/last_rich_analysis.md"
    log_file = WORKSPACE / "memory/growth-log.md"
    if not rich_file.exists():
        return
    try:
        content = rich_file.read_text().strip()
        if not content:
            return
        mtime = datetime.fromtimestamp(rich_file.stat().st_mtime).astimezone()
        header = mtime.strftime("### %Y-%m-%d %H:%M %Z — photo review")
        entry = f"\n\n{header}\n\n{content}\n"
        existing = log_file.read_text() if log_file.exists() else ""
        log_file.write_text(existing + entry)
    except Exception as exc:
        _record_error(state, "rich-analysis-archive",
                      f"{type(exc).__name__}: {exc}")


def _append_history_snapshot(state: dict) -> None:
    """Append a compact snapshot of current readings to readings_history.
    Trimmed to last 48 entries — 12 hours at the 15-min sweep cadence."""
    r = state.get("readings", {}) or {}
    snap = {
        "at": _now_iso(),
        "soil_moisture": (r.get("soil_moisture") or {}).get("value"),
        "temp_f": (r.get("temp_f") or {}).get("value"),
        "humidity_pct": (r.get("humidity_pct") or {}).get("value"),
        "lux": (r.get("lux") or {}).get("value"),
    }
    # skip snapshots where every reading is None (no useful data)
    if all(snap[k] is None for k in ("soil_moisture", "temp_f", "humidity_pct", "lux")):
        return
    hist = state.setdefault("readings_history", [])
    hist.append(snap)
    state["readings_history"] = hist[-48:]


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return _default_state()
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
    except json.JSONDecodeError:
        return _default_state()
    # fill missing keys if schema evolved
    base = _default_state()
    for k, v in base.items():
        state.setdefault(k, v)
    return state


def _save_state(state: dict) -> None:
    state["updated_at"] = _now_iso()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    tmp.replace(STATE_FILE)


def _record_error(state: dict, source: str, message: str) -> None:
    state.setdefault("last_errors", []).append({
        "source": source,
        "error": message[:500],
        "at": _now_iso(),
    })
    state["last_errors"] = state["last_errors"][-20:]


def _update_day_range(state: dict, key: str, value: float, today_start: str) -> None:
    dr = state["day_ranges"].setdefault(
        key, {"min": None, "max": None, "window_start": None}
    )
    if dr.get("window_start") != today_start:
        dr["min"] = value
        dr["max"] = value
        dr["window_start"] = today_start
    else:
        dr["min"] = value if dr["min"] is None else min(dr["min"], value)
        dr["max"] = value if dr["max"] is None else max(dr["max"], value)


def _ticker_health_score(state: dict) -> int:
    """Same rubric as the dashboard's _compute_health, score-only (1..10).
    Duplicated here so host-tick stays self-contained and the ticker can run
    even if the dashboard process is down."""
    readings = state.get("readings", {}) or {}
    soil = (readings.get("soil_moisture") or {}).get("value")
    temp = (readings.get("temp_f") or {}).get("value")
    humidity = (readings.get("humidity_pct") or {}).get("value")

    def _bucket(v, alert_lo, alert_hi, healthy_lo, healthy_hi, edge_lo, edge_hi):
        if v is None:
            return 3
        if v < alert_lo or v > alert_hi:
            return 3
        if v < healthy_lo or v > healthy_hi:
            return 2
        if v < edge_lo or v > edge_hi:
            return 1
        return 0

    score = 10
    if soil is not None:
        score -= _bucket(soil, 20, 85, 35, 70, 45, 65)
    if humidity is not None:
        score -= _bucket(humidity, 30, 80, 40, 60, 45, 55)
    if temp is not None:
        score -= _bucket(temp, 55, 95, 65, 85, 68, 82)

    # Stale-readings guard — if we can't see the plant, it has no investor
    # confidence. Score floors at 1.
    updated_at = state.get("updated_at")
    if updated_at:
        try:
            dt = datetime.fromisoformat(updated_at)
            age_min = (datetime.now(dt.tzinfo) - dt).total_seconds() / 60
            if age_min > 30:
                score = 1
        except Exception:
            pass

    return max(1, min(10, score))


def _ticker_growth_multiplier(health: int) -> float:
    """Health score → mass-growth multiplier. Healthy plants grow at nominal
    rate; struggling plants grow slowly; dying plants lose mass."""
    # health 10 → 1.0 (full growth)
    # health 5  → 0.29 (slow)
    # health 3  → 0.0  (maintenance)
    # health 1  → -0.2 (wilting, clamped)
    return max(-0.2, min(1.0, (health - 3) / 7))


def _ticker_price_multiplier(rolling_daily_rate_g: float | None) -> float:
    """Rolling 24h growth rate → price-per-gram multiplier vs. base. A plant
    growing at the nominal rate trades at a ~15% premium to Whole Foods.
    Flat/declining plants trade at a discount."""
    if rolling_daily_rate_g is None:
        return 1.0
    ratio = rolling_daily_rate_g / NOMINAL_DAILY_G
    return max(0.70, min(1.30, 0.85 + 0.30 * ratio))


def _ticker_rolling_rate(ticks: list, now_dt: datetime,
                          current_mass: float) -> float | None:
    """Growth rate in g/day over the last 24h of ticks, linearly extrapolated.
    Returns None when there's not enough history to estimate."""
    if not ticks:
        return None
    cutoff = now_dt - timedelta(hours=24)
    baseline = None
    for t in ticks:
        try:
            t_dt = datetime.fromisoformat(t["at"])
        except Exception:
            continue
        if t_dt >= cutoff:
            baseline = t
            break
    if not baseline:
        return None
    base_dt = datetime.fromisoformat(baseline["at"])
    hours = (now_dt - base_dt).total_seconds() / 3600
    if hours < 1.0:
        return None
    return (current_mass - baseline["mass_g"]) * 24.0 / hours


def _ticker_default_inception() -> str:
    """Derive inception date from the oldest photo's mtime; fall back to
    'now' if no photos have been captured yet. The value is saved once and
    never recomputed, so the ticker's age stays stable."""
    try:
        if PHOTOS_DIR.exists():
            photos = [
                p for p in PHOTOS_DIR.iterdir()
                if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png"}
            ]
            if photos:
                oldest = min(photos, key=lambda p: p.stat().st_mtime)
                return (
                    datetime.fromtimestamp(oldest.stat().st_mtime)
                    .astimezone()
                    .isoformat(timespec="seconds")
                )
    except Exception:
        pass
    return _now_iso()


def _load_ticker() -> dict:
    if not TICKER_FILE.exists():
        return {
            "version": 1,
            "inception_at": None,
            "base_price_per_g": BASE_PRICE_PER_G,
            "nominal_daily_g": NOMINAL_DAILY_G,
            "mass_grams": 0.0,
            "ticks": [],
        }
    try:
        with open(TICKER_FILE) as f:
            return json.load(f)
    except Exception:
        return {
            "version": 1,
            "inception_at": None,
            "base_price_per_g": BASE_PRICE_PER_G,
            "nominal_daily_g": NOMINAL_DAILY_G,
            "mass_grams": 0.0,
            "ticks": [],
        }


def _save_ticker(ticker: dict) -> None:
    TICKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TICKER_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(ticker, f, indent=2)
    tmp.replace(TICKER_FILE)


def _update_ticker(state: dict) -> None:
    """Append a tick to the BASIL.X ticker if at least ~1h has passed since
    the last one. Mass accrues path-dependently from the current health score;
    price per gram floats on the rolling 24h growth rate."""
    try:
        ticker = _load_ticker()
        now_iso = _now_iso()
        now_dt = datetime.fromisoformat(now_iso)

        if not ticker.get("inception_at"):
            ticker["inception_at"] = _ticker_default_inception()

        ticks = ticker.setdefault("ticks", [])
        hours_since = None
        if ticks:
            try:
                last_dt = datetime.fromisoformat(ticks[-1]["at"])
                hours_since = (now_dt - last_dt).total_seconds() / 3600
                if hours_since < TICKER_MIN_TICK_HOURS:
                    return  # too soon, skip silently
            except Exception:
                hours_since = None

        health = _ticker_health_score(state)
        mass = float(ticker.get("mass_grams") or 0.0)
        # First tick has no elapsed interval — record the starting mass with no delta
        if hours_since is not None:
            delta = (NOMINAL_DAILY_G * (hours_since / 24)
                     * _ticker_growth_multiplier(health))
            mass = max(0.0, mass + delta)

        rolling = _ticker_rolling_rate(ticks, now_dt, mass)
        price_per_g = BASE_PRICE_PER_G * _ticker_price_multiplier(rolling)

        ticks.append({
            "at": now_iso,
            "mass_g": round(mass, 4),
            "health": health,
            "price_per_g": round(price_per_g, 5),
        })

        # Trim ticks beyond the retention window
        cutoff = now_dt - timedelta(days=TICKER_RETAIN_DAYS)
        ticker["ticks"] = [
            t for t in ticks
            if _safe_parse(t.get("at"), now_dt) > cutoff
        ]
        ticker["mass_grams"] = mass
        _save_ticker(ticker)
    except Exception as exc:
        _record_error(state, "ticker",
                      f"{type(exc).__name__}: {exc}")


def _safe_parse(iso: str | None, fallback: datetime) -> datetime:
    if not iso:
        return fallback
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        return fallback


def cmd_sensors() -> None:
    state = _load_state()
    today = _today_start()

    reads = [
        ("soil", f"{PI_VENV_PYTHON} -m clawfarmer_pi read-soil "
                  f"--channel {SOIL_CHANNEL} "
                  f"--dry-raw {SOIL_DRY_RAW} --wet-raw {SOIL_WET_RAW}"),
        ("bme280", f"{PI_VENV_PYTHON} -m clawfarmer_pi read-bme280"),
        ("lux", f"{PI_VENV_PYTHON} -m clawfarmer_pi read-lux"),
    ]

    for name, cmd in reads:
        try:
            rc, out, err = _ssh(PI_KEY, PI_USER, PI_HOST, cmd)
        except subprocess.TimeoutExpired:
            _record_error(state, f"pi/{name}", "ssh timed out")
            continue
        except Exception as exc:
            _record_error(state, f"pi/{name}", f"{type(exc).__name__}: {exc}")
            continue

        if rc != 0:
            _record_error(state, f"pi/{name}", (err or out).strip())
            continue

        try:
            data = json.loads(out.strip())
        except json.JSONDecodeError as exc:
            _record_error(state, f"pi/{name}", f"bad JSON: {exc}; out={out!r}")
            continue

        at = data.get("at")

        if name == "soil":
            val = data.get("pct_vwc")
            if val is not None:
                state["readings"]["soil_moisture"] = {
                    "value": val, "unit": "pct_vwc", "at": at, "stale": False,
                }
                _update_day_range(state, "soil_moisture", val, today)
        elif name == "bme280":
            tf = data.get("temp_f")
            rh = data.get("humidity_pct")
            if tf is not None:
                state["readings"]["temp_f"] = {
                    "value": tf, "unit": "fahrenheit", "at": at, "stale": False,
                }
                _update_day_range(state, "temp_f", tf, today)
            if rh is not None:
                state["readings"]["humidity_pct"] = {
                    "value": rh, "unit": "pct_rh", "at": at, "stale": False,
                }
                _update_day_range(state, "humidity_pct", rh, today)
        elif name == "lux":
            val = data.get("lux")
            if val is not None:
                state["readings"]["lux"] = {
                    "value": val, "unit": "lux", "at": at, "stale": False,
                }

    _append_history_snapshot(state)
    _update_ticker(state)
    _save_state(state)
    print(json.dumps({"ok": True, "action": "sensors", "at": state["updated_at"]}))


def cmd_photo() -> None:
    state = _load_state()
    PHOTOS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        rc, out, err = _ssh(
            JETSON_KEY, JETSON_USER, JETSON_HOST,
            f"{JETSON_VENV_PYTHON} -m clawfarmer_jetson capture "
            f"--out {JETSON_PHOTO_DIR}",
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        _record_error(state, "jetson/capture", "ssh timed out")
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo", "error": "timeout"}))
        sys.exit(1)
    except Exception as exc:
        _record_error(state, "jetson/capture", f"{type(exc).__name__}: {exc}")
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo",
                          "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)

    if rc != 0:
        _record_error(state, "jetson/capture", (err or out).strip())
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo",
                          "error": (err or out).strip()[:200]}))
        sys.exit(1)

    try:
        data = json.loads(out.strip())
    except json.JSONDecodeError as exc:
        _record_error(state, "jetson/capture", f"bad JSON: {exc}; out={out!r}")
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo", "error": "bad JSON"}))
        sys.exit(1)

    if not data.get("ok"):
        _record_error(state, "jetson/capture", data.get("error", "capture ok:false"))
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo",
                          "error": data.get("error")}))
        sys.exit(1)

    filename = data.get("filename")
    if not filename:
        _record_error(state, "jetson/capture", "capture JSON missing filename")
        _save_state(state)
        sys.exit(1)

    rc2, out2, err2 = _scp(
        JETSON_KEY, JETSON_USER, JETSON_HOST,
        f"{JETSON_PHOTO_DIR}/{filename}",
        PHOTOS_DIR / filename,
    )
    if rc2 != 0:
        _record_error(state, "jetson/scp", (err2 or out2).strip())
        _save_state(state)
        print(json.dumps({"ok": False, "action": "photo", "error": "scp failed"}))
        sys.exit(1)

    state["last_photo"] = {"filename": filename, "at": data.get("at")}
    review_triggered = False

    # Second step: ask the Jetson's local vision model to describe the photo.
    # This runs on the Jetson via Ollama; the observation lands in state so
    # the photo-review cron on the agent side can just read + relay.
    try:
        rc3, out3, err3 = _ssh(
            JETSON_KEY, JETSON_USER, JETSON_HOST,
            f"{JETSON_VENV_PYTHON} -m clawfarmer_jetson analyze "
            f"--image {JETSON_PHOTO_DIR}/{filename}",
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        _record_error(state, "jetson/analyze", "ssh to analyze timed out")
    except Exception as exc:
        _record_error(state, "jetson/analyze", f"{type(exc).__name__}: {exc}")
    else:
        if rc3 != 0:
            _record_error(state, "jetson/analyze", (err3 or out3).strip())
        else:
            try:
                ana = json.loads(out3.strip())
                if ana.get("ok"):
                    state["last_photo"]["observation"] = ana.get("observation", "")
                    state["last_photo"]["analysis_model"] = ana.get("model")
                    state["last_photo"]["analyzed_at"] = ana.get("at")
                    # sidecar JSON so the dashboard can look up per-photo
                    # observation when the user clicks an older gallery thumbnail
                    sidecar_payload = {
                        "filename": filename,
                        "captured_at": data.get("at"),
                        "observation": ana.get("observation", ""),
                        "analysis_model": ana.get("model"),
                        "analyzed_at": ana.get("at"),
                        "size_bytes": data.get("size_bytes"),
                    }
                    try:
                        sidecar_path = PHOTOS_DIR / f"{filename}.json"
                        sidecar_path.write_text(
                            json.dumps(sidecar_payload, indent=2)
                        )
                    except Exception as exc:
                        _record_error(state, "sidecar-write",
                                      f"{type(exc).__name__}: {exc}")

                    # Before we refresh last_rich_analysis.md (either via
                    # Gemma on Jetson or the OpenClaw cron chain), archive
                    # the current file to growth-log.md so history accumulates.
                    _archive_rich_analysis(state)

                    if USE_JETSON_RICH_ANALYSIS:
                        # Compose a prompt with sensor context + AGENTS.md
                        # thresholds and run Gemma on the Jetson over the
                        # image we just captured. No Together API, no
                        # OpenClaw cron, no sandbox — local analysis only.
                        prompt = _compose_rich_prompt(state)
                        remote_image = f"{JETSON_PHOTO_DIR}/{filename}"
                        text, err = _run_jetson_rich_analyze(remote_image, prompt)
                        if err:
                            _record_error(state, "jetson/rich-analyze", err)
                        elif text:
                            try:
                                (WORKSPACE / "memory/last_rich_analysis.md"
                                 ).write_text(text)
                                review_triggered = True
                            except Exception as exc:
                                _record_error(
                                    state, "rich-analysis-write",
                                    f"{type(exc).__name__}: {exc}",
                                )
                            # Deliver the same text to Telegram via Bot API
                            # (only if token + chat id are configured).
                            tg_err = _post_to_telegram(text)
                            if tg_err:
                                _record_error(state, "telegram-post", tg_err)
                    elif PHOTO_REVIEW_CRON_ID:
                        trigger_ts = time.time()
                        try:
                            r = subprocess.run(
                                ["openclaw", "cron", "run",
                                 PHOTO_REVIEW_CRON_ID],
                                capture_output=True, text=True, timeout=15,
                            )
                            if r.returncode != 0:
                                _record_error(
                                    state, "photo-review-trigger",
                                    (r.stderr or r.stdout).strip()[:200],
                                )
                            else:
                                review_triggered = True
                                # Poll for the run to finish, then take its
                                # summary and write it to last_rich_analysis.md
                                # ourselves — bypasses the sandbox's Write tool
                                # which misreports errors on success.
                                summary = _wait_and_capture_review_summary(
                                    PHOTO_REVIEW_CRON_ID, trigger_ts,
                                )
                                if summary:
                                    try:
                                        (WORKSPACE / "memory/last_rich_analysis.md"
                                         ).write_text(summary)
                                    except Exception as exc:
                                        _record_error(
                                            state, "rich-analysis-write",
                                            f"{type(exc).__name__}: {exc}",
                                        )
                                else:
                                    _record_error(
                                        state, "photo-review-poll",
                                        "no finished run captured within timeout",
                                    )
                        except subprocess.TimeoutExpired:
                            _record_error(state, "photo-review-trigger",
                                          "openclaw cron run timed out")
                        except Exception as exc:
                            _record_error(state, "photo-review-trigger",
                                          f"{type(exc).__name__}: {exc}")
                else:
                    _record_error(state, "jetson/analyze",
                                  ana.get("error", "analyze ok:false"))
            except json.JSONDecodeError as exc:
                _record_error(state, "jetson/analyze",
                              f"bad JSON: {exc}; out={out3!r}")

    _save_state(state)
    print(json.dumps({
        "ok": True, "action": "photo",
        "filename": filename, "size_bytes": data.get("size_bytes"),
        "has_observation": bool(state["last_photo"].get("observation")),
        "review_triggered": review_triggered,
    }))


def main() -> None:
    p = argparse.ArgumentParser(prog="clawfarmer-host-tick")
    p.add_argument("action", choices=("sensors", "photo"))
    args = p.parse_args()
    if args.action == "sensors":
        cmd_sensors()
    else:
        cmd_photo()


if __name__ == "__main__":
    main()
