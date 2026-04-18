# SETUP.md — Jetson camera capture for clawfarmer

Short version. Assumes JetPack is already installed, you can SSH into the Jetson from another machine, and the `clawfarmer` repo is accessible (either rsync'd from your dev box or cloned from GitHub).

## 1. Enable the IMX477 device-tree overlay

Stock JetPack boots without a CSI overlay — `/dev/video*` won't exist. Fix once:

```bash
sudo /opt/nvidia/jetson-io/jetson-io.py
```

TUI walkthrough:
1. **Configure Jetson 24-pin CSI Connector**
2. **Camera IMX477-A** (or **Camera IMX477** for a single camera on CAM0)
3. **Save pin changes** → **Save and reboot to reconfigure pins**

After reboot, verify:

```bash
ls -la /dev/video*                                   # should show /dev/video0
sudo dmesg | grep -iE "imx477|nvcsi" | tail -10      # should show "imx477 ... bound"
```

If dmesg shows `error during i2c read probe (-121)` → the ribbon is flipped at one end. Power off, pull the cable, rotate 180° at one end (metal contacts swap sides), reseat, power on. Retry.

## 2. Single-frame capture smoke test

```bash
gst-launch-1.0 -v nvarguscamerasrc num-buffers=1 ! \
  'video/x-raw(memory:NVMM),width=4056,height=3040,framerate=30/1' ! \
  nvjpegenc ! filesink location=/tmp/test.jpg
```

`file /tmp/test.jpg` should report a 4056×3040 JPEG of ~2–5 MB. SCP it to another machine and eyeball it before continuing.

## 3. Install the `clawfarmer_jetson` package

Get the repo onto the Jetson (rsync from Claw or git clone), then:

```bash
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip

python3 -m venv ~/clawfarmer-venv
source ~/clawfarmer-venv/bin/activate
pip install -e ~/clawfarmer/jetson
```

Verify:

```bash
~/clawfarmer-venv/bin/python3 -m clawfarmer_jetson --help
```

Should print the `capture` subcommand.

Same gotcha as the Pi: non-interactive SSH doesn't reliably source `~/.bashrc` on Ubuntu. The SKILL.md commands from Claw use the absolute venv path `~/clawfarmer-venv/bin/python3`.

## 4. Create the photo output dir

```bash
sudo mkdir -p /var/lib/clawfarmer/photos
sudo chown $USER:$USER /var/lib/clawfarmer/photos
```

(Or pick any path you want, but remember to set `{{PHOTO_OUTPUT_DIR}}` to match in TOOLS.md later.)

## 5. End-to-end capture test

```bash
~/clawfarmer-venv/bin/python3 -m clawfarmer_jetson capture \
  --out /var/lib/clawfarmer/photos
```

Should print one JSON line like:

```json
{"ok":true,"sensor":"imx477","filename":"2026-04-18T15-42-17.jpg","path":"/var/lib/clawfarmer/photos/2026-04-18T15-42-17.jpg","width":4056,"height":3040,"size_bytes":2841739,"at":"..."}
```

And the JPEG lands in the output dir.

## 6. Give the openclaw service user on Claw SSH access

Same pattern as the Pi. On **Claw**:

```bash
sudo -u openclaw -H ssh-keygen -t ed25519 -N "" -f /var/lib/openclaw/.ssh/id_ed25519_plantjetson
sudo cat /var/lib/openclaw/.ssh/id_ed25519_plantjetson.pub
```

On the **Jetson**:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "<paste the pubkey from Claw>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Verify from Claw:

```bash
sudo -u openclaw -H ssh \
  -i /var/lib/openclaw/.ssh/id_ed25519_plantjetson \
  -o StrictHostKeyChecking=accept-new \
  {{JETSON_USER}}@{{JETSON_HOST}} echo ok
```

Should print `ok`.

## 7. Fill in TOOLS.md placeholders

On Claw, substitute the Jetson values in the deployed workspace:

```bash
WS=/var/lib/openclaw/.openclaw/workspace-plant
sudo find $WS -type f \( -name '*.md' -o -name '*.json' \) -exec sed -i 's|{{JETSON_HOST}}|orin-nano.local|g' {} +
sudo find $WS -type f \( -name '*.md' -o -name '*.json' \) -exec sed -i 's|{{JETSON_USER}}|shane|g' {} +
sudo find $WS -type f \( -name '*.md' -o -name '*.json' \) -exec sed -i 's|{{JETSON_SSH_KEY_PATH}}|/var/lib/openclaw/.ssh/id_ed25519_plantjetson|g' {} +
sudo find $WS -type f \( -name '*.md' -o -name '*.json' \) -exec sed -i 's|{{CAMERA_DEVICE}}|/dev/video0|g' {} +
sudo find $WS -type f \( -name '*.md' -o -name '*.json' \) -exec sed -i 's|{{CAMERA_RESOLUTION}}|4056x3040|g' {} +
sudo find $WS -type f \( -name '*.md' -o -name '*.json' \) -exec sed -i 's|{{PHOTO_OUTPUT_DIR}}|/var/lib/clawfarmer/photos|g' {} +
sudo find $WS -type f \( -name '*.md' -o -name '*.json' \) -exec sed -i 's|{{PHOTO_SYNC_DIR}}|/var/lib/clawfarmer/photos|g' {} +
```

(Substitute your actual Jetson username / hostname / preferred paths.)

## 8. End-to-end test from Claw

```bash
sudo -u openclaw -H ssh -i /var/lib/openclaw/.ssh/id_ed25519_plantjetson \
  shane@orin-nano.local \
  "~/clawfarmer-venv/bin/python3 -m clawfarmer_jetson capture --out /var/lib/clawfarmer/photos"
```

Should print the JSON response — proves the whole chain works.

## 9. Enable the photo cron jobs

Once the above is all green, enable the morning + evening photo jobs you created earlier:

```bash
sudo -u openclaw -H openclaw cron enable 996d17e7-680a-4b42-a077-88d9f4852776  # Morning photo
sudo -u openclaw -H openclaw cron enable 9905fe40-e4b3-4b3f-9b20-90b7993ea289  # Evening photo
```

(Use `openclaw cron list --all | grep photo` to get the IDs if they differ.)

## Troubleshooting

- `/dev/video0` missing after jetson-io reboot → overlay didn't save. Re-run jetson-io, make sure to pick "Save and reboot", not "Save".
- `gst-launch` hangs → the nvargus-daemon might be in a bad state. `sudo systemctl restart nvargus-daemon` and retry.
- Capture succeeds but photo is completely black → lens cap still on, or Argus AE hasn't converged (the package already grabs 3 frames to give it time; if this happens often, bump `num-buffers` in `capture.py`).
- Capture at 4056×3040 is slow → drop `--width` and `--height` to something like 1920×1080 for faster turnaround. Full res matters for Qwen vision later; 1080p is fine for daily-log sanity checks.
