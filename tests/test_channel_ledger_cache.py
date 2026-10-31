"""Recorded broker ledgers survive rotation without hiding other channels."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wtpy.monitor.DataMgr import DataMgr


class ChannelLedgerCacheTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="wt-channel-ledger-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.folder = self.root / "generated/traders/a"
        self.folder.mkdir(parents=True)
        (self.root / "generated/marker.json").write_text(json.dumps({"marks": [], "channels": ["a", "b"]}))
        self.manager = DataMgr.__new__(DataMgr)
        self.manager.__config__ = {"groups": {"g": {"path": str(self.root)}}}
        self.manager.__grp_cache__ = {}

    def write(self, kind, identifiers, path=None):
        path = path or self.folder / (kind+".csv")
        if kind == "trades":
            header = "localid,date,time,code,action,volume,price,tradeid,orderid\n"
            rows = [f"{i},20261025,1792900000000,SHFE.rb.2701,OL,1,3000,T{i},O{i}\n" for i in identifiers]
        else:
            header = "localid,date,time,code,action,volume,traded,price,orderid,canceled,remark\n"
            rows = [f"{i},20261025,1792900000000,SHFE.rb.2701,OL,1,1,3000,O{i},FALSE,test\n" for i in identifiers]
        path.write_text(header+"".join(rows), encoding="gb2312")
        return path

    def ids(self, kind, limit=200):
        method = self.manager.get_channel_trades if kind == "trades" else self.manager.get_channel_orders
        return [row["tradeid" if kind == "trades" else "orderid"].strip() for row in method("g", "a", limit)]

    def test_missing_channel_does_not_erase_another_channels_positions(self):
        expected = {"code": "SHFE.rb.2701", "long": {"newvol": 1}}
        (self.folder / "rtdata.json").write_text(json.dumps({"positions": [expected]}))
        self.assertEqual(self.manager.get_channel_positions("g", "all"), [dict(expected, channel="a")])

    def test_readonly_factory_does_not_open_database(self):
        with patch("sqlite3.connect", side_effect=AssertionError("read-only getter opened DB")):
            manager=DataMgr.from_groups([{"id":"g","path":str(self.root)}])
        self.assertIsNone(manager.get_db())
        self.assertEqual(manager.get_channels("g"),["a","b"])

    def test_shrink_then_append_reads_new_ledger_for_orders_and_trades(self):
        for kind in ("orders", "trades"):
            with self.subTest(kind=kind):
                self.write(kind, [1, 2])
                self.assertEqual(len(self.ids(kind)), 2)
                self.write(kind, [3])
                self.assertEqual(self.ids(kind), [("T" if kind == "trades" else "O")+"3"])
                self.write(kind, [3, 4])
                self.assertEqual(self.ids(kind), [("T" if kind == "trades" else "O")+"3", ("T" if kind == "trades" else "O")+"4"])

    def test_atomic_replacement_same_size_rebuilds_cache(self):
        for kind in ("orders", "trades"):
            with self.subTest(kind=kind):
                path = self.write(kind, [1, 2])
                self.ids(kind)
                replacement = self.write(kind, [3, 4], path.with_suffix(".next"))
                self.assertEqual(path.stat().st_size, replacement.stat().st_size)
                os.replace(replacement, path)
                self.assertEqual(self.ids(kind), [("T" if kind == "trades" else "O")+"3", ("T" if kind == "trades" else "O")+"4"])

    def test_unchanged_file_skips_open_and_limit_still_applies(self):
        for kind in ("orders", "trades"):
            with self.subTest(kind=kind):
                self.write(kind, range(1, 6))
                self.assertEqual(len(self.ids(kind)), 5)
                with patch("builtins.open", side_effect=AssertionError("unchanged ledger was reread")):
                    self.assertEqual(self.ids(kind, 1), [("T" if kind == "trades" else "O")+"5"])

    def test_same_size_rewrite_with_unchanged_last_row_rebuilds_cache(self):
        path = self.write("trades", [1, 2])
        self.ids("trades")
        original = path.stat()
        self.write("trades", [3, 2])
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns+1000000))
        self.assertEqual(path.stat().st_size, original.st_size)
        self.assertEqual(self.ids("trades"), ["T3", "T2"])

    def test_incomplete_append_is_not_consumed_or_duplicated(self):
        path = self.write("trades", [1])
        self.ids("trades")
        row = "2,20261025,1792900000000,SHFE.rb.2701,OL,1,3000,T2,O2"
        with path.open("a", encoding="gb2312") as stream:
            stream.write(row[:20])
        self.assertEqual(self.ids("trades"), ["T1"])
        with path.open("a", encoding="gb2312") as stream:
            stream.write(row[20:]+"\n")
        self.assertEqual(self.ids("trades"), ["T1", "T2"])


if __name__ == "__main__":
    unittest.main()
