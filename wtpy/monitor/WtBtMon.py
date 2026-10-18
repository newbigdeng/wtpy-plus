import os
import json
import subprocess
import platform
import sys
import psutil
import hashlib
import datetime
import shutil
import json
import threading
import time
import logging
import tempfile
import csv

from wtpy import WtDtServo
from .WtLogger import WtLogger
from .EventReceiver import BtEventReceiver, BtEventSink
from .BacktestTemplates import STRATEGY_TYPES, normalize_strategy_type, strategy_template, runner_template

def isWindows():
    if "windows" in platform.system().lower():
        return True

    return False

def md5_str(v:str) -> str:
    return hashlib.md5(v.encode()).hexdigest()

def gen_btid(user:str, straid:str) -> str:
    now = datetime.datetime.now()
    s = user + "_" + straid + "_" + str(now.timestamp())
    return md5_str(s)

def gen_straid(user:str) -> str:
    now = datetime.datetime.now()
    s = user + "_" + str(now.timestamp())
    return md5_str(s)

class BtTaskSink:

    def __init__(self):
        pass

    def on_start(self, user:str, straid:str, btid:str):
        pass

    def on_stop(self, user:str, straid:str, btid:str):
        pass

    def on_state(self, user:str, straid:str, btid:str, statInfo:dict):
        pass

    def on_fund(self, user:str, straid:str, btid:str, fundInfo:dict):
        pass

class WtBtTask(BtEventSink):
    """Own the child process until it is reaped, including failed and stopped runs."""
    def __init__(self, user, straid, btid, folder, logger=None, sink=None):
        self.user, self.straid, self.btid = user, straid, btid
        self.folder = os.path.abspath(folder)
        self.logger = logger or logging.getLogger(__name__)
        self.sink = sink
        self._mq_url = self.ipc_url(btid)
        self._state, self._procid = 0, None
        self._process = self._evt_receiver = self._log = None
        self.returncode = None
        self.stop_requested = False
        self.watcher = None
        self._done = threading.Event()

    @staticmethod
    def ipc_url(btid):
        # Unix domain socket paths are limited; a deeply nested deployment path cannot be used.
        return "ipc://" + os.path.join(tempfile.gettempdir(), "wtpy-bt-" + btid + ".ipc")

    @property
    def cmd_line(self):
        return sys.executable + " " + os.path.join(self.folder, "runBT.py")

    def _receive(self):
        self._evt_receiver = BtEventReceiver(
            url=self._mq_url, topics=["BT_EVENT", "BT_STATE", "BT_FUND"],
            logger=self.logger, sink=self)
        self._evt_receiver.run()

    def _finish(self):
        try:
            if self._evt_receiver is not None:
                self._evt_receiver.release()
                self._evt_receiver = None
            if self._log is not None:
                self._log.close()
                self._log = None
            self._state = 2
            if self.sink is not None:
                self.sink.on_stop(self.user, self.straid, self.btid)
        finally:
            self._done.set()

    def _check(self):
        try:
            if self._process is not None:
                self.returncode = self._process.wait()
            else:
                # A recovered process belongs to the previous monitor.
                try:
                    self.returncode = psutil.Process(self._procid).wait()
                except psutil.NoSuchProcess:
                    self.returncode = None
        finally:
            self._finish()

    def run(self):
        if self._state != 0:
            return
        try:
            self._receive()
            self._log = open(os.path.join(self.folder, "task.log"), "ab")
            environment = os.environ.copy()
            package_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            environment["PYTHONPATH"] = package_root + os.pathsep + environment.get("PYTHONPATH", "")
            self._process = subprocess.Popen(
                [sys.executable, os.path.join(self.folder, "runBT.py")],
                cwd=self.folder, stdout=self._log, stderr=subprocess.STDOUT, env=environment)
            self._procid = self._process.pid
            self._state = 1
            self.watcher = threading.Thread(target=self._check, name=self.btid, daemon=True)
            self.watcher.start()
        except Exception:
            self.returncode = -1
            self.logger.exception("Backtest %s failed to start", self.btid)
            self._finish()

    def is_running(self, pids):
        if self._process is not None:
            return self._process.poll() is None
        expected = [sys.executable, os.path.join(self.folder, "runBT.py")]
        for pid in pids:
            try:
                process = psutil.Process(pid)
                if process.cmdline() != expected or process.status() == psutil.STATUS_ZOMBIE:
                    continue
                self._procid, self._state = pid, 1
                self._receive()
                self.watcher = threading.Thread(target=self._check, name=self.btid, daemon=True)
                self.watcher.start()
                return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return False

    def stop(self):
        if self._state != 1:
            return False
        self.stop_requested = True
        try:
            process = psutil.Process(self._procid)
            process.terminate()
            if not self._done.wait(10):
                process.kill()
                self._done.wait(3)
        except psutil.NoSuchProcess:
            self._done.wait(3)
        return self._done.is_set()

    def on_begin(self):
        if self.sink is not None:
            self.sink.on_start(self.user, self.straid, self.btid)

    def on_finish(self):
        # Replay completion precedes report writing and process exit.
        pass

    def on_state(self, statInfo):
        if self.sink is not None:
            self.sink.on_state(self.user, self.straid, self.btid, statInfo)

    def on_fund(self, fundInfo):
        if self.sink is not None:
            self.sink.on_fund(self.user, self.straid, self.btid, fundInfo)


class WtBtMon(BtTaskSink):
    '''
    回测管理器
    '''
    def __init__(self, deploy_folder:str, dtServo:WtDtServo = None, logger:WtLogger = None):
        self.path = deploy_folder
        self.user_stras = dict()
        self.user_bts = dict()
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self.dt_servo = dtServo

        self.task_infos = dict()
        self.task_map = dict()

        self.__load_tasks__()

    def __load_user_data__(self, user:str):
        folder = os.path.join(self.path, user)
        if not os.path.exists(folder):
            os.mkdir(folder)

        filepath = os.path.join(folder, "marker.json")
        if not os.path.exists(filepath):
            return False

        f = open(filepath, "r")
        content = f.read()
        f.close()

        obj = json.loads(content)
        self.user_stras[user] = obj["strategies"]
        self.user_bts[user] = obj["backtests"]
        return True

    def __save_user_data__(self, user):
        folder = os.path.join(self.path, user)
        if not os.path.exists(folder):
            os.mkdir(folder)

        obj = {
            "strategies":{},
            "backtests":{}
        }

        if user in self.user_stras:
            obj["strategies"] = self.user_stras[user]

        if user in self.user_bts:
            obj["backtests"] = self.user_bts[user]

        filepath = os.path.join(folder, "marker.json")
        with self._lock:
            temporary = filepath + ".tmp"
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(obj, stream, indent=4, ensure_ascii=False)
            os.replace(temporary, filepath)
        return True

    def get_strategies(self, user:str) -> list:
        if user not in self.user_stras:
            bSucc = self.__load_user_data__(user)
        
            if not bSucc:
                return []

        ay = list()
        for straid in self.user_stras[user]:
            item = self.user_stras[user][straid]
            item.setdefault("strategy_type", "cta")
            ay.append(item)
        return ay

    def add_strategy(self, user:str, name:str, strategy_type:str="cta") -> dict:
        strategy_type = normalize_strategy_type(strategy_type)
        if user not in self.user_stras:
            self.__load_user_data__(user)

        if user not in self.user_stras:
            self.user_stras[user] = dict()

        straid = gen_straid(user)
        self.user_stras[user][straid] = {
            "id":straid,
            "name":name,
            "strategy_type":strategy_type,
            "perform":{
                "days": 0,
                "total_return": 0,
                "annual_return": 0,
                "win_rate": 0,
                "max_falldown": 0,
                "max_profratio": 0,
                "std": 0,
                "down_std": 0,
                "sharpe_ratio": 0,
                "sortino_ratio": 0,
                "calmar_ratio": 0
            }
        }

        folder = os.path.join(self.path, user, straid)
        if not os.path.exists(folder):
            os.mkdir(folder)

        fname = os.path.join(folder, "MyStrategy.py")
        with open(fname, "w", encoding="utf-8") as stream:
            stream.write(strategy_template(strategy_type))

        self.__save_user_data__(user)

        return self.user_stras[user][straid]

    def rename_strategy(self, user, straid, name):
        name = name.strip()
        if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
            raise ValueError("策略名称须为1至80个字符，不含换行")
        with self._lock:
            if not self.has_strategy(user, straid):
                raise ValueError("策略不存在")
            self.user_stras[user][straid]["name"] = name
            self.__save_user_data__(user)
        return name

    def del_strategy(self, user:str, straid:str):
        with self._lock:
            if user not in self.user_bts and not self.__load_user_data__(user):
                return False
            if straid not in self.user_stras.get(user, {}):
                return True
            if any(info.get("user") == user and info.get("straid") == straid for info in self.task_infos.values()):
                return False
            folder = os.path.join(self.path, user, straid)
            removed_bts = [bid for bid, info in self.user_bts[user].items()
                           if info.get("straid") == straid or os.path.isdir(os.path.join(folder, "backtests", bid))]
            if os.path.isdir(folder):
                deleted = os.path.join(self.path, user, ".del")
                os.makedirs(deleted, exist_ok=True)
                destination = os.path.join(deleted, straid)
                if os.path.exists(destination):
                    destination += "-" + datetime.datetime.now().strftime("%Y%m%d%H%M%S%f")
                shutil.move(folder, destination)
            for bid in removed_bts:
                self.user_bts[user].pop(bid, None)
            self.user_stras[user].pop(straid)
            self.__save_user_data__(user)
            return True

    def has_strategy(self, user:str, straid:str, btid:str = None) -> bool:
        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return False

        if btid is None:
            return straid in self.user_stras[user]
        else:
            return straid in self.user_stras.get(user, {}) and btid in self.user_bts[user] and (
                self.user_bts[user][btid].get("straid") == straid or
                os.path.isdir(os.path.join(self.path, user, straid, "backtests", btid)))

    def get_strategy_code(self, user:str, straid:str, btid:str = None) -> str:
        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return None

        if btid is None:
            path = os.path.join(self.path, user, straid, "MyStrategy.py")
            if not os.path.exists(path):
                return None

            f = open(path, "r", encoding="UTF-8")
            content = f.read()
            f.close()
            return content
        else:
            thisBts = self.user_bts[user]
            if btid not in thisBts:
                return None

            if not self.has_strategy(user, straid, btid):
                return None
            bt_path = os.path.join(self.path, "%s/%s/backtests/%s/MyStrategy.py" % (user, straid, btid))
            f = open(bt_path, "r")
            content = f.read()
            f.close()
            return content

    def set_strategy_code(self, user:str, straid:str, content:str) -> bool:
        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return False

        path = os.path.join(self.path, user, straid, "MyStrategy.py")
        if not os.path.exists(path):
            return None

        f = open(path, "w", encoding="UTF-8")
        f.write(content)
        f.close()
        return True

    def get_backtests(self, user:str, straid:str) -> list:
        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return None

        if user not in self.user_bts:
            return None

        ay = list()
        for btid, info in self.user_bts[user].items():
            if info.get("straid") == straid or os.path.isdir(os.path.join(self.path, user, straid, "backtests", btid)):
                ay.append(info)

        return ay

    def del_backtest(self, user:str, btid:str):
        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return

        if user not in self.user_bts:
            return

        if btid in self.user_bts[user]:
            self.user_bts[user].pop(btid)
            self.logger.info("Backtest %s of %s deleted" % (btid, user))

            self.__save_user_data__(user)

    def get_bt_funds(self, user:str, straid:str, btid:str) -> list:
        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return None

        thisBts = self.user_bts[user]
        if btid not in thisBts:
            return None

        filename = "%s/%s/backtests/%s/outputs_bt/%s/funds.csv" % (user, straid, btid, btid)
        filename = os.path.join(self.path, filename)
        if not os.path.exists(filename):
            return None

        f = open(filename, "r")
        lines = f.readlines()
        f.close()
        lines = lines[1:]

        funds = list()
        for line in lines:
            cells = line.split(",")
            if len(cells) > 10:
                continue

            tItem = {
                "date": int(cells[0]),
                "closeprofit": float(cells[1]),
                "dynprofit": float(cells[2]),
                "dynbalance": float(cells[3]),
                "fee": 0
            }

            if len(cells) > 4:
                tItem["fee"] = float(cells[4])

            funds.append(tItem)
        
        return funds

    def _read_result_rows(self, user, straid, btid, filename):
        if not self.has_strategy(user, straid, btid):
            return None
        path = os.path.join(self.path, user, straid, "backtests", btid, "outputs_bt", btid, filename)
        if not os.path.isfile(path):
            return None
        with open(path, "r", encoding="utf-8-sig", newline="") as stream:
            # CTA, HFT and SEL order their tag/fee columns differently.
            # Read their actual headers instead of guessing column positions.
            reader = csv.DictReader(stream, skipinitialspace=True)
            rows = []
            try:
                fields = reader.fieldnames or []
                for row in reader:
                    extra = row.pop(None, None)
                    if extra:
                        # Native writers historically omit CSV escaping for tags. Only a
                        # single free-text tag at the end has an unambiguous recovery.
                        last = fields[-1] if fields else ""
                        text_fields = {"tag", "usertag", "entertag", "exittag"}
                        if last in {"tag", "usertag"} and not text_fields.intersection(fields[:-1]):
                            row[last] = ",".join([row.get(last, ""), *extra])
                        else:
                            raise ValueError("回测CSV列数异常，标签含未转义分隔符，无法可靠读取：" + filename)
                    if any(value is None for value in row.values()):
                        raise ValueError("回测CSV缺少字段：" + filename)
                    rows.append({str(key).strip(): value.strip() for key, value in row.items()})
            except csv.Error as error:
                raise ValueError("回测CSV格式损坏：" + filename) from error
            return rows

    def get_bt_trades(self, user:str, straid:str, btid:str) -> list:
        rows = self._read_result_rows(user, straid, btid, "trades.csv")
        if rows is None:
            return None
        return [{"code": row["code"], "time": int(row["time"]),
                 "direction": row["direct"], "offset": row["action"],
                 "price": float(row["price"]), "volume": float(row["qty"]),
                 "tag": row.get("tag", row.get("usertag", "")), "fee": float(row.get("fee") or 0)}
                for row in rows]

    def get_bt_rounds(self, user:str, straid:str, btid:str) -> list:
        rows = self._read_result_rows(user, straid, btid, "closes.csv")
        if rows is None:
            return None
        return [{"code": row["code"], "direct": row["direct"],
                 "opentime": int(row["opentime"]), "openprice": float(row["openprice"]),
                 "closetime": int(row["closetime"]), "closeprice": float(row["closeprice"]),
                 "qty": float(row["qty"]), "profit": float(row["profit"]),
                 "maxprofit": float(row["maxprofit"]), "maxloss": float(row["maxloss"]),
                 "entertag": row.get("entertag", ""), "exittag": row.get("exittag", "")}
                for row in rows]

    def get_bt_signals(self, user:str, straid:str, btid:str) -> list:
        rows = self._read_result_rows(user, straid, btid, "signals.csv")
        if rows is None:
            return None
        items = []
        for row in rows:
            if "target" in row:
                items.append({"code": row["code"], "target": float(row["target"]),
                              "sigprice": float(row["sigprice"]), "gentime": row["gentime"],
                              "tag": row.get("usertag", row.get("tag", "")), "source": "target_position"})
            else:
                # HFT records execution events, with no symbol in this CSV.
                # Keep the missing symbol explicit; do not infer one for a multi-symbol strategy.
                items.append({"code": "", "target": float(row["position"]),
                              "sigprice": float(row["price"]), "gentime": row["time"],
                              "tag": row.get("action", ""), "source": "hft_execution"})
        return items

    def get_bt_summary(self, user:str, straid:str, btid:str) -> list:
        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return None

        thisBts = self.user_bts[user]
        if btid not in thisBts:
            return None

        filename = "%s/%s/backtests/%s/outputs_bt/%s/summary.json" % (user, straid, btid, btid)
        filename = os.path.join(self.path, filename)
        if not os.path.exists(filename):
            return None

        f = open(filename, "r")
        content = f.read()
        f.close()

        obj = json.loads(content)
        return obj

    def get_bt_state(self, user:str, straid:str, btid:str) -> dict:
        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return None

        thisBts = self.user_bts[user]
        if btid not in thisBts:
            return None

        filename = "%s/%s/backtests/%s/outputs_bt/%s/btenv.json" % (user, straid, btid, btid)
        filename = os.path.join(self.path, filename)
        if not os.path.exists(filename):
            return None

        f = open(filename, "r")
        content = f.read()
        f.close()

        thisBts[btid]["state"] = json.loads(content)

        return thisBts[btid]["state"]

    def update_bt_state(self, user:str, straid:str, btid:str, stateObj:dict):
        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return None

        thisBts = self.user_bts[user]
        if btid not in thisBts:
            return None

        thisBts[btid]["state"] = stateObj

    def get_bt_kline(self, user:str, straid:str, btid:str) -> list:
        if self.dt_servo is None:
            return None

        if user not in self.user_bts:
            bSucc = self.__load_user_data__(user)

            if not bSucc:
                return None
        
        btState = self.get_bt_state(user, straid, btid)
        if btState is None:
            return None

        thisBts = self.user_bts[user]
        if "kline" not in thisBts[btid]:
            code = btState["code"]
            period = btState["period"]
            stime = btState["stime"]
            etime = btState["etime"]
            barList = self.dt_servo.get_bars(stdCode=code, period=period, fromTime=stime, endTime=etime)
            if barList is None:
                return None

            bars = barList.to_df().to_dict(orient="records")
            for bar in bars:
                bar["time"] = bar["bartime"]
                bar["date"] = int(bar["bartime"]) if len(str(bar["bartime"])) == 8 else int(bar["bartime"]) // 10000
            thisBts[btid]["kline"] = bars

        return thisBts[btid]["kline"]

    def get_backtest_detail(self, user, straid, btid):
        if not self.has_strategy(user, straid, btid):
            return None
        info = self.user_bts[user][btid]
        folder = os.path.join(self.path, user, straid, "backtests", btid)
        log_path = os.path.join(folder, "task.log")
        logs = ""
        if os.path.isfile(log_path):
            with open(log_path, "rb") as stream:
                stream.seek(0, os.SEEK_END)
                stream.seek(max(0, stream.tell() - 16384))
                logs = stream.read().decode("utf-8", errors="replace")
        errors = [line for line in logs.splitlines() if any(marker in line for marker in ("Error:", "Exception:", "Traceback"))]
        return {"id": btid, "strategy_type": info.get("strategy_type", "cta"),
                "status": info.get("status", "unknown"), "returncode": info.get("returncode"),
                "state": self.get_bt_state(user, straid, btid) or info.get("state", {}),
                "summary": self.get_bt_summary(user, straid, btid) or info.get("perform", {}),
                "logs": logs, "error": errors[-1] if errors else ""}

    def run_backtest(self, user:str, straid:str, fromTime:int, endTime:int, capital:float, slippage:int=0,
                     fill_model:str="causal_touch", event_delay:int=0, participation_rate:float=0) -> dict:
        from ..wrapper.WtBtWrapper import validate_cta_fill_config
        validate_cta_fill_config(fill_model, event_delay, participation_rate)
        with self._lock:
            if user not in self.user_bts:
                self.__load_user_data__(user)

            if user not in self.user_bts:
                self.user_bts[user] = dict()
            
            strategy_type = normalize_strategy_type(self.user_stras[user][straid].get("strategy_type", "cta"))
            if strategy_type != "cta" and (fill_model != "causal_touch" or event_delay != 0 or participation_rate != 0):
                raise ValueError("成交模型参数仅适用于CTA快速回测")
            btid = gen_btid(user, straid)

            # 生成回测目录
            folder = os.path.join(self.path, user, straid, "backtests")
            if not os.path.exists(folder):
                os.mkdir(folder)
        
            folder = os.path.join(folder, btid)
            os.mkdir(folder)

            # 将策略文件复制到该目录下
            old_path = os.path.join(self.path, user, straid, "MyStrategy.py")
            new_path = os.path.join(folder, "MyStrategy.py")
            shutil.copyfile(old_path, new_path)

            # 初始化目录下的配置文件
            old_path = os.path.join(self.path, "template/configbt.json")
            new_path = os.path.join(folder, "configbt.json")
            f = open(old_path, "r", encoding="UTF-8")
            content = f.read()
            f.close()
            content = content.replace("$BTID$", btid).replace("$TASKDIR$", folder).replace("$MQURL$", WtBtTask.ipc_url(btid))
            config = json.loads(content)
            config.setdefault("env", {})["mocker"] = strategy_type
            content = json.dumps(config, ensure_ascii=False, indent=2)

            f = open(new_path, "w", encoding="UTF-8")
            f.write(content)
            f.close()

            old_path = os.path.join(self.path, "template/logcfgbt.json")
            new_path = os.path.join(folder, "logcfgbt.json")
            shutil.copyfile(old_path, new_path)

            old_path = os.path.join(self.path, "template/fees.json")
            new_path = os.path.join(folder, "fees.json")
            shutil.copyfile(old_path, new_path)

            new_path = os.path.join(folder, "runBT.py")
            content = runner_template()
            commodity_file = config.get("replayer", {}).get("basefiles", {}).get("commodity", "")
            basefiles = os.path.dirname(os.path.abspath(os.path.join(folder, commodity_file)))
            content = content.replace("$BASEFILES$", basefiles.replace("\\", "/") + "/")
            content = content.replace("$STRATEGY_TYPE$", strategy_type)
            content = content.replace("$FROMTIME$", str(fromTime))
            content = content.replace("$ENDTIME$", str(endTime))
            content = content.replace("$STRAID$", btid)
            content = content.replace("$CAPITAL$", str(capital))
            content = content.replace("$SLIPPAGE$", str(slippage))
            content = content.replace("$FILL_MODEL$", fill_model)
            content = content.replace("$EVENT_DELAY$", str(event_delay))
            content = content.replace("$PARTICIPATION_RATE$", repr(float(participation_rate)))

            f = open(new_path, "w", encoding="UTF-8")
            f.write(content)
            f.close()

            btInfo = {
                "id":btid,
                "straid":straid,
                "strategy_type":strategy_type,
                "status":"running",
                "returncode":None,
                "capital":capital,
                "fill_config":({"fill_model":fill_model,"event_delay":event_delay,"participation_rate":participation_rate} if strategy_type=="cta" else None),
                "runtime":datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S"),
                "state":{
                    "code": "",
                    "period": "",
                    "stime": fromTime,
                    "etime": endTime,
                    "progress": 0,
                    "elapse": 0
                },
                "perform":{
                    "days": 0,
                    "total_return": 0,
                    "annual_return": 0,
                    "win_rate": 0,
                    "max_falldown": 0,
                    "max_profratio": 0,
                    "std": 0,
                    "down_std": 0,
                    "sharpe_ratio": 0,
                    "sortino_ratio": 0,
                    "calmar_ratio": 0
                }
            }

            self.user_bts[user][btid] = btInfo

            self.__save_user_data__(user)

            # 添加
            btTask = WtBtTask(user, straid, btid, folder, self.logger, sink=self)
            self.task_map[btid] = btTask

            # 这里还需要记录一下回测的任务，不然如果重启就恢复不了了
            taskInfo = {
                "user":user,
                "straid":straid,
                "btid":btid,
                "folder":folder
            }
            self.task_infos[btid]= taskInfo
            self.__save_tasks__()
            btTask.run()

            return btInfo

    def __update_bt_result__(self, user:str, straid:str, btid:str):
        if user not in self.user_bts:
            self.__load_user_data__(user)

        if user not in self.user_bts:
            self.user_bts[user] = dict()

        # 更新回测状态
        stateObj = self.get_bt_state(user, straid, btid)
        if stateObj is not None:
            self.user_bts[user][btid]["state"] = stateObj

        # 更新回测结果摘要
        summaryObj = self.get_bt_summary(user, straid, btid)
        if summaryObj is not None:
            self.user_bts[user][btid]["perform"] = summaryObj
            self.user_stras[user][straid]["perform"] = summaryObj

        self.__save_user_data__(user)
    
    def __save_tasks__(self):
        obj = self.task_infos

        filename = os.path.join(self.path, "tasks.json")
        with self._lock:
            temporary = filename + ".tmp"
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(obj, stream, indent=4)
            os.replace(temporary, filename)

    def __load_tasks__(self):
        filename = os.path.join(self.path, "tasks.json")
        if not os.path.exists(filename):
            return

        f = open(filename, "r")
        content = f.read()
        f.close()

        task_infos = json.loads(content)
        pids = psutil.pids()
        for btid in task_infos:
            tInfo = task_infos[btid].copy()
            tInfo["logger"] = self.logger
            btTask = WtBtTask(**tInfo, sink=self)

            if btTask.is_running(pids):
                self.task_map[btid] = btTask
                self.task_infos[btid] = task_infos[btid]
                self.logger.info("回测任务%s已恢复", btid)
            else:
                # 之前记录过测回测任务，执行完成了，要更新回测数据
                user, straid = tInfo["user"], tInfo["straid"]
                self.__update_bt_result__(user, straid, btid)
                self.user_bts[user][btid]["status"] = "interrupted"
                self.__save_user_data__(user)
        
        self.__save_tasks__()
            

    def on_start(self, user:str, straid:str, btid:str):
        pass

    def on_stop(self, user:str, straid:str, btid:str):
        with self._lock:
            task = self.task_map.get(btid)
            self.__update_bt_result__(user, straid, btid)
            info = self.user_bts[user][btid]
            code = task.returncode if task else None
            info["returncode"] = code
            if task and task.stop_requested:
                info["status"] = "stopped"
            elif code == 0 and (self.get_bt_state(user, straid, btid) or {}).get("progress", 0) >= 100 and self.get_bt_summary(user, straid, btid) is not None:
                info["status"] = "completed"
            else:
                info["status"] = "failed" if code is not None else "interrupted"
            self.task_infos.pop(btid, None)
            self.task_map.pop(btid, None)
            self.__save_user_data__(user)
            self.__save_tasks__()

    def stop_backtest(self, user, straid, btid):
        with self._lock:
            task = self.task_map.get(btid)
            if task is None or task.user != user or task.straid != straid:
                return False
        return task.stop()

    def shutdown(self):
        for task in list(self.task_map.values()):
            task.stop()


    def on_state(self, user:str, straid:str, btid:str, statInfo:dict):
        with self._lock:
            self.user_bts[user][btid]["state"].update(statInfo)

    def on_fund(self, user:str, straid:str, btid:str, fundInfo:dict):
        pass