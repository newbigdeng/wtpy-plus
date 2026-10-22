"""Opt-in pre-trade gates over WT Python contexts; native accounting is unchanged.

This is a trusted-strategy extension point, not a sandbox for hostile Python.
Rules inspect the current event/native account; they do not calculate fills,
fees or margin. Reductions and cancellations remain available after a breach.
"""
from copy import deepcopy
import ctypes
import functools
import inspect
import json
import math
import os
from pathlib import Path
import threading
import time

from .RiskLibrary import validate_rule

SUBMISSIONS = {"stra_set_position", "stra_enter_long", "stra_enter_short",
               "stra_exit_long", "stra_exit_short", "stra_buy", "stra_sell"}
ALIASES = {name.removeprefix("stra_"): name for name in SUBMISSIONS}
RULE_HANDLERS = {}


def register_rule(kind, handler):
    """Register trusted application code explicitly, never from a JSON import path.

Handler receives (rule, request, account, state) and returns a breach message
or None. Register its schema in RiskLibrary.RULE_TYPES for web configuration.
"""
    RULE_HANDLERS[kind] = handler


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _builtin(rule, request, account, state):
    kind, limit = rule["type"], rule["params"]["limit"]
    if kind == "max_order_lots":
        return "单次增仓超过手数上限" if request["opening_lots"] > limit else None
    if kind == "max_position_lots":
        return "目标持仓（含在途委托）超过手数上限" if request["exposure"] > limit else None
    field = "available" if kind == "min_available" else "equity"
    if not account.get("valid") or not _finite(account.get(field)):
        return "原生账户数据缺失或过期，无法计算资金风险"
    if kind == "min_available":
        return "当前可用资金低于开仓下限" if account[field] < limit else None
    if kind == "day_loss_pct":
        baseline = state.get("day_baseline")
        if not _finite(baseline) or baseline <= 0:
            return "缺少有效当日权益基准"
        value = max(0, (baseline-account["equity"])/baseline*100)
        return "当日权益亏损达到上限" if value >= limit else None
    if kind == "drawdown_pct":
        high = state.get("peak")
        if not _finite(high) or high <= 0:
            return "缺少有效权益高点"
        return "权益回撤达到上限" if (high-account["equity"])/high*100 >= limit else None
    raise ValueError("未注册的风控类型：" + kind)


for _kind in ("max_order_lots", "max_position_lots", "min_available", "day_loss_pct", "drawdown_pct"):
    register_rule(_kind, _builtin)


def read_risk_events(path):
    path = Path(path)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def replay_account_provider(path):
    """Read the native POD snapshot, not the less frequent JSON checkpoints.

The ABI copies a mutex-protected snapshot. It never posts to/waits for the API
worker, so querying from native order/trade callbacks cannot deadlock it.
"""
    module = Path(os.environ.get("WT_PLUS_NATIVE_DIR", ""))/"traders/libTraderMocker.so"
    library = ctypes.CDLL(str(module))
    getter = getattr(library, "plus_mocker_account", None)
    if getter:
        getter.argtypes = [ctypes.c_uint32, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_uint32)]
        getter.restype = ctypes.c_bool
    def snapshot(context, channel=""):
        values, clock = (ctypes.c_double*9)(), (ctypes.c_uint32*3)()
        if getter is None or not getter(1, values, clock):
            return {"valid": False, "source": "TraderMocker"}
        obj = dict(zip(("balance", "equity", "realized", "unrealized", "commission", "margin",
                        "frozen_margin", "frozen_commission", "available"), values))
        return {**obj, "date": clock[0], "time": clock[1], "trading_date": clock[2],
                "valid": bool(clock[2]) and all(_finite(value) for value in values), "source": "TraderMocker原生账户"}
    return snapshot


def fast_account_provider(capital):
    def snapshot(context, channel=""):
        getter = getattr(context, "stra_get_fund_data", None)
        day = getattr(context, "stra_get_tdate", context.stra_get_date)()
        if getter is None:
            return {"valid": False, "trading_date": day, "source": "HFT快速Mocker无权益接口"}
        profit = getter(0)
        return dict(valid=_finite(profit), equity=capital+profit, trading_date=day,
                    source="快速Mocker原生盈亏+指定初始资金")
    return snapshot


def live_account_provider(root, query_account=None):
    """Read the existing TraderAdapter query receipt; no synthetic cash balance."""
    root = Path(root)
    queried = {}
    def refresh(channel, updated=None):
        if not query_account or (_finite(updated) and time.time()*1000-updated < 60000):
            return
        now = time.monotonic()
        if now-queried.get(channel, -60) >= 30:
            queried[channel] = now
            # Submission is not a fresh receipt. Missing/stale data remains
            # invalid until the native callback writes an actual query result.
            query_account(channel)
    def snapshot(context, channel=""):
        path = root / "generated/traders" / channel / "rtdata.json"
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            status = json.loads(path.with_name(path.name+".status.json").read_text(encoding="utf-8"))
            funds = obj.get("funds", {})
            if isinstance(funds, list):
                values = funds
            elif "balance" in funds:
                values = [funds]
            else:
                values = list(funds.values())
            # Do not aggregate currencies or choose one of several broker accounts.
            if len(values) != 1:
                if not values:
                    refresh(channel)
                return {"valid": False, "source": "柜台资金币种不唯一"}
            value = values[0]
            updated = value.get("updated_at")
            refresh(channel, updated)
            same_day = str(value.get("trading_date")) == str(status.get("trading_date"))
            fresh = _finite(updated) and -1000 <= time.time()*1000-updated <= 300000
            valid = status.get("ready") is True and status.get("connected") is True and fresh and same_day
            balance, dynamic = value.get("balance"), value.get("dynprofit")
            equity = balance+dynamic if _finite(balance) and _finite(dynamic) else None
            return {**value, "equity": equity, "valid": valid and _finite(equity), "source": "TraderAdapter柜台回执"}
        except (OSError, ValueError, TypeError, AttributeError):
            refresh(channel)
            return {"valid": False, "source": "柜台回执尚不可读取"}
    return snapshot


class _State:
    def __init__(self, name, kind, channel):
        self.name, self.kind, self.channel = name, kind, channel
        self.lock = threading.RLock()
        self.account, self.metrics, self.pending, self.targets = {}, {}, {}, {}
        self.views = {}


class _RiskContext:
    def __init__(self, context, runtime, state):
        self.context, self.runtime, self.state = context, runtime, state

    def __getattr__(self, name):
        canonical = ALIASES.get(name, name)
        original = getattr(self.context, canonical)
        if canonical not in SUBMISSIONS or not callable(original):
            return original
        @functools.wraps(original)
        def submit(*args, **kwargs):
            with self.state.lock:
                bound = inspect.signature(original).bind(*args, **kwargs)
                bound.apply_defaults()
                params = bound.arguments
                code, qty = params["stdCode"], params["qty"]
                if not _finite(qty) or (canonical != "stra_set_position" and qty < 0):
                    raise ValueError("委托数量须为有限数值，增减仓数量不可为负")
                position = self.context.stra_get_position(code)
                if not _finite(position):
                    self.runtime.event(self.state, None, code, canonical, qty, 0, "持仓不可读取，拒绝提交")
                    return [] if canonical in ("stra_buy", "stra_sell") else None
                if canonical in ("stra_exit_long", "stra_exit_short"):
                    projected = self.state.targets.get(code, position)
                    result = original(*args, **kwargs)
                    if canonical == "stra_exit_long" and projected > 0:
                        self.state.targets[code] = max(0, projected-qty)
                    elif canonical == "stra_exit_short" and projected < 0:
                        self.state.targets[code] = min(0, projected+qty)
                    return result
                hft = canonical in ("stra_buy", "stra_sell")
                if hft:
                    side = 1 if canonical == "stra_buy" else -1
                    # Keep buy/sell reservations separate: netting them could hide risk.
                    buys = sum(row["qty"] for row in self.state.pending.values() if row["code"] == code and row["side"] > 0)
                    sells = sum(row["qty"] for row in self.state.pending.values() if row["code"] == code and row["side"] < 0)
                    projected = position + side*(buys if side > 0 else sells)
                    reducing = max(0, -projected*side)
                    opening = max(0, qty-reducing)
                    exposure = max(abs(position+buys+(qty if side > 0 else 0)), abs(position-sells-(qty if side < 0 else 0)))
                    requested_target = projected+side*qty
                else:
                    projected = self.state.targets.get(code, position)
                    if canonical == "stra_set_position":
                        requested_target = qty
                    elif canonical == "stra_enter_long":
                        requested_target = max(projected, 0)+qty
                    else:
                        requested_target = min(projected, 0)-qty
                    same_side = projected*requested_target >= 0
                    opening = max(0, abs(requested_target)-(abs(projected) if same_side else 0))
                    exposure = abs(requested_target)
                if opening <= 0:
                    result = original(*args, **kwargs)
                    if hft:
                        self._reserve(result, code, side, qty)
                    else:
                        self.state.targets[code] = requested_target
                    return result
                self.runtime.observe(self.context, self.state)
                request = dict(code=code, method=canonical, quantity=qty, target=requested_target,
                               opening_lots=opening, exposure=exposure)
                blocked = False
                for rule in self.runtime.rules:
                    reason = RULE_HANDLERS[rule["type"]](rule, request, self.state.account, self.state.metrics)
                    if reason:
                        blocked |= rule["mode"] == "pretrade"
                        self.runtime.event(self.state, rule, code, canonical, qty, requested_target, reason)
                if not blocked:
                    result = original(*args, **kwargs)
                    if hft:
                        self._reserve(result, code, side, qty)
                    else:
                        self.state.targets[code] = requested_target
                    return result
                # Reverse orders retain their reducing leg; no automatic flattening.
                if hft and reducing > 0:
                    params["qty"] = min(qty, reducing)
                    result = original(*bound.args, **bound.kwargs)
                    self._reserve(result, code, side, params["qty"])
                    return result
                if not hft and projected*requested_target < 0:
                    self.state.targets[code] = 0
                    return self.context.stra_set_position(code, 0, params.get("usertag", ""),
                                                          params.get("limitprice", 0), params.get("stopprice", 0))
                return [] if hft else None
        return submit

    def _reserve(self, ids, code, side, quantity):
        # A native adapter may split an instruction. Reserve conservatively until
        # its real on_order reports the remaining amount of each child order.
        for identifier in ids or []:
            self.state.pending[identifier] = dict(code=code, side=side, qty=quantity)


class _RiskStrategy:
    def __init__(self, strategy, runtime, state):
        self.strategy, self.runtime, self.state = strategy, runtime, state

    def name(self):
        return self.strategy.name()

    def __getattr__(self, name):
        original = getattr(self.strategy, name)
        if not name.startswith("on_") or not callable(original):
            return original
        def callback(context, *args, **kwargs):
            with self.state.lock:
                if name == "on_order":
                    # localid, code, isBuy, totalQty, leftQty, price, isCanceled, tag
                    identifier, code, side, _, remaining, _, canceled = args[:7]
                    if canceled or remaining <= 0:
                        self.state.pending.pop(identifier, None)
                    else:
                        self.state.pending[identifier] = dict(code=code, side=1 if side else -1, qty=remaining)
                elif name == "on_entrust" and not args[2]:
                    self.state.pending.pop(args[0], None)
                self.runtime.observe(context, self.state)
                identity = id(context)
                if identity not in self.state.views:
                    self.state.views[identity] = _RiskContext(context, self.runtime, self.state)
                return original(self.state.views[identity], *args, **kwargs)
        return callback


class RiskRuntime:
    def __init__(self, selection, output, account_provider=None):
        self.rules = deepcopy((selection or {}).get("rules", []))
        for rule in self.rules:
            validate_rule(rule)
            if rule["type"] not in RULE_HANDLERS:
                raise ValueError("未注册的交易前风控处理器")
        self.output = Path(output)
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.provider = account_provider or (lambda context, channel: {"valid": False})
        self.needs_account = any(rule["type"] not in ("max_order_lots", "max_position_lots") for rule in self.rules)
        self.lock = threading.Lock()

    def observe(self, context, state):
        if not self.needs_account:
            day = getattr(context, "stra_get_tdate", getattr(context, "stra_get_date", None))
            state.account = {"trading_date": day() if day else None}
            return
        state.account = self.provider(context, state.channel)
        if not state.account.get("valid") or not _finite(state.account.get("equity")):
            return
        equity, day = state.account["equity"], state.account.get("trading_date")
        if not day:
            state.account["valid"] = False
            return
        if state.metrics.get("day") != day:
            previous = state.account.get("prebalance")
            baseline = previous if _finite(previous) and previous > 0 else state.metrics.get("last_equity", equity)
            state.metrics.update(day=day, day_baseline=baseline)
        state.metrics["peak"] = max(state.metrics.get("peak", equity), equity)
        state.metrics["last_equity"] = equity

    def wrap(self, strategy, kind, channel=""):
        return _RiskStrategy(strategy, self, _State(strategy.name(), kind, channel))

    def event(self, state, rule, code, method, quantity, target, reason):
        row = dict(type="pretrade_risk", strategy=state.name, channel=state.channel, code=code,
                   method=method, quantity=quantity, target=target, reason=reason,
                   action="blocked_opening" if rule is None or rule["mode"] == "pretrade" else "alert_only",
                   rule_id=rule["id"] if rule else None, rule_version=rule.get("version") if rule else None,
                   rule_type=rule["type"] if rule else "position_unavailable", time=time.time(),
                   trading_date=state.account.get("trading_date"), account_source=state.account.get("source"))
        with self.lock:
            with self.output.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(row, ensure_ascii=False)+"\n")


def attach_risk(engine, selection=None, output="risk-events.jsonl", account_provider=None):
    """Call before adding/setting strategies. Works with live and fast engines."""
    runtime = RiskRuntime(selection, output, account_provider)
    engine._account_provider = runtime.provider
    if not runtime.rules:
        return runtime
    for name, kind in (("add_cta_strategy", "CTA"), ("add_hft_strategy", "HFT"), ("add_sel_strategy", "SEL"),
                       ("set_cta_strategy", "CTA"), ("set_hft_strategy", "HFT"), ("set_sel_strategy", "SEL")):
        original = getattr(engine, name, None)
        if original is None:
            continue
        def register(strategy, *args, _original=original, _kind=kind, **kwargs):
            channel = kwargs.get("trader", args[0] if _kind == "HFT" and args and isinstance(args[0], str) else "")
            # Managed CTA execution routing supplies this private mapping.
            channel = getattr(engine, "_risk_channels", {}).get(strategy.name(), channel)
            return _original(runtime.wrap(strategy, _kind, channel), *args, **kwargs)
        setattr(engine, name, register)
    engine._risk_runtime = runtime
    return runtime
