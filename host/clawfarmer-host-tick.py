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
from datetime import datetime, timezone
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

STATE_FILE = WORKSPACE / "memory/sensor-state.json"
PHOTOS_DIR = WORKSPACE / "photos"


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

                    # Before triggering the photo-review cron (which will
                    # overwrite last_rich_analysis.md), archive the current
                    # rich analysis to growth-log.md so history accumulates.
                    _archive_rich_analysis(state)

                    # Chain into the rich photo-review cron so every manual
                    # button press + scheduled capture refreshes the
                    # dashboard's rich_analysis.md with a fresh synthesis.
                    if PHOTO_REVIEW_CRON_ID:
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
