from datetime import date, datetime, timedelta
import unittest

from wtpy.monitor.CNEquityHistory import resample_five, usable


class FiveMinuteTests(unittest.TestCase):
    def bars(self, end):
        return [dict(symbol="RB2701.SHF", bar_time=end-timedelta(minutes=4-i), trade_date=date(2026, 10, 22),
                     open=100+i, high=103+i, low=99+i, close=102+i, volume=i+1,
                     open_interest=200+i, source="sina") for i in range(5)]

    def test_complete_closed_minutes_preserve_ohlc_volume_and_last_interest(self):
        end = datetime(2026, 10, 22, 9, 5)
        rows = resample_five(self.bars(end), {202610220905}, {202610220905: date(2026, 10, 22)})
        self.assertEqual(len(rows), 1)
        self.assertEqual([rows[0][key] for key in ("open", "high", "low", "close", "volume", "open_interest")], [100, 107, 99, 106, 15, 204])
        self.assertEqual(rows[0]["source"], "derived_sina_1m")

    def test_missing_minute_is_not_filled_or_aggregated_across_break(self):
        end = datetime(2026, 10, 22, 10, 35)
        rows = self.bars(end)
        del rows[2]
        rows.append(dict(rows[0], bar_time=datetime(2026, 10, 22, 10, 15)))
        self.assertEqual(resample_five(rows, {202610221035}, {202610221035: date(2026, 10, 22)}), [])

    def test_midnight_aggregation_keeps_exchange_trading_day(self):
        end = datetime(2026, 10, 24, 0, 0)
        rows = resample_five(self.bars(end), {202610240000}, {202610240000: date(2026, 10, 26)})
        self.assertEqual(rows[0]["trade_date"], date(2026, 10, 26))
        self.assertEqual(rows[0]["bar_time"], end)

    def test_untraded_rows_without_ohlc_are_not_invented(self):
        rows = self.bars(datetime(2026, 10, 22, 9, 5))
        rows[2]["open"] = None
        self.assertEqual(len(usable(rows)), 4)


if __name__ == "__main__":
    unittest.main()
