"""The NinjaScript port (ninjatrader/QqqDailyTarget.cs) keeps the same
defaults as the Python rules it mirrors, so the two can't drift apart.
NinjaTrader isn't available here, so this checks the C# source text."""

import inspect
import re
from pathlib import Path

from qqq_iron_condor.config import DEFAULT_MACRO_EVENT_DATES, Config
from qqq_iron_condor.shares_backtest import simulate_shares

SOURCE = (Path(__file__).resolve().parents[1] / "ninjatrader" / "QqqDailyTarget.cs").read_text()


def _default(name):
    m = re.search(rf"^\s*{name} = ([^;]+);", SOURCE, re.M)
    assert m, f"no default for {name}"
    return float(m.group(1))


def test_skip_dates_match_the_python_macro_calendar():
    block = re.search(r"DefaultSkipDates =(.*?);", SOURCE, re.S).group(1)
    assert set(re.findall(r"\d{4}-\d{2}-\d{2}", block)) == set(DEFAULT_MACRO_EVENT_DATES)


def test_rule_defaults_match_python():
    cfg = Config()
    shares = inspect.signature(simulate_shares).parameters
    assert _default("OrderQuantity") == shares["shares"].default
    assert _default("ProfitTarget") == shares["target"].default
    assert _default("HardStop") == shares["hard_stop"].default
    assert _default("MaxHoldSessions") == shares["max_hold"].default == cfg.daily_target_max_hold_days
    assert _default("FirstHourMinutes") == cfg.daily_target_first_hour_minutes
    assert _default("GapThresholdPct") == cfg.gap_threshold_pct
    assert _default("VixSpikeThreshold") == cfg.vix_spike_threshold
