# SETUP.md — fresh Raspberry Pi for clawfarmer

From unboxed Pi to the Claw host shelling in and reading sensors. ~30–45 minutes, most of it waiting on first boot and `apt-get update`.

This assumes:
- a Raspberry Pi 4 or 5 (3B+ works too, same steps)
- Raspberry Pi OS **Trixie (Debian 13)** or **Bookworm (Debian 12)** — both work with no code changes
- the Ubuntu "Claw" host is already on your LAN with OpenClaw running as the `openclaw` service user
- you know the Claw host's hostname / IP and can SSH into it

## 1. Flash the SD card

On your laptop:

1. Install Raspberry Pi Imager: https://www.raspberrypi.com/software/
2. Launch it, pick:
   - **Device**: your Pi model
   - **OS**: `Raspberry Pi OS (other)` → `Raspberry Pi OS Lite (64-bit)` (the current default — Trixie as of late 2025, Bookworm on older Imager builds; either is fine)
   - **Storage**: your microSD card
3. Click **Next** → **Edit Settings** and set:
   - **Hostname**: `clawpi` (this is what Claw will SSH to — `clawpi.local` over mDNS)
   - **Username**: `pi` (or `plant` — whatever you want; this guide uses `pi`)
   - **Password**: one you'll remember; only used as a fallback once SSH keys are in
   - **Wi-Fi**: SSID + password + country (or skip if you're using ethernet)
   - **Locale**: your timezone and keyboard layout
   - **Services** tab → enable **SSH** → "Allow public-key authentication only" → paste the pubkey of whichever user on your laptop will SSH in first (`cat ~/.ssh/id_ed25519.pub`)
4. Save, write, eject when done.

## 2. First boot

1. Pop the card into the Pi, plug in ethernet (if not Wi-Fi), then power.
2. Wait ~2 minutes. First boot expands the filesystem and reboots once.
3. From your laptop:
   ```bash
   ssh pi@clawpi.local
   ```
   If `.local` doesn't resolve, check your router's DHCP lease table for the Pi's IP and SSH to that instead.

## 3. Update and install base packages

```bash
sudo apt-get update && sudo apt-get -y upgrade
sudo apt-get install -y python3-pip python3-venv python3-dev i2c-tools git swig liblgpio-dev
sudo reboot
```

`swig`, `python3-dev`, and `liblgpio-dev` are needed because the Python `lgpio` wrapper (the GPIO backend `gpiozero` uses on Pi 5) doesn't ship prebuilt wheels for Python 3.13 on aarch64 yet, so pip has to build it from source: swig + python3-dev give it the compile toolchain, and liblgpio-dev provides the native C library the wrapper links against.

SSH back in after the reboot completes.

## 4. Enable I²C

```bash
sudo raspi-config nonint do_i2c 0
```

Verify the kernel module is loaded:

```bash
ls /dev/i2c-*
# should show /dev/i2c-1
```

With nothing wired yet, `i2cdetect -y 1` prints an empty grid — that's fine. Once the ADS1115, BME280, and BH1750 are wired you should see `0x23`, `0x48`, and `0x76`.

## 5. Drop the clawfarmer_pi package onto the Pi

Two options — pick whichever fits how you version-control this:

**Option A — rsync from Claw (simplest if the repo already lives there):**

```bash
# on Claw, as your normal user
rsync -av --delete ~/clawfarmer/pi/ pi@clawpi.local:~/clawfarmer-pi/
```

**Option B — clone from GitHub once the repo is pushed public:**

```bash
# on the Pi
git clone https://github.com/<your-gh-user>/clawfarmer.git ~/clawfarmer
ln -s ~/clawfarmer/pi ~/clawfarmer-pi
```

Either way, install into a venv on the Pi:

```bash
python3 -m venv ~/clawfarmer-venv
source ~/clawfarmer-venv/bin/activate
pip install -e ~/clawfarmer-pi
```

Sanity check — prints the CLI subcommands:

```bash
~/clawfarmer-venv/bin/python3 -m clawfarmer_pi --help
```

Use the absolute path `~/clawfarmer-venv/bin/python3` everywhere: non-interactive SSH sessions don't reliably load `~/.bashrc` on Raspberry Pi OS, so a bare `python3` from Claw won't find the venv. The SKILL commands in this repo already assume absolute paths.

## 6. Create the `openclaw`-on-Claw → `pi`-on-Pi SSH path

The skills on Claw run as the `openclaw` service user and shell into the Pi. That user needs its own SSH key, and its pubkey needs to be on the Pi.

**On Claw**, generate the key (no passphrase — this is a daemon):

```bash
sudo -u openclaw -H mkdir -p /var/lib/openclaw/.ssh
sudo -u openclaw -H ssh-keygen -t ed25519 -N "" -f /var/lib/openclaw/.ssh/id_ed25519_plantpi
sudo cat /var/lib/openclaw/.ssh/id_ed25519_plantpi.pub
```

Copy the printed pubkey to your clipboard.

**On the Pi**, append it to `authorized_keys`:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo "<paste the pubkey you just copied>" >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

**Back on Claw**, verify it works:

```bash
sudo -u openclaw -H ssh \
  -i /var/lib/openclaw/.ssh/id_ed25519_plantpi \
  -o StrictHostKeyChecking=accept-new \
  pi@clawpi.local echo ok
```

Should print `ok` and exit. If it asks for a password, the key didn't land correctly — re-check `~/.ssh/authorized_keys` permissions on the Pi (must be `600`, directory `700`).

## 7. Point `TOOLS.md` at the Pi

In the deployed `TOOLS.md` inside `/var/lib/openclaw/.openclaw/workspace-plant/` on Claw, replace these placeholders:

```
{{SENSOR_TRANSPORT}}  = ssh
{{PI_HOST}}           = clawpi.local
{{PI_USER}}           = pi
{{PI_SSH_KEY_PATH}}   = /var/lib/openclaw/.ssh/id_ed25519_plantpi
```

The daemon overwrites config on shutdown — edit with `sed` while the service is running, or use `openclaw config set`. Don't hand-edit while it's running.

After filling these in, the SKILL commands should look like this one (and actually work):

```bash
sudo -u openclaw ssh -i /var/lib/openclaw/.ssh/id_ed25519_plantpi pi@clawpi.local \
  "~/clawfarmer-venv/bin/python3 -m clawfarmer_pi read-bme280"
```

If you want `python3` on the PATH without the absolute path, you can edit the SKILL files to use a full path, or put the venv in `/opt/clawfarmer-venv` and symlink `/usr/local/bin/clawfarmer-pi` → that venv's entry point. Either is fine — pick one and be consistent in `TOOLS.md`.

## 8. Smoke test end-to-end

With the sensors wired per the table in `pi/README.md`:

**On the Pi**:

```bash
i2cdetect -y 1
# should show 0x23, 0x48, 0x76

~/clawfarmer-venv/bin/python3 -m clawfarmer_pi read-bme280
~/clawfarmer-venv/bin/python3 -m clawfarmer_pi read-lux
~/clawfarmer-venv/bin/python3 -m clawfarmer_pi read-soil --channel 0 \
  --dry-raw 26000 --wet-raw 12000
```

Each command should print one JSON line. The soil reading will be meaningless until you calibrate — see the calibration section in `pi/README.md`.

**From Claw**, exercise the same commands over SSH:

```bash
sudo -u openclaw -H ssh -i /var/lib/openclaw/.ssh/id_ed25519_plantpi pi@clawpi.local \
  "~/clawfarmer-venv/bin/python3 -m clawfarmer_pi read-bme280"
```

If both sides print valid JSON, the Pi is ready and the skills can talk to it.

## 9. Keep the pump off until you've measured flow rate

Before flipping `read_only: false` in `TOOLS.md`:

1. Wire the MOSFET per the note in `pi/README.md` (gate resistor, pull-down, flyback diode). The pump should stay off during Pi boot.
2. Put the pump intake and outlet both in a container of water.
3. Run one 60-second pulse:
   ```bash
   ~/clawfarmer-venv/bin/python3 -m clawfarmer_pi pulse-pump --pin 17 --duration 60
   ```
4. Measure the delivered volume (mL). That's your flow rate in mL/min.
5. Record it in `TOOLS.md` so the care skill can convert "water ~30 mL" into the right `--duration`.

## Things to skip on first pass

- **No systemd service for `clawfarmer-pi`** — every invocation is one-shot, nothing to keep alive.
- **No static IP yet** — try mDNS (`clawpi.local`) first. Only reserve a DHCP IP on your router if mDNS proves flaky (it sometimes is on Wi-Fi with sleep states).
- **No firewall config** — the Pi is behind your LAN and only exposes SSH; leave it alone for now.
- **No read-write mode until the smoke test passes for at least a day in `read_only: true`** — you want to watch the proposed-action messages in Telegram before any actuator runs for real.

## Troubleshooting

- `ssh: Could not resolve hostname clawpi.local` → your laptop or Claw can't see mDNS on that network. Use the IP from the router's DHCP table, or install `avahi-daemon` on Claw (`sudo apt install -y avahi-daemon`).
- `i2cdetect -y 1` shows nothing after wiring → check SDA/SCL aren't swapped, and that VCC is actually hitting the sensor modules (multimeter on VCC→GND pins of the sensor).
- `OSError: [Errno 121] Remote I/O error` when reading BME280 → address mismatch. Try `--address 0x77` (some boards ship with SDO pulled high).
- `RuntimeError: No access to /dev/mem` when pulsing the pump → the Pi user is not in the `gpio` group. `sudo usermod -aG gpio pi && sudo reboot`.
- Pump twitches briefly during Pi boot → the pull-down resistor on the MOSFET gate is missing or wrong value. Add a 10kΩ from gate to GND.
- `Failed building wheel for lgpio` / `swig: No such file or directory` during `pip install` → missing build tools for a source build on Python 3.13. `sudo apt install -y swig python3-dev liblgpio-dev` and re-run the `pip install`.
- `Failed building wheel for lgpio` / `ld: cannot find -llgpio` → swig ran but the native `liblgpio` C library isn't installed. `sudo apt install -y liblgpio-dev` and re-run the `pip install`.
