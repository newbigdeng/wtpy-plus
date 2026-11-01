"""Owner-scoped research APIs; attachments and results are passive documents."""
from pathlib import Path
import subprocess
from fastapi import Body, Request
from starlette.responses import FileResponse, HTMLResponse
from .ResearchWorkspace import ResearchWorkspace
from .Roles import RESEARCH_ROLES


def register_research_routes(app, bt_mon, check_auth, manager=None, workspace='/workspace', root=None):
    manager=manager or ResearchWorkspace(root or Path(bt_mon.path)/'_workspace',bt_mon,workspace)

    def authorize(request,token=None,read=False):
        ok,user=check_auth(request,token)
        if not ok:
            return None,user
        if user.get('role') not in RESEARCH_ROLES and not (read and user.get('role')=='guest'):
            return None,{'result':-403,'message':'需要策略研究权限'}
        return user,None

    def failed(error):
        return {'result':-2,'message':str(error)}

    from .ResearchPortfolio import register_portfolio_routes
    register_portfolio_routes(app,manager,authorize,failed)

    @app.post('/mgr/research/list',tags=['研究工作台'])
    def listing(request:Request,token:str=Body(None,embed=True),include_catalog:bool=Body(True,embed=True)):
        user,error=authorize(request,token,True)
        if error:return error
        try:
            return {'result':0,'can_edit':user.get('role') in RESEARCH_ROLES,**manager.list(user,include_catalog)}
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    @app.post('/mgr/research/catalog',tags=['研究工作台'])
    def catalog(request:Request,token:str=Body(None,embed=True)):
        user,error=authorize(request,token,True)
        if error:return error
        try:return {'result':0,**manager.catalog(user)}
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    @app.post('/mgr/research/report_save',tags=['研究工作台'])
    def report_save(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,'report':manager.save_report(user['loginid'],value)}
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    @app.post('/mgr/research/project_save',tags=['研究工作台'])
    def project_save(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,'project':manager.save_project(user['loginid'],value)}
        except (ValueError,OSError,RuntimeError,SyntaxError) as exc:return failed(exc)

    @app.post('/mgr/research/report_search',tags=['研究工作台'])
    def report_search(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'),True)
        if error:return error
        rows=manager.reports.reports(value.get('query',''),value.get('provider',''),value.get('report_type',''),value.get('topic',''))
        return {'result':0,'reports':manager.summaries.enrich(user['loginid'],rows,compact=True),
                'sources':manager.reports.sources(),'filters':manager.reports.filters()}

    @app.post('/mgr/research/report_detail',tags=['研究工作台'])
    def report_detail(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'),True)
        if error:return error
        try:
            report=manager._get('report',user['loginid'],value.get('report_id'))
            return {'result':0,'report':manager.summaries.enrich(user['loginid'],[report])[0]}
        except (ValueError,OSError) as exc:return failed(exc)

    @app.post('/mgr/research/report_summary_request',tags=['研究工作台'])
    def report_summary_request(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:
            with manager.lock:
                return {'result':0,'summary_job':manager.summaries.request(user['loginid'],value.get('report_id'),refresh=value.get('refresh') is True)}
        except (ValueError,OSError) as exc:return failed(exc)

    @app.post('/mgr/research/report_collect',tags=['研究工作台'])
    def report_collect(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        if user.get('role') not in ('admin','superman'):return {'result':-403,'message':'管理员可触发公共研报采集'}
        # Manual requests return immediately; the scheduled CLI uses the same collector.
        import threading
        if manager.reports.lock.locked():return {'result':-2,'message':'研报采集正在运行'}
        providers=value.get('providers')
        from .ResearchReports import PROVIDERS
        if providers is not None and (not isinstance(providers,list) or any(key not in PROVIDERS for key in providers)):
            return failed('研报来源不存在')
        threading.Thread(target=manager.reports.collect,args=(providers,6),name='research-reports',daemon=True).start()
        return {'result':0,'state':'queued','message':'采集已排队，刷新查看官网状态'}

    @app.post('/mgr/research/factor_save',tags=['研究工作台'])
    def factor_save(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,'factor':manager.factors.save(user['loginid'],value)}
        except (ValueError,OSError) as exc:return failed(exc)

    @app.post('/mgr/research/factor_run',tags=['研究工作台'])
    def factor_run(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,'factor_experiment':manager.factors.run(user,value)}
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    @app.post('/mgr/research/factor_detail',tags=['研究工作台'])
    def factor_detail(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,**manager.factors.detail(user['loginid'],value.get('experiment_id'))}
        except (ValueError,OSError) as exc:return failed(exc)

    @app.get('/mgr/research/factor_results/{experiment_id}',tags=['研究工作台'])
    def factor_result(request:Request,experiment_id:str,kind:str='result'):
        user,error=authorize(request)
        if error:return error
        try:
            item=manager._get('factor_experiment',user['loginid'],experiment_id)
            if kind not in ('result','input'):raise ValueError('文件类型不存在')
            name=item['id']+('-input' if kind=='input' else '')+'.csv'
            path=manager.root/'factor-results'/name
            if not path.is_file():raise ValueError('因子实验尚未完成')
            return FileResponse(path,filename=name,media_type='text/csv',headers={'X-Content-Type-Options':'nosniff'})
        except (ValueError,OSError) as exc:return failed(exc)

    @app.post('/mgr/research/workflow_run',tags=['研究工作台'])
    def workflow_run(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,'workflow':manager.factors.workflow.run(user,value)}
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    @app.post('/mgr/research/workflow_detail',tags=['研究工作台'])
    def workflow_detail(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,**manager.factors.workflow.detail(user['loginid'],value.get('workflow_id'))}
        except (ValueError,OSError) as exc:return failed(exc)

    @app.post('/mgr/research/workflow_choose',tags=['研究工作台'])
    def workflow_choose(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,'workflow':manager.factors.workflow.choose(user['loginid'],value)}
        except (ValueError,OSError) as exc:return failed(exc)

    @app.post('/mgr/research/workflow_reveal',tags=['研究工作台'])
    def workflow_reveal(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,**manager.factors.workflow.reveal(user['loginid'],value.get('workflow_id'))}
        except (ValueError,OSError,RuntimeError,subprocess.TimeoutExpired) as exc:return failed(exc)

    @app.post('/mgr/research/workflow_decide',tags=['研究工作台'])
    def workflow_decide(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,'workflow':manager.factors.workflow.decide(user['loginid'],value)}
        except (ValueError,OSError) as exc:return failed(exc)

    @app.post('/mgr/research/experiment_run',tags=['研究工作台'])
    def experiment_run(request:Request,value:dict=Body(...)):
        user,error=authorize(request,value.get('token'))
        if error:return error
        try:return {'result':0,'experiment':manager.run(user,value)}
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    @app.post('/mgr/research/experiment_detail',tags=['研究工作台'])
    def experiment_detail(request:Request,experiment_id:str=Body(...,embed=True),token:str=Body(None,embed=True)):
        user,error=authorize(request,token)
        if error:return error
        try:return {'result':0,**manager.detail(user['loginid'],experiment_id)}
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    @app.post('/mgr/research/experiment_delete',tags=['研究工作台'])
    def experiment_delete(request:Request,experiment_id:str=Body(...,embed=True)):
        user,error=authorize(request)
        if error:return error
        try:
            manager.delete_experiment(user['loginid'],experiment_id)
            return {'result':0}
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    @app.post('/mgr/research/experiment_cancel',tags=['研究工作台'])
    def experiment_cancel(request:Request,experiment_id:str=Body(...,embed=True),token:str=Body(None,embed=True)):
        user,error=authorize(request,token)
        if error:return error
        try:return {'result':0,'experiment':manager.cancel(user['loginid'],experiment_id)}
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    @app.get('/mgr/research/attachments/{report_id}',tags=['研究工作台'])
    def attachment(request:Request,report_id:str):
        user,error=authorize(request,read=True)
        if error:return error
        try:
            path,name=manager.attachment(user['loginid'],report_id)
            return FileResponse(path,filename=name,content_disposition_type='inline',
                headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':"sandbox; default-src 'none'"})
        except (ValueError,OSError) as exc:return failed(exc)

    @app.get('/mgr/research/results/{experiment_id}',tags=['研究工作台'])
    def result(request:Request,experiment_id:str):
        user,error=authorize(request)
        if error:return error
        try:
            return HTMLResponse(manager.result_html(user['loginid'],experiment_id),
                headers={'Content-Security-Policy':"sandbox; default-src 'none'; style-src 'unsafe-inline'",'X-Content-Type-Options':'nosniff'})
        except (ValueError,OSError,RuntimeError) as exc:return failed(exc)

    return manager
