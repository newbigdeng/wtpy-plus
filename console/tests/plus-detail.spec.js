import {test,expect} from '@playwright/test'
import {fixture} from './fixtures/console.js'

test('Plus details draw bounded equity, paginate native funds and defer logs',async({page})=>{
 const f=await fixture(page),calls=[]
 const task={id:'a'.repeat(32),straid:'example-cta',engine_type:'CTA',code:'SHFE.rb.2701',status:'completed',processed:189732,total:189732,created_at:1792908484,params:{}}
 await page.route('**/bt/**',async route=>{
  const path=new URL(route.request().url()).pathname,body=route.request().postDataJSON()||{}
  calls.push({path,body})
  let value={}
  if(path==='/bt/qrystras')value={strategies:[{id:'example-cta',name:'Example CTA',strategy_type:'cta'}]}
  if(path==='/bt/qrycode')value={content:'from wtpy import BaseCtaStrategy\nclass Example(BaseCtaStrategy): pass'}
  if(path==='/bt/qrystrabts')value={backtests:[]}
  if(path==='/bt/plus/capabilities')value={available:true,engines:['CTA','HFT'],datasets:[],executors:[],limitations:[]}
  if(path==='/bt/plus/list')value={tasks:[task]}
  if(path==='/bt/plus/detail')value={task,orders:[],trades:[],positions:[],funds:[],events:[],rejections:[],risk:{events:[]},summary:{equity:10000},
   ledger_totals:{orders:0,trades:0,positions:0,funds:401},equity_curve:[['2026-10-19 09:00',10000],['2026-10-19 09:01',10010],['2026-10-19 09:02',10000]],
   ledger_page:body.ledger?{id:body.ledger,total:body.ledger==='funds'?401:0,rows:body.ledger==='funds'?[{equity:10000+(body.offset||0)}]:[]}:null,
   diagnostics:{message:'没有委托记录，先检查实际参数。'},log_tail:body.include_log?'native simulated log tail':''}
  await route.fulfill({json:{result:0,...value}})
 })
 await page.getByRole('link',{name:'在线回测',exact:true}).click()
 await page.getByText('Example CTA',{exact:true}).click()
 await page.getByRole('button',{name:/^Plus 回测/}).click()
 await page.locator('.plus-tasks').getByRole('button',{name:'详情',exact:true}).click()
 await expect(page.locator('.plus-proof')).toContainText('委托 0')
 await expect(page.getByLabel('Plus模拟权益曲线').locator('canvas')).toBeVisible()
 expect(calls.filter(x=>x.path==='/bt/plus/detail').every(x=>x.body.limit===200&&!x.body.include_log)).toBe(true)
 await page.getByRole('tab',{name:'资金',exact:true}).click()
 await expect(page.locator('.plus-ledger')).toContainText('10000')
 await page.locator('.plus-detail .btn-next').click()
 await expect(page.locator('.plus-ledger')).toContainText('10200')
 expect(calls.some(x=>x.path==='/bt/plus/detail'&&x.body.ledger==='funds'&&x.body.offset===200)).toBe(true)
 await page.getByRole('tab',{name:'运行日志',exact:true}).click()
 await expect(page.locator('.plus-detail .plus-log:visible').last()).toHaveText('native simulated log tail')
 expect(calls.at(-1).body.include_log).toBe(true)
 expect(f.errors).toEqual([])
})
