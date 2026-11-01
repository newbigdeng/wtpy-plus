"""Fetch permitted futures history and fill gaps through WT's native DSB writer."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import asyncio
from datetime import date, datetime, time as daytime, timedelta
import json
import importlib.util
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

from fastapi import Body, Request

from .ChannelProfiles import channel_transition_lock
from .MarketSessions import CALENDAR
from .MarketSubscriptions import MarketSubscriptions, _inside, _load
from .Roles import can_operate, can_read, group_allowed
from .CNEquityHistory import availability as cne_availability, request as cne_request, settings as cne_settings
from wtpy.StrategyRuntime import atomic_json

PERIODS = {"d": "日线", "m1": "1 分钟", "m5": "5 分钟"}
CN = ZoneInfo("Asia/Shanghai")


def day_value(value):
    return datetime.strptime(str(value), "%Y%m%d").date()


def days_between(start, end, max_days=31):
    start, end = day_value(start), day_value(end)
    if end < start or (end - start).days >= max_days:
        raise ValueError("请选择连续 %d 天以内的日期" % max_days)
    return [start + timedelta(days=n) for n in range((end - start).days + 1)]


def number(day):
    return int(day.strftime("%Y%m%d"))


HISTORICAL_CALENDAR = json.loads((Path(__file__).parent / "calendars/cn-futures-history.json").read_text(encoding="utf8"))
NO_NIGHT = set(CALENDAR["no_night"]) | set(HISTORICAL_CALENDAR["no_night"])


def open_day(day):
    value = number(day)
    if day.weekday() >= 5:
        return False
    for cal in (CALENDAR, HISTORICAL_CALENDAR):
        if cal["coverage_start"] <= value <= cal["coverage_end"]:
            return not any(start <= value <= end for start, end in cal["closed_ranges"])
    return None


def expected_stamps(day, session, period):
    """Use the installed exchange calendar, including the preceding Friday night."""
    if open_day(day) is None:
        return None
    if not open_day(day):
        return set()
    if period == "d":
        return {number(day)}
    sections = session.get("sections", [])
    def is_night(section):
        begin, end = section["from"], section["to"]
        return begin >= 1800 or begin > end or (session.get("offset", 0) > 0 and begin // 100 * 60 + begin % 100 < session["offset"])
    previous = day - timedelta(days=1)
    if any(is_night(section) for section in sections):
        while open_day(previous) is False:
            previous -= timedelta(days=1)
        if open_day(previous) is None:
            return None
    result = set()
    for section in sections:
        begin, end = section["from"], section["to"]
        night = is_night(section)
        if night and number(previous) in NO_NIGHT:
            continue
        base = previous if night else day
        if night and begin < 1800:
            base += timedelta(days=1)
        left = datetime.combine(base, daytime()) + timedelta(hours=begin // 100, minutes=begin % 100)
        right = datetime.combine(base, daytime()) + timedelta(hours=end // 100, minutes=end % 100)
        if end < begin:
            right += timedelta(days=1)
        step = timedelta(minutes=1 if period == "m1" else 5)
        cursor = left + step
        while cursor <= right:
            result.add(int(cursor.strftime("%Y%m%d%H%M")))
            cursor += step
    return result


def tushare_code(contract):
    suffix = {"SHFE": "SHF", "CFFEX": "CFX", "CZCE": "ZCE", "GFEX": "GFE",
              "DCE": "DCE", "INE": "INE"}.get(contract["exchange"])
    if suffix is None:
        raise ValueError("当前仅支持中国期货交易所的具体合约")
    ticker = contract["ticker"].upper()
    digits = ticker[len(contract["product"]):]
    if contract["exchange"] == "CZCE" and len(digits) == 3:
        expiry = str(contract.get("expire_date") or "")
        if len(expiry) != 8 or expiry[3] != digits[0]:
            raise ValueError("郑商所合约缺少可靠的到期年份，不能转换平台代码")
        ticker = contract["product"].upper() + expiry[2] + digits
    return ticker + "." + suffix


def normalize_bars(items, period, allowed):
    result = []
    for row in items:
        stamp = int(str(row["trade_date"])) if period == "d" else int(datetime.fromisoformat(row["trade_time"]).strftime("%Y%m%d%H%M"))
        trading_date = allowed.get(stamp)
        if trading_date is None:
            continue
        values = {key: float(row.get(key) or 0) for key in ("open", "high", "low", "close", "settle", "vol", "amount", "oi")}
        if not all(math.isfinite(v) for v in values.values()) or values["vol"] < 0 or values["high"] < values["low"]:
            raise ValueError("平台返回了无效的行情数值，未写入文件")
        result.append(dict(date=trading_date, time=0 if period == "d" else stamp - 199000000000,
                           open=values["open"], high=values["high"], low=values["low"], close=values["close"],
                           settle=values["settle"], vol=values["vol"], hold=values["oi"], diff=0,
                           money=values["amount"] * row.get("_money_scale", 10000 if period == "d" else 1)))
    return result


class TushareHistory:
    def __init__(self, config_path):
        self.config_path = Path(config_path)

    def configured(self):
        return self.config_path.is_file() and bool(self.token())

    def token(self):
        if not self.config_path.is_file():
            return ""
        return json.loads(self.config_path.read_text()).get("tushare", {}).get("token", "")

    def request(self, name, params, fields):
        token = self.token()
        if not token:
            raise ValueError("尚未在云端配置 Tushare 凭据")
        request = urllib.request.Request("https://api.tushare.pro", headers={"Content-Type": "application/json"},
            data=json.dumps(dict(api_name=name, token=token, params=params, fields=fields)).encode())
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                value = json.load(response)
        except (OSError, ValueError, urllib.error.URLError):
            raise ValueError("连接 Tushare 失败，请稍后检查平台和网络") from None
        if value.get("code") != 0:
            raise ValueError(str(value.get("msg") or "Tushare 拒绝了请求").replace(token, "[已隐藏]"))
        data = value.get("data") or {}
        rows = data.get("items") or []
        if len(rows) >= 8000:
            raise ValueError("平台返回数量达到上限，未将可能截断的数据标为补齐")
        return [dict(zip(data["fields"], row)) for row in rows]


class MarketHistory:
    def __init__(self, data_mgr, dog, manifest_path=None, data_dir=None):
        self.data_mgr, self.dog = data_mgr, dog
        self.root = Path(data_dir or Path.cwd())
        self.subscriptions = MarketSubscriptions(data_mgr, dog, manifest_path)
        self.provider = TushareHistory(self.root / "market-providers.local.json")
        self.status_path, self.jobs_path = self.root / "market-provider-status.json", self.root / "market-history-jobs.json"
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-history")
        self.stopping = threading.Event()
        self.jobs = json.loads(self.jobs_path.read_text()) if self.jobs_path.is_file() else []
        for job in self.jobs:
            if job["status"] in ("queued", "running"):
                job.update(status="failed", error="监控服务已重启，请重新提交补数")
        self._save_jobs()

    def _save_jobs(self):
        atomic_json(self.jobs_path, self.jobs[-20:])

    def close(self):
        self.stopping.set()
        self.executor.shutdown(wait=True, cancel_futures=True)
        with self.lock:
            for job in self.jobs:
                if job["status"] == "queued":
                    job.update(status="failed", error="服务停止，补数未执行")
            self._save_jobs()

    def _check_stopping(self):
        if self.stopping.is_set():
            raise ValueError("监控服务正在停止，补数已取消")

    def providers(self, groupid, refresh=False):
        ctx = self.subscriptions.context(groupid)
        configured = self.provider.configured()
        status = json.loads(self.status_path.read_text()) if self.status_path.is_file() else {}
        version = self.provider.config_path.stat().st_mtime_ns if configured else 0
        if status.get("credential_mtime") != version:
            status = {"credential_mtime": version, "periods": {}}
        if refresh and configured:
            if not ctx["catalog"]:
                raise ValueError("请先更新合约资料")
            code = tushare_code(ctx["catalog"][0])
            day = date.today() - timedelta(days=1)
            while open_day(day) is False:
                day -= timedelta(days=1)
            for period, api in (("d", "fut_daily"), ("m1", "ft_mins")):
                params = dict(ts_code=code, start_date=day.strftime("%Y%m%d"), end_date=day.strftime("%Y%m%d")) if period == "d" else dict(ts_code=code, freq="1min", start_date=day.strftime("%Y-%m-%d")+" 09:00:00", end_date=day.strftime("%Y-%m-%d")+" 09:01:00")
                try:
                    self.provider.request(api, params, "ts_code")
                    entry = dict(available=True, reason="接口权限检查通过")
                except ValueError as error:
                    entry = dict(available=False, reason=str(error))
                status["periods"][period] = entry
            status["periods"]["m5"] = dict(status["periods"]["m1"])
            status["checked_at"] = int(time.time()*1000)
            atomic_json(self.status_path, status)
        periods = [dict(value=p, label=label, **status.get("periods", {}).get(p, dict(available=False, reason="尚未检查平台权限" if configured else "尚未配置云端凭据"))) for p, label in PERIODS.items()]
        available = any(p["available"] for p in periods)
        replay = _load(ctx["root"] / "run/config.yaml").get("monitor", {}).get("market_mode") == "simnow_replay"
        ak_installed = importlib.util.find_spec("akshare") is not None
        private = json.loads(self.provider.config_path.read_text()) if self.provider.config_path.is_file() else {}
        cne = cne_availability(cne_settings(private))
        ak_periods = [dict(value=p, label=label, available=ak_installed,
            reason="仅返回平台仍保留的具体合约 K 线；不能补 Tick，旧分钟数据可能取不到" if ak_installed else "运行环境未安装 AkShare") for p,label in PERIODS.items()]
        return dict(result=0, message="外部平台只补真实返回的 K 线，不把 K 线标成 Tick", can_backfill=not replay and not self.dog.isRunning(groupid), can_prepare_lake=not replay and cne["available"],
                    providers=[dict(id="akshare", label="AkShare（默认）", configured=ak_installed, available=ak_installed,
                                    reason="新浪具体合约日线和分钟线；历史范围由源端决定" if ak_installed else "运行环境未安装 AkShare", periods=ak_periods, max_days=31,
                                    documentation="https://akshare.akfamily.xyz/data/futures/futures.html"),
                               dict(id="tushare", label="Tushare", configured=configured, available=available,
                                    reason="接口已就绪" if available else "期货接口尚未授权或尚未检查权限", periods=periods, max_days=31, checked_at=status.get("checked_at")),
                               dict(id="cnequity", label="CNEquity 数据湖", configured=cne["available"], **cne,
                                    periods=[dict(value=p,label=PERIODS[p],**cne) for p in PERIODS], max_days=366,
                                    documentation="https://rootsunc.github.io/CNEquity/datasets/catalog/"),
                               dict(id="ifind", label="同花顺 iFinD", configured=False, available=False, periods=[], max_days=31, reason="需另行配置已授权的 iFinD 账号与接口"),
                               dict(id="wind", label="Wind", configured=False, available=False, periods=[], max_days=31, reason="需另行配置已授权的 Wind API 与运行环境")],
                    reason="回放测试环境不能混入真实市场历史数据" if replay else "交易引擎须停止；写入前协调数据中台")

    def context(self, groupid, code, period, start_date, end_date, max_days=31):
        if period not in PERIODS:
            raise ValueError("支持日线、1分钟、5分钟；不能用它们补造 Tick")
        days = days_between(start_date, end_date, max_days)
        ctx = self.subscriptions.context(groupid)
        info = next((item for item in ctx["catalog"] if item["code"] == code), None)
        if info is None:
            raise ValueError("请选择合约表中的具体合约")
        session_path = _inside(ctx["root"], ctx["folder"], ctx["config"]["basefiles"]["session"])
        session = _load(session_path).get(info["session"])
        if session is None:
            raise ValueError("合约没有已配置的交易时段")
        ctx.update(contract=info, session=session, days=days, storage=_inside(ctx["root"], ctx["folder"], ctx["config"]["writer"]["path"]))
        return ctx

    def worker(self, ctx, period, start_date, end_date, action="coverage", bars=None):
        payload = dict(action=action, storage=str(ctx["storage"]), exchange=ctx["contract"]["exchange"], ticker=ctx["contract"]["ticker"],
                       period=period, start_date=start_date, end_date=end_date)
        if bars is not None:
            payload["bars"] = bars
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("HistoryDataWorker.py"))], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=20)
        try:
            value = json.loads(result.stdout.splitlines()[-1])
        except (ValueError, IndexError):
            raise ValueError("WT 历史文件读写进程失败，原文件保留") from None
        if result.returncode or value.get("error"):
            raise ValueError(value.get("error") or "WT 历史文件操作失败")
        return value

    def lake(self, ctx, period, start_date, end_date, action="coverage"):
        private = json.loads(self.provider.config_path.read_text()) if self.provider.config_path.is_file() else {}
        value = cne_settings(private)
        access = cne_availability(value)
        if not access["available"]:
            raise ValueError(access["reason"])
        expected = {}
        for day in ctx["days"]:
            stamps = expected_stamps(day, ctx["session"], period)
            if stamps is None:
                raise ValueError("交易日历未覆盖所选日期，不能确认数据湖缺口")
            expected[str(number(day))] = sorted(stamps)
        code = tushare_code(ctx["contract"])
        ticker, suffix = code.split(".")
        return cne_request(value, dict(action=action, symbol=ticker+"."+{"CFX":"CFE","ZCE":"CZC"}.get(suffix,suffix),
            period=period, start_date=start_date, end_date=end_date, expected=expected), timeout=900 if action == "ensure" else 30)

    def coverage(self, groupid, code, period, start_date, end_date, provider=""):
        ctx = self.context(groupid, code, period, start_date, end_date, 366 if provider == "cnequity" else 31)
        records = self.worker(ctx, period, start_date, end_date)["records"]
        lake = self.lake(ctx, period, start_date, end_date)["lake"] if provider == "cnequity" else None
        lake_dates = {row["date"]: row for row in lake["dates"]} if lake else {}
        source_path=ctx["storage"] / "import-sources.json"
        sources=json.loads(source_path.read_text()) if source_path.is_file() else {}
        provenance=sources.get(code+"/"+period,{})
        rows = []
        for day in ctx["days"]:
            value = number(day)
            actual = [r for r in records if r["date"] == value]
            expected = expected_stamps(day, ctx["session"], period)
            status = "unknown" if expected is None else "closed" if not expected else "present" if expected.issubset({r["stamp"] for r in actual}) else "partial" if actual else "missing"
            if ctx["contract"].get("expire_date") and value > int(ctx["contract"]["expire_date"]):
                status = "not_listed"
            names={r["source"] for r in actual}
            names.update(item["provider"]+"补数记录" for item in provenance.get(str(value),[]))
            rows.append(dict(date=value, status=status, rows=len(actual), expected_rows=len(expected) if expected is not None else None,
                             source="、".join(sorted(names)) or "—", lake_rows=lake_dates.get(value, {}).get("rows"), lake_status=lake_dates.get(value, {}).get("status")))
        return dict(result=0, code=code, period=period, start_date=start_date, end_date=end_date, dates=rows,
                    missing_dates=[r["date"] for r in rows if r["status"] in ("missing", "partial")], absent_dates=[r["date"] for r in rows if not r["rows"]],
                    storage=str(ctx["storage"]), source=CALENDAR["source"], lake=lake,
                    message="WT 与数据湖分别按交易日检查；缺失时仅采集源端能提供的范围" if lake else "按交易日检查；分钟线包含上一交易日夜盘，日历范围之外标为未确认")

    def _ensure_stopped(self, ctx):
        for group in self.data_mgr.get_groups():
            if Path(group["path"]).resolve().is_relative_to(ctx["root"]) and self.dog.isRunning(group["id"]):
                raise ValueError("请先停止使用此数据中台的交易引擎")

    def submit(self, groupid, provider, code, period, start_date, end_date, restart_collector=False):
        with channel_transition_lock, self.lock:
            self._check_stopping()
            ctx = self.context(groupid, code, period, start_date, end_date, 366 if provider == "cnequity" else 31)
            if provider != "cnequity":
                self._ensure_stopped(ctx)
            permitted = self.providers(groupid)
            if not permitted["can_backfill"] and not (provider == "cnequity" and permitted["can_prepare_lake"]):
                raise ValueError(permitted["reason"])
            platform = next((p for p in permitted["providers"] if p["id"] == provider), None)
            access = next((p for p in platform["periods"] if p["value"] == period), None) if platform else None
            if not access or not access["available"]:
                raise ValueError(access["reason"] if access else "该平台尚未接入已授权的历史接口")
            now = datetime.now(CN)
            if day_value(end_date) > now.date() or (day_value(end_date) == now.date() and now.hour < 16):
                raise ValueError("请选择已经结束的交易日，避免把盘中数据当作完整历史")
            if provider != "cnequity" and self.dog.isRunning(ctx["profile"]["datahub_id"]) and not restart_collector:
                raise ValueError("请先停止采集，或勾选允许写入时短暂停采并自动恢复")
            if any(j["status"] in ("queued", "running") for j in self.jobs):
                raise ValueError("已有补数任务正在运行，请等待完成")
            job = dict(id=str(time.time_ns()), groupid=groupid, profileid=ctx["profile"]["id"], provider=provider, code=code,
                       period=period, start_date=start_date, end_date=end_date, status="queued", rows=0, dates=[], error="", created_at=int(time.time()*1000))
            self.jobs.append(job); self.jobs=self.jobs[-20:]; self._save_jobs()
            self.executor.submit(self._run, job, ctx, restart_collector)
            return dict(result=0, message="补数任务已提交", task=dict(job))

    def fetch(self, ctx, job):
        self._check_stopping()
        code, period = tushare_code(ctx["contract"]), job["period"]
        ak_rows = None
        if job["provider"] == "akshare":
            from .HistoryCatalog import native_worker
            ak_rows = native_worker(dict(action="akshare", ticker=ctx["contract"]["ticker"], period=period))["rows"]
        elif job["provider"] == "cnequity":
            result = self.lake(ctx, period, job["start_date"], job["end_date"], "ensure")
            ak_rows = result["rows"]
            job["lake"] = result["lake"]
        if period == "d":
            rows = ak_rows if ak_rows is not None else self.provider.request("fut_daily", dict(ts_code=code, start_date=str(job["start_date"]), end_date=str(job["end_date"])),
                                         "ts_code,trade_date,open,high,low,close,settle,vol,amount,oi")
            allowed = {number(d): number(d) for d in ctx["days"]}
        else:
            allowed = {}
            for day in ctx["days"]:
                stamps = expected_stamps(day, ctx["session"], period)
                if stamps is None:
                    raise ValueError("交易日历未覆盖所选日期，不能可靠判断夜盘所属交易日")
                allowed.update({stamp: number(day) for stamp in stamps})
            dates = sorted({str(stamp)[:8] for stamp in allowed})
            rows = ak_rows if ak_rows is not None else []
            for day in ([] if ak_rows is not None else dates):
                self._check_stopping()
                text = datetime.strptime(day, "%Y%m%d").strftime("%Y-%m-%d")
                rows += self.provider.request("ft_mins", dict(ts_code=code, freq="1min" if period=="m1" else "5min", start_date=text+" 00:00:00", end_date=text+" 23:59:59"),
                                              "ts_code,trade_time,open,high,low,close,vol,amount,oi")
        if not rows:
            if job["provider"] == "cnequity" and period != "d":
                raise ValueError("所选日期没有可用分钟行情；当前源只提供最近窗口，历史文件未改动")
            raise ValueError("平台没有返回所选合约日期的数据，未写入文件")
        bars = normalize_bars(rows, period, allowed)
        if not bars:
            raise ValueError("平台返回数据不属于所选交易日或交易时段，未写入文件")
        return bars

    def _run(self, job, ctx, restart_collector):
        try:
            with self.lock:
                job["status"]="running"; self._save_jobs()
            bars = self.fetch(ctx, job)
            with channel_transition_lock:
                self._check_stopping()
                current=self.subscriptions.context(job["groupid"])
                if current["root"] != ctx["root"]:
                    raise ValueError("通道环境已切换，请在目标环境重新补数")
                if job["provider"] == "cnequity":
                    records = self.worker(ctx, job["period"], job["start_date"], job["end_date"])["records"]
                    existing = {row["stamp"] for row in records if row["source"] == "WT历史"}
                    incoming = {bar["date"] if job["period"] == "d" else bar["time"]+199000000000 for bar in bars}
                    if incoming.issubset(existing):
                        self._completed(job, dict(rows=0, received_rows=len(bars), already_present_rows=len(incoming)), False)
                        return
                    try:
                        self._ensure_stopped(ctx)
                        if self.dog.isRunning(ctx["profile"]["datahub_id"]) and not restart_collector:
                            raise ValueError("采集正在运行，尚未允许短暂停采")
                    except ValueError as error:
                        with self.lock:
                            job.update(status="ready", message="数据湖已准备，WT 尚未写入："+str(error), finished_at=int(time.time()*1000))
                            self._save_jobs()
                        return
                else:
                    self._ensure_stopped(ctx)
                appid=ctx["profile"]["datahub_id"]
                was_running=bool(self.dog.isRunning(appid))
                if was_running and not restart_collector:
                    raise ValueError("采集已启动，未允许暂停采集，补数未写入")
                if was_running:
                    self.dog.stop(appid)
                    if self.dog.isRunning(appid):
                        raise ValueError("采集器未能停止，补数未写入")
                try:
                    result=self.worker(ctx,job["period"],job["start_date"],job["end_date"],"merge",bars)
                    if result["rows"]:
                        source_path=ctx["storage"] / "import-sources.json"
                        sources=json.loads(source_path.read_text()) if source_path.is_file() else {}
                        record=sources.setdefault(job["code"]+"/"+job["period"],{})
                        for day in result["dates"]:
                            record.setdefault(str(day),[]).append(dict(provider=job["provider"],job=job["id"],imported_at=int(time.time()*1000)))
                        atomic_json(source_path,sources)
                finally:
                    if was_running:
                        self.dog.start(appid)
                        if not self.dog.isRunning(appid):
                            raise ValueError("写入后采集器恢复失败，请在调度中心检查")
            self._completed(job, result, was_running)
        except Exception as error:
            message=str(error)
            token=self.provider.token()
            if token:message=message.replace(token,"[已隐藏]")
            with self.lock:
                job.update(status="failed",error=message,finished_at=int(time.time()*1000));self._save_jobs()

    def _completed(self, job, result, collector_restarted):
        message = "WT 对应时间点已有记录，跳过写入" if result.get("already_present_rows") else "WT 新增 %d 条；已有时间点保留" % result["rows"]
        if job.get("lake", {}).get("missing_dates"):
            message += "；数据湖仍有 %d 个交易日缺失或不完整，源端未提供的历史没有补造" % len(job["lake"]["missing_dates"])
        with self.lock:
            job.update(result,status="completed",message=message,finished_at=int(time.time()*1000),collector_restarted=collector_restarted)
            self._save_jobs()


def register_market_history_routes(app, data_mgr, dog, check_auth, manifest_path=None, data_dir=None):
    history=MarketHistory(data_mgr,dog,manifest_path,data_dir)
    previous_lifespan=app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            try:yield state
            finally:await asyncio.to_thread(history.close)
    app.router.lifespan_context=lifespan
    def authorize(request,token,groupid,control=False):
        ok,user=check_auth(request,token)
        if not ok:return None,user
        if not (can_operate(user) if control else can_read(user)) or not group_allowed(user,groupid):
            return None,dict(result=-403,message="没有该组合行情数据的操作权限")
        if not data_mgr.has_group(groupid):return None,dict(result=-1,message="组合不存在")
        return user,None
    def failure(error):return dict(result=-3,message=str(error))

    @app.post("/mgr/market/providers",tags=["历史行情"])
    def providers(request:Request,groupid:str=Body(...,embed=True),refresh:bool=Body(False,embed=True),token:str=Body(None,embed=True)):
        user,error=authorize(request,token,groupid,refresh)
        if error:return error
        try:
            result=history.providers(groupid,refresh);result["can_backfill"] &= can_operate(user);result["can_prepare_lake"] &= can_operate(user);return result
        except (ValueError,OSError,KeyError)as error:return failure(error)

    @app.post("/mgr/market/coverage",tags=["历史行情"])
    def coverage(request:Request,groupid:str=Body(...,embed=True),code:str=Body(...,embed=True),period:str=Body(...,embed=True),start_date:int=Body(...,embed=True),end_date:int=Body(...,embed=True),provider:str=Body("",embed=True),token:str=Body(None,embed=True)):
        user,error=authorize(request,token,groupid)
        if error:return error
        try:return history.coverage(groupid,code,period,start_date,end_date,provider)
        except (ValueError,OSError,KeyError,subprocess.TimeoutExpired)as error:return failure(error)

    @app.post("/mgr/market/backfill",tags=["历史行情"])
    def backfill(request:Request,groupid:str=Body(...,embed=True),provider:str=Body(...,embed=True),code:str=Body(...,embed=True),period:str=Body(...,embed=True),start_date:int=Body(...,embed=True),end_date:int=Body(...,embed=True),restart_collector:bool=Body(False,embed=True),token:str=Body(None,embed=True)):
        user,error=authorize(request,token,groupid,True)
        if error:return error
        try:
            result=history.submit(groupid,provider,code,period,start_date,end_date,restart_collector)
            data_mgr.log_action(user,"market_backfill",groupid+":"+result["task"]["id"])
            return result
        except (ValueError,OSError,KeyError)as error:return failure(error)

    @app.post("/mgr/market/jobs",tags=["历史行情"])
    def jobs(request:Request,groupid:str=Body(...,embed=True),token:str=Body(None,embed=True)):
        user,error=authorize(request,token,groupid)
        if error:return error
        with history.lock:return dict(result=0,jobs=[dict(j)for j in reversed(history.jobs)if j["groupid"]==groupid])
    return history
