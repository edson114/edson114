# BitForge Nano: "Max" performance preset

ForgeOS (the BitForge Nano firmware) built with a fourth preset on the
Settings page, next to Eco, Medium and Power.

| Preset | Frequency | Core voltage | Expected hashrate (2 × BM1370) |
|--------|-----------|--------------|--------------------------------|
| Eco    | 430 MHz   | 1000 mV      | ~1.75 TH/s |
| Medium | 525 MHz   | 1150 mV      | ~2.14 TH/s (factory) |
| Power  | 610 MHz   | 1200 mV      | ~2.49 TH/s |
| **Max**| **650 MHz** | **1225 mV** | **~2.65 TH/s** |

Hashrate is the firmware's own formula: frequency × 2040 cores × 2 chips.

## Why not higher

- Both chips share one 1.2 V regulator (TPS546). It warns at 38 A and shuts
  off at 40 A. Max is estimated at about 33–35 A, which leaves a margin.
- If either chip passes 75 °C, ForgeOS drops to 50 MHz and stops mining until
  you clear overheat mode by hand. A preset that runs too hot means zero
  hashes, not more.
- The automatic fan only reaches 100% at 75 °C, the same point where that
  shutdown happens. On Max, keep chip temps around 68 °C or lower: use a
  second fan, a cool spot, or set the fan to 100% manually.

## Install (over WiFi, no cable)

Your WiFi, pool and payout-address settings are kept.

1. Open the miner's web page, go to **Settings**, then **Install firmware from a file**.
2. Under **Firmware**, choose `firmware/bitforgeos.bin`. Wait for the restart.
3. Under **Web interface**, choose `firmware/www.bin`. The page reloads.
4. On Settings → Performance, click **Max**. It saves immediately.
5. Watch the chip temps on the dashboard for the first hour.

The presets only show while overclock mode is off. If you unlocked it with
`?oc`, turn it off to see the preset buttons again.

If the Max preset stops showing, check whether **Install from GitHub** was
used: it replaces this build with the official release. Install these two
files again to get it back.

## What's in this folder

- `max-preset.patch`: the source change (two UI files), against
  [WantClue/forge-os](https://github.com/WantClue/forge-os) commit `30c64e9`.
- `firmware/bitforgeos.bin` and `firmware/www.bin`: built with ESP-IDF v6.0.2
  for the ESP32-S3, from that commit plus the patch.
- `firmware/SHA256SUMS`: checksums for both files.

## Rebuild from source

```bash
git clone --recursive https://github.com/WantClue/forge-os.git
```

```bash
cd forge-os && git checkout 30c64e9 && git submodule update --init --recursive
```

```bash
git apply ../max-preset.patch
```

```bash
(cd main/http_server/forge-os && npm ci && npm run build)
```

```bash
GITHUB_ACTIONS=true idf.py set-target esp32s3 build
```

The outputs are `build/bitforgeos.bin` and `build/www.bin`. Run
`./merge_bin.sh ./bitforgeos-merged.bin` for a single image to flash over USB
with `bitaxetool`.
