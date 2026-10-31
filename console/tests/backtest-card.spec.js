import {test,expect} from '@playwright/test';
import {fixture} from './fixtures/console.js';

test('a completed Plus task refreshes its strategy card without a page reload',async({page})=>{
  const f=await fixture(page);
  let completed=false,queries=0;
  await page.route('**/bt/**',async route=>{
    const path=new URL(route.request().url()).pathname;
    let value={};
    if(path==='/bt/qrystras'){
      queries++;
      value={strategies:[{id:'example',name:'Synthetic card example',strategy_type:'cta',perform:{annual_return:0,max_falldown:0},
        latest_performance:completed?{task_id:'a'.repeat(32),mode:'plus',matches_saved_code:false,created_at:1793000000,metrics:{total_return:-2.32,max_falldown:3.02}}:null}]};
    }
    if(path==='/bt/qrycode')value={content:'from wtpy import BaseCtaStrategy\nclass Example(BaseCtaStrategy): pass'};
    if(path==='/bt/qrystrabts')value={backtests:[]};
    if(path==='/bt/plus/capabilities')value={available:true,engines:['CTA'],datasets:[],executors:[],limitations:[]};
    if(path==='/bt/plus/list')value={tasks:[{id:'a'.repeat(32),straid:'example',status:completed?'completed':'running',created_at:1793000000}]};
    await route.fulfill({json:{result:0,...value}});
  });
  await page.getByRole('link',{name:'在线回测',exact:true}).click();
  const card=page.locator('.stra-item');
  await expect(card).toContainText('暂无已完成结果');
  await expect(card.locator('.wt-stra-metrics')).not.toContainText('0.00%');
  await page.getByText('Synthetic card example',{exact:true}).click();
  await expect.poll(()=>page.locator('.plus-tasks').innerText()).toContain('运行中');
  const before=queries;completed=true;
  await expect(card).toContainText('-2.32%');
  await expect(card).toContainText('3.02%');
  await expect(card).toContainText('历史代码');
  expect(queries).toBeGreaterThan(before);
  expect(f.errors).toEqual([]);
});
