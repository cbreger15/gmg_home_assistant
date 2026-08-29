# Changes from jwhitby91/gmg_home_assistant

Everything here was found by reading the original implementation closely
while unblocking it for a current Home Assistant install, not assumed --
see gmg.py's module docstring for the specifics on what's provably a bug
versus what's preserved on purpose.

## Added in 3.1.0

- **Firmware version.** `UN!` ("get grill firmware") is in the reference
  project's own command set (`brandenco/green-mountain-grill`) and matches
  the naming convention of every other command this fork already had
  independently confirmed -- not previously implemented here. Fetched
  once at setup (not polled -- firmware doesn't change mid-cook) and shown
  as the device's `sw_version` in Home Assistant's device page. The exact
  response format is unconfirmed, so it's decoded as plain text and
  surfaced as-is rather than parsed into structured fields.
- **`homeassistant` minimum version pin** (`2025.1.0`) added to the
  manifest. Given the entire reason this fork exists is an HA-version
  compatibility break in the original project, it seemed worth actually
  preventing installation on an HA version too old for the modern APIs
  this fork now depends on, rather than repeating the same failure mode
  from the other direction.
- **CI.** `.github/workflows/test.yml` now runs `tests/test_gmg_parsing.py`
  and a syntax check on every push/PR, instead of relying on remembering
  to run them by hand.
- **Expanded test suite:** command-byte-building for every outgoing
  command (confirmed against the reference project's own command set),
  `set_temp`'s range validation, `is_probe_connected`'s boundaries, and
  the new firmware command's success/failure paths. 10 tests total, all
  pure Python, runnable without any Home Assistant test harness.

## Fixed

- **Infinite loop on no response.** `grill.status()`'s retry condition was
  `status is None or count < 5` (an OR). Once the retry count hit 5, if the
  grill still hadn't responded, `status is None` alone kept the loop going
  forever. Any time the grill drops off wifi mid-cook, that call would hang
  indefinitely. Now bounded correctly and raises `GmgCommunicationError`
  instead of hanging or silently returning nothing.
- **Socket file descriptor leak.** `grill.send()` opened a UDP socket on
  every call and never closed it, on any path. Now uses `with socket...`
  so it's always closed.
- **3x redundant polling.** The grill and both probes were separate
  entities that each independently called `.status()` on their own timer --
  one logical "check the grill" was three full UDP round-trips. Replaced
  with a single `DataUpdateCoordinator` shared by every entity for one grill.
- **Blocking I/O on the event loop.** Discovery and status polling are
  genuinely blocking socket calls; they're now always run via
  `hass.async_add_executor_job`, including at config-flow / platform setup
  time (the original called discovery directly inside an `async def`).
- **Swallowed errors.** `print(e)` on socket errors → real `_LOGGER` calls;
  a malformed/short status response now raises instead of silently
  returning a partially-filled or empty state dict.

## Changed

- **Config flow instead of YAML-only.** The discovery logic already
  existed in the original `gmg.py`, it just was never wired into a config
  flow. `Settings → Devices & Services → Add Integration` now works.
- **Probes are no longer fake climate entities.** A food probe modeled as
  a `ClimateEntity` with `HVACMode.HEAT`/`HVACMode.OFF` standing in for
  "connected" was a real hack (the original README says as much). Probes
  are now a `sensor` (current temperature, unavailable when disconnected),
  a `number` (target/alarm temperature, settable), and a `binary_sensor`
  (connected). This is also what makes a "notify me when the probe hits
  temp" automation straightforward to write.
- **Grill health data finally exposed.** `fireState`, `fireStatePercentage`,
  and `warnState` were already being parsed out of every status response,
  they just weren't attached to any entity. Now `sensor.*_fire_state`,
  `sensor.*_fire_state_percentage`, and `binary_sensor.*_warning`.
- **One HA Device per grill.** Grill + both probes now group under a
  single device via `DeviceInfo`, instead of three unrelated top-level
  entities named by raw serial number.

## Fixed in 3.0.0 -- a real, independently-verified temperature bug

2.1.0's Raw Status sensor was built specifically to find more of this
protocol safely instead of guessing. That search turned up
[brandenco/green-mountain-grill](https://github.com/brandenco/green-mountain-grill)
(MIT licensed), an independent Go reverse-engineering of this exact same
protocol -- including real captured payloads with known-correct expected
output as test fixtures. Its command codes (`UT%03d!`, `UK001!`, `UK002!`,
`UK004!`, etc.) match this project's exactly, which is what made it worth
taking seriously rather than dismissing as an unrelated guess.

Its fixtures were hand-recomputed here from scratch, independently -- not
trusted blindly -- and every field matched. `tests/test_gmg_parsing.py`
reproduces that verification as a real, runnable test using those exact
byte sequences.

- **Temperature readings above 255°F were being silently truncated or
  wrong.** Every temperature field (`temp`, `grill_set_temp`, both probes'
  current and set temperatures) is actually a 16-bit value split across a
  low byte and a "_high" byte -- `(high << 8) + low`. The original
  implementation, and this fork through 2.1.0, parsed and even exposed the
  "_high" fields but never combined them into anything -- they sat unused
  in the state dict. Since this grill's own documented range goes up to
  500°F, and a single byte tops out at 255, this was a real correctness
  gap for a meaningful chunk of its actual operating range, not an edge
  case. Confirmed by hand-recomputing both of the reference project's real
  captured payloads: every combined value matches their documented
  expected output exactly.
- **Cold-smoke mode was misdetected as "Off."** The original mapped
  `on == 2` to cold-smoke/`HVACMode.FAN_ONLY`. A real captured "power on
  cold smoke" payload shows `on == 3`, not 2 -- confirmed by hand
  recomputation, not just read off the reference project's claim. Fixed
  in `climate.py`; `on == 2` is a distinct, unconfirmed state neither
  project has a real example of, and is left falling through to "Off"
  rather than guessed at.
- **Probe-disconnected detection was reading the wrong byte.** The
  original single-byte read happened to see `89` for a disconnected probe
  -- but that's the low byte of a combined value that's actually `601`,
  confirmed identically across both probes in both of the reference
  project's real payloads. `601` is outside a probe's real physical range
  (32-257°F), which is what's actually checked now
  (`const.is_probe_connected`) instead of hardcoding either magic number --
  a range check degrades safely even if the exact sentinel value varies.
- **`warnState` was reading 1 byte of what is very likely a 4-byte value**
  (indices 24-27, combined the same way as the temperature fields). Fixed
  to match. Both known real payloads have all four bytes at zero (no
  active warning), so unlike the temperature fix above, this specific
  combination hasn't been confirmed against a real non-zero warning --
  but reading only 1 of 4 bytes was provably incomplete regardless of
  what the correct combination turns out to be.
- **Fire state now exposes a friendly name** (`sensor.*_fire_state`
  returns e.g. `"off"` or `"cold_smoke"` instead of a bare number) where
  one is reasonably known. Only `off` (1) and `cold_smoke` (198) are
  confirmed against real captured payloads; `default`/`startup`/`running`/
  `cooldown`/`fail` are carried over from the reference project's own enum
  but unconfirmed here -- worth double-checking if one of those shows up,
  not treating as certain. The raw numeric code is always available as an
  attribute.

**What's still just this fork's own history, unverified against anything
independent:** the actual index-to-field mapping itself (that byte 2 is
grill temp, byte 4 is probe 1, etc.) -- the reference project agrees with
it exactly, which is reassuring, but neither project's author has stated
where that original mapping came from. The 150°F minimum-before-setpoint-
change rule is unrelated to any of this -- it's from the GMG manual per
this project's own original testing notes, not the wire protocol.

## Added in 2.1.0

- **Raw Status sensor.** Only 16 of the response's bytes are decoded into
  named fields anywhere in this project's history -- the rest may hold real
  signal (hopper level, run time, an error code, ambient temp) that's never
  been identified. `sensor.*_raw_status` exposes every byte, indexed by
  position, as an attribute -- disabled by default (it's a
  reverse-engineering tool, not a day-to-day entity). Watch which index
  changes when you do something specific to the grill, confirm it holds
  across more than one observation, then promote it to a named field in
  `const.py`/`gmg.py` the same way the existing 16 fields were identified.

## Known gaps, not addressed here

- Fire state's friendly names are only 2-of-7 independently confirmed (see
  3.0.0 above) -- the rest are plausible, not certain.
- `warnState`'s 4-byte combination is structurally consistent with
  everything else that has been confirmed, but hasn't itself been checked
  against a real non-zero warning -- both known payloads show zero.
- Whatever's in the currently-undecoded bytes (see Raw Status sensor,
  added 2.1.0) hasn't been identified. It's observable now, not decoded.
- `PowerState == 2` ("fan," per the reference project's own enum) has no
  confirmed real example in either project and isn't mapped to anything.
- Test coverage now includes `tests/test_gmg_parsing.py` (the protocol
  parsing itself, runnable without any Home Assistant test harness) but
  still doesn't cover the entity-layer modules (sensor/number/
  binary_sensor/config_flow) -- those still only have the original bare
  component-setup test.
