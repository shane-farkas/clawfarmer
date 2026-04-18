# clawfarmer

`clawfarmer` is an open-source OpenClaw starter pack for turning a plant growing rig into an autonomous caretaker. It runs as an OpenClaw agent, reads from an assortment of sensors (camera, soil moisture, temperature, humidity, light), decides when to water, when to run the grow light, and when to flag the operator, and keeps a daily growth log.

This pack ships opinionated about the *architecture* of the workflow. Hardware — which sensors, which pins, which host — is left as `{{PLACEHOLDER}}` tokens so the same skills run on any rig you wire up.

The default crop is **basil**. Swap `AGENTS.md` for any other crop by replacing the care-knowledge section.

## What this repo gives you

- an `AGENTS.md` that teaches the agent how to care for the target crop
- a hardware config layer (`TOOLS.md`) with `{{PLACEHOLDER}}` tokens for pins, thresholds, and transport
- an orchestrator heartbeat (`HEARTBEAT.md`) for sensor polling, photo capture, and alerting
- two skills:
  - `plant-monitor` — sensor reading, alerting, anomaly detection
  - `plant-care` — watering, lighting, environment-control decisions
- a cron template with daily photo, daily growth log, and short-cycle sensor sweeps
- memory files for live sensor state and a human-readable growth log

## Target hardware (reference architecture)

- **Jetson Orin Nano Dev Kit** — camera, vision, photo capture
- **Raspberry Pi** — GPIO sensors and actuators (soil moisture, DHT22, grow-light relay, water-pump relay)
- **OpenClaw host** — runs the agent; reaches the Jetson and Pi over your chosen transport

The skills are deliberately transport-agnostic — see `TOOLS.md` for the `{{SENSOR_TRANSPORT}}` knob.

## Repo layout

### Workspace-level files

- `AGENTS.md` — crop identity + care knowledge
- `TOOLS.md` — hardware + environment config
- `HEARTBEAT.md` — orchestrator tick logic

### Skills

- `skills/plant-monitor/SKILL.md`
- `skills/plant-care/SKILL.md`

### Cron

- `cron/jobs.template.json`

### Memory

- `memory/sensor-state.json` — latest readings + watering history
- `memory/growth-log.md` — daily observations

### Setup docs

- `INSTALL.md`

## The design pattern

The system works best when you separate:

1. *crop knowledge* → `AGENTS.md`
2. *hardware config* → `TOOLS.md`
3. *orchestration* → `HEARTBEAT.md` + `cron/jobs.template.json`
4. *observation workflow* → `skills/plant-monitor`
5. *action workflow* → `skills/plant-care`
6. *live state* → `memory/sensor-state.json`
7. *human-readable history* → `memory/growth-log.md`

Skills hold the procedures. `TOOLS.md` holds the environment. `AGENTS.md` holds the crop. Cron holds the cadence.

## Install order

1. Read `INSTALL.md`
2. Wire up the Pi sensors/actuators and the Jetson camera
3. Decide on a transport and write it into `TOOLS.md`
4. Copy skills and workspace files into `<workspace>/`
5. Replace placeholders
6. Create cron jobs from `cron/jobs.template.json`

## Good customization targets

Customize these first:

- `TOOLS.md` (every placeholder)
- `AGENTS.md` (swap basil for your crop)
- `cron/jobs.template.json` (cadence)
- `skills/plant-care/SKILL.md` (watering / lighting rules that depend on your rig)
