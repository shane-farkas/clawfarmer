"""Sensor reads. Each function returns a plain dict so the CLI can json.dumps it."""

from __future__ import annotations

from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_soil(
    channel: int,
    dry_raw: int,
    wet_raw: int,
    address: int = 0x48,
) -> dict:
    """Read a capacitive soil-moisture probe through an ADS1115 channel.

    dry_raw / wet_raw are the int16 ADC readings you capture during calibration
    (probe in open air for dry, probe in water for wet). The raw value is
    normalized linearly into a 0-100 pct_vwc scale and clamped to that range.
    """
    import board
    import busio
    from adafruit_ads1x15.ads1115 import ADS1115
    from adafruit_ads1x15.analog_in import AnalogIn

    i2c = busio.I2C(board.SCL, board.SDA)
    ads = ADS1115(i2c, address=address)
    pin = [ADS1115.P0, ADS1115.P1, ADS1115.P2, ADS1115.P3][channel]
    chan = AnalogIn(ads, pin)
    raw = chan.value
    voltage = chan.voltage

    if dry_raw == wet_raw:
        pct = None
    else:
        pct = (dry_raw - raw) / (dry_raw - wet_raw) * 100
        pct = max(0.0, min(100.0, pct))
        pct = round(pct, 1)

    return {
        "sensor": "ads1115_soil",
        "channel": channel,
        "raw": raw,
        "voltage": round(voltage, 4),
        "pct_vwc": pct,
        "at": _now_iso(),
    }


def read_bme280(address: int = 0x76) -> dict:
    """Read temperature, humidity, and pressure from a BME280."""
    import board
    import busio
    from adafruit_bme280 import basic as adafruit_bme280

    i2c = busio.I2C(board.SCL, board.SDA)
    bme = adafruit_bme280.Adafruit_BME280_I2C(i2c, address=address)

    temp_c = bme.temperature
    temp_f = temp_c * 9 / 5 + 32
    return {
        "sensor": "bme280",
        "temp_c": round(temp_c, 2),
        "temp_f": round(temp_f, 2),
        "humidity_pct": round(bme.relative_humidity, 1),
        "pressure_hpa": round(bme.pressure, 1),
        "at": _now_iso(),
    }


def read_lux(address: int = 0x23) -> dict:
    """Read ambient light in lux from a BH1750."""
    import board
    import busio
    import adafruit_bh1750

    i2c = busio.I2C(board.SCL, board.SDA)
    bh = adafruit_bh1750.BH1750(i2c, address=address)
    return {
        "sensor": "bh1750",
        "lux": round(bh.lux, 1),
        "at": _now_iso(),
    }
