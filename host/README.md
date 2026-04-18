# host/ — host-side plumbing

Why this exists: OpenClaw's non-default agents run cron jobs inside a sandboxed "isolated" session that does not have `ssh` in its execution environment. The agent's *chat* session does have ssh, but cron doesn't, and there's no routing workaround. So we move the "SSH out to hardware" work to the host, where it runs as the `openclaw` service user (which does have ssh), and have it write results into the agent's workspace. The agent's OpenClaw cron then becomes pure reasoning over those state files.

## Files

- [`clawfarmer-host-tick.py`](clawfarmer-host-tick.py) — single Python script. Two modes: `sensors` (SSH to the Pi, read soil/BME280/BH1750, update `memory/sensor-state.json`) and `photo` (SSH to the Jetson, capture via `clawfarmer_jetson`, scp the JPEG back to `workspace-plant/photos/`, update `last_photo` in state).
- [`systemd/clawfarmer-host-tick@.service`](systemd/clawfarmer-host-tick@.service) — service template; `%I` is the action (`sensors` or `photo`).
- [`systemd/clawfarmer-host-sensors.timer`](systemd/clawfarmer-host-sensors.timer) — every 15 min.
- [`systemd/clawfarmer-host-photo-morning.timer`](systemd/clawfarmer-host-photo-morning.timer) — 08:00 local.
- [`systemd/clawfarmer-host-photo-evening.timer`](systemd/clawfarmer-host-photo-evening.timer) — 19:00 local.

## Install (on Claw)

```bash
cd ~/clawfarmer && git pull

# script → /usr/local/bin, executable, openclaw-readable
sudo install -m 0755 host/clawfarmer-host-tick.py /usr/local/bin/clawfarmer-host-tick

# systemd units → /etc/systemd/system
sudo install -m 0644 host/systemd/clawfarmer-host-tick@.service          /etc/systemd/system/
sudo install -m 0644 host/systemd/clawfarmer-host-sensors.timer          /etc/systemd/system/
sudo install -m 0644 host/systemd/clawfarmer-host-photo-morning.timer    /etc/systemd/system/
sudo install -m 0644 host/systemd/clawfarmer-host-photo-evening.timer    /etc/systemd/system/

sudo systemctl daemon-reload

# enable + start
sudo systemctl enable --now clawfarmer-host-sensors.timer
sudo systemctl enable --now clawfarmer-host-photo-morning.timer
sudo systemctl enable --now clawfarmer-host-photo-evening.timer
```

## Smoke test before enabling timers

```bash
# sensors: will fail on sensor reads until Pi is wired (expected), but proves the script runs
sudo systemctl start clawfarmer-host-tick@sensors.service
sudo journalctl -u clawfarmer-host-tick@sensors.service --since "2 min ago" --no-pager

# photo: should land a JPEG in workspace-plant/photos/
sudo systemctl start clawfarmer-host-tick@photo.service
sudo journalctl -u clawfarmer-host-tick@photo.service --since "2 min ago" --no-pager

# confirm the photo synced
sudo ls -la /var/lib/openclaw/.openclaw/workspace-plant/photos/
```

## Timer status

```bash
systemctl list-timers 'clawfarmer-host-*'
```

Shows next-fire times and last-fire times for all three timers.

## Config

Hardware defaults are baked into `clawfarmer-host-tick@.service` as `Environment=` lines. To retune after calibration:

```bash
sudo systemctl edit clawfarmer-host-tick@sensors.service
```

Add an override like:

```
[Service]
Environment=SOIL_DRY_RAW=25400
Environment=SOIL_WET_RAW=11800
Environment=SOIL_CHANNEL=0
```

Then `sudo systemctl daemon-reload && sudo systemctl restart clawfarmer-host-sensors.timer`. The service-level edit applies to both `@sensors` and `@photo` instances because they share the template.

## After this lands: adjust the OpenClaw cron jobs

Once the host timers are running, the plant agent's OpenClaw cron jobs should be reasoning-only — they shouldn't try to SSH anywhere. Change or recreate them so their payload is:

- **Sensor sweep** → "Read memory/sensor-state.json. If any reading is anomalous (see AGENTS.md thresholds) or stale (>30 min), message the operator. Otherwise stay silent."
- **Care decision** → "Read memory/sensor-state.json. Decide whether a watering, grow-light toggle, or fan action is appropriate per AGENTS.md + TOOLS.md. If read_only: true (default), draft the proposed command to the operator. Do not attempt SSH."
- **Morning photo / Evening photo** → **disable these OpenClaw jobs**; the host timers handle them now.
- **Daily growth log** → "Read today's readings from memory/sensor-state.json, analyze the latest photo in photos/, append a dated entry to memory/growth-log.md per the template. Message operator only on new failure-mode detection."

Disable the two photo jobs once the host timers take over:

```bash
sudo -u openclaw -H openclaw cron disable 996d17e7-680a-4b42-a077-88d9f4852776  # Morning photo (OpenClaw)
sudo -u openclaw -H openclaw cron disable 9905fe40-e4b3-4b3f-9b20-90b7993ea289  # Evening photo (OpenClaw)
```

(IDs will differ if you recreated jobs — use `openclaw cron list --all | grep photo` to find yours.)
