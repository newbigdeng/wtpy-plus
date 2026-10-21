import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Body, Request
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, FileResponse
import uvicorn

import json
import yaml
import datetime
import os
import hashlib
import sys
import chardet
import pytz
import base64
import secrets

from .WtLogger import WtLogger
from .DataMgr import DataMgr, backup_file
from .PushSvr import PushServer
from .WatchDog import WatchDog, WatcherSink
from .WtBtMon import WtBtMon
from .StrategyControl import register_strategy_routes
from .ManagedStrategies import register_managed_routes, validate_managed_start
from .StrategyLibrary import register_library_routes
from .HistoryCatalog import register_history_catalog_routes
from .RiskLibrary import register_risk_library_routes
from .RiskControl import register_risk_routes
from .ChannelProfiles import register_channel_routes, channel_transition_lock
from .MarketSubscriptions import register_market_subscription_routes
from .MarketHistory import register_market_history_routes
from .PlusBacktestRoutes import register_plus_routes
from .TradeRiskApi import register_trade_risk_routes
from .Roles import READ_ROLES, RESEARCH_ROLES, can_read, can_operate, can_admin, group_allowed, can_research_read
from .GuestAccess import GuestAccessMiddleware, LoginAttempts, guest_session_valid, GUEST_READ_PATHS
from pathlib import Path
from wtpy import WtDtServo
import signal
import platform

def isWindows():
    if "windows" in platform.system().lower():
        return True

    return False

def get_session(request: Request, key: str):
    if key not in request["session"]:
        return None
    return request["session"][key]

def set_session(request: Request, key: str, val):
    request["session"][key] = val

def pop_session(request: Request, key: str):
    if key not in request["session"]:
        return
    request["session"].pop(key)

def AES_Encrypt(key:str, data:str):
    from Crypto.Cipher import AES # pip install pycryptodome
    vi = '0102030405060708'
    pad = lambda s: s + (16 - len(s) % 16) * chr(16 - len(s) % 16)
    data = pad(data)
    # 字符串补位
    cipher = AES.new(key.encode('utf8'), AES.MODE_CBC, vi.encode('utf8'))
    encryptedbytes = cipher.encrypt(data.encode('utf8'))
    # 加密后得到的是bytes类型的数据
    encodestrs = base64.b64encode(encryptedbytes)
    # 使用Base64进行编码,返回byte字符串
    enctext = encodestrs.decode('utf8')
    # 对byte字符串按utf-8进行解码
    return enctext

def AES_Decrypt(key:str, data:str):
    from Crypto.Cipher import AES # pip install pycryptodome
    vi = '0102030405060708'
    data = data.encode('utf8')
    encodebytes = base64.decodebytes(data)
    # 将加密数据转换位bytes类型数据
    cipher = AES.new(key.encode('utf8'), AES.MODE_CBC, vi.encode('utf8'))
    text_decrypted = cipher.decrypt(encodebytes)
    unpad = lambda s: s[0:-s[-1]]
    text_decrypted = unpad(text_decrypted)
    # 去补位
    text_decrypted = text_decrypted.decode('utf8')
    return text_decrypted


# 获取文件最后N行的函数
def get_tail(filename, N: int = 100, encoding="GBK"):
    filesize = os.path.getsize(filename)
    blocksize = 10240
    dat_file = open(filename, 'r', encoding=encoding)
    last_line = ""
    if filesize > blocksize:
        maxseekpoint = (filesize // blocksize)
        dat_file.seek((maxseekpoint - 1) * blocksize)
    elif filesize:
        dat_file.seek(0, 0)
    lines = dat_file.readlines()
    if lines:
        last_line = lines[-N:]
    dat_file.close()
    return ''.join(last_line), len(last_line)


def check_auth(request: Request, token: str = None, seckey: str = None):
    settings = request.app.state
    tokeninfo = get_session(request, "tokeninfo")
    if tokeninfo and tokeninfo.get("role") == "guest":
        if not guest_session_valid(request.scope):
            return False, {"result": -999, "message": "登录已过期"}
        info = dict(tokeninfo)
        if request.url.path in GUEST_READ_PATHS and request.url.path.startswith("/bt/"):
            info["loginid"] = settings.monitor_owner
        return True, info
    if token is not None:
        if getattr(settings, "guest_enabled", False):
            return False, {"result": -999, "message": "请使用账户会话登录"}
        try:
            tokeninfo = json.loads(AES_Decrypt(seckey, token))
        except (ValueError, TypeError, KeyError):
            tokeninfo = None
    now = datetime.datetime.now().strftime("%Y.%m.%d %H:%M:%S")
    if not isinstance(tokeninfo, dict) or now >= tokeninfo.get("expiretime", ""):
        return False, {"result": -999, "message": "请先登录"}
    if getattr(settings, "guest_enabled", False) and (tokeninfo.get("loginid") != settings.monitor_owner or tokeninfo.get("auth_version") != settings.auth_version):
        return False, {"result": -999, "message": "请重新登录"}
    return True, tokeninfo

def get_cfg_tree(root: str, name: str):
    if not os.path.exists(root):
        return {
            "label": name,
            "path": root,
            "exist": False,
            "isfile": False,
            "children": []
        }

    if os.path.isfile(root):
        return {
            "label": name,
            "path": root,
            "exist": False,
            "isfile": True
        }

    ret = {
        "label": name,
        "path": root,
        "exist": True,
        "isfile": False,
        "children": []
    }

    filepath = os.path.join(root, "run.py")
    ret['children'].append({
        "label": "run.py",
        "path": filepath,
        "exist": True,
        "isfile": True,
        "children": []
    })

    filepath = os.path.join(root, "config.json")
    isYaml = False
    if not os.path.exists(filepath):
        filepath = os.path.join(root, "config.yaml")
        isYaml = True

    ret['children'].append({
        "label": "config.yaml" if isYaml else "config.json",
        "path": filepath,
        "exist": True,
        "isfile": True,
        "children": []
    })

    f = open(filepath, "rb")
    content = f.read()
    f.close()

    # 加一段编码检查的逻辑
    encoding = chardet.detect(content[:500])["encoding"]
    content = content.decode(encoding or "utf-8")

    if isYaml:
        cfgObj = yaml.full_load(content)
    else:
        cfgObj = json.loads(content)

    if "executers" in cfgObj:
        filename = cfgObj["executers"]
        if type(filename) == str:
            filepath = os.path.join(root, filename)
            ret['children'].append({
                "label": filename,
                "path": filepath,
                "exist": True,
                "isfile": True,
                "children": []
            })

    if "parsers" in cfgObj:
        filename = cfgObj["parsers"]
        if type(filename) == str:
            filepath = os.path.join(root, filename)
            ret['children'].append({
                "label": filename,
                "path": filepath,
                "exist": True,
                "isfile": True,
                "children": []
            })

    if "traders" in cfgObj:
        filename = cfgObj["traders"]
        if type(filename) == str:
            filepath = os.path.join(root, filename)
            ret['children'].append({
                "label": filename,
                "path": filepath,
                "exist": True,
                "isfile": True,
                "children": []
            })

    filepath = os.path.join(root, 'generated')
    ret["children"].append(get_path_tree(filepath, 'generated', True))

    return ret


def get_path_tree(root: str, name: str, hasFile: bool = True):
    if not os.path.exists(root):
        return {
            "label": name,
            "path": root,
            "exist": False,
            "isfile": False,
            "children": []
        }

    if os.path.isfile(root):
        return {
            "label": name,
            "path": root,
            "exist": False,
            "isfile": True
        }

    ret = {
        "label": name,
        "path": root,
        "exist": True,
        "isfile": False,
        "children": []
    }
    files = os.listdir(root, )
    for filename in files:
        if filename in ['__pycache__', '.vscode', 'wtpy', '__init__.py']:
            continue
        if filename[-3:] == 'pyc':
            continue

        filepath = os.path.join(root, filename)
        if os.path.isfile(filepath):
            if not hasFile:
                continue
            else:
                ret["children"].append({
                    "label": filename,
                    "path": filepath,
                    "exist": True,
                    "isfile": True})
        else:
            ret["children"].append(get_path_tree(filepath, filename, hasFile))

        ay1 = list()
        ay2 = list()
        for item in ret["children"]:
            if item["isfile"]:
                ay2.append(item)
            else:
                ay1.append(item)
        ay = ay1 + ay2
        ret["children"] = ay
    return ret


class WtMonSink:

    def __init__(self):
        return

    def notify(self, level: str, msg: str):
        return

from fastapi.middleware.cors import CORSMiddleware

class WtMonSvr(WatcherSink):

    def __init__(self, static_folder: str = "static/", deploy_dir="C:/", sink: WtMonSink = None, notifyTimeout:bool = True, stop_apps_on_shutdown:bool = False):
        '''
        WtMonSvr构造函数

        @static_folder      静态文件根目录
        @static_url_path    静态文件访问路径
        @deploy_dir         实盘部署目录
        '''

        self.logger = WtLogger(__name__, "WtMonSvr.log")
        self._sink_ = sink
        self.stop_apps_on_shutdown = stop_apps_on_shutdown

        # 数据管理器，主要用于缓存各组合的数据
        self.__data_mgr__ = DataMgr('data.db', logger=self.logger)

        self.__bt_mon__: WtBtMon = None
        self.__plus_mon__ = None
        self.__dt_servo__: WtDtServo = None

        # 秘钥和开启token访问，单独控制，减少依赖项
        self.__sec_key__ = ""
        self.__token_enabled__ = False

        # 看门狗模块，主要用于调度各个组合启动关闭
        self._dog = WatchDog(sink=self, db=self.__data_mgr__.get_db(), logger=self.logger)

        app = FastAPI(title="WtMonSvr", description="A http api of WtMonSvr", redoc_url=None, docs_url=None, openapi_url=None, version="1.0.0", lifespan=self._lifespan)
        app.state.guest_enabled = os.environ.get("WT_GUEST_ENABLED", "0") == "1"
        app.state.monitor_owner = os.environ.get("WT_MONITOR_OWNER", "").strip()
        app.state.auth_version = secrets.token_urlsafe(24)
        if app.state.guest_enabled:
            owner = self.__data_mgr__.get_user(app.state.monitor_owner)
            if owner is None or not can_admin(owner):
                raise ValueError("启用游客前，请设置 WT_MONITOR_OWNER 为现有管理员账户")
        app.state.login_attempts = LoginAttempts()
        app.add_middleware(GuestAccessMiddleware)
        app.add_middleware(SessionMiddleware,
            secret_key=os.environ.get("WT_SESSION_SECRET") or secrets.token_urlsafe(32),
            max_age=21600, session_cookie="WtMonSvr_sid", same_site="strict",
            https_only=os.environ.get("WT_COOKIE_SECURE", "0") == "1")
        app.add_middleware(GZipMiddleware, minimum_size=1000)
        origins = [item.strip() for item in os.environ.get("WT_ALLOWED_ORIGINS", "").split(",") if item.strip()]
        if origins:
            app.add_middleware(CORSMiddleware, allow_origins=origins,
                allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

        script_dir = os.path.dirname(__file__)
        static_folder = os.path.join(script_dir, static_folder)
        target_dir = os.path.join(static_folder,"console")
        app.mount("/console", StaticFiles(directory=target_dir, html=True), name="console")

        target_dir = os.path.join(static_folder,"mobile")
        app.mount("/mobile", StaticFiles(directory=target_dir, html=True), name="mobile")

        self.app = app
        self.worker = None
        self.deploy_dir = deploy_dir
        self.deploy_tree = None
        self.static_folder = static_folder
        self.notifyTimeout = notifyTimeout

        self.push_svr = PushServer(app, self.__data_mgr__, self.logger)

        self.init_mgr_apis(app)
        self.init_comm_apis(app)

    @asynccontextmanager
    async def _lifespan(self, app):
        self.push_svr.run()
        await self.push_svr.start()
        try:
            yield
        finally:
            if self.__plus_mon__ is not None:
                await asyncio.to_thread(self.__plus_mon__.shutdown)
            if self.__bt_mon__ is not None:
                await asyncio.to_thread(self.__bt_mon__.shutdown)
            await asyncio.to_thread(self._dog.shutdown, self.stop_apps_on_shutdown)
            await self.push_svr.stop()

    def enable_token(self, seckey: str = "WtMonSvr@2021"):
        '''
        启用访问令牌, 默认通过session方式验证
        注意: 这里如果启用令牌访问的话, 需要安装pycryptodome, 所以改成单独控制
        '''
        
        self.__sec_key__ = seckey
        self.__token_enabled__ = True

    def set_bt_mon(self, btMon: WtBtMon):
        '''
        设置回测管理器

        @btMon      回测管理器WtBtMon实例
        '''
        self.__bt_mon__ = btMon
        self.init_bt_apis(self.app)
        self.__plus_mon__ = register_plus_routes(self.app, btMon,
            lambda request, token: check_auth(request, token, self.__sec_key__))

    def set_dt_servo(self, dtServo: WtDtServo):
        '''
        设置DtServo

        @dtServo    本地数据伺服WtDtServo实例
        '''
        self.__dt_servo__ = dtServo

    def init_bt_apis(self, app: FastAPI):
        from .BacktestTemplates import STRATEGY_TYPES, normalize_strategy_type

        @app.get("/bt/strategytypes", tags=["回测管理接口"])
        @app.post("/bt/strategytypes", tags=["回测管理接口"])
        def qry_strategy_types(request: Request, token: str = Body(None, embed=True)):
            authenticated, info = check_auth(request, token, self.__sec_key__)
            if not authenticated:
                return info
            if not can_research_read(info):
                return {"result": -1, "message": "没有权限"}
            return {"result": 0, "message": "OK", "types": list(STRATEGY_TYPES.values())}

        @app.post("/bt/renamestra", tags=["回测管理接口"])
        def rename_research_strategy(request: Request, straid: str = Body(..., embed=True),
                name: str = Body(..., embed=True), token: str = Body(None, embed=True)):
            ok, user = check_auth(request, token, self.__sec_key__)
            if not ok:
                return user
            if user.get("role") not in RESEARCH_ROLES:
                return {"result": -403, "message": "需要策略研究权限"}
            try:
                name = self.__bt_mon__.rename_strategy(user["loginid"], straid, name)
            except (ValueError, OSError) as exc:
                return {"result": -2, "message": str(exc)}
            return {"result": 0, "message": "策略名称已修改", "name": name}

        @app.post("/bt/qrybtdetail", tags=["回测管理接口"])
        def qry_backtest_detail(request: Request,
                straid: str = Body(..., embed=True), btid: str = Body(..., embed=True),
                token: str = Body(None, embed=True)):
            authenticated, info = check_auth(request, token, self.__sec_key__)
            if not authenticated:
                return info
            if not can_research_read(info):
                return {"result": -1, "message": "没有权限"}
            if self.__bt_mon__ is None:
                return {"result": -3, "message": "回测管理器未配置"}
            detail = self.__bt_mon__.get_backtest_detail(info["loginid"], straid, btid)
            if detail is None:
                return {"result": -2, "message": "回测不存在或不属于该策略"}
            return {"result": 0, "message": "OK", **detail}

        # 拉取K线数据
        @app.post("/bt/qrybars", tags=["回测管理接口"])
        async def qry_bt_bars(
            request: Request,
            token: str = Body(None, title="访问令牌", embed=True),
            code: str = Body(..., title="合约代码", embed=True),
            period: str = Body(..., title="K线周期", embed=True),
            stime: int = Body(None, title="开始时间", embed=True),
            etime: int = Body(..., title="结束时间", embed=True),
            count: int = Body(None, title="数据条数", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if not can_research_read(userInfo):
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if role == "guest":
                import re
                if not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+", code) or period not in ("m1", "m5", "d", "d1"):
                    return {"result": -2, "message": "合约或周期无效"}
                stime = None
                count = max(1, min(count or 600, 2000))

            if self.__dt_servo__ is None:
                ret = {
                    "result": -2,
                    "message": "没有配置数据伺服"
                }
                return ret

            stdCode = code
            fromTime = stime
            dataCount = count
            endTime = etime

            bars = self.__dt_servo__.get_bars(stdCode=stdCode, period=period, fromTime=fromTime, dataCount=dataCount,
                                              endTime=endTime)
            if bars is None:
                ret = {
                    "result": -2,
                    "message": "Data not found"
                }
            else:
                bar_list = bars.to_df().to_dict(orient="records")
                for bar in bar_list:
                    bar["time"] = bar["bartime"]
                    bar["date"] = int(bar["bartime"]) if len(str(bar["bartime"])) == 8 else int(bar["bartime"]) // 10000

                ret = {
                    "result": 0,
                    "message": "Ok",
                    "bars": bar_list
                }

            return ret

        # 拉取用户策略列表
        @app.post("/bt/qrystras", tags=["回测管理接口"])
        @app.get("/bt/qrystras", tags=["回测管理接口"])
        async def qry_my_stras(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if not can_research_read(userInfo):
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            ret = {
                "result": 0,
                "message": "OK",
                "strategies": self.__bt_mon__.get_strategies(user)
            }

            return ret

        # 拉取策略代码
        @app.post("/bt/qrycode", tags=["回测管理接口"])
        async def qry_stra_code(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                straid: str = Body(..., title="策略ID", embed=True),
                btid: str = Body(None, title="回测快照ID", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if role not in RESEARCH_ROLES:
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                if not self.__bt_mon__.has_strategy(user, straid, btid):
                    ret = {
                        "result": -2,
                        "message": "策略代码不存在"
                    }
                else:
                    content = self.__bt_mon__.get_strategy_code(user, straid, btid)
                    ret = {
                        "result": 0,
                        "message": "OK",
                        "content": content
                    }

            return ret

        # 提交策略代码
        @app.post("/bt/setcode", tags=["回测管理接口"])
        def set_stra_code(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                straid: str = Body(..., title="策略ID", embed=True),
                content: str = Body(..., title="策略代码", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if role not in RESEARCH_ROLES:
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if len(content) == 0 or len(straid) == 0:
                ret = {
                    "result": -2,
                    "message": "策略ID和代码不能为空"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                if not self.__bt_mon__.has_strategy(user, straid):
                    ret = {
                        "result": -2,
                        "message": "策略不存在"
                    }
                else:
                    ret = self.__bt_mon__.set_strategy_code(user, straid, content)
                    if ret:
                        ret = {
                            "result": 0,
                            "message": "OK"
                        }
                    else:
                        ret = {
                            "result": -3,
                            "message": "保存策略代码失败"
                        }

            return ret

        # 添加用户策略
        @app.post("/bt/addstra", tags=["回测管理接口"])
        async def cmd_add_stra(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                name: str = Body(..., title="策略名称", embed=True),
                strategy_type: str = Body("cta", title="策略类型", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if role not in RESEARCH_ROLES:
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            name = name.strip()
            try:
                strategy_type = normalize_strategy_type(strategy_type)
            except ValueError as error:
                return {"result": -2, "message": str(error)}
            if len(name) == 0:
                ret = {
                    "result": -2,
                    "message": "策略名称不能为空"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -3,
                    "message": "回测管理器未配置"
                }
                return ret

            straInfo = self.__bt_mon__.add_strategy(user, name, strategy_type)
            if straInfo is None:
                ret = {
                    "result": -4,
                    "message": "策略添加失败"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "OK",
                    "strategy": straInfo
                }

            return ret

        # 删除用户策略
        @app.post("/bt/delstra", tags=["回测管理接口"])
        async def cmd_del_stra(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                straid: str = Body(..., title="策略ID", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if role not in RESEARCH_ROLES:
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if len(straid) == 0:
                ret = {
                    "result": -2,
                    "message": "策略ID不能为空"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                if not self.__bt_mon__.has_strategy(user, straid):
                    ret = {
                        "result": -2,
                        "message": "策略不存在"
                    }
                else:
                    ret = self.__bt_mon__.del_strategy(user, straid)
                    if ret:
                        ret = {
                            "result": 0,
                            "message": "OK"
                        }
                    else:
                        ret = {
                            "result": -3,
                            "message": "保存策略代码失败"
                        }

            return ret

        # 获取策略回测列表
        @app.post("/bt/qrystrabts", tags=["回测管理接口"])
        async def qry_stra_bts(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                straid: str = Body(..., title="策略ID", embed=True),
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if not can_research_read(userInfo):
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if len(straid) == 0:
                ret = {
                    "result": -2,
                    "message": "策略ID不能为空"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                if not self.__bt_mon__.has_strategy(user, straid):
                    ret = {
                        "result": -2,
                        "message": "策略不存在"
                    }
                else:
                    ret = {
                        "result": 0,
                        "message": "OK",
                        "backtests": self.__bt_mon__.get_backtests(user, straid)
                    }

            return ret

        # 获取策略回测信号
        @app.post("/bt/qrybtsigs", tags=["回测管理接口"])
        async def qry_stra_bt_signals(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                straid: str = Body(..., title="策略ID", embed=True),
                btid: str = Body(..., title="回测ID", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if not can_research_read(userInfo):
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if len(straid) == 0 or len(btid) == 0:
                ret = {
                    "result": -2,
                    "message": "策略ID和回测ID不能为空"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                if not self.__bt_mon__.has_strategy(user, straid, btid):
                    ret = {
                        "result": -2,
                        "message": "策略不存在"
                    }
                else:
                    try:
                        ret = {"result": 0, "message": "OK",
                               "signals": self.__bt_mon__.get_bt_signals(user, straid, btid)}
                    except ValueError as error:
                        ret = {"result": -4, "message": str(error)}

            return ret

        # 删除策略回测列表
        @app.post("/bt/delstrabt", tags=["回测管理接口"])
        async def cmd_del_stra_bt(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                btid: str = Body(..., title="回测ID", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if role not in RESEARCH_ROLES:
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if len(btid) == 0:
                ret = {
                    "result": -2,
                    "message": "回测ID不能为空"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                self.__bt_mon__.del_backtest(user, btid)
                ret = {
                    "result": 0,
                    "message": "OK"
                }

            return ret

        # 获取策略回测成交
        @app.post("/bt/qrybttrds", tags=["回测管理接口"])
        async def qry_stra_bt_trades(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                straid: str = Body(..., title="策略ID", embed=True),
                btid: str = Body(..., title="回测ID", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if not can_research_read(userInfo):
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if len(straid) == 0 or len(btid) == 0:
                ret = {
                    "result": -2,
                    "message": "策略ID和回测ID不能为空"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                if not self.__bt_mon__.has_strategy(user, straid, btid):
                    ret = {
                        "result": -2,
                        "message": "策略不存在"
                    }
                else:
                    try:
                        ret = {"result": 0, "message": "OK",
                               "trades": self.__bt_mon__.get_bt_trades(user, straid, btid)}
                    except ValueError as error:
                        ret = {"result": -4, "message": str(error)}

            return ret

        # 获取策略回测资金
        @app.post("/bt/qrybtfunds", tags=["回测管理接口"])
        async def qry_stra_bt_funds(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                straid: str = Body(..., title="策略ID", embed=True),
                btid: str = Body(..., title="回测ID", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if not can_research_read(userInfo):
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if len(straid) == 0 or len(btid) == 0:
                ret = {
                    "result": -2,
                    "message": "策略ID和回测ID不能为空"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                if not self.__bt_mon__.has_strategy(user, straid, btid):
                    ret = {
                        "result": -2,
                        "message": "策略不存在"
                    }
                else:
                    ret = {
                        "result": 0,
                        "message": "OK",
                        "funds": self.__bt_mon__.get_bt_funds(user, straid, btid)
                    }

            return ret

        # 获取策略回测回合
        @app.post("/bt/qrybtrnds", tags=["回测管理接口"])
        async def qry_stra_bt_rounds(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                straid: str = Body(..., title="策略ID", embed=True),
                btid: str = Body(..., title="回测ID", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if not can_research_read(userInfo):
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            if len(straid) == 0 or len(btid) == 0:
                ret = {
                    "result": -2,
                    "message": "策略ID和回测ID不能为空"
                }
                return ret

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                if not self.__bt_mon__.has_strategy(user, straid, btid):
                    ret = {
                        "result": -2,
                        "message": "策略不存在"
                    }
                else:
                    try:
                        ret = {"result": 0, "message": "OK",
                               "rounds": self.__bt_mon__.get_bt_rounds(user, straid, btid)}
                    except ValueError as error:
                        ret = {"result": -4, "message": str(error)}

            return ret

        @app.post("/bt/stopstrabt", tags=["回测管理接口"])
        def cmd_stop_stra_bt(request: Request,
                straid: str = Body(..., embed=True), btid: str = Body(..., embed=True),
                token: str = Body(None, embed=True)):
            authenticated, info = check_auth(request, token, self.__sec_key__)
            if not authenticated:
                return info
            if info["role"] not in RESEARCH_ROLES:
                return {"result": -1, "message": "没有权限"}
            stopped = self.__bt_mon__.stop_backtest(info["loginid"], straid, btid)
            return {"result": 0 if stopped else -2, "message": "OK" if stopped else "任务未运行"}

        # 启动策略回测
        @app.post("/bt/runstrabt", tags=["回测管理接口"])
        def cmd_run_stra_bt(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                straid: str = Body(..., title="策略ID", embed=True),
                stime: int = Body(..., title="开始时间", embed=True),
                etime: int = Body(..., title="结束时间", embed=True),
                capital: int = Body(500000, gt=0, title="本金", embed=True),
                slippage: int = Body(0, ge=0, title="滑点", embed=True),
                fill_model: str = Body("causal_touch", embed=True),
                event_delay: int = Body(0, ge=0, embed=True),
                participation_rate: float = Body(0, ge=0, le=1, embed=True),
                risk: dict = Body(None, embed=True),
                dataset: str = Body(None, embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo

            user = userInfo["loginid"]
            role = userInfo["role"]
            if role not in RESEARCH_ROLES:
                ret = {
                    "result": -1,
                    "message": "没有权限"
                }
                return ret

            curDt = int(datetime.datetime.now().strftime("%Y%m%d"))
            fromtime = stime
            endtime = etime

            if len(straid) == 0:
                ret = {
                    "result": -2,
                    "message": "策略ID不能为空"
                }
                return ret

            try:
                for value in (fromtime, endtime):
                    datetime.datetime.strptime(str(value), "%Y%m%d")
                    if not 19900101 <= value <= 20321231:
                        raise ValueError("回测日期支持1990至2032年")
            except ValueError as error:
                return {"result": -2, "message": str(error)}

            if fromtime > endtime:
                fromtime, endtime = endtime, fromtime

            fromtime = fromtime * 10000
            endtime = endtime * 10000 + 2359

            if self.__bt_mon__ is None:
                ret = {
                    "result": -1,
                    "message": "回测管理器未配置"
                }
            else:
                if not self.__bt_mon__.has_strategy(user, straid):
                    ret = {
                        "result": -2,
                        "message": "策略不存在"
                    }
                else:
                    try:
                        if dataset:
                            from .HistoryCatalog import dataset_entries
                            selected = next((row for row in dataset_entries(os.environ.get("WT_PLUS_WORKSPACE", "/workspace"))
                                             if row["id"] == dataset), None)
                            if selected is None or not group_allowed(userInfo, selected["groupid"]):
                                return {"result": -403, "message": "历史数据集不存在或无访问权限"}
                        btInfo = self.__bt_mon__.run_backtest(user, straid, fromtime, endtime, capital, slippage,
                                                            fill_model, event_delay, participation_rate,
                                                            risk=risk, dataset=dataset)
                    except ValueError as error:
                        return {"result": -2, "message": str(error)}
                    ret = {
                        "result": 0,
                        "message": "OK",
                        "backtest": btInfo
                    }

            return ret

    def init_mgr_apis(self, app: FastAPI):
        register_library_routes(app, self.__data_mgr__,
                                lambda request, token: check_auth(request, token, self.__sec_key__), self.deploy_dir,
                                bt_mon_provider=lambda: self.__bt_mon__)
        register_risk_library_routes(app, self.__data_mgr__,
                                     lambda request, token: check_auth(request, token, self.__sec_key__))
        register_history_catalog_routes(app, self.__data_mgr__, self._dog,
                lambda request, token: check_auth(request, token, self.__sec_key__), data_dir=Path.cwd(),
                bt_mon=lambda: self.__bt_mon__, plus_manager=lambda: self.__plus_mon__)
        register_managed_routes(app, self.__data_mgr__, self._dog,
                               lambda request, token: check_auth(request, token, self.__sec_key__), self.deploy_dir)
        register_strategy_routes(app, self.__data_mgr__, self._dog,
                                 lambda request, token: check_auth(request, token, self.__sec_key__))
        self.__risk_observer__ = register_risk_routes(app, self.__data_mgr__, self._dog,
                                 lambda request, token: check_auth(request, token, self.__sec_key__))
        register_channel_routes(app, self.__data_mgr__, self._dog,
                                lambda request, token: check_auth(request, token, self.__sec_key__))
        register_market_subscription_routes(app, self.__data_mgr__, self._dog,
                                lambda request, token: check_auth(request, token, self.__sec_key__))
        register_market_history_routes(app, self.__data_mgr__, self._dog,
                                lambda request, token: check_auth(request, token, self.__sec_key__))
        register_trade_risk_routes(app, self.__data_mgr__,
                                   lambda request, token: check_auth(request, token, self.__sec_key__),
                                   dog=self._dog)

        def control_app(appid, action, user, audit):
            try:
                with channel_transition_lock:
                    if action == "start":
                        try:
                            validate_managed_start(self.__data_mgr__, self._dog, appid)
                        except (ValueError, OSError) as exc:
                            return {"result": -3, "message": str(exc) if isinstance(exc, ValueError) else "已应用策略计划无法读取"}
                    getattr(self._dog, action)(appid)
            except Exception:
                self.__data_mgr__.log_action(user, audit, json.dumps({"target": appid, "result": "failed"}))
                raise


        '''下面是API接口的编写'''

        @app.post("/mgr/access", tags=["用户接口"])
        async def access_settings(request: Request):
            return {"result": 0, "guest_enabled": app.state.guest_enabled,
                    "public_read_only": request.headers.get("x-wt-public-read-only") == "1"}

        @app.post("/mgr/guest", tags=["用户接口"])
        async def guest_login(request: Request):
            if not app.state.guest_enabled:
                return {"result": -403, "message": "游客访问未启用"}
            now = datetime.datetime.now()
            request.session.clear()
            request.session["tokeninfo"] = {
                "loginid": "guest", "name": "游客", "role": "guest", "guest": True,
                "products": [], "auth_version": app.state.auth_version,
                "expiretime": (now + datetime.timedelta(hours=6)).strftime("%Y.%m.%d %H:%M:%S")}
            return {"result": 0, "userinfo": {"loginid": "guest", "name": "游客", "role": "guest", "guest": True, "products": []}}

        @app.post("/mgr/logout", tags=["用户接口"])
        async def logout(request: Request):
            info = request.session.get("tokeninfo", {})
            if app.state.guest_enabled and info.get("role") != "guest" and info.get("loginid") == app.state.monitor_owner:
                app.state.auth_version = secrets.token_urlsafe(24)
            request.session.clear()
            return {"result": 0, "message": "已退出登录"}

        @app.post("/mgr/login", tags=["用户接口"])
        async def cmd_login(
            request: Request,
            loginid: str = Body(..., title="用户名", embed=True),
            passwd: str = Body(..., title="用户密码", embed=True)
        ):
            peer = request.client.host if request.client else "unknown"
            if app.state.login_attempts.blocked(peer):
                from starlette.responses import JSONResponse
                return JSONResponse({"result": -429, "message": "登录尝试过多，请稍后再试"}, status_code=429, headers={"Retry-After": "600"})
            if True:
                user = loginid
                pwd = passwd

                if len(user) == 0 or len(pwd) == 0:
                    ret = {
                        "result": -1,
                        "message": "用户名和密码不能为空"
                    }
                else:
                    encpwd = hashlib.md5((user + pwd).encode("utf-8")).hexdigest()
                    now = datetime.datetime.now()
                    usrInf = self.__data_mgr__.get_user(user)
                    if usrInf is None or not secrets.compare_digest(encpwd, usrInf["passwd"]) or (app.state.guest_enabled and user != app.state.monitor_owner):
                        ret = {
                            "result": -1,
                            "message": "用户名或密码错误"
                        }
                    else:
                        usrInf.pop("passwd")
                        usrInf["loginip"] = request.client.host
                        usrInf["logintime"] = now.strftime("%Y/%m/%d %H:%M:%S")
                        products = usrInf["products"]

                        exptime = now + datetime.timedelta(minutes=360)  # 360分钟令牌超时
                        tokenInfo = {
                            "loginid": user,
                            "role": usrInf["role"],
                            "logintime": now.strftime("%Y/%m/%d %H:%M:%S"),
                            "expiretime": exptime.replace(tzinfo=pytz.timezone('UTC')).strftime("%Y.%m.%d %H:%M:%S"),
                            "products": products,
                            "loginip": request.client.host
                        }
                        request.session.clear()
                        tokenInfo["auth_version"] = app.state.auth_version
                        set_session(request, "tokeninfo", tokenInfo)

                        if self.__token_enabled__:
                            token = AES_Encrypt(self.__sec_key__, json.dumps(tokenInfo))
                            ret = {
                                "result": 0,
                                "message": "Ok",
                                "userinfo": usrInf,
                                "token": token
                            }
                        else:
                            ret = {
                                "result": 0,
                                "message": "Ok",
                                "userinfo": usrInf
                            }

                        self.__data_mgr__.log_action(usrInf, "login", json.dumps(request.headers.get('User-Agent')))
            else:
                ret = {
                    "result": -1,
                    "message": "请求处理出现异常",
                }
                if get_session(request, "userinfo") is not None:
                    pop_session("userinfo")

            app.state.login_attempts.record(peer, ret.get("result") == 0)
            return ret

        @app.post("/mgr/session", tags=["用户接口"])
        async def current_session(request: Request):
            success, info = check_auth(request, None, self.__sec_key__)
            if not success:
                return {"result": -9999, "message": "登录已过期"}
            if info.get("role") == "guest":
                return {"result": 0, "userinfo": {"loginid": "guest", "name": "游客", "role": "guest", "guest": True, "products": []}}
            user = self.__data_mgr__.get_user(info["loginid"])
            if user is None:
                return {"result": -9999, "message": "用户不存在"}
            return {"result": 0, "userinfo": {
                key: user.get(key, info.get(key, ""))
                for key in ("loginid", "name", "role", "products")
            }}

        # 修改密码
        # 修改密码
        @app.post("/mgr/modpwd", tags=["用户接口"])
        async def mod_pwd(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                oldpwd: str = Body(..., title="旧密码", embed=True),
                newpwd: str = Body(..., title="新密码", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if len(oldpwd) == 0 or len(newpwd) == 0:
                ret = {
                    "result": -1,
                    "message": "新旧密码都不能为空"
                }
            else:
                user = adminInfo["loginid"]
                oldencpwd = hashlib.md5((user + oldpwd).encode("utf-8")).hexdigest()
                usrInf = self.__data_mgr__.get_user(user)
                if usrInf is None:
                    ret = {
                        "result": -1,
                        "message": "用户不存在"
                    }
                else:
                    if oldencpwd != usrInf["passwd"]:
                        ret = {
                            "result": -1,
                            "message": "旧密码错误"
                        }
                    else:
                        if 'builtin' in usrInf and usrInf["builtin"]:
                            # 如果是内建账号要改密码，则先添加用户
                            usrInf["passwd"] = oldpwd
                            self.__data_mgr__.add_user(usrInf, user)
                            print("%s是内建账户，自动添加到数据库中" % user)

                        newencpwd = hashlib.md5((user + newpwd).encode("utf-8")).hexdigest()
                        self.__data_mgr__.mod_user_pwd(user, newencpwd, user)
                        app.state.auth_version = secrets.token_urlsafe(24)

                        ret = {
                            "result": 0,
                            "message": "Ok"
                        }

            return ret

        # 添加组合
        @app.post("/mgr/addgrp", tags=["策略组管理接口"])
        async def cmd_add_group(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                name: str = Body('', title="组合名称", embed=True),
                path: str = Body('', title="组合路径", embed=True),
                gtype: str = Body('', title="组合类型", embed=True),
                info: str = Body('', title="组合信息",embed=True),
                env: str = Body('', title="组合环境，实盘/回测", embed=True),
                datmod: str = Body('mannual', title="数据模式，mannal/auto", embed=True),
                mqurl: str = Body('', title="消息队列地址", embed=True),
                action: str = Body('add', title="操作类型，add/mod", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not group_allowed(adminInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            if adminInfo.get("role") not in ("admin", "superman"):
                return {"result": -403, "message": "仅管理员可修改组合"}
            allowed = adminInfo.get("products", "")
            allowed = [x.strip() for x in allowed.split(",") if x.strip()] if isinstance(allowed, str) else list(allowed or [])
            if allowed and groupid not in allowed:
                return {"result": -403, "message": "无权修改该组合"}

            previous = self.__data_mgr__.get_group(groupid)
            if previous and self._dog.isRunning(groupid) and any(
                    previous.get(key) != value for key,value in
                    (("path",path),("gtype",gtype),("env",env),("mqurl",mqurl))):
                return {"result": -3, "message": "请先停止组合再修改运行环境；名称可直接修改"}
            id = groupid

            if action == "":
                action = "add"

            if len(id) == 0 or len(name) == 0 or len(gtype) == 0:
                ret = {
                    "result": -1,
                    "message": "组合ID、名称、类型都不能为空"
                }
            elif not os.path.exists(path) or not os.path.isdir(path):
                ret = {
                    "result": -2,
                    "message": "组合运行目录不正确"
                }
            elif action == "add" and self.__data_mgr__.has_group(id):
                ret = {
                    "result": -3,
                    "message": "组合ID不能重复"
                }
            else:
                try:
                    grpInfo = {
                        "id": id,
                        "name": name,
                        "path": path,
                        "info": info,
                        "gtype": gtype,
                        "datmod": datmod,
                        "env": env,
                        "mqurl": mqurl
                    }

                    if self.__data_mgr__.add_group(grpInfo):
                        ret = {
                            "result": 0,
                            "message": "Ok"
                        }

                        if action == "add":
                            self.__data_mgr__.log_action(adminInfo, "addgrp", json.dumps(grpInfo))
                        else:
                            self.__data_mgr__.log_action(adminInfo, "modgrp", json.dumps(grpInfo))

                        self._dog.updateMQURL(id, mqurl)
                    else:
                        ret = {
                            "result": -2,
                            "message": "添加用户失败"
                        }
                except:
                    ret = {
                        "result": -1,
                        "message": "请求解析失败"
                    }

            return ret

        # 删除组合
        @app.post("/mgr/delgrp", tags=["策略组管理接口"])
        async def cmd_del_group(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not group_allowed(adminInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            if adminInfo.get("role") not in ("admin", "superman"):
                return {"result": -403, "message": "仅管理员可修改组合"}
            allowed = adminInfo.get("products", "")
            allowed = [x.strip() for x in allowed.split(",") if x.strip()] if isinstance(allowed, str) else list(allowed or [])
            if allowed and groupid not in allowed:
                return {"result": -403, "message": "无权修改该组合"}

            id = groupid

            if len(id) == 0:
                ret = {
                    "result": -1,
                    "message": "组合ID不能为空"
                }
            elif not self.__data_mgr__.has_group(id):
                ret = {
                    "result": -3,
                    "message": "该组合不存在"
                }
            elif self._dog.isRunning(id):
                ret = {
                    "result": -3,
                    "message": "请先停止该组合"
                }
            else:
                if True:
                    self._dog.delApp(id)
                    self.__data_mgr__.del_group(id)
                    ret = {
                        "result": 0,
                        "message": "Ok"
                    }

                    self.__data_mgr__.log_action(adminInfo, "delgrp", id)
                else:
                    ret = {
                        "result": -1,
                        "message": "请求解析失败"
                    }

            return ret

        # 组合停止
        @app.post("/mgr/stopgrp", tags=["策略组管理接口"])
        async def cmd_stop_group(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not group_allowed(adminInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            if not can_operate(adminInfo):
                return {"result": -403, "message": "当前角色不能停止进程"}
            allowed = adminInfo.get("products", "")
            allowed = [x.strip() for x in allowed.split(",") if x.strip()] if isinstance(allowed, str) else list(allowed or [])
            if allowed and groupid not in allowed:
                return {"result": -403, "message": "无权停止该进程"}

            grpid = groupid

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                if self._dog.isRunning(grpid):
                    await asyncio.to_thread(control_app, grpid, "stop", adminInfo, "stopgrp")
                ret = {
                    "result": 0,
                    "message": "Ok"
                }

                self.__data_mgr__.log_action(adminInfo, "stopgrp", json.dumps({"target": grpid, "result": "ok"}))

            return ret

        # 组合启动
        @app.post("/mgr/startgrp", tags=["策略组管理接口"])
        async def cmd_start_group(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not group_allowed(adminInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            if not can_operate(adminInfo):
                return {"result": -403, "message": "当前角色不能控制进程"}
            allowed = adminInfo.get("products", "")
            allowed = [x.strip() for x in allowed.split(",") if x.strip()] if isinstance(allowed, str) else list(allowed or [])
            if allowed and groupid not in allowed:
                return {"result": -403, "message": "无权控制该进程"}

            grpid = groupid

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                if not self._dog.isRunning(grpid):
                    failure = await asyncio.to_thread(control_app, grpid, "start", adminInfo, "startgrp")
                    if failure:
                        return failure
                ret = {
                    "result": 0,
                    "message": "Ok"
                }
                self.__data_mgr__.log_action(adminInfo, "startgrp", json.dumps({"target": grpid, "result": "ok"}))

            return ret

        # 获取执行的python进程的路径
        @app.post("/mgr/qryexec", tags=["通用接口"])
        def qry_exec_path(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            ret = {
                "result": 0,
                "message": "Ok",
                "path": sys.executable
            }
            return ret

        # 配置监控
        @app.post("/mgr/qrymon", tags=["调度器管理接口"])
        async def qry_mon_cfg(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not can_admin(adminInfo):
                return {"result": -403, "message": "需要管理员权限"}
            if not group_allowed(adminInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            grpid = groupid

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                monCfg = self._dog.getAppConf(grpid)
                if monCfg is None:
                    ret = {
                        "result": 0,
                        "message": "ok"
                    }
                else:
                    ret = {
                        "result": 0,
                        "message": "ok",
                        "config": monCfg
                    }

            return ret

        # 配置监控
        @app.post("/mgr/cfgmon", tags=["调度器管理接口"])
        async def cmd_config_monitor(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                config: dict = Body(..., title="监控配置", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not can_admin(adminInfo):
                return {"result": -403, "message": "需要管理员权限"}
            if not group_allowed(adminInfo, config.get("id", "")):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            if not config.get("id") or not config.get("path") or not config.get("folder"):
                return {"result": -2, "message": "请填写任务ID、执行程序和工作目录"}
            previous = self._dog.getAppConf(config["id"])
            if previous and self._dog.isRunning(config["id"]) and any(
                    config.get(key) != previous.get(key) for key in ("path", "folder", "param", "mqurl")):
                return {"result": -3, "message": "请先停止进程再修改启动命令"}
            isGrp = False
            if "group" in config:
                isGrp = config["group"]

            self._dog.applyAppConf(config, isGrp)
            ret = {
                "result": 0,
                "message": "ok"
            }
            self.__data_mgr__.log_action(adminInfo, "cfgmon", json.dumps(config))

            return ret

        # 查询目录结构
        @app.post("/mgr/qrydir", tags=["通用接口"])
        async def qry_directories(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not can_admin(usrInfo):
                return {"result": -403, "message": "需要管理员权限"}

            if True:
                if self.deploy_tree is None:
                    self.deploy_tree = get_path_tree(self.deploy_dir, "root")

                ret = {
                    "result": 0,
                    "message": "Ok",
                    "tree": self.deploy_tree
                }
            else:
                ret = {
                    "result": -1,
                    "message": "请求解析失败"
                }

            return ret

        # 查询目录结构
        @app.post("/mgr/qrygrpdir", tags=["策略组管理接口"])
        async def qry_grp_directories(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not can_admin(usrInfo):
                return {"result": -403, "message": "需要管理员权限"}
            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            grpid = groupid

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                monCfg = self.__data_mgr__.get_group(grpid)

                ret = {
                    "result": 0,
                    "message": "Ok",
                    "tree": get_cfg_tree(monCfg["path"], "root")
                }

            return ret

        # 查询组合列表
        @app.post("/mgr/qrygrp", tags=["策略组管理接口"])
        @app.get("/mgr/qrygrp", tags=["策略组管理接口"])
        async def qry_groups(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True)
        ):
            bSucc, tokenInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return tokenInfo

            products = tokenInfo["products"]
            
            try:
                groups = self.__data_mgr__.get_groups()
                rets = list()
                for grpInfo in groups:
                    if group_allowed(tokenInfo, grpInfo["id"]):
                        grpInfo["running"] = self._dog.isRunning(grpInfo["id"])
                        rets.append(grpInfo)

                ret = {
                    "result": 0,
                    "message": "Ok",
                    "groups": rets
                }
            except:
                ret = {
                    "result": -1,
                    "message": "请求解析失败"
                }

            return ret

        # 查询文件信息
        @app.post("/mgr/qrygrpfile", tags=["策略组管理接口"])
        async def qry_group_file(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                path: str = Body(..., title="文件路径", embed=True),
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not can_admin(usrInfo):
                return {"result": -403, "message": "需要管理员权限"}
            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            grpid = groupid

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                monCfg = self.__data_mgr__.get_group(grpid)
                root = monCfg["path"]
                root_path = Path(root).resolve()
                selected_path = Path(path)
                selected_path = selected_path if selected_path.is_absolute() else root_path / selected_path
                selected_path = selected_path.resolve()
                path = str(selected_path)
                if not selected_path.is_relative_to(root_path):
                    ret = {
                        "result": -1,
                        "message": "目标文件不在当前组合下"
                    }
                else:
                    f = open(path, 'rb')
                    content = f.read()
                    f.close()

                    encoding = chardet.detect(content)["encoding"]
                    content = content.decode(encoding or "utf-8")

                    ret = {
                        "result": 0,
                        "message": "Ok",
                        "content": content
                    }

            return ret

        # 提交组合文件
        @app.post("/mgr/cmtgrpfile", tags=["策略组管理接口"])
        async def cmd_commit_group_file(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                path: str = Body(..., title="文件路径", embed=True),
                content: str = Body(..., title="文件内容", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not can_admin(usrInfo):
                return {"result": -403, "message": "需要管理员权限"}
            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            grpid = groupid

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                monCfg = self.__data_mgr__.get_group(grpid)
                root = monCfg["path"]
                root_path = Path(root).resolve()
                selected_path = Path(path)
                selected_path = selected_path if selected_path.is_absolute() else root_path / selected_path
                selected_path = selected_path.resolve()
                path = str(selected_path)
                if not selected_path.is_relative_to(root_path):
                    ret = {
                        "result": -1,
                        "message": "目标文件不在当前组合下"
                    }
                else:
                    try:
                        f = open(path, 'rb')
                        old_content = f.read()
                        f.close()
                        encoding = chardet.detect(old_content)["encoding"]

                        backup_file(path)

                        f = open(path, 'wb')
                        f.write(content.encode(encoding or "utf-8"))
                        f.close()

                        ret = {
                            "result": 0,
                            "message": "Ok"
                        }
                    except:
                        ret = {
                            "result": -1,
                            "message": "文件保存失败"
                        }

            return ret

        # 查询策略列表
        @app.post("/mgr/qrystras", tags=["策略组管理接口"])
        async def qry_strategys(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            grpid = groupid

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "Ok",
                    "strategies": self.__data_mgr__.get_strategies(grpid)
                }

            return ret

        # 查询通道列表
        @app.post("/mgr/qrychnls", tags=["策略组管理接口"])
        async def qry_channels(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            grpid = groupid

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "Ok",
                    "channels": self.__data_mgr__.get_channels(grpid)
                }

            return ret

        # 查询组合日志
        @app.post("/mgr/qrylogs", tags=["策略组管理接口"])
        async def qry_logs(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                id: str = Body(..., title="组合ID", embed=True),
                type: str = Body(..., title="日志类型", embed=True),
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            grpid = id
            logtype = type

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                grpInfo = self.__data_mgr__.get_group(grpid)
                try:
                    logfolder = os.path.join(grpInfo["path"], "./Logs/")
                    file_list = os.listdir(logfolder)
                    targets = list()
                    for fname in file_list:
                        if fname[:6] == "Runner":
                            targets.append(fname)

                    targets.sort()
                    filename = os.path.join(logfolder, targets[-1])
                    content, lines = get_tail(filename, 100)
                    ret = {
                        "result": 0,
                        "message": "Ok",
                        "content": content,
                        "lines": lines
                    }
                except:
                    ret = {
                        "result": -1,
                        "message": "请求解析失败"
                    }

            return ret

        # 查询策略成交
        @app.post("/mgr/qrytrds", tags=["策略管理接口"])
        async def qry_trades(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                strategyid: str = Body(..., title="策略ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid
            sid = strategyid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "trades": self.__data_mgr__.get_trades(gid, sid)
                }

            return ret

        # 查询策略信号
        @app.post("/mgr/qrysigs", tags=["策略管理接口"])
        async def qry_signals(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                strategyid: str = Body(..., title="策略ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid
            sid = strategyid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "signals": self.__data_mgr__.get_signals(gid, sid)
                }

            return ret

        # 查询策略回合
        @app.post("/mgr/qryrnds", tags=["策略管理接口"])
        async def qry_rounds(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                strategyid: str = Body(..., title="策略ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid
            sid = strategyid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "rounds": self.__data_mgr__.get_rounds(gid, sid)
                }

            return ret

        # 查询策略持仓
        @app.post("/mgr/qrypos", tags=["策略管理接口"])
        async def qry_positions(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                strategyid: str = Body(..., title="策略ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid
            sid = strategyid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "positions": self.__data_mgr__.get_positions(gid, sid)
                }

            return ret

        # 查询策略绩效
        @app.post("/mgr/qryfunds", tags=["策略管理接口"])
        async def qry_funds(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                strategyid: str = Body(..., title="策略ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid
            sid = strategyid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "funds": self.__data_mgr__.get_funds(gid, sid)
                }

            return ret

        # 查询通道订单
        @app.post("/mgr/qrychnlords", tags=["交易通道管理接口"])
        async def qry_channel_orders(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                channelid: str = Body(..., title="通道ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid
            cid = channelid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "orders": self.__data_mgr__.get_channel_orders(gid, cid)
                }

            return ret

        # 查询通道成交
        @app.post("/mgr/qrychnltrds", tags=["交易通道管理接口"])
        async def qry_channel_trades(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                channelid: str = Body(..., title="通道ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid
            cid = channelid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "trades": self.__data_mgr__.get_channel_trades(gid, cid)
                }

            return ret

        # 查询通道持仓
        @app.post("/mgr/qrychnlpos", tags=["交易通道管理接口"])
        async def qry_channel_position(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                channelid: str = Body(..., title="通道ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid
            cid = channelid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "positions": self.__data_mgr__.get_channel_positions(gid, cid)
                }

            return ret

        # 查询通道资金
        @app.post("/mgr/qrychnlfund", tags=["交易通道管理接口"])
        async def qry_channel_funds(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                channelid: str = Body(..., title="通道ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid
            cid = channelid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "funds": self.__data_mgr__.get_channel_funds(gid, cid)
                }

            return ret

        # 查询用户列表
        @app.post("/mgr/qryusers", tags=["系统管理接口"])
        @app.get("/mgr/qryusers", tags=["系统管理接口"])
        async def qry_users(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if usrInfo.get("role") not in ("admin", "superman"):
                return {"result": -403, "message": "仅管理员可管理用户和操作记录"}

            users = self.__data_mgr__.get_users()
            for usrInfo in users:
                usrInfo.pop("passwd")

            ret = {
                "result": 0,
                "message": "",
                "users": users
            }

            return ret

        # 提交用户信息
        @app.post("/mgr/cmtuser", tags=["系统管理接口"])
        async def cmd_commit_user(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                loginid: str = Body(..., title="登录名", embed=True),
                name: str = Body(..., title="用户姓名", embed=True),
                passwd: str = Body("", title="登录密码", embed=True),
                role: str = Body("", title="用户角色", embed=True),
                iplist: str = Body("", title="限定ip", embed=True),
                products: list = Body([], title="产品列表", embed=True),
                remark: str = Body("", title="备注信息", embed=True)
        ):
            if app.state.guest_enabled:
                return {"result": -403, "message": "该工作台仅保留所有者账户，不提供账户创建或删除"}
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if role not in READ_ROLES:
                return {"result": -2, "message": "用户角色无效"}

            if adminInfo.get("role") not in ("admin", "superman"):
                return {"result": -403, "message": "仅管理员可管理用户和操作记录"}

            userinfo = {
                "loginid": loginid,
                "name": name,
                "passwd": passwd,
                "role": role,
                "iplist": iplist,
                "products": ",".join(products),
                "remark": remark
            }

            self.__data_mgr__.add_user(userinfo, adminInfo["loginid"])
            ret = {
                "result": 0,
                "message": "Ok"
            }

            self.__data_mgr__.log_action(adminInfo, "cmtuser", json.dumps({k:v for k,v in userinfo.items() if k != "passwd"}))

            return ret

        # 删除用户
        @app.post("/mgr/deluser", tags=["系统管理接口"])
        async def cmd_delete_user(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                loginid: str = Body(..., title="用户名", embed=True)
        ):
            if app.state.guest_enabled:
                return {"result": -403, "message": "该工作台仅保留所有者账户，不提供账户创建或删除"}
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if adminInfo.get("role") not in ("admin", "superman"):
                return {"result": -403, "message": "仅管理员可管理用户和操作记录"}

            if self.__data_mgr__.del_user(loginid, adminInfo["loginid"]):
                self.__data_mgr__.log_action(adminInfo, "delusr", loginid)
            ret = {
                "result": 0,
                "message": "Ok"
            }

            return ret

        # 修改密码
        @app.post("/mgr/resetpwd", tags=["系统管理接口"])
        async def reset_pwd(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                loginid: str = Body(..., title="用户名", embed=True),
                passwd: str = Body(..., title="新密码", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if adminInfo.get("role") not in ("admin", "superman") and loginid != adminInfo.get("loginid"):
                return {"result": -403, "message": "只能修改自己的密码"}

            user = loginid
            pwd = passwd

            if len(pwd) == 0 or len(user) == 0:
                ret = {
                    "result": -1,
                    "message": "密码都不能为空"
                }
            else:
                encpwd = hashlib.md5((user + pwd).encode("utf-8")).hexdigest()
                usrInf = self.__data_mgr__.get_user(user)
                if usrInf is None:
                    ret = {
                        "result": -1,
                        "message": "用户不存在"
                    }
                else:
                    self.__data_mgr__.mod_user_pwd(user, encpwd, adminInfo["loginid"])
                    app.state.auth_version = secrets.token_urlsafe(24)
                    self.__data_mgr__.log_action(adminInfo, "resetpwd", loginid)
                    ret = {
                        "result": 0,
                        "message": "Ok"
                    }

            return ret

        # 查询操作记录
        @app.post("/mgr/qryacts", tags=["系统管理接口"])
        async def qry_actions(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                sdate: str = Body(..., title="开始日期", embed=True),
                edate: str = Body(..., title="结束日期", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if adminInfo.get("role") not in ("admin", "superman"):
                return {"result": -403, "message": "仅管理员可管理用户和操作记录"}

            from datetime import datetime
            try:
                start = datetime.strptime(sdate, "%Y-%m-%d %H:%M:%S")
                end = datetime.strptime(edate, "%Y-%m-%d %H:%M:%S")
                if start > end:
                    raise ValueError()
            except ValueError:
                return {"result": -2, "message": "请选择有效的起止日期"}
            ret = {
                "result": 0, "message": "",
                "actions": self.__data_mgr__.get_actions(sdate, edate)
            }

            return ret

        # 查询全部调度
        @app.post("/mgr/qrymons", tags=["调度器管理接口"])
        @app.get("/mgr/qrymons", tags=["调度器管理接口"])
        async def qry_mon_apps(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            schedules = {key: value for key,value in self._dog.get_apps().items() if group_allowed(adminInfo, key)}
            for appid in schedules:
                schedules[appid]["group"] = self.__data_mgr__.has_group(appid)

            ret = {
                "result": 0,
                "message": "",
                "schedules": schedules
            }

            return ret

        @app.post("/mgr/startapp", tags=["调度器管理接口"])
        async def cmd_start_app(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                appid: str = Body(..., title="AppID", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not group_allowed(adminInfo, appid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            if not can_operate(adminInfo):
                return {"result": -403, "message": "当前角色不能控制进程"}
            allowed = adminInfo.get("products", "")
            allowed = [x.strip() for x in allowed.split(",") if x.strip()] if isinstance(allowed, str) else list(allowed or [])
            if allowed and appid not in allowed:
                return {"result": -403, "message": "无权控制该进程"}

            if not self._dog.has_app(appid):
                ret = {
                    "result": -1,
                    "message": "App不存在"
                }
            else:
                if not self._dog.isRunning(appid):
                    failure = await asyncio.to_thread(control_app, appid, "start", adminInfo, "startapp")
                    if failure:
                        return failure
                ret = {
                    "result": 0,
                    "message": "Ok"
                }
                self.__data_mgr__.log_action(adminInfo, "startapp", json.dumps({"target": appid, "result": "ok"}))

            return ret

        # 组合停止
        @app.post("/mgr/stopapp", tags=["调度器管理接口"])
        async def cmd_stop_app(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                appid: str = Body(..., title="AppID", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not group_allowed(adminInfo, appid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            if not can_operate(adminInfo):
                return {"result": -403, "message": "当前角色不能停止进程"}
            allowed = adminInfo.get("products", "")
            allowed = [x.strip() for x in allowed.split(",") if x.strip()] if isinstance(allowed, str) else list(allowed or [])
            if allowed and appid not in allowed:
                return {"result": -403, "message": "无权停止该进程"}

            if not self._dog.has_app(appid):
                ret = {
                    "result": -1,
                    "message": "App不存在"
                }
            else:
                if self._dog.isRunning(appid):
                    await asyncio.to_thread(control_app, appid, "stop", adminInfo, "stopapp")
                ret = {
                    "result": 0,
                    "message": "Ok"
                }

                self.__data_mgr__.log_action(adminInfo, "stopapp", json.dumps({"target": appid, "result": "ok"}))

            return ret

        # 查询调度日志
        @app.post("/mgr/qrymonlog", tags=["调度器管理接口"])
        @app.get("/mgr/qrymonlog", tags=["调度器管理接口"])
        async def qry_mon_logs(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not can_admin(adminInfo):
                return {"result": -403, "message": "需要管理员权限"}

            filename = os.getcwd() + "/logs/WtMonSvr.log"
            content, lines = get_tail(filename, 100, "UTF-8")
            ret = {
                "result": 0,
                "message": "Ok",
                "content": content,
                "lines": lines
            }

            return ret

        # 删除调度任务
        @app.post("/mgr/delapp", tags=["调度器管理接口"])
        async def cmd_del_app(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                appid: str = Body(..., title="AppID", embed=True)
        ):
            bSucc, adminInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return adminInfo

            if not can_admin(adminInfo):
                return {"result": -403, "message": "需要管理员权限"}
            if not group_allowed(adminInfo, appid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            id = appid

            if len(id) == 0:
                ret = {
                    "result": -1,
                    "message": "组合ID不能为空"
                }
            elif self.__data_mgr__.has_group(id):
                ret = {
                    "result": -2,
                    "message": "该调度任务是策略组合，请从组合管理删除"
                }
            elif not self._dog.has_app(id):
                ret = {
                    "result": -3,
                    "message": "该调度任务不存在"
                }
            elif self._dog.isRunning(id):
                ret = {
                    "result": -4,
                    "message": "请先停止该任务"
                }
            else:
                if True:
                    self._dog.delApp(id)
                    ret = {
                        "result": 0,
                        "message": "Ok"
                    }

                    self.__data_mgr__.log_action(adminInfo, "delapp", id)
                else:
                    ret = {
                        "result": -1,
                        "message": "请求解析失败"
                    }

            return ret

        # 查询组合持仓
        @app.post("/mgr/qryportpos", tags=["组合盘管理接口"])
        async def qry_group_positions(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "positions": self.__data_mgr__.get_group_positions(gid)
                }

            return ret

        # 查询组合成交
        @app.post("/mgr/qryporttrd", tags=["组合盘管理接口"])
        async def qry_group_trades(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "trades": self.__data_mgr__.get_group_trades(gid)
                }

            return ret

        # 查询组合回合
        @app.post("/mgr/qryportrnd", tags=["组合盘管理接口"])
        async def qry_group_rounds(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "rounds": self.__data_mgr__.get_group_rounds(gid)
                }

            return ret

        # 查询组合资金
        @app.post("/mgr/qryportfunds", tags=["组合盘管理接口"])
        async def qry_group_funds(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "funds": self.__data_mgr__.get_group_funds(gid)
                }

            return ret

        # 查询组合绩效分析
        @app.post("/mgr/qryportperfs", tags=["组合盘管理接口"])
        async def qry_group_perfs(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "performance": self.__data_mgr__.get_group_performances(gid)
                }

            return ret

        # 查询组合过滤器
        @app.post("/mgr/qryportfilters", tags=["组合盘管理接口"])
        async def qry_group_filters(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            gid = groupid

            if not self.__data_mgr__.has_group(gid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                ret = {
                    "result": 0,
                    "message": "",
                    "filters": self.__data_mgr__.get_group_filters(gid)
                }

            return ret

        # 提交组合过滤器
        @app.post("/mgr/cmtgrpfilters", tags=["组合盘管理接口"])
        async def cmd_commit_group_filters(
                request: Request,
                token: str = Body(None, title="访问令牌", embed=True),
                groupid: str = Body(..., title="组合ID", embed=True),
                filters: dict = Body(..., title="过滤器", embed=True)
        ):
            bSucc, usrInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return usrInfo

            if not can_operate(usrInfo):
                return {"result": -403, "message": "当前角色不能控制过滤器"}
            if not group_allowed(usrInfo, groupid):
                return {"result": -403, "message": "没有该组合或进程的访问权限"}

            grpid = groupid

            if not self.__data_mgr__.has_group(grpid):
                ret = {
                    "result": -1,
                    "message": "组合不存在"
                }
            else:
                try:
                    self.__data_mgr__.set_group_filters(grpid, filters)
                    self.__data_mgr__.log_action(usrInfo, "cmtgrpfilters", json.dumps({"target": grpid, "result": "ok"}))
                    ret = {
                        "result": 0,
                        "message": "Ok"
                    }
                except:
                    ret = {
                        "result": -1,
                        "message": "过滤器保存失败"
                    }

            return ret
        
        @app.get("/mgr/auth", tags=["令牌认证"])
        @app.post("/mgr/auth")
        async def authority(
            request: Request,
            token: str = Body(None, title="访问令牌", embed=True)
        ):
            bSucc, userInfo = check_auth(request, token, self.__sec_key__)
            if not bSucc:
                return userInfo
            else:
                return {
                    "result": 0,
                    "message": "Ok",
                    "userinfo": userInfo
                }

    def init_comm_apis(self, app: FastAPI):
        @app.get("/")
        async def root_entry():
            return RedirectResponse("/console/")

        @app.get("/console")
        async def console_entry():
            return RedirectResponse("/console/index.html")
        
        @app.get("/mobile")
        async def mobile_entry():
            return RedirectResponse("/mobile/index.html")

        @app.get("/favicon.ico")
        async def favicon_entry():
            return FileResponse(os.path.join(self.static_folder, "favicon.ico"))
        
        @app.get("/hasbt")
        @app.post("/hasbt")
        async def check_btmon():
            if self.__bt_mon__ is None:
                return {
                    "result": -1,
                    "message": "不支持在线回测"
                }
            else:
                return {
                    "result": 0,
                    "message": "Ok"
                }

    def __run_impl__(self, port: int, host: str):
        self._dog.run()
        self.push_svr.run()
        uvicorn.run(self.app, port=port, host=host)

    def run(self, port: int = 8080, host="0.0.0.0", bSync: bool = True):
        # Each process owner waits for its children. Ignoring SIGCHLD discards exit codes.
        if bSync:
            self.__run_impl__(port, host)
        else:
            import threading
            self.worker = threading.Thread(target=self.__run_impl__, args=(port, host,))
            self.worker.daemon = True
            self.worker.start()

    def init_logging(self):
        pass

    def on_start(self, grpid: str):
        if self.__data_mgr__.has_group(grpid):
            self.push_svr.notifyGrpEvt(grpid, 'start')

    def on_stop(self, grpid: str, isErr: bool):
        if self.__data_mgr__.has_group(grpid):
            self.push_svr.notifyGrpEvt(grpid, 'stop')

        # 如果是错误，要通知管理员
        if isErr and self._sink_:
            grpInfo = self.__data_mgr__.get_group(grpid)
            self._sink_.notify("fatal", "检测到 %s[%s] 意外停止, 请及时处理!!!" % (grpInfo["name"], grpid))

    def on_output(self, grpid: str, tag: str, time: int, message: str):
        if self.__data_mgr__.has_group(grpid):
            self.push_svr.notifyGrpLog(grpid, tag, time, message)

    def on_order(self, grpid: str, chnl: str, ordInfo: dict):
        self.push_svr.notifyGrpChnlEvt(grpid, chnl, 'order', ordInfo)

    def on_trade(self, grpid: str, chnl: str, trdInfo: dict):
        self.push_svr.notifyGrpChnlEvt(grpid, chnl, 'trade', trdInfo)

    def on_notify(self, grpid: str, chnl: str, message: str):
        self.push_svr.notifyGrpChnlEvt(grpid, chnl, 'notify', message)

    def on_timeout(self, grpid: str):
        if not self.notifyTimeout:
            return
            
        if self._sink_:
            grpInfo = self.__data_mgr__.get_group(grpid)
            self._sink_.notify("fatal", f'检测到 {grpInfo["name"]}[{grpid}]的MQ消息超时，请及时检查并处理!!!')
