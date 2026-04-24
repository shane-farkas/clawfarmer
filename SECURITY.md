# Security

## What to keep out of this repo

The repo is designed to be safe to publish as-is, but a few files get populated with real secrets during install and must not be committed:

- **Telegram bot token / chat id.** The `clawfarmer-host-tick@.service` unit ships with empty `TELEGRAM_BOT_TOKEN=` / `TELEGRAM_CHAT_ID=` values. Fill these in via a systemd drop-in, never by editing the unit file in-tree:

  ```bash
  sudo systemctl edit clawfarmer-host-tick@.service
  ```

  That writes to `/etc/systemd/system/clawfarmer-host-tick@.service.d/override.conf`, which lives outside this repo.

- **SSH private keys.** `~/.ssh/id_ed25519_plantpi` and `~/.ssh/id_ed25519_plantjetson` live under the `openclaw` user's home on Claw — never in the repo. `.gitignore` already excludes `id_*`, `*.pem`, `*.key`.

- **Photos.** Captured plant photos may contain incidental background (room, window view, people). `.gitignore` excludes `photos/` and common image extensions. If you want to share example photos, scrub EXIF and review the frame first.

- **`memory/sensor-state.json` in a running workspace.** The template in this repo is empty, but the copy under `/var/lib/openclaw/.openclaw/workspace-plant/memory/` accumulates real readings, watering history, and photo filenames. Do not copy that back into the repo.

## Reporting a vulnerability

Open a GitHub issue on this repo, or if the issue is sensitive, contact the maintainer via a GitHub DM before disclosing publicly.
