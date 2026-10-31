import {test,expect} from '@playwright/test'

async function fixture(page){
 const calls=[],errors=[]
 const project={id:'project',title:'单合约研究',version:1,kind:'timeseries',report_id:'official-nanhua-1',template_id:'dual_ma',code:'',hypothesis:'检验方向'}
 const factor={id:'factor',name:'动量',version:1,builtin_id:'momentum',lookback:5,inputs:['close'],formula:'close[t]/close[t-5]-1'}
 const reports=[{id:'official-nanhua-1',provider:'nanhua',title:'螺纹周报',organization:'南华期货',body:'正文',report_type:'周报',report_type_origin:'source',source_category:'周度报告 / 黑色',topic_tags:['黑色'],fetch_state:'text_ready'},
 {id:'official-ruida-2',provider:'ruida',title:'铜专题',organization:'瑞达期货',body:'正文',report_type:'专题/调研',report_type_origin:'title_rule',source_category:'瑞达研究',topic_tags:['有色'],fetch_state:'text_ready'}]
 const metrics={paired_rows:60,rank_ic:.12,group_monotonicity:.2,last_label_exit:202610201450}
 const candidate={id:'candidate-1',name:'动量',lookback:5,train:metrics,validation:metrics,validation_stability:[{segment:1,...metrics}],purged_train:4,purged_validation:4,redundant_with:null}
 const analysis={candidates:[candidate],correlations:[],recommended_candidate_id:candidate.id,redundancy_threshold:.9,split:{train_percent:60,validation_percent:20,test_percent:20,start:202610190900,validation_start:202610210900,test_start:202610230900,end:202610261500}}
 let workflow=null
 page.on('pageerror',error=>errors.push(error.message))
 await page.routeWebSocket(/\/ws(?:[/?]|$)/,ws=>ws.onMessage(text=>{const message=JSON.parse(text);if(['subscribe','heartbeat'].includes(message.type))ws.send(JSON.stringify(message))}))
 await page.route('**/mgr/**',async route=>{
  const path=new URL(route.request().url()).pathname,body=route.request().postDataJSON()||{};calls.push({path,body});let data={}
  if(path==='/mgr/session')data={userinfo:{loginid:'researcher',name:'研究员',role:'researcher'}}
  if(path==='/mgr/qrygrp')data={groups:[]}
  if(path==='/mgr/research/list')data={can_edit:true,projects:[project],reports,factors:[factor],factor_builtins:[],experiments:[],datasets:[],research_workflows:workflow?[workflow]:[],factor_experiments:[{id:'input',project_id:project.id,code:'SHFE.rb.2701',period:'m1',status:'completed',factor_id:factor.id,factor_snapshot:factor}],report_sources:[{id:'nanhua',name:'南华期货'},{id:'ruida',name:'瑞达期货'}],report_filters:{report_types:['周报','专题/调研'],topics:['黑色','有色']}}
  if(path==='/mgr/research/report_search')data={reports:reports.filter(x=>(!body.provider||x.provider===body.provider)&&(!body.report_type||x.report_type===body.report_type)&&(!body.topic||x.topic_tags.includes(body.topic)))}
  if(path==='/mgr/research/workflow_run'){workflow={id:'workflow',project_id:project.id,code:'SHFE.rb.2701',status:'review',lifecycle:'candidate',candidates:[candidate],test_revealed:false,holdout_note:'来源历史若已查看，不是全新未知样本。'};data={workflow:{...workflow,status:'comparing'}}}
  if(path==='/mgr/research/workflow_detail')data={workflow,analysis}
  if(path==='/mgr/research/workflow_choose'){workflow.selected_candidate_id=body.candidate_id;data={workflow}}
  if(path==='/mgr/research/workflow_reveal'){workflow={...workflow,status:'evaluated',test_revealed:true};analysis.test={...metrics,oriented_rank_ic:.12,stability:[{segment:1,...metrics}]};data={workflow,analysis}}
  if(path==='/mgr/research/workflow_decide'){workflow={...workflow,lifecycle:body.lifecycle,decision_note:body.note};data={workflow}}
  await route.fulfill({json:{result:0,...data}})
 })
 await page.goto('/#/research');await expect(page.getByRole('heading',{name:project.title,exact:true})).toBeVisible()
 return {calls,errors}
}

test('project workflow freezes input before one test reveal and records a research decision',async({page})=>{
 const f=await fixture(page)
 await page.locator('.research-tabs').getByRole('button',{name:'研究检查',exact:true}).click()
 await page.getByRole('button',{name:'新建检查',exact:true}).click()
 await page.getByLabel('候选回看窗口',{exact:true}).fill('3,5,8')
 await page.getByRole('button',{name:'保存输入并开始检查',exact:true}).click()
 await expect(page.getByRole('button',{name:'选定候选',exact:true})).toBeVisible()
 await expect(page.getByRole('button',{name:'锁定选择并揭示保留测试',exact:true})).toBeDisabled()
 await page.getByRole('button',{name:'选定候选',exact:true}).click()
 await page.getByRole('button',{name:'锁定选择并揭示保留测试',exact:true}).click()
 await expect(page.getByRole('button',{name:'已选定',exact:true})).toBeDisabled()
 await page.getByLabel('研究判断原因',{exact:true}).fill('下一步交给WT检验费用与成交')
 await page.getByRole('button',{name:'采纳为研究候选',exact:true}).click()
 await expect(page.locator('.research-checks')).toContainText('已采纳：下一步交给WT检验费用与成交')
 expect(f.calls.find(x=>x.path.endsWith('workflow_run')).body).toMatchObject({project_id:'project',input_experiment_id:'input',factor_ids:['factor'],windows:[3,5,8]})
 expect(f.calls.filter(x=>x.path.endsWith('workflow_reveal'))).toHaveLength(1)
 expect(f.calls.some(x=>/strategy_apply|startapp|set_position/.test(x.path))).toBe(false)
 expect(f.errors).toEqual([])
})

test('report type and topic filter source categories without changing report text',async({page})=>{
 const f=await fixture(page)
 await page.getByRole('button',{name:/^研报库/}).click()
 await page.locator('.research-report-filters .el-select').nth(1).click()
 await page.getByRole('option',{name:'周报',exact:true}).click()
 await expect(page.locator('.research-paper')).toHaveCount(1)
 await expect(page.locator('.research-paper')).toContainText('官网：周度报告 / 黑色')
 await page.locator('.research-report-filters .el-select').nth(2).click()
 await page.getByRole('option',{name:'黑色',exact:true}).click()
 await expect.poll(()=>f.calls.filter(x=>x.path.endsWith('report_search')).at(-1)?.body).toMatchObject({report_type:'周报',topic:'黑色'})
 await expect(page.locator('.research-library')).toContainText('品种主题来自标题规则')
 expect(f.errors).toEqual([])
})
