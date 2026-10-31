import {test,expect} from '@playwright/test'
import {fixture} from './fixtures/console.js'

test('initial chart and live quote do not wait for the ledger response',async({page})=>{
 const f=await fixture(page)
 let release,quoteCalls=0
 const ledger=new Promise(resolve=>{release=resolve})
 const nowQuote=price=>{const now=new Date(Date.now()+8*3600000);return {price,pre_close:3000,action_date:Number(now.toISOString().slice(0,10).replaceAll('-','')),action_time:Number(now.toISOString().slice(11,23).replaceAll(':','').replace('.','')),quote_basis:'tick'}}
 const market=()=>({code:'SHFE.rb.2701',period:'m1',session_open:true,contract:{name:'螺纹钢',price_tick:1,volume_scale:10},contracts:[],bars:[{time:202610270930,open:3000,high:3010,low:2990,close:3001,volume:10}],quote:nowQuote(3001)})
 await page.route('**/mgr/workbench',async route=>{
  const payload=route.request().postDataJSON()
  if(payload.quote_only){quoteCalls++;if(quoteCalls===1)await new Promise(resolve=>setTimeout(resolve,5500));return route.fulfill({json:{result:0,market:{...market(),quote:nowQuote(3002)}}}).catch(()=>{})}
  if(payload.market_only)return route.fulfill({json:{result:0,market:market()}})
  const old=market();await ledger
  await route.fulfill({json:{result:0,market:old,positions:[],orders:[],trades:[],accounts:[]}})
 })
 try{
  await page.getByRole('tab',{name:'交易工作台',exact:true}).click()
  await expect(page.locator('.quote-main')).toContainText('3,001')
  await expect(page.locator('.market-chart canvas')).toBeVisible()
  await expect(page.locator('.quote-main')).toContainText('3,002',{timeout:10000})
  expect(quoteCalls).toBeGreaterThan(1)
  release()
  await expect(page.getByRole('button',{name:'刷新行情',exact:true})).toBeEnabled()
  await expect(page.locator('.quote-main')).toContainText('3,002')
  expect(f.errors).toEqual([])
 }finally{release()}
})
