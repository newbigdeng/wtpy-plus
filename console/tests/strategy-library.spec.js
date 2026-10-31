import {test,expect} from '@playwright/test'
import {fixture} from './fixtures/console.js'

const entries=Array.from({length:3},(_,i)=>({id:'example_'+i,name:'Synthetic library '+i,version:i+1,engine_type:'CTA',class_name:'Example'}))
const source=id=>'from wtpy import BaseCtaStrategy\nclass Example(BaseCtaStrategy):\n    label = "'+id+'"\n'

test('strategy library opens each requested version in the code editor',async({page})=>{
  const f=await fixture(page)
  await page.route('**/mgr/strategy_library',route=>route.fulfill({json:{result:0,strategies:entries}}))
  await page.route('**/mgr/strategy_library_get',route=>{
    const data=route.request().postDataJSON()
    return route.fulfill({json:{result:0,strategy:{...entries.find(item=>item.id===data.library_id),source:source(data.library_id)}}})
  })
  await page.getByRole('link',{name:'策略管理',exact:true}).click()
  for(const entry of entries){
    await page.getByRole('row').filter({hasText:entry.name}).getByRole('button',{name:'代码与版本'}).click()
    await expect(page.locator('.library-editor .cm-content')).toContainText(entry.id)
    await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click()
  }
  expect(f.errors).toEqual([])
})

test('an unreadable version reports the error and cannot save empty source',async({page})=>{
  await fixture(page)
  await page.route('**/mgr/strategy_library',route=>route.fulfill({json:{result:0,strategies:entries}}))
  await page.route('**/mgr/strategy_library_get',route=>route.fulfill({json:{result:0,strategy:entries[0]}}))
  await page.getByRole('link',{name:'策略管理',exact:true}).click()
  await page.getByRole('button',{name:'代码与版本'}).first().click()
  await expect(page.locator('.library-editor')).toContainText('此版本源码无法读取')
  await expect(page.getByRole('button',{name:'保存新版本'})).toBeDisabled()
})

test('online backtest lists library versions and creates only a chosen research copy',async({page})=>{
  const f=await fixture(page)
  const writes=[],reads=[]
  await page.route('**/mgr/strategy_library',route=>route.fulfill({json:{result:0,strategies:entries}}))
  await page.route('**/mgr/strategy_library_get',route=>{
    const data=route.request().postDataJSON();reads.push(data)
    return route.fulfill({json:{result:0,strategy:{...entries.find(item=>item.id===data.library_id),source:source(data.library_id)}}})
  })
  await page.route('**/bt/**',route=>{
    const path=new URL(route.request().url()).pathname,data=route.request().postDataJSON()
    let value={}
    if(path==='/bt/qrystras')value={strategies:[{id:'old',name:'Existing research',strategy_type:'cta'}]}
    if(path==='/bt/qrytypes')value={types:[{id:'cta',label:'CTA',base_class:'BaseCtaStrategy'}]}
    if(path==='/bt/addstra'){writes.push({path,data});value={strategy:{id:'new',name:data.name,strategy_type:'cta'}}}
    if(path==='/bt/setcode')writes.push({path,data})
    if(path==='/bt/qrycode')value={content:source('example_1')}
    if(path==='/bt/qrystrabts')value={backtests:[]}
    if(path==='/bt/plus/capabilities')value={available:true,engines:['CTA'],datasets:[],executors:[],limitations:[]}
    if(path==='/bt/plus/list')value={tasks:[]}
    return route.fulfill({json:{result:0,...value}})
  })
  await page.getByRole('link',{name:'在线回测',exact:true}).click()
  await expect(page.locator('.wt-library-card')).toHaveCount(3)
  await expect(page.locator('.stra-item')).toHaveCount(1)
  expect(writes).toEqual([])
  await page.locator('.wt-library-card').filter({hasText:'Synthetic library 1'}).click()
  await expect(page.getByRole('dialog')).toContainText('创建副本不代表研究通过')
  await page.getByRole('button',{name:'创建并编写'}).click()
  await expect(page.locator('.wt-research-top h1')).toContainText('Synthetic library 1')
  await expect(page.locator('.wt-editor-frame .cm-content')).toContainText('example_1')
  expect(reads).toEqual([{library_id:'example_1',version:2}])
  expect(writes.map(item=>item.path)).toEqual(['/bt/addstra','/bt/setcode'])
  expect(writes[1].data.content).toContain('example_1 v2')
  expect(f.errors).toEqual([])
})
