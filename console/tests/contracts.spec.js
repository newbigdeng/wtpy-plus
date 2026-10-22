import {test,expect} from '@playwright/test'
import {fixture} from './fixtures/console.js'

async function contractFixture(page,running=false){
 const f=await fixture(page)
 const codes=['SHFE.rb.2701','SHFE.rb.2705','SHFE.cu.2701']
 const catalog=codes.map(code=>({code,subscription_code:code.replace(/\.([0-9]+)$/, '$1'),exchange:'SHFE',product:code.split('.')[1],name:code.includes('.rb.')?'螺纹钢':'铜'}))
 const row={id:'trend',name:'合约管理示例',class_name:'ExampleCta',library_id:'example',library_version:1,engine_type:'CTA',trader:'simnow',enabled:true,params:{code:'SHFE.rb.2701',capital_limit:100000,margin_fraction:.4,risk_fraction:.02},execution:{id:'minimpact'}}
 const plan={revision:'current',engine_type:'CTA',strategies:[row],trading_enabled:true,risk:{rules:[]}}
 const statuses={trend:{id:'trend',name:row.name,engine:'CTA',state:running?'running':'stopped',position_known:true,position_volume:0,new_strategy:false,in_launch_plan:true,applied_contracts:['SHFE.rb.2701'],planned_contracts:[running?'SHFE.rb.2705':'SHFE.rb.2701'],plan_pending:running}}
 if(running)row.params.code='SHFE.rb.2705'
 const state={plan,running,can_manage:true,managed:true,has_applied:true,applied_revision:'active',applied_trading_enabled:true,pending_changes:running,strategy_status:statuses}
 await page.route('**/mgr/strategy_catalog',route=>route.fulfill({json:{result:0,channels:[{id:'simnow',label:'SimNow'}],executors:[]}}))
 await page.route('**/mgr/strategy_plan',route=>route.fulfill({json:{result:0,...state}}))
 await page.route('**/mgr/strategy_library',route=>route.fulfill({json:{result:0,strategies:[]}}))
 await page.route('**/mgr/qrystra_runtime',route=>route.fulfill({json:{result:0,strategies:Object.values(statuses),can_control:true}}))
 await page.route('**/mgr/market/subscriptions',route=>route.fulfill({json:{result:0,catalog,products:[],selected:['SHFE.rb2701'],profile_label:'SimNow-1',process_running:running,datahub_running:true,can_manage:true,can_apply:!running}}))
 await page.route('**/mgr/preview_strategy_contracts',async route=>{
  const body=route.request().postDataJSON();f.calls.push({path:'/mgr/preview_strategy_contracts',data:body})
  const instances=body.contracts.map((code,index)=>({id:index?'trend_rb2705':'trend',code,capital_limit:100000,margin_fraction:.4/body.contracts.length,risk_fraction:.02/body.contracts.length}))
  const strategies=instances.map(item=>({...row,id:item.id,contract_group:'trend',params:{...row.params,code:item.code,margin_fraction:item.margin_fraction,risk_fraction:item.risk_fraction}}))
  await route.fulfill({json:{result:0,plan:{...plan,strategies},instances,added:['SHFE.rb.2705'],removed:[]}})
 })
 return f
}

test('contract editor previews equal budgets and keeps edits in the page draft',async({page})=>{
 const f=await contractFixture(page)
 await page.getByRole('tab',{name:'策略管理',exact:true}).click()
 const table=page.locator('.managed-strategies > .el-table')
 await expect(table).toContainText('SHFE.rb.2701')
 await page.getByRole('button',{name:'管理策略合约 trend',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'策略合约与预算'})
 await dialog.locator('.managed-contract-select').click()
 await page.getByRole('option',{name:'螺纹钢 · SHFE.rb.2705',exact:true}).click()
 await expect(page.getByRole('option',{name:'铜 · SHFE.cu.2701',exact:true})).toHaveCount(0)
 await dialog.getByText('策略合约与预算',{exact:true}).click()
 await dialog.getByRole('button',{name:'预览合约与均分预算',exact:true}).click()
 await expect(dialog.locator('.el-table')).toContainText('20,000元')
 await expect(dialog.locator('.el-table')).toContainText('1,000元')
 await dialog.getByRole('button',{name:'加入页面草稿',exact:true}).click()
 await expect(table.locator('.el-table__body tbody tr')).toHaveCount(2)
 expect(f.calls.find(call=>call.path==='/mgr/preview_strategy_contracts').data.contracts).toEqual(['SHFE.rb.2701','SHFE.rb.2705'])
 expect(f.calls.some(call=>/save_strategy_plan|startapp|stopapp|subscriptions\/apply/.test(call.path))).toBe(false)
 expect(f.errors).toEqual([])
})

test('phone: subscription shortcut and running/draft contract labels stay distinct',async({page})=>{
 await page.setViewportSize({width:390,height:844})
 const f=await contractFixture(page,true)
 await page.getByRole('tab',{name:'交易工作台',exact:true}).click()
 await page.getByRole('button',{name:'订阅合约',exact:true}).click()
 await expect(page.getByRole('heading',{name:'实时采集订阅',exact:true})).toBeVisible()
 await expect(page.getByText('请先停止交易引擎，再修改采集订阅。',{exact:true})).toBeVisible()
 await page.getByRole('tab',{name:'策略管理',exact:true}).click()
 const cell=page.locator('.managed-strategies > .el-table .el-table__body tbody tr').first()
 await expect(cell).toContainText('SHFE.rb.2701')
 await expect(cell).toContainText('计划：SHFE.rb.2705')
 await page.getByRole('button',{name:'管理策略合约 trend',exact:true}).click()
 const dialog=page.getByRole('dialog',{name:'策略合约与预算'})
 await expect(dialog.getByRole('button',{name:'预览合约与均分预算',exact:true})).toBeDisabled()
 const bounds=await dialog.boundingBox()
 expect(bounds.x).toBeGreaterThanOrEqual(0)
 expect(bounds.x+bounds.width).toBeLessThanOrEqual(390)
 await dialog.getByRole('button',{name:'关闭',exact:true}).click()
 await page.getByRole('tab',{name:'运行控制',exact:true}).click()
 await expect(page.locator('.runtime-table')).toContainText('SHFE.rb.2701')
 await expect(page.locator('.runtime-table')).toContainText('草稿：SHFE.rb.2705')
 expect(f.calls.some(call=>/preview_strategy_contracts|save_strategy_plan|startapp|stopapp|subscriptions\/apply/.test(call.path))).toBe(false)
 expect(f.errors).toEqual([])
})
