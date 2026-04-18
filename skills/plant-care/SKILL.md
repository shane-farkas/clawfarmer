---
name: plant-care
description: "Make and (in write mode) execute care decisions for {{OWNER_NAME}}'s {{CROP_NAME}} rig: when to water, when to turn the grow light on or off, when to run the fan. Use on care-decision and grow-light-check heartbeats; on any operator request to water / change the light schedule; and whenever the monitor skill surfaces a reading that needs an action. Prefer plant-monitor when the tick is observation-only."
---

# Plant Care

Use this skill to decide and (when `read_only: false`) execute actuator commands. All reads should come from `memory/sensor-state.json` — run `plant-monitor` first if the state file is stale.

## Read these first at the start of every run

- `AGENTS.md`
- `TOOLS.md`
- `memory/sensor-state.json`

## Operating mode gate (check before every action)

Read the `Operating mode` section of `TOOLS.md` at the start of every run and obey the `read_only` knob before taking any actuator action.

When `read_only: true`:
- decide what the action would be as usual
- **do not** run the water pump, grow-light relay, or fan relay
- draft the proposal to `{{PRIMARY_UPDATE_CHANNEL}} -> {{PRIMARY_UPDATE_TARGET}}` containing:
  - the action name (`water`, `grow_light on`, `grow_light off`, `fan on`, `fan off`)
  - the reading + threshold that triggered it
  - the exact command the agent would have run (copy-pasteable for the operator)
  - the cooldown that would have applied next
- you may still update `memory/sensor-state.json` to reflect that a proposal was surfaced (`active_detections[]`), but do **not** append to `watering_history[]` unless the action actually fired

When `read_only: false`, every action below fires for real, still bounded by the cooldowns and windows in `TOOLS.md`.

If `TOOLS.md` does not contain a `read_only` line, default to `read_only: true` and surface a note asking the operator to set the flag.

## Actuator transports

Same dispatch as `plant-monitor`: the `sensor_transport` knob in `TOOLS.md` selects the channel.

### SSH transport

```bash
# water pump: on for N seconds, then off
sudo -u openclaw ssh -i {{PI_SSH_KEY_PATH}} {{PI_USER}}@{{PI_HOST}} \
  "python3 -c 'from clawfarmer_pi import pulse_relay; \
   pulse_relay({{WATER_PUMP_GPIO_PIN}}, {{WATER_PUMP_DURATION_SECONDS}})'"

# grow light on / off
sudo -u openclaw ssh -i {{PI_SSH_KEY_PATH}} {{PI_USER}}@{{PI_HOST}} \
  "python3 -c 'from clawfarmer_pi import set_relay; set_relay({{GROW_LIGHT_GPIO_PIN}}, True)'"
sudo -u openclaw ssh -i {{PI_SSH_KEY_PATH}} {{PI_USER}}@{{PI_HOST}} \
  "python3 -c 'from clawfarmer_pi import set_relay; set_relay({{GROW_LIGHT_GPIO_PIN}}, False)'"

# fan on / off
sudo -u openclaw ssh -i {{PI_SSH_KEY_PATH}} {{PI_USER}}@{{PI_HOST}} \
  "python3 -c 'from clawfarmer_pi import set_relay; set_relay({{FAN_GPIO_PIN}}, True)'"
sudo -u openclaw ssh -i {{PI_SSH_KEY_PATH}} {{PI_USER}}@{{PI_HOST}} \
  "python3 -c 'from clawfarmer_pi import set_relay; set_relay({{FAN_GPIO_PIN}}, False)'"
```

### MQTT transport

```bash
mosquitto_pub -h {{MQTT_BROKER_HOST}} -p {{MQTT_BROKER_PORT}} \
  -t "{{MQTT_TOPIC_PREFIX}}pump" -m '{"duration_s": {{WATER_PUMP_DURATION_SECONDS}}}'
mosquitto_pub -h {{MQTT_BROKER_HOST}} -p {{MQTT_BROKER_PORT}} \
  -t "{{MQTT_TOPIC_PREFIX}}grow_light" -m "on"   # or "off"
mosquitto_pub -h {{MQTT_BROKER_HOST}} -p {{MQTT_BROKER_PORT}} \
  -t "{{MQTT_TOPIC_PREFIX}}fan" -m "on"          # or "off"
```

### HTTP transport

```bash
curl -s -X POST {{PI_HTTP_BASE_URL}}/actuators/pump \
  -d '{"duration_s": {{WATER_PUMP_DURATION_SECONDS}}}'
curl -s -X POST {{PI_HTTP_BASE_URL}}/actuators/grow_light -d '{"state": "on"}'
curl -s -X POST {{PI_HTTP_BASE_URL}}/actuators/fan -d '{"state": "on"}'
```

### Mock transport

Log the decision to `memory/sensor-state.json` and pretend the action succeeded. Update `watering_history[]` for mock waterings so anomaly detection has something to exercise.

## Care-decision workflow

1. Read `memory/sensor-state.json`. If `updated_at` is older than 30 minutes, trigger a `plant-monitor` sweep first — do not act on stale readings.
2. Evaluate watering (below).
3. Evaluate grow-light schedule (below).
4. Evaluate fan (below — optional).
5. For each decision, either fire the actuator (write mode) or draft the proposal (read-only mode).
6. Record the action in `memory/sensor-state.json`:
   - waterings append to `watering_history[]` with `pre_moisture`, `duration_s`, and the post-watering `soil_moisture` reading captured 60 seconds after the pump stops
   - grow-light state changes update `grow_light.state` and `grow_light.last_toggled_at`

## Watering decision

Triggers (evaluated in order — stop at the first that fires):

1. **Cooldown guard**: if the last entry in `watering_history[]` is within `{{WATER_COOLDOWN_MINUTES}}` minutes, skip. Do not alert.
2. **Daily cap guard**: if 4 waterings have already happened today (local `{{TIMEZONE}}`), skip and flag if soil is still low — escalating moisture loss at this rate usually means a drainage or sensor issue.
3. **Window guard**: if the current local hour is outside `06:00`–`20:00`, skip unless soil is at or below `{{SOIL_MOISTURE_MIN}} - 10` (i.e. an emergency read).
4. **Over-moisture guard**: if soil is at or above `{{SOIL_MOISTURE_MAX}}`, skip and flag — drainage problem or sensor fault.
5. **Main trigger**: if soil is at or below `{{SOIL_MOISTURE_MIN}}`, water for `{{WATER_PUMP_DURATION_SECONDS}}` seconds.

After watering:
- wait 60 seconds
- run a targeted soil-moisture read via `plant-monitor`
- append the event to `watering_history[]` with `pre_moisture` and `post_moisture`
- if `post_moisture - pre_moisture < 5`, flag "watering had no effect" — likely empty reservoir or pump failure

## Grow-light decision

Runs on `grow-light-check` ticks (hourly is plenty).

- if `current_hour_local >= {{GROW_LIGHT_ON_HOUR}}` and `current_hour_local < {{GROW_LIGHT_OFF_HOUR}}`, target state = `on`
- otherwise, target state = `off`
- if `grow_light.state` already matches target, do nothing (no message)
- if they differ, issue the relay command, then update `grow_light.state` + `grow_light.last_toggled_at`
- respect the 60-second toggle cooldown — if `last_toggled_at` was less than 60s ago, skip this tick and let the next one apply the change

Sanity check after toggling:
- light commanded on but latest `lux` reading (within the next sweep) is <1000 → flag
- light commanded off but latest `lux` reading is >5000 → flag

## Fan decision (optional)

Only runs if `{{FAN_GPIO_PIN}}` is populated.

- if `humidity_pct > {{HUMIDITY_MAX}}` or `temp_f > {{TEMP_MAX_F}}` → fan on
- if humidity has returned to band **and** temp is in band for two consecutive sweeps → fan off
- skip if `grow_light.last_toggled_at` was within the last 60 seconds (don't compound relay flapping)

## Operator-initiated actions

If the operator sends `water now`, `light on`, `light off`, `fan on`, or `fan off`:

- honor the command immediately in write mode
- still enforce the cooldown guards (watering) and the toggle cooldown (relays) — explain to the operator if a guard blocks the action
- log the operator-initiated action in `memory/sensor-state.json` with `source: operator`

## Safety

- never run the pump for more than `{{WATER_PUMP_DURATION_SECONDS}}` in one call
- never issue more than one actuator command per tick — compound behavior comes from multiple ticks, not chained commands inside one run
- if an actuator command fails (non-zero exit, timeout, transport error), do **not** retry silently; log to `last_errors[]` and surface to the operator
- if the state file is unreadable or malformed, stop and flag — do not act on assumptions

## Output style

When updating the operator:
- lead with the action taken (or proposed, in read-only mode)
- cite the reading that triggered it with units
- one line per action, maximum three lines total
- do not include raw command output unless the action failed
