"""Run a local vision model over an image via Ollama's HTTP API.

Ollama is expected to be running on localhost:11434 (the default) and to have
the requested model already pulled (`ollama pull moondream`). This module uses
urllib from the standard library so the package stays dependency-free.
"""

from __future__ import annotations

import base64
import json
import socket
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/generate"
DEFAULT_MODEL = "moondream"
DEFAULT_PROMPT = (
    "Describe what you see in this image. Focus on plant condition, leaf "
    "color, posture, and anything notable like flowering, pests, or soil "
    "issues. If there is no plant visible, say so clearly and describe what "
    "the camera is aimed at instead. Keep it to 3-5 sentences."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def analyze_image(
    path: str,
    prompt: str = DEFAULT_PROMPT,
    model: str = DEFAULT_MODEL,
    url: str = DEFAULT_OLLAMA_URL,
    timeout_s: int = 120,
) -> dict:
    """Send an image + prompt to Ollama and return an observation dict."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"image not found: {path}", "at": _now_iso()}

    image_bytes = p.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    payload = {
        "model": model,
        "prompt": prompt,
        "images": [image_b64],
        "stream": False,
    }
    body = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        return {
            "ok": False,
            "error": f"HTTP {e.code}: {e.read()[:300].decode('utf-8', 'replace')}",
            "at": _now_iso(),
        }
    except urllib.error.URLError as e:
        return {"ok": False, "error": f"URL error: {e.reason}", "at": _now_iso()}
    except socket.timeout:
        return {"ok": False, "error": f"ollama timed out after {timeout_s}s",
                "at": _now_iso()}

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"bad JSON from ollama: {e}",
                "at": _now_iso()}

    observation = (data.get("response") or "").strip()

    return {
        "ok": True,
        "model": model,
        "filename": p.name,
        "observation": observation,
        "eval_duration_ns": data.get("eval_duration"),
        "total_duration_ns": data.get("total_duration"),
        "at": _now_iso(),
    }
