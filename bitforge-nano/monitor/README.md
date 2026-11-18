# BitForge Nano monitor

Watches your Nano from another computer at home and sends alerts to your
phone. Made for a miner you can't see, such as one in the garage.

It checks the miner once a minute through its own web API. Everything runs on
your network; the only outside connections are the alerts themselves and a
lookup of the current network difficulty (mempool.space) once an hour.

## What it alerts on

| Alert | Priority | When |
|-------|----------|------|
| **BLOCK FOUND!** | urgent | The miner reports a new block, with a mempool.space link. Follow-ups when it confirms or is orphaned. |
| Payout address does not match | urgent | The main or fallback pool pays a different address than yours (needs `--expect-address`). |
| Block template does not pay you | urgent | The block the pool gave the miner pays nothing to your address (needs `--expect-address`). |
| Overheat shutdown: mining stopped | urgent | ForgeOS hit 75 °C (chips) or 105 °C (regulator) and stopped. It stays stopped until you clear overheat mode in Settings. |
| Power fault | urgent | The voltage regulator reports a fault. |
| Miner offline / back online | high | No answer for 3 minutes. The "back online" alert also says if it restarted (power cut). |
| Chips running hot | high | Hottest chip at 68 °C or more, a warning before the 75 °C shutdown. |
| Voltage regulator hot | high | Regulator at 95 °C or more. |
| Hashrate low | high | 15-minute average under 80% of what the frequency should give. |
| No accepted shares | high | Nothing accepted for 30 minutes while it should be mining. |
| No pool reachable | high | Neither pool answers, so the miner has paused. |
| Running on the fallback pool | normal | Main pool down; still mining. |
| Miner restarted | normal | It rebooted without a long outage. |
| New best share | normal | A new all-time best, and how much harder a block would have been. |
| Weak WiFi | normal | Signal at -75 dBm or worse for 5 minutes. |
| Daily report | low | 9:00 every day: average hashrate, time offline, temperatures, shares, best share. If this stops arriving, the monitor itself is down. |

Problems that last (offline, overheat, wrong address, and others) repeat every
6 hours until fixed, and you get an "OK again" message when they clear.

## What you need

- A computer at home that stays on and is on the same network as the miner:
  a Raspberry Pi (Raspberry Pi OS or Kali Linux), an old laptop, a NAS or a
  desktop. Python 3.8 or newer. Nothing else to install.
- The **ntfy** app on your phone (free, Android and iPhone). Telegram also
  works; see below.

## Setup

**1. Give the miner a fixed address.** In your router's app, reserve an IP for
the BitForge (often called "DHCP reservation"). Otherwise its address can
change and the monitor will report it offline.

**2. Pick a private topic name.** Anyone who knows an ntfy topic name can read
it, so use a random one. This prints one:

```bash
python3 -c "import secrets; print('bitforge-' + secrets.token_hex(8))"
```

In the ntfy app, tap **+** and subscribe to that exact name.

**3. Copy `nano_monitor.py` to the computer** that will run it.

**4. Check it can see the miner** (use your miner's address and your BTC address):

```bash
python3 nano_monitor.py --once --miner 192.168.1.50 --expect-address YOUR_BTC_ADDRESS
```

This prints the hashrate, temperatures, who each pool pays, and whether the
block template pays your address. Fix anything that says DOES NOT MATCH
before going on.

**5. Send a test alert to your phone:**

```bash
python3 nano_monitor.py --test --ntfy-topic YOUR_TOPIC
```

**6. Start monitoring:**

```bash
python3 nano_monitor.py --miner 192.168.1.50 --ntfy-topic YOUR_TOPIC --expect-address YOUR_BTC_ADDRESS
```

You should get "Monitoring started" right away. Leave it running.

## Keep it running after a reboot

### Raspberry Pi or Linux (systemd)

This works on Raspberry Pi OS, Kali Linux, Debian, Ubuntu and other Linux
systems that use systemd. Check three things first:

- **User name.** The example below uses `pi`, the Raspberry Pi OS default. On
  Kali Linux it is usually `kali`. Run `whoami` to see yours, and use it for
  `User=` and in the path to the script.
- **Time zone.** The daily report goes out at 9:00 on the computer's clock,
  and Kali images often come set to UTC. Run `timedatectl` to check. If it is
  wrong, find your zone with `timedatectl list-timezones` and set it with
  `sudo timedatectl set-timezone Your/Zone`.
- **Network.** The Pi must be on your home network like any normal computer
  (Ethernet or regular WiFi). On Kali, a WiFi adapter left in monitor mode or
  a VPN with a kill switch stops it from reaching the miner.

To copy the script from another computer to the Pi's home folder (use your
user name and the Pi's address):

```bash
scp nano_monitor.py kali@PI_ADDRESS:~/
```

Save this as `/etc/systemd/system/nano-monitor.service`, with your own values,
your user name and the real path to the script:

```ini
[Unit]
Description=BitForge Nano monitor
After=network-online.target
Wants=network-online.target

[Service]
ExecStart=/usr/bin/python3 /home/pi/nano_monitor.py
Environment=MINER_HOST=192.168.1.50
Environment=NTFY_TOPIC=bitforge-your-topic
Environment=PAYOUT_ADDRESS=bc1q-your-address
Restart=always
RestartSec=30
User=pi

[Install]
WantedBy=multi-user.target
```

Then turn it on:

```bash
sudo systemctl enable --now nano-monitor
```

See its log:

```bash
journalctl -u nano-monitor -f
```

### Windows

1. Open **Task Scheduler**, then **Create Task**.
2. **General:** name it "Nano monitor" and choose "Run whether user is logged on or not".
3. **Triggers:** New, then "At startup".
4. **Actions:** New. Set Program to the full path of `pythonw.exe` (run
   `where pythonw` in Command Prompt to find it) and Arguments to
   `C:\path\to\nano_monitor.py --miner 192.168.1.50 --ntfy-topic YOUR_TOPIC --expect-address YOUR_BTC_ADDRESS`.
5. **Settings:** tick "If the task fails, restart every 1 minute".
6. Turn off sleep for that computer, or the monitor stops with it.

### Mac

A MacBook works while it is at home, awake and on the same network as the
miner. Closing the lid or leaving home pauses the watching. When the Mac is
back, the monitor reports what it finds, such as an overheat shutdown still
in place or a restart that happened in the meantime.

1. Put the script in its own folder:

   ```bash
   mkdir -p ~/nano-monitor && mv ~/Downloads/nano_monitor.py ~/nano-monitor/
   ```

2. Start it through `caffeinate`, which keeps the Mac from sleeping while the
   monitor runs:

   ```bash
   caffeinate -is python3 ~/nano-monitor/nano_monitor.py --miner 192.168.1.50 --ntfy-topic YOUR_TOPIC --expect-address YOUR_BTC_ADDRESS
   ```

   Leave that Terminal window open, the Mac plugged in and the lid open. The
   screen can turn off.

3. Before taking the MacBook out of the house, press Ctrl+C in that window.
   Otherwise you get a "Miner offline" alert, because the Mac can't reach the
   miner from another network.

If something doesn't work:

- **`python3` asks to install the command line developer tools:** click
  Install, then run the command again.
- **The miner's page opens in your browser, but `--once` says "No answer":**
  open System Settings > Privacy & Security > Local Network and allow
  Terminal (or iTerm) if it is listed.
- **`--test` fails with `CERTIFICATE_VERIFY_FAILED`:** your Python came from
  python.org. Open its folder in Applications and double-click
  "Install Certificates.command".

## Telegram instead of (or as well as) ntfy

1. In Telegram, message **@BotFather**, send `/newbot` and copy the token it gives you.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and copy the `chat` `id`.
4. Add `--telegram-token <TOKEN> --telegram-chat <ID>` to the commands above,
   or set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the service file.

## Options

| Option | Default | |
|--------|---------|---|
| `--miner` | (env `MINER_HOST`) | Miner address. |
| `--ntfy-topic` | (env `NTFY_TOPIC`) | Topic to publish to. |
| `--ntfy-server` | `https://ntfy.sh` | Your own ntfy server, if you run one. |
| `--expect-address` | (env `PAYOUT_ADDRESS`) | Your BTC address, for the payout checks. |
| `--temp-warn` | 68 | Chip alert in °C. |
| `--vr-warn` | 95 | Regulator alert in °C. |
| `--daily-hour` | 9 | Local hour for the daily report; `-1` turns it off. |
| `--no-best-share-alerts` | off | Skip the new-best-share alerts. |
| `--interval` | 60 | Seconds between checks (15 minimum). |
| `--state-file` | next to the script | Remembers the best share and blocks between runs. |
| `-v` | off | Log every check. |

## Summer

In winter the garage keeps the chips cool. When it warms up, a "Chips running
hot" alert is the sign to step down from Max to Power or Medium, before the
75 °C shutdown stops mining.
