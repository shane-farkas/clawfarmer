# dashboard/

Minimal web dashboard for the plant agent. Runs as a systemd service on Claw, reads the workspace state file and photos dir, serves a single-page view with current readings, photo history, and observations.

## Install on Claw

```bash
cd ~/clawfarmer && git pull

sudo install -m 0755 dashboard/clawfarmer-dashboard.py /usr/local/bin/clawfarmer-dashboard
sudo install -m 0644 dashboard/clawfarmer-dashboard.service /etc/systemd/system/

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
