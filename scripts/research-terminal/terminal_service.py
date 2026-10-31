"""Loopback-only terminal sidecar in the existing isolated research environment."""
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import threading

OPS=Path(os.environ.get('WT_REVIEW_OPS',Path(__file__).resolve().parent))
sys.path.insert(0,str(OPS))
from research_records import Records
from terminal_analytics import TOOLS, calculate
from terminal_data import DataHub
from terminal_research import ResearchDesk, ROLES, intelligence
from terminal_workflows import NODE_TYPES, Workflows


class Terminal:
    def __init__(self,config=None):
        self.config=config or json.loads((OPS/'factor-automation-config.json').read_text())
        self.store=Records(self.config['workspace_root'])
        self.hub=DataHub(self.store.root/'terminal-data')
        self.research=ResearchDesk(self.store,self.config)
        self.workflows=Workflows(self.store,self.hub)
        self.lock=threading.Lock()

    def pipeline(self,owner):
        kinds=[('report','资料'),('summary_job','摘要'),('factor_report_screen','原文筛选'),
               ('factor_automation','因子研究'),('automated_factor_version','因子版本'),
               ('factor_monitor','持续监控')]
        rows=[]
        for kind,label in kinds:
            records=self.store._rows(kind,owner)
            rows.append(dict(id=kind,name=label,count=len(records),states=dict(Counter(r.get('status','recorded') for r in records))))
        return rows

    def dispatch(self,action,owner,p,guest=False):
        if not owner or len(owner)>128:raise ValueError('无效的研究账户')
        if action=='catalog':
            return dict(tools=TOOLS,roles=ROLES,node_types=NODE_TYPES,**self.hub.catalog(),budget=self.research.budget(),
                        pipeline=self.pipeline(owner),can_edit=not guest,
                        integration=dict(trading='WT 原生执行',factors='RD-Agent / KunQuant / Optuna / MLflow',
                            validation='原生收益矩阵 / DSR / PBO / fincore',admission='40 对齐日 + 20 独立前向日 + 60 平仓',
                            mcp='本机 stdio，只读研究工具'))
        if action=='data':return self.hub.read(p.get('dataset'),bool(p.get('refresh')) and not guest)
        if action=='intelligence':
            return dict(articles=[],themes=[],edges=[],note='游客可加载公开数据缓存；个人研究资料的关系图仅账户所有者可见。') if guest else intelligence(self.store,owner)
        if action=='research_list':return dict(jobs=[] if guest else self.store._rows('terminal_research',owner)[:30],budget=self.research.budget())
        if action=='workflow_list':return dict(workflows=[] if guest else self.store._rows('terminal_workflow',owner),runs=[] if guest else self.store._rows('terminal_workflow_run',owner)[:20])
        if guest:raise PermissionError('游客只能查看已有数据，不能启动计算或模型')
        if action=='research_start':return dict(job=self.research.start(owner,p))
        if action=='workflow_save':return dict(workflow=self.workflows.save(owner,p))
        if not self.lock.acquire(blocking=False):raise ValueError('已有分析任务运行，请稍后重试')
        try:
            if action=='calculate':return calculate(p.get('tool'),p.get('params',{}))
            if action=='workflow_run':return dict(run=self.workflows.run(owner,p.get('id')))
            raise ValueError('未知终端操作')
        finally:self.lock.release()


def create_app(terminal=None):
    from fastapi import FastAPI, Request
    from starlette.responses import JSONResponse
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    terminal=terminal or Terminal()

    @app.get('/healthz')
    def health():return dict(status='ready',service='terminal',trading=False)

    @app.post('/dispatch')
    async def dispatch(request:Request):
        import asyncio
        if request.headers.get('content-length') and int(request.headers['content-length'])>1024*1024:
            return JSONResponse(dict(result=-2,message='请求超过 1 MiB'),status_code=413)
        try:
            data=await request.json()
            value=await asyncio.to_thread(terminal.dispatch,data['action'],data['owner'],data.get('params',{}),data.get('guest',False))
            return dict(result=0,**value)
        except PermissionError as e:return dict(result=-403,message=str(e))
        except (ValueError,KeyError,TypeError,StopIteration,RuntimeError) as e:return dict(result=-2,message=str(e)[:300] or '记录不存在')
    return app


if __name__=='__main__':
    import uvicorn
    os.umask(0o077)
    uvicorn.run(create_app(),host='127.0.0.1',port=8104,log_level='warning')
