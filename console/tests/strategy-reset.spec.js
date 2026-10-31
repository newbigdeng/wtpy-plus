import {test,expect} from '@playwright/test'
import {fixture} from './fixtures/console.js'

for(const width of [1600,390]){
 test(`strategy reset ${width}px: confirmation, progress and failure remain visible`,async({page})=>{
  await page.setViewportSize({width,height:1000})
  const f=await fixture(page),strategies=[{id:'example',name:'示例策略',class_name:'ExampleCta',engine_type:'CTA',trader:'example',enabled:true,params:{code:'SHFE.rb.2701'}},{id:'legacy',name:'旧控制桥示例',class_name:'ExampleCta',engine_type:'CTA',trader:'legacy',enabled:true,params:{code:'SHFE.rb.2705'}}]
  const status={example:{id:'example',reset_supported:true,reset_reason:'',applied_contracts:['SHFE.rb.2701']},legacy:{id:'legacy',reset_supported:false,reset_reason:'控制桥需要更新',applied_contracts:['SHFE.rb.2705']}}
  let task=null,polls=0,submissions=0
  await page.route('**/mgr/strategy_catalog',route=>route.fulfill({json:{result:0,channels:[],executors:[]}}))
  await page.route('**/mgr/strategy_library',route=>route.fulfill({json:{result:0,strategies:[]}}))
  await page.route('**/mgr/strategy_plan',route=>route.fulfill({json:{result:0,can_manage:true,managed:true,has_applied:true,running:true,strategy_status:status,plan:{engine_type:'CTA',strategies,trading_enabled:true,risk:{rules:[]}}}}))
  await page.route('**/mgr/reset_strategy',route=>{
   const body=route.request().postDataJSON();f.calls.push({path:'/mgr/reset_strategy',data:body});submissions++;polls=0
   task={id:'reset-'+submissions,strategyid:'example',status:submissions===1?'queued':'failed',step:submissions===1?'pausing':'failed',message:submissions===1?'开始暂停策略':'策略保持暂停',error:submissions===1?'':submissions===2?'柜台拒绝退仓，当前状态未清理':'策略重建失败',updated_at:1792675620,...(submissions>2?{native_completed:true,archive_path:'archive/example-reset'}:{})}
   return route.fulfill({json:{result:0,task}})
  })
  await page.route('**/mgr/reset_strategy_status',route=>{
   if(task?.status==='queued'||task?.status==='running'){
    polls++;task=polls===1?{...task,status:'running',step:'flattening',message:'等待柜台退仓回执'}:{...task,status:'completed',step:'completed',message:'柜台空仓已确认，旧记录已归档'}
   }
   return route.fulfill({json:{result:0,task}})
  })
  await page.getByRole('tab',{name:'策略管理',exact:true}).click()
  const button=page.getByRole('button',{name:'重置策略 example',exact:true})
  const unsupported=page.getByRole('button',{name:'重置策略 legacy',exact:true})
  await expect(unsupported).toBeDisabled()
  await expect(unsupported).toHaveAttribute('title','控制桥需要更新')
  await button.click()
  const dialog=page.getByRole('dialog',{name:'重置策略：示例策略',exact:true})
  await expect(dialog).toContainText('先柜台平仓，确认后清空当前信号与理论持仓')
  await expect(dialog).toContainText('实际成交与盈亏保留')
  await dialog.getByRole('button',{name:'关闭',exact:true}).click()
  expect(submissions).toBe(0)
  await button.click()
  await dialog.getByRole('button',{name:'确认重置',exact:true}).click()
  await expect(dialog.getByRole('status',{name:'策略重置进度'})).toContainText('柜台退仓')
  await expect(dialog.getByRole('button',{name:'确认重置',exact:true})).toBeDisabled()
  await expect(dialog.getByRole('status',{name:'策略重置进度'})).toContainText('重置完成')
  await expect(dialog.getByRole('status',{name:'策略重置进度'})).toContainText(/2026.*10.*22/)
  expect(f.calls.find(call=>call.path==='/mgr/reset_strategy').data).toEqual({groupid:'acceptance01',strategyid:'example',confirmed:true})
  await dialog.getByRole('button',{name:'确认重置',exact:true}).click()
  await expect(dialog.getByRole('status',{name:'策略重置进度'})).toContainText('重置失败')
  await expect(dialog.getByRole('alert')).toContainText('柜台拒绝退仓，当前状态未清理')
  await expect(dialog).toContainText('不自动恢复交易')
  await dialog.getByRole('button',{name:'确认重置',exact:true}).click()
  await expect(dialog).toContainText('原生清理已执行，策略重建未完成')
  await expect(dialog.getByRole('status',{name:'策略重置进度'})).toContainText('旧记录已归档')
  const bounds=await dialog.boundingBox();expect(bounds.x).toBeGreaterThanOrEqual(0);expect(bounds.x+bounds.width).toBeLessThanOrEqual(width)
  await dialog.getByRole('button',{name:'关闭',exact:true}).click()
  await expect(page.locator('.managed-reset-status').first()).toContainText('重置失败')
  expect(f.errors).toEqual([])
  expect(f.calls.some(call=>/stopapp|startapp|cmtgrpfile|save_strategy_plan/.test(call.path))).toBe(false)
 })
}
