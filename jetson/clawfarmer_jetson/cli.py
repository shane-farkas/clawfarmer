"""CLI surface. Every command prints one JSON object on stdout. Exit 0 on
success, 1 on failure (with ok:false payload)."""

from __future__ import annotations

import argparse
import json
import sys


def _emit(payload: dict) -> None:
    ok = bool(payload.get("ok", True))
    print(json.dumps(payload, separators=(",", ":")))
    sys.exit(0 if ok else 1)


def _cmd_capture(args: argparse.Namespace) -> None:
    from .capture import capture_still

    out = capture_still(
        out_dir=args.out,
        width=args.width,
        height=args.height,
        sensor_id=args.sensor_id,
        timeout_s=args.timeout,
    )
    _emit(out)


def _cmd_analyze(args: argparse.Namespace) -> None:
    from .analyze import analyze_image, DEFAULT_PROMPT

    out = analyze_image(
        path=args.image,
        prompt=args.prompt or DEFAULT_PROMPT,
        model=args.model,
        url=args.url,
        timeout_s=args.timeout,
    )
    _emit(out)


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clawfarmer-jetson")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("capture", help="Capture one still from the CSI camera")
    s.add_argument("--out", required=True, help="Output directory for the JPEG")
    s.add_argument("--width", type=int, default=4056, help="Capture width (IMX477 max 4056)")
    s.add_argument("--height", type=int, default=3040, help="Capture height (IMX477 max 3040)")
    s.add_argument("--sensor-id", type=int, default=0, help="CSI sensor id (0 or 1)")
    s.add_argument("--timeout", type=int, default=30, help="gst-launch timeout in seconds")
    s.set_defaults(func=_cmd_capture)

    s = sub.add_parser("analyze", help="Run a local vision model over an image via Ollama")
    s.add_argument("--image", required=True, help="Path to the JPEG to analyze")
    s.add_argument("--prompt", default=None, help="Override the default observation prompt")
    s.add_argument("--model", default="moondream", help="Ollama model name (default: moondream)")
    s.add_argument("--url", default="http://127.0.0.1:11434/api/generate",
                   help="Ollama generate endpoint")
    s.add_argument("--timeout", type=int, default=120, help="Ollama request timeout in seconds")
    s.set_defaults(func=_cmd_analyze)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
