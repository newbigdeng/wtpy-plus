"""One current parse per native ledger path, with incremental complete-line reads."""
from collections import OrderedDict
import csv
import json
from pathlib import Path
import threading


class NativeLedgerCache:
    def __init__(self, max_paths=32):
        self.entries = OrderedDict()
        self.max_paths = max_paths
        self.lock = threading.RLock()

    def read(self, path, kind):
        path = Path(path)
        key = (str(path.resolve()), kind)
        with self.lock:
            try:
                stat = path.stat()
            except FileNotFoundError:
                self.entries.pop(key, None)
                return (), False, 0
            identity = (stat.st_dev, stat.st_ino)
            entry = self.entries.get(key)
            if entry and entry["identity"] == identity and entry["size"] == stat.st_size and entry["modified"] == stat.st_mtime_ns:
                self.entries.move_to_end(key)
                return tuple(entry["rows"]), True, entry["invalid"]
            append = (entry and entry["identity"] == identity and stat.st_size > entry["size"])
            if append:
                with path.open("rb") as stream:
                    stream.seek(entry["size"]-len(entry["boundary"]))
                    append = stream.read(len(entry["boundary"])) == entry["boundary"]
            if not append:
                entry = {"identity": identity, "size": 0, "tail": b"", "rows": [],
                         "header": None, "invalid": 0, "boundary": b""}
            with path.open("rb") as stream:
                stream.seek(entry["size"])
                chunk = stream.read()
            lines = (entry["tail"] + chunk).split(b"\n")
            entry["tail"] = lines.pop()
            for raw in lines:
                if not raw.strip():
                    continue
                if kind == "jsonl":
                    try:
                        value = json.loads(raw.decode("utf-8"))
                        if not isinstance(value, dict) or value.get("schema") != 1:
                            entry["invalid"] += 1
                        else:
                            entry["rows"].append(value)
                    except (ValueError, UnicodeDecodeError):
                        entry["invalid"] += 1
                else:
                    cells = next(csv.reader([raw.decode("gb18030", errors="replace")]))
                    if entry["header"] is None:
                        entry["header"] = cells
                    else:
                        entry["rows"].append(dict(zip(entry["header"], cells)))
            entry["size"] += len(chunk)
            entry["boundary"] = (entry["boundary"]+chunk)[-128:]
            entry["modified"] = stat.st_mtime_ns
            self.entries[key] = entry
            self.entries.move_to_end(key)
            while len(self.entries) > self.max_paths:
                self.entries.popitem(last=False)
            # The writer's unfinished final line is retried on its next append.
            return tuple(entry["rows"]), True, entry["invalid"]


ledger_cache = NativeLedgerCache()
