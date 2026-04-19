# Install

End-to-end install across three devices: the OpenClaw host (Claw), the Raspberry Pi, and the Jetson. Follow in order.

## 0. Prerequisites

- OpenClaw is installed on your Ubuntu host and running as a systemd service — `systemctl status openclaw` shows `active (running)`, service user is `openclaw`, home is `/var/lib/openclaw/`. Start it with `sudo systemctl start openclaw`; it auto-starts on boot.
- The plant agent is registered (`openclaw agents add` if not). Its workspace lands at `/var/lib/openclaw/.openclaw/workspace-<agent-id>/` (e.g. `workspace-plant`).
- A messaging surface is paired with the agent (Telegram recommended) so the assistant can reach you proactively.
- You have a Raspberry Pi (Pi 4 or 5) and a Jetson Orin Nano Dev Kit.
- You understand the architectural constraint: **OpenClaw non-default agents run cron in an isolated sandbox that lacks `ssh`**. All hardware IO lives on the host side via systemd timers (`host/`). OpenClaw cron jobs are reasoning-only and read state files the host-side script wrote. If you try to have a plant-agent cron job directly SSH the Pi or Jetson, it will fail. See `README.md` "Key architectural constraint" for the full story.

## 1. Gather install values

### Identity + routing

- `{{OWNER_NAME}}`
- `{{AGENT_NAME}}` (must match the OpenClaw agent id, e.g. `plant`)
- `{{CROP_NAME}}` (default `basil` — swap for your crop)
- `{{TIMEZONE}}` (e.g. `America/Los_Angeles`)
- `{{PRIMARY_UPDATE_CHANNEL}}` (`telegram`)
- `{{PRIMARY_UPDATE_TARGET}}` (Telegram numeric chat id — get it from `@userinfobot`)
- `{{WORKSPACE_PATH}}` (typically `/var/lib/openclaw/.openclaw/workspace-plant`)

### SSH transport (used by the host-side tick script, not the agent)

- `{{PI_HOST}}` (e.g. `clawpi.local`)
- `{{PI_USER}}` (e.g. `pi`)
- `{{PI_SSH_KEY_PATH}}` (e.g. `/var/lib/openclaw/.ssh/id_ed25519_plantpi`)
- `{{JETSON_HOST}}` (e.g. `orin-nano.local`)
- `{{JETSON_USER}}` (the Linux user on the Jetson)
- `{{JETSON_SSH_KEY_PATH}}` (e.g. `/var/lib/openclaw/.ssh/id_ed25519_plantjetson`)

### Pi hardware (I²C + MOSFET)

- `{{ADS1115_I2C_ADDR}}` (default `0x48`)
- `{{BME280_I2C_ADDR}}` (`0x76` or `0x77`)
- `{{LIGHT_SENSOR_I2C_ADDR}}` (`0x23` or `0x5C`)
- `{{SOIL_MOISTURE_ADC_CHANNEL}}` (0–3 on the ADS1115)
- `{{SOIL_MOISTURE_DRY_RAW}}` / `{{SOIL_MOISTURE_WET_RAW}}` — captured during calibration (see `pi/README.md`)
- `{{WATER_PUMP_GPIO_PIN}}` — BCM pin driving the pump MOSFET gate (e.g. GPIO 17)
- `{{GROW_LIGHT_GPIO_PIN}}` / `{{FAN_GPIO_PIN}}` — relay pins (leave as placeholders if not wiring these yet)

### Jetson camera

- `{{CAMERA_DEVICE}}` (`/dev/video0` on the default single-camera Orin Nano setup)
- `{{CAMERA_RESOLUTION}}` (`4056x3040` for IMX477 native; `1920x1080` for a cheaper/faster capture)
- `{{PHOTO_OUTPUT_DIR}}` (where the Jetson writes — e.g. `/var/lib/clawfarmer/photos`)
- `{{PHOTO_SYNC_DIR}}` (where the host-tick script lands photos — `<workspace>/photos`)

### Care thresholds

Defaults in `AGENTS.md` are tuned for basil. Override if your cultivar or environment is different.

- `{{SOIL_MOISTURE_MIN}}` (water when at or below) — basil default ~35
- `{{SOIL_MOISTURE_MAX}}` (flag if above) — basil default ~85
- `{{TEMP_MIN_F}}` / `{{TEMP_MAX_F}}` — basil defaults 55 / 95
- `{{HUMIDITY_MIN}}` / `{{HUMIDITY_MAX}}` — basil defaults 30 / 80
- `{{GROW_LIGHT_ON_HOUR}}` / `{{GROW_LIGHT_OFF_HOUR}}` — basil default 08 / 22 (14 hours on)
- `{{WATER_PUMP_DURATION_SECONDS}}` — tune to your measured flow rate
- `{{WATER_COOLDOWN_MINUTES}}` — minimum between waterings (default 120)

## 2. Set up the Raspberry Pi

Full walkthrough in `pi/SETUP.md`. Summary:

1. Flash Raspberry Pi OS Lite 64-bit (Bookworm or Trixie) with your hostname, user, Wi-Fi, and laptop SSH pubkey baked in via Imager.
2. `sudo apt-get install -y python3-venv python3-pip python3-dev i2c-tools git swig liblgpio-dev`
3. `sudo raspi-config nonint do_i2c 0`
4. Copy `pi/` onto the Pi, create `~/clawfarmer-venv`, `pip install -e ~/clawfarmer-pi`
5. Generate `id_ed25519_plantpi` on Claw as the `openclaw` user; append its pubkey to the Pi user's `~/.ssh/authorized_keys`
6. Verify: `sudo -u openclaw -H ssh -i /var/lib/openclaw/.ssh/id_ed25519_plantpi pi@clawpi.local echo ok` prints `ok`

After wiring: `i2cdetect -y 1` should show `0x23`, `0x48`, `0x76`. Calibrate the soil probe (dry reading in air + wet reading in water) and save both `dry_raw` and `wet_raw` for step 6.

## 3. Set up the Jetson

Full walkthrough in `jetson/SETUP.md`. Summary:

1. Start with JetPack installed and SSH working from another machine.
2. Enable the IMX477 CSI overlay: `sudo /opt/nvidia/jetson-io/jetson-io.py` → Configure 24-pin CSI → Camera IMX477 → Save and reboot. Verify `/dev/video0` appears and `dmesg | grep imx477` shows `bound`.
3. Physical capture smoke test with `gst-launch-1.0 nvarguscamerasrc num-buffers=1 ...`.
4. Copy `jetson/` onto the Jetson, create `~/clawfarmer-venv`, `pip install -e ~/clawfarmer/jetson`.
5. `sudo mkdir -p /var/lib/clawfarmer/photos && sudo chown $USER:$USER ...`
6. Generate `id_ed25519_plantjetson` on Claw as `openclaw`; append its pubkey to the Jetson user's `~/.ssh/authorized_keys`. **Watch the paste format** — any garbage before `ssh-ed25519 AAAA…` on a line will make sshd silently reject that line and fall back to password auth (which will then fail for non-interactive cron).
7. Verify: `sudo -u openclaw -H ssh -i /var/lib/openclaw/.ssh/id_ed25519_plantjetson -o BatchMode=yes <user>@<host> echo ok` prints `ok` with no password prompt.

## 4. Deploy the workspace pack to Claw

```bash
cd ~/clawfarmer && git pull

# stop the daemon so there's no race with workspace auto-heal
sudo systemctl stop openclaw

sudo cp -r ~/clawfarmer/AGENTS.md \
            ~/clawfarmer/TOOLS.md \
            ~/clawfarmer/HEARTBEAT.md \
            ~/clawfarmer/skills \
            ~/clawfarmer/memory \
            /var/lib/openclaw/.openclaw/workspace-plant/
sudo chown -R openclaw:openclaw /var/lib/openclaw/.openclaw/workspace-plant/

sudo systemctl start openclaw
```

Verify the deploy landed:

```bash
sudo grep -c 'ADS1115\|BME280\|clawfarmer_pi' /var/lib/openclaw/.openclaw/workspace-plant/TOOLS.md
# should print a non-zero number
```

## 5. Replace placeholders in the deployed workspace

```bash
WS=/var/lib/openclaw/.openclaw/workspace-plant
for p in "{{OWNER_NAME}}=<your name>" \
         "{{AGENT_NAME}}=plant" \
         "{{CROP_NAME}}=basil" \
         "{{TIMEZONE}}=America/Los_Angeles" \
         "{{PRIMARY_UPDATE_CHANNEL}}=telegram" \
         "{{PRIMARY_UPDATE_TARGET}}=<your telegram chat id>" \
         "{{WORKSPACE_PATH}}=$WS" \
         "{{PI_HOST}}=clawpi.local" \
         "{{PI_USER}}=pi" \
         "{{PI_SSH_KEY_PATH}}=/var/lib/openclaw/.ssh/id_ed25519_plantpi" \
         "{{JETSON_HOST}}=orin-nano.local" \
         "{{JETSON_USER}}=<jetson user>" \
         "{{JETSON_SSH_KEY_PATH}}=/var/lib/openclaw/.ssh/id_ed25519_plantjetson" \
         "{{ADS1115_I2C_ADDR}}=0x48" \
         "{{BME280_I2C_ADDR}}=0x76" \
         "{{LIGHT_SENSOR_I2C_ADDR}}=0x23" \
         "{{CAMERA_DEVICE}}=/dev/video0" \
         "{{CAMERA_RESOLUTION}}=4056x3040" \
         "{{PHOTO_OUTPUT_DIR}}=/var/lib/clawfarmer/photos" \
         "{{PHOTO_SYNC_DIR}}=$WS/photos" \
         "{{GROW_LIGHT_ON_HOUR}}=8" \
         "{{GROW_LIGHT_OFF_HOUR}}=22" \
         "{{WATER_PUMP_DURATION_SECONDS}}=10" \
         "{{WATER_COOLDOWN_MINUTES}}=120" \
         "{{SENSOR_TRANSPORT}}=ssh"; do
  key="${p%%=*}"; val="${p#*=}"
  sudo find $WS -type f \( -name '*.md' -o -name '*.json' \) -exec sed -i "s|${key}|${val}|g" {} +
done

# verify only hardware-TBD and literal template tokens remain
sudo grep -RE '\{\{[^}]+\}\}' $WS/ --include='*.md' --include='*.json' | sort -u
```

Remaining `{{...}}` should only be things you fill after wiring (GPIO pins, calibration raw values) and the `{{YYYY-MM-DD}}` literal in `growth-log.md`'s template.

## 6. Install the host-side systemd plumbing

```bash
sudo install -m 0755 ~/clawfarmer/host/clawfarmer-host-tick.py /usr/local/bin/clawfarmer-host-tick
sudo install -m 0644 ~/clawfarmer/host/systemd/*.service ~/clawfarmer/host/systemd/*.timer /etc/systemd/system/
```

Edit `/etc/systemd/system/clawfarmer-host-tick@.service` to match your hostnames/keys if they differ from the defaults there, then:

```bash
sudo systemctl daemon-reload

sudo systemctl enable --now clawfarmer-host-sensors.timer
sudo systemctl enable --now clawfarmer-host-photo-morning.timer
sudo systemctl enable --now clawfarmer-host-photo-evening.timer

systemctl list-timers 'clawfarmer-host-*'
```

Smoke test immediately with an ad-hoc photo fire:

```bash
sudo systemctl start clawfarmer-host-tick@photo.service
sudo journalctl -u clawfarmer-host-tick@photo.service --since "1 min ago" --no-pager
sudo ls -la /var/lib/openclaw/.openclaw/workspace-plant/photos/
```

Expected: a `{"ok": true, ...}` JSON line in the journal and a fresh timestamped JPEG in the photos dir.

Full install reference: `host/README.md`.

## 7. Create OpenClaw cron jobs (reasoning-only)

Non-default agents must use `--session isolated` with `--message` payloads. Create one at a time (paste-queuing multiple at once lets the CLI's TUI eat subsequent commands):

```bash
sudo -u openclaw -H openclaw cron add --agent plant --disabled \
  --name "Sensor sweep" --cron "*/15 * * * *" --session isolated \
  --message "sensor-sweep. Read memory/sensor-state.json. If updated_at is more than 30 minutes old, or any reading is outside the healthy bands in AGENTS.md, surface a one-line alert to the operator. If last_errors shows 3+ consecutive failures on the same sensor, surface that too. Otherwise stay silent. Do NOT attempt ssh — the host systemd timer does all ssh work."
```

Repeat for:

- **Care decision** — `*/15 * * * *`, message about reading state + AGENTS.md + TOOLS.md and drafting actuator proposals in read-only mode, no ssh.
- **Grow-light check** — `0 * * * *`, message about comparing `grow_light.state` against the TOOLS.md on/off window, drafting toggles, no ssh.
- **Daily growth log** — `30 21 * * *`, message about appending a dated block to `memory/growth-log.md` using today's readings + latest photo analysis.
- **Photo review** (uses an image-capable model) — `5 8,19 * * *` with `--model together/moonshotai/Kimi-K2.5`, message about reading `last_photo.filename`, opening the JPEG, observing it per AGENTS.md cues, sending a 3-5 line Telegram update.

Enable them once you've verified each fires cleanly:

```bash
sudo -u openclaw -H openclaw cron list --all | grep plant
sudo -u openclaw -H openclaw cron enable <job-id>
```

## 8. Safety gates

Before the pump is ever driven for real:

- `read_only: true` in `TOOLS.md` (default). The care-decision cron drafts actuator proposals to Telegram instead of firing the pump.
- The `clawfarmer_pi.actuators.pulse_pump` call has a hard 60s cap regardless of what the agent asks for.
- Measure the actual pump flow rate with a timed pulse into a graduated container before you let it run unattended. Tune `{{WATER_PUMP_DURATION_SECONDS}}` to deliver your target volume per shot.
- Flip `read_only: false` in `TOOLS.md` only after at least a day of clean readings and drafted-proposal confirmations.

## 9. Validation checklist

### Pi
- [ ] `i2cdetect -y 1` on the Pi shows `0x23`, `0x48`, `0x76`
- [ ] `clawfarmer-pi read-bme280` / `read-lux` / `read-soil` all return `{"ok":true,...}` JSON
- [ ] `sudo -u openclaw -H ssh -i /var/lib/openclaw/.ssh/id_ed25519_plantpi pi@clawpi.local echo ok` prints `ok`

### Jetson
- [ ] `/dev/video0` exists and `dmesg | grep imx477` shows `bound`
- [ ] `clawfarmer-jetson capture --out /var/lib/clawfarmer/photos` returns a JSON with `ok:true` and a filename
- [ ] `sudo -u openclaw -H ssh -i /var/lib/openclaw/.ssh/id_ed25519_plantjetson -o BatchMode=yes <user>@<host> echo ok` prints `ok`

### Host-side plumbing on Claw
- [ ] `systemctl list-timers 'clawfarmer-host-*'` shows three timers with next-fire times
- [ ] `clawfarmer-host-tick@photo.service` run produces a JPEG in `<workspace>/photos`
- [ ] `clawfarmer-host-tick@sensors.service` run updates `memory/sensor-state.json` (with real readings once sensors are wired; with `last_errors` entries before then)

### Reasoning loop
- [ ] All 5 OpenClaw plant crons exist, enabled, with isolated session + agentTurn payloads
- [ ] Manually triggering Sensor sweep against healthy readings → no Telegram message
- [ ] Manually triggering Sensor sweep against a forced anomaly → one clear Telegram alert with reading + threshold
- [ ] Photo review sends a short Telegram observation after a capture lands
- [ ] Daily growth log appends one dated block per day, doesn't rewrite prior days, doesn't write on days with no valid data

### Safety
- [ ] `read_only: true` verified in the deployed `TOOLS.md`
- [ ] Care-decision cron produces a drafted watering proposal (not a real pump pulse) for a simulated dry reading
- [ ] Pump flow rate measured, duration tuned in `{{WATER_PUMP_DURATION_SECONDS}}` before write mode

If every box is ticked, the install is good. Customize from there.
