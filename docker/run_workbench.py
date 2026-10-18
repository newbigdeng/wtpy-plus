"""Start an empty, persistent workbench without connecting a trading account."""
from pathlib import Path
import fcntl
import hashlib
import json
import os
import secrets
import signal

os.umask(0o077)
data=Path(os.environ.get("WT_DATA_DIR","/data")).resolve()
data.mkdir(parents=True,exist_ok=True)
os.chdir(data)
lock=(data/".workbench.lock").open("a")
fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)


def create_text(path,value):
    with path.open("x",encoding="utf-8") as out:
        out.write(value+"\n")


secret=data/".session-secret"
if not secret.exists():create_text(secret,secrets.token_urlsafe(48))
os.environ["WT_SESSION_SECRET"]=secret.read_text().strip()
if len(os.environ["WT_SESSION_SECRET"])<32:raise ValueError("/data/.session-secret 至少需要32个字符")

from wtpy.monitor import WtMonSvr,WtBtMon
from wtpy.monitor.DataMgr import DataMgr

manager=DataMgr(str(data/"data.db"))
if not manager.get_users():
    username=os.environ.get("WT_ADMIN_USER","admin")
    if not username or len(username)>64 or any(char.isspace() for char in username):
        raise ValueError("WT_ADMIN_USER 应为不含空白的账号名称")
    password_path=Path(os.environ.get("WT_ADMIN_PASSWORD_FILE",str(data/"admin-password.txt")))
    password=os.environ.get("WT_ADMIN_PASSWORD")
    if password is None:
        if not password_path.exists():create_text(password_path,secrets.token_urlsafe(18))
        password=password_path.read_text().rstrip("\r\n")
    elif not password_path.exists():
        create_text(password_path,password)
    if not password:raise ValueError("首次启动的管理员密码不能为空")
    # The browser sends MD5(password); DataMgr stores MD5(loginid + browser value).
    manager.add_user(dict(loginid=username,name="管理员",passwd=hashlib.md5(password.encode()).hexdigest(),
                         role="admin",iplist="",products="",remark="首次启动创建"),"bootstrap")
    (data/"admin-user.txt").write_text(username+"\n")
    print("已创建管理员，账号文件:",data/"admin-user.txt","密码文件:",password_path,flush=True)
manager.get_db().close()

config_path=data/"workbench.json"
config=json.loads(config_path.read_text()) if config_path.exists() else {}
if not isinstance(config,dict):raise ValueError("workbench.json 应为配置对象")
basefiles=Path(config.get("basefiles",str(data/"basefiles"))).resolve()
storage=Path(config.get("storage",str(data/"storage"))).resolve()
mode=config.get("storage_mode","bin")
if mode not in ("bin","csv"):raise ValueError("storage_mode 仅支持 bin 或 csv")

research=data/"research"
template=research/"template"
template.mkdir(parents=True,exist_ok=True)
replay=dict(basefiles={key:str(basefiles/name) for key,name in (
    ("commodity","commodities.json"),("contract","contracts.json"),("session","sessions.json"),
    ("holiday","holidays.json"),("hot","hots.json"))},mode=mode,
    store=dict(path=str(storage)+"/"),stime=0,etime=0,tick=True,fees=str(basefiles/"fees.json"))
if "cta_fill_model" in config:replay["cta_fill_model"]=config["cta_fill_model"]
(template/"configbt.json").write_text(json.dumps(dict(replayer=replay,env=dict(mocker="cta"),
    notifier=dict(active=True,url="$MQURL$")),ensure_ascii=False,indent=2))
logging_config=dict(root=dict(level="info",sinks=[dict(type="console_sink",pattern="[%Y-%m-%d %H:%M:%S] %v")]))
(template/"logcfgbt.json").write_text(json.dumps(logging_config))
(template/"fees.json").write_bytes((basefiles/"fees.json").read_bytes() if (basefiles/"fees.json").is_file() else b"{}\n")
manifest=data/"plus-datasets.json"
if not manifest.exists():manifest.write_text("[]\n")

server=WtMonSvr(static_folder="/opt/wt/static",deploy_dir=str(data),notifyTimeout=False,stop_apps_on_shutdown=True)
servo=None
if config.get("basefiles") and config.get("storage"):
    if not basefiles.is_dir() or not storage.is_dir():raise ValueError("请挂载 workbench.json 指定的 basefiles 和 storage 目录")
    from wtpy import WtDtServo
    logcfg=data/"logcfgdt.json"
    logcfg.write_text(json.dumps(logging_config))
    servo=WtDtServo(str(logcfg))
    servo.setBasefiles(str(basefiles))
    servo.setStorage(str(storage)+"/")
    server.set_dt_servo(servo)
else:
    print("历史行情尚未配置。挂载数据后，在 /data/workbench.json 设置 basefiles 和 storage。",flush=True)
server.set_bt_mon(WtBtMon(str(research),dtServo=servo,logger=server.logger))
from wtpy.monitor.WorkbenchApi import install_workbench_api
from wtpy.monitor.WtMonSvr import check_auth
install_workbench_api(server,check_auth)
def exit_container(_signal, _frame):
    raise SystemExit(0)

# Uvicorn re-delivers SIGTERM after completing the lifespan cleanup.
signal.signal(signal.SIGTERM, exit_container)
server.run(port=8100,host="0.0.0.0",bSync=True)
