#!/usr/bin/env python3
"""clawfarmer dashboard — minimal stdlib HTTP server.

Runs as the `openclaw` service user on Claw. Reads the plant agent's workspace
state file and photo directory; serves a single-page dashboard with:

    * current readings (soil / temp / humidity / lux) with health status dots
    * today's min/max
    * latest photo + Moondream observation
    * gallery of the last 12 photos
    * watering history
    * recent errors

No external dependencies. Bind + port + workspace path are overridable via
environment variables in the systemd unit.
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, quote

WORKSPACE = Path(os.getenv(
    "CLAWFARMER_WORKSPACE",
    "/var/lib/openclaw/.openclaw/workspace-plant",
))
STATE_FILE = WORKSPACE / "memory/sensor-state.json"
PHOTOS_DIR = WORKSPACE / "photos"

PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
BIND = os.getenv("DASHBOARD_BIND", "0.0.0.0")

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>clawfarmer — plant status</title>
<style>
:root {{
  --bg: #0f1315; --card: #1a2028; --border: #2a3441;
  --text: #e6eef5; --dim: #8b9bac;
  --good: #4ade80; --warn: #fbbf24; --bad: #ef4444; --accent: #60a5fa;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0; padding: 20px 24px 60px;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  background: var(--bg); color: var(--text);
  max-width: 1200px; margin-left: auto; margin-right: auto;
}}
h1 {{ margin: 0 0 4px; font-size: 22px; font-weight: 600; }}
.updated {{ color: var(--dim); font-size: 13px; margin-bottom: 24px; }}
.grid {{
  display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px; margin-bottom: 28px;
}}
.card {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 16px;
}}
.reading-label {{
  color: var(--dim); font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.8px; margin-bottom: 6px;
}}
.reading-value {{ font-size: 28px; font-weight: 600; line-height: 1.1; }}
.reading-unit {{ font-size: 14px; color: var(--dim); font-weight: 400; }}
.reading-range {{ margin-top: 10px; font-size: 12px; color: var(--dim); }}
.dot {{ display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: middle; }}
.dot.good {{ background: var(--good); box-shadow: 0 0 8px rgba(74, 222, 128, 0.4); }}
.dot.warn {{ background: var(--warn); box-shadow: 0 0 8px rgba(251, 191, 36, 0.4); }}
.dot.bad  {{ background: var(--bad);  box-shadow: 0 0 8px rgba(239, 68, 68, 0.4); }}
section {{ margin-bottom: 28px; }}
section h2 {{ font-size: 12px; text-transform: uppercase; letter-spacing: 1px; color: var(--dim); margin: 0 0 12px; font-weight: 600; }}
.photo-primary img {{ width: 100%; border-radius: 8px; display: block; border: 1px solid var(--border); }}
.observation {{
  margin-top: 12px; padding: 14px 16px; background: var(--card);
  border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0;
  line-height: 1.5; font-size: 14px;
}}
.photo-meta {{ color: var(--dim); font-size: 12px; margin-top: 8px; }}
.gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }}
.gallery a {{ display: block; background: var(--card); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; text-decoration: none; color: var(--text); transition: border-color 0.15s; }}
.gallery a:hover {{ border-color: var(--accent); }}
.gallery img {{ width: 100%; aspect-ratio: 4/3; object-fit: cover; display: block; }}
.gallery-caption {{ padding: 6px 8px; font-size: 11px; color: var(--dim); }}
.errors {{ border-left: 3px solid var(--bad); padding-left: 12px; font-size: 12px; color: var(--dim); }}
.errors ul {{ margin: 0; padding-left: 16px; }}
.empty {{ color: var(--dim); font-style: italic; font-size: 14px; }}
ul.watering {{ list-style: none; padding: 0; margin: 0; }}
ul.watering li {{ padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; font-variant-numeric: tabular-nums; }}
ul.watering li:last-child {{ border-bottom: none; }}
</style>
</head>
<body>
<h1>🌿 clawfarmer</h1>
<div class="updated">last updated {updated_at} · auto-refresh every 60s</div>

<section>
  <h2>Current readings</h2>
  <div class="grid">{reading_cards}</div>
</section>

<section>
  <h2>Latest photo</h2>
  {photo_block}
</section>

<section>
  <h2>Recent photos</h2>
  {gallery}
</section>

<section>
  <h2>Watering history ({watering_count})</h2>
  {watering_block}
</section>

{errors_block}
</body>
</html>
"""


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _fmt_time(iso: str | None) -> str:
    if not iso:
        return "—"
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %I:%M %p")
    except Exception:
        return iso


def _status(key: str, value: float | None) -> str:
    if value is None:
        return "bad"
    thresholds = {
        "soil_moisture": (20, 35, 70, 85),
        "temp_f":        (55, 65, 85, 95),
        "humidity_pct":  (20, 30, 60, 80),
    }
    t = thresholds.get(key)
    if not t:
        return "good"
    bad_lo, warn_lo, warn_hi, bad_hi = t
    if value < bad_lo or value > bad_hi:
        return "bad"
    if value < warn_lo or value > warn_hi:
        return "warn"
    return "good"


def _render_reading(key: str, reading: dict, day_range: dict | None,
                    label: str, unit: str, fmt: str = "{:.1f}") -> str:
    v = reading.get("value")
    vstr = fmt.format(v) if v is not None else "—"
    status = _status(key, v)
    rng_str = ""
    if day_range:
        mn, mx = day_range.get("min"), day_range.get("max")
        if mn is not None and mx is not None:
            rng_str = f"today: {fmt.format(mn)} – {fmt.format(mx)}"
    return f"""
    <div class="card">
      <div class="reading-label"><span class="dot {status}"></span>{label}</div>
      <div class="reading-value">{vstr} <span class="reading-unit">{unit}</span></div>
      <div class="reading-range">{rng_str}</div>
    </div>"""


def _render_photo_block(state: dict) -> str:
    lp = state.get("last_photo") or {}
    filename = lp.get("filename")
    if not filename:
        return '<p class="empty">No photos captured yet.</p>'
    obs = (lp.get("observation") or "").strip() or "— no observation yet —"
    at = _fmt_time(lp.get("at"))
    model = lp.get("analysis_model", "—")
    return f"""
    <div class="photo-primary">
      <a href="/photos/{quote(filename)}" target="_blank">
        <img src="/photos/{quote(filename)}" alt="latest plant photo">
      </a>
    </div>
    <div class="observation">{obs}</div>
    <div class="photo-meta">{filename} · captured {at} · analyzed by {model}</div>
    """


def _render_gallery() -> str:
    if not PHOTOS_DIR.exists():
        return '<p class="empty">Photos directory not found.</p>'
    files = [f for f in PHOTOS_DIR.iterdir()
             if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".png"}]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    files = files[:12]
    if not files:
        return '<p class="empty">No photos yet.</p>'
    tiles = []
    for f in files:
        ts = datetime.fromtimestamp(f.stat().st_mtime).strftime("%b %d · %I:%M %p")
        tiles.append(
            f'<a href="/photos/{quote(f.name)}" target="_blank">'
            f'<img src="/photos/{quote(f.name)}" loading="lazy" alt="">'
            f'<div class="gallery-caption">{ts}</div></a>'
        )
    return f'<div class="gallery">{"".join(tiles)}</div>'


def _render_watering(state: dict) -> str:
    wh = state.get("watering_history", []) or []
    if not wh:
        return '<p class="empty">No waterings logged yet.</p>'
    rows = []
    for w in list(reversed(wh))[:10]:
        at = _fmt_time(w.get("at"))
        d = w.get("duration_s", "?")
        pre = w.get("pre_moisture", "—")
        post = w.get("post_moisture", "—")
        rows.append(f"<li>{at} · {d}s · {pre}% → {post}%</li>")
    return f'<ul class="watering">{"".join(rows)}</ul>'


def _render_errors(state: dict) -> str:
    errs = state.get("last_errors", []) or []
    if not errs:
        return ""
    items = []
    for e in list(reversed(errs))[:6]:
        at = _fmt_time(e.get("at"))
        src = e.get("source", "")
        msg = (e.get("error") or "")[:200]
        items.append(f"<li>{at} · <b>{src}</b>: {msg}</li>")
    return f"""
<section>
  <h2>Recent errors ({len(errs)})</h2>
  <div class="errors"><ul>{"".join(items)}</ul></div>
</section>"""


def render_index() -> str:
    state = _load_state()
    readings = state.get("readings", {}) or {}
    ranges = state.get("day_ranges", {}) or {}
    cards = [
        _render_reading("soil_moisture", readings.get("soil_moisture", {}),
                        ranges.get("soil_moisture"), "Soil moisture", "% VWC"),
        _render_reading("temp_f", readings.get("temp_f", {}),
                        ranges.get("temp_f"), "Temperature", "°F"),
        _render_reading("humidity_pct", readings.get("humidity_pct", {}),
                        ranges.get("humidity_pct"), "Humidity", "% RH"),
        _render_reading("lux", readings.get("lux", {}),
                        None, "Light", "lux", fmt="{:.0f}"),
    ]
    return HTML_TEMPLATE.format(
        updated_at=_fmt_time(state.get("updated_at")),
        reading_cards="".join(cards),
        photo_block=_render_photo_block(state),
        gallery=_render_gallery(),
        watering_count=len(state.get("watering_history", []) or []),
        watering_block=_render_watering(state),
        errors_block=_render_errors(state),
    )


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, content: bytes | str,
              content_type: str = "text/html; charset=utf-8",
              extra_headers: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.wfile.write(content)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            try:
                body = render_index()
            except Exception as exc:
                self._send(500, f"dashboard render failed: {exc}", "text/plain; charset=utf-8")
                return
            self._send(200, body)
            return
        if path.startswith("/photos/"):
            name = path[len("/photos/"):]
            if "/" in name or ".." in name:
                self._send(404, "not found", "text/plain")
                return
            target = PHOTOS_DIR / name
            if not target.exists() or not target.is_file():
                self._send(404, "photo not found", "text/plain")
                return
            ctype = "image/jpeg" if target.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
            self._send(200, target.read_bytes(), ctype,
                       extra_headers={"Cache-Control": "public, max-age=600"})
            return
        self._send(404, "not found", "text/plain")

    def log_message(self, fmt: str, *args) -> None:
        # suppress default access logs; systemd journal stays readable
        pass


def main() -> None:
    server = ThreadingHTTPServer((BIND, PORT), Handler)
    host = socket.gethostname()
    print(f"clawfarmer dashboard listening on http://{host}:{PORT}/ (bind {BIND}:{PORT})")
    print(f"workspace: {WORKSPACE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
