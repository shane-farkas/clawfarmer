"""Camera capture. Shells out to gst-launch-1.0 — the Python GStreamer bindings
are heavy to install on Jetson and a subprocess call is perfectly fine for a
one-shot still capture."""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _filename_stamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%dT%H-%M-%S")


def capture_still(
    out_dir: str,
    width: int = 4056,
    height: int = 3040,
    sensor_id: int = 0,
    timeout_s: int = 30,
) -> dict:
    """Capture one frame from the CSI sensor into `out_dir` and return metadata.

    Uses nvarguscamerasrc → nvjpegenc → filesink. The argus daemon handles
    auto-exposure and auto-white-balance; the first couple of frames after a
    cold start can be over/underexposed, so we grab 3 frames and keep the last
    one to give AE/AWB a chance to converge.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    filename = f"{_filename_stamp()}.jpg"
    full_path = str(Path(out_dir) / filename)

    pipeline = (
        f"nvarguscamerasrc sensor-id={sensor_id} num-buffers=3 ! "
        f"video/x-raw(memory:NVMM),width={width},height={height},framerate=30/1 ! "
        f"nvjpegenc ! multifilesink location={full_path} max-files=1"
    )

    try:
        result = subprocess.run(
            ["gst-launch-1.0", "-q"] + pipeline.split(),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "error": f"gst-launch timed out after {timeout_s}s",
            "at": _now_iso(),
        }

    if not Path(full_path).exists():
        return {
            "ok": False,
            "error": "gst-launch returned without writing an output file",
            "stderr_tail": result.stderr[-500:] if result.stderr else None,
            "returncode": result.returncode,
            "at": _now_iso(),
        }

    size_bytes = os.path.getsize(full_path)

    return {
        "ok": True,
        "sensor": "imx477",
        "filename": filename,
        "path": full_path,
        "width": width,
        "height": height,
        "size_bytes": size_bytes,
        "at": _now_iso(),
    }
