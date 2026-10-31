"""Authenticate terminal operations through the original monitor session."""
import json
from urllib.request import Request as UrlRequest, build_opener, ProxyHandler

from fastapi import Body, Request
from .Roles import RESEARCH_ROLES

READ_ACTIONS={'catalog','data','intelligence','research_list','workflow_list'}
WRITE_ACTIONS={'calculate','research_start','workflow_save','workflow_run'}


def register_terminal_routes(app,check_auth,owner='localadmin'):
    @app.post('/mgr/research/terminal/{action}')
    def terminal(action:str,request:Request,value:dict=Body(default={})):
        ok,user=check_auth(request)
        if not ok:return user
        guest=user.get('role')=='guest'
        if action not in READ_ACTIONS|WRITE_ACTIONS:return dict(result=-2,message='未知终端操作')
        if (user.get('role') not in RESEARCH_ROLES and not guest) or guest and action in WRITE_ACTIONS:
            return dict(result=-403,message='需要研究权限')
        if len(json.dumps(value))>1024*1024:return dict(result=-2,message='输入超过 1 MiB')
        payload=dict(action=action,owner=owner if guest else user['loginid'],guest=guest,params=value)
        req=UrlRequest('http://127.0.0.1:8104/dispatch',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        try:
            with build_opener(ProxyHandler({})).open(req,timeout=65) as response:return json.load(response)
        except (OSError,ValueError):return dict(result=-2,message='分析服务暂时不可用，请稍后重试')
