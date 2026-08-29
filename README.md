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
- **Fire state / Fire state percentage** (diagnostic sensors)

## Known limitations

- Fire state is exposed as a raw numeric code -- the meaning of each value isn't independently documented anywhere, so it isn't translated to friendly text here. Worth mapping once someone's confirmed what the values actually mean against a real grill.
- Cold-smoke mode is wired up but not extensively tested.
- The "probe connected" check is a heuristic (a specific raw temperature value the grill reports for an empty probe jack) -- there's no dedicated connected/disconnected flag in the protocol.
