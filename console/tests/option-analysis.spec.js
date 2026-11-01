import {test, expect} from '@playwright/test'

async function login(page) {
  await page.route('**/mgr/session', r=>r.fulfill({json:{result:0,userinfo:{loginid:'tester',role:'admin'}}}))
  await page.routeWebSocket(/.*/,s=>s.onMessage(()=>{}))
}

test('analysis subscribes only checked contracts and the selected mode',async({page})=>{
  await login(page)
  const rows=[3000,3100].map(strike=>({symbol:'DCE.m2701-C-'+strike,side:'看涨',strike,expiry:20261216}))
  await page.route('**/mgr/assets/option/chain',r=>r.fulfill({json:{result:0,source:'测试合约表',contracts:rows}}))
  let body
  await page.route('**/mgr/assets/option/analysis',r=>{
    body=r.request().postDataJSON()
    return r.fulfill({contentType:'application/x-ndjson',body:JSON.stringify({error:'测试会话结束'})+'\n'})
  })
  await page.goto('/#/monitor?asset=option')
  await page.getByLabel('行情环境').selectOption('simulation')
  await page.getByPlaceholder('例如 DCE.m2701').fill('DCE.m2701')
  await page.getByRole('button',{name:'查看合约链'}).click()
  await page.getByLabel('分析 DCE.m2701-C-3000',{exact:true}).check()
  await page.getByRole('button',{name:'开始分析'}).click()
  await expect.poll(()=>body?.chains).toEqual([{underlying:'DCE.m2701',symbols:[rows[0].symbol]}])
  expect(body.mode).toBe('simulation')
  await expect(page.getByText('测试会话结束')).toBeVisible()
})

for(const theme of ['dark','light']){
  test('the W+ mark is monochrome in '+theme,async({page})=>{
    await login(page)
    await page.addInitScript(t=>localStorage.setItem('wt.workspace.theme',t),theme)
    await page.goto('/#/monitor?asset=option')
    const mark=page.locator('.wt-brand img')
    await expect(mark).toBeVisible()
    const src=await mark.getAttribute('src')
    const colors=await page.evaluate(async src=>{
      const svg=await (await fetch(src)).text()
      return [...svg.matchAll(/(?:fill|stroke)="(#[0-9a-fA-F]+)"/g)].map(m=>m[1])
    },src)
    expect(new Set(colors)).toEqual(new Set(['#000','#fff']))
    expect(await page.locator('link[rel="icon"]').getAttribute('href')).toBeTruthy()
    await page.goto('/#/login')
    await expect(page.locator('.wt-login-mark')).toHaveAttribute('src',src)
  })
}
