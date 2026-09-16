# Cooking automations

Four automations for a long cook, ready to paste into `automations.yaml`:

1. **Stall monitor** -- tells you when the brisket has plateaued and is ready to wrap.
2. **Hold, then shut down** -- when probe 1 reaches its target, holds at 150°F for 45 minutes, then turns the grill off unless someone has taken over.
3. **Flameout** -- turns the grill off if the fire dies mid-cook.
4. **Possible grease fire** -- turns the grill off and sends a critical alert on a sudden temperature spike above 400°F.

They use the entity IDs from the author's grill. Yours follow your device's name and area:
check **Settings → Devices & services → Entities** and search for your grill.

| What | Entity ID below |
| --- | --- |
| The grill | `climate.green_mountain_grill_gmg12272191` |
| Probe 1 temperature | `sensor.green_mountain_grill_gmg12272191_probe_1_temperature` |
| Probe 1 target | `number.green_mountain_grill_gmg12272191_probe_1_target_temperature` |
| Fire Active (3.3.0) | `binary_sensor.patio_gmg_smoker_fire_active` |
| Probe 1 Estimated Finish Time (3.3.0) | `sensor.patio_gmg_smoker_probe_1_estimated_finish_time` |
| Probe 1 rate (the helper below) | `sensor.gmg_probe_1_rate` |
| Your phone | `notify.mobile_app_chris_iphone` |

The syntax is Home Assistant 2024.10 or later (`triggers:` / `actions:`).

## Before you start: the probe 1 rate helper

The stall monitor needs probe 1's rate of rise in °F per hour. Create it once:

1. **Settings → Devices & services → Helpers → Create helper → Derivative sensor**
2. Fill in:
   - **Name:** `GMG Probe 1 Rate` (this makes `sensor.gmg_probe_1_rate`)
   - **Input sensor:** `sensor.green_mountain_grill_gmg12272191_probe_1_temperature`
   - **Precision:** `1`
   - **Time window:** `00:15:00`
   - **Metric prefix:** none
   - **Time unit:** Hours
   - **Max sub-interval:** `00:01:00`

   With the time window set, the rate is averaged over the last 15 minutes. The max sub-interval makes the helper recalculate at least once a minute, so a long flat stretch still pulls the rate down.

## The automations

```yaml
# 1. STALL MONITOR
- id: gmg_brisket_stall
  alias: "GMG: Probe 1 has stalled"
  description: >-
    Probe 1 is past 145°F and has risen less than 2°F an hour for 15 minutes:
    the stall. Wrapping pushes through it.
  mode: single
  triggers:
    - trigger: numeric_state
      entity_id: sensor.gmg_probe_1_rate
      below: 2
      for: "00:15:00"
  conditions:
    - condition: state
      entity_id: climate.green_mountain_grill_gmg12272191
      state: heat
    - condition: numeric_state
      entity_id: sensor.green_mountain_grill_gmg12272191_probe_1_temperature
      above: 145
  actions:
    - action: logbook.log
      data:
        name: GMG smoker
        entity_id: sensor.green_mountain_grill_gmg12272191_probe_1_temperature
        message: >-
          stalled at
          {{ states('sensor.green_mountain_grill_gmg12272191_probe_1_temperature') }}°F,
          rising {{ states('sensor.gmg_probe_1_rate') }}°F/h
    - action: notify.mobile_app_chris_iphone
      data:
        title: "Brisket has stalled"
        message: >-
          Probe 1 is holding at
          {{ states('sensor.green_mountain_grill_gmg12272191_probe_1_temperature') }}°F
          and rising only {{ states('sensor.gmg_probe_1_rate') }}°F an hour.
          It's ready to wrap.
        data:
          push:
            sound:
              name: default
          tag: gmg_stall

# 2. HOLD, THEN SHUT DOWN
- id: gmg_probe_1_hold_then_shutdown
  alias: "GMG: Probe 1 done - hold at 150°F, then shut down"
  description: >-
    When probe 1 reaches its target, drop the grill to its 150°F floor. If
    it is still heating at exactly 150°F 45 minutes later, nobody has
    stepped in, so turn it off.
  mode: single
  triggers:
    - trigger: template
      value_template: >-
        {% set temp = states('sensor.green_mountain_grill_gmg12272191_probe_1_temperature') | float(0) %}
        {% set target = states('number.green_mountain_grill_gmg12272191_probe_1_target_temperature') | float(0) %}
        {{ target > 0 and temp >= target }}
  conditions:
    - condition: state
      entity_id: climate.green_mountain_grill_gmg12272191
      state: heat
  actions:
    - alias: "Phase 1: drop to the 150°F holding floor"
      action: climate.set_temperature
      target:
        entity_id: climate.green_mountain_grill_gmg12272191
      data:
        temperature: 150
    - action: logbook.log
      data:
        name: GMG smoker
        entity_id: climate.green_mountain_grill_gmg12272191
        message: "probe 1 reached its target; holding at 150°F for 45 minutes"
    - action: notify.mobile_app_chris_iphone
      data:
        title: "Probe 1 is done - holding at 150°F"
        message: >-
          Probe 1 reached
          {{ states('sensor.green_mountain_grill_gmg12272191_probe_1_temperature') }}°F
          (target {{ states('number.green_mountain_grill_gmg12272191_probe_1_target_temperature') }}°F).
          The grill is holding at 150°F and turns off in 45 minutes unless you
          change the setpoint.
        data:
          push:
            sound:
              name: default
          tag: gmg_hold
    - alias: "Phase 2: hold for 45 minutes"
      delay: "00:45:00"
    - alias: "Phase 3: shut down, unless someone took over"
      if:
        - condition: state
          entity_id: climate.green_mountain_grill_gmg12272191
          state: heat
        - condition: template
          value_template: >-
            {{ state_attr('climate.green_mountain_grill_gmg12272191', 'temperature') | float(0) == 150 }}
      then:
        - action: climate.turn_off
          target:
            entity_id: climate.green_mountain_grill_gmg12272191
        - action: logbook.log
          data:
            name: GMG smoker
            entity_id: climate.green_mountain_grill_gmg12272191
            message: "held 45 minutes with no change; turned off (fan cooldown)"
        - action: notify.mobile_app_chris_iphone
          data:
            title: "Grill shut down after the hold"
            message: >-
              45 minutes at 150°F with no change, so the grill has been turned
              off. Its fan runs for about 15 minutes to burn out the firepot.
            data:
              push:
                interruption-level: time-sensitive
                sound:
                  name: default
              tag: gmg_hold
      else:
        - action: logbook.log
          data:
            name: GMG smoker
            entity_id: climate.green_mountain_grill_gmg12272191
            message: "the hold was changed by hand; leaving the grill as it is"

# 3. FLAMEOUT
- id: gmg_flameout
  alias: "GMG: Flameout - shut down"
  description: >-
    For a full minute the fire has been out and the grill has been below
    130°F, while it is set to heat. Turn it off so the auger stops feeding
    pellets into a dead firepot.
  mode: single
  triggers:
    - trigger: template
      value_template: >-
        {{ is_state('binary_sensor.patio_gmg_smoker_fire_active', 'off')
           and state_attr('climate.green_mountain_grill_gmg12272191', 'current_temperature') | float(999) < 130 }}
      for: "00:01:00"
  conditions:
    - condition: state
      entity_id: climate.green_mountain_grill_gmg12272191
      state: heat
  actions:
    - action: climate.turn_off
      target:
        entity_id: climate.green_mountain_grill_gmg12272191
    - action: logbook.log
      data:
        name: GMG smoker
        entity_id: climate.green_mountain_grill_gmg12272191
        message: >-
          flameout at
          {{ state_attr('climate.green_mountain_grill_gmg12272191', 'current_temperature') }}°F;
          turned off
    - action: notify.mobile_app_chris_iphone
      data:
        title: "GMG ALARM: flameout"
        message: >-
          The fire is out while the grill was set to heat, and it has cooled to
          {{ state_attr('climate.green_mountain_grill_gmg12272191', 'current_temperature') }}°F.
          The grill has been turned off so it stops feeding pellets. Let it cool
          and clear the firepot before relighting.
        data:
          push:
            sound:
              name: default
              critical: 1
              volume: 1.0
          tag: gmg_flameout

# 4. POSSIBLE GREASE FIRE
- id: gmg_grease_fire
  alias: "GMG: Possible grease fire - shut down"
  description: >-
    The grill was already above 400°F and jumped more than 40°F since the
    previous poll (at most 60 seconds earlier). GMG's manual says to turn the
    grill off, keep the lid closed and leave it plugged in.
  mode: single
  triggers:
    - trigger: state
      entity_id: climate.green_mountain_grill_gmg12272191
      attribute: current_temperature
  conditions:
    - condition: template
      value_template: >-
        {% set old = trigger.from_state %}
        {% set new = trigger.to_state %}
        {% if old is none or new is none %}
          false
        {% else %}
          {% set before = old.attributes.get('current_temperature') | float(0) %}
          {% set after = new.attributes.get('current_temperature') | float(0) %}
          {% set seconds = (new.last_updated - old.last_reported).total_seconds() %}
          {{ before > 400 and after - before > 40 and seconds <= 60 }}
        {% endif %}
    - condition: state
      entity_id: climate.green_mountain_grill_gmg12272191
      state: heat
  actions:
    - action: climate.turn_off
      target:
        entity_id: climate.green_mountain_grill_gmg12272191
    - action: logbook.log
      data:
        name: GMG smoker
        entity_id: climate.green_mountain_grill_gmg12272191
        message: >-
          possible grease fire: {{ trigger.from_state.attributes.current_temperature }}°F to
          {{ trigger.to_state.attributes.current_temperature }}°F; turned off
    - action: notify.mobile_app_chris_iphone
      data:
        title: "CRITICAL: possible grease fire"
        message: >-
          CRITICAL ALERT: Potential grease fire detected! Smoker has been
          commanded OFF to initiate safe shutdown. DO NOT open the lid, DO NOT
          unplug the grill. Keep lid closed to starve oxygen. Call emergency
          services if uncontrolled.
        data:
          push:
            sound:
              name: default
              critical: 1
              volume: 1.0
          tag: gmg_grease_fire
```

## What these can and cannot do

- **Home Assistant reads the grill every 30 seconds, over Wi-Fi that sometimes drops a reply.** These automations are a backstop, not a safety system. Stay within reach of a lit grill.
- **Turning the grill off does not cut its air.** GMG's power-off starts a fan cooldown of about 15 minutes, and this integration shows it as `Cooldown Fan` and the grill's `fan` state. Turning the grill off is still what GMG's manual says to do in a grease fire:
  - keep the lid closed until the fire is out;
  - don't unplug the grill;
  - call the fire department if the fire isn't going out;
  - never use water.
- **The grease-fire check compares two consecutive polls, and only while the grill is set to heat.** It misses:
  - a spike spread across two polls, with neither step above 40°F;
  - a flare-up during the cooldown after power-off.

  Before 3.2.0, a reply missing its first bytes could fake a sudden jump. Such replies are no longer read.
- **The flameout check can only act while the grill still reports heat.** Fire state 5 (`fail`) has never been seen, so it's unknown what the grill reports after it shuts itself down (display code `FAL`). If it reports itself as off, this automation stays quiet.
- **The grill cannot report what its auger, fan or igniter are doing.** Its status reply has no bits for them, so none of these automations can use them.
- **Probe 1 Estimated Finish Time** projects the last 20 minutes of probe 1 readings to its target. It is:
  - `unknown` during a stall or a drop, once the target is reached, or with fewer than 5 minutes of readings;
  - `unavailable` when the grill isn't cooking, the probe is unplugged, or no target is set.

  So it has nothing to add to the stall and hold notifications above. To show it somewhere else, such as a dashboard card or a regular update, give it a fallback: `| as_datetime | as_local` fails when the sensor is `unknown`. For example:

  ```jinja
  {% set finish = states('sensor.patio_gmg_smoker_probe_1_estimated_finish_time') | as_datetime(none) %}
  {{ 'done around ' ~ (finish | as_timestamp | timestamp_custom('%H:%M')) if finish else 'no finish time yet' }}
  ```
