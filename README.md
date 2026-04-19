# clawfarmer

`clawfarmer` is an open-source OpenClaw starter pack for turning a plant growing rig into an autonomous caretaker. An OpenClaw plant agent reads soil moisture / temp / humidity / light, captures daily photos, decides when to water, when to run the grow light, and when to flag the operator, and keeps a daily growth log.

The default crop is **basil**. Swap `AGENTS.md` for any other crop by replacing the care-knowledge section.

## Reference architecture

Three hosts cooperate over SSH on the LAN:

- **Raspberry Pi** — sensors + pump (ADS1115 soil ADC, BME280 temp/humidity/pressure, BH1750 lux, logic-level MOSFET driving a 12V peristaltic pump). Runs `clawfarmer_pi` as a one-shot CLI over SSH.
- **Jetson Orin Nano Dev Kit** — camera + vision (Arducam Mini IMX477 over CSI, optional local Qwen vision later). Runs `clawfarmer_jetson` as a one-shot capture CLI over SSH.
- **OpenClaw host** (Ubuntu) — runs the OpenClaw daemon, the plant agent, Telegram bot, and the **host-side systemd timers** that do the SSH work the sandboxed agent can't.

## Key architectural constraint (important)

OpenClaw's non-default agents run scheduled work inside an **isolated sandbox that does not have `ssh` in its execution environment**. The agent's *chat* session has ssh; cron-driven `--session isolated` jobs don't, and there is no routing workaround.

So the pack splits work by layer:

- **Host systemd timers** (`host/`) do the SSH: sensor reads on the Pi, photo capture + scp on the Jetson. They run as the `openclaw` service user, on the host, every 15 min for sensors and twice a day for photos. They write results directly into the plant agent's workspace.
- **OpenClaw cron jobs** (reasoning-only) read `memory/sensor-state.json` and photos, apply the thresholds in `AGENTS.md`, and surface alerts to Telegram. They never SSH.
- **Chat session** can do anything — SSH, curl, shell — so ad-hoc debugging by messaging the bot still works end-to-end.

## What this repo gives you

- `AGENTS.md` — crop identity + care knowledge (basil defaults baked in)
- `TOOLS.md` — hardware + threshold config with `{{PLACEHOLDER}}` tokens
- `HEARTBEAT.md` — tick-type dispatch for the reasoning cron
- `skills/plant-monitor/SKILL.md` + `skills/plant-care/SKILL.md` — workflow specs
- `cron/jobs.template.json` — reasoning cron job template
- `memory/sensor-state.json` + `memory/growth-log.md` — live state + human-readable history
- `pi/` — Python package (`clawfarmer_pi`) for ADS1115 / BME280 / BH1750 / MOSFET + wiring / install / calibration docs
- `jetson/` — Python package (`clawfarmer_jetson`) for IMX477 capture via GStreamer + setup docs
- `host/` — Python host-tick script + systemd service/timer units that do the SSH work
- `INSTALL.md` — top-level walkthrough; `pi/SETUP.md` + `jetson/SETUP.md` + `host/README.md` cover the device-specific steps

## Repo layout

```
clawfarmer/
├── AGENTS.md                                  # crop knowledge
├── TOOLS.md                                   # hardware + thresholds (placeholders)
├── HEARTBEAT.md                               # tick dispatch
├── README.md  INSTALL.md
├── skills/
│   ├── plant-monitor/SKILL.md                 # observation workflow
│   └── plant-care/SKILL.md                    # action / drafting workflow
├── cron/jobs.template.json                    # OpenClaw reasoning cron
├── memory/
│   ├── sensor-state.json                      # live readings / photo / history
│   └── growth-log.md                          # daily dated entries
├── pi/
│   ├── clawfarmer_pi/                         # Python package
│   ├── pyproject.toml
│   ├── README.md  SETUP.md                    # wiring, install, calibration
├── jetson/
│   ├── clawfarmer_jetson/
│   ├── pyproject.toml
│   └── README.md  SETUP.md                    # CSI overlay, capture, install
└── host/
    ├── clawfarmer-host-tick.py                # host-side SSH runner
    ├── systemd/                               # service + 3 timers
    └── README.md                              # install + post-install cron rewrites
```

## Verified hardware (as built)

- Raspberry Pi 5 on Raspberry Pi OS Lite (Bookworm / Trixie)
- **Teyleten Robot ADS1115** (I²C 0x48) + **Songhe capacitive soil probe** (3.3V)
- **Starry GY-BME280** (I²C 0x76, 5V tolerant)
- **JESSINIE BH1750 GY-302** (I²C 0x23)
- **Diitao 12V peristaltic dosing pump** switched by a logic-level N-channel MOSFET on Pi GPIO (flyback diode + gate pull-down + series resistor required — see `pi/README.md`)
- Jetson Orin Nano Dev Kit 8GB, JetPack, 22-pin CSI
- **Arducam Mini 12.3MP IMX477** on CAM0 — IMX477 device-tree overlay enabled via `jetson-io.py`

## Install order

1. Read `INSTALL.md` for the top-level placeholders and workspace deploy
2. Flash the Pi (Raspberry Pi OS Lite 64-bit), follow `pi/SETUP.md` to enable I²C, install `clawfarmer_pi` in a venv, wire keys
3. Set up the Jetson per `jetson/SETUP.md`: enable the IMX477 overlay with `jetson-io.py`, install `clawfarmer_jetson`, wire the openclaw→jetson SSH key
4. Deploy the pack to the OpenClaw host (`sudo cp` to `/var/lib/openclaw/.openclaw/workspace-<agent>/`), replace placeholders with `sed`
5. Install the host-side plumbing per `host/README.md` (one Python script + three systemd timers)
6. Register OpenClaw cron jobs (reasoning-only) using the payloads in `cron/jobs.template.json`
7. Wire sensors, calibrate the soil probe, measure pump flow rate, fill remaining hardware placeholders

## Customization targets (do these first)

- `TOOLS.md` thresholds + calibration values
- `AGENTS.md` crop section (swap basil for your crop)
- `host/systemd/clawfarmer-host-tick@.service` `Environment=` lines (hosts, keys, soil calibration)
- OpenClaw cron payloads — each agent turn's `--message` text
- **Photo review cron must use an image-capable model** (e.g. Kimi-K2.5; set via `--model` on the cron). Text-only models cannot analyze the JPEG.

## Design pattern

Seven separations:

1. *crop knowledge* → `AGENTS.md`
2. *hardware config* → `TOOLS.md`
3. *orchestration of reasoning* → `HEARTBEAT.md` + OpenClaw cron
4. *observation workflow* → `skills/plant-monitor`
5. *action workflow* → `skills/plant-care`
6. *live state* → `memory/sensor-state.json`
7. *human history* → `memory/growth-log.md`

Plus a cross-cutting architectural split:

- **host-side** (`host/`) owns SSH and hardware IO
- **agent-side** (OpenClaw cron) owns reasoning + user-facing messages

Keeping those two layers cleanly separated is the main thing this repo is trying to teach, because it's the part that isn't obvious from the OpenClaw docs alone.

## Security posture

- The host-side script runs as the `openclaw` service user and uses per-device SSH keys at `/var/lib/openclaw/.ssh/id_ed25519_plant{pi,jetson}`. Authorized on the Pi and Jetson user accounts only.
- The agent's sandbox has `workspaceAccess: rw` and nothing else — it cannot reach the network-attached devices directly. All hardware mutation flows through the host.
- `read_only: true` in `TOOLS.md` gates actuator commands. The care-decision cron drafts proposals to Telegram in read-only mode instead of firing the pump.
- Pump has a hard cap of 60s per invocation inside `clawfarmer_pi.actuators.pulse_pump()` regardless of what the agent requests, and a cooldown guard in the skill.

## Private files you may still want

The public pack does not ship personal context files. Create your own if your setup depends on them: `USER.md`, `IDENTITY.md`, `SOUL.md`, `MEMORY.md`, `memory/` entries.
