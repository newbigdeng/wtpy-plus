"""Manage explicit native CTP subscriptions in registered datahub profiles."""
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading

from fastapi import Body, Request
import yaml

from .ChannelProfiles import channel_transition_lock, load_profiles, selected_profile
from .Roles import can_read, can_operate, group_allowed


def _inside(root, folder, name):
    path = (folder / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError("行情配置必须位于已登记的通道环境内")
    return path


def _load(path):
    content = path.read_text(encoding="utf-8-sig")
    value = json.loads(content) if path.suffix == ".json" else yaml.safe_load(content)
    if not isinstance(value, dict):
        raise ValueError("行情配置必须为对象")
    return value


def _write(path, content):
    # Replacing one complete document also prevents a collector seeing partial YAML.
    mode = path.stat().st_mode & 0o777
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".subscription-", delete=False) as out:
            temporary = Path(out.name)
            os.chmod(temporary, mode)
            out.write(content)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _configured_contracts(parser, catalog):
    """Match ParserAdapter's code > exchange filter > all-basefiles precedence."""
    codes = [item.strip() for item in str(parser.get("code") or "").split(",") if item.strip()]
    exchanges = [item.strip() for item in str(parser.get("filter") or "").split(",") if item.strip()]
    if codes:
        selected = []
        for item in catalog:
            keys = {item["ticker"], item["subscription_code"], item["exchange"] + "." + item["product"]}
            if keys.intersection(codes):
                selected.append(item["subscription_code"])
        return selected, "contracts"
    return ([item["subscription_code"] for item in catalog
             if not exchanges or item["exchange"] in exchanges],
            "exchanges" if exchanges else "all_basefiles")


def _ctp_library(root):
    wrapper = Path(__file__).resolve().parents[1] / "wrapper/linux"
    for directory in (root / "run/traders", root / "run", root / "loader",
                      Path("/opt/wt/native/traders"), Path("/opt/wt/native"), wrapper / "traders", wrapper):
        for name in ("thosttraderapi_se.so", "libthosttraderapi_se.so"):
            path = directory / name
            if path.is_file():
                return path.resolve()
    return None


def _query_native_contracts(config, folder):
    # CTPLoader calls exit(0) when the final instrument arrives. Never load it in the web process.
    code = "import json,sys; from wtpy import ContractLoader; ContractLoader().start_with_config(json.load(sys.stdin))"
    try:
        result = subprocess.run([sys.executable, "-c", code], input=json.dumps(config),
                                text=True, cwd=folder, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=45)
    except subprocess.TimeoutExpired:
        raise ValueError("CTP 合约查询超时；请检查当前环境服务时段和交易前置连接") from None
    if result.returncode != 0 or not (folder / "contracts.json").is_file():
        raise ValueError("CTP 未返回完整合约表；请检查当前环境的登录和认证状态")
    return _load(folder / "contracts.json"), _load(folder / "commodities.json")


class MarketSubscriptions:
    def __init__(self, data_mgr, dog, manifest_path=None):
        self.data_mgr, self.dog = data_mgr, dog
        self.manifest_path = manifest_path
        self.refresh_lock = threading.Lock()

    def _require_stopped(self, ctx):
        if not ctx["current"] or ctx["current"]["id"] != ctx["profile"]["id"]:
            raise ValueError("请在当前选定的通道环境更新行情资料")
        for group in self.data_mgr.get_groups():
            if Path(group["path"]).resolve().is_relative_to(ctx["root"]) and self.dog.isRunning(group["id"]):
                raise ValueError("仍有组合使用此数据中台，请先停止相关组合")

    def context(self, groupid, profileid=None):
        group = self.data_mgr.get_group(groupid)
        profiles = load_profiles(self.manifest_path).get(groupid, [])
        current = selected_profile(group, profiles)
        profile = next((item for item in profiles if item["id"] == profileid), None) if profileid else current
        if profile is None:
            raise ValueError("请先选择已登记的通道环境")
        root = Path(profile["root"]).resolve()
        folder = root / "datahub"
        config = _load(_inside(root, folder, "dtcfg.ctp.yaml"))
        name = config.get("parsers")
        if not isinstance(name, str):
            raise ValueError("数据中台未配置独立行情通道文件")
        parser_path = _inside(root, folder, name)
        parser_config = _load(parser_path)
        parsers = [item for item in parser_config.get("parsers", [])
                   if isinstance(item, dict) and item.get("active")
                   and "parserctp" in str(item.get("module", "")).lower()]
        if len(parsers) != 1:
            raise ValueError("订阅管理需要恰好一个启用的 CTP 行情通道")
        bases = config.get("basefiles", {})
        files = {}
        for key in ("contract", "commodity"):
            name = bases.get(key)
            if not isinstance(name, str) or "," in name:
                raise ValueError("订阅管理需要单独的 WT 合约表和品种表")
            files[key] = _inside(root, folder, name)
        contracts, commodities = _load(files["contract"]), _load(files["commodity"])
        catalog = []
        for exchange, rows in contracts.items():
            for ticker, item in rows.items():
                product = item.get("product", "")
                commodity = commodities.get(exchange, {}).get(product)
                if not isinstance(commodity, dict):
                    continue
                standard = (exchange + "." + product + "." + ticker[len(product):]
                            if product and ticker.startswith(product) else exchange + "." + ticker)
                catalog.append({"code": standard, "subscription_code": exchange + "." + ticker,
                                "ticker": ticker, "exchange": exchange, "product": product,
                                "name": item.get("name", ticker),
                                "product_name": commodity.get("name", product),
                                "price_tick": commodity.get("pricetick"),
                                "volume_scale": commodity.get("volscale"),
                                "session": commodity.get("session"),
                                "expire_date": item.get("expiredate")})
        catalog.sort(key=lambda item: (item["exchange"], item["product"], item["ticker"]))
        return {"group": group, "profile": profile, "current": current, "root": root,
                "folder": folder, "config": config, "parser_path": parser_path,
                "parser_config": parser_config, "parser": parsers[0],
                "files": files, "catalog": catalog}

    def snapshot(self, groupid, profileid=None):
        with channel_transition_lock:
            ctx = self.context(groupid, profileid)
            profile, catalog = ctx["profile"], ctx["catalog"]
            selected, mode = _configured_contracts(ctx["parser"], catalog)
            counts = Counter((item["exchange"], item["product"]) for item in catalog)
            products = []
            for exchange, product in counts:
                item = next(row for row in catalog if row["exchange"] == exchange and row["product"] == product)
                products.append({"code": exchange + "." + product, "exchange": exchange,
                                 "product": product, "name": item["product_name"],
                                 "contract_count": counts[(exchange, product)]})
            for item in catalog:
                item["selected"] = item["subscription_code"] in selected
            running = bool(self.dog.isRunning(groupid))
            return {"result": 0, "message": "OK", "groupid": groupid,
                    "current_profile": ctx["current"]["id"] if ctx["current"] else None,
                    "profileid": profile["id"], "profile_label": profile.get("label", profile["id"]),
                    "datahub_id": profile["datahub_id"],
                    "datahub_running": bool(self.dog.isRunning(profile["datahub_id"])),
                    "process_running": running, "parser_id": ctx["parser"].get("id", ""),
                    "selection_mode": mode, "catalog": catalog, "products": products,
                    "selected": selected,
                    "source": "WT 数据中台订阅配置", "active_subscriptions_confirmed": False,
                    "metadata_time": int(max(path.stat().st_mtime for path in ctx["files"].values()) * 1000),
                    "catalog_message": "来自当前环境的 WT 合约表；列表不会猜测或自动订阅新合约",
                    "can_refresh": not running and bool(ctx["current"] and ctx["current"]["id"] == profile["id"])
                                   and (ctx["root"] / "common/commodity-reference.json").is_file()
                                   and _ctp_library(ctx["root"]) is not None,
                    "can_apply": not running and bool(ctx["current"] and ctx["current"]["id"] == profile["id"])}

    def _plan(self, groupid, profileid, contracts):
        ctx = self.context(groupid, profileid)
        if not contracts:
            raise ValueError("请至少选择一个具体合约；WT 的空订阅配置会订阅全部合约")
        catalog = {item["subscription_code"]: item for item in ctx["catalog"]}
        selected = sorted(set(contracts))
        if any(code not in catalog for code in selected):
            raise ValueError("只能选择当前 WT 合约表中的具体合约，请先更新合约资料")
        old, mode = _configured_contracts(ctx["parser"], ctx["catalog"])
        current = set(old)
        configured = sorted(item.strip() for item in str(ctx["parser"].get("code") or "").split(",") if item.strip())
        return ctx, {"result": 0, "message": "OK", "profileid": ctx["profile"]["id"],
                     "selected": selected, "added": sorted(set(selected) - current),
                     "removed": sorted(current - set(selected)),
                     "changed": configured != selected or bool(ctx["parser"].get("filter")),
                     "requires_restart": bool(self.dog.isRunning(ctx["profile"]["datahub_id"])),
                     "can_apply": not self.dog.isRunning(groupid) and bool(ctx["current"] and
                                      ctx["current"]["id"] == ctx["profile"]["id"])}

    def preview(self, groupid, profileid, contracts):
        with channel_transition_lock:
            return self._plan(groupid, profileid, contracts)[1]

    def apply(self, groupid, profileid, contracts, restart_collector=False):
        with channel_transition_lock:
            ctx, result = self._plan(groupid, profileid, contracts)
            if not result["can_apply"]:
                raise ValueError("请先停止组合，并在当前选定的通道环境保存订阅")
            profile, root = ctx["profile"], ctx["root"]
            self._require_stopped(ctx)
            appid = profile["datahub_id"]
            if restart_collector:
                app = self.dog.getAppConf(appid)
                if not app or Path(app["folder"]).resolve() != ctx["folder"] or app.get("param") != "-u " + str(ctx["folder"] / "run_datahub.py"):
                    raise ValueError("数据中台尚未正确登记，请先在调度中心检查启动配置")
            original = ctx["parser_path"].read_text(encoding="utf-8-sig")
            if result["changed"]:
                config = deepcopy(ctx["parser_config"])
                parser = next(item for item in config["parsers"] if item == ctx["parser"])
                parser["code"] = ",".join(result["selected"])
                # A stale exchange filter otherwise drops ticks selected by code.
                parser["filter"] = ""
                _write(ctx["parser_path"], yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
            restarted = False
            if restart_collector:
                try:
                    self.dog.restart(appid)
                    restarted = bool(self.dog.isRunning(appid))
                    if not restarted:
                        raise RuntimeError("数据中台未启动")
                except Exception:
                    if result["changed"]:
                        _write(ctx["parser_path"], original)
                    raise RuntimeError("数据中台启动失败，原订阅配置已恢复；请在调度中心检查进程") from None
            result.update(saved=True, collector_restarted=restarted,
                          restart_required=bool(result["changed"] and result["requires_restart"] and not restarted),
                          datahub_id=appid, engine_started=False,
                          message="订阅已保存" + ("，数据中台已启动；行情登录以运行状态为准" if restarted else ""))
            return result

    def refresh(self, groupid, profileid=None):
        if not self.refresh_lock.acquire(blocking=False):
            raise ValueError("已有合约资料查询正在进行，请稍后刷新列表")
        try:
            with channel_transition_lock:
                ctx = self.context(groupid, profileid)
                self._require_stopped(ctx)
                # Enlarging master data must never enlarge an implicit native subscription.
                configured = [code.strip() for code in str(ctx["parser"].get("code") or "").split(",") if code.strip()]
                known = {item["subscription_code"] for item in ctx["catalog"]}
                if not configured or not set(configured).issubset(known):
                    raise ValueError("请先保存具体合约订阅，避免更新合约表后扩大原生通配订阅")
                root, folder = ctx["root"], ctx["folder"]
                reference = _load(_inside(root, root, "common/commodity-reference.json"))
                sessions = _load(_inside(root, folder, ctx["config"]["basefiles"]["session"]))
                engine = _load(_inside(root, root / "run", "config.ctp-observe.yaml"))
                traders = _load(_inside(root, root / "run", engine["traders"])).get("traders", [])
                active = [item for item in traders if item.get("active") and "traderctp" in str(item.get("module", "")).lower()]
                if len(active) != 1:
                    raise ValueError("合约查询需要恰好一个启用的 CTP 交易通道配置")
                ctp = {key: active[0].get(key, "") for key in ("front", "broker", "user", "pass", "appid", "authcode")}
                if any(not ctp[key] for key in ("front", "broker", "user", "pass")):
                    raise ValueError("当前环境的 CTP 合约查询登录配置不完整")
                library = _ctp_library(root)
                if library is None:
                    raise ValueError("容器未安装 CTP 原生交易接口库")
                ctp["module"] = str(library)
                replay = engine.get("monitor", {}).get("market_mode") == "simnow_replay"
                if replay and "SIMNOW_TEST" not in sessions:
                    raise ValueError("回放环境缺少独立的 SIMNOW_TEST 时段配置")
                existing = _load(ctx["files"]["commodity"])
                for exchange, rows in existing.items():
                    for product, commodity in rows.items():
                        merged = dict(reference.get(exchange, {}).get(product, {}))
                        merged.update(commodity)
                        reference.setdefault(exchange, {})[product] = merged
                mapping = {}
                for exchange, rows in reference.items():
                    for product, commodity in rows.items():
                        session = "SIMNOW_TEST" if replay else commodity.get("session")
                        if session in sessions:
                            mapping[exchange + "." + product] = session
                original = {key: path.read_text(encoding="utf-8-sig") for key, path in ctx["files"].items()}
                parser_text = ctx["parser_path"].read_text(encoding="utf-8-sig")
            # Network authentication/query runs outside the monitor's global process-control lock.
            state = root / "state"
            state.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".contract-refresh-", dir=state) as temporary:
                target = Path(temporary)
                maps = target / "sessions.ini"
                maps.write_text("[Session]\n" + "\n".join(key + "=" + value for key, value in mapping.items()), encoding="utf-8")
                config = {"ctp": ctp, "config": {"path": str(target) + "/", "mask": 1,
                          "commfile": "commodities.json", "contfile": "contracts.json", "mapfiles": str(maps)}}
                queried, commodities = _query_native_contracts(config, target)
            contracts = json.loads(original["contract"])
            refreshed = json.loads(original["commodity"])
            updated, products, skipped = 0, set(), set()
            for exchange, rows in queried.items():
                for ticker, contract in rows.items():
                    product = contract.get("product")
                    key = exchange + "." + str(product)
                    commodity = commodities.get(exchange, {}).get(product)
                    if key not in mapping or not commodity:
                        skipped.add(key)
                        continue
                    merged = dict(reference.get(exchange, {}).get(product, {}))
                    merged.update(commodity)
                    merged["session"] = mapping[key]
                    refreshed.setdefault(exchange, {})[product] = merged
                    contracts.setdefault(exchange, {})[ticker] = contract
                    updated += 1
                    products.add(key)
            if not updated:
                raise ValueError("CTP 返回合约没有匹配的 WT 交易时段，原合约表保持不变")
            with channel_transition_lock:
                current = self.context(groupid, profileid)
                self._require_stopped(current)
                if current["root"] != root or current["parser_path"].read_text(encoding="utf-8-sig") != parser_text or any(
                        current["files"][key].read_text(encoding="utf-8-sig") != text for key, text in original.items()):
                    raise ValueError("查询期间环境或行情配置发生变化，请重新更新合约资料")
                _write(ctx["files"]["commodity"], json.dumps(refreshed, ensure_ascii=False, indent=2))
                try:
                    _write(ctx["files"]["contract"], json.dumps(contracts, ensure_ascii=False, indent=2))
                except Exception:
                    _write(ctx["files"]["commodity"], original["commodity"])
                    raise
            return {"result": 0, "message": "CTP 合约资料已更新，订阅保持不变；现有历史数据保留",
                    "profileid": ctx["profile"]["id"], "updated_contracts": updated,
                    "updated_products": len(products), "skipped_products": sorted(skipped),
                    "subscriptions_changed": False, "engine_started": False,
                    "metadata_time": int(ctx["files"]["contract"].stat().st_mtime * 1000)}
        finally:
            self.refresh_lock.release()


def register_market_subscription_routes(app, data_mgr, dog, check_auth, manifest_path=None):
    manager = MarketSubscriptions(data_mgr, dog, manifest_path)

    def authorize(request, token, groupid, control=False):
        ok, info = check_auth(request, token)
        if not ok:
            return None, info
        if not (can_operate(info) if control else can_read(info)) or not group_allowed(info, groupid):
            return None, {"result": -403, "message": "无权修改行情订阅" if control else "无权查看行情订阅"}
        if not data_mgr.has_group(groupid):
            return None, {"result": -1, "message": "组合不存在"}
        return info, None

    def failure(error):
        if isinstance(error, (ValueError, RuntimeError)) and not isinstance(error, yaml.YAMLError):
            return {"result": -3, "message": str(error)}
        return {"result": -3, "message": "行情配置读取失败，请检查已登记环境的配置文件"}

    @app.post("/mgr/market/subscriptions", tags=["行情订阅"])
    def query(request: Request, groupid: str = Body(..., embed=True),
              profileid: str = Body(None, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid)
        if error:
            return error
        try:
            result = manager.snapshot(groupid, profileid)
            result["can_manage"] = can_operate(info)
            result["can_apply"] = result["can_apply"] and result["can_manage"]
            result["can_refresh"] = result["can_refresh"] and result["can_manage"]
            return result
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
            return failure(error)

    @app.post("/mgr/market/subscriptions/preview", tags=["行情订阅"])
    def preview(request: Request, groupid: str = Body(..., embed=True),
                contracts: list[str] = Body(..., embed=True),
                profileid: str = Body(None, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        try:
            return manager.preview(groupid, profileid, contracts)
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
            return failure(error)

    @app.post("/mgr/market/subscriptions/apply", tags=["行情订阅"])
    def apply(request: Request, groupid: str = Body(..., embed=True),
              contracts: list[str] = Body(..., embed=True), restart_collector: bool = Body(False, embed=True),
              profileid: str = Body(None, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        try:
            result = manager.apply(groupid, profileid, contracts, restart_collector)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, yaml.YAMLError) as error:
            return failure(error)
        data_mgr.log_action(info, "market_subscription", groupid + "/" + result["profileid"])
        return result

    @app.post("/mgr/market/subscriptions/refresh", tags=["行情订阅"])
    def refresh(request: Request, groupid: str = Body(..., embed=True),
                profileid: str = Body(None, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        try:
            result = manager.refresh(groupid, profileid)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, yaml.YAMLError) as error:
            return failure(error)
        data_mgr.log_action(info, "market_contracts", groupid + "/" + result["profileid"])
        return result

    return manager
