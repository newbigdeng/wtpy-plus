import {test,expect} from '@playwright/test'

test('changing period and observation keeps history separate from the latest quote',async({page})=>{
  const calls=[],code='DCE.m2701-P-3100'
  await page.route('**/mgr/session',r=>r.fulfill({json:{result:0,userinfo:{loginid:'tester',role:'admin'}}}))
  await page.routeWebSocket(/.*/,()=>{})
  await page.route('**/mgr/assets/option/lookup',r=>r.fulfill({json:{result:0,items:[]}}))
  await page.route('**/mgr/assets/option/watch',r=>{
    const body=r.request().postDataJSON();calls.push(body)
    const dates=body.period==='d'?['2026-10-29','2026-10-30']:['2026-10-30 14:59','2026-10-30 15:00']
    const bars=dates.map((day,i)=>({day,open:30,close:30+i,low:29,high:32,volume:10+i,source:'WT 本地'}))
    return r.fulfill({json:{result:0,bars,meta:{symbol:code,side:'看跌',underlying:'DCE.m2701',expiry:20261207},
      valuations:[{day:dates[1],iv:0.2,delta:-0.3,forward:3180}],sources:['WT 本地'],quote:null}})
  })
  let live=0
  await page.route('**/mgr/assets/option/quote',r=>{live++;return r.abort()})
  await page.goto('/#/monitor?asset=option&view=quote')
  await page.getByRole('combobox',{name:'期权合约',exact:true}).fill(code)
  await page.getByRole('button',{name:'查看行情',exact:true}).click()
  await expect(page.getByRole('heading',{name:'波动率与希腊字母'})).toBeVisible()
  await page.getByRole('button',{name:'1 分钟',exact:true}).click()
  await expect(page.getByLabel('查看哪根 K 线')).toHaveValue('2026-10-30 15:00')
  await page.getByLabel('查看哪根 K 线').selectOption('2026-10-30 14:59')
  await expect(page.getByText(/缺少同时刻的标的或估值输入/)).toBeVisible()
  await expect(page.getByText(/没有已记录的盘口快照/)).toBeVisible()
  expect(calls.map(x=>[x.symbol,x.period])).toEqual([[code,'d'],[code,'m1']])
  expect(live).toBe(0)
})
