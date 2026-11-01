import {test,expect} from '@playwright/test'

test('switching instrument, asset and page stops previous quote polling',async({page})=>{
  await page.route('**/mgr/session',route=>route.fulfill({json:{result:0,userinfo:{loginid:'tester',name:'Tester',role:'admin'}}}))
  await page.routeWebSocket(/.*/,socket=>socket.onMessage(()=>{}))
  await page.clock.install()
  const calls=[]
  await page.route('**/mgr/assets/**',route=>{
    const action=new URL(route.request().url()).pathname
    const body=route.request().postDataJSON()||{}
    calls.push([action,body.symbol])
    return route.fulfill({json:{result:0,quote:{last:12,source:body.symbol},datasets:[],runs:[],strategies:[]}})
  })
  await page.goto('/#/monitor?asset=stock')
  const input=page.getByPlaceholder('SSE.600000')
  await input.fill('SSE.600000')
  await page.getByRole('button',{name:'查看行情'}).click()
  await expect(page.getByRole('definition').filter({hasText:'SSE.600000'})).toHaveCount(1)
  await input.fill('SZSE.000001'); await page.getByRole('button',{name:'查看行情'}).click()
  await expect(page.getByRole('status').filter({hasText:'SZSE.000001'})).toContainText('已收到快照')
  await page.getByRole('link',{name:'历史数据',exact:true}).click()
  await expect(page.getByRole('heading',{name:'本地历史数据 · 股票'})).toBeVisible()
  await page.clock.fastForward(16000)
  expect(calls.filter(([path])=>path.endsWith('/quote')).map(([,symbol])=>symbol)).toEqual(['SSE.600000','SZSE.000001'])
  await page.getByRole('link',{name:'期权',exact:true}).click()
  await page.getByRole('link',{name:'在线回测',exact:true}).click()
  await expect(page.getByRole('heading',{name:'原生策略回测 · 期权'})).toBeVisible()
  expect(calls.some(([path])=>path==='/mgr/assets/option/history')).toBeTruthy()
  await page.getByRole('link',{name:'策略管理',exact:true}).click()
  await expect.poll(()=>calls.filter(([path])=>path==='/mgr/assets/option/strategy_library').length).toBe(2)
})
