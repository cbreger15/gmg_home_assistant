# Changes from jwhitby91/gmg_home_assistant

Everything here was found by reading the original implementation closely
while unblocking it for a current Home Assistant install, not assumed --
see gmg.py's module docstring for the specifics on what's provably a bug
versus what's preserved on purpose.

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

## Preserved on purpose, not touched

- The actual wire-format parsing in `Grill._parse_status` (`list(raw_bytes)`,
  indexed by byte position) is exactly what the original did. It isn't
  documented anywhere independently of this project's own commit history,
  so it wasn't safe to reinterpret without a real grill to verify against --
  getting that wrong would mean silently wrong temperature readings, not
  just a code-quality issue.
- The 150°F minimum-before-setpoint-change rule, straight from the GMG
  manual per the original author's testing notes.
- The "probe disconnected at 89°F" heuristic -- still a heuristic, now
  documented and centralized in one constant instead of a magic number
  buried in a property getter.

## Known gaps, not addressed here

- Fire state is exposed as a raw numeric code. Its meaning per value isn't
  independently documented; worth mapping to friendly text once confirmed
  against a real grill.
- Test coverage doesn't yet cover the new sensor/number/binary_sensor/
  config_flow modules -- only the original bare component-setup test exists.
