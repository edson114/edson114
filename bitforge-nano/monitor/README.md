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
  a Raspberry Pi, an old laptop, a NAS or a desktop. Python 3.8 or newer.
  Nothing else to install.
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

Save this as `/etc/systemd/system/nano-monitor.service`, with your own values
and the real path to the script:

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

Run the step 6 command in Terminal and leave the window open. Set the Mac to
never sleep while plugged in (System Settings > Battery or Energy).

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
