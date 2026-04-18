# clawfarmer-jetson

Jetson-side camera capture helper. Runs on the Jetson and is shelled into from the OpenClaw host by the `plant-monitor` skill on every `photo-capture` and `daily-log` heartbeat.

## Hardware it speaks to

- **IMX477 CSI camera** on `CAM0` (or `CAM1` via `--sensor-id 1`) — verified against the Arducam Mini 12.3MP HQ module (UC-698 equivalent with M12 lens)
- Stock nvidia-l4t-jetson-multimedia GStreamer pipeline: `nvarguscamerasrc → nvjpegenc → multifilesink`

Qwen / vision inference runs separately — this package does capture only. Vision is a follow-up step.

## Prereqs on the Jetson

- JetPack with the IMX477 device-tree overlay enabled via `sudo /opt/nvidia/jetson-io/jetson-io.py`
- `/dev/video0` present (`ls /dev/video*`) and `dmesg` shows `imx477 ... bound`
- `gst-launch-1.0` and `nvarguscamerasrc` installed (ship with JetPack)

## Install

```bash
# on the Jetson, as your normal user
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip

python3 -m venv ~/clawfarmer-venv
source ~/clawfarmer-venv/bin/activate
pip install -e ~/clawfarmer/jetson  # or wherever you rsync/clone the repo
```

Sanity check:

```bash
~/clawfarmer-venv/bin/python3 -m clawfarmer_jetson --help
```

Should print the `capture` subcommand.

## Commands

```bash
# capture one 4056x3040 still into /var/lib/clawfarmer/photos
python3 -m clawfarmer_jetson capture --out /var/lib/clawfarmer/photos
# {"ok":true,"sensor":"imx477","filename":"2026-04-18T15-42-17.jpg","path":"/var/lib/clawfarmer/photos/...","width":4056,"height":3040,"size_bytes":2841739,"at":"..."}

# smaller / faster capture if you don't need full res
python3 -m clawfarmer_jetson capture --out /tmp --width 1920 --height 1080
```

Every invocation prints one JSON line. Exit code 0 on success, 1 on failure.

## Capture pipeline notes

- Grabs 3 frames, keeps the last one — gives Argus's AE/AWB a moment to converge. First-frame captures from a cold start are often over/underexposed.
- Filename is UTC-local ISO timestamp (`YYYY-MM-DDTHH-MM-SS.jpg`). Matches the convention expected by `plant-monitor` daily-log skill.
- Native IMX477 resolution is 4056×3040 (12.3MP). That's ~2–5 MB per JPEG. For multi-day retention on an eMMC or microSD, see the photo sync plan in `TOOLS.md`.
- Has a 30s internal timeout on the GStreamer call; if argus-daemon hangs, the capture returns `ok:false` with the stderr tail instead of blocking forever.

## Photo sync to Claw

The Jetson is the originator; Claw is the reader. The `plant-monitor` SKILL.md has OpenClaw (running as the `openclaw` user) SSH into the Jetson and either:

1. **Call capture then scp the result back:**
   ```bash
   # remote capture
   sudo -u openclaw ssh -i {{JETSON_SSH_KEY_PATH}} {{JETSON_USER}}@{{JETSON_HOST}} \
     "~/clawfarmer-venv/bin/python3 -m clawfarmer_jetson capture --out {{PHOTO_OUTPUT_DIR}}"
   # pull the latest JPEG back to Claw
   sudo -u openclaw scp -i {{JETSON_SSH_KEY_PATH}} \
     {{JETSON_USER}}@{{JETSON_HOST}}:{{PHOTO_OUTPUT_DIR}}/*.jpg \
     {{PHOTO_SYNC_DIR}}/
   ```
2. Or Claw can rsync `{{PHOTO_OUTPUT_DIR}}` periodically.

The capture command already returns the JSON path of the new file; a future iteration can scp just that one file instead of globbing the whole dir.

## What's intentionally out of scope

- No vision / Qwen inference here — `plant-monitor`'s daily-log skill reads the image and runs vision separately.
- No MQTT / HTTP transports yet — SSH only, mirroring the Pi package.
- No camera tuning / ISP overrides — if AE/AWB defaults need to change (e.g. for consistent daily comparisons), we'll add `--exposure`, `--gain`, `--wbmode` flags later.
