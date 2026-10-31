"""A bounded analysis graph; production factor admission remains host-owned."""
import time
import uuid

from terminal_analytics import calculate

NODE_TYPES = [dict(id='data',name='公开数据',description='读取一个已登记的数据集'),
              dict(id='returns',name='价格转收益',description='按相邻观测计算简单收益'),
              dict(id='risk',name='风险分析',description='计算 VaR、CVaR、夏普和回撤'),
              dict(id='result',name='保存结果',description='保存输入来源与计算结果')]


def validate_graph(value):
    nodes=value.get('nodes',[]);edges=value.get('edges',[])
    if not isinstance(nodes,list) or not 2<=len(nodes)<=8 or not isinstance(edges,list) or len(edges)>12:
        raise ValueError('分析流程须有 2 至 8 个节点')
    ids=[n.get('id') for n in nodes]
    if any(not isinstance(i,str) or not i.isalnum() or len(i)>40 for i in ids) or len(set(ids))!=len(ids):
        raise ValueError('节点标识必须唯一')
    types={n['id']:n.get('type') for n in nodes};allowed={n['id'] for n in NODE_TYPES}
    if any(t not in allowed for t in types.values()):raise ValueError('流程只允许数据、收益、风险和结果节点')
    incoming={i:[] for i in ids}
    for edge in edges:
        source,target=edge.get('source'),edge.get('target')
        if source not in ids or target not in ids or source==target or source in incoming[target]:raise ValueError('连线不合法')
        incoming[target].append(source)
    order=[];remaining=set(ids)
    while remaining:
        ready=[i for i in ids if i in remaining and all(parent in order for parent in incoming[i])]
        if not ready:raise ValueError('流程不能形成循环')
        order.extend(ready);remaining.difference_update(ready)
    accepts={'data':None,'returns':'data','risk':'returns','result':'risk'}
    for i,parents in incoming.items():
        expected=accepts[types[i]]
        if expected is None and parents or expected is not None and (len(parents)!=1 or types[parents[0]]!=expected):
            raise ValueError('请连接：数据 → 收益 → 风险 → 结果')
    if not any(t=='result' for t in types.values()):raise ValueError('流程需要一个结果节点')
    return order,incoming


class Workflows:
    def __init__(self,store,hub):self.store=store;self.hub=hub

    def save(self,owner,p):
        validate_graph(p)
        if p.get('id'):self.store._get('terminal_workflow',owner,p['id'])
        item=dict(id=p.get('id') or uuid.uuid4().hex,name=str(p.get('name') or '分析流程')[:80],nodes=p['nodes'],edges=p['edges'])
        return self.store._save('terminal_workflow',owner,item)

    def run(self,owner,identifier):
        graph=self.store._get('terminal_workflow',owner,identifier);order,parents=validate_graph(graph)
        nodes={n['id']:n for n in graph['nodes']};outputs={}
        run=dict(id=uuid.uuid4().hex,workflow_id=identifier,graph=graph,status='running',owner=owner,started_at=time.time())
        try:
            for key in order:
                node=nodes[key];params=node.get('params',{});previous=outputs[parents[key][0]] if parents[key] else None
                if node['type']=='data':
                    output=self.hub.read(params.get('dataset','sp500'),True)
                    if output.get('status')!='ready' or output.get('stale'):raise ValueError('数据不可用或已过期，流程停止')
                    if output.get('frequency')!='日度':raise ValueError('该模板只接受日频价格；宏观序列不能自动当成可交易价格')
                elif node['type']=='returns':
                    series=previous['series'];prices=[r['value'] for r in series]
                    if len(prices)<21 or any(p<=0 for p in prices):raise ValueError('至少需要 21 个正价格')
                    output=dict(returns=[b/a-1 for a,b in zip(prices,prices[1:])],
                                dates=[r['time'] for r in series[1:]],source=previous['source_url'],costs_included=False)
                elif node['type']=='risk':
                    output=calculate('risk',dict(returns=previous['returns'],periods=params.get('periods',252),
                        confidence=params.get('confidence',.95),capital=params.get('capital',10000),risk_free=params.get('risk_free',.02)))
                    output.update(source=previous['source'],dates=previous['dates'],costs_included=False)
                    output['note']='标的价格收益，未扣交易费用；不是策略回测，不能用于上线准入。'
                else:output=previous
                outputs[key]=output
            run.update(status='completed',outputs=outputs)
        except Exception as error:run.update(status='failed',outputs=outputs,error=str(error)[:300])
        self.store._save('terminal_workflow_run',owner,run)
        return run
