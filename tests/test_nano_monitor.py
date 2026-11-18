"""Tests for the BitForge Nano monitor (bitforge-nano/monitor/nano_monitor.py).

The alert logic is driven with hand-built API responses and a fake clock;
the HTTP parts talk to local throwaway servers. No internet access.
"""

import datetime as dt
import importlib.util
import json
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "bitforge-nano" / "monitor" / "nano_monitor.py"
_spec = importlib.util.spec_from_file_location("nano_monitor", _PATH)
nm = importlib.util.module_from_spec(_spec)
sys.modules["nano_monitor"] = nm  # dataclasses look the module up while it loads
_spec.loader.exec_module(nm)

ADDR = "bc1qexampleexampleexampleexampleexample0x"
OTHER = "bc1qsomeoneelsesomeoneelsesomeoneelse999"
INTERVAL = 60


def healthy(**overrides):
    info = {
        "hostname": "BitForge",
        "hashRate": 2650.0,  # GH/s
        "frequency": 650,
        "coreVoltage": 1225,
        "asicCount": 2,
        "smallCoreCount": 2040,
        "chiptemp1": 50.0,
        "chiptemp2": 52.0,
        "vrTemp": 60.0,
        "fanSpeed": 40,
        "uptimeSeconds": 3600,
        "sharesAccepted": 100,
        "sharesRejected": 1,
        "bestDiff": 1e9,
        "blocksFound": 0,
        "lastBlockHash": "",
        "lastBlockStatus": "none",
        "overheat_mode": 0,
        "poolsUnavailable": False,
        "isUsingFallbackStratum": 0,
        "wifiRSSI": -50,
        "stratumURL": "solo.example.org",
        "fallbackStratumURL": "fallback.example.org",
        "stratumUser": ADDR + ".nano",
        "fallbackStratumUser": ADDR + ".nano",
        "coinbase": {"outputs": [{"address": ADDR, "value": 312500000},
                                 {"address": "OP_RETURN: witness commitment", "value": 0}]},
    }
    info.update(overrides)
    return info


class Sim:
    """Runs polls one interval apart. Each healthy poll advances uptime and
    the accepted-share counter so the miner looks alive."""

    def __init__(self, start=dt.datetime(2026, 11, 18, 10, 0), **cfg):
        cfg.setdefault("interval", INTERVAL)
        self.cfg = nm.Config(**cfg)
        self.state = {}
        self.mon = nm.Monitor(self.cfg, self.state)
        self.now = start.timestamp()
        self.uptime = 3600
        self.shares = 100

    def poll(self, info="healthy", **overrides):
        if info == "healthy":
            self.uptime += INTERVAL
            self.shares += 5
            fields = {"uptimeSeconds": self.uptime, "sharesAccepted": self.shares}
            fields.update(overrides)
            info = healthy(**fields)
        alerts = self.mon.on_poll(info, self.now)
        self.now += INTERVAL
        return alerts

    def polls(self, n, info="healthy", **overrides):
        out = []
        for _ in range(n):
            out += self.poll(info, **overrides)
        return out


def titles(alerts):
    return [a.title for a in alerts]


def test_first_poll_sends_startup_only_and_records_state():
    sim = Sim()
    alerts = sim.poll()
    assert titles(alerts) == ["Monitoring started"]
    assert "2.65 TH/s" in alerts[0].message and "Max preset" in alerts[0].message
    assert sim.state["best_diff"] == 1e9
    assert sim.state["blocks_found"] == 0
    assert sim.polls(5) == []


def test_offline_after_three_failed_polls_then_back_online_after_restart():
    sim = Sim()
    sim.poll()
    assert sim.polls(2, info=None) == []
    alerts = sim.poll(info=None)
    assert titles(alerts) == ["Miner offline"]
    assert alerts[0].priority == "high"
    assert sim.polls(10, info=None) == []
    back = sim.poll(uptimeSeconds=120)
    assert titles(back) == ["Miner back online"]
    assert "restarted" in back[0].message


def test_offline_reminder_every_six_hours():
    sim = Sim()
    sim.poll()
    alerts = sim.polls(6 * 60 + 5, info=None)
    assert titles(alerts) == ["Miner offline", "Miner still offline"]


def test_short_outage_is_reported_as_a_restart():
    sim = Sim()
    sim.poll()
    sim.poll(info=None)
    alerts = sim.poll(uptimeSeconds=45)
    assert titles(alerts) == ["Miner restarted"]


def test_overheat_shutdown_is_urgent_reminds_and_clears():
    sim = Sim()
    sim.poll()
    alerts = sim.poll(overheat_mode=1, frequency=50, coreVoltage=1000, hashRate=0.0)
    assert titles(alerts) == ["Overheat shutdown: mining stopped"]
    assert alerts[0].priority == "urgent"
    # No low-hashrate or stalled-share noise while it is shut down.
    reminders = sim.polls(6 * 60, overheat_mode=1, frequency=50, coreVoltage=1000, hashRate=0.0)
    assert titles(reminders) == ["Overheat shutdown: mining stopped (still)"]
    assert titles(sim.poll()) == ["Overheat mode cleared"]


def test_chip_temperature_alert_has_hysteresis():
    sim = Sim(temp_warn=68)
    sim.poll()
    assert titles(sim.poll(chiptemp2=68.4)) == ["Chips running hot: 68°C"]
    assert sim.poll(chiptemp2=66.0) == []  # still within the 3 °C slack
    assert titles(sim.poll(chiptemp2=64.5)) == ["Chips cooled to 64°C"]


def test_invalid_temperature_readings_are_ignored():
    sim = Sim()
    sim.poll()
    assert sim.poll(chiptemp1=0.0, chiptemp2=-1.0, vrTemp=0.0) == []


def test_regulator_temperature_alert():
    sim = Sim()
    sim.poll()
    alerts = sim.poll(vrTemp=97.0)
    assert titles(alerts) == ["Voltage regulator hot: 97°C"]
    assert "105°C" in alerts[0].message


def test_low_hashrate_needs_a_sustained_drop():
    sim = Sim()
    sim.poll()
    assert sim.poll(hashRate=1500.0) == []  # one bad reading is not enough
    sim.polls(20)
    alerts = sim.polls(16, hashRate=1900.0)  # 72% of the 2652 GH/s expected
    assert titles(alerts) == ["Hashrate low"]
    assert "about 2.65 TH/s" in alerts[0].message
    recovered = sim.polls(16)
    assert titles(recovered) == ["Hashrate back to normal"]


def test_no_hashrate_judgement_right_after_a_restart():
    sim = Sim()
    sim.poll()
    sim.uptime = 0
    alerts = sim.polls(9, hashRate=100.0)
    assert "Hashrate low" not in titles(alerts)


def test_stalled_shares_alert_and_recovery():
    sim = Sim(stall_minutes=30)
    sim.poll()
    stuck = sim.shares
    alerts = []
    for _ in range(31):
        sim.uptime += INTERVAL
        alerts += sim.poll(info=healthy(uptimeSeconds=sim.uptime, sharesAccepted=stuck))
    assert titles(alerts) == ["No accepted shares"]
    assert titles(sim.poll()) == ["Shares flowing again"]


def test_pools_unavailable_and_fallback():
    sim = Sim()
    sim.poll()
    assert titles(sim.poll(isUsingFallbackStratum=1)) == ["Running on the fallback pool"]
    assert titles(sim.poll()) == ["Back on the main pool"]
    alerts = sim.poll(poolsUnavailable=True, hashRate=0.0)
    assert titles(alerts) == ["No pool reachable: mining paused"]


def test_power_fault_is_urgent():
    sim = Sim()
    sim.poll()
    alerts = sim.poll(power_fault="VOUT_UV")
    assert titles(alerts) == ["Power fault"]
    assert "VOUT_UV" in alerts[0].message and alerts[0].priority == "urgent"


def test_weak_wifi_needs_five_polls():
    sim = Sim()
    sim.poll()
    alerts = sim.polls(5, wifiRSSI=-80)
    assert titles(alerts) == ["Weak WiFi: -80 dBm"]


def test_new_best_share_mentions_how_far_from_a_block():
    sim = Sim()
    sim.mon.network_difficulty = 134.8e12
    sim.poll()
    alerts = sim.poll(bestDiff=5e10)
    assert titles(alerts) == ["New best share: 50.00 G"]
    assert "2,696x harder" in alerts[0].message
    assert sim.state["best_diff"] == 5e10
    # A lower value after a firmware reset neither alerts nor lowers the record.
    assert sim.poll(bestDiff=2e9) == []
    assert sim.state["best_diff"] == 5e10


def test_best_share_alerts_can_be_turned_off():
    sim = Sim(best_share_alerts=False)
    sim.poll()
    assert sim.poll(bestDiff=5e10) == []


def test_block_found_then_confirmed():
    sim = Sim()
    sim.poll()
    found = sim.poll(blocksFound=1, lastBlockStatus="pending", lastBlockHash="00000000abc")
    assert titles(found) == ["BLOCK FOUND!"]
    assert found[0].priority == "urgent"
    assert "https://mempool.space/block/00000000abc" in found[0].message
    assert sim.poll(blocksFound=1, lastBlockStatus="pending", lastBlockHash="00000000abc") == []
    confirmed = sim.poll(blocksFound=1, lastBlockStatus="confirmed", lastBlockHash="00000000abc")
    assert titles(confirmed) == ["Block confirmed"]


def test_old_blocks_are_not_announced_on_first_run():
    sim = Sim()
    assert "BLOCK FOUND!" not in titles(sim.poll(blocksFound=2, lastBlockStatus="confirmed"))


def test_payout_address_checks():
    sim = Sim(expect_address=ADDR)
    assert titles(sim.poll()) == ["Monitoring started"]
    alerts = sim.poll(fallbackStratumUser=OTHER + ".worker")
    assert titles(alerts) == ["Payout address does not match"]
    assert "fallback pool" in alerts[0].message and alerts[0].priority == "urgent"
    assert titles(sim.poll()) == ["Payout address OK"]


def test_block_template_not_paying_you_after_three_polls():
    sim = Sim(expect_address=ADDR)
    sim.poll()
    wrong = {"outputs": [{"address": OTHER, "value": 312500000}]}
    alerts = sim.polls(3, coinbase=wrong)
    assert titles(alerts) == ["Block template does not pay you"]
    assert "3.12500000 BTC" in alerts[0].message
    assert titles(sim.poll()) == ["Block template pays you again"]


def test_daily_report_once_a_day_at_the_set_hour():
    sim = Sim(start=dt.datetime(2026, 11, 18, 10, 0), daily_hour=9)
    alerts = sim.polls(23 * 60 + 1)  # 10:00 through 09:00 the next day
    assert [t for t in titles(alerts) if t.startswith("Daily report")] == ["Daily report: BitForge"]
    report = next(a for a in alerts if a.title.startswith("Daily report"))
    assert "average 2.65 TH/s" in report.message
    assert "shares accepted" in report.message
    later = sim.polls(120)
    assert not [t for t in titles(later) if t.startswith("Daily report")]


def test_daily_report_still_sent_while_offline():
    sim = Sim(start=dt.datetime(2026, 11, 18, 8, 0), daily_hour=9)
    sim.poll()
    alerts = sim.polls(70, info=None)
    report = [a for a in alerts if a.title.startswith("Daily report")]
    assert len(report) == 1
    assert "NOT answering" in report[0].message


def test_notifier_retries_only_the_channel_that_failed():
    sent = []
    fail = {"telegram": True}

    def post(url, payload, timeout):
        channel = "telegram" if "telegram" in url else "ntfy"
        if fail.get(channel):
            raise OSError("network down")
        sent.append((channel, payload))

    n = nm.Notifier(ntfy_topic="topic", ntfy_server="https://ntfy.example",
                    telegram_token="t", telegram_chat="1", post=post)
    n.send([nm.Alert("Hello", "World", "high", ["warning"])])
    assert [c for c, _ in sent] == ["ntfy"]
    assert sent[0][1] == {"topic": "topic", "title": "Hello", "message": "World",
                          "priority": 4, "tags": ["warning"]}
    fail["telegram"] = False
    n.send([])
    assert [c for c, _ in sent] == ["ntfy", "telegram"]
    assert sent[1][1]["text"] == "Hello\n\nWorld"
    assert n.pending == []


def test_state_file_round_trip(tmp_path):
    path = tmp_path / "state.json"
    assert nm.load_state(str(path)) == {}
    nm.save_state(str(path), {"best_diff": 5e10, "blocks_found": 0})
    assert nm.load_state(str(path)) == {"best_diff": 5e10, "blocks_found": 0}
    path.write_text("not json")
    assert nm.load_state(str(path)) == {}


def test_formatting():
    assert nm.format_diff(666e9) == "666.00 G"
    assert nm.format_diff(134.8e12) == "134.80 T"
    assert nm.format_duration(30) == "under a minute"
    assert nm.format_duration(3 * 86400 + 4 * 3600) == "3d 4h"
    assert nm.miner_base_url("192.168.1.50/") == "http://192.168.1.50"
    assert nm.preset_name(healthy()) == "Max"
    assert nm.preset_name(healthy(frequency=600)) == "custom"


# -- HTTP against local servers ----------------------------------------------


@pytest.fixture
def local_server():
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/system/info":
                body = json.dumps(healthy()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", received
    server.shutdown()


def test_fetch_info_from_miner(local_server):
    url, _ = local_server
    info = nm.fetch_info(url)
    assert info["hostname"] == "BitForge" and info["frequency"] == 650
    assert nm.fetch_info("http://127.0.0.1:9", timeout=2) is None


def test_ntfy_publish_payload(local_server, monkeypatch):
    url, received = local_server
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    urllib.request.install_opener(None)  # rebuild urllib's opener without proxies
    n = nm.Notifier(ntfy_topic="my-topic", ntfy_server=url)
    n.send([nm.Alert("BLOCK FOUND!", "Body", "urgent", ["tada"])])
    assert n.pending == []
    assert received == [{"topic": "my-topic", "title": "BLOCK FOUND!", "message": "Body",
                         "priority": 5, "tags": ["tada"]}]
