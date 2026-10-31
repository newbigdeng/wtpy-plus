import {test,expect} from '@playwright/test'
import {fixture} from './fixtures/console.js'
import {mkdirSync} from 'node:fs'

const code='SHFE.rb.2701',time=Date.parse('2026-10-22T21:27:00+08:00')
const fills=['OL','OS','CNL','CNS'].map((action,index)=>({code,action,time:time+index*60000,price:3300+index,volume:1,channel:'example',tradeid:'fill-'+index,trading_date:20261023}))
const history=[{time:time-3600000,equity:20000},{time:time-60000,equity:20010},{time,equity:8000},{time:time+60000,equity:8005}]

async function chartState(page,selector,name){
 return page.locator(selector).evaluate((element,componentName)=>{
  let component=element.__vueParentComponent
  while(component&&component.type.name!==componentName)component=component.parent
  const proxy=component.proxy,chart=proxy.marketChart||proxy.chart
  return {series:chart.getOption().series.map(series=>({data:series.data,markPoint:series.markPoint})),points:proxy.points,delta:proxy.delta}
 },name)
}

for(const width of [1600,390]){
 test(`terminal ${width}px: real fills, Chinese actions and reset segments`,async({page})=>{
  await page.setViewportSize({width,height:1000})
  const f=await fixture(page)
  let risk={broker_summary:{valid:true,equity:9850,margin:985,currency:'CNY'},baseline:{value:10000},metrics:{day_loss_pct:{valid:true,value:1.5},margin_usage_pct:{valid:true,value:10}}}
  const account={channel:'example',currency:'CNY',equity:8005,updated_at:time+60000,stale:true,history,current_history:history.slice(2),capital_events:[{time,type:'simnow_reset'}],performance:{valid:true,fresh:true,snapshot_at:time,pnl:5,pnl_pct:0.0625,initial_capital:8000,currency:'CNY',scope:'current_capital_phase',message:'从最近一次已确认资金重置起计算'}}
  let accounts=[account],theoretical={valid:true,gross_lots:0,fresh:false,snapshot_at:time,message:'完整持仓账簿快照；未获实时风控确认'}
  await page.route('**/mgr/qryrisk_state',route=>route.fulfill({json:{result:0,snapshot:risk}}))
  await page.route('**/mgr/strategy_plan',route=>route.fulfill({json:{result:0,strategy_status:{example:{id:'example',name:'示例策略',reset_supported:false,reset_reason:'控制桥需要更新'}}}}))
  await page.route('**/mgr/reset_strategy_status',route=>route.fulfill({json:{result:0,task:null}}))
  await page.route('**/mgr/workbench',async route=>{
   const body=route.request().postDataJSON(),daily=body.period==='d'
   const bars=daily?[{time:20261022,date:20261022,open:3300,close:3303,high:3310,low:3290,volume:20},{time:20261023,date:20261023,open:3303,close:3307,high:3315,low:3297,volume:25}]:Array.from({length:8},(_,index)=>({time:202610222126+index,date:20261023,open:3299+index,close:3300+index,low:3297+index,high:3304+index,volume:10+index}))
   await route.fulfill({json:{result:0,market:{code,bars,contracts:[{code,name:'螺纹钢',available:true}],contract:{code,name:'螺纹钢',volume_scale:10,price_tick:1}},theoretical_position_summary:theoretical,positions:[{code,long:true,volume:2,available:1,new_volume:1,previous_volume:1,cost:3310,open_price:null,last_price:3320,profit:null,margin:null,return_on_margin_pct:null,metrics_updated_at:time,market_state:'closed',market_message:'休市，保留行情快照',quote_updated_at:time,strategy_owners:[{id:'example',name:'示例策略'}]},{code:'SHFE.cu.2701',long:false,volume:1,available:1,new_volume:0,previous_volume:1,cost:78000,open_price:77900,last_price:78010,profit:0,margin:5000,return_on_margin_pct:0,metrics_updated_at:time,market_state:'live',market_message:'行情有效',quote_updated_at:time,strategy_owners:[],ownership_message:'未确认归属'}],trades:fills,orders:fills.map(fill=>({...fill,total:1,traded:1})),accounts}})
  })
  await page.getByRole('tab',{name:'交易工作台',exact:true}).click()
  const dayPnl=page.locator('.terminal-cockpit>div').filter({has:page.getByText('今日盈亏',{exact:true})})
  await expect(dayPnl.locator('strong')).toHaveText('-150.00 CNY')
  await expect(dayPnl.locator('strong')).toHaveClass('market-down')
  await expect(dayPnl).toContainText('-1.50% · 柜台 · 当日基准')
  await expect(page.locator('.terminal-cockpit')).not.toContainText('当日亏损')
  const margin=page.locator('.terminal-cockpit>div').filter({has:page.getByText('保证金占用',{exact:true})})
  await expect(margin.locator('strong')).toHaveText('10.00%')
  await expect(margin).toContainText('柜台已用 / 动态权益')
  risk={...risk,broker_summary:{...risk.broker_summary,equity:10120,margin:1012},metrics:{...risk.metrics,day_loss_pct:{valid:true,value:0}}}
  await page.getByRole('button',{name:'刷新行情',exact:true}).click()
  await expect(dayPnl.locator('strong')).toHaveText('+120.00 CNY')
  await expect(dayPnl.locator('strong')).toHaveClass('market-up')
  await expect(dayPnl).toContainText('+1.20% · 柜台 · 当日基准')
  const validRisk=risk
  for(const state of [
   {...validRisk,broker_summary:{...validRisk.broker_summary,valid:false}},
   {...validRisk,metrics:{...validRisk.metrics,day_loss_pct:{valid:false,value:0}}},
   {...validRisk,baseline:{value:0}}
  ]){
   risk=state
   await page.getByRole('button',{name:'刷新行情',exact:true}).click()
   await expect(dayPnl.locator('strong')).toHaveText('—')
   await expect(dayPnl).toContainText('当日基准未确认')
  }
  risk=validRisk
  await page.getByRole('button',{name:'刷新行情',exact:true}).click()
  await expect(dayPnl.locator('strong')).toHaveText('+120.00 CNY')
  await expect(page.locator('.terminal-ledger .el-table__body')).toContainText('未提供')
  const positions=page.locator('.terminal-ledger .el-table__body tbody tr')
  await expect(positions.first()).toContainText('2')
  await expect(positions.first()).not.toContainText('3,310.00')
  await expect(positions.nth(1)).toContainText('77,900.00')
  await expect(positions.nth(1)).not.toContainText('78,000.00')
  await expect(positions.nth(1)).toContainText('+0.00%')
  await expect(positions.first()).toContainText('休市')
  await expect(positions.first()).toContainText('示例策略')
  await expect(positions.first()).toContainText('持仓查询 2026-10-22 21:27:00')
  await expect(page.locator('.terminal-positions .el-table__header')).toContainText('收益率（浮盈/保证金）')
  for(const label of ['持仓','可平','今仓','昨仓','开仓均价','最新价','浮动盈亏','占用保证金','行情状态','归属策略','操作'])await expect(page.locator('.terminal-positions .el-table__header')).toContainText(label)
  const pnlHistory=page.locator('.terminal-cockpit>div').filter({has:page.getByText('历史总盈亏',{exact:true})})
  await expect(pnlHistory.locator('strong')).toHaveText('+5.00 CNY')
  await expect(pnlHistory).toContainText('+0.06% · 柜台 · 本次资金阶段')
  await expect(pnlHistory).toHaveAttribute('title','从最近一次已确认资金重置起计算')
  accounts=[{...account,performance:{...account.performance,fresh:false}}]
  risk={...validRisk,broker_summary:{...validRisk.broker_summary,valid:false}}
  await page.getByRole('button',{name:'刷新行情',exact:true}).click()
  await expect(pnlHistory.locator('strong')).toHaveText('+5.00 CNY')
  await expect(pnlHistory).toContainText('+0.06% · 柜台快照 · 2026-10-22 21:27:00 · 本次资金阶段')
  const historicalSource=pnlHistory.locator('span.cockpit-source-wrap')
  expect(await historicalSource.evaluate(element=>element.scrollWidth<=element.clientWidth+1&&element.scrollHeight<=element.clientHeight+1)).toBe(true)
  if(process.env.WT_TEST_ARTIFACT_DIR){
   mkdirSync(process.env.WT_TEST_ARTIFACT_DIR,{recursive:true})
   await page.locator('.terminal-cockpit').screenshot({path:process.env.WT_TEST_ARTIFACT_DIR+`/historical-snapshot-${width}.png`})
  }
  await expect(dayPnl.locator('strong')).toHaveText('—')
  accounts=[account];risk=validRisk
  await page.getByRole('button',{name:'刷新行情',exact:true}).click()
  await expect(pnlHistory).toContainText('+0.06% · 柜台 · 本次资金阶段')
  const theory=page.locator('.terminal-cockpit>div').filter({has:page.getByText('理论持仓',{exact:true})})
  await expect(theory.locator('strong')).toHaveText('0')
  await expect(theory).toContainText('账簿快照 · 更新时间 2026-10-22 21:27:00')
  await expect(theory).toHaveAttribute('title',/未获实时风控确认/)
  theoretical={...theoretical,gross_lots:3,fresh:true}
  await page.getByRole('button',{name:'刷新行情',exact:true}).click()
  await expect(theory.locator('strong')).toHaveText('3')
  await expect(theory).toContainText('引擎 · 手')
  theoretical={...theoretical,valid:false,gross_lots:0}
  await page.getByRole('button',{name:'刷新行情',exact:true}).click()
  await expect(theory.locator('strong')).toHaveText('—')
  accounts=[{...account,performance:{valid:false,message:'资金基数未确认'}}]
  await page.getByRole('button',{name:'刷新行情',exact:true}).click()
  await expect(pnlHistory.locator('strong')).toHaveText('—')
  accounts=[account,{...account,channel:'other',currency:'USD'}]
  await page.getByRole('button',{name:'刷新行情',exact:true}).click()
  await expect(pnlHistory).toContainText('多账户请分别查看')
  await expect(pnlHistory.locator('strong')).toHaveText('—')
  accounts=[account]
  await page.getByRole('button',{name:'刷新行情',exact:true}).click()
  await expect(pnlHistory.locator('strong')).toHaveText('+5.00 CNY')
  await page.getByRole('button',{name:'重置持仓策略 example',exact:true}).click()
  const reset=page.getByRole('dialog',{name:'重置策略：示例策略',exact:true})
  await expect(reset).toContainText('控制桥需要更新')
  await expect(reset.getByRole('button',{name:'确认重置',exact:true})).toBeDisabled()
  await reset.getByRole('button',{name:'关闭',exact:true}).click()
  await expect(positions.nth(1)).toContainText('0.00')
  await expect(page.locator('.trade-marker-note')).toContainText('真实成交')
  await expect.poll(async()=>((await chartState(page,'.terminal','TradingTerminal')).series[0].markPoint?.data||[]).length).toBe(4)
  const markers=(await chartState(page,'.terminal','TradingTerminal')).series[0].markPoint.data
  expect(markers.map(marker=>marker.name)).toEqual(['开多','开空','平多','平空'])
  expect(markers[0].coord).toEqual([2,3300])
  expect(markers.every(marker=>marker.label.show)).toBe(true)
  await page.locator('.terminal-ledger').getByRole('tab',{name:'成交',exact:true}).click()
  await expect(page.locator('.terminal-ledger .el-table__body')).toContainText('2026-10-22 21:27:00')
  await expect(page.locator('.terminal-ledger .el-table__body')).toContainText('开空')
  await expect(page.locator('.terminal-ledger .el-table__body')).not.toContainText('CNL')
  await page.locator('.terminal-ledger').getByRole('tab',{name:'委托',exact:true}).click()
  await expect(page.locator('.terminal-ledger .el-table__body')).toContainText('平多')
  await expect(page.locator('.terminal-ledger .el-table__body')).not.toContainText('OL')
  await expect(page.locator('.equity-reset')).toContainText('旧记录保留')
  const current=await chartState(page,'.account-equity','AccountEquityChart')
  expect(current.points.map(row=>row.equity)).toEqual([8000,8005])
  expect(current.delta).toBe(5)
  await page.locator('.equity-controls .el-select').filter({has:page.getByRole('combobox',{name:'资金重置记录范围',exact:true})}).locator('.el-select__wrapper').click()
  await page.getByRole('option',{name:'全部原始记录',exact:true}).click()
  await expect.poll(async()=>(await chartState(page,'.account-equity','AccountEquityChart')).series.length).toBe(2)
  const all=await chartState(page,'.account-equity','AccountEquityChart')
  expect(all.series.map(series=>series.data)).toEqual([[20000,20010,null,null],[null,null,8000,8005]])
  expect(all.delta).toBe(null)
  await expect(page.locator('.equity-summary')).not.toContainText('区间变化')
  await page.locator('.el-radio-button').filter({has:page.getByRole('radio',{name:'日线',exact:true})}).locator('.el-radio-button__inner').click()
  await expect.poll(async()=>((await chartState(page,'.terminal','TradingTerminal')).series[0].markPoint?.data||[])[0]?.coord[0]).toBe(1)
  for(const selector of ['.terminal-toolbar','.terminal-bottom','.terminal-ledger']){
   const bounds=await page.locator(selector).boundingBox();expect(bounds.x).toBeGreaterThanOrEqual(-1);expect(bounds.x+bounds.width).toBeLessThanOrEqual(width+1)
  }
  await page.locator('.el-radio-button').filter({has:page.getByRole('radio',{name:'1 分钟',exact:true})}).locator('.el-radio-button__inner').click()
  await expect.poll(async()=>((await chartState(page,'.terminal','TradingTerminal')).series[0].markPoint?.data||[])[0]?.coord[0]).toBe(2)
  await page.locator('.terminal-ledger').getByRole('tab',{name:'持仓',exact:true}).click()
  const accountBounds=await page.locator('.account-equity').boundingBox(),sourceBounds=await page.locator('.account-equity>.equity-source').boundingBox()
  expect(sourceBounds.y+sourceBounds.height).toBeLessThanOrEqual(accountBounds.y+accountBounds.height)
  if(process.env.WT_TEST_ARTIFACT_DIR){
   mkdirSync(process.env.WT_TEST_ARTIFACT_DIR,{recursive:true})
   const scroll=page.locator('.terminal-positions .el-table__body-wrapper .el-scrollbar__wrap')
   await scroll.evaluate(element=>{element.scrollLeft=700})
   expect(await scroll.evaluate(element=>element.scrollLeft)).toBeGreaterThan(0)
   await page.locator('.terminal-ledger').screenshot({path:process.env.WT_TEST_ARTIFACT_DIR+`/positions-${width}.png`})
   await scroll.evaluate(element=>{element.scrollLeft=0})
   await page.locator('.market-chart-panel').screenshot({path:process.env.WT_TEST_ARTIFACT_DIR+`/markers-${width}.png`})
   await page.locator('.account-equity').screenshot({path:process.env.WT_TEST_ARTIFACT_DIR+`/equity-${width}.png`})
   await page.locator('.terminal-cockpit').screenshot({path:process.env.WT_TEST_ARTIFACT_DIR+`/cockpit-${width}.png`})
   await page.screenshot({path:process.env.WT_TEST_ARTIFACT_DIR+`/terminal-${width}.png`,fullPage:true})
  }
  expect(f.errors).toEqual([])
  expect(f.calls.some(call=>/startapp|stopapp|cmt|place_order/.test(call.path))).toBe(false)
 })
}
