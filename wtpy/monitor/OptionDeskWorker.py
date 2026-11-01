"""One pricing worker and one CTP session for a visible selection."""
import json
from pathlib import Path
import sys
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from wtpy.monitor.AssetCtpQuote import emit, run_session
from wtpy.monitor.OptionDesk import OptionDesk
from wtpy.monitor.OptionFrames import publish_frame


def main():
    plan = json.loads(sys.stdin.buffer.readline())
    session = Path.cwd().name
    desk = OptionDesk(plan)
    books = {}
    lock = threading.Lock()
    stopped = threading.Event()

    def receive(**event):
        with lock:
            if "symbol" in event:
                books[event["symbol"]] = dict(event, received=time.time())
            elif event.get("error") or "断开" in event.get("status",""):
                books.clear()
            if "symbol" not in event:
                # Subscription acknowledgements arrive per instrument.
                if event.get("status") != "已订阅，等待交易所快照":
                    emit(**event)
                if "error" in event:
                    stopped.set()

    def watch_parent():
        sys.stdin.buffer.read()
        stopped.set()

    def calculate():
        threading.Thread(target=watch_parent, daemon=True).start()
        while not stopped.is_set():
            with lock:
                latest = books.copy()
            frame = desk.snapshot(latest,session)
            publish_frame(Path.cwd(),frame)
            emit(frame=frame)
            stopped.wait(2)

    run_session(plan["symbols"],plan["mode"],receive,calculate)


if __name__ == "__main__":
    main()
