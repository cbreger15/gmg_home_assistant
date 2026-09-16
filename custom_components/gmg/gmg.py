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

from dataclasses import dataclass
import ipaddress
import logging
import socket
import threading
import time

from .const import (
    CLIMATE_SETTINGS,
    CONFIG_CONFIRM_FIRST_DELAY,
    CONFIG_CONFIRM_INTERVAL,
    CONFIG_CONFIRM_POLLS,
    CONFIG_READ_ATTEMPTS,
    CONFIG_WRITE_API_VERSIONS,
    MAX_STATUS_RETRIES,
    MIN_STATUS_BYTES,
    MAX_TEMP_F,
    MAX_TEMP_F_PROBE,
    MIN_TEMP_F,
    MIN_TEMP_F_PROBE,
    STATUS_PACKET_BYTES,
    STATUS_PREFIX,
    decode_warnings,
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
# "UC" + the 8-byte config block + "!" -- from facultymatt/gmg-js (2020),
# whose notes record whole frames (5543050b02322020202021, "pizza mode off")
# and whose author said it "used to work". No other project sends it.
# Confirmed on a Jim Bowie (APIv6) on 16 Sep 2026: Pizza Mode on and off from
# Home Assistant, each read back exactly. Grill.write_config_field still reads
# the block back after every write.
CODE_WRITE_CONFIG = b"UC"


class GmgCommunicationError(Exception):
    """Raised when the grill does not respond after all retries."""


class GmgConfigWriteError(GmgCommunicationError):
    """Raised when a config write was refused, or the grill did not end up as asked."""


def is_status_reply(raw: bytes) -> bool:
    """Whether a reply can be read for status: it starts UR and reaches byte 33."""
    return len(raw) >= MIN_STATUS_BYTES and raw[:2] == STATUS_PREFIX


def is_full_status_reply(raw: bytes) -> bool:
    """Whether a reply is exactly one whole packet: the only kind config is read from."""
    return len(raw) == STATUS_PACKET_BYTES and raw[:2] == STATUS_PREFIX


# Status bytes 8-15: the GMG app's Grill Config screen. The app writes all
# eight back at once (see Grill.write_config_field), so they are kept together.
CONFIG_BLOCK = slice(8, 16)


@dataclass(frozen=True)
class ConfigField:
    """One setting packed into the Grill Config block."""

    name: str
    index: int  # byte within the block; block[0] is status byte 8
    shift: int
    width: int  # bits
    max_value: int  # highest value the app offers

    def __post_init__(self) -> None:
        block_len = CONFIG_BLOCK.stop - CONFIG_BLOCK.start
        if not (
            0 <= self.index < block_len
            and self.shift >= 0
            and self.width >= 1
            and self.shift + self.width <= 8
            and 0 < self.max_value < 1 << self.width
        ):
            raise ValueError(f"{self.name} does not fit its bits: {self}")

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.shift

    def validate(self, value: int) -> None:
        # bool is an int, and True/False are fine for a one-bit field.
        if not isinstance(value, int) or not 0 <= value <= self.max_value:
            raise ValueError(f"{self.name} must be a whole number 0-{self.max_value}, got {value!r}")


# Status byte 9, decoded on a Jim Bowie (firmware 2.3, APIv6) on 16 Sep 2026 by
# changing one setting at a time in the GMG app and reading the packet back:
#
#   bit 5     Pizza Mode          07 -> 27 when turned on
#   bits 4-2  Climate, 0-4        Icy 03, Cold 07, Average 0b, Warm 0f, Hot 13
#   bit 1     Auto-Revert WiFi    0b -> 09 when turned off
#   bit 0     Lock Temp Display   0b -> 0a when turned off
#
# facultymatt/gmg-js (2020) is the only earlier source. Its numbers agree once
# the fields are split -- its "Hot = 1" is bit 4 alone, its "Average = 8" is
# bits 4-2 with both toggles still attached -- but it set Pizza Mode by
# overwriting the whole top nibble, which also clears bit 4 and silently drops
# a Hot grill to Icy. Fields here change only their own bits.
PIZZA_MODE = ConfigField("pizza_mode", index=1, shift=5, width=1, max_value=1)
CLIMATE = ConfigField("climate", index=1, shift=2, width=3, max_value=len(CLIMATE_SETTINGS) - 1)
AUTO_REVERT_WIFI = ConfigField("auto_revert_wifi", index=1, shift=1, width=1, max_value=1)
LOCK_TEMP_DISPLAY = ConfigField("lock_temp_display", index=1, shift=0, width=1, max_value=1)


@dataclass(frozen=True)
class GrillConfig:
    """The Grill Config block, exactly as the grill sent it.

    Bytes 10-15 are the app's temperature calibration boxes, a left and a
    right box per adjustment: the grill's apply at 150F and 500F, each food
    probe's at 32F and 212F (per GMG's support pages). On 16 Sep 2026 the app
    set the three left boxes to +2 / +8 / +4 and bytes 10, 12 and 14 read
    22 / 33 / 29, so a left box is stored as 20 (grill) or 25 (probe) plus the
    adjustment; the right boxes, at 0, read 50 / 25 / 25. Negative values and
    non-zero right boxes have not been seen, so these stay raw and are never
    written.
    """

    block: bytes

    def __post_init__(self) -> None:
        if len(self.block) != CONFIG_BLOCK.stop - CONFIG_BLOCK.start:
            raise ValueError(f"A config block is 8 bytes, got {len(self.block)}: {self.block!r}")

    def __str__(self) -> str:
        return self.block.hex(" ")

    def get(self, field: ConfigField) -> int:
        return (self.block[field.index] & field.mask) >> field.shift

    def with_field(self, field: ConfigField, value: int) -> GrillConfig:
        """This block with one field changed and every other bit as it was."""
        field.validate(value)
        block = bytearray(self.block)
        block[field.index] = (block[field.index] & ~field.mask & 0xFF) | (value << field.shift)
        return GrillConfig(bytes(block))

    def write_frame(self) -> bytes:
        """The command that writes this whole block to the grill."""
        return CODE_WRITE_CONFIG + self.block + b"!"

    @property
    def api_version(self) -> int:
        return self.block[0]

    @property
    def pizza_mode(self) -> bool:
        return bool(self.get(PIZZA_MODE))

    @property
    def climate(self) -> int:
        """0-4 in CLIMATE_SETTINGS order; 5-7 have never been seen."""
        return self.get(CLIMATE)

    @property
    def auto_revert_wifi(self) -> bool:
        return bool(self.get(AUTO_REVERT_WIFI))

    @property
    def lock_temp_display(self) -> bool:
        return bool(self.get(LOCK_TEMP_DISPLAY))

    @property
    def grill_adjustment_raw(self) -> tuple[int, int]:
        """Status bytes 10-11, as sent: the grill's 150F and 500F boxes."""
        return (self.block[2], self.block[3])

    @property
    def probe1_adjustment_raw(self) -> tuple[int, int]:
        """Status bytes 12-13, as sent: probe 1's 32F and 212F boxes."""
        return (self.block[4], self.block[5])

    @property
    def probe2_adjustment_raw(self) -> tuple[int, int]:
        """Status bytes 14-15, as sent: probe 2's 32F and 212F boxes."""
        return (self.block[6], self.block[7])


# One config write per grill at a time -- per address, not per Grill object:
# reloading the integration makes a new Grill while the old one may still be
# confirming a write, and each write reads the block, changes one field and
# writes all eight bytes back, so two at once would undo each other.
_CONFIG_WRITE_LOCKS: dict[str, threading.Lock] = {}
_CONFIG_WRITE_LOCKS_GUARD = threading.Lock()


def _config_write_lock(ip: str) -> threading.Lock:
    with _CONFIG_WRITE_LOCKS_GUARD:
        return _CONFIG_WRITE_LOCKS.setdefault(ip, threading.Lock())


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

    # How write_config_field waits between read-backs. Tests replace it.
    _sleep = staticmethod(time.sleep)

    def __init__(self, ip: str, serial_number: str = "") -> None:
        if not ipaddress.ip_address(ip):
            raise ValueError(f"IP address not valid: {ip}")

        self._ip = ip
        self._serial_number = serial_number
        self._config_lock = _config_write_lock(ip)

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

        A SHORT RESPONSE IS RETRIED THE SAME WAY A MISSING ONE IS, and that
        is the whole point of this loop's shape. The previous version guarded
        on `response is None`, which is only true on a socket timeout. A
        truncated payload is not None -- it is a perfectly good `bytes` object
        that happens to be too short to parse -- so it fell straight through
        to _parse_status, raised, and took every entity for the grill
        unavailable until the next poll.

        Five retries were configured and none of them could ever be spent on
        the failure that actually happens. Measured on one install: 112 of
        these in 25 hours, each costing a full DEFAULT_SCAN_INTERVAL (30s) of
        unavailability, recovering on its own every time.

        A REPLY THAT LOST ITS HEAD IS RETRIED TOO. Checking the length alone
        let through replies that arrived without their first bytes: 46, 48
        and 50 bytes is plenty, but every field then sits at the wrong offset
        and the grill reads as "on" at 601F. The UR prefix is what proves the
        offsets are right (see STATUS_PREFIX).
        """
        attempts = 0
        last_bad = None

        while attempts < MAX_STATUS_RETRIES:
            response = self.send(CODE_STATUS)
            attempts += 1

            if response is None:
                continue

            if is_status_reply(response):
                return self._parse_status(response)

            # Arrived, but cannot be read. Worth a debug line rather than
            # silence: if a grill ever returns a CONSISTENT short or headless
            # reply, that is a protocol difference to investigate, not a
            # blip to retry past.
            last_bad = response
            _LOGGER.debug(
                "Unusable status response from grill %s (%d bytes, starts %r), retrying",
                self._ip,
                len(response),
                response[:2],
            )

        if last_bad is not None:
            raise GmgCommunicationError(
                f"Grill {self._ip} returned no usable status response in "
                f"{MAX_STATUS_RETRIES} attempts; last was {len(last_bad)} "
                f"bytes, need {MIN_STATUS_BYTES} starting {STATUS_PREFIX!r}: "
                f"{last_bad!r}"
            )

        raise GmgCommunicationError(
            f"No response from grill {self._ip} after {MAX_STATUS_RETRIES} attempts"
        )

    def write_config_field(self, field: ConfigField, value: int) -> dict:
        """Change one Grill Config setting and confirm the grill took it.

        A write replaces all eight bytes of the block, so it starts from the
        block as the grill holds it right now -- never a remembered or default
        copy, which would silently undo anything changed in the GMG app since.

        1. Read the block until two whole status packets agree on it. If they
           never do, nothing is sent.
        2. Refuse a grill whose config API version is not verified.
        3. Change `field` in that block. If it already has `value`, stop.
        4. Send UC + block + ! once -- whole, even when a byte of the block is
           itself 0x21, the "!" that ends every command: the grill reads the
           frame by length (confirmed 16 Sep 2026, when the GMG app wrote
           06 09 16 32 21 19 1d 19 and every byte landed). A write that did
           not land is reported, never repeated.
        5. Read the block back, first after CONFIG_CONFIRM_FIRST_DELAY and then
           every CONFIG_CONFIRM_INTERVAL. The grill reporting exactly the block
           sent is success; any other change fails at once; no change fails
           after CONFIG_CONFIRM_POLLS reads.

        Returns the status that confirmed it. Raises ValueError for a value
        the app does not offer (before any I/O), GmgCommunicationError if the
        grill cannot be read, GmgConfigWriteError for a refusal or a failed
        write. Blocking: a few seconds normally, up to about a minute when the
        grill stops answering -- run it in the executor.
        """
        field.validate(value)

        with self._config_lock:
            current = self._read_agreed_config()
            before: GrillConfig = current["config"]

            if before.api_version not in CONFIG_WRITE_API_VERSIONS:
                raise GmgConfigWriteError(
                    f"Grill {self._ip} reports config API version {before.api_version}; "
                    f"writing settings is only verified on version "
                    f"{', '.join(map(str, sorted(CONFIG_WRITE_API_VERSIONS)))}. "
                    f"Nothing was sent."
                )

            if before.get(field) == value:
                return current

            expected = before.with_field(field, value)
            _LOGGER.info(
                "Grill %s: setting %s to %s, config block %s -> %s",
                self._ip,
                field.name,
                value,
                before,
                expected,
            )
            reply = self.send(expected.write_frame())
            _LOGGER.debug("Grill %s answered the config write with %r", self._ip, reply)

            return self._confirm_config(before, expected)

    def _read_agreed_config(self) -> dict:
        """The latest status, once two whole packets in a row agree on the block.

        One packet is not enough to write from. The read-back after a write
        cannot catch a bad starting block -- the grill faithfully reports
        whatever it was sent -- and this link delivers replies held back from
        earlier requests (two whole replies have arrived as one datagram), so
        a single whole-looking packet can be stale or spliced. Pieces of
        replies in between are skipped, not counted as disagreement.
        """
        previous = None
        disagreement = None
        last_bad = None

        for _ in range(CONFIG_READ_ATTEMPTS):
            response = self.send(CODE_STATUS)
            if response is None:
                continue
            if not is_full_status_reply(response):
                last_bad = response
                continue

            status = self._parse_status(response)
            if previous is not None:
                if status["config"] == previous["config"]:
                    return status
                disagreement = (previous["config"], status["config"])
                _LOGGER.debug(
                    "Grill %s: config block %s then %s, reading again",
                    self._ip,
                    *disagreement,
                )
            previous = status

        if previous is None:
            detail = (
                f"the last reply was {len(last_bad)} bytes: {last_bad!r}"
                if last_bad is not None
                else "no reply at all"
            )
            raise GmgCommunicationError(
                f"Grill {self._ip} sent no whole status packet (exactly "
                f"{STATUS_PACKET_BYTES} bytes starting {STATUS_PREFIX!r}) in "
                f"{CONFIG_READ_ATTEMPTS} attempts; {detail}. Nothing was sent."
            )
        if disagreement is None:
            raise GmgCommunicationError(
                f"Grill {self._ip} sent only one whole status packet in "
                f"{CONFIG_READ_ATTEMPTS} attempts, and a write needs two that agree. "
                f"Nothing was sent."
            )
        raise GmgConfigWriteError(
            f"Grill {self._ip} never sent two whole status packets in a row with "
            f"the same config block in {CONFIG_READ_ATTEMPTS} attempts (the last "
            f"two read {disagreement[0]}, then {disagreement[1]}), so its current "
            f"settings are uncertain. Nothing was sent."
        )

    def _confirm_config(self, before: GrillConfig, expected: GrillConfig) -> dict:
        seen = None

        for poll in range(CONFIG_CONFIRM_POLLS):
            self._sleep(CONFIG_CONFIRM_FIRST_DELAY if poll == 0 else CONFIG_CONFIRM_INTERVAL)
            response = self.send(CODE_STATUS)
            if response is None or not is_full_status_reply(response):
                continue

            status = self._parse_status(response)
            seen = status["config"]
            if seen == expected:
                _LOGGER.debug("Grill %s confirmed config block %s", self._ip, seen)
                return status
            if seen != before:
                raise GmgConfigWriteError(
                    f"Grill {self._ip} reports config block {seen} after a write of "
                    f"{expected} (it was {before}): something other than the "
                    f"requested setting changed. Check Grill Config in the GMG app, "
                    f"which can restore any setting."
                )

        reads = f"{CONFIG_CONFIRM_POLLS} read-backs"
        if seen is None:
            raise GmgConfigWriteError(
                f"Grill {self._ip} sent no whole status packet in {reads} after a "
                f"write of {expected}, so the result is unknown (it was {before}). "
                f"Check Grill Config in the GMG app."
            )
        raise GmgConfigWriteError(
            f"Grill {self._ip} still reports config block {seen} after {reads} "
            f"following a write of {expected}: the change did not take, and was "
            f"not repeated."
        )

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

        return self.send(b"UT%03d!" % target_temp)

    def set_temp_probe(self, target_temp: int, probe_number: int):
        """Set a food probe's target/alarm temperature.

        Always three digits, as every other implementation sends it: the
        Aenima4six2 emulator only matches UF(\\d{3})!, so a target under 100F
        sent as UF32! may never register on the grill.
        """
        if not MIN_TEMP_F_PROBE <= target_temp <= MAX_TEMP_F_PROBE:
            raise ValueError(f"Target temperature {target_temp} is out of range")

        if probe_number == 1:
            message = b"UF%03d!" % target_temp
        elif probe_number == 2:
            message = b"Uf%03d!" % target_temp
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
        if raw[:2] != STATUS_PREFIX:
            raise GmgCommunicationError(
                f"Status response does not start with {STATUS_PREFIX!r}, so its "
                f"fields cannot be located ({len(raw)} bytes): {raw!r}"
            )

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

        # As flags, so two warnings at once both show -- see const.WARN_FLAGS.
        parsed["warnings"] = decode_warnings(parsed["warnState"])

        # Only from a whole packet -- see STATUS_PACKET_BYTES.
        parsed["config"] = (
            GrillConfig(bytes(raw[CONFIG_BLOCK])) if is_full_status_reply(raw) else None
        )

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
