#!/usr/bin/env python3
"""A/B vision model comparison via Ollama.

Runs the same image + prompt through multiple models on the local Ollama
instance and prints side-by-side observations + timings. Uses `keep_alive: 0`
so each model unloads after its call, avoiding OOM collisions on the
8GB Jetson.

Usage on the Jetson:

    # default: compares moondream vs llava-phi3 on the latest photo
    python3 compare-models.py

    # specify models + a specific image
    python3 compare-models.py \\
        --image /mnt/ssd/photos/2026-04-19T01-38-38.jpg \\
        --models moondream llava-phi3 llava:7b-q3_K_S

    # skip the auto-pull step if you've already got everything
    python3 compare-models.py --no-pull
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_PHOTO_DIR = "/mnt/ssd/photos"
DEFAULT_PROMPT = "Describe the plant in this image, its leaf color and condition."
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


def pull_if_missing(model: str) -> None:
    try:
        res = subprocess.run(
            ["ollama", "list"], capture_output=True, text=True, check=True
        )
    except Exception as exc:
        print(f"  ⚠  could not check installed models: {exc}")
        return
    if model.split(":")[0] in res.stdout and (
        ":" not in model or model in res.stdout
    ):
        return
    print(f"  ⬇  pulling {model} (one-time, may take a few minutes)...")
    try:
        subprocess.run(["ollama", "pull", model], check=True)
    except subprocess.CalledProcessError as exc:
        print(f"  ⚠  pull failed for {model}: exit {exc.returncode}")


def analyze(image_path: Path, prompt: str, model: str, timeout: int = 180) -> dict:
    try:
        img_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    except Exception as exc:
        return {"ok": False, "error": f"cannot read image: {exc}"}

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt, "images": [img_b64]}],
        "stream": False,
        "keep_alive": 0,
    }
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "error": f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}",
            "seconds": round(time.time() - t0, 1),
        }
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"URL error: {exc.reason}",
                "seconds": round(time.time() - t0, 1)}

    elapsed = round(time.time() - t0, 1)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"bad JSON: {exc}", "seconds": elapsed}

    msg = (data.get("message") or {}).get("content") or ""
    return {
        "ok": True,
        "content": msg.strip(),
        "eval_count": data.get("eval_count"),
        "prompt_eval_count": data.get("prompt_eval_count"),
        "seconds": elapsed,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="A/B vision models via Ollama.")
    p.add_argument("--image", default=None,
                   help="Image path. Default: latest JPEG in /mnt/ssd/photos.")
    p.add_argument("--prompt", default=DEFAULT_PROMPT,
                   help="Prompt to send to every model.")
    p.add_argument("--models", nargs="+",
                   default=["moondream", "llava-phi3"],
                   help="Model tags to compare (space-separated).")
    p.add_argument("--no-pull", action="store_true",
                   help="Skip auto-pulling missing models.")
    args = p.parse_args()

    if args.image:
        image = Path(args.image)
    else:
        photos = sorted(
            Path(DEFAULT_PHOTO_DIR).glob("*.jpg"),
            key=lambda f: f.stat().st_mtime,
            reverse=True,
        )
        if not photos:
            print(f"no .jpg files found in {DEFAULT_PHOTO_DIR}", file=sys.stderr)
            sys.exit(1)
        image = photos[0]

    if not image.exists():
        print(f"image not found: {image}", file=sys.stderr)
        sys.exit(1)

    print(f"image  : {image}")
    print(f"prompt : {args.prompt!r}")
    print(f"models : {', '.join(args.models)}")
    print()

    if not args.no_pull:
        for m in args.models:
            pull_if_missing(m)
        print()

    results = []
    for model in args.models:
        header = f" {model} "
        print(f"┏{'━' * 70}")
        print(f"┃{header:━^70}")
        print(f"┗{'━' * 70}")
        r = analyze(image, args.prompt, model)
        results.append((model, r))
        if r["ok"]:
            print(f"  {r['seconds']}s · {r.get('eval_count') or '?'} tokens generated "
                  f"(prompt {r.get('prompt_eval_count') or '?'} tokens)")
            print()
            print("  " + (r["content"] or "— empty response —").replace("\n", "\n  "))
        else:
            print(f"  ✗ failed ({r.get('seconds', '?')}s): {r.get('error', '?')}")
        print()

    # summary
    print("=" * 72)
    print(f"{'summary':^72}")
    print("=" * 72)
    for model, r in results:
        status = "✓" if r["ok"] else "✗"
        t = f"{r['seconds']}s" if r.get("seconds") is not None else "—"
        tokens = str(r.get("eval_count", "—"))
        first_line = (r.get("content") or r.get("error", ""))[:60].replace("\n", " ")
        print(f"  {status}  {model:22}  {t:>7}  {tokens:>4} tok  {first_line}")


if __name__ == "__main__":
    main()
