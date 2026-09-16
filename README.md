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
- **Warning** (binary sensor) -- the grill's own warning state, with the active warnings listed in its `warnings` attribute (e.g. `low_pellet`)
- **Pizza Mode** (switch) -- the Pizza Mode setting from the GMG app's Grill Config screen
- **Climate Setting** (select: Icy / Cold / Average / Warm / Hot), **Auto-Revert WiFi** and **Lock Temp Display** (switches) -- the rest of the Grill Config screen, as configuration entities
- **Config Block** (diagnostic sensor) -- the Grill Config block as hex, with the temperature calibration boxes as raw attributes
- **Fire state / Fire state percentage** (diagnostic sensors) -- fire state shows a friendly name (e.g. `off`, `cold_smoke`) where one is confirmed
- **Raw Status** (diagnostic sensor, disabled by default) -- every byte of the last response, for anyone digging further into what the protocol has left undecoded
- **Firmware version** -- shown on the device page (`sw_version`), fetched once at setup

Changing a Grill Config setting reads the grill's current settings first, changes only that one, and then waits for the grill to report the change. If the grill doesn't, the toggle shows an error; nothing is retried behind your back. Settings are only written to grills that report API version 6 (the "APIv6" in the GMG app's header), the only version this has been worked out on.

Temperature readings above 255°F, and cold-smoke mode detection, were both fixed in 3.0.0 after cross-checking against an independent reverse-engineering of this same protocol with real captured test data -- see [CHANGES.md](CHANGES.md) for the full verification writeup, including a runnable test (`tests/test_gmg_parsing.py`) that proves it against that real data rather than just asserting it. CI runs the protocol tests on every push, and the Home Assistant entity tests against Home Assistant 2026.9.

## Known limitations

- Fire state's friendly names: only `off` and `cold_smoke` are independently confirmed; the rest are plausible, not certain (detail in CHANGES.md).
- Warnings: only `low_pellet` has been confirmed against a real grill. The other warning names are a best reading of the sources and may be wrong.
- The Grill Config write (`UC`) comes from a single 2020 source. Each write is checked by reading the settings back, but treat it as new.
- The temperature calibration boxes are shown raw and can't be changed from Home Assistant yet.
- On grills that send the older, shorter status reply, the Grill Config entities stay unavailable.
- The "probe connected" check is a heuristic (a probe reading outside its own physical range) -- there's no dedicated connected/disconnected flag in the protocol, but this is now a principled range check rather than a hardcoded magic number.
- Cold-smoke mode is wired up and its status detection is now confirmed against real data, but the actual cooking behavior in that mode isn't extensively tested.
