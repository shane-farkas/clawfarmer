# dashboard/

Minimal web dashboard for the plant agent. Runs as a systemd service on Claw, reads the workspace state file and photos dir, serves a single-page view with current readings, photo history, and observations.

## Install on Claw

```bash
cd ~/clawfarmer && git pull

sudo install -m 0755 dashboard/clawfarmer-dashboard.py /usr/local/bin/clawfarmer-dashboard
sudo install -m 0644 dashboard/clawfarmer-dashboard.service /etc/systemd/system/

# sudoers rule that lets the openclaw user trigger the two whitelisted
# host-tick services without a password (photo + sensors). This is
# scoped tight; it does NOT grant openclaw broader sudo.
sudo install -m 0440 dashboard/clawfarmer-dashboard.sudoers /etc/sudoers.d/clawfarmer-dashboard
sudo visudo -cf /etc/sudoers.d/clawfarmer-dashboard   # should print "parsed OK"

sudo systemctl daemon-reload
sudo systemctl enable --now clawfarmer-dashboard
systemctl status clawfarmer-dashboard --no-pager
```

## Access

From any device on your LAN:

```
http://openclaw-host.local:8765/
```

Or use Claw's IP if mDNS is flaky on your network.

## What it shows

- **Current readings** (soil, temp, humidity, lux) with health-status dots (green/yellow/red based on basil thresholds from AGENTS.md)
- **Today's min/max** per reading
- **Latest photo** full-size with Moondream's observation
- **Gallery** of the last 12 photos with timestamps
- **Watering history** (once you wire the pump)
- **Recent errors** (only shown if `last_errors[]` has entries)

Auto-refreshes every 60s.

## Manual triggers

Two buttons at the top of the dashboard:

- **📸 Take photo now** — POSTs to `/trigger/capture`, fires `clawfarmer-host-tick@photo.service`, redirects back with a flash message
- **🌡️ Read sensors now** — POSTs to `/trigger/sensors`, fires `clawfarmer-host-tick@sensors.service`, redirects back

After clicking, the capture takes ~30-60s (capture + scp + Moondream analysis) and the sensor sweep takes ~2-3s. The page's 60s auto-refresh will pick up the new reading / photo automatically.

Both triggers go through `sudo -n systemctl start <whitelisted service>`, with the sudoers rule installed above scoping exactly those two commands for the `openclaw` user.

## Config (override via systemctl edit)

```bash
sudo systemctl edit clawfarmer-dashboard
```

Available environment variables:
- `CLAWFARMER_WORKSPACE` — default `/var/lib/openclaw/.openclaw/workspace-plant`
- `DASHBOARD_PORT` — default `8765`
- `DASHBOARD_BIND` — default `0.0.0.0` (LAN-accessible). Set to `127.0.0.1` to restrict to the host itself.

## Security note

No auth. Intended for a trusted home LAN behind a NAT router. If you ever expose Claw to the internet, bind to `127.0.0.1` and tunnel via SSH or Tailscale instead.
