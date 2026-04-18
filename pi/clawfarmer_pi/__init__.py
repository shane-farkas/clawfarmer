"""Pi-side helpers for the clawfarmer OpenClaw pack.

Read sensors and pulse the pump from the CLI:

    python3 -m clawfarmer_pi read-soil --channel 0
    python3 -m clawfarmer_pi read-bme280
    python3 -m clawfarmer_pi read-lux
    python3 -m clawfarmer_pi pulse-pump --pin 17 --duration 10
    python3 -m clawfarmer_pi set-relay --pin 27 --state on
"""

__version__ = "0.1.0"
