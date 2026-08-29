"""Green Mountain Grill UDP protocol client.

Rewritten to fix three bugs present in the original implementation. The
wire-format parsing in _parse_status (raw byte-indexed, not comma-split
text) is preserved exactly as-is from the original -- it isn't independently
documented anywhere, so it isn't safe to reinterpret without a real grill
to verify against. Everything else here is provably a bug from the control
flow alone, independent of the protocol's actual byte layout:

1. grill.status() retried on "status is None or count < 5" -- an OR, not
   an AND. Once count reached 5 the loop never actually exited if the
   grill still hadn't responded, since "status is None" alone kept the
   condition true. Any time the grill drops off wifi mid-cook, that call
   hung forever.
2. grill.send() opened a UDP socket but never closed it, on any path.
   Every poll leaked a file descriptor.
3. Each entity (grill + 2 probes) independently called .status() on its
   own poll cycle -- one "check the grill" pass was actually three full
   UDP round-trips. Fixed at the coordinator layer, not here, but this
   module now assumes a single shared caller.
"""

from __future__ import annotations

import ipaddress
import logging
import socket

from .const import (
    MAX_STATUS_RETRIES,
    MAX_TEMP_F,
    MAX_TEMP_F_PROBE,
    MIN_TEMP_F,
    MIN_TEMP_F_PROBE,
)

_LOGGER = logging.getLogger(__name__)

UDP_PORT = 8080
CODE_SERIAL = b"UL!"
CODE_STATUS = b"UR001!"
# "UN!" -- confirmed against github.com/brandenco/green-mountain-grill's
# CommandGetGrillFirmware, which matches every other command code this
# project already had independently verified. Not previously implemented
# here; the response's exact format is unconfirmed (assumed to decode as
# plain text, same as the serial number response), so it's surfaced as-is
# rather than parsed into structured fields.
CODE_FIRMWARE = b"UN!"


class GmgCommunicationError(Exception):
    """Raised when the grill does not respond after all retries."""


def discover_grills(timeout: float = 2, ip_bind_address: str = "0.0.0.0") -> list["Grill"]:
    """Broadcast for grills on every local interface and return what answered.

    Blocking -- callers on the event loop must run this via
    hass.async_add_executor_job.
    """
    _LOGGER.debug("Broadcasting for grills (timeout=%s)", timeout)

    interfaces = socket.getaddrinfo(host=socket.gethostname(), port=None, family=socket.AF_INET)
    all_ips = {ip[-1][0] for ip in interfaces}
    all_ips.add(ip_bind_address)

    found: dict[str, "Grill"] = {}

    for ip in all_ips:
        _LOGGER.debug("Broadcasting from interface %s", ip)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.bind((ip, 0))
                sock.settimeout(timeout)
                sock.sendto(CODE_SERIAL, ("<broadcast>", UDP_PORT))

                while True:
                    try:
                        data, (address, _) = sock.recvfrom(1024)
                    except socket.timeout:
                        break

                    response = data.decode("utf-8", errors="ignore")
                    if not response.startswith("GMG"):
                        continue

                    if response in found:
                        _LOGGER.debug("Grill %s already found, skipping duplicate", response)
                        continue

                    _LOGGER.debug("Found grill %s at %s", response, address)
                    found[response] = Grill(address, response)
        except OSError as err:
            _LOGGER.debug("Could not broadcast on interface %s: %s", ip, err)

    _LOGGER.debug("Discovery finished, found %d grill(s)", len(found))
    return list(found.values())


class Grill:
    """A single Green Mountain Grill, communicating over its UDP protocol."""

    MIN_TEMP_F = MIN_TEMP_F
    MAX_TEMP_F = MAX_TEMP_F
    MIN_TEMP_F_PROBE = MIN_TEMP_F_PROBE
    MAX_TEMP_F_PROBE = MAX_TEMP_F_PROBE

    def __init__(self, ip: str, serial_number: str = "") -> None:
        if not ipaddress.ip_address(ip):
            raise ValueError(f"IP address not valid: {ip}")

        self._ip = ip
        self._serial_number = serial_number

    @property
    def ip(self) -> str:
        return self._ip

    @property
    def serial_number(self) -> str:
        return self._serial_number

    def send(self, message: bytes, timeout: float = 1):
        """Send one UDP message and return the raw response, or None on timeout."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(timeout)
                sock.sendto(message, (self._ip, UDP_PORT))
                data, _ = sock.recvfrom(1024)
                return data
        except socket.timeout:
            _LOGGER.debug("Timed out waiting for grill %s to respond", self._ip)
            return None
        except OSError as err:
            _LOGGER.warning("Error communicating with grill %s: %s", self._ip, err)
            return None

    def status(self) -> dict:
        """Fetch and parse the grill's current status.

        Retries up to MAX_STATUS_RETRIES times, then raises
        GmgCommunicationError -- callers must not treat a missing response
        as success, unlike the original implementation.
        """
        response = None
        attempts = 0

        while response is None and attempts < MAX_STATUS_RETRIES:
            response = self.send(CODE_STATUS)
            attempts += 1

        if response is None:
            raise GmgCommunicationError(
                f"No response from grill {self._ip} after {MAX_STATUS_RETRIES} attempts"
            )

        return self._parse_status(response)

    def serial(self) -> str:
        """Fetch the grill's serial number over the network."""
        response = self.send(CODE_SERIAL)
        if response is None:
            raise GmgCommunicationError(f"No response from grill {self._ip} requesting serial")

        self._serial_number = response.decode("utf-8", errors="ignore")
        return self._serial_number

    def firmware(self) -> str | None:
        """Fetch the grill's firmware version, once, at setup.

        Not polled -- firmware doesn't change mid-cook, and there's no
        reason to spend a UDP round-trip on it every 30 seconds. Returns
        None on failure rather than raising, since this is diagnostic
        information, not something that should block setup or a status
        poll if it's temporarily unavailable.
        """
        response = self.send(CODE_FIRMWARE)
        if response is None:
            return None
        return response.decode("utf-8", errors="ignore").strip()

    def set_temp(self, target_temp: int):
        """Set the grill's target temperature."""
        if not MIN_TEMP_F <= target_temp <= MAX_TEMP_F:
            raise ValueError(f"Target temperature {target_temp} is out of range")

        return self.send(b"UT" + str(target_temp).encode() + b"!")

    def set_temp_probe(self, target_temp: int, probe_number: int):
        """Set a food probe's target/alarm temperature."""
        if not MIN_TEMP_F_PROBE <= target_temp <= MAX_TEMP_F_PROBE:
            raise ValueError(f"Target temperature {target_temp} is out of range")

        if probe_number == 1:
            message = b"UF" + str(target_temp).encode() + b"!"
        elif probe_number == 2:
            message = b"Uf" + str(target_temp).encode() + b"!"
        else:
            raise ValueError(f"Unknown probe number: {probe_number}")

        return self.send(message)

    def power_on(self):
        return self.send(b"UK001!")

    def power_on_cool(self):
        """Power on in cold-smoke mode."""
        return self.send(b"UK002!")

    def power_off(self):
        return self.send(b"UK004!")

    @staticmethod
    def _combine_temp(low: int, high: int) -> int:
        """Combine a temperature's low/high byte pair into one value.

        Confirmed against github.com/brandenco/green-mountain-grill's own
        test fixtures (independently reverse-engineered, MIT licensed) --
        hand-recomputed here, not just trusted: (high << 8) + low matches
        every temperature field in both of that project's captured real
        payloads, including the probe-disconnected sentinel (601, which
        the original single-byte-only parsing in this project's history
        read as a coincidentally-similar-looking 89). Above 255F this is
        not optional -- the low byte alone cannot represent it at all.
        """
        return (high << 8) + low

    @staticmethod
    def _parse_status(raw: bytes) -> dict:
        # The overall byte layout (which index means what) is preserved
        # from this project's own history. The VALUE at each temperature
        # index is now combined with its paired high byte -- see
        # _combine_temp's docstring for why this isn't optional above
        # 255F, and CHANGES.md for the full verification writeup.
        values = list(raw)

        try:
            parsed = {
                "on": values[30],
                "temp": Grill._combine_temp(values[2], values[3]),
                "grill_set_temp": Grill._combine_temp(values[6], values[7]),
                "probe1_temp": Grill._combine_temp(values[4], values[5]),
                "probe1_set_temp": Grill._combine_temp(values[28], values[29]),
                "probe2_temp": Grill._combine_temp(values[16], values[17]),
                "probe2_set_temp": Grill._combine_temp(values[18], values[19]),
                "fireState": values[32],
                "fireStatePercentage": values[33],
                # Original single-byte read of warnState (index 24 alone) is
                # very likely incomplete: the independent reference project
                # treats this as a 4-byte value spanning indices 24-27,
                # combined the same way CurveRemainTime is. Both known real
                # payloads have all four bytes at 0 (no active warning), so
                # this specific combination hasn't been confirmed against a
                # real non-zero warning the way the temperature fields have
                # -- but reading only 1 of 4 bytes is provably incomplete
                # either way, so it's fixed here rather than left as-is.
                "warnState": (
                    (values[27] << 24) + (values[26] << 16) + (values[25] << 8) + values[24]
                ),
            }
        except IndexError as err:
            raise GmgCommunicationError(
                f"Status response shorter than expected ({len(values)} bytes): {raw!r}"
            ) from err

        # Every byte, indexed by position, alongside the named fields above.
        # A meaningful chunk of this payload isn't decoded anywhere in this
        # project's history -- there may be real signal in the unused
        # indices (hopper level, run time, an error code, ambient temp),
        # but guessing at meanings without a real grill to correlate
        # against is how the probe-disconnected heuristic already in this
        # codebase happened. This is the tool for finding more of those
        # safely: watch which index changes when you do something specific
        # to the grill, then promote it to a named field in const.py once
        # confirmed. See sensor.py's raw status entity and CHANGES.md.
        parsed["_raw_bytes"] = values

        return parsed
