---
name: plant-monitor
description: "Read sensors, capture photos, detect anomalies, and keep `memory/sensor-state.json` + `memory/growth-log.md` current for {{OWNER_NAME}}'s {{CROP_NAME}} rig. Use on sensor-sweep, photo-capture, and daily-log heartbeats; on any operator check-in asking 'how is the plant?'; and whenever a care decision needs a fresh reading before acting. Prefer this skill over plant-care when the tick is observation-only."
---

# Plant Monitor

Use this skill to read sensors, capture photos, detect anomalies, and maintain the state files. It does **not** run actuators — route those through `plant-care`.

## Read these first at the start of every run

- `AGENTS.md`
- `TOOLS.md`
- `memory/sensor-state.json`

## Operating mode gate

This skill is **not gated** by `read_only` — sensor reads and memory-file writes are safe in both modes. It must still check the flag so any anomaly escalation it *triggers* respects it downstream in `plant-care`.

## Transports

Pick the one set by `sensor_transport:` in `TOOLS.md`.

### SSH transport

Each reading is a small Python one-liner shelled over SSH.

```bash
# soil moisture (0–100 after calibration)
sudo -u openclaw ssh -i {{PI_SSH_KEY_PATH}} {{PI_USER}}@{{PI_HOST}} \
  "python3 -c 'from clawfarmer_pi import read_soil; print(read_soil({{SOIL_MOISTURE_ADC_CHANNEL}}))'"

# temperature + humidity (DHT22)
sudo -u openclaw ssh -i {{PI_SSH_KEY_PATH}} {{PI_USER}}@{{PI_HOST}} \
  "python3 -c 'from clawfarmer_pi import read_dht; print(read_dht({{TEMP_HUMIDITY_GPIO_PIN}}))'"

# ambient light (lux)
sudo -u openclaw ssh -i {{PI_SSH_KEY_PATH}} {{PI_USER}}@{{PI_HOST}} \
  "python3 -c 'from clawfarmer_pi import read_lux; print(read_lux({{LIGHT_SENSOR_I2C_ADDR}}))'"

# photo capture on the Jetson
sudo -u openclaw ssh -i {{JETSON_SSH_KEY_PATH}} {{JETSON_USER}}@{{JETSON_HOST}} \
  "python3 -m clawfarmer_jetson.capture --device {{CAMERA_DEVICE}} \
   --resolution {{CAMERA_RESOLUTION}} --out {{PHOTO_OUTPUT_DIR}}"

# pull the photo back to the host
sudo -u openclaw scp -i {{JETSON_SSH_KEY_PATH}} \
  {{JETSON_USER}}@{{JETSON_HOST}}:{{PHOTO_OUTPUT_DIR}}/*.jpg \
  {{PHOTO_SYNC_DIR}}/
```

The `clawfarmer_pi` and `clawfarmer_jetson` Python helpers are out of scope for this pack — install them on the devices as part of the hardware setup. Each helper should print JSON on stdout (for readings) or exit 0 with the file written (for the camera).

### MQTT transport

Pi/Jetson publish; agent subscribes. The agent polls the last retained message on each topic.

```bash
mosquitto_sub -h {{MQTT_BROKER_HOST}} -p {{MQTT_BROKER_PORT}} \
  -t "{{MQTT_TOPIC_PREFIX}}soil" -C 1
mosquitto_sub -h {{MQTT_BROKER_HOST}} -p {{MQTT_BROKER_PORT}} \
  -t "{{MQTT_TOPIC_PREFIX}}dht" -C 1
mosquitto_sub -h {{MQTT_BROKER_HOST}} -p {{MQTT_BROKER_PORT}} \
  -t "{{MQTT_TOPIC_PREFIX}}lux" -C 1

# photo capture request (Jetson listens on this topic and captures)
mosquitto_pub -h {{MQTT_BROKER_HOST}} -p {{MQTT_BROKER_PORT}} \
  -t "{{MQTT_TOPIC_PREFIX}}camera/capture" -m "now"
```

### HTTP transport

```bash
curl -s {{PI_HTTP_BASE_URL}}/sensors/soil
curl -s {{PI_HTTP_BASE_URL}}/sensors/dht
curl -s {{PI_HTTP_BASE_URL}}/sensors/lux
curl -s -X POST {{JETSON_HTTP_BASE_URL}}/camera/capture
```

### Mock transport

Emit synthetic readings — soil moisture walks a slow random walk down to the water-now threshold, recovers after any watering logged in `memory/sensor-state.json`. Temperature and humidity stay in-band with small wiggle. Light is 1 lux in the grow-light off window and ~20000 lux in the on window. Photo capture writes a placeholder `.jpg` to `{{PHOTO_SYNC_DIR}}`.

Use this transport for first install so the whole pipeline can be exercised before hardware arrives.

## Sensor-sweep workflow

1. Read all three sensors. Run them in parallel where the transport allows.
2. If a read fails, record it under `last_errors[]` in `memory/sensor-state.json` with the sensor name + error string + timestamp. Do not fail the whole sweep — use the prior value (flagged `stale: true`) for anomaly detection.
3. Write the fresh readings into `memory/sensor-state.json` (see schema below).
4. Run anomaly detection (see below).
5. If any anomaly is new or worsening, surface it to `{{PRIMARY_UPDATE_CHANNEL}} -> {{PRIMARY_UPDATE_TARGET}}` with the reading, the threshold, and the recommended action.
6. If nothing is anomalous and nothing changed materially, stay silent — return `HEARTBEAT_OK`.

## Photo-capture workflow

1. Trigger the camera via the active transport.
2. Wait up to 30 seconds for the file to land in `{{PHOTO_SYNC_DIR}}`.
3. If no file arrives, log it under `last_errors[]` and move on.
4. Note the photo filename + capture timestamp under `last_photo` in `memory/sensor-state.json`.
5. Do not analyze the photo here — the `daily-log` tick handles photo-based observation.

## Daily-log workflow

Runs once per day (see `cron/jobs.template.json`).

1. Gather today's readings from `memory/sensor-state.json` — the latest values plus the day's min/max for temp, humidity, soil moisture.
2. Identify the most recent photo in `{{PHOTO_SYNC_DIR}}` with a capture timestamp from today.
3. Analyze the photo for the observation cues listed in `AGENTS.md` — leaf color, posture, flowering, pests, soil surface. Note changes from the prior day's entry when it exists.
4. Summarize watering events from today (count, timestamps) — read `watering_history[]` in `memory/sensor-state.json`.
5. Append one dated block to `memory/growth-log.md` using the template below. Do not rewrite prior days.
6. If the photo analysis surfaces a new failure-mode detection (per `AGENTS.md`), also send a short operator update.

### Growth-log entry template

```markdown
## {{YYYY-MM-DD}}

- soil moisture: latest X, day range Y–Z
- temperature: day range Y–Z °F
- humidity: day range Y–Z %
- light hours: N (on-window delivered)
- waterings: N (HH:MM, HH:MM, …)
- photo: `<filename>` — one-line description of what the photo shows
- observations: new detections or visible changes from yesterday, one line each
- flag: single short phrase only if something needs operator attention
```

## Anomaly detection

Re-check after every sweep. These are the baseline rules — `AGENTS.md` is the source of truth for any crop-specific detail.

### Reading-level rules

- `soil_moisture > {{SOIL_MOISTURE_MAX}}` or `< {{SOIL_MOISTURE_MIN}}` — flag and surface
- `temp_f < {{TEMP_MIN_F}}` or `> {{TEMP_MAX_F}}` — flag and surface
- `humidity_pct < {{HUMIDITY_MIN}}` or `> {{HUMIDITY_MAX}}` — flag and surface
- grow-light commanded on but lux < 1000 — likely bulb/relay failure, flag
- grow-light commanded off but lux > 5000 — unexpected light source (or the command failed), flag

### Trend rules (need at least 6 sweeps of history)

- `soil_moisture` drops >30 points in under 2 hours — probable leak or sensor fault
- `soil_moisture` pinned at the same raw value for >4 hours — probable sensor stuck, request visual check
- `temp_f` swings >15 °F in 30 minutes — environmental disturbance, not a care issue; log but do not alert unless repeated
- 3 consecutive read failures on the same sensor — surface the transport issue, not the reading

Escalation rule: only message the operator when a detection is *new* or *materially worse* than the last sweep's detection. Do not resurface the same in-band-edge reading every 15 minutes.

## `memory/sensor-state.json` schema

```json
{
  "version": 1,
  "updated_at": "2026-04-18T14:15:00-07:00",
  "readings": {
    "soil_moisture": {"value": 48, "unit": "pct_vwc", "at": "2026-04-18T14:15:00-07:00", "stale": false},
    "temp_f": {"value": 74.2, "unit": "fahrenheit", "at": "2026-04-18T14:15:00-07:00", "stale": false},
    "humidity_pct": {"value": 52, "unit": "pct_rh", "at": "2026-04-18T14:15:00-07:00", "stale": false},
    "lux": {"value": 18400, "unit": "lux", "at": "2026-04-18T14:15:00-07:00", "stale": false}
  },
  "day_ranges": {
    "soil_moisture": {"min": 38, "max": 62, "window_start": "2026-04-18T00:00:00-07:00"},
    "temp_f": {"min": 68.1, "max": 81.4, "window_start": "2026-04-18T00:00:00-07:00"},
    "humidity_pct": {"min": 44, "max": 58, "window_start": "2026-04-18T00:00:00-07:00"}
  },
  "grow_light": {"state": "on", "last_toggled_at": "2026-04-18T06:00:00-07:00"},
  "watering_history": [
    {"at": "2026-04-18T07:12:00-07:00", "duration_s": 12, "pre_moisture": 34, "post_moisture": 58}
  ],
  "last_photo": {"filename": "2026-04-18T14-14-52.jpg", "at": "2026-04-18T14:14:52-07:00"},
  "active_detections": [
    {"kind": "low_humidity", "since": "2026-04-17T22:00:00-07:00", "last_value": 28}
  ],
  "last_errors": []
}
```

Rules for maintaining the file:
- never lose `watering_history` — append-only, trim to last 60 days at the very end of daily-log runs
- `day_ranges` reset at local midnight in `{{TIMEZONE}}`
- `active_detections` only contains currently-true detections; clear them when the reading returns in-band
- write atomically (temp file + rename) so concurrent sweeps cannot corrupt it

## Output style

When updating the operator:
- lead with the detection or change
- cite the reading with units and the threshold it crossed
- if a care action is being recommended, name the skill that will run it (`plant-care`) and the command it would run
- one message per tick, maximum
