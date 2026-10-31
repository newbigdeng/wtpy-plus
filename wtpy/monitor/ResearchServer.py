"""Run research and document reads beside the existing monitor, without a WatchDog."""
import asyncio
from contextlib import asynccontextmanager, closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request as UrlRequest, urlopen

from fastapi import Body, FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from starlette.responses import JSONResponse
from yaml import YAMLError

from .ContractRollover import planning_hint
from .AccountAnalytics import account_analytics, record_account_receipts
from .BacktestPerformance import merge_latest_performance
from .PlusBacktest import PlusBacktestManager
from .ResearchWorkspaceRoutes import register_research_routes
from .Roles import can_read, can_research_read, group_allowed
from .TradeRiskApi import trade_risk_snapshot
from .WtBtMon import WtBtMon


def upstream_headers(request):
    return {key:request.headers[key] for key in ('cookie','host','origin','x-wt-public-read-only')
            if key in request.headers}


def monitor_json(upstream, path, payload, headers):
    request=UrlRequest(upstream+path,data=json.dumps(payload).encode(),
                       headers={'Content-Type':'application/json',**headers})
    try:
        with urlopen(request,timeout=20) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            return json.load(error)
        except ValueError:
            return {'result':-999,'message':'原监控服务未返回有效会话'}
    except (URLError,TimeoutError):
        return {'result':-999,'message':'原监控服务暂时不可用，请稍后重试'}


def monitor_auth(upstream, request):
    """Delegate session validation to its original owner; never mint a second session."""
    origin=request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.headers.get('host'):
        return False,{'result':-403,'message':'拒绝跨站操作'}
    value=monitor_json(upstream,'/mgr/session',{},upstream_headers(request))
    if value.get('result') != 0:
        return False,value
    return True,value['userinfo']


def create_research_app(*, root, runs, template, plus_root, upstream, workspace='/workspace', owner='localadmin',
                        records_db=None, account_history=None):
    root,runs,template=Path(root),Path(runs),Path(template)
    root.mkdir(parents=True,exist_ok=True)
    runs.mkdir(parents=True,exist_ok=True)
    if not (runs/'template').exists():
        shutil.copytree(template,runs/'template')
    bt_mon=WtBtMon(str(runs))
    plus=PlusBacktestManager(plus_root,workspace=workspace,recover_running=False,
                             cache_root=root/'plus-detail-cache')
    from .DataMgr import DataMgr
    from .WorkbenchApi import WorkbenchFeed, channel_positions, position_owners, position_market_fields, trades_with_dates
    feed=WorkbenchFeed(account_history or root/'account-history')
    record_lock=threading.RLock()
    readers={}
    research=None
    account_capture={'last_at':None,'error':None}
    stop_capture=asyncio.Event()

    async def capture_accounts():
        while not stop_capture.is_set():
            try:
                failed=await asyncio.to_thread(capture_recorded_accounts)
                account_capture['error']='部分账户后台采样失败' if failed else None
                if not failed:account_capture['last_at']=int(time.time()*1000)
            except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,YAMLError):
                account_capture['error']='账户后台采样失败'
            try:await asyncio.wait_for(stop_capture.wait(),timeout=10)
            except TimeoutError:pass

    @asynccontextmanager
    async def lifespan(_app):
        stop_capture.clear()
        try:
            await asyncio.to_thread(feed.start)
        except (OSError,ValueError,TimeoutError):
            import logging
            logging.getLogger(__name__).exception('Market reader warmup failed')
        capture=asyncio.create_task(capture_accounts()) if records_db else None
        try:
            yield
        finally:
            if capture:
                stop_capture.set()
                await capture
            await asyncio.to_thread(research.shutdown)
            await asyncio.to_thread(bt_mon.shutdown)
            await asyncio.to_thread(feed.close)

    app=FastAPI(title='WT research',docs_url=None,redoc_url=None,openapi_url=None,lifespan=lifespan)
    app.add_middleware(GZipMiddleware,minimum_size=1000)

    def authenticate(request, _token=None):
        return monitor_auth(upstream,request)

    research=register_research_routes(app,bt_mon,authenticate,workspace=workspace,root=root)
    app.state.research,app.state.bt_mon=research,bt_mon
    from .TerminalRoutes import register_terminal_routes
    register_terminal_routes(app, authenticate, owner)
    from .ResearchCodeVersions import register_code_version_routes
    register_code_version_routes(app, root, authenticate,
        lambda request, route, body: monitor_json(upstream, route, body, upstream_headers(request)))

    @app.get('/healthz')
    def health():
        return {'status':'ready','service':'research','watchdog':False,
                'account_capture':account_capture.copy()}

    @app.post('/mgr/research/public_status')
    async def public_research_status(request:Request):
        ok,user=await asyncio.to_thread(authenticate,request)
        if not ok:return user
        from .PublicResearch import snapshot
        value=await asyncio.to_thread(snapshot,root,owner)
        return JSONResponse(value,headers={'Cache-Control':'no-store'})

    @app.post('/mgr/account_equity')
    async def account_equity(request:Request,groupid:str=Body(...,embed=True),
                             start_date:int=Body(None,embed=True),end_date:int=Body(None,embed=True),
                             segment_id:str=Body('current',embed=True)):
        ok,user=await asyncio.to_thread(authenticate,request)
        if not ok:return user
        # Match the existing workbench's financial-account visibility.
        if user.get('role') not in ('admin','superman','guest') or not group_allowed(user,groupid):
            return {'result':-403,'message':'没有该组合的查看权限'}
        if not records_db:return {'result':-2,'message':'未配置账户历史读取'}
        try:
            accounts=await asyncio.to_thread(read_accounts,groupid)
            value=await asyncio.to_thread(account_analytics,accounts,start_date=start_date,end_date=end_date,segment_id=segment_id)
            if user.get('role')=='guest':
                from .GuestAccess import guest_data
                value=guest_data(value)
            return JSONResponse({'result':0,**value},headers={'Cache-Control':'no-store'})
        except (OSError,ValueError,sqlite3.Error,YAMLError) as error:
            return {'result':-2,'message':'游客页面暂时无法读取这项数据' if user.get('role')=='guest' else '账户记录读取失败：'+str(error)}

    @app.get('/bt/qrystras')
    @app.post('/bt/qrystras')
    async def strategies(request:Request):
        ok,user=await asyncio.to_thread(authenticate,request)
        if not ok:return user
        if not can_research_read(user):return {'result':-403,'message':'需要策略研究权限'}
        payload=await request.json() if await request.body() else {}
        value=await asyncio.to_thread(monitor_json,upstream,'/bt/qrystras',payload,upstream_headers(request))
        if value.get('result')==0:
            try:
                value['strategies']=await asyncio.to_thread(merge_latest_performance,value.get('strategies') or [],
                    owner if user.get('role')=='guest' else user['loginid'],research_root=Path(plus_root).parent,plus_manager=plus)
            except (OSError,ValueError):
                return {'result':-2,'message':'回测绩效暂时无法读取，请稍后刷新'}
        if user.get('role')=='guest':
            from .GuestAccess import guest_data
            value=guest_data(value)
        return JSONResponse(value,headers={'Cache-Control':'no-store'})

    @app.post('/bt/plus/list')
    def tasks(request:Request,straid:str=Body(None,embed=True)):
        ok,user=authenticate(request)
        if not ok:return user
        if not can_research_read(user):return {'result':-403,'message':'需要策略研究权限'}
        value={'result':0,'tasks':plus.list_tasks(owner if user.get('role')=='guest' else user['loginid'],straid)}
        if user.get('role')=='guest':
            from .GuestAccess import guest_data
            value=guest_data(value)
        return value

    @app.post('/bt/plus/detail')
    def detail(request:Request,taskid:str=Body(...,embed=True),
               ledger:str=Body(None,embed=True),offset:int=Body(0,ge=0,embed=True),
               limit:int=Body(100,ge=1,le=500,embed=True),include_log:bool=Body(False,embed=True)):
        ok,user=authenticate(request)
        if not ok:return user
        if not can_research_read(user):return {'result':-403,'message':'需要策略研究权限'}
        try:
            value=plus.detail(owner if user.get('role')=='guest' else user['loginid'],taskid,
                              ledger=ledger,offset=offset,limit=limit,include_log=include_log)
            if value is None:return {'result':-2,'message':'任务不存在或无访问权限'}
            if user.get('role')=='guest':
                from .GuestAccess import guest_data
                value=guest_data(value)
            return {'result':0,**value}
        except (OSError,ValueError,RuntimeError) as error:
            return {'result':-2,'message':str(error)}

    @app.post('/mgr/workbench')
    async def workbench(request:Request):
        ok,user=await asyncio.to_thread(authenticate,request)
        if not ok:return user
        payload=await request.json()
        if records_db and (payload.get('quote_only') is True or payload.get('market_only') is True):
            groupid=payload.get('groupid')
            if not isinstance(groupid,str) or not groupid:return {'result':-2,'message':'请选择组合'}
            if not can_read(user) or not group_allowed(user,groupid):
                return {'result':-403,'message':'没有该组合的查看权限'}
            try:
                group=await asyncio.to_thread(lambda:recorded_groups(groupid)[0])
                market=await asyncio.to_thread(feed.market,group,payload.get('code',''),
                    payload.get('period','m1'),payload.get('count',600),False,
                    payload.get('start_date'),payload.get('end_date'),quote_only=payload.get('quote_only') is True)
                return JSONResponse({'result':0,'market':market},headers={'Cache-Control':'no-store'})
            except (OSError,ValueError,TypeError,sqlite3.Error):
                return {'result':-2,'message':'行情读取暂时不可用，请稍后重试'}
        include_catalog=payload.get('include_catalog',True)
        value=await asyncio.to_thread(monitor_json,upstream,'/mgr/workbench',
                                     {**payload,'include_catalog':True},upstream_headers(request))
        if value.get('result')==0:
            if records_db:
                try:
                    await asyncio.to_thread(enrich_records,payload['groupid'],value,payload)
                except (OSError,ValueError,sqlite3.Error):
                    return JSONResponse({'result':-1,'message':'通道账簿暂时不可读，请稍后刷新。'},status_code=503)
            market=value.get('market') or {}
            selected=market.get('contract')
            if selected:
                market['rollover']=planning_hint(selected,market.get('contracts') or [],market.get('rollover') or {})
            if not include_catalog:market['contracts']=None
        if user.get('role')=='guest':
            from .GuestAccess import guest_data
            value=guest_data(value)
        return JSONResponse(value,headers={'Cache-Control':'no-store'})

    def recorded_groups(groupid=None):
        with closing(sqlite3.connect(Path(records_db).resolve().as_uri()+'?mode=ro',uri=True,timeout=.5)) as database:
            rows=(database.execute('SELECT groupid,path FROM groups WHERE groupid=?',(groupid,))
                  if groupid is not None else database.execute('SELECT groupid,path FROM groups')).fetchall()
        if groupid is not None and not rows:
            raise ValueError('Unknown recorded group')
        return [{'id':row[0],'path':row[1]} for row in rows]

    def record_reader(group):
        key=(group['id'],group['path'])
        manager=readers.get(key)
        if manager is None:
            manager=DataMgr.from_groups([group]);readers[key]=manager
        return manager

    def read_accounts(groupid,group=None):
        group=group or recorded_groups(groupid)[0]
        with record_lock:
            manager=record_reader(group)
            channels=manager.get_channels(groupid) or []
            accounts=trade_risk_snapshot(group,channels)['accounts']
            return record_account_receipts(groupid,accounts,group['path'],feed.history_root)

    def capture_recorded_accounts():
        import logging
        failed=False
        for group in recorded_groups():
            try:read_accounts(group['id'],group)
            except (OSError,ValueError,TypeError,KeyError,sqlite3.Error,YAMLError):
                logging.getLogger(__name__).exception('Account sampling failed for group %s',group['id'])
                failed=True
        return failed

    @app.post('/mgr/qryfunds')
    async def strategy_performance(request:Request, groupid:str=Body(...,embed=True),
                                   strategyid:str=Body(...,embed=True),token:str=Body(None,embed=True)):
        payload={'groupid':groupid,'strategyid':strategyid}
        if token is not None:payload['token']=token
        value=await asyncio.to_thread(monitor_json,upstream,'/mgr/qryfunds',payload,upstream_headers(request))
        if value.get('result') != 0 or not records_db:
            return value
        try:
            group=await asyncio.to_thread(recorded_groups,groupid)
            def read():
                with record_lock:
                    return record_reader(group[0]).get_funds(groupid,strategyid)
            value['funds']=await asyncio.to_thread(read)
        except (OSError,ValueError,sqlite3.Error,YAMLError):
            return {'result':-2,'message':'策略理论绩效读取失败，请稍后刷新'}
        value['performance_kind']='strategy_theoretical'
        return JSONResponse(value,headers={'Cache-Control':'no-store'})

    def enrich_records(groupid,value,payload):
        # The original monitor has already authorized this group before any file reads.
        group=recorded_groups(groupid)[0]
        with record_lock:
            manager=record_reader(group)
            channels=manager.get_channels(groupid) or []
            positions=channel_positions(manager.get_channel_positions(groupid,'all'))
            trades=[];orders=[]
            for channel in channels:
                trades.extend(manager.get_channel_trades(groupid,channel,limit=500))
                orders.extend(manager.get_channel_orders(groupid,channel,limit=500))
            try:
                value['market']=feed.market(group,payload.get('code',''),payload.get('period','m1'),
                    payload.get('count',600),True,payload.get('start_date'),payload.get('end_date'))
            except (OSError,ValueError):
                market=value.get('market') or {};value['market']=market
                market['source_warning']='行情读桥暂不可用；显示原监控服务读取的行情'
                market['market_message']=market['source_warning']
            market=value['market'];owners=position_owners(group)
            from .ExecutionQuality import execution_quality
            from .ExecutionQualityEvents import event_quality
            quality=execution_quality(group,channels,contracts=market.get('contracts') or [])
            quality['telemetry']=event_quality(group,channels)
            value['execution_quality']=quality
            quotes={market.get('code'):market}
            for position in positions:
                code=position['code']
                if code not in quotes:
                    try:quotes[code]=feed.market(group,code,'m1',20,False)
                    except (OSError,ValueError):quotes[code]={}
                position_market_fields(position,quotes[code],owners)
            value['positions']=positions
            value['trades']=sorted(trades_with_dates(group,trades,market.get('contracts') or []),key=lambda r:r.get('time',0))[-1000:]
            value['orders']=sorted(orders,key=lambda r:r.get('time',0))[-1000:]
            record_account_receipts(groupid,value.get('accounts',[]),group['path'],feed.history_root)

    return app


def main():
    import uvicorn
    os.umask(0o077)
    root=os.environ.get('WT_RESEARCH_ROOT','/data/research/workspace-store')
    Path(root).mkdir(parents=True,exist_ok=True)
    os.chdir(root)
    app=create_research_app(root=root,
        runs=os.environ.get('WT_RESEARCH_RUNS','/data/research/workspace-runs'),
        template=os.environ.get('WT_RESEARCH_TEMPLATE','/opt/research-template'),
        plus_root=os.environ.get('WT_PLUS_REPORTS','/data/research/_plus'),
        upstream=os.environ.get('WT_MONITOR_UPSTREAM','http://127.0.0.1:8100'),
        owner=os.environ.get('WT_MONITOR_OWNER','localadmin'),
        records_db=os.environ.get('WT_MONITOR_RECORDS_DB'),
        account_history=os.environ.get('WT_MONITOR_ACCOUNT_HISTORY'))
    uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('WT_RESEARCH_PORT','8103')))


if __name__=='__main__':
    main()
