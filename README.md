# Green Mountain Grill for Home Assistant

Fork of [jwhitby91/gmg_home_assistant](https://github.com/jwhitby91/gmg_home_assistant), rewritten to fix a few real bugs found while unblocking it for a current Home Assistant install, and to model probes properly instead of as fake thermostats. See [CHANGES.md](CHANGES.md) for the full list of what changed and why.

If `jwhitby91/gmg_home_assistant` ever ships its own current release, prefer that one -- this fork exists to be usable in the meantime, not to replace it.

## Installation

Via HACS:

1. HACS → ⋮ (top right) → Custom repositories
2. Add `https://github.com/cbreger15/gmg_home_assistant` as an Integration
3. Install, then restart Home Assistant

## Setup

**Settings → Devices & Services → Add Integration → Green Mountain Grill.** The integration broadcasts on the local network and lists whatever it finds -- pick your grill. No YAML required.

Requires UDP port 8080 to be reachable between Home Assistant and the grill.

## What you get

One device per grill, with:

- **Climate entity** -- the grill itself: on/off, cold-smoke mode, target temperature (the controller won't accept a new setpoint below 150°F, same restriction as the GMG app itself)
- **Probe 1 / Probe 2 temperature** (sensor) -- current reading, unavailable rather than a misleading number when nothing's plugged in
- **Probe 1 / Probe 2 target temperature** (number) -- the alarm/target temp, settable directly, meant to be wired into an automation for "notify me when the probe hits temp" rather than treated as a pretend thermostat
- **Probe 1 / Probe 2 connected** (binary sensor) -- whether a probe is actually plugged in
- **Warning** (binary sensor) -- the grill's own warning state
- **Fire state / Fire state percentage** (diagnostic sensors) -- fire state shows a friendly name (e.g. `off`, `cold_smoke`) where one is confirmed
- **Raw Status** (diagnostic sensor, disabled by default) -- every byte of the last response, for anyone digging further into what the protocol has left undecoded

Temperature readings above 255°F, and cold-smoke mode detection, were both fixed in 3.0.0 after cross-checking against an independent reverse-engineering of this same protocol with real captured test data -- see [CHANGES.md](CHANGES.md) for the full verification writeup, including a runnable test (`tests/test_gmg_parsing.py`) that proves it against that real data rather than just asserting it.

## Known limitations

- Fire state's friendly names: only `off` and `cold_smoke` are independently confirmed; the rest are plausible, not certain (detail in CHANGES.md).
- `warnState` is read as a combined 4-byte value now, consistent with everything else that's been verified, but hasn't itself been checked against a real non-zero warning.
- The "probe connected" check is a heuristic (a probe reading outside its own physical range) -- there's no dedicated connected/disconnected flag in the protocol, but this is now a principled range check rather than a hardcoded magic number.
- Cold-smoke mode is wired up and its status detection is now confirmed against real data, but the actual cooking behavior in that mode isn't extensively tested.
