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
import subprocess
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, quote

# Services the dashboard is allowed to kick off via sudo systemctl start.
# These are whitelisted — nothing else can be triggered over HTTP.
TRIGGERABLE_SERVICES = {
    "capture": "clawfarmer-host-tick@photo.service",
    "sensors": "clawfarmer-host-tick@sensors.service",
}

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
.header {{
  display: flex; justify-content: space-between; align-items: center;
  gap: 16px; flex-wrap: wrap;
  margin-bottom: 20px;
}}
.header-left {{ flex: 1 1 auto; }}
h1 {{ margin: 0 0 4px; font-size: 22px; font-weight: 600; }}
.updated {{ color: var(--dim); font-size: 13px; }}
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
.photo-layout {{
  display: grid;
  grid-template-columns: minmax(360px, 560px) 1fr;
  gap: 20px;
  margin-bottom: 28px;
}}
.photo-layout section {{ margin-bottom: 0; }}
@media (max-width: 820px) {{ .photo-layout {{ grid-template-columns: 1fr; }} }}
.photo-primary img {{ width: 100%; border-radius: 8px; display: block; border: 1px solid var(--border); }}
.charts {{
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
}}
.chart-card {{
  background: var(--card); border: 1px solid var(--border);
  border-radius: 8px; padding: 14px;
}}
.chart-title {{
  display: flex; justify-content: space-between; align-items: baseline;
  color: var(--dim); font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.8px; margin-bottom: 8px;
}}
.chart-current {{ color: var(--text); font-size: 13px; font-weight: 600; text-transform: none; letter-spacing: 0; }}
.chart-svg {{ display: block; width: 100%; height: auto; }}
.chart-meta {{ color: var(--dim); font-size: 10px; margin-top: 4px; display: flex; justify-content: space-between; }}
.observation {{
  margin-top: 12px; padding: 14px 16px; background: var(--card);
  border-left: 3px solid var(--accent); border-radius: 0 6px 6px 0;
  line-height: 1.5; font-size: 14px;
}}
.photo-meta {{ color: var(--dim); font-size: 12px; margin-top: 8px; }}
.gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(130px, 1fr)); gap: 10px; }}
.gallery a {{ display: block; background: var(--card); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; text-decoration: none; color: var(--text); transition: border-color 0.15s; }}
.gallery a:hover {{ border-color: var(--accent); }}
.gallery a.selected {{ border-color: var(--accent); box-shadow: 0 0 0 2px rgba(96, 165, 250, 0.3); }}
.gallery img {{ width: 100%; aspect-ratio: 4/3; object-fit: cover; display: block; }}
.gallery-caption {{ padding: 6px 8px; font-size: 11px; color: var(--dim); }}
.errors {{ border-left: 3px solid var(--bad); padding-left: 12px; font-size: 12px; color: var(--dim); }}
.errors ul {{ margin: 0; padding-left: 16px; }}
.empty {{ color: var(--dim); font-style: italic; font-size: 14px; }}
ul.watering {{ list-style: none; padding: 0; margin: 0; }}
ul.watering li {{ padding: 8px 0; border-bottom: 1px solid var(--border); font-size: 13px; font-variant-numeric: tabular-nums; }}
ul.watering li:last-child {{ border-bottom: none; }}
.toolbar {{ display: flex; gap: 8px; flex-wrap: wrap; flex: 0 0 auto; }}
.toolbar form {{ margin: 0; }}
.toolbar button {{
  font: inherit; color: var(--text);
  background: var(--card); border: 1px solid var(--border);
  padding: 8px 14px; border-radius: 6px; cursor: pointer;
  transition: background 0.15s, border-color 0.15s;
}}
.toolbar button:hover {{ background: #232d38; border-color: var(--accent); }}
.toolbar button:active {{ transform: translateY(1px); }}
.flash {{
  padding: 10px 14px; border-radius: 6px; margin-bottom: 16px;
  background: rgba(96, 165, 250, 0.1); border-left: 3px solid var(--accent);
  font-size: 13px;
}}
.flash.error {{ background: rgba(239, 68, 68, 0.1); border-left-color: var(--bad); }}
</style>
</head>
<body>
<div class="header">
  <div class="header-left">
    <h1>🌿 clawfarmer</h1>
    <div class="updated">last updated {updated_at} · auto-refresh every 60s</div>
  </div>
  <div class="toolbar">
    <form method="post" action="/trigger/capture"><button type="submit">📸 Take photo now</button></form>
    <form method="post" action="/trigger/sensors"><button type="submit">🌡️ Read sensors now</button></form>
  </div>
</div>

{flash_block}

<section>
  <h2>Current readings</h2>
  <div class="grid">{reading_cards}</div>
</section>

<section>
  <h2>Last 12 hours</h2>
  <div class="charts">{charts_block}</div>
</section>

<div class="photo-layout">
  <section>
    <h2>Latest photo</h2>
    {photo_block}
  </section>
  <section>
    <h2>Recent photos</h2>
    {gallery}
  </section>
</div>

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


def _render_chart(title: str, history: list, value_key: str, unit: str,
                  healthy_band: tuple | None = None,
                  fmt: str = "{:.1f}") -> str:
    """Render a single-series SVG mini-chart for a reading over time."""
    points = [(h.get("at"), h.get(value_key)) for h in history
              if h.get(value_key) is not None]

    W, H = 280, 110
    PAD_L, PAD_R, PAD_T, PAD_B = 32, 10, 8, 20
    chart_w = W - PAD_L - PAD_R
    chart_h = H - PAD_T - PAD_B

    if len(points) < 2:
        return f'''
  <div class="chart-card">
    <div class="chart-title">{title}<span class="chart-current">— {unit}</span></div>
    <svg viewBox="0 0 {W} {H}" class="chart-svg"></svg>
    <div class="chart-meta"><span>no data yet</span><span></span></div>
  </div>'''

    values = [p[1] for p in points]
    vmin = min(values)
    vmax = max(values)
    # pad the y range so the line isn't flat against the edges
    span = max(vmax - vmin, 1.0)
    pad = span * 0.15
    vmin_axis = vmin - pad
    vmax_axis = vmax + pad

    # include the healthy band in the axis range if it extends beyond data
    if healthy_band:
        band_lo, band_hi = healthy_band
        vmin_axis = min(vmin_axis, band_lo - pad * 0.3)
        vmax_axis = max(vmax_axis, band_hi + pad * 0.3)

    span_axis = max(vmax_axis - vmin_axis, 1.0)

    def _y(v: float) -> float:
        return PAD_T + chart_h * (1 - (v - vmin_axis) / span_axis)

    def _x(i: int, n: int) -> float:
        if n <= 1:
            return PAD_L + chart_w / 2
        return PAD_L + i * chart_w / (n - 1)

    # healthy-band rect
    band_svg = ""
    if healthy_band:
        band_lo, band_hi = healthy_band
        band_top = _y(band_hi)
        band_bot = _y(band_lo)
        band_svg = (f'<rect x="{PAD_L}" y="{band_top:.1f}" '
                    f'width="{chart_w}" height="{band_bot - band_top:.1f}" '
                    f'fill="rgba(74, 222, 128, 0.08)" />')

    # line polyline
    n = len(points)
    xs = [_x(i, n) for i in range(n)]
    ys = [_y(v) for v in values]
    polyline = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))

    # y-axis labels (min + max)
    y_labels = f'''
    <text x="{PAD_L - 4}" y="{PAD_T + 4}" text-anchor="end" font-size="9" fill="#8b9bac">{fmt.format(vmax_axis)}</text>
    <text x="{PAD_L - 4}" y="{PAD_T + chart_h + 2}" text-anchor="end" font-size="9" fill="#8b9bac">{fmt.format(vmin_axis)}</text>'''

    # timestamps at x edges (oldest / newest)
    def _short_time(iso: str | None) -> str:
        if not iso:
            return ""
        try:
            return datetime.fromisoformat(iso).strftime("%-I:%M%p").lower().replace("am", "a").replace("pm", "p")
        except Exception:
            return ""

    t_first = _short_time(points[0][0])
    t_last = _short_time(points[-1][0])

    last_val = values[-1]
    current_str = f"{fmt.format(last_val)} {unit}"

    return f'''
  <div class="chart-card">
    <div class="chart-title">{title}<span class="chart-current">{current_str}</span></div>
    <svg viewBox="0 0 {W} {H}" class="chart-svg" xmlns="http://www.w3.org/2000/svg">
      {band_svg}
      {y_labels}
      <polyline points="{polyline}" fill="none" stroke="#60a5fa" stroke-width="1.6" />
      <circle cx="{xs[-1]:.1f}" cy="{ys[-1]:.1f}" r="2.5" fill="#60a5fa" />
    </svg>
    <div class="chart-meta"><span>{t_first}</span><span>{t_last}</span></div>
  </div>'''


def _render_charts(state: dict) -> str:
    history = state.get("readings_history", []) or []
    if not history:
        return '<p class="empty">No history yet — first 15-min sweep will start populating.</p>'
    charts = [
        _render_chart("Soil moisture", history, "soil_moisture", "% VWC",
                      healthy_band=(40, 70)),
        _render_chart("Temperature", history, "temp_f", "°F",
                      healthy_band=(65, 85)),
        _render_chart("Humidity", history, "humidity_pct", "% RH",
                      healthy_band=(40, 60)),
        _render_chart("Light", history, "lux", "lux", fmt="{:.0f}"),
    ]
    return "".join(charts)


def _load_sidecar(filename: str) -> dict:
    """Look up a photo's sidecar JSON (observation + metadata) if it exists."""
    if not filename:
        return {}
    path = PHOTOS_DIR / f"{filename}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _render_photo_block(state: dict, selected_filename: str | None = None) -> str:
    # if ?photo=<filename> is in the query, look up the sidecar; otherwise use
    # the live last_photo from state
    lp: dict
    is_historical = False
    if selected_filename:
        sidecar = _load_sidecar(selected_filename)
        if sidecar:
            lp = {
                "filename": selected_filename,
                "at": sidecar.get("captured_at"),
                "observation": sidecar.get("observation", ""),
                "analysis_model": sidecar.get("analysis_model"),
            }
        else:
            # photo exists but was captured before sidecars were introduced —
            # show the image with an explanatory note instead of an observation
            lp = {"filename": selected_filename, "observation": None}
        is_historical = True
    else:
        lp = state.get("last_photo") or {}

    filename = lp.get("filename")
    if not filename:
        return '<p class="empty">No photos captured yet.</p>'

    obs_raw = lp.get("observation")
    if obs_raw is None and is_historical:
        obs_html = ('<div class="observation"><em>No saved observation for this photo '
                    '(captured before per-photo analysis was added).</em></div>')
    else:
        obs_text = (obs_raw or "").strip() or "— no observation yet —"
        obs_html = f'<div class="observation">{obs_text}</div>'

    at = _fmt_time(lp.get("at"))
    model = lp.get("analysis_model", "—")
    back_link = ""
    if is_historical:
        back_link = (' <a href="/" class="back-link" '
                     'style="color: var(--accent); text-decoration: none; '
                     'font-size: 12px;">← back to latest</a>')

    return f"""
    <div class="photo-primary">
      <a href="/photos/{quote(filename)}" target="_blank" title="open full-size">
        <img src="/photos/{quote(filename)}" alt="plant photo">
      </a>
    </div>
    {obs_html}
    <div class="photo-meta">{filename} · captured {at} · analyzed by {model}{back_link}</div>
    """


def _render_gallery(selected_filename: str | None = None) -> str:
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
        # click loads the photo at the top instead of opening the raw image
        is_selected = (f.name == selected_filename)
        cls = ' class="selected"' if is_selected else ""
        tiles.append(
            f'<a href="/?photo={quote(f.name)}"{cls} title="view at top">'
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


def render_index(flash: tuple[str, str] | None = None,
                 selected_photo: str | None = None) -> str:
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
    flash_block = ""
    if flash:
        kind, message = flash
        css = "flash error" if kind == "error" else "flash"
        flash_block = f'<div class="{css}">{message}</div>'
    return HTML_TEMPLATE.format(
        updated_at=_fmt_time(state.get("updated_at")),
        reading_cards="".join(cards),
        charts_block=_render_charts(state),
        photo_block=_render_photo_block(state, selected_filename=selected_photo),
        gallery=_render_gallery(selected_filename=selected_photo),
        watering_count=len(state.get("watering_history", []) or []),
        watering_block=_render_watering(state),
        errors_block=_render_errors(state),
        flash_block=flash_block,
    )


def _trigger_service(service: str) -> tuple[str, str]:
    """Fire a whitelisted systemd service. Returns (kind, message).

    Uses --no-block so systemctl returns as soon as the job is enqueued instead
    of waiting for the oneshot to finish. A 30s photo capture would otherwise
    block the dashboard's HTTP response for the duration.
    """
    try:
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", "--no-block", service],
            capture_output=True, text=True, timeout=10,
        )
    except subprocess.TimeoutExpired:
        return "error", f"Timed out triggering {service}."
    except Exception as exc:
        return "error", f"Failed to trigger {service}: {type(exc).__name__}: {exc}"
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[:300]
        return "error", f"systemctl exited {result.returncode} for {service}: {err}"
    label = "Photo capture" if "photo" in service else "Sensor sweep"
    return "ok", f"{label} triggered — new readings will appear within ~30-60s. Refresh to see."


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
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            # ?flash=ok&msg=... comes back from the POST redirect
            # ?photo=<filename> selects a gallery photo to feature at the top
            flash = None
            selected_photo = None
            if parsed.query:
                from urllib.parse import parse_qs
                q = parse_qs(parsed.query)
                kind = (q.get("flash") or [""])[0]
                msg = (q.get("msg") or [""])[0]
                if kind and msg:
                    flash = (kind, msg)
                raw = (q.get("photo") or [""])[0]
                if raw and "/" not in raw and ".." not in raw:
                    selected_photo = raw
            try:
                body = render_index(flash=flash, selected_photo=selected_photo)
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

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if not path.startswith("/trigger/"):
            self._send(404, "not found", "text/plain")
            return
        key = path[len("/trigger/"):]
        service = TRIGGERABLE_SERVICES.get(key)
        if not service:
            self._send(400, "unknown trigger", "text/plain")
            return
        kind, message = _trigger_service(service)
        # consume any POST body (we don't use it, but read it to close the connection cleanly)
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length:
                self.rfile.read(length)
        except Exception:
            pass
        # redirect back to the dashboard with a flash message in the query string
        self.send_response(303)
        self.send_header("Location", f"/?flash={quote(kind)}&msg={quote(message)}")
        self.end_headers()

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
