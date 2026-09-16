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

- **Climate entity** -- the grill itself: on/off, cold-smoke mode, target temperature (the controller won't accept a new setpoint below 150°F, same restriction as the GMG app itself). It also shows what the grill is doing: `preheating` while the fire lights, `heating` once it's running, `fan` during the cooldown after power-off (and in cold smoke), `off`, and `idle` if it's on with any other fire state. Turning it off sends the grill's own power-off, which starts that cooldown. (The HomeKit bridge shows `fan` as Cooling.)
- **Probe 1 / Probe 2 temperature** (sensor) -- current reading, unavailable rather than a misleading number when nothing's plugged in
- **Probe 1 / Probe 2 target temperature** (number) -- the alarm/target temp, settable directly, meant to be wired into an automation for "notify me when the probe hits temp" rather than treated as a pretend thermostat
- **Probe 1 Estimated Finish Time** (timestamp sensor) -- when probe 1 should reach its target, projected from its last 20 minutes of readings. It's `unknown` until there are 5 minutes of readings, during a stall or a drop, when the rise is too slow to measure in whole degrees (under about 4.5°F an hour), and when the finish is more than a day away. It's `unavailable` when the grill is off or cooling down, the probe is unplugged or no target is set. The `rate_f_per_hour` attribute shows how fast probe 1 is rising.
- **Probe 1 / Probe 2 connected** (binary sensor) -- whether a probe is actually plugged in
- **Fire Active** (diagnostic binary sensor) -- on while the fire is lighting or running
- **Cooldown Fan** (diagnostic binary sensor) -- on during the fan cooldown after power-off, about 16 minutes
- **Warning** (binary sensor) -- the grill's own warning state, with the active warnings listed in its `warnings` attribute (e.g. `low_pellet`)
- **Pizza Mode** (switch) -- the Pizza Mode setting from the GMG app's Grill Config screen
- **Climate Setting** (select: Icy / Cold / Average / Warm / Hot), **Auto-Revert WiFi** and **Lock Temp Display** (switches) -- the rest of the Grill Config screen, as configuration entities
- **Config Block** (diagnostic sensor) -- the Grill Config block as hex, with the temperature calibration boxes as attributes, as the app shows them (e.g. `probe_1_adjustment_32f: -8`)
- **Fire state / Fire state percentage** (diagnostic sensors) -- fire state shows a friendly name (`off`, `startup`, `running`, `cooldown`, `cold_smoke`)
- **Raw Status** (diagnostic sensor, disabled by default) -- every byte of the last response, for anyone digging further into what the protocol has left undecoded
- **Firmware version** -- shown on the device page (`sw_version`), fetched once at setup

Changing a Grill Config setting reads the grill's current settings (twice, and they must agree), changes only that one, and then waits for the grill to report the change. If the grill doesn't, the toggle shows an error; nothing is retried behind your back. The Grill Config controls only work on grills that report API version 6 (the "APIv6" in the GMG app's header), the only version this has been worked out on; elsewhere they show as unavailable.

The GMG app sends all of those settings together when you press Confirm on its Grill Config screen. If that screen was opened before a change made from Home Assistant, Confirm puts the old value back -- reopen the screen first.

Temperature readings above 255°F, and cold-smoke mode detection, were both fixed in 3.0.0 after cross-checking against an independent reverse-engineering of this same protocol with real captured test data -- see [CHANGES.md](CHANGES.md) for the full verification writeup, including a runnable test (`tests/test_gmg_parsing.py`) that proves it against that real data rather than just asserting it. CI runs the protocol and finish-time tests, and the Home Assistant tests (the entities, and the cooking automations below) against Home Assistant 2026.9, on every pull request and every push to main.

## Cooking automations

[docs/automations.md](docs/automations.md) has five automations to paste into `automations.yaml`. They aren't part of the integration.

- **Stall monitor** -- tells you when probe 1 has plateaued. It needs a Derivative helper; the page shows how to set it up.
- **Hold, then shut down** -- when probe 1 reaches its target, drops the grill to 150°F, then turns it off 45 minutes later unless you've changed the setpoint. One hold per cook.
- **Flameout** -- turns the grill off if it has reported no fire for a minute, and fallen below 130°F, while set to heat. A real flameout more likely shows as the grill trying to relight, which the fire-not-lighting alert watches for.
- **Possible grease fire** -- while set to heat and above 400°F, a jump of more than 40°F between two readings no more than 60 seconds apart turns the grill off and sends a critical alert.
- **Fire not lighting** -- a critical alert when the grill has been trying to light for 30 minutes and is still below 130°F: an ignition that failed, or a relight that isn't taking. It only alerts, and the grill keeps trying.

Every shutdown is checked. If the grill doesn't read as off within 90 seconds, the automation sends the command again and tells you. The temperatures are all °F, so the automations need Home Assistant's US customary unit system.

They're a backstop, not a safety system. Home Assistant reads the grill every 30 seconds, so stay within reach of a lit grill. The page lists what each automation can't catch.

## Known limitations

- The grill doesn't report what its auger, fan or igniter are doing, so there are no entities for them (CHANGES.md, 3.3.0). Fire Active and Cooldown Fan are worked out from the grill's power and fire states.
- Fire state's friendly names: `default` and `fail` have never been seen on a real grill; the other five have (detail in CHANGES.md).
- The finish time is for probe 1 only, and it's a straight line: it can't see a stall coming. It needs more than a degree of movement to go on, so a rise under about 4.5°F an hour shows no finish at all.
- Warnings: only `low_pellet` has been confirmed against a real grill. The other warning names are a best reading of the sources and may be wrong.
- The Grill Config write (`UC`) comes from a single 2020 source and has been confirmed on one grill, a Jim Bowie on APIv6 (September 2026). Each write is checked by reading the settings back.
- The temperature calibration boxes are shown, but can't be changed from Home Assistant yet.
- On grills that send the older, shorter status reply, the Grill Config entities stay unavailable.
- The "probe connected" check is a heuristic (a probe reading outside its own physical range) -- there's no dedicated connected/disconnected flag in the protocol, but this is now a principled range check rather than a hardcoded magic number.
- Cold-smoke mode is wired up and its status detection is now confirmed against real data, but the actual cooking behavior in that mode isn't extensively tested.
