import {test,expect} from '@playwright/test'

async function researchFixture(page,{guest=false,existing=false,automation=null}={}){
 const calls=[],errors=[]
 const report={id:'public-report',title:'趋势信号研究原文',organization:'公开研究机构',published_at:'2026-10-01',source_url:'https://example.com/public-report.pdf',body:'原文讨论趋势延续与交易成本。',notes:'模板用于方法验证。',public:true}
 const template={id:'dual_ma',label:'双均线趋势 · 方法验证',params:{fast:5,slow:20,lots:1},bounds:{fast:[2,200],slow:[3,500],lots:[1,20]}}
 let projects=existing?[{id:'project-one',title:'趋势假设复现',report_id:report.id,kind:'timeseries',version:1,template_id:'dual_ma',hypothesis:'趋势能否持续',formula:'快均线与慢均线',notes:'第2页',code:'class ResearchTrend: pass'}]:[]
 let experiments=existing?[{id:'experiment-running',project_id:'project-one',project_version:1,dataset:'history-rb-d',code:'SHFE.rb.2701',period:'d',start:20260803,end:20261023,created_at:1792890000,status:'running',progress:30}]:[]
 const datasets=[{id:'history-rb-d',code:'SHFE.rb.2701',label:'rb2701 · 日线',period:'d',rows:54,start:202608031500,end:202610231500}]
 page.on('pageerror',error=>errors.push(error.message))
 await page.routeWebSocket(/\/ws(?:[/?]|$)/,ws=>ws.onMessage(text=>{const message=JSON.parse(text);if(['subscribe','heartbeat'].includes(message.type))ws.send(JSON.stringify(message))}))
 await page.route('**/mgr/**',async route=>{
  const path=new URL(route.request().url()).pathname,body=route.request().postDataJSON()||{}
  calls.push({path,body})
  let data={}
  if(path==='/mgr/session')data={userinfo:{loginid:guest?'guest':'research-user',name:'研究员',role:guest?'guest':'researcher'}}
  if(path==='/mgr/qrygrp')data={groups:[]}
  if(path==='/mgr/research/list')data={can_edit:!guest,reports:[report],projects:guest?[]:projects,experiments:guest?[]:experiments,datasets,templates:[template],factor_automation:automation}
  if(path==='/mgr/research/catalog')data={datasets,templates:[template]}
  if(path==='/mgr/research/project_save'){
   const project={...body,id:body.id||'project-one',version:1,code:'class ResearchTrend: pass'}
   projects=[project];data={project}
  }
  if(path==='/mgr/research/experiment_run'){
   const experiment={...body,id:'experiment-complete',project_version:1,code:'SHFE.rb.2701',period:'d',created_at:1792890000,status:'completed',progress:100,metrics:{trade_count:4}}
   experiments=[experiment];data={experiment}
  }
  if(path==='/mgr/research/experiment_cancel'){experiments=experiments.map(x=>({...x,status:'stopped',error:'用户取消实验'}));data={experiment:experiments[0]}}
  if(path==='/mgr/research/experiment_detail')data={experiment:experiments.find(x=>x.id===body.experiment_id),project:projects[0],report,metrics:{trade_count:4,closed_trades:2,signal_count:8,fee:16,total_return:1.2,max_drawdown:0.8,sharpe:0.4,days:54},diagnostics:{calculations:54,warmup_skips:20,long_signals:20,short_signals:14,target_changes:4,message:'信号来自策略回调，成交来自 WT。',samples:[{time:202610231500,fast:3050,slow:3040,target:1}]},funds:[{date:20260803,dynbalance:0},{date:20261023,dynbalance:12000}],trades:[{code:'SHFE.rb.2701',time:202610231500,direction:'LONG',offset:'OPEN',price:3040,volume:1,fee:4}],read_url:'/mgr/research/results/experiment-complete'}
  await route.fulfill({json:{result:0,...data}})
 })
 await page.goto('/#/research')
 await expect(page.getByRole('heading',{name:'研究',exact:true})).toBeVisible()
 return {calls,errors,report}
}

test('report reading to saved project, native experiment and traceable result',async({page})=>{
 const f=await researchFixture(page)
 await expect(page.getByRole('link',{name:'研究',exact:true})).toBeVisible()
 await expect(page.locator('.research-first-paper')).toContainText(f.report.title)
 await expect(page.getByRole('button',{name:'阅读这份研报',exact:true})).toBeVisible()
 await page.getByRole('button',{name:/^研报库/}).click()
 await page.getByRole('button',{name:'阅读',exact:true}).click()
 const reader=page.getByRole('dialog',{name:f.report.title,exact:true})
 await expect(reader).toContainText(f.report.body)
 await expect(reader.getByRole('link',{name:/打开官方原文/})).toHaveAttribute('href',f.report.source_url)
 await reader.getByRole('button',{name:'从这份研报创建研究'}).click()
 const create=page.getByRole('dialog',{name:'新建研究',exact:true})
 await create.getByLabel('新研究名称',{exact:true}).fill('趋势假设复现')
 await create.getByRole('button',{name:'创建研究',exact:true}).click()
 await expect(page.getByRole('heading',{name:'趋势假设复现',exact:true})).toBeVisible()
 await page.locator('.research-tabs').getByRole('button',{name:'实验',exact:true}).click()
 await page.getByRole('button',{name:'配置实验',exact:true}).click()
 const run=page.getByRole('dialog',{name:'配置复现实验',exact:true})
 await expect(run).toContainText('实际覆盖 2026-08-03 15:00 — 2026-10-23 15:00')
 await run.getByRole('button',{name:'保存配置并运行'}).click()
 const detail=page.getByRole('dialog',{name:'实验与成果',exact:true})
 await expect(detail).toContainText('已完成 · experiment-complete')
 await expect(detail).toContainText('信号来自策略回调，成交来自 WT。')
 await expect(detail.getByRole('link',{name:/打开完整成果阅读页/})).toHaveAttribute('href','/mgr/research/results/experiment-complete')
 await expect(detail.getByRole('img',{name:'WT 原生日结累计净盈亏曲线'}).locator('canvas')).toHaveCount(1)
 const launch=f.calls.find(x=>x.path==='/mgr/research/experiment_run')
 expect(launch.body).toMatchObject({project_id:'project-one',dataset:'history-rb-d',start:20260803,end:20261023,params:{fast:5,slow:20,lots:1}})
 expect(f.calls.some(x=>x.path==='/mgr/startapp'||x.path==='/mgr/strategy_apply')).toBe(false)
 expect(f.errors).toEqual([])
})

test('390px project and cancellation stay inside the workspace',async({page})=>{
 await page.setViewportSize({width:390,height:844})
 const f=await researchFixture(page,{existing:true})
 await page.locator('.research-tabs').getByRole('button',{name:'实验',exact:true}).click()
 await page.getByRole('button',{name:'取消',exact:true}).click()
 await expect(page.locator('.research-experiment-table')).toContainText('已停止')
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true)
 expect(f.calls.filter(x=>x.path==='/mgr/research/experiment_cancel')).toHaveLength(1)
 expect(f.errors).toEqual([])
})

test('guest can read public reports and cannot create or run',async({page})=>{
 const f=await researchFixture(page,{guest:true})
 await expect(page.getByRole('button',{name:'添加研报',exact:true})).toBeDisabled()
 await expect(page.locator('.research-actions').getByRole('button',{name:'新建研究',exact:true})).toBeDisabled()
 await page.getByRole('button',{name:/^研报库/}).click()
 await page.getByRole('button',{name:'阅读',exact:true}).click()
 await expect(page.getByRole('dialog').getByRole('button',{name:'从这份研报创建研究'})).toBeDisabled()
 expect(f.calls.filter(x=>/(_save|_run|_cancel)$/.test(x.path))).toEqual([])
 expect(f.errors).toEqual([])
})


test('automatic research shows a rejected factor without publishing a strategy',async({page})=>{
 const automation={counts:{screened:15,jobs:1,versions:1,research_accepted:0},cycle:[{checked_at:'2026-10-27T06:00:00Z'}],jobs:[{id:'a1',status:'research_rejected',spec:{name:'研报时序动量',frequency:'d'},reasons:['test_nonpositive_after_cost']}],versions:[{id:'a1-v1',name:'研报时序动量',version:1,status:'research_rejected',selected_candidate:{expression:'mean(close / lag(close, 1) - 1, lookback)',lookback:5,horizon:5},source_refs:[{url:'https://www.htfc.com/report.pdf',section:'策略方法',pdf_page:4}]}],monitors:[]}
 const f=await researchFixture(page,{automation})
 await page.locator('.research-sections button').filter({hasText:'因子库'}).click()
 const panel=page.getByRole('region',{name:'因子自动研究'})
 await expect(panel).toBeVisible()
 await panel.locator('summary').first().click()
 await expect(panel).toContainText('研究检验未通过')
 await expect(panel).toContainText('保留测试扣成本后未改善')
 await panel.getByText('查看公式与来源',{exact:true}).click()
 await expect(panel.getByRole('link',{name:'打开研报原文'})).toHaveAttribute('href','https://www.htfc.com/report.pdf')
 await page.setViewportSize({width:390,height:844})
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
 expect(f.calls.some(x=>/strategy_apply|ctrlstra|startapp/.test(x.path))).toBe(false)
 expect(f.errors).toEqual([])
 await page.screenshot({path:'test-results/automation-mobile.png',fullPage:true})
})
