import {test,expect} from '@playwright/test'
import {fixture} from './fixtures/console.js'

async function setup(page,ready=true){
 const f=await fixture(page)
 const calls=[]
 const access={available:ready,state:ready?'ready':'not_connected',reason:ready?'先查数据湖，缺失时采集':'CNEquity 研究环境尚未接入'}
 await page.route('**/mgr/market/**',async route=>{
  const path=new URL(route.request().url()).pathname,body=route.request().postDataJSON();calls.push({path,body})
  let result={}
  if(path.endsWith('/subscriptions'))result={catalog:[{code:'SHFE.rb.2701',subscription_code:'SHFE.rb2701',exchange:'SHFE',product:'rb',name:'螺纹钢'}],products:[],selected:['SHFE.rb2701'],profile_label:'合成测试环境',can_manage:true,process_running:true,datahub_running:true}
  if(path.endsWith('/providers'))result={providers:[{id:'cnequity',label:'CNEquity 数据湖',configured:ready,...access,max_days:366,periods:[['d','日线'],['m1','1 分钟'],['m5','5 分钟']].map(([value,label])=>({value,label,...access}))}],can_backfill:false,can_prepare_lake:ready}
  if(path.endsWith('/coverage'))result={storage:'合成测试目录',message:'WT 与数据湖分别检查',dates:[{date:20260803,status:'missing',rows:0,source:'—',lake_rows:1,lake_status:'partial'}],lake:{rows:1}}
  if(path.endsWith('/jobs'))result={jobs:[{id:'prepared',code:'SHFE.rb.2701',provider:'cnequity',period:'d',start_date:20260803,end_date:20260803,status:'ready',rows:0,message:'数据湖已准备，WT 尚未写入'}]}
  if(path.endsWith('/backfill'))result={task:{id:'new',status:'queued'},message:'数据湖准备任务已提交'}
  await route.fulfill({json:{result:0,...result}})
 })
 await page.getByRole('tab',{name:'行情数据',exact:true}).click()
 return {f,calls}
}

test('lake readiness is separate from coverage and live trading only prepares the lake',async({page})=>{
 const {f,calls}=await setup(page)
 await expect(page.getByText('研究环境已接入',{exact:true})).toBeVisible()
 await expect(page.getByRole('button',{name:'准备到数据湖',exact:true})).toBeEnabled()
 await page.getByRole('button',{name:'检查 WT 与数据湖',exact:true}).click()
 await expect(page.getByText('数据湖记录数',{exact:true})).toBeVisible()
 await expect(page.getByText('部分数据',{exact:true})).toBeVisible()
 await expect(page.getByRole('button',{name:'继续导入 WT',exact:true})).toBeDisabled()
 await page.getByRole('button',{name:'准备到数据湖',exact:true}).click()
 expect(calls.find(item=>item.path.endsWith('/backfill')).body.provider).toBe('cnequity')
 expect(f.calls.some(item=>/stopapp|startapp/.test(item.path))).toBe(false)
 expect(f.errors).toEqual([])
})

test('phone distinguishes an unconnected environment from authorization',async({page})=>{
 await page.setViewportSize({width:390,height:844})
 const {f}=await setup(page,false)
 await page.locator('.el-select').filter({has:page.getByRole('combobox',{name:'补数数据频率',exact:true})}).locator('.el-select__wrapper').click()
 await expect(page.getByRole('option',{name:'1 分钟 · 未接入',exact:true})).toBeVisible()
 await expect(page.getByText('未授权',{exact:false})).toHaveCount(0)
 await expect(page.getByRole('button',{name:'准备到数据湖',exact:true})).toBeDisabled()
 expect(f.errors).toEqual([])
})
