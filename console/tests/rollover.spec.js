import {test,expect} from '@playwright/test'
import {fixture} from './fixtures/console.js'

for(const width of [1600,390])test(`rollover reminder ${width}px: missing market and observed successor`,async({page})=>{
 await page.setViewportSize({width,height:1000})
 const f=await fixture(page)
 let candidate=null
 await page.route('**/mgr/workbench',route=>route.fulfill({json:{result:0,market:{code:'SHFE.rb.2701',bars:[],contract:{code:'SHFE.rb.2701',name:'螺纹钢',exchange:'SHFE'},contracts:[],rollover:{candidate,planned_candidate:{code:'SHFE.rb.2705'},pattern_months:[1,5,10],observation_window:{start:'2026-11-01',end:'2026-12-31'},market_date:20261023,plan_deadline:'2026-12-31',plan_basis:'工作台保守计划：交割月前完成换月',scope:'仅比较本机已采集合约',recommendation:candidate?'已出现远月持仓量超越，建议检查换月。':'2026-11-01起观察换月。'}}}}))
 await page.getByRole('tab',{name:'交易工作台',exact:true}).click()
 const reminder=page.getByLabel('合约换月提醒')
 await expect(reminder).toContainText('下一接续候选SHFE.rb.2705')
 await expect(reminder).toContainText('保守计划完成日2026-12-31')
 await expect(reminder).toContainText('建议观察窗口2026-11-01 ～ 2026-12-31')
 await expect(reminder).toContainText('尚缺远月行情')
 expect(await reminder.evaluate(el=>el.getBoundingClientRect().right<=window.innerWidth)).toBe(true)
 candidate={code:'SHFE.rb.2705'}
 await page.getByRole('button',{name:'刷新行情',exact:true}).click()
 await expect(reminder).toContainText('SHFE.rb.2705')
 await expect(reminder).toContainText('本机远月持仓量首位')
 expect(f.calls.filter(call=>call.path.endsWith('/apply'))).toHaveLength(0)
 expect(f.errors).toEqual([])
})
