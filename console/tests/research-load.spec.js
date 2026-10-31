import {test,expect} from '@playwright/test'

test('project becomes usable while the history catalogue is still loading',async({page})=>{
 let releaseCatalog
 const catalogGate=new Promise(resolve=>{releaseCatalog=resolve}),calls=[]
 await page.routeWebSocket(/\/ws(?:[/?]|$)/,ws=>ws.onMessage(text=>{const message=JSON.parse(text);if(['subscribe','heartbeat'].includes(message.type))ws.send(JSON.stringify(message))}))
 await page.route('**/mgr/**',async route=>{
  const path=new URL(route.request().url()).pathname,body=route.request().postDataJSON()||{}
  calls.push({path,body})
  let data={}
  if(path==='/mgr/session')data={userinfo:{loginid:'research-user',name:'研究员',role:'researcher'}}
  if(path==='/mgr/qrygrp')data={groups:[]}
  if(path==='/mgr/research/list')data={can_edit:true,projects:[{id:'project',title:'首载中的研究项目',kind:'timeseries',version:1,hypothesis:'可以编辑的假设'}],reports:[],experiments:[]}
  if(path==='/mgr/research/catalog'){
   await catalogGate
   data={datasets:[{id:'bars',period:'d',rows:20}],templates:[],lake:{state:'ready',datasets:[],message:'行情目录已就绪'}}
  }
  await route.fulfill({json:{result:0,...data}})
 })
 await page.goto('/#/research')
 await expect(page.getByRole('heading',{name:'首载中的研究项目',exact:true})).toBeVisible()
 await expect(page.getByRole('button',{name:'刷新',exact:true})).not.toHaveClass(/is-loading/)
 await page.getByRole('textbox',{name:'研究假设',exact:true}).fill('目录读取期间仍能填写研究假设')
 expect(calls.find(call=>call.path==='/mgr/research/list').body).toEqual({include_catalog:false})
 await expect.poll(()=>calls.some(call=>call.path==='/mgr/research/catalog')).toBe(true)
 releaseCatalog()
 await page.getByRole('button',{name:'数据与公式',exact:true}).click()
 await expect(page.getByText('行情目录已就绪',{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'资料与假设',exact:true}).click()
 await expect(page.getByRole('textbox',{name:'研究假设',exact:true})).toHaveValue('目录读取期间仍能填写研究假设')
})
