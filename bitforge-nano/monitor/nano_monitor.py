#!/usr/bin/env python3
"""BitForge Nano monitor: phone alerts for a solo miner you can't see.

Polls the miner's ForgeOS API (http://<miner>/api/system/info) once a minute
and sends a push notification (ntfy and/or Telegram) when something needs
attention:

  - offline / back online, and unexpected restarts
  - overheat shutdown (mining stays stopped until cleared by hand)
  - chip or voltage-regulator temperature close to the shutdown limits
  - voltage-regulator power fault
  - no pool reachable, running on the fallback pool, no accepted shares
  - hashrate well below what the frequency setting should give
  - weak WiFi
  - payout address on the miner not matching yours
  - new all-time best share, and BLOCK FOUND
  - a short daily report, so silence means "still fine"

Standard library only. Runs on any computer at home that stays on
(Raspberry Pi, old laptop, NAS, desktop) with Python 3.8 or newer.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

log = logging.getLogger("nano_monitor")

USER_AGENT = "nano-monitor/1.0"
PRIORITY = {"min": 1, "low": 2, "default": 3, "high": 4, "urgent": 5}

# Firmware limits (ForgeOS main/tasks/power_management_task.c): above these
# the miner drops to 50 MHz and stops until overheat mode is cleared by hand.
CHIP_SHUTDOWN_C = 75
VR_SHUTDOWN_C = 105

PRESETS = {
    (430, 1000): "Eco",
    (525, 1150): "Medium",
    (610, 1200): "Power",
    (650, 1225): "Max",
}


@dataclass
class Alert:
    title: str
    message: str
    priority: str = "default"
    tags: List[str] = field(default_factory=list)


@dataclass
class Config:
    interval: int = 60                  # seconds between polls
    offline_after: int = 3              # failed polls in a row before "offline"
    temp_warn: float = 68.0             # chip °C; the firmware stops at 75
    vr_warn: float = 95.0               # regulator °C; the firmware stops at 105
    low_hashrate_ratio: float = 0.80    # alert below this share of expected
    low_hashrate_minutes: int = 15
    stall_minutes: int = 30             # no accepted shares for this long
    rssi_warn: int = -75                # dBm
    remind_hours: float = 6.0           # repeat for problems that persist
    daily_hour: Optional[int] = 9       # local hour for the daily report; None = off
    expect_address: Optional[str] = None
    best_share_alerts: bool = True


# --------------------------------------------------------------------------
# Formatting


def format_diff(value: float) -> str:
    for unit, size in (("E", 1e18), ("P", 1e15), ("T", 1e12), ("G", 1e9), ("M", 1e6), ("K", 1e3)):
        if value >= size:
            return f"{value / size:.2f} {unit}"
    return f"{value:.0f}"


def format_duration(seconds: float) -> str:
    seconds = int(max(0, seconds))
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return "under a minute"


def format_ths(gh_per_s: float) -> str:
    return f"{gh_per_s / 1000:.2f} TH/s"


def short_address(addr: str) -> str:
    if not addr:
        return "(empty)"
    return addr if len(addr) <= 16 else f"{addr[:8]}...{addr[-6:]}"


def _num(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def chip_temps(info: dict) -> List[float]:
    temps = [_num(info.get("chiptemp1")), _num(info.get("chiptemp2"))]
    # The firmware reports 0 or less when a sensor reading is invalid.
    return [t for t in temps if t is not None and t > 0]


def expected_gh(info: dict) -> Optional[float]:
    """Hashrate the firmware's own formula gives for the current frequency."""
    freq = _num(info.get("frequency"))
    cores = _num(info.get("smallCoreCount")) or 2040.0
    asics = _num(info.get("asicCount")) or 2.0
    if not freq:
        return None
    return freq * cores * asics / 1000.0


def template_outputs(info: dict) -> List[tuple]:
    """(address, satoshis) for each paying output of the current block template."""
    coinbase = info.get("coinbase")
    if not isinstance(coinbase, dict) or not isinstance(coinbase.get("outputs"), list):
        return []
    result = []
    for out in coinbase["outputs"]:
        if not isinstance(out, dict):
            continue
        value = _num(out.get("value")) or 0.0
        address = str(out.get("address") or "").strip()
        if value > 0 and address:
            result.append((address, value))
    return result


def preset_name(info: dict) -> str:
    key = (info.get("frequency"), info.get("coreVoltage"))
    return PRESETS.get(key, "custom")


def status_line(info: dict) -> str:
    chips = chip_temps(info)
    chip_txt = "/".join(f"{t:.0f}" for t in chips) if chips else "?"
    parts = [
        f"{info.get('hostname') or 'miner'}:",
        format_ths(_num(info.get("hashRate")) or 0.0) + ",",
        f"chips {chip_txt}°C, VR {(_num(info.get('vrTemp')) or 0):.0f}°C,",
        f"{info.get('frequency')} MHz/{info.get('coreVoltage')} mV ({preset_name(info)}),",
        f"fan {info.get('fanSpeed', '?')}%,",
        f"WiFi {info.get('wifiRSSI', '?')} dBm,",
        f"shares {info.get('sharesAccepted', '?')} ok/{info.get('sharesRejected', '?')} rejected,",
        f"best {format_diff(_num(info.get('bestDiff')) or 0)},",
        f"up {format_duration(_num(info.get('uptimeSeconds')) or 0)}",
    ]
    return " ".join(parts)


# --------------------------------------------------------------------------
# Alert logic (no network access: feed it API responses and timestamps)


class _PeriodStats:
    """Totals for the daily report."""

    def __init__(self, start: float):
        self.start = start
        self.ok_polls = 0
        self.failed_polls = 0
        self.hash_sum = 0.0
        self.chip_min: Optional[float] = None
        self.chip_max: Optional[float] = None
        self.accepted = 0
        self.rejected = 0

    def add(self, info: dict, prev: Optional[dict]) -> None:
        self.ok_polls += 1
        self.hash_sum += _num(info.get("hashRate")) or 0.0
        for t in chip_temps(info):
            self.chip_min = t if self.chip_min is None else min(self.chip_min, t)
            self.chip_max = t if self.chip_max is None else max(self.chip_max, t)
        for key, attr in (("sharesAccepted", "accepted"), ("sharesRejected", "rejected")):
            cur = _num(info.get(key))
            if cur is None:
                continue
            before = _num(prev.get(key)) if prev else None
            if before is None:
                continue
            # Counters reset to zero when the miner restarts.
            delta = cur - before if cur >= before else cur
            setattr(self, attr, getattr(self, attr) + int(delta))


class Monitor:
    def __init__(self, config: Config, state: Optional[dict] = None):
        self.cfg = config
        self.state: dict = state if state is not None else {}
        self.network_difficulty: Optional[float] = None
        self.name = "Miner"
        self.started = False
        self.last_info: Optional[dict] = None
        self.last_ok: Optional[float] = None
        self.fail_count = 0
        self.offline_since: Optional[float] = None
        self.offline_last_sent: Optional[float] = None
        self.issues: Dict[str, float] = {}  # active problem -> last time alerted
        self.hash_samples: deque = deque()
        self.last_share_count: Optional[float] = None
        self.last_share_change: Optional[float] = None
        self.weak_wifi_polls = 0
        self.template_misses = 0
        self.period: Optional[_PeriodStats] = None

    # -- entry point ---------------------------------------------------------

    def on_poll(self, info: Optional[dict], now: float) -> List[Alert]:
        """Process one poll. `info` is the API response, or None if the miner
        did not answer. Returns the alerts to send."""
        if self.period is None:
            self.period = _PeriodStats(now)
        if info is None:
            alerts = self._on_failure(now)
        else:
            alerts = self._on_success(info, now)
            alerts += self._check_health(info, now)
            alerts += self._check_luck(info)
            self.period.add(info, self.last_info)
            self.last_info = info
            self.last_ok = now
        alerts += self._maybe_daily_report(info, now)
        return alerts

    # -- helpers -------------------------------------------------------------

    def _issue(self, key: str, bad: bool, clear: bool, now: float,
               alert: Callable[[], Alert], clear_alert: Optional[Callable[[], Alert]] = None,
               remind: bool = False) -> List[Alert]:
        """Track a problem that can last. Alerts when it starts, repeats every
        remind_hours while it lasts (if remind), and sends clear_alert once it
        is over. `clear` is separate from `not bad` so thresholds get slack."""
        if key in self.issues:
            if clear:
                del self.issues[key]
                return [clear_alert()] if clear_alert else []
            if remind and now - self.issues[key] >= self.cfg.remind_hours * 3600:
                self.issues[key] = now
                a = alert()
                a.title += " (still)"
                return [a]
            return []
        if bad:
            self.issues[key] = now
            return [alert()]
        return []

    # -- reachability --------------------------------------------------------

    def _on_failure(self, now: float) -> List[Alert]:
        self.fail_count += 1
        self.period.failed_polls += 1
        if self.offline_since is None:
            self.offline_since = now
        if self.fail_count < self.cfg.offline_after:
            return []
        if self.offline_last_sent is None:
            self.offline_last_sent = now
            if self.last_ok is not None:
                seen = f"Last answered {format_duration(now - self.last_ok)} ago."
            else:
                seen = "It has not answered since the monitor started; check the address too."
            return [Alert("Miner offline",
                          f"{self.name} is not answering. {seen} Check its power, the power "
                          "supply and WiFi.", "high", ["warning"])]
        if now - self.offline_last_sent >= self.cfg.remind_hours * 3600:
            self.offline_last_sent = now
            return [Alert("Miner still offline",
                          f"{self.name} has not answered for {format_duration(now - self.offline_since)}.",
                          "high", ["warning"])]
        return []

    def _on_success(self, info: dict, now: float) -> List[Alert]:
        alerts: List[Alert] = []
        self.name = info.get("hostname") or self.name
        uptime = _num(info.get("uptimeSeconds")) or 0.0
        prev_uptime = _num(self.last_info.get("uptimeSeconds")) if self.last_info else None
        restarted = prev_uptime is not None and uptime + 5 < prev_uptime

        if self.offline_last_sent is not None:
            msg = f"{self.name} is answering again after {format_duration(now - self.offline_since)}."
            if restarted:
                msg += f" It restarted {format_duration(uptime)} ago (power cut or crash)."
            alerts.append(Alert("Miner back online", msg, "default", ["white_check_mark"]))
        elif restarted:
            alerts.append(Alert(
                "Miner restarted",
                f"{self.name} restarted {format_duration(uptime)} ago after running "
                f"{format_duration(prev_uptime)}. A short power cut, a crash or a settings "
                "change can cause this.", "default", ["arrows_counterclockwise"]))
        self.fail_count = 0
        self.offline_since = None
        self.offline_last_sent = None

        if restarted:
            self.hash_samples.clear()
            self.last_share_count = None

        if not self.started:
            self.started = True
            chips = chip_temps(info)
            temp_txt = f", chips {max(chips):.0f}°C" if chips else ""
            alerts.append(Alert(
                "Monitoring started",
                f"Watching {self.name}: {format_ths(_num(info.get('hashRate')) or 0)}"
                f"{temp_txt}, {preset_name(info)} preset, up {format_duration(uptime)}.",
                "low", ["eyes"]))
        return alerts

    # -- health --------------------------------------------------------------

    def _check_health(self, info: dict, now: float) -> List[Alert]:
        cfg, name = self.cfg, self.name
        out: List[Alert] = []

        overheat = info.get("overheat_mode") == 1
        out += self._issue(
            "overheat", overheat, not overheat, now,
            lambda: Alert("Overheat shutdown: mining stopped",
                          f"{name} got too hot and dropped to 50 MHz. It will not mine again until "
                          "you open Settings and turn off overheat mode. Fix the cooling or pick a "
                          "lower preset first.", "urgent", ["fire"]),
            lambda: Alert("Overheat mode cleared", f"{name} is back on its normal settings.",
                          "default", ["white_check_mark"]),
            remind=True)

        fault = info.get("power_fault")
        out += self._issue(
            "power_fault", bool(fault), not fault, now,
            lambda: Alert("Power fault",
                          f"{name} reports a voltage regulator fault: {fault}. Mining is probably "
                          "stopped. Check the power supply (12 V, 70 W or more), then restart the "
                          "miner.", "urgent", ["zap"]),
            lambda: Alert("Power fault cleared", f"{name} no longer reports a power fault.",
                          "default", ["white_check_mark"]),
            remind=True)

        pools_down = bool(info.get("poolsUnavailable"))
        out += self._issue(
            "pools", pools_down, not pools_down, now,
            lambda: Alert("No pool reachable: mining paused",
                          f"{name} cannot reach the main or the fallback pool, so it stopped "
                          "hashing. Check the internet connection and the pool settings.",
                          "high", ["warning"]),
            lambda: Alert("Pool connection restored", f"{name} is mining again.",
                          "default", ["white_check_mark"]),
            remind=True)

        fallback = bool(info.get("isUsingFallbackStratum")) and not pools_down
        out += self._issue(
            "fallback", fallback, not fallback, now,
            lambda: Alert("Running on the fallback pool",
                          f"The main pool ({info.get('stratumURL')}) is not reachable, so {name} "
                          f"switched to {info.get('fallbackStratumURL')}. It is still mining.",
                          "default", ["information_source"]),
            lambda: Alert("Back on the main pool", f"{name} is mining on {info.get('stratumURL')} again.",
                          "low", ["white_check_mark"]))

        chips = chip_temps(info)
        if chips:
            chip = max(chips)
            out += self._issue(
                "chip_hot", chip >= cfg.temp_warn, chip <= cfg.temp_warn - 3, now,
                lambda: Alert(f"Chips running hot: {chip:.0f}°C",
                              f"{name} is at {chip:.0f}°C. At {CHIP_SHUTDOWN_C}°C it stops mining "
                              "until you clear overheat mode by hand. Add cooling or switch to a "
                              "lower preset (Power or Medium).", "high", ["thermometer"]),
                lambda: Alert(f"Chips cooled to {chip:.0f}°C", f"{name} is back in a safe range.",
                              "low", ["white_check_mark"]))

        vr = _num(info.get("vrTemp"))
        if vr is not None and vr > 0:
            out += self._issue(
                "vr_hot", vr >= cfg.vr_warn, vr <= cfg.vr_warn - 5, now,
                lambda: Alert(f"Voltage regulator hot: {vr:.0f}°C",
                              f"{name}'s voltage regulator is at {vr:.0f}°C. At {VR_SHUTDOWN_C}°C it "
                              "stops mining until you clear overheat mode. Improve airflow or pick "
                              "a lower preset.", "high", ["thermometer"]),
                lambda: Alert(f"Voltage regulator cooled to {vr:.0f}°C",
                              f"{name} is back in a safe range.", "low", ["white_check_mark"]))

        # Hashrate and share checks only mean something while it should be mining.
        uptime = _num(info.get("uptimeSeconds")) or 0.0
        should_mine = not overheat and not pools_down and not fault and uptime >= 600

        gh = _num(info.get("hashRate"))
        want = expected_gh(info)
        if not should_mine or gh is None or not want:
            self.hash_samples.clear()
            self.issues.pop("low_hashrate", None)
        else:
            window = cfg.low_hashrate_minutes * 60
            self.hash_samples.append((now, gh))
            while now - self.hash_samples[0][0] > window:
                self.hash_samples.popleft()
            if now - self.hash_samples[0][0] >= window - cfg.interval:
                avg = sum(s for _, s in self.hash_samples) / len(self.hash_samples)
                freq = info.get("frequency")
                out += self._issue(
                    "low_hashrate", avg < cfg.low_hashrate_ratio * want, avg >= 0.9 * want, now,
                    lambda: Alert("Hashrate low",
                                  f"{name} averaged {format_ths(avg)} over {cfg.low_hashrate_minutes} "
                                  f"minutes; at {freq} MHz it should do about {format_ths(want)}. "
                                  "Likely causes: an unstable overclock (try a lower preset), a chip "
                                  "not hashing, or a power supply that can't keep up.",
                                  "high", ["chart_with_downwards_trend"]),
                    lambda: Alert("Hashrate back to normal", f"{name} is at {format_ths(avg)}.",
                                  "low", ["white_check_mark"]))

        shares = _num(info.get("sharesAccepted"))
        if shares is not None:
            if self.last_share_count is None or shares != self.last_share_count:
                self.last_share_count = shares
                self.last_share_change = now
            if not should_mine:
                self.issues.pop("stalled", None)
            else:
                quiet = now - self.last_share_change
                limit = cfg.stall_minutes * 60
                out += self._issue(
                    "stalled", quiet >= limit, quiet < limit, now,
                    lambda: Alert("No accepted shares",
                                  f"{name} has had no accepted shares for {format_duration(quiet)}. "
                                  "The pool connection may be stuck; restarting the miner usually "
                                  "fixes it.", "high", ["warning"]),
                    lambda: Alert("Shares flowing again", f"{name} is getting shares accepted.",
                                  "low", ["white_check_mark"]),
                    remind=True)

        rssi = _num(info.get("wifiRSSI"))
        if rssi is not None and rssi < 0:
            self.weak_wifi_polls = self.weak_wifi_polls + 1 if rssi <= cfg.rssi_warn else 0
            out += self._issue(
                "wifi", self.weak_wifi_polls >= 5, rssi > cfg.rssi_warn + 5, now,
                lambda: Alert(f"Weak WiFi: {rssi:.0f} dBm",
                              f"{name}'s WiFi signal is weak, which can drop the pool connection. "
                              "Move it or the router a little, or add a 2.4 GHz extender.",
                              "default", ["signal_strength"]),
                lambda: Alert("WiFi signal OK", f"{name} is at {rssi:.0f} dBm.", "low",
                              ["white_check_mark"]))

        if cfg.expect_address:
            want_addr = cfg.expect_address.strip()
            for key, label, field_name in (("addr_main", "main pool", "stratumUser"),
                                           ("addr_fallback", "fallback pool", "fallbackStratumUser")):
                user = str(info.get(field_name) or "")
                got = user.split(".")[0].strip()
                bad = got.lower() != want_addr.lower()
                out += self._issue(
                    key, bad, not bad, now,
                    lambda: Alert("Payout address does not match",
                                  f"The {label} on {name} pays {short_address(got)}, not your address "
                                  f"{short_address(want_addr)}. A block found now would not pay you. "
                                  "Fix it in Settings > Pool.", "urgent", ["rotating_light"]),
                    lambda: Alert("Payout address OK", f"The {label} on {name} now pays your address.",
                                  "default", ["white_check_mark"]),
                    remind=True)

            # The block template the pool sent: who a block found right now pays.
            outputs = template_outputs(info)
            if outputs:
                to_you = sum(v for a, v in outputs if a.lower() == want_addr.lower())
                self.template_misses = 0 if to_you else self.template_misses + 1
                total_btc = sum(v for _, v in outputs) / 1e8
                payees = ", ".join(short_address(a) for a, _ in outputs[:3])
                out += self._issue(
                    "template", self.template_misses >= 3, to_you > 0, now,
                    lambda: Alert("Block template does not pay you",
                                  f"The pool's block template pays {total_btc:.8f} BTC to {payees}, and "
                                  f"nothing to your address {short_address(want_addr)}. Check the pool "
                                  "and the payout address in Settings > Pool.", "urgent", ["rotating_light"]),
                    lambda: Alert("Block template pays you again",
                                  f"A block found now pays {to_you / 1e8:.8f} BTC to your address.",
                                  "default", ["white_check_mark"]),
                    remind=True)
        return out

    # -- luck ----------------------------------------------------------------

    def _check_luck(self, info: dict) -> List[Alert]:
        out: List[Alert] = []
        name = self.name

        blocks = info.get("blocksFound")
        if isinstance(blocks, int) and not isinstance(blocks, bool):
            known = self.state.get("blocks_found")
            status = str(info.get("lastBlockStatus") or "none")
            block_hash = str(info.get("lastBlockHash") or "")
            link = f" https://mempool.space/block/{block_hash}" if block_hash else ""
            if known is not None and blocks > known:
                out.append(Alert(
                    "BLOCK FOUND!",
                    f"{name} found a Bitcoin block! Status: {status}.{link} The reward goes to the "
                    "payout address once the block is confirmed (100 confirmations before it can "
                    "be spent).", "urgent", ["tada", "moneybag"]))
            elif (known is not None and blocks == known and blocks > 0
                  and status != self.state.get("last_block_status")):
                if status == "confirmed":
                    out.append(Alert("Block confirmed",
                                     f"The block {name} found is confirmed on the chain.{link}",
                                     "high", ["tada"]))
                elif status == "orphaned":
                    out.append(Alert("Block orphaned",
                                     f"Another miner's block won the race, so the block {name} found "
                                     f"will not pay.{link}", "high", ["disappointed"]))
            self.state["blocks_found"] = blocks
            self.state["last_block_status"] = status

        best = _num(info.get("bestDiff"))
        if best is not None and best > 0:
            known_best = self.state.get("best_diff")
            if known_best is not None and best > known_best and self.cfg.best_share_alerts:
                msg = f"{name} beat its best share ever ({format_diff(known_best)})."
                net = self.network_difficulty
                if net:
                    if best >= net:
                        msg += " That is above the network difficulty: it should be a block!"
                    else:
                        msg += (f" A block needs one about {net / best:,.0f}x harder "
                                f"(network difficulty {format_diff(net)}).")
                out.append(Alert(f"New best share: {format_diff(best)}", msg, "default", ["star"]))
            self.state["best_diff"] = max(best, known_best or 0.0)
        return out

    # -- daily report --------------------------------------------------------

    def _maybe_daily_report(self, info: Optional[dict], now: float) -> List[Alert]:
        hour = self.cfg.daily_hour
        if hour is None:
            return []
        local = dt.datetime.fromtimestamp(now)
        today = local.date()
        if "last_report" not in self.state:
            # First run: the first report goes out at the next report hour.
            first = today if local.hour >= hour else today - dt.timedelta(days=1)
            self.state["last_report"] = first.isoformat()
            return []
        if local.hour < hour or self.state["last_report"] >= today.isoformat():
            return []
        self.state["last_report"] = today.isoformat()

        p = self.period
        lines = []
        span = format_duration(now - p.start)
        if p.ok_polls:
            lines.append(f"Last {span}: average {format_ths(p.hash_sum / p.ok_polls)}, "
                         f"offline about {format_duration(p.failed_polls * self.cfg.interval)}.")
        else:
            lines.append(f"Last {span}: the miner never answered.")
        if p.chip_min is not None:
            lines.append(f"Chips {p.chip_min:.0f}-{p.chip_max:.0f}°C.")
        total = p.accepted + p.rejected
        if total:
            lines.append(f"{p.accepted:,} shares accepted ({p.rejected / total:.1%} rejected).")
        if self.state.get("best_diff"):
            lines.append(f"Best share ever: {format_diff(self.state['best_diff'])}.")
        if info is not None:
            lines.append(f"Now: {format_ths(_num(info.get('hashRate')) or 0)}, "
                         f"{preset_name(info)} preset, up {format_duration(_num(info.get('uptimeSeconds')) or 0)}.")
        elif self.offline_since is not None:
            lines.append(f"Right now it is NOT answering (since {format_duration(now - self.offline_since)} ago).")

        self.period = _PeriodStats(now)
        return [Alert(f"Daily report: {self.name}", " ".join(lines), "low", ["bar_chart"])]


# --------------------------------------------------------------------------
# Network


# The miner is on the local network: never send its requests through a proxy.
_LAN_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def miner_base_url(miner: str) -> str:
    miner = miner.strip().rstrip("/")
    return miner if miner.startswith(("http://", "https://")) else f"http://{miner}"


def fetch_info(base_url: str, timeout: float = 10.0) -> Optional[dict]:
    req = urllib.request.Request(base_url + "/api/system/info", headers={"User-Agent": USER_AGENT})
    try:
        with _LAN_OPENER.open(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("Miner did not answer: %s", exc)
        return None


def fetch_network_difficulty(timeout: float = 15.0) -> Optional[float]:
    sources = (
        ("https://mempool.space/api/v1/mining/hashrate/3d",
         lambda body: float(json.loads(body)["currentDifficulty"])),
        ("https://blockchain.info/q/getdifficulty", lambda body: float(body.strip())),
    )
    for url, parse in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                value = parse(resp.read().decode("utf-8"))
            if value > 0:
                return value
        except Exception as exc:  # any failure: try the next source
            log.debug("Network difficulty from %s failed: %s", url, exc)
    return None


def _post_json(url: str, payload: dict, timeout: float) -> None:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


class Notifier:
    """Sends alerts to every configured channel. An alert that fails on a
    channel (internet down, say) is retried on that channel on the next poll."""

    def __init__(self, ntfy_topic: Optional[str] = None, ntfy_server: str = "https://ntfy.sh",
                 telegram_token: Optional[str] = None, telegram_chat: Optional[str] = None,
                 timeout: float = 15.0, post: Callable[[str, dict, float], None] = _post_json):
        self.timeout = timeout
        self.post = post
        self.channels: Dict[str, Callable[[Alert], None]] = {}
        if ntfy_topic:
            server = ntfy_server.rstrip("/")
            self.channels["ntfy"] = lambda a: self.post(server, {
                "topic": ntfy_topic, "title": a.title, "message": a.message,
                "priority": PRIORITY.get(a.priority, 3), "tags": a.tags}, self.timeout)
        if telegram_token and telegram_chat:
            url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
            self.channels["telegram"] = lambda a: self.post(url, {
                "chat_id": telegram_chat, "text": f"{a.title}\n\n{a.message}",
                "disable_web_page_preview": True}, self.timeout)
        self.pending: List[tuple] = []  # (alert, channel names still to send to)

    def send(self, alerts: List[Alert]) -> None:
        for alert in alerts:
            self.pending.append((alert, list(self.channels)))
        self.pending = self.pending[-50:]
        still: List[tuple] = []
        for alert, names in self.pending:
            failed = []
            for ch in names:
                try:
                    self.channels[ch](alert)
                except Exception as exc:
                    log.warning("Could not send '%s' via %s: %s", alert.title, ch, exc)
                    failed.append(ch)
            if failed:
                still.append((alert, failed))
        self.pending = still


# --------------------------------------------------------------------------
# State file


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        log.warning("Ignoring unreadable state file %s: %s", path, exc)
        return {}


def save_state(path: str, state: dict) -> None:
    folder = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".nano_monitor_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


# --------------------------------------------------------------------------
# Command line


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    env = os.environ.get
    default_state = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nano_monitor_state.json")
    p = argparse.ArgumentParser(
        description="Watch a BitForge Nano and send phone alerts (ntfy and/or Telegram).")
    p.add_argument("--miner", default=env("MINER_HOST"),
                   help="miner address, e.g. 192.168.1.50 (env MINER_HOST)")
    p.add_argument("--ntfy-topic", default=env("NTFY_TOPIC"),
                   help="ntfy topic to publish to; pick a hard-to-guess name (env NTFY_TOPIC)")
    p.add_argument("--ntfy-server", default=env("NTFY_SERVER", "https://ntfy.sh"),
                   help="ntfy server (default https://ntfy.sh, env NTFY_SERVER)")
    p.add_argument("--telegram-token", default=env("TELEGRAM_BOT_TOKEN"),
                   help="Telegram bot token (env TELEGRAM_BOT_TOKEN)")
    p.add_argument("--telegram-chat", default=env("TELEGRAM_CHAT_ID"),
                   help="Telegram chat id (env TELEGRAM_CHAT_ID)")
    p.add_argument("--expect-address", default=env("PAYOUT_ADDRESS"),
                   help="your BTC payout address; alert if the miner pays anywhere else "
                        "(env PAYOUT_ADDRESS)")
    p.add_argument("--interval", type=int, default=60, help="seconds between polls (default 60)")
    p.add_argument("--temp-warn", type=float, default=68.0,
                   help=f"chip temperature alert in °C (default 68; mining stops at {CHIP_SHUTDOWN_C})")
    p.add_argument("--vr-warn", type=float, default=95.0,
                   help=f"regulator temperature alert in °C (default 95; mining stops at {VR_SHUTDOWN_C})")
    p.add_argument("--daily-hour", type=int, default=9,
                   help="local hour (0-23) for the daily report; -1 turns it off (default 9)")
    p.add_argument("--no-best-share-alerts", action="store_true",
                   help="do not alert on a new best share")
    p.add_argument("--state-file", default=env("NANO_MONITOR_STATE", default_state),
                   help="where to remember best share and blocks between runs")
    p.add_argument("--once", action="store_true",
                   help="check the miner once, print its status and exit (no alerts)")
    p.add_argument("--test", action="store_true", help="send a test alert and exit")
    p.add_argument("-v", "--verbose", action="store_true", help="log every poll")
    args = p.parse_args(argv)
    if not args.test and not args.miner:
        p.error("--miner is required (or set MINER_HOST)")
    if args.interval < 15:
        p.error("--interval must be at least 15 seconds")
    if not -1 <= args.daily_hour <= 23:
        p.error("--daily-hour must be between 0 and 23, or -1")
    return args


def build_notifier(args: argparse.Namespace) -> Notifier:
    return Notifier(ntfy_topic=args.ntfy_topic, ntfy_server=args.ntfy_server,
                    telegram_token=args.telegram_token, telegram_chat=args.telegram_chat)


def run_once(args: argparse.Namespace) -> int:
    info = fetch_info(miner_base_url(args.miner))
    if info is None:
        print(f"No answer from {miner_base_url(args.miner)}/api/system/info. "
              "Check the address and that this computer is on the same network.")
        return 1
    print(status_line(info))
    print(f"Main pool:     {info.get('stratumURL')}:{info.get('stratumPort')} pays {info.get('stratumUser')}")
    print(f"Fallback pool: {info.get('fallbackStratumURL')}:{info.get('fallbackStratumPort')} "
          f"pays {info.get('fallbackStratumUser')}")
    outputs = template_outputs(info)
    if outputs:
        print("A block found now pays: " + ", ".join(f"{v / 1e8:.8f} BTC to {a}" for a, v in outputs))
    if info.get("overheat_mode") == 1:
        print("WARNING: overheat mode is on, so it is not mining. Clear it in Settings.")
    if args.expect_address:
        for label, key in (("main", "stratumUser"), ("fallback", "fallbackStratumUser")):
            got = str(info.get(key) or "").split(".")[0].strip()
            ok = got.lower() == args.expect_address.strip().lower()
            print(f"Payout address on {label} pool: {'OK' if ok else 'DOES NOT MATCH ' + args.expect_address}")
        if outputs:
            pays = any(a.lower() == args.expect_address.strip().lower() for a, _ in outputs)
            print(f"Block template pays your address: {'yes' if pays else 'NO'}")
    return 0


def run_test(args: argparse.Namespace) -> int:
    notifier = build_notifier(args)
    if not notifier.channels:
        print("No alert channel set. Pass --ntfy-topic (or --telegram-token and --telegram-chat).")
        return 1
    notifier.send([Alert("Test from your BitForge monitor",
                         "If you can read this on your phone, alerts work.",
                         "default", ["white_check_mark"])])
    if notifier.pending:
        print("Sending failed; see the warning above.")
        return 1
    print(f"Test alert sent via {', '.join(notifier.channels)}.")
    return 0


def run_forever(args: argparse.Namespace) -> int:
    cfg = Config(interval=args.interval, temp_warn=args.temp_warn, vr_warn=args.vr_warn,
                 daily_hour=None if args.daily_hour < 0 else args.daily_hour,
                 expect_address=args.expect_address,
                 best_share_alerts=not args.no_best_share_alerts)
    notifier = build_notifier(args)
    if not notifier.channels:
        log.warning("No alert channel set: alerts will only be printed here.")
    state = load_state(args.state_file)
    monitor = Monitor(cfg, state)
    base = miner_base_url(args.miner)
    log.info("Watching %s every %ss; alerts via %s.", base, cfg.interval,
             ", ".join(notifier.channels) or "console only")

    next_difficulty = 0.0
    next_status_log = 0.0
    saved = json.dumps(state, sort_keys=True)
    while True:
        started = time.time()
        if started >= next_difficulty:
            diff = fetch_network_difficulty()
            if diff:
                monitor.network_difficulty = diff
            next_difficulty = started + (3600 if diff else 600)

        info = fetch_info(base)
        alerts = monitor.on_poll(info, started)
        for alert in alerts:
            log.info("ALERT [%s] %s: %s", alert.priority, alert.title, alert.message)
        notifier.send(alerts)

        if info is not None and (args.verbose or started >= next_status_log):
            log.info(status_line(info))
            next_status_log = started + 900
        elif info is None:
            log.info("No answer from the miner (%s in a row).", monitor.fail_count)

        current = json.dumps(state, sort_keys=True)
        if current != saved:
            try:
                save_state(args.state_file, state)
                saved = current
            except OSError as exc:
                log.warning("Could not save state to %s: %s", args.state_file, exc)

        time.sleep(max(1.0, cfg.interval - (time.time() - started)))


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    if args.test:
        return run_test(args)
    if args.once:
        return run_once(args)
    try:
        return run_forever(args)
    except KeyboardInterrupt:
        log.info("Stopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
