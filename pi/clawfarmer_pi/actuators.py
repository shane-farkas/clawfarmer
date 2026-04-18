"""GPIO actuator control: the pump MOSFET and any relay-driven loads."""

from __future__ import annotations

import time
from datetime import datetime, timezone

MAX_PUMP_SECONDS = 60.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def pulse_pump(pin: int, duration_s: float) -> dict:
    """Drive the pump MOSFET high for duration_s, then low.

    A hard upper bound (MAX_PUMP_SECONDS) prevents a runaway command even if the
    caller passes a larger value. 60s is tuned for a ~40 mL/min peristaltic
    dosing pump (~40 mL per max pulse). For a faster centrifugal pump, lower it.
    The GPIO is explicitly returned to LOW in a finally block so a Ctrl-C or
    exception cannot leave the pump running.
    """
    from gpiozero import DigitalOutputDevice

    clamped = max(0.0, min(float(duration_s), MAX_PUMP_SECONDS))
    pump = DigitalOutputDevice(pin, active_high=True, initial_value=False)
    started_at = _now_iso()
    try:
        pump.on()
        time.sleep(clamped)
    finally:
        pump.off()
        pump.close()
    return {
        "actuator": "pump",
        "pin": pin,
        "duration_s": clamped,
        "clamped": clamped != float(duration_s),
        "started_at": started_at,
        "finished_at": _now_iso(),
        "ok": True,
    }


def set_relay(pin: int, state: bool, active_high: bool = True) -> dict:
    """Drive a GPIO high or low for a latching relay (grow light, fan)."""
    from gpiozero import DigitalOutputDevice

    relay = DigitalOutputDevice(pin, active_high=active_high, initial_value=state)
    # gpiozero sets the value via the constructor, but we re-assert to make the
    # command idempotent: calling with state=True when it's already on is a no-op
    # from the plant's perspective.
    if state:
        relay.on()
    else:
        relay.off()
    value = relay.value
    relay.close()
    return {
        "actuator": "relay",
        "pin": pin,
        "state": "on" if state else "off",
        "active_high": active_high,
        "gpio_value": value,
        "at": _now_iso(),
        "ok": True,
    }
