# HEARTBEAT.md

# Orchestrated plant-care heartbeat

Canonical sources:
- Crop knowledge: `AGENTS.md`
- Hardware + thresholds: `TOOLS.md`
- Live sensor state: `memory/sensor-state.json`
- Growth log: `memory/growth-log.md`
- Observation workflow: `skills/plant-monitor`
- Action workflow: `skills/plant-care`

## Tick types

Heartbeats come from `cron/jobs.template.json`. The payload text tells the agent which tick this is. Expected tick types:

- `sensor-sweep` — read every sensor, update `memory/sensor-state.json`, flag anomalies
- `photo-capture` — capture a photo, land it in `{{PHOTO_SYNC_DIR}}`, log it
- `grow-light-check` — compare the current hour against `{{GROW_LIGHT_ON_HOUR}}`/`{{GROW_LIGHT_OFF_HOUR}}` and align the relay
- `care-decision` — re-read state, decide whether to water, and (unless `read_only: true`) act
- `daily-log` — write the end-of-day entry into `memory/growth-log.md` using readings + the latest photo
- `operator-ping` — direct chat turn from the operator; handle it like a normal conversational request

## On every heartbeat

1. Read `TOOLS.md` and obey the `read_only` knob before any actuator action.
2. Read `memory/sensor-state.json` to see what is already known.
3. Dispatch on the tick type:
   - observation-only ticks (`sensor-sweep`, `photo-capture`, `daily-log`) → `plant-monitor` skill
   - action ticks (`care-decision`, `grow-light-check`) → `plant-care` skill
4. Update `memory/sensor-state.json` in the same turn as any reading or action.
5. If the operator needs to know or act, send one short, direct update to `{{PRIMARY_UPDATE_CHANNEL}} -> {{PRIMARY_UPDATE_TARGET}}`.
6. If nothing is off and nothing material changed, reply: `HEARTBEAT_OK` — do not message the operator.

## Rules

- be proactive but do not create noise
- one update per tick at most; bundle alerts if a single tick surfaces multiple issues
- do not repeat a detection the operator already saw yesterday unless it materially worsened
- let `AGENTS.md` own what "healthy" means
- let `TOOLS.md` own pins, thresholds, and cooldowns
- let skills own their detailed procedures — this file is orchestration only
- if a sensor read fails, log the failure in `memory/sensor-state.json` under `last_errors[]` and retry on the next sweep; do not escalate transient failures until there are 3 in a row
- if the camera fails, skip the photo tick silently unless it has failed 3 days in a row
- keep any operator-facing message to 1–4 short lines
