# clawfarmer-pi

Raspberry Pi sensor + actuator helper. Lives on the Pi and is shelled into from the OpenClaw host by the `plant-monitor` and `plant-care` skills.

## Hardware it speaks to

- **ADS1115** (I²C, default `0x48`) + capacitive soil-moisture probe on channel 0–3 — verified against Teyleten Robot ADS1115 + Songhe capacitive probe
- **BME280** (I²C, default `0x76`, alt `0x77`) — temperature / humidity / pressure — verified against Starry GY-BME280 ("5V" variant with onboard regulator + level shifter, so either 3.3V or 5V VCC is fine)
- **BH1750** (I²C, default `0x23`, alt `0x5C`) — ambient light — verified against JESSINIE GY-302 BH1750FVI
- **Logic-level N-channel MOSFET** → 12V peristaltic dosing pump (e.g. Diitao), fed from a dedicated 12V 2A wall adapter
- **Relay board(s)** on other GPIOs → grow light, optional fan — **not in the first hardware batch**; skills gate on populated pins so these can be wired later without code changes

All I²C devices share one bus. The pump MOSFET takes its own GPIO (BCM numbering). Any future relays take their own GPIOs.

## Wiring reference

Pi 40-pin header (BCM numbering):

| Device                       | Pi pin (physical) | Signal        | Notes                                                   |
|------------------------------|-------------------|---------------|---------------------------------------------------------|
| 3.3V rail                    | 1                 | 3V3           | ADS1115, BH1750, and the capacitive soil probe VCC      |
| 5V rail                      | 2                 | 5V            | BME280 VCC is fine here (onboard regulator)             |
| GND                          | 6                 | GND           | shared ground — also tie the 12V pump supply GND here   |
| I²C SDA                      | 3                 | GPIO 2 / SDA1 | all three I²C sensors                                   |
| I²C SCL                      | 5                 | GPIO 3 / SCL1 | all three I²C sensors                                   |
| Pump MOSFET gate             | pick one          | GPIO (BCM)    | e.g. GPIO 17 (pin 11) — Pi drives gate, not pump coil   |
| Grow-light relay (later)     | pick one          | GPIO (BCM)    | not in first batch — e.g. GPIO 27 (pin 13) when wired   |
| Fan relay (optional, later)  | pick one          | GPIO (BCM)    | not in first batch — e.g. GPIO 22 (pin 15) when wired   |

**Pump power**: the Diitao peristaltic pump runs off the 12V 2A adapter, not the Pi. The MOSFET switches the pump's ground leg; its drain goes to the pump's negative lead, source to GND (shared with Pi GND), gate to the Pi GPIO through a small series resistor (100–220Ω) with a 10kΩ pull-down from gate to GND so the pump stays off during Pi boot. Put a flyback diode across the pump terminals (cathode to +12V, anode to the MOSFET drain) to absorb the back-EMF when the motor switches off — peristaltic pumps have a DC motor inside so the flyback is not optional.

**Capacitive soil probe**: the probe's VCC pin goes to the Pi's 3.3V rail; AOUT to any ADS1115 A0–A3 channel. Do not feed it 5V — the raw output can exceed the ADS1115's `VDD + 0.3V` max.

## Pi-side one-time setup

Assuming Raspberry Pi OS (Bookworm or later), run on the Pi:

```bash
# enable I2C
sudo raspi-config nonint do_i2c 0

# packages
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv i2c-tools

# verify the sensors appear (you should see 0x23, 0x48, and 0x76 after wiring)
i2cdetect -y 1

# create a venv for the helper
python3 -m venv ~/clawfarmer-venv
source ~/clawfarmer-venv/bin/activate

# install this package from the repo (clone or rsync the pi/ directory first)
pip install -e ~/clawfarmer/pi
```

The OpenClaw host shells in as the Pi user and runs `~/clawfarmer-venv/bin/python3 -m clawfarmer_pi …`. You can either put that venv bin on the Pi user's `PATH` (e.g. in `~/.bashrc` or a systemd `EnvironmentFile`) or have the SKILL commands use the absolute path — pick one and document it in `TOOLS.md`.

## Commands

Every command prints one JSON line to stdout. Exit code is 0 on success, 1 on failure (with `{"ok": false, "error": "..."}`).

```bash
# soil — pass the calibration values you captured
python3 -m clawfarmer_pi read-soil --channel 0 --dry-raw 26000 --wet-raw 12000
# {"sensor":"ads1115_soil","channel":0,"raw":17234,"voltage":2.14,"pct_vwc":62.6,"at":"..."}

# BME280
python3 -m clawfarmer_pi read-bme280
# {"sensor":"bme280","temp_c":22.3,"temp_f":72.1,"humidity_pct":48.2,"pressure_hpa":1013.5,"at":"..."}

# BH1750
python3 -m clawfarmer_pi read-lux
# {"sensor":"bh1750","lux":18400.0,"at":"..."}

# pump: pulse GPIO 17 high for 10 seconds (hard cap at 60s inside the module —
# peristaltic pumps are slow, so tens of seconds is a normal single watering)
python3 -m clawfarmer_pi pulse-pump --pin 17 --duration 10

# grow-light relay: most cheap relay boards are active-low, so pass --active-low
python3 -m clawfarmer_pi set-relay --pin 27 --state on --active-low
python3 -m clawfarmer_pi set-relay --pin 27 --state off --active-low
```

## Calibrating the soil probe

Before filling in `{{SOIL_MOISTURE_DRY_RAW}}` and `{{SOIL_MOISTURE_WET_RAW}}` in `TOOLS.md`, capture both endpoints with the probe wired up:

1. Hold the probe in open air (fully dry) and run `read-soil` with any placeholder calibration. Read back the `"raw"` field — that's your `dry_raw`.
2. Submerge the probe's detection region in a glass of water (not past the electronics!) and run the same command. That's your `wet_raw`.
3. Put both values into `TOOLS.md`. The normalized `pct_vwc` in subsequent reads will then be meaningful 0–100.

Expected ballpark on a typical capacitive probe through an ADS1115 at 3.3V: dry ≈ 25000–27000, wet ≈ 11000–13000. If your numbers are wildly different, check the probe supply voltage and the ADS1115 gain (defaults to ±4.096V FSR in this package).

## Smoke test after wiring

With the venv active and the sensors on the bus:

```bash
i2cdetect -y 1                                        # confirm 0x23, 0x48, 0x76
python3 -m clawfarmer_pi read-bme280                   # should print a plausible temp_f
python3 -m clawfarmer_pi read-lux                      # cover the sensor → ~0, light it → high
python3 -m clawfarmer_pi read-soil --channel 0 \
  --dry-raw 26000 --wet-raw 12000                      # move probe between air and water

# dry-run the pump for 5 seconds with intake + outlet both in a container of
# water (peristaltic pumps dislike running dry for long, and 1s is too short to
# see any flow at ~40 mL/min)
python3 -m clawfarmer_pi pulse-pump --pin 17 --duration 5

# measure flow rate: pump into a graduated container for 60s, weigh/read the
# volume, and record mL/min in TOOLS.md so the care skill can convert a
# "water the plant ~30 mL" decision into the right --duration.
python3 -m clawfarmer_pi pulse-pump --pin 17 --duration 60
```

If every command prints a valid JSON line, the Pi side is ready for the OpenClaw host to shell in.

## What's intentionally out of scope

- camera capture (runs on the Jetson, not the Pi)
- MQTT / HTTP transports (only SSH is covered here — the SKILL files show the other transport shapes for later)
- any long-running daemon — every invocation is one-shot so there is nothing to keep alive if the Pi reboots
