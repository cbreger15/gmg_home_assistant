# Changes from jwhitby91/gmg_home_assistant

Everything here was found by reading the original implementation closely
while unblocking it for a current Home Assistant install, not assumed --
see gmg.py's module docstring for the specifics on what's provably a bug
versus what's preserved on purpose.

## Added in 3.2.0 -- the Grill Config screen, from Home Assistant

The GMG app's Grill Config screen was in every status reply all along: bytes
8-15, which the parser skipped. They were decoded on a Jim Bowie (firmware
2.3, "NJB APIv6") on 16 Sep 2026 by changing one setting at a time in the app
and reading the reply back:

| Byte | Setting | Observed |
| --- | --- | --- |
| 8 | API version | `6` |
| 9, bit 5 | Pizza Mode | `07` -> `27` when turned on |
| 9, bits 4-2 | Climate, 0-4 in slider order | Icy `03`, Cold `07`, Average `0b`, Warm `0f`, Hot `13` |
| 9, bit 1 | Auto-Revert WiFi | `0b` -> `09` when turned off |
| 9, bit 0 | Lock Temp Display | `0b` -> `0a` when turned off |
| 10-15 | Temperature calibration boxes | `14 32 19 19 19 19` -- raw, see below |

- **New entities.** **Pizza Mode** (switch) is an ordinary control, so Home
  Assistant's HomeKit bridge can publish it. **Climate Setting** (select),
  **Auto-Revert WiFi** and **Lock Temp Display** (switches) are config
  entities, which the bridge skips unless one is included by entity ID.
  **Config Block** (diagnostic sensor) shows the eight bytes as hex, with the
  API version and the six calibration bytes as attributes.
- **How a write works.** The only documented write is `UC` + the 8 bytes +
  `!`, from [facultymatt/gmg-js](https://github.com/facultymatt/gmg-js) (2020),
  whose notes record whole frames and whose author said it "used to work".
  A write replaces all eight bytes, so `Grill.write_config_field` never
  sends a remembered or default block. It:
  1. reads the grill until **two whole status packets in a row agree** on the
     block -- nothing is sent if they never do. One packet is not enough: the
     read-back in step 6 cannot catch a bad starting block (the grill reports
     whatever it was sent), and this link delivers replies held back from
     earlier requests;
  2. refuses any grill that doesn't report API version 6, the only version
     the layout is confirmed on;
  3. changes that one field's bits, and does nothing if they already match;
  4. refuses a new block containing `0x21`, the `!` that ends every command
     (see Known gaps);
  5. sends the frame **once** -- a write that did not land is reported, never
     repeated;
  6. reads the block back after 0.5 s, then every 2 s, 15 times in all. The
     exact block sent is success. Any other change fails at once, with the
     before / sent / now blocks in the error so the GMG app can put things
     back. No change at all fails after the last read -- about 30 s, or up to
     a minute if the grill has stopped answering.

  Writes to a grill take turns -- also across an integration reload -- so
  two quick toggles cannot both start from the same block and undo each
  other, and Home Assistant's own polling waits while a write is in
  progress. A failure shows as an error on the toggle or the automation step,
  and is logged.
- **gmg-js's own Pizza Mode write had a bug** that this avoids: it overwrote
  the whole top nibble of byte 9, which also clears bit 4 and silently drops
  a grill on Hot to Icy. Its other numbers agree once the fields are split --
  its "Hot = 1" is bit 4 alone, its "Average = 8" is bits 4-2 with both
  toggles still attached.
- **Bytes 10-15 are read, never written.** GMG's support pages say each
  adjustment has a left and a right box: the grill's apply at 150F and 500F,
  each probe's at 32F and 212F. Which byte is which box, and the stored zero
  (probably 20 / 50 / 25, since this grill reads `20 50 25 25 25 25` while the
  app shows 0 everywhere), are inferred. So the sensor labels them by box and
  shows them raw. Setting one box in the app and watching which byte moves
  will settle it.
- **Only a whole 52-byte packet is trusted for the block.** Cut and merged
  replies arrive too, and a misaligned byte 9 can read as Pizza Mode ON.
  Between whole packets the entities keep showing the last whole block --
  for display only; a write always reads the grill first. On grills that
  send 36-byte replies, the Grill Config entities stay unavailable. On a
  grill whose API version isn't 6, only Config Block is available: the
  controls would be showing an unconfirmed decode, and could not write.
- **Warning** (binary sensor) now lists which warnings are set, in a
  `warnings` attribute, plus the raw code (see below).

## Fixed in 3.2.0

- **Phantom cooks: a reply that lost its first bytes was read at the wrong
  offsets.** `status()` only checked length. But on one install 54 of 1,768
  polls in 48 hours arrived with 1-18 bytes missing from the front -- 46, 48,
  50 bytes, plenty to pass -- and every field then sat somewhere else. Home
  Assistant recorded grill temperatures of 601F, 2822F and 38402F, fire states
  like `unknown_85`, and a cold grill "on": 12 of the 21 off-to-on changes it
  recorded in 10 days were one bad reply, not a cook. A reply must now start
  `UR` to be read, and is otherwise retried like a short one. A reply cut at
  the end, or two run together, still starts `UR`, and every piece captured
  so far kept its bytes in order, so its status fields sit where they belong
  and it is still read. (A reply missing bytes from its middle would not be
  caught; none has been seen.)
- **Probe targets under 100F were sent unpadded.** `UF32!` is now `UF032!`
  (and `Uf` for probe 2), as every other implementation sends them; the
  Aenima4six2 emulator only recognises `UF(\d{3})!`. `UT` is padded the same
  way for consistency (grill targets are always three digits anyway).
- **`warnState` is decoded as flags**, so two warnings at once both show
  instead of reading as neither. The only real non-zero capture anyone has
  (gmg-js, 2020) is low pellets = 128, which is bit 7 -- and bit 7 is where
  low pellets lands if brandenco's warning list is read as one bit each. The
  other seven names follow that order and are **unconfirmed**: the
  Aenima4six2 emulator reads the same list as one code in steps of 16, and
  the two readings agree only on 128. Unnamed bits show as `unknown_bit_N`.
- **The Home Assistant tests run, in CI.** `tests/test_init.py` never enabled
  custom integrations, so it failed ("Integration not found") -- unnoticed,
  because CI only ran the pure-Python file. A new CI job installs Home
  Assistant 2026.9.2's test harness and runs everything, including new
  entity tests that drive the real write path against a simulated grill.
  Tests use a documentation-only address (192.0.2.x), and any attempt to
  open a real socket fails the test instead of reaching a grill.
- **The coordinator passes its config entry explicitly**, as Home Assistant
  asks, instead of relying on the context it was created in.

## Fixed in 3.1.2

- **A short status response is now retried instead of raising.** `status()`
  looped `while response is None`, which is only true on a socket timeout. A
  truncated payload is not `None` -- it is a perfectly good `bytes` object that
  happens to be too short to parse -- so it fell through to `_parse_status`,
  raised `GmgCommunicationError`, and took every entity for the grill
  unavailable until the next poll.

  **Five retries were configured and none of them could ever be spent on the
  failure that actually happens.** Measured on one install: 112 short responses
  in 25 hours (18, 22, 28, 29 and 31 bytes observed), each costing a full
  30-second scan interval of unavailability and recovering on its own every
  time.

  Ruled out while diagnosing, so nobody re-checks them: not the receive buffer
  (`recvfrom(1024)` against a 34-byte need), not a partial read (UDP datagrams
  are atomic -- the grill really does send short payloads), and not the parser
  (it correctly needs index 33 and correctly refuses to guess when it is
  absent; its `IndexError` handler was doing its job, the caller was not).

  This does not explain *why* a grill truncates -- that is firmware and not
  visible from the client -- but a fault that recovers within one retry should
  not take every entity down for thirty seconds. `MIN_STATUS_BYTES = 34` is now
  explicit, an all-short failure names the length it saw, and both behaviours
  are covered by tests.

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
- `warnState`: only low pellets (128, bit 7) has been seen for real. The
  other seven flag names are an inference that one other source contradicts
  (see 3.2.0).
- The `UC` config write is single-sourced (gmg-js, 2020). The byte layout
  it writes is confirmed on APIv6 by reading; each write is confirmed only by
  reading the block back afterwards, and it is refused on any other API
  version.
- A write whose new block contains `0x21` is refused. Every command ends with
  `!` (`0x21`), and whether the grill reads such a frame whole has not been
  seen. In practice that is Icy with Pizza Mode and Lock Temp Display on and
  Auto-Revert WiFi off -- and every change at all while any calibration byte
  reads 33 (probably +8 on a probe box, +13 or -17 on the grill's), since a
  write sends all eight. Setting a probe box to +8 in the GMG app and reading
  the block back would show whether the grill takes such a frame -- the app
  has to send one to make that change.
- The GMG app writes all eight bytes from its own Grill Config screen too.
  Pressing Confirm on a screen opened before a change made from Home
  Assistant puts the old value back; reopen the screen first.
- Bytes 10-15 (the calibration boxes) are exposed raw and never written: the
  byte-to-box mapping and the zero points are inferred, not confirmed.
- Whatever's in the currently-undecoded bytes (see Raw Status sensor,
  added 2.1.0) hasn't been identified. It's observable now, not decoded.
- `PowerState == 2` ("fan," per the reference project's own enum) has no
  confirmed real example in either project and isn't mapped to anything.
- Test coverage: the protocol (`tests/test_gmg_parsing.py`,
  `tests/test_gmg_config.py`, no Home Assistant needed) and the Grill Config
  and warning entities (`tests/test_config_entities.py`). The climate and
  number entities and the config flow are still barely tested.
