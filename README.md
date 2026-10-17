# Keto-Mojo GK+ Meter for Home Assistant

A custom integration that pulls **blood glucose and ketone readings** from a
Keto-Mojo GK+ meter straight into Home Assistant over Bluetooth, and works out
your **GKI** (Glucose Ketone Index). You don't need the phone app.

> **Not a medical device.** This is a hobby project for personal tracking only.
> Don't use it for diagnosis or treatment decisions. Talk to your doctor.
>
> Not affiliated with or endorsed by Keto-Mojo.

## Features

- **Syncs automatically.** Switch the meter on near a Bluetooth adapter and its
  readings are pulled within seconds. There's also a **Sync now** button.
- **Set up automatically.** Home Assistant discovers the meter by its Bluetooth
  name, so there's nothing to type.
- **GKI.** Uses the meter's own GKI-mode pairs, and also pairs a separate
  glucose and ketone test taken within 30 minutes of each other.
- **Full history.** Every reading is stored once (no duplicates) and back-filled
  into long-term statistics at the time it was measured, so graphs line up
  with when you actually tested.
- Control-solution tests are recognised and never shown as your glucose or ketone value.

## Entities

| Entity | What it shows |
|---|---|
| `sensor.keto_mojo_glucose` | Latest glucose (mg/dL or mmol/L, matching the meter) |
| `sensor.keto_mojo_ketone` | Latest blood ketone (mmol/L) |
| `sensor.keto_mojo_gki` | Latest GKI, with the glucose/ketone it came from and how it was paired |
| `sensor.keto_mojo_last_reading` | Time of the newest reading, and the total count |
| `sensor.keto_mojo_last_sync` | Time of the last successful sync, and the last error if any |
| `button.keto_mojo_sync_now` | Sync immediately |

Long-term statistics for graphs: `keto_mojo:glucose`, `keto_mojo:ketone` and
`keto_mojo:gki`. Use them with a **Statistics graph** card.

## Requirements

- A **connectable** Bluetooth adapter in Home Assistant: the host's built-in
  Bluetooth (for example a Raspberry Pi) or an active ESPHome Bluetooth proxy.
- The meter within range when you switch it on.
- **Close the Keto-Mojo phone app while syncing.** The meter only accepts one
  connection at a time. Using the app at other times is fine, because it
  doesn't erase readings.

## Installation

### HACS
1. In HACS, open the menu → **Custom repositories**.
2. Add `https://github.com/gjg4000-debug/ha-keto-mojo`, category **Integration**.
3. Install **Keto-Mojo GK+ Meter**, then restart Home Assistant.

### Manual
Copy `custom_components/keto_mojo` into your `config/custom_components/`
folder and restart Home Assistant.

## Setup

Switch the meter on near your Bluetooth adapter. Home Assistant shows a
**Discovered: Keto-Mojo** notice under **Settings → Devices & services**.
Click **Configure**, and you're done.

If nothing is discovered, go to **Add integration → Keto-Mojo GK+ Meter**
while the meter is on and pick it from the list.

## GKI ranges (commonly quoted)

| GKI | Meaning |
|---|---|
| over 9 | not in ketosis |
| 6–9 | low ketosis |
| 3–6 | moderate ketosis |
| 1–3 | high ketosis |
| under 1 | very deep |

GKI = glucose (mmol/L) ÷ ketone (mmol/L). Glucose in mg/dL is divided by 18
first, and the result is rounded down to 0.1, like the official app.

## Tested on

- Keto-Mojo **GK+**, firmware `SW2071323011A001`, meter set to mg/dL.

Not tested yet: meters set to mmol/L, other Keto-Mojo models (GKI, classic
Bluetooth meter) and very large memories. If you try one of these, please open
an issue, whether it works or not.

## How it works

The GK+ is made by VivaChek and uses a proprietary BLE service
(`0003cdd0-0000-1000-8000-00805f9b0131`). The integration writes fixed
command frames to `cdd2` and reads replies from `cdd1`. These commands ask for
the unit, the serial number and all stored records, and it decodes each 9-byte
record (date and time, value, meal flag and type). The protocol was worked
out independently, for interoperability with hardware the user owns. No
vendor code is included.

## License

MIT
