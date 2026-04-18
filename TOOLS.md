# TOOLS.md — Local Hardware Notes

Environment-specific details for this rig. Every `{{PLACEHOLDER}}` below must be replaced before the pack runs against real hardware.

## Operating mode

- `read_only: true`

When `read_only: true`, every skill in this pack must behave as follows:

- **Reads are unrestricted** — sensor reads, photo captures, memory file reads and writes, growth log writes all continue normally.
- **Actuator writes are forbidden** — do **not** run the water pump, do **not** toggle the grow-light relay, do **not** toggle the fan relay, even if a threshold would have triggered the action.
- **Every would-be actuation becomes a drafted proposal** sent to `{{PRIMARY_UPDATE_CHANNEL}} -> {{PRIMARY_UPDATE_TARGET}}` containing:
  - what the action is (water / light on / light off / fan on)
  - the exact reading + threshold that triggered it
  - the exact command the agent would have run (so the operator can run it manually if desired)
  - the cooldown that would have applied next

When `read_only: false`, every actuator action fires for real, still bounded by the cooldowns and windows below.

If this file does not contain a `read_only` line at all, default to `read_only: true` and surface a note asking the operator to set the flag explicitly.

## Communication defaults

- Principal name: `{{OWNER_NAME}}`
- Agent name: `{{AGENT_NAME}}`
- Time zone: `{{TIMEZONE}}`
- Primary proactive update route: `{{PRIMARY_UPDATE_CHANNEL}} -> {{PRIMARY_UPDATE_TARGET}}`

## Transport

- `sensor_transport: {{SENSOR_TRANSPORT}}`

One of:
- `ssh` — OpenClaw host shells into the Pi/Jetson via SSH and runs small Python one-liners
- `mqtt` — Pi/Jetson publish readings to an MQTT broker; OpenClaw subscribes
- `http` — Pi/Jetson run a tiny Flask/FastAPI service; OpenClaw GETs readings
- `mock` — use synthetic data for dry-run installs before hardware arrives

Each transport has a matching command template in `skills/plant-monitor/SKILL.md`. Only one is active per install.

### SSH transport config

- Pi host: `{{PI_HOST}}`
- Pi user: `{{PI_USER}}`
- Pi SSH key: `{{PI_SSH_KEY_PATH}}`
- Jetson host: `{{JETSON_HOST}}`
- Jetson user: `{{JETSON_USER}}`
- Jetson SSH key: `{{JETSON_SSH_KEY_PATH}}`

The openclaw service user must have those keys readable (check `sudo -u openclaw ssh -i {{PI_SSH_KEY_PATH}} {{PI_USER}}@{{PI_HOST}} echo ok`).

### MQTT transport config (if used)

- Broker host: `{{MQTT_BROKER_HOST}}`
- Broker port: `{{MQTT_BROKER_PORT}}`
- Topic prefix: `{{MQTT_TOPIC_PREFIX}}` (e.g. `clawfarmer/plant/`)

### HTTP transport config (if used)

- Pi base URL: `{{PI_HTTP_BASE_URL}}` (e.g. `http://10.0.0.20:8080`)
- Jetson base URL: `{{JETSON_HTTP_BASE_URL}}`

## Raspberry Pi — sensors and actuators

Everything sensor-side is I²C on bus 1. The pump is switched by a logic-level MOSFET driven directly off a GPIO pin. Pin numbers are BCM.

### Sensors (I²C bus 1)

- Soil moisture — capacitive probe through an ADS1115 ADC:
  - ADS1115 address: `{{ADS1115_I2C_ADDR}}` (default `0x48`)
  - ADC channel: `{{SOIL_MOISTURE_ADC_CHANNEL}}` (0–3)
  - Calibration — dry reading (raw int16, probe in air): `{{SOIL_MOISTURE_DRY_RAW}}`
  - Calibration — wet reading (raw int16, probe in water): `{{SOIL_MOISTURE_WET_RAW}}`
  - Normalized output: 0 (bone dry) → 100 (saturated)
- Temperature + humidity + pressure (BME280):
  - Address: `{{BME280_I2C_ADDR}}` (default `0x76`, alt `0x77`)
  - Units: °F for temp, % RH for humidity, hPa for pressure
  - Pressure is logged but not currently used for care decisions
- Ambient light (BH1750):
  - Address: `{{LIGHT_SENSOR_I2C_ADDR}}` (default `0x23`, alt `0x5C`)
  - Units: lux

### Actuators

- Water pump (12V peristaltic dosing, via logic-level N-channel MOSFET):
  - Gate GPIO: `{{WATER_PUMP_GPIO_PIN}}`
  - Active-high (GPIO high → MOSFET on → pump runs)
  - Supply: 12V from the dedicated wall adapter (e.g. 12V 2A); Pi only drives the MOSFET gate
  - Pump supply ground must be tied to Pi ground
  - Flyback diode across the pump (cathode to +12V) is required — the peristaltic pump has a DC motor inside
  - Flow rate is slow (~30–60 mL/min typical), so a single watering pulse is tens of seconds, not 1–2s like a submersible
- Grow-light relay: GPIO `{{GROW_LIGHT_GPIO_PIN}}` (active-low unless your relay board is active-high — verify)
- Fan relay (optional): GPIO `{{FAN_GPIO_PIN}}`

### Pi helper package

All sensor reads and actuator pulses go through the `clawfarmer_pi` package on the Pi (source: `pi/clawfarmer_pi/` in the repo, installed on the Pi with `pip install -e .` from `pi/`). The skills shell into it as `python3 -m clawfarmer_pi <command> …` and expect JSON on stdout. See `pi/README.md` for the command list and the wiring table.

## Jetson Orin Nano — camera

- Camera device: `{{CAMERA_DEVICE}}`
- Capture resolution: `{{CAMERA_RESOLUTION}}`
- Photo output dir on the Jetson: `{{PHOTO_OUTPUT_DIR}}`
- Photo sync dir on the OpenClaw host: `{{PHOTO_SYNC_DIR}}`
- Photo filename format: `YYYY-MM-DDTHH-MM-SS.jpg` (UTC is fine; daily-log skill will convert to `{{TIMEZONE}}` when citing)

After each capture the Jetson script writes the file, then the OpenClaw side either pulls it (SSH: `scp`) or it arrives via the active transport. A photo is "available" once it lands in `{{PHOTO_SYNC_DIR}}`.

## Care thresholds (overrides)

Set these only if they should differ from the baseline in `AGENTS.md`. Leaving them as placeholders means the agent uses the `AGENTS.md` defaults.

- Soil moisture — water now if at or below: `{{SOIL_MOISTURE_MIN}}`
- Soil moisture — do not water if at or above: `{{SOIL_MOISTURE_MAX}}`
- Temperature — alert if below (°F): `{{TEMP_MIN_F}}`
- Temperature — alert if above (°F): `{{TEMP_MAX_F}}`
- Humidity — alert if below (%): `{{HUMIDITY_MIN}}`
- Humidity — alert if above (%): `{{HUMIDITY_MAX}}`
- Grow-light on hour (local `{{TIMEZONE}}`, 24h): `{{GROW_LIGHT_ON_HOUR}}`
- Grow-light off hour (local `{{TIMEZONE}}`, 24h): `{{GROW_LIGHT_OFF_HOUR}}`

## Actuator cooldowns and windows

- Water pump single shot duration: `{{WATER_PUMP_DURATION_SECONDS}}` seconds
- Minimum minutes between waterings: `{{WATER_COOLDOWN_MINUTES}}`
- Max waterings per 24h: 4
- Watering window (local `{{TIMEZONE}}`, 24h): 06:00 → 20:00 (no night watering; override here if needed)
- Grow-light toggle cooldown: 60 seconds (avoid relay flapping)

## Photo storage

- Local copy path: `{{PHOTO_SYNC_DIR}}`
- Retention: keep the last 60 days on-host; older photos may be archived externally
- The agent does not delete photos on its own

## Dry-run / first-install notes

On first install, prefer `sensor_transport: mock` and `read_only: true`. The skills emit synthetic readings and draft every actuator proposal to `{{PRIMARY_UPDATE_CHANNEL}}` so you can watch the behavior without wiring anything up.

Flip to the real transport, then flip `read_only: false`, only after:
- at least 24h of clean synthetic ticks
- the Telegram update channel is reliably receiving proposals
- each physical actuator has been manually pulsed once to prove the wiring
