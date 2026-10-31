"""Owner-scoped queue for the trusted multi-factor trend research worker."""
import json
import math
import uuid


class ResearchPortfolio:
    def __init__(self, workspace):
        self.workspace = workspace

    def status(self, owner, project_id=None):
        if project_id:
            self.workspace._get('project', owner, project_id)
        path = self.workspace.root / 'portfolio-policy.json'
        policy = json.loads(path.read_text()) if path.is_file() else dict(enabled=False, instruments=[], factors=[])
        def records(kind):
            rows = self.workspace._rows(kind, owner)
            return [row for row in rows if not project_id or row.get('project_id') == project_id][:20]
        states = {row['version_id']:row for row in self.workspace._rows('portfolio_version_state', owner)}
        versions = []
        for row in records('portfolio_research_version'):
            value = {key:row.get(key) for key in ('id','project_id','created_at','status','message','test')}
            value['model'] = {key:row['model'].get(key) for key in ('weights','method','frozen_at')}
            native=row.get('native_validation',{})
            value['native_validation']={key:native.get(key) for key in ('status','reasons')}
            value['native_validation']['scenarios']={scenario:{role:{key:run.get(key) for key in
                ('net','closed_trades','max_daily_drawdown_pct')} for role,run in pair.items()}
                for scenario,pair in native.get('scenarios',{}).items()}
            value['promotion'] = states.get(row['id'], {}).get('promotion')
            versions.append(value)
        return dict(policy=policy, jobs=records('portfolio_research_job'), versions=versions,
                    monitors=records('portfolio_monitor'))

    def request(self, owner, value):
        project_id = value.get('project_id')
        if not project_id:
            raise ValueError('请先选择一个研究项目')
        current = self.status(owner, project_id)
        policy = current['policy']
        if not policy.get('enabled'):
            raise ValueError('后台尚未启用多因子研究')
        codes = value.get('codes') or [row['code'] for row in policy['instruments']]
        allowed = {row['code'] for row in policy['instruments']}
        if (not isinstance(codes, list) or not codes or any(type(code) is not str for code in codes) or len(codes) != len(set(codes))
                or not set(codes) <= allowed or policy['anchor'] not in codes):
            raise ValueError('只能选择已登记品种，并保留基准合约')
        method = value.get('weight_method', 'select')
        if method not in ('select', 'equal', 'risk'):
            raise ValueError('权重方式只能是验证集选择、等权或风险权重')
        allocations = value.get('allocations') or {code:1 / len(codes) for code in codes}
        if (not isinstance(allocations, dict) or set(allocations) != set(codes) or any(type(x) not in (int,float) or not math.isfinite(x)
                or not 0 < x <= 1 for x in allocations.values()) or sum(allocations.values()) > 1 + 1e-9):
            raise ValueError('品种预算须大于零，总和不超过100%')
        spec = dict(codes=codes, weight_method=method, allocations=allocations, policy_version=policy['version'])
        with self.workspace.lock:
            previous = next((row for row in current['jobs'] if row['spec'] == spec
                and row['status'] in ('ready','running','waiting_data','waiting_validation')), None)
            if previous:
                return previous
            return self.workspace._save('portfolio_research_job', owner, dict(id='portfolio-job-'+uuid.uuid4().hex,
                project_id=project_id, spec=spec, status='ready', reasons=[], source_kind='host_defined',
                automatic_apply_eligible=False))


def register_portfolio_routes(app, workspace, authorize, failed):
    from fastapi import Body, Request
    portfolios = ResearchPortfolio(workspace)

    @app.post('/mgr/research/portfolio_status', tags=['研究工作台'])
    def status(request:Request, value:dict=Body(...)):
        user,error = authorize(request,value.get('token'))
        if error:return error
        try:return dict(result=0,**portfolios.status(user['loginid'],value.get('project_id')))
        except (ValueError,OSError,KeyError) as exc:return failed(exc)

    @app.post('/mgr/research/portfolio_run', tags=['研究工作台'])
    def run(request:Request, value:dict=Body(...)):
        user,error = authorize(request,value.get('token'))
        if error:return error
        try:return dict(result=0,job=portfolios.request(user['loginid'],value))
        except (ValueError,OSError,KeyError) as exc:return failed(exc)
