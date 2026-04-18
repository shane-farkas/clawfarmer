# Install

Follow this in order.

## 0. Prerequisites

- OpenClaw is running on the host and the plant agent is registered (agent id `plant`, workspace at `/var/lib/openclaw/.openclaw/workspace-plant/`).
- A messaging surface is paired with the agent (Telegram chat, Slack DM, etc.) so the assistant can reach you proactively.
- Hardware is wired up (or is about to be — the placeholder pattern means you can install the pack and fill pins later).

## 1. Gather install values

Collect these before editing files:

- `{{OWNER_NAME}}`
- `{{AGENT_NAME}}`
- `{{TIMEZONE}}`
- `{{PRIMARY_UPDATE_CHANNEL}}`
- `{{PRIMARY_UPDATE_TARGET}}`
- `{{WORKSPACE_PATH}}`
- `{{CROP_NAME}}` (defaults to `basil` — swap if using a different crop)

Sensor transport:

- `{{SENSOR_TRANSPORT}}` (one of: `ssh`, `mqtt`, `http`, `mock`)
- `{{PI_HOST}}`
- `{{PI_USER}}`
- `{{PI_SSH_KEY_PATH}}` (if using ssh)
- `{{JETSON_HOST}}`
- `{{JETSON_USER}}`
- `{{JETSON_SSH_KEY_PATH}}` (if using ssh)

Hardware pins / devices (Raspberry Pi):

- `{{SOIL_MOISTURE_ADC_CHANNEL}}` (e.g. MCP3008 channel 0)
- `{{TEMP_HUMIDITY_GPIO_PIN}}` (e.g. DHT22 on GPIO4)
- `{{LIGHT_SENSOR_I2C_ADDR}}` (e.g. BH1750 at 0x23)
- `{{WATER_PUMP_GPIO_PIN}}` (relay)
- `{{GROW_LIGHT_GPIO_PIN}}` (relay)
- `{{FAN_GPIO_PIN}}` (relay, optional)

Camera (Jetson):

- `{{CAMERA_DEVICE}}` (e.g. `/dev/video0` or CSI camera id)
- `{{CAMERA_RESOLUTION}}` (e.g. `1920x1080`)
- `{{PHOTO_OUTPUT_DIR}}` (where photos land on the Jetson before sync)
- `{{PHOTO_SYNC_DIR}}` (where photos land on the OpenClaw host — typically `<workspace>/photos/`)

Care thresholds (start with the defaults baked into `AGENTS.md` for basil; override here only if your cultivar or setup is different):

- `{{SOIL_MOISTURE_MIN}}` (below this → water)
- `{{SOIL_MOISTURE_MAX}}` (above this → skip watering, possibly alert)
- `{{TEMP_MIN_F}}`
- `{{TEMP_MAX_F}}`
- `{{HUMIDITY_MIN}}`
- `{{HUMIDITY_MAX}}`
- `{{GROW_LIGHT_ON_HOUR}}` (local hour, 24h)
- `{{GROW_LIGHT_OFF_HOUR}}` (local hour, 24h)
- `{{WATER_PUMP_DURATION_SECONDS}}` (one watering shot)
- `{{WATER_COOLDOWN_MINUTES}}` (minimum minutes between waterings)

## 2. Verify the workspace path

The plant agent's workspace is at `/var/lib/openclaw/.openclaw/workspace-plant/` under the systemd install.

```bash
getent passwd openclaw
ls /var/lib/openclaw/.openclaw/workspace-plant/
```

If the path differs, update `{{WORKSPACE_PATH}}` everywhere.

## 3. Deploy the pack

The repo is pulled, copied, chowned, and the service is restarted. On the OpenClaw host:

```bash
cd ~/clawfarmer && git pull
sudo cp -r ~/clawfarmer/AGENTS.md \
            ~/clawfarmer/TOOLS.md \
            ~/clawfarmer/HEARTBEAT.md \
            ~/clawfarmer/skills \
            ~/clawfarmer/memory \
            /var/lib/openclaw/.openclaw/workspace-plant/
sudo chown -R openclaw:openclaw /var/lib/openclaw/.openclaw/workspace-plant/
sudo systemctl restart openclaw
```

## 4. Replace placeholders

Replace every `{{PLACEHOLDER}}` token in the deployed copy. Minimum search list:

```
{{OWNER_NAME}} {{AGENT_NAME}} {{TIMEZONE}} {{PRIMARY_UPDATE_CHANNEL}} {{PRIMARY_UPDATE_TARGET}}
{{WORKSPACE_PATH}} {{CROP_NAME}}
{{SENSOR_TRANSPORT}} {{PI_HOST}} {{PI_USER}} {{PI_SSH_KEY_PATH}}
{{JETSON_HOST}} {{JETSON_USER}} {{JETSON_SSH_KEY_PATH}}
{{SOIL_MOISTURE_ADC_CHANNEL}} {{TEMP_HUMIDITY_GPIO_PIN}} {{LIGHT_SENSOR_I2C_ADDR}}
{{WATER_PUMP_GPIO_PIN}} {{GROW_LIGHT_GPIO_PIN}} {{FAN_GPIO_PIN}}
{{CAMERA_DEVICE}} {{CAMERA_RESOLUTION}} {{PHOTO_OUTPUT_DIR}} {{PHOTO_SYNC_DIR}}
{{SOIL_MOISTURE_MIN}} {{SOIL_MOISTURE_MAX}}
{{TEMP_MIN_F}} {{TEMP_MAX_F}} {{HUMIDITY_MIN}} {{HUMIDITY_MAX}}
{{GROW_LIGHT_ON_HOUR}} {{GROW_LIGHT_OFF_HOUR}}
{{WATER_PUMP_DURATION_SECONDS}} {{WATER_COOLDOWN_MINUTES}}
```

The daemon overwrites its config on shutdown. Don't edit config while the service is running — either use `openclaw config set` CLI or edit with `sed` and restart.

## 5. Create the cron jobs

Use `cron/jobs.template.json` as the starting pattern. Create jobs via the OpenClaw CLI rather than committing runtime state:

```bash
openclaw cron create --agent plant --from ~/clawfarmer/cron/jobs.template.json
```

(or whatever the equivalent command is in your OpenClaw version — refer to the OpenClaw docs if the CLI has moved).

Recommended starting jobs (all enabled by default in the template):

1. Sensor sweep every 15 minutes
2. Daily photo at morning and evening
3. Daily growth log at end of day
4. Grow-light schedule check hourly

## 6. Safety gates

Before enabling any actuator writes, set `read_only: true` in `TOOLS.md` and watch a full day of readings route through `{{PRIMARY_UPDATE_CHANNEL}}`. Flip to `read_only: false` only once you trust the thresholds and the transport is reliable.

In read-only mode:
- reads run normally
- every actuator command (water pump, grow light, fan) becomes a drafted proposal sent to `{{PRIMARY_UPDATE_CHANNEL}} → {{PRIMARY_UPDATE_TARGET}}` with the reading that triggered it and the exact command it would run

## 7. First-run validation

- [ ] `plant-monitor` skill runs and returns at least one full set of sensor readings
- [ ] a photo lands in `{{PHOTO_SYNC_DIR}}` after the first daily-photo cron tick
- [ ] `memory/sensor-state.json` updates with the latest readings
- [ ] `memory/growth-log.md` gets a new entry after the first end-of-day cron
- [ ] an intentionally dry soil reading produces a drafted watering proposal in read-only mode
- [ ] flipping to write mode, a real actuator cycle runs and logs correctly
