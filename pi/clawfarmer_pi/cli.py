"""CLI surface. Every command prints one JSON object on stdout and exits 0
on success, or prints `{"ok": false, "error": "..."}` to stdout and exits 1
on failure. The skills on the OpenClaw host parse stdout as JSON."""

from __future__ import annotations

import argparse
import json
import sys


def _emit(payload: dict, ok: bool = True) -> None:
    print(json.dumps(payload, separators=(",", ":")))
    sys.exit(0 if ok else 1)


def _cmd_read_soil(args: argparse.Namespace) -> None:
    from .sensors import read_soil

    out = read_soil(
        channel=args.channel,
        dry_raw=args.dry_raw,
        wet_raw=args.wet_raw,
        address=int(args.address, 0),
    )
    _emit(out)


def _cmd_read_bme280(args: argparse.Namespace) -> None:
    from .sensors import read_bme280

    _emit(read_bme280(address=int(args.address, 0)))


def _cmd_read_lux(args: argparse.Namespace) -> None:
    from .sensors import read_lux

    _emit(read_lux(address=int(args.address, 0)))


def _cmd_pulse_pump(args: argparse.Namespace) -> None:
    from .actuators import pulse_pump

    _emit(pulse_pump(pin=args.pin, duration_s=args.duration))


def _cmd_set_relay(args: argparse.Namespace) -> None:
    from .actuators import set_relay

    state = args.state.lower() in ("on", "true", "1", "high")
    _emit(set_relay(pin=args.pin, state=state, active_high=not args.active_low))


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="clawfarmer-pi")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("read-soil", help="Read soil moisture from an ADS1115 channel")
    s.add_argument("--channel", type=int, required=True, choices=(0, 1, 2, 3))
    s.add_argument("--address", default="0x48")
    s.add_argument("--dry-raw", dest="dry_raw", type=int, default=26000,
                   help="Raw ADC value when probe is in open air (calibrate!)")
    s.add_argument("--wet-raw", dest="wet_raw", type=int, default=12000,
                   help="Raw ADC value when probe is in water (calibrate!)")
    s.set_defaults(func=_cmd_read_soil)

    s = sub.add_parser("read-bme280", help="Read BME280 temp + humidity + pressure")
    s.add_argument("--address", default="0x76")
    s.set_defaults(func=_cmd_read_bme280)

    s = sub.add_parser("read-lux", help="Read BH1750 ambient light")
    s.add_argument("--address", default="0x23")
    s.set_defaults(func=_cmd_read_lux)

    s = sub.add_parser("pulse-pump", help="Pulse the pump MOSFET for N seconds")
    s.add_argument("--pin", type=int, required=True)
    s.add_argument("--duration", type=float, required=True)
    s.set_defaults(func=_cmd_pulse_pump)

    s = sub.add_parser("set-relay", help="Drive a relay GPIO on or off")
    s.add_argument("--pin", type=int, required=True)
    s.add_argument("--state", required=True, choices=("on", "off"))
    s.add_argument("--active-low", action="store_true",
                   help="Use if your relay board is active-low (most blue relay modules)")
    s.set_defaults(func=_cmd_set_relay)

    return p


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except SystemExit:
        raise
    except Exception as exc:
        _emit({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ok=False)
