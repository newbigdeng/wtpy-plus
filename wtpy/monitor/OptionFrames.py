"""File-backed shared memory for a live analysis session and its WT strategies."""
import json
import mmap
import os
from pathlib import Path
import re
import time


def publish_frame(directory, frame):
    data = json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(",",":")).encode()
    # Existing readers finish their old mapping. New opens see one complete generation.
    pending = Path(directory)/"frame.next"
    with pending.open("w+b") as out:
        out.truncate(len(data))
        with mmap.mmap(out.fileno(), len(data), access=mmap.ACCESS_WRITE) as buf:
            buf[:] = data
    os.replace(pending, pending.with_name("frame"))


def read_option_frame(option_root, session, *, mode, max_age=10):
    """Read from the same option volume as the producer; a stopped session cannot be reused."""
    if not re.fullmatch(r"[0-9a-f]{24}", session):
        raise ValueError("分析会话编号无效")
    file = Path(option_root)/"analysis"/session/"frame"
    with file.open("rb") as src, mmap.mmap(src.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        value = json.loads(buf[:])
    if value["mode"] != mode or not 0<=time.time()-value["published"]<=max_age:
        raise ValueError("分析快照已过期或行情环境不一致")
    return value
