"""Group-scoped risk visibility, persisted alerts and native CTA configuration.

This service never submits/cancels orders or changes target filters. Manual strategy
controls use StrategyControl routes. Native SimpleRiskMon remains the only automatic
portfolio scaling mechanism exposed here, and its edits require an engine restart.
"""
import asyncio
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time

import yaml
from fastapi import Body, Request
from wtpy.StrategyRuntime import atomic_json
from .TradeRiskApi import trade_risk_snapshot
from .Roles import can_read, can_operate, can_admin, group_allowed

try:
    import fcntl
except ImportError:
    fcntl = None

NATIVE_FIELDS = ("active", "base_amount", "basic_ratio", "calc_span",
                 "inner_day_active", "inner_day_fd", "multi_day_active",
                 "multi_day_fd", "risk_scale", "risk_span")
DEFAULT_NATIVE = dict(active=False, module="WtRiskMonFact", name="SimpleRiskMon",
    base_amount=0, basic_ratio=101, calc_span=5, inner_day_active=False,
    inner_day_fd=20, multi_day_active=False, multi_day_fd=20,
    risk_scale=0.3, risk_span=30)
THRESHOLDS = ("day_loss_pct", "drawdown_pct", "gross_lots", "margin_usage_pct", "available_funds", "top1_concentration_pct")
LIMITS = dict(day_loss_pct=(0, 100), drawdown_pct=(0, 100), gross_lots=(0, 1000000000),
              margin_usage_pct=(0, 10000), available_funds=(0, 1e15), top1_concentration_pct=(0, 100))
METRIC_LABELS = dict(day_loss_pct="柜台当日亏损", drawdown_pct="引擎权益高点回撤", gross_lots="理论持仓总手数",
                     margin_usage_pct="柜台保证金占用率", available_funds="柜台可用资金", top1_concentration_pct="Top1 品种手数集中度")
BOUNDARY = "柜台资金与引擎理论账簿分别计算。后台只预警；暂停、过滤和原生风控的影响以通道回报为准。"


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _read_json(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("风控数据应为对象")
    return value


def _root(group):
    root = Path(group["path"]).resolve()
    folder = (root / "generated" / "risk-control").resolve()
    if not folder.is_relative_to(root):
        raise ValueError("风控目录不可跨出组合目录")
    return folder


def _config(group):
    root = Path(group["path"]).resolve()
    for name in ("config.yaml", "config.json"):
        path = root / name
        if path.is_file():
            if not path.resolve().is_relative_to(root):
                raise ValueError("组合配置不可跨出组合目录")
            try:
                obj = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
            except yaml.YAMLError as exc:
                raise ValueError("组合配置无法解析，保留原文件") from exc
            if not isinstance(obj, dict) or not isinstance(obj.get("env", {}), dict):
                raise ValueError("无法读取组合引擎配置")
            return path, obj
    raise ValueError("未找到组合 config.yaml/config.json")


def native_config(group):
    _, obj = _config(group)
    env = obj.get("env", {})
    config = env.get("riskmon", {})
    if not isinstance(config, dict):
        raise ValueError("env.riskmon 应为对象")
    # No trader configuration or arbitrary module path is sent to the browser.
    values = {key: config.get(key, DEFAULT_NATIVE[key]) for key in NATIVE_FIELDS}
    compatible = (config.get("module", "WtRiskMonFact") == "WtRiskMonFact"
                  and config.get("name", "SimpleRiskMon") == "SimpleRiskMon")
    return dict(engine=str(env.get("name", "")).upper(),
        supported=str(env.get("name", "")).lower() == "cta" and compatible,
        configured=bool(config), params=values, restart_required=True,
        runtime_confirmed=False,
        reason="仅 CTA 引擎支持此原生目标倍率配置；HFT/UFT 不适用" if str(env.get("name", "")).lower() != "cta" else
               "原生 SimpleRiskMon 仅启动时读取；保存不表示当前进程已采用参数" if compatible else
               "检测到自定义风控模块，仅显示通用参数，不提供覆盖编辑")


def default_policy(group):
    native = native_config(group)
    capital = _number(native["params"].get("base_amount")) or 0
    return dict(schema=1, enabled=False, mode="alert_only", capital=max(0, capital),
        stale_seconds=30, thresholds={key: None for key in THRESHOLDS},
        account_channel="", account_currency="", updated_at=None)


def read_policy(group):
    path = _root(group) / "policy.json"
    if not path.exists():
        return default_policy(group)
    value = _read_json(path)
    return validate_policy(value, require_threshold=False)


def validate_policy(value, require_threshold=True):
    if not isinstance(value, dict):
        raise ValueError("风控策略须为对象")
    if value.get("mode", "alert_only") != "alert_only":
        raise ValueError("仅支持后台预警，自动交易动作未开放")
    if not isinstance(value.get("enabled", False), bool):
        raise ValueError("enabled 须为布尔值")
    capital = _number(value.get("capital", 0))
    stale = _number(value.get("stale_seconds", 30))
    if capital is None or capital < 0 or capital > 1e15:
        raise ValueError("基准资金须为有效非负数")
    if stale is None or not stale.is_integer() or not 5 <= stale <= 3600:
        raise ValueError("数据新鲜度须为 5 至 3600 秒的整数")
    raw = value.get("thresholds", {})
    if not isinstance(raw, dict) or set(raw) - set(THRESHOLDS):
        raise ValueError("包含不支持的风控阈值")
    thresholds = {}
    for key in THRESHOLDS:
        item = raw.get(key)
        if item is None:
            thresholds[key] = None
            continue
        number = _number(item)
        low, high = LIMITS[key]
        if number is None or not low < number <= high:
            raise ValueError(METRIC_LABELS[key] + "阈值超出有效范围")
        thresholds[key] = number
    if thresholds["drawdown_pct"] is not None and capital <= 0:
        raise ValueError("引擎权益回撤需要大于零的引擎初始资金")
    enabled = value.get("enabled", False)
    if enabled and require_threshold and all(x is None for x in thresholds.values()):
        raise ValueError("开启后台预警前至少设置一个阈值")
    channel, currency=value.get("account_channel", ""), value.get("account_currency", "")
    if not isinstance(channel,str) or not isinstance(currency,str) or len(channel)>80 or len(currency)>20:
        raise ValueError("请选择有效的柜台通道和币种")
    return dict(schema=2, enabled=enabled, mode="alert_only", capital=capital,
        stale_seconds=int(stale), thresholds=thresholds, account_channel=channel,
        account_currency=currency, updated_at=value.get("updated_at"))


@contextmanager
def _lock(group):
    if fcntl is None:
        raise ValueError("风控配置目前仅支持 Linux 容器")
    folder = _root(group)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (folder / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield folder


def _event(state, kind, detail, actor=None, now=None, **fields):
    stamp = time.time() if now is None else now
    events = state.setdefault("events", [])
    next_seq = int(state.get("sequence", 0)) + 1
    state["sequence"] = next_seq
    events.append(dict(id=next_seq, at=stamp, kind=kind, detail=detail,
                       actor=actor or "risk-observer", **fields))
    state["events"] = events[-200:]


def _state(folder):
    path = folder / "events.json"
    return _read_json(path) if path.exists() else dict(schema=1, sequence=0, events=[], conditions={})


def save_policy(group, value, actor):
    policy = validate_policy(value)
    policy["updated_at"] = time.time()
    with _lock(group) as folder:
        state = _state(folder)
        _event(state, "policy_updated", "后台预警已开启" if policy["enabled"] else "后台预警已关闭", actor)
        state["conditions"] = {}
        atomic_json(folder / "policy.json", policy)
        atomic_json(folder / "events.json", state)
    return policy


def save_native(group, values, actor):
    current = native_config(group)
    if not current["supported"]:
        raise ValueError("仅支持 CTA 原生 SimpleRiskMon 参数配置")
    if not isinstance(values, dict) or set(values) - set(NATIVE_FIELDS):
        raise ValueError("原生风控包含未知参数")
    merged = dict(current["params"])
    merged.update(values)
    for key in ("active", "inner_day_active", "multi_day_active"):
        if not isinstance(merged[key], bool):
            raise ValueError(key + " 须为布尔值")
    rules = dict(base_amount=(0, 1e15), basic_ratio=(100, 10000), calc_span=(1, 3600),
                 inner_day_fd=(0.01, 100), multi_day_fd=(0.01, 100),
                 risk_scale=(0, 1), risk_span=(1, 1440))
    for key, (low, high) in rules.items():
        number = _number(merged[key])
        if number is None or not low <= number <= high:
            raise ValueError(key + " 超出有效范围")
        if key in ("basic_ratio", "calc_span", "risk_span"):
            if not number.is_integer():
                raise ValueError(key + " 须为整数")
            number = int(number)
        merged[key] = number
    if merged["active"] and merged["base_amount"] <= 0:
        raise ValueError("启用原生资金风控需要有效基准资金")
    # The configuration is read again under the group lock; preserve all unrelated keys.
    with _lock(group) as folder:
        path, obj = _config(group)
        state = _state(folder)
        native = dict(obj.setdefault("env", {}).get("riskmon", {}))
        native.update(merged)
        native.setdefault("module", "WtRiskMonFact")
        native.setdefault("name", "SimpleRiskMon")
        obj["env"]["riskmon"] = native
        backup = path.with_name(path.name + ".before-risk-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".bak")
        shutil.copy2(path, backup)
        payload = json.dumps(obj, ensure_ascii=False, indent=2) + "\n" if path.suffix == ".json" else yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=".risk-config-", delete=False) as handle:
                temp = Path(handle.name)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, path.stat().st_mode & 0o777)
            os.replace(temp, path)
            temp = None
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
        _event(state, "native_config_saved", "原生参数已保存；须重启组合进程，未确认加载或成交", actor)
        atomic_json(folder / "events.json", state)
    return native_config(group)


def _engine_snapshot(group, running, policy=None, now=None):
    now = time.time() if now is None else now
    policy = read_policy(group) if policy is None else policy
    path = Path(group["path"]).resolve() / "generated/portfolio/datas.json"
    metrics = {key: dict(value=None, valid=False, reason="尚无有效组合账簿") for key in THRESHOLDS}
    snapshot = dict(process_running=bool(running), file_present=path.is_file(),
        fresh=False, age_seconds=None, fund_update_time=None, trading_date=None,
        state="missing", reason="尚无组合账簿", source="generated/portfolio/datas.json",
        account_confirmed=False, metrics=metrics, native_scale=None, native_scale_date=None)
    if not path.is_file():
        return snapshot
    try:
        obj = _read_json(path)
        fund = obj.get("fund", {})
        if not isinstance(fund, dict):
            raise ValueError("组合资金对象无效")
        age = now - path.stat().st_mtime
        updated = _number(fund.get("update_time"))
        fund_age = now-updated/1000 if updated is not None and updated > 0 else None
        fresh = bool(running and 0 <= age <= policy["stale_seconds"] and
                     fund_age is not None and 0 <= fund_age <= policy["stale_seconds"])
        snapshot.update(fresh=fresh, age_seconds=round(age, 3),
            fund_age_seconds=round(fund_age, 3) if fund_age is not None else None,
            fund_update_time=fund.get("update_time"), trading_date=fund.get("date"),
            state="fresh" if fresh else "stopped" if not running else "stale",
            reason="组合账簿新鲜；仍须另查通道持仓" if fresh else
                   "组合进程未运行，显示历史账簿" if not running else "组合账簿过期或时钟异常，不能确认当前风险")
        risk = obj.get("riskmon", {})
        if isinstance(risk, dict):
            snapshot["native_scale"] = _number(risk.get("scale"))
            snapshot["native_scale_date"] = risk.get("date")
        capital = policy["capital"]
        balance, dynamic = _number(fund.get("balance")), _number(fund.get("dynprofit"))
        previous = _number(fund.get("predynbal"))
        if balance is not None and dynamic is not None:
            pnl = balance + dynamic
            equity = capital + pnl
            snapshot["equity"]=equity if capital>0 else None
            if previous is not None and capital + previous > 0 and capital > 0:
                metrics["day_loss_pct"] = dict(value=max(0, (previous-pnl)*100/(capital+previous)),
                    valid=True, reason="相对前一交易日组合动态权益；基准资金由风控设置指定")
            else:
                metrics["day_loss_pct"]["reason"] = "缺少有效基准资金或前日动态权益，不能计算比例"
            peaks = [_number(fund.get("max_dyn_bal"))]
            maxmd = fund.get("maxmd", {})
            if isinstance(maxmd, dict) and maxmd.get("date"):
                peaks.append(_number(maxmd.get("dyn_balance")))
            # DBL_MAX is the engine's uninitialised intraday-high sentinel.
            peaks = [value for value in peaks if value is not None and abs(value) < 1e100]
            if capital > 0 and peaks and capital+max(peaks) > 0:
                high = capital+max(peaks)
                metrics["drawdown_pct"] = dict(value=max(0, (high-equity)*100/high), valid=True,
                    reason="相对组合已记录的日内/跨日动态权益高点；不等同柜台账户净值")
            else:
                metrics["drawdown_pct"]["reason"] = "基准资金或有效权益高点不足"
        positions = obj.get("positions")
        total, valid = 0.0, isinstance(positions, list)
        if valid:
            for item in positions:
                if not isinstance(item, dict):
                    valid = False
                    break
                details = item.get("details")
                if not isinstance(details, list):
                    valid = False
                    break
                for detail in details:
                    value = _number(detail.get("volume")) if isinstance(detail, dict) else None
                    if value is None or value < 0:
                        valid = False
                        break
                    total += value
        metrics["gross_lots"] = dict(value=total if valid else None, valid=valid,
            reason="逐笔多空理论持仓绝对手数之和；不是名义金额、保证金或柜台持仓" if valid else "持仓明细不完整")
    except (OSError, ValueError, TypeError, OverflowError):
        snapshot.update(state="invalid", fresh=False, reason="组合账簿读取失败或正在写入，等待下一次检查")
    return snapshot


def _channels(group):
    folder=Path(group["path"])/"generated"
    try:
        names=_read_json(folder/"marker.json").get("channels",[])
        if isinstance(names,list):return names
    except (OSError,ValueError):
        pass
    root=folder/"traders"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.is_dir() else []


def _trading_day(value):
    try:
        text=str(value)
        datetime.strptime(text,"%Y%m%d")
        return text if len(text)==8 else None
    except (TypeError,ValueError):
        return None


def _select_account(accounts,policy):
    selected=[row for row in accounts if
              (not policy.get("account_channel") or row["channel"]==policy["account_channel"]) and
              (not policy.get("account_currency") or row["currency"]==policy["account_currency"])]
    if len(selected)==1:return selected[0],None
    return None,"请选择一个柜台通道和币种；不同账户不合并" if selected else "尚无所选柜台资金回执"


def _frozen_baseline(group,account,now,tradingday=None):
    day=_trading_day(account.get("trading_date")) if account else _trading_day(tradingday)
    if day is None:return None
    with _lock(group) as folder:
        path=folder/"baselines"/(day+".json")
        if path.exists():return _read_json(path)
        if not account:return None
        value=_number(account.get("prebalance"))
        source="柜台昨结算权益"
        if value is None or value<=0:
            value=_number(account.get("equity"))
            source="柜台盘中首笔权益"
        if not account["valid"] or value is None or value<=0:return None
        baseline=dict(tradingday=day,groupid=group["id"],channel=account["channel"],
                      currency=account["currency"],value=value,source=source,
                      snapshot_time=account["updated_at"],frozen_at=int(now*1000),manual=False)
        path.parent.mkdir(exist_ok=True,mode=0o700)
        atomic_json(path,baseline)
        return baseline


def save_manual_baseline(group,value,channel,currency,tradingday,reason,actor):
    value=_number(value)
    day=_trading_day(tradingday)
    if value is None or not 0<value<=1e15 or day is None or not channel or not currency or not reason.strip():
        raise ValueError("请填写有效金额、交易日、通道、币种和手工来源说明")
    if len(reason)>200:
        raise ValueError("来源说明最多200字")
    with _lock(group) as folder:
        path=folder/"baselines"/(day+".json")
        if path.exists():raise ValueError("该交易日基准已冻结，不可覆盖；请核对通道与交易日")
        now=int(time.time()*1000)
        baseline=dict(tradingday=day,groupid=group["id"],channel=channel,currency=currency,
                      value=value,source="手工："+reason.strip(),snapshot_time=None,
                      frozen_at=now,manual=True,actor=actor)
        path.parent.mkdir(exist_ok=True,mode=0o700)
        atomic_json(path,baseline)
        state=_state(folder)
        _event(state,"manual_baseline","手工基准已冻结："+day,actor,target=group["id"],result="saved")
        atomic_json(folder/"events.json",state)
        return baseline


def risk_snapshot(group,running,policy=None,now=None):
    now=time.time() if now is None else now
    policy=read_policy(group) if policy is None else policy
    engine=_engine_snapshot(group,running,policy,now)
    broker=trade_risk_snapshot(group,_channels(group),now=now*1000,running=running)
    engine_day=_trading_day(engine.get("trading_date"))
    for row in broker["accounts"]:
        broker_day=_trading_day(row.get("trading_date"))
        if engine_day and broker_day and broker_day<engine_day:
            row.update(valid=False,reason="柜台交易日尚未更新",margin_ratio=None)
    account,reason=_select_account(broker["accounts"],policy)
    baseline=_frozen_baseline(group,account,now,engine.get("trading_date"))
    metrics=engine["metrics"]
    for key,metric in metrics.items():
        metric.update(book="engine",source="引擎组合账簿",snapshot_time=engine.get("fund_update_time"))
        if not engine["fresh"]:
            metric.update(reference_value=metric["value"],value=None,valid=False,reason=engine["reason"])
    unavailable=reason or account["reason"]
    current=account if account and account["valid"] else None
    for key in ("day_loss_pct","margin_usage_pct","available_funds"):
        metrics[key]=dict(value=None,valid=False,reason=unavailable,book="broker",source="柜台查询回执",
                          snapshot_time=account.get("updated_at") if account else None)
    matches=bool(current and baseline and baseline["channel"]==current["channel"] and baseline["currency"]==current["currency"])
    if current:
        equity=_number(current["equity"])
        if matches and equity is not None:
            metrics["day_loss_pct"].update(value=max(0,(baseline["value"]-equity)*100/baseline["value"]),
                valid=True,reason="相对当日冻结基准"+("；基准为手工设定" if baseline.get("manual") else ""))
        else:
            metrics["day_loss_pct"]["reason"]="缺少当日基准" if baseline is None else "当日基准属于其他账户；请恢复原通道选择"
        if current["margin_ratio"] is not None:
            metrics["margin_usage_pct"].update(value=current["margin_ratio"]*100,valid=True,reason="已用保证金 / 柜台动态权益")
        if current["available"] is not None:
            metrics["available_funds"].update(value=current["available"],valid=True,reason="柜台可用资金；不保证下一笔委托被受理")
    concentration=dict(value=None,valid=False,reason=engine["reason"],book="engine",source="引擎理论持仓手数",snapshot_time=engine.get("fund_update_time"))
    if engine["fresh"]:
        try:
            positions=_read_json(Path(group["path"])/"generated/portfolio/datas.json")["positions"]
            products={}
            for item in positions:
                code=str(item["code"])
                product=".".join(code.split(".")[:2])
                lots=sum(float(detail["volume"]) for detail in item["details"])
                products[product]=products.get(product,0)+lots
            total=sum(products.values())
            concentration.update(value=max(products.values())*100/total if total else 0,valid=metrics["gross_lots"]["valid"],
                                 reason="同品种绝对手数 / 理论总手数；不是金额集中度",product=max(products,key=products.get) if total else None)
        except (OSError,ValueError,TypeError,KeyError):
            concentration["reason"]="持仓明细不完整"
    metrics["top1_concentration_pct"]=concentration
    usages=[]
    for key,limit in policy["thresholds"].items():
        metric=metrics[key]
        if limit is not None and metric["valid"]:
            usage=(limit/metric["value"]*100 if metric["value"]>0 else 100) if key=="available_funds" else metric["value"]/limit*100
            usages.append(dict(key=key,value=max(0,usage),limit=limit,current=metric["value"],book=metric["book"]))
    highest=max(usages,key=lambda row:row["value"]) if usages else None
    metrics["limit_usage_pct"]=dict(value=highest["value"] if highest else None,valid=bool(highest),
                                    reason=METRIC_LABELS[highest["key"]] if highest else "尚无可计算的已设限额",book="limits",source="各项独立限额",usages=usages)
    summary=dict(valid=bool(current),reason="柜台查询回执有效" if current else unavailable,
                 equity=current.get("equity") if current else None,available=current.get("available") if current else None,
                 margin=current.get("margin") if current else None,fee=current.get("fee") if current else None,
                 updated_at=account.get("updated_at") if account else None,
                 channel=account.get("channel") if account else None,currency=account.get("currency") if account else None)
    return dict(engine,metrics=metrics,baseline=baseline,broker=broker,broker_summary=summary,
                engine_reference=dict(book="engine",fresh=engine["fresh"],trading_date=engine.get("trading_date"),
                                      capital=policy["capital"],equity=engine.get("equity"),
                                      snapshot_time=engine.get("fund_update_time"),reason=engine["reason"]),
                fresh=bool(current) or engine["fresh"],account_confirmed=bool(current),
                state="fresh" if current else "engine_only" if engine["fresh"] else engine["state"],
                reason="柜台与引擎指标分别检查" if current else unavailable)


def evaluate(group, running, persist=False, now=None):
    policy = read_policy(group)
    snapshot = risk_snapshot(group, running, policy, now)
    conditions = {}
    alerts = []
    if policy["enabled"]:
        if not snapshot["fresh"]:
            conditions["data_unavailable"] = snapshot["state"]
            alerts.append(dict(key="data_unavailable", severity="warning", state=snapshot["state"],
                               message=snapshot["reason"]))
        for key, limit in policy["thresholds"].items():
            if limit is None:
                continue
            metric = snapshot["metrics"][key]
            if not snapshot["fresh"] or not metric["valid"]:
                conditions[key] = "unavailable"
                alerts.append(dict(key=key, severity="warning", state="unavailable",
                    message=METRIC_LABELS[key] + "不可判定：" + (metric["reason"] if snapshot["fresh"] else snapshot["reason"])))
            elif (metric["value"] <= limit if key=="available_funds" else metric["value"] >= limit):
                conditions[key] = "breached"
                alerts.append(dict(key=key, severity="danger", state="breached",
                    message=METRIC_LABELS[key] + "达到阈值"+("；基准为手工设定" if key=="day_loss_pct" and snapshot.get("baseline",{}).get("manual") else ""),
                    value=metric["value"], threshold=limit,book=metric["book"],source=metric["source"]))
            else:
                conditions[key] = "normal"
    result = dict(policy=policy, snapshot=snapshot, alerts=alerts, automatic_actions=False,
                  action_mode="manual", boundary=BOUNDARY, evaluated_at=time.time() if now is None else now)
    folder = _root(group)
    if persist and policy["enabled"]:
        with _lock(group) as folder:
            state = _state(folder)
            previous = state.get("conditions", {})
            for key, status in conditions.items():
                if previous.get(key) != status and status != "normal":
                    alert=next(item for item in alerts if item["key"]==key)
                    _event(state,"alert",alert["message"],now=now,key=key,target=group["id"],
                           status="pending",value=alert.get("value"),threshold=alert.get("threshold"),
                           book=alert.get("book"),result=status)
                elif previous.get(key) not in (None, "normal") and status == "normal":
                    _event(state, "recovered", key + ": normal", now=now)
            state["conditions"] = conditions
            state["last_evaluated_at"] = result["evaluated_at"]
            atomic_json(folder / "events.json", state)
    state = _state(folder) if (folder / "events.json").exists() else dict(events=[])
    current_time=time.time() if now is None else now
    for row in state.get("events",[]):
        if row.get("status")=="silenced" and row.get("silenced_until",0)<=current_time:
            row["status"]="pending"
    for alert in alerts:
        match=next((row for row in reversed(state.get("events",[])) if row.get("kind")=="alert" and row.get("key")==alert["key"]),None)
        alert["disposition"]=match.get("status","pending") if match else "pending"
        alert["event_id"]=match["id"] if match else None
    result["events"] = list(reversed(state.get("events", [])[-60:]))
    return result


def disposition(group,event_id,action,actor,silence_minutes=30):
    if action not in ("confirm","silence"):
        raise ValueError("仅支持确认或静默")
    if action=="silence" and (isinstance(silence_minutes,bool) or not isinstance(silence_minutes,int) or not 1<=silence_minutes<=1440):
        raise ValueError("静默时间须为1至1440分钟")
    with _lock(group) as folder:
        state=_state(folder)
        event=next((row for row in state.get("events",[]) if row["id"]==event_id and row["kind"]=="alert"),None)
        if event is None:raise ValueError("告警不存在或已归档")
        now=time.time()
        event.update(status="confirmed" if action=="confirm" else "silenced",handled_by=actor,handled_at=now)
        if action=="silence":event["silenced_until"]=now+silence_minutes*60
        else:event.pop("silenced_until",None)
        _event(state,"alert_"+action,"告警 "+str(event_id)+(" 已确认" if action=="confirm" else " 已静默"),actor,
               target=group["id"],result="saved")
        atomic_json(folder/"events.json",state)
    return event


class RiskObserver:
    """Backend alert loop. Closing the web page does not stop an enabled policy."""
    def __init__(self, data_mgr, dog):
        self.data_mgr, self.dog = data_mgr, dog
        self.task = None
        self.last_scan = None
        self.error_count = 0
        self.stop_requested = None

    async def start(self):
        self.stop_requested = asyncio.Event()
        self.task = asyncio.create_task(self._run())

    async def stop(self):
        if self.task:
            # Cancellation of to_thread does not stop its worker. Signal and
            # await the in-flight local-file scan before monitor teardown.
            self.stop_requested.set()
            await self.task

    def tick(self):
        for group in self.data_mgr.get_groups():
            try:
                # Read-only for every group unless its user explicitly enabled alerts.
                path = _root(group) / "policy.json"
                if path.exists() and read_policy(group)["enabled"]:
                    evaluate(group, self.dog.isRunning(group["id"]), persist=True)
            except (OSError, ValueError, KeyError, TypeError):
                self.error_count += 1
        self.last_scan = time.time()

    async def _run(self):
        while not self.stop_requested.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                # One malformed policy/group must not silently kill the alert loop.
                self.error_count += 1
            try:
                await asyncio.wait_for(self.stop_requested.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass


def register_risk_routes(app, data_mgr, dog, check_auth):
    observer = RiskObserver(data_mgr, dog)
    # Compose with the monitor's existing lifespan (push server/watchdog shutdown).
    previous_lifespan = app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            await observer.start()
            try:
                yield state
            finally:
                await observer.stop()
    app.router.lifespan_context = lifespan

    def authorize(request, token, groupid, write=False,operate=False):
        authenticated, info = check_auth(request, token)
        if not authenticated:
            return None, info
        if not data_mgr.has_group(groupid):
            return None, dict(result=-1, message="组合不存在")
        if not can_read(info) or not group_allowed(info,groupid):
            return None, dict(result=-403, message="无权访问当前组合")
        if write and not can_admin(info):
            return None, dict(result=-403, message="仅管理员可修改风控")
        if operate and not can_operate(info):
            return None, dict(result=-403, message="需要操作员或管理员权限")
        return info, None

    @app.post("/mgr/qryrisk_state", tags=["风控中心"])
    def query(request: Request, groupid: str = Body(..., embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid)
        if error:
            return error
        try:
            result = evaluate(data_mgr.get_group(groupid), dog.isRunning(groupid))
            result.update(result=0, message="OK", native=native_config(data_mgr.get_group(groupid)),
                can_control=can_admin(info),can_operate=can_operate(info),
                observer=dict(period_seconds=5, last_scan=observer.last_scan,
                              online=observer.last_scan is not None and 0 <= time.time()-observer.last_scan < 15,
                              error_count=observer.error_count))
            return result
        except (OSError, ValueError, TypeError) as exc:
            return dict(result=-3, message=str(exc))

    @app.post("/mgr/cmt_risk_policy", tags=["风控中心"])
    def policy(request: Request, groupid: str = Body(..., embed=True),
               policy: dict = Body(..., embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        try:
            saved = save_policy(data_mgr.get_group(groupid), policy, str(info.get("loginid", "admin")))
            data_mgr.log_action(info, "risk_policy", json.dumps(dict(target=groupid,result="saved")))
            return dict(result=0, message="后台预警设置已保存；不会自动交易", policy=saved)
        except (OSError, ValueError, TypeError) as exc:
            return dict(result=-3, message=str(exc))

    @app.post("/mgr/cmt_native_risk", tags=["风控中心"])
    def native(request: Request, groupid: str = Body(..., embed=True),
               params: dict = Body(..., embed=True), restart_ack: bool = Body(False, embed=True),
               token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        if not restart_ack:
            return dict(result=-2, message="须确认保存后需要人工重启组合；本接口不会重启或确认加载")
        try:
            saved = save_native(data_mgr.get_group(groupid), params, str(info.get("loginid", "admin")))
            data_mgr.log_action(info, "native_risk_config", json.dumps(dict(target=groupid,result="saved",restart_required=True)))
            return dict(result=0, message="原生配置已保存，须人工重启组合并核对加载日志", native=saved,
                        restart_required=True, execution_confirmed=False)
        except (OSError, ValueError, TypeError) as exc:
            return dict(result=-3, message=str(exc))

    @app.post("/mgr/risk_evaluate", tags=["风控中心"])
    def inspect(request: Request, groupid: str = Body(..., embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, operate=True)
        if error:
            return error
        try:
            result = evaluate(data_mgr.get_group(groupid), dog.isRunning(groupid), persist=True)
            result.update(result=0, message="已检查；未执行自动交易动作")
            return result
        except (OSError, ValueError, TypeError) as exc:
            return dict(result=-3, message=str(exc))

    @app.post("/mgr/risk_baseline",tags=["风控中心"])
    def manual(request:Request,groupid:str=Body(...,embed=True),value:float=Body(...,embed=True),
               channel:str=Body(...,embed=True),currency:str=Body(...,embed=True),tradingday:str=Body(...,embed=True),
               reason:str=Body(...,embed=True),token:str=Body(None,embed=True)):
        info,error=authorize(request,token,groupid,True)
        if error:return error
        try:
            group=data_mgr.get_group(groupid)
            if channel not in _channels(group):raise ValueError("通道不存在")
            snapshot=risk_snapshot(group,dog.isRunning(groupid))
            observed={str(row.get("trading_date")) for row in snapshot["broker"]["accounts"]}
            if snapshot.get("trading_date"):observed.add(str(snapshot["trading_date"]))
            if tradingday not in observed:raise ValueError("交易日须与柜台或引擎已记录的交易日一致")
            saved=save_manual_baseline(group,value,channel,currency,tradingday,reason,info["loginid"])
            data_mgr.log_action(info,"risk_baseline",json.dumps(dict(target=groupid,result="frozen",tradingday=tradingday,source="manual")))
            return dict(result=0,message="手工基准已冻结；来源已标注",baseline=saved)
        except (OSError,ValueError,TypeError) as exc:
            return dict(result=-3,message=str(exc))

    @app.post("/mgr/risk_alert_action",tags=["风控中心"])
    def handle_alert(request:Request,groupid:str=Body(...,embed=True),event_id:int=Body(...,embed=True),
                     action:str=Body(...,embed=True),silence_minutes:int=Body(30,embed=True),token:str=Body(None,embed=True)):
        info,error=authorize(request,token,groupid,operate=True)
        if error:return error
        try:
            event=disposition(data_mgr.get_group(groupid),event_id,action,info["loginid"],silence_minutes)
            data_mgr.log_action(info,"risk_alert_"+action,json.dumps(dict(target=groupid,event_id=event_id,result=event["status"])))
            return dict(result=0,message="告警已确认" if action=="confirm" else "告警已静默；风险计算仍继续",event=event)
        except (OSError,ValueError,TypeError) as exc:
            return dict(result=-3,message=str(exc))
    return observer
