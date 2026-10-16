"""Tests for the supply/demand read (zones + volume pressure).

Hourly bars are hand-built so every zone, break and pressure value is known.
"""

import pandas as pd
import pytest

from qqq_iron_condor import daily_target as dtg
from qqq_iron_condor import supply_demand as sd

P = sd.SDParams(atr_bars=10, lookback_bars=60, pressure_bars=3)


def _bars(rows, start="2026-10-01 09:30"):
    """rows: (open, high, low, close, volume) hourly bars, 7 per session."""
    idx, t = [], pd.Timestamp(start)
    for i in range(len(rows)):
        idx.append(t)
        t += pd.Timedelta(hours=1)
        if t.hour >= 16:
            t = (t + pd.Timedelta(days=1)).replace(hour=9, minute=30)
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])


def _quiet(n, px=100.0, vol=1000):
    # Small up/down alternating bars, ~1.0 true range each.
    return [(px, px + 0.5, px - 0.5, px + (0.1 if i % 2 else -0.1), vol) for i in range(n)]


def test_find_zones_demand_and_supply():
    rows = _quiet(12) + [(100.0, 100.2, 99.8, 100.1, 1000), (100.1, 103.2, 100.0, 103.0, 5000)]  # base + impulse up
    rows += _quiet(4, 103.0) + [(103.0, 103.1, 102.9, 103.0, 1000), (103.0, 103.1, 100.2, 100.4, 5000)]  # base + impulse down
    bars = _bars(rows)
    zones = sd.find_zones(bars, atr=1.0, p=P)
    kinds = [(z.kind, round(z.low, 1), round(z.high, 1)) for z in zones]
    # Base = the up-to-two small-bodied bars right before the impulse.
    assert ("demand", 99.5, 100.5) in kinds
    assert ("supply", 102.5, 103.5) in kinds


def test_zone_marked_broken_on_close_through():
    rows = _quiet(12) + [(100.0, 100.2, 99.8, 100.1, 1000), (100.1, 103.2, 100.0, 103.0, 5000)]
    rows += [(103.0, 103.0, 99.0, 99.2, 3000)]  # closes below the demand zone's $99.50 low
    bars = _bars(rows)
    demand = [z for z in sd.find_zones(bars, 1.0, P) if z.kind == "demand"][0]
    assert demand.broken_at == bars.index[-1]


def test_chaikin_money_flow_sign():
    up = _bars([(100, 101, 99, 100.9, 1000)] * 3)
    down = _bars([(100, 101, 99, 99.1, 1000)] * 3)
    assert sd.chaikin_money_flow(up) == pytest.approx(0.9)
    assert sd.chaikin_money_flow(down) == pytest.approx(-0.9)


def test_read_at_demand_zone_with_buying_is_call():
    rows = _quiet(12) + [(100.0, 100.2, 99.8, 100.1, 1000), (100.1, 103.2, 100.0, 103.0, 5000)]
    # Drift back down toward the zone, last bars closing near their highs (buying).
    rows += [(103.0, 103.1, 101.5, 101.6, 1000), (101.6, 101.7, 100.4, 100.5, 1000)]
    rows += [(100.5, 101.0, 100.2, 100.95, 3000)] * 3
    bars = _bars(rows)
    read = sd.supply_demand_read(bars, today_start=bars.index[-1], p=P)
    assert read.ready
    assert read.zone_score == 1.0
    assert read.pressure > 0
    assert read.side == "call"
    assert "demand zone" in read.detail


def test_read_demand_broken_today_is_put():
    rows = _quiet(12) + [(100.0, 100.2, 99.8, 100.1, 1000), (100.1, 103.2, 100.0, 103.0, 5000)]
    rows += [(103.0, 103.1, 101.0, 101.2, 1000)] * 2
    rows += [(101.2, 101.3, 98.6, 98.7, 4000)]  # today's first hour breaks the zone
    bars = _bars(rows)
    read = sd.supply_demand_read(bars, today_start=bars.index[-1], p=P)
    assert read.zone_score == -1.0
    assert read.side == "put"
    assert "broke below" in read.detail


def test_read_needs_enough_bars():
    assert not sd.supply_demand_read(_bars(_quiet(5)), pd.Timestamp("2026-10-01 09:30"), P).ready


def test_backtest_direction_only_sees_bars_before_entry():
    rows = _quiet(12) + [(100.0, 100.2, 99.8, 100.1, 1000), (100.1, 103.2, 100.0, 103.0, 5000)]
    rows += [(103.0, 103.1, 101.5, 101.6, 1000), (101.6, 101.7, 100.4, 100.5, 1000)]
    rows += [(100.5, 101.0, 100.2, 100.95, 3000)] * 3
    bars = _bars(rows)
    # A huge later bar must not change the read made at the earlier entry.
    later = bars.copy()
    later.loc[later.index[-1] + pd.Timedelta(hours=1)] = (100.95, 120.0, 80.0, 81.0, 99999)
    d1 = sd.make_backtest_direction(bars, P)
    d2 = sd.make_backtest_direction(later, P)
    first_hour = bars.iloc[[-1]]
    assert d1(0, first_hour) == d2(0, first_hour) == "call"


READY_UP = dtg.FirstHourRead(True, "call", 100.0, 101.0, 1.0, "up")
SD_PUT = sd.SupplyDemandRead(True, "put", -0.5, -1.0, 0.25, "x", [], "detail")
SD_CALL = sd.SupplyDemandRead(True, "call", 0.6, 1.0, 0.0, "x", [], "detail")
SD_NONE = sd.SupplyDemandRead(True, None, 0.1, 0.0, 0.25, "x", [], "detail")


@pytest.mark.parametrize(
    "source, sd_read, expected",
    [
        ("supply_demand", SD_PUT, "PUT"),
        ("supply_demand", SD_NONE, "NO TRADE"),
        ("sd_first_hour", SD_PUT, "NO TRADE"),  # disagrees with the up first hour
        ("sd_first_hour", SD_CALL, "CALL"),
        ("supply_demand", None, "UNKNOWN"),
        ("first_hour", SD_PUT, "CALL"),  # default source ignores supply/demand
    ],
)
def test_choose_bias_supply_demand_sources(source, sd_read, expected):
    assert dtg.choose_bias(source, None, [], None, READY_UP, sd_read)[0] == expected


def test_supply_demand_waits_for_first_hour():
    not_ready = dtg.FirstHourRead(False, None, None, None, None, "not yet")
    assert dtg.choose_bias("supply_demand", None, [], None, not_ready, SD_CALL)[0] == "WAIT"


def test_report_shows_supply_demand_row_and_backtest_note():
    chain = dtg._synthetic_chain(750.0, 60, dtg.Config())
    plan = dtg.build_plan(750.0, 9.5, chain, "PUT", "supply/demand", dtg.Config(), first_hour=READY_UP, sd=SD_PUT)
    report = dtg.render_plan(plan, dtg.Config())
    assert "| Supply/demand | **PUT**" in report
    assert "lost money in the backtest" in report
