// QQQ Daily $ Target -- NinjaTrader 8 strategy (NinjaScript).
//
// A port of qqq_iron_condor/shares_backtest.py: the daily $ target's
// first-hour signal, traded on the underlying. NinjaTrader 8 doesn't trade
// options, so this trades QQQ shares (800 shares ~ 10 contracts at 0.80
// delta, the same stand-in the shares backtest uses) or, with
// PointsPerQqqDollar set, NQ/MNQ futures.
//
// Rules (defaults match the shares backtest's live rules and config.py):
//   - Skip the day if the opening gap vs the prior close is >= 0.8%, the
//     prior VIX close is >= 20, or it's a listed FOMC/CPI day.
//   - Direction: the first hour's close vs its open (9:30-10:30 ET). Up ->
//     long, down -> short, flat -> no trade. Enter at market at 10:30.
//   - Exit on the first of: +$1.25/share (limit order); -$5/share hard stop
//     (stop order); an hourly *close* past the first hour's low (long) /
//     high (short); or the close of the 5th session. A later session that
//     opens past that level exits at the open.
//   - One position at a time, and no new entry on a day that began with one
//     open.
//
// Run it on 1-minute bars with RTH trading hours ("US Equities RTH" for
// QQQ), so each session starts at 9:30 ET. Hourly closes are read from the
// 1-minute bar ending at :30 past each hour. The session's last hourly close
// and the time stop are checked on the bar ending 1 minute before the
// close, so those exits fill in the final minute.
//
// Unlike the Python tools, this places orders when enabled. Run it on the
// Sim101 account first. Not financial advice -- see README.md.

#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.NinjaScript;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
	public class QqqDailyTarget : Strategy
	{
		// Same days as DEFAULT_MACRO_EVENT_DATES in qqq_iron_condor/config.py
		// (2026 FOMC decisions and CPI releases). Extend both for 2027.
		private const string DefaultSkipDates =
			"2026-01-13,2026-01-28,2026-02-13,2026-03-11,2026-03-18,2026-04-10,2026-04-29," +
			"2026-05-12,2026-06-10,2026-06-17,2026-07-14,2026-07-29,2026-08-12,2026-09-11," +
			"2026-09-16,2026-10-14,2026-10-28,2026-11-10,2026-12-09,2026-12-10";

		private const string LongEntry = "First hour long";
		private const string ShortEntry = "First hour short";

		private SessionIterator sessions;
		private DateTime sessionBegin = DateTime.MinValue;
		private DateTime sessionEnd;
		private HashSet<DateTime> skipDates;
		private bool warnedNoVix;

		// Per session, reset on its first bar.
		private double gapPct, priorVix;
		private double fhOpen, fhHigh, fhLow, fhClose;
		private bool decided, finalChecked, carriedOver, openAtLastBar;

		// Per position.
		private double level = double.NaN; // first-hour low (long) / high (short); NaN = no level stop
		private bool levelValidated, exitPending;
		private int sessionsHeld;

		protected override void OnStateChange()
		{
			if (State == State.SetDefaults)
			{
				Name = "QqqDailyTarget";
				Description = "QQQ daily $ target: first-hour direction, +$1.25/share target, level stop on an hourly close, -$5/share hard stop, 5-session time stop.";
				Calculate = Calculate.OnBarClose;
				EntriesPerDirection = 1;
				EntryHandling = EntryHandling.AllEntries;
				IsExitOnSessionCloseStrategy = false; // positions are held up to 5 sessions
				StartBehavior = StartBehavior.WaitUntilFlat;
				BarsRequiredToTrade = 1;
				IsInstantiatedOnEachOptimizationIteration = true;

				OrderQuantity = 800;
				PointsPerQqqDollar = 1.0;
				FirstHourMinutes = 60;
				ProfitTarget = 1.25;
				HardStop = 5.0;
				UseLevelStop = true;
				MaxHoldSessions = 5;
				GapThresholdPct = 0.8;
				UseVixFilter = true;
				VixSymbol = "^VIX";
				VixSpikeThreshold = 20.0;
				SkipDates = DefaultSkipDates;
			}
			else if (State == State.Configure)
			{
				if (UseVixFilter)
					AddDataSeries(VixSymbol, BarsPeriodType.Day, 1);
			}
			else if (State == State.DataLoaded)
			{
				sessions = new SessionIterator(Bars);
				skipDates = ParseDates(SkipDates);
			}
		}

		protected override void OnBarUpdate()
		{
			if (BarsInProgress != 0)
				return;
			if (Bars.IsFirstBarOfSession)
				StartSession();
			if (sessionBegin == DateTime.MinValue)
				return; // data started mid-session

			int minutes = (int)Math.Round((Time[0] - sessionBegin).TotalMinutes);
			if (minutes <= FirstHourMinutes)
			{
				fhHigh = Math.Max(fhHigh, High[0]);
				fhLow = Math.Min(fhLow, Low[0]);
				fhClose = Close[0];
			}
			bool lastMinute = !finalChecked && Time[0] >= sessionEnd.AddMinutes(-1);
			if (lastMinute)
				finalChecked = true;

			if (Position.MarketPosition == MarketPosition.Flat)
			{
				exitPending = false;
				if (!decided && minutes >= FirstHourMinutes)
					Decide();
			}
			else if (!exitPending)
				Manage(minutes % 60 == 0 || lastMinute, lastMinute);
			openAtLastBar = Position.MarketPosition != MarketPosition.Flat;
		}

		private void StartSession()
		{
			sessions.GetNextSession(Time[0], true);
			sessionBegin = sessions.ActualSessionBegin;
			sessionEnd = sessions.ActualSessionEnd;
			if (CurrentBar == 0 && (sessionEnd - sessionBegin).TotalHours > 8)
				Print(Name + ": sessions are longer than 8 hours -- use RTH trading hours so the first hour starts at 9:30 ET.");

			gapPct = CurrentBar > 0 ? Math.Abs(Open[0] / Close[1] - 1.0) * 100.0 : double.NaN;
			priorVix = double.NaN;
			if (UseVixFilter && CurrentBars[1] >= 0)
			{
				int back = Times[1][0].Date < sessionBegin.Date ? 0 : 1; // never today's unfinished VIX bar
				if (CurrentBars[1] >= back)
					priorVix = Closes[1][back];
			}
			if (UseVixFilter && double.IsNaN(priorVix) && CurrentBar > 0 && !warnedNoVix)
			{
				warnedNoVix = true;
				Print(Name + ": no VIX data for " + VixSymbol + " -- the VIX skip rule is off until it loads. Set your feed's VIX symbol, or turn the filter off.");
			}

			fhOpen = Open[0];
			fhHigh = double.MinValue;
			fhLow = double.MaxValue;
			decided = finalChecked = false;
			// From the prior bar, not Position now: a carried position whose
			// target or stop fills at this open still rules out a new entry today.
			carriedOver = openAtLastBar;
			if (Position.MarketPosition == MarketPosition.Flat)
				return;
			sessionsHeld++;
			if (!double.IsNaN(level) && (IsLong ? Open[0] < level : Open[0] > level))
				ExitPosition("Gap past level");
		}

		private void Decide()
		{
			decided = true;
			if (carriedOver)
				return; // a position carried into today closed today: the next entry is tomorrow

			double move = fhClose - fhOpen;
			string skip = SkipReason();
			if (skip == null && move == 0)
				skip = "flat first hour";
			if (skip != null)
			{
				Note("no trade (" + skip + ")");
				return;
			}

			bool goLong = move > 0;
			level = UseLevelStop ? (goLong ? fhLow : fhHigh) : double.NaN;
			levelValidated = false;
			sessionsHeld = 1;
			SetProfitTarget(CalculationMode.Ticks, ToTicks(ProfitTarget));
			SetStopLoss(CalculationMode.Ticks, ToTicks(HardStop));
			Note(string.Format(CultureInfo.InvariantCulture, "{0} {1} (first hour {2:+0.00;-0.00}, level stop {3})",
				goLong ? "BUY" : "SELL SHORT", OrderQuantity, move, double.IsNaN(level) ? "off" : level.ToString("0.00", CultureInfo.InvariantCulture)));
			if (goLong)
				EnterLong(OrderQuantity, LongEntry);
			else
				EnterShort(OrderQuantity, ShortEntry);
		}

		private string SkipReason()
		{
			if (double.IsNaN(gapPct))
				return "no prior close";
			if (gapPct >= GapThresholdPct)
				return string.Format(CultureInfo.InvariantCulture, "opening gap {0:0.00}% >= {1:0.0}%", gapPct, GapThresholdPct);
			if (!double.IsNaN(priorVix) && priorVix >= VixSpikeThreshold)
				return string.Format(CultureInfo.InvariantCulture, "prior VIX close {0:0.0} >= {1:0.0}", priorVix, VixSpikeThreshold);
			if (skipDates.Contains(sessionBegin.Date))
				return "FOMC/CPI day";
			return null;
		}

		private void Manage(bool hourClose, bool lastMinute)
		{
			if (!levelValidated)
			{
				// Filled already past the level (a fast 10:30 open): no level stop.
				if (!double.IsNaN(level) && (IsLong ? level >= Position.AveragePrice : level <= Position.AveragePrice))
					level = double.NaN;
				levelValidated = true;
			}
			if (hourClose && !double.IsNaN(level) && (IsLong ? Close[0] < level : Close[0] > level))
				ExitPosition("Level stop");
			else if (lastMinute && sessionsHeld >= MaxHoldSessions)
				ExitPosition("Time stop");
		}

		private void ExitPosition(string reason)
		{
			exitPending = true;
			Note(string.Format("{0} after {1} session(s)", reason, sessionsHeld));
			if (IsLong)
				ExitLong(reason, LongEntry);
			else
				ExitShort(reason, ShortEntry);
		}

		private bool IsLong
		{
			get { return Position.MarketPosition == MarketPosition.Long; }
		}

		private int ToTicks(double qqqDollars)
		{
			return Math.Max(1, (int)Math.Round(qqqDollars * PointsPerQqqDollar / TickSize));
		}

		private void Note(string message)
		{
			Print(string.Format("{0} {1:yyyy-MM-dd HH:mm} {2}", Name, Time[0], message));
		}

		private HashSet<DateTime> ParseDates(string list)
		{
			var dates = new HashSet<DateTime>();
			foreach (string item in (list ?? string.Empty).Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries))
			{
				DateTime date;
				if (DateTime.TryParseExact(item, "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out date))
					dates.Add(date);
				else
					Print(Name + ": ignoring skip date '" + item + "' (use yyyy-MM-dd)");
			}
			return dates;
		}

		#region Properties
		[NinjaScriptProperty]
		[Range(1, int.MaxValue)]
		[Display(Name = "Quantity", Description = "Shares or contracts. 800 QQQ shares ~ 10 calls at 0.80 delta; 1 NQ or 10 MNQ is about the same exposure.", Order = 1, GroupName = "1. Size")]
		public int OrderQuantity { get; set; }

		[NinjaScriptProperty]
		[Range(0.0001, double.MaxValue)]
		[Display(Name = "Points per QQQ $1", Description = "Converts the $/share levels to this instrument's points: 1 for QQQ, about 41 for NQ/MNQ (NQ price / QQQ price).", Order = 2, GroupName = "1. Size")]
		public double PointsPerQqqDollar { get; set; }

		[NinjaScriptProperty]
		[Range(1, 390)]
		[Display(Name = "First hour (minutes)", Description = "Direction = this window's close vs the session open; entry when it ends.", Order = 1, GroupName = "2. Entry")]
		public int FirstHourMinutes { get; set; }

		[NinjaScriptProperty]
		[Range(0.01, double.MaxValue)]
		[Display(Name = "Profit target ($/share)", Order = 1, GroupName = "3. Exits")]
		public double ProfitTarget { get; set; }

		[NinjaScriptProperty]
		[Range(0.01, double.MaxValue)]
		[Display(Name = "Hard stop ($/share)", Order = 2, GroupName = "3. Exits")]
		public double HardStop { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "Level stop on hourly close", Description = "Exit when an hourly bar closes past the first hour's low (long) / high (short).", Order = 3, GroupName = "3. Exits")]
		public bool UseLevelStop { get; set; }

		[NinjaScriptProperty]
		[Range(1, int.MaxValue)]
		[Display(Name = "Time stop (sessions)", Description = "Close at the end of this session, counting the entry day as 1.", Order = 4, GroupName = "3. Exits")]
		public int MaxHoldSessions { get; set; }

		[NinjaScriptProperty]
		[Range(0.0, double.MaxValue)]
		[Display(Name = "Max opening gap (%)", Order = 1, GroupName = "4. Skip days")]
		public double GapThresholdPct { get; set; }

		[NinjaScriptProperty]
		[Display(Name = "VIX filter", Order = 2, GroupName = "4. Skip days")]
		public bool UseVixFilter { get; set; }

		[Display(Name = "VIX symbol", Description = "Your data feed's VIX index symbol.", Order = 3, GroupName = "4. Skip days")]
		public string VixSymbol { get; set; }

		[NinjaScriptProperty]
		[Range(0.0, double.MaxValue)]
		[Display(Name = "VIX spike threshold", Description = "Skip when the prior VIX close is at or above this.", Order = 4, GroupName = "4. Skip days")]
		public double VixSpikeThreshold { get; set; }

		[Display(Name = "FOMC/CPI dates", Description = "Comma-separated yyyy-MM-dd days to skip.", Order = 5, GroupName = "4. Skip days")]
		public string SkipDates { get; set; }
		#endregion
	}
}
