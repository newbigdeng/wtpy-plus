import {expect} from '@playwright/test'
export async function fixture(page) {
  const errors=[], calls=[]; let socket
  page.on('pageerror',error=>errors.push(error.message))
  const groups=[{id:'acceptance01',name:'验收组合一',datmod:'auto',gtype:'cta',running:false},{id:'acceptance02',name:'验收组合二',datmod:'auto',gtype:'cta',running:false}]
  await page.route('**/mgr/**',async route=>{
    const path=new URL(route.request().url()).pathname, data=route.request().postDataJSON(); calls.push({path,data})
    const responses={
      '/mgr/session':{userinfo:{loginid:'localadmin',name:'本地验收用户',role:'superman'}},
      '/mgr/login':{userinfo:{loginid:'localadmin',name:'本地验收用户',role:'superman'}},
      '/mgr/qrygrp':{groups},'/mgr/qrystras':{strategies:[]},'/mgr/qrychnls':{channels:[]},
      '/mgr/qrypos':{positions:[]},'/mgr/qryportpos':{positions:[]},'/mgr/qryportperfs':{performance:{}},'/mgr/qryportfilters':{filters:{strategy_filters:{},code_filters:{},channel_filters:{}}},
      '/mgr/qrygrpdir':{tree:{label:'root',children:[{label:'config.json',path:'config.json',isfile:true}]}},
      '/mgr/qrygrpfile':{content:'{"enabled":true}'},'/mgr/cmtgrpfile':{},
      '/mgr/qrylogs':{content:'历史日志\n',lines:1},'/mgr/qryusers':{users:[{loginid:'localadmin',name:'本地验收用户',role:'superman'}]},
      '/mgr/qrymons':{schedules:{}},'/mgr/qrymonlog':{content:'',lines:0},'/mgr/qryacts':{actions:[]},
      '/mgr/qryportrnd':{rounds:[{code:'SHFE.rb',profit:123.4}]},'/mgr/qryporttrd':{trades:Array.from({length:205},(_,index)=>({code:'SHFE.rb',price:3000+index,volume:1}))}
    }
    await route.fulfill({json:{result:0,message:'OK',...(responses[path]||{})}})
  })
  await page.routeWebSocket(/\/ws(?:[/?]|$)/,ws=>{socket=ws;ws.onMessage(text=>{const message=JSON.parse(text);if(['subscribe','heartbeat'].includes(message.type))ws.send(JSON.stringify(message))})})
  await page.route('**/bt/**',route=>route.fulfill({json:{result:0,message:'OK',strategies:[]}}))
  await page.goto('/#/login')
  await page.getByPlaceholder('用户名').fill('localadmin')
  await page.getByPlaceholder('密码',{exact:true}).fill('test-only-local-password')
  await page.getByRole('button',{name:/登\s*录/}).click()
  await expect(page.getByRole('button',{name:'验收组合一',exact:true})).toBeVisible()
  await expect(page.locator('.wt-topbar .wt-push-status')).toContainText('推送已连接')
  await page.getByRole('tab',{name:'数据',exact:true}).click()
  await page.getByRole('button',{name:'运行日志',exact:false}).click()
  return {errors,calls,send:message=>socket.send(JSON.stringify(message))}
}

