import {test, expect} from '@playwright/test';
import {fixture} from './fixtures/console.js';

const time = value => Date.parse(value + '+08:00');
function account(segment = 'current') {
  const selected = segment === 'initial' ? 'initial' : 'capital-2';
  return {account_key:'sim:CNY',channel:'sim',currency:'CNY',segments:[
    {id:'initial',start_at:time('2026-10-16T09:00:00')},
    {id:'capital-2',start_at:time('2026-10-20T09:00:00')}
  ],points:selected === 'initial' ? [{time:time('2026-10-16T09:01:00'),equity:10000,segment_id:'initial',pnl:null,return_pct:null}] : [
    {time:time('2026-10-23T15:00:00'),equity:499950,segment_id:'capital-2',pnl:-50,return_pct:-.01},
    {time:time('2026-10-26T14:00:00'),equity:500050,segment_id:'capital-2',pnl:50,return_pct:.01}
  ],daily:selected === 'initial' ? [] : [
    {trading_date:20261022,segment_id:'capital-2',pnl:-50,return_pct:-.01},
    {trading_date:20261023,segment_id:'capital-2',pnl:0,return_pct:0},
    {trading_date:20261026,segment_id:'capital-2',pnl:100,return_pct:.02}
  ],monthly:selected === 'initial' ? [] : [{month:'2026-10',segment_id:'capital-2',pnl:50,return_pct:.01,complete:false}],summary:{selected_segment_id:selected,latest_trading_date:20261026,end_equity:selected === 'initial'?10000:500050,pnl:selected === 'initial'?null:50,return_pct:selected === 'initial'?null:.01,max_drawdown:null},coverage:{complete:false}};
}

test('equity tab renders broker analytics, keeps missing days blank and selects one capital segment', async ({page}) => {
  const {errors} = await fixture(page), requests = [];
  await page.route('**/mgr/account_equity', route => {
    const data = route.request().postDataJSON();requests.push(data);
    return route.fulfill({json:{result:0,accounts:[account(data.segment_id)],read_only:true}});
  });
  await page.getByRole('tab',{name:'账户权益',exact:true}).click();
  await expect(page.locator('.equity-metrics article').nth(1).locator('strong')).toHaveText('50.00');
  await expect(page.locator('.equity-day').filter({has:page.locator('time[datetime="2026-10-25"]')}).locator('span')).toHaveText('—');
  await expect(page.locator('.equity-day').filter({has:page.locator('time[datetime="2026-10-23"]')}).locator('span')).toHaveText('0.00');
  await page.screenshot({path:test.info().outputPath('equity-page.png'),fullPage:true});
  await page.locator('.equity-breakdown').scrollIntoViewIfNeeded();
  await page.screenshot({path:test.info().outputPath('equity-breakdown.png'),fullPage:true});
  await page.getByRole('button',{name:'当日',exact:true}).click();
  await expect.poll(() => requests.at(-1).start_date).toBe('20261026');
  await page.getByText('总权益',{exact:true}).click();
  await expect(page.locator('.curve-card h3')).toHaveText('账户总权益');
  await page.locator('.equity-filters label').nth(1).locator('.el-select').click();
  await page.getByRole('option',{name:/初始阶段/}).click();
  await expect.poll(() => requests.at(-1).segment_id).toBe('initial');
  await expect(page.locator('.equity-metrics article').nth(1).locator('strong')).toHaveText('—');
  await expect(page.locator('.equity-metrics article').nth(2).locator('strong')).toHaveText('10,000.00');
  await expect(page.locator('.equity-main-chart canvas')).toHaveCount(1);
  await page.setViewportSize({width:390,height:844});
  await expect.poll(() => page.locator('.equity-page').evaluate(element => element.scrollWidth <= element.clientWidth + 1)).toBe(true);
  expect(errors).toEqual([]);
});

test('empty account history does not display fabricated zero returns', async ({page}) => {
  await fixture(page);
  await page.route('**/mgr/account_equity', route => route.fulfill({json:{result:0,accounts:[],read_only:true}}));
  await page.getByRole('tab',{name:'账户权益',exact:true}).click();
  await expect(page.locator('.equity-no-account')).toContainText('尚无柜台权益记录');
  await expect(page.locator('.equity-metrics')).toHaveCount(0);
});

test('refreshing a stale account receipt does not claim a new live balance', async ({page}) => {
  await fixture(page);
  const requests=[];
  page.on('request',request=>requests.push(new URL(request.url()).pathname));
  const value=account();
  value.receipt={updated_at:time('2026-10-26T14:00:00'),valid:false,stale:true,reason:'柜台快照过期'};
  await page.route('**/mgr/account_equity',route=>route.fulfill({json:{result:0,accounts:[value]}}));
  await page.getByRole('tab',{name:'账户权益',exact:true}).click();
  await expect(page.locator('.equity-receipt')).toContainText('历史柜台回执 · 柜台快照过期');
  await expect(page.locator('.equity-receipt')).toContainText('14:00:00');
  await page.locator('.equity-heading').getByRole('button',{name:'刷新',exact:true}).click();
  await expect(page.locator('.equity-receipt')).toContainText('历史柜台回执 · 柜台快照过期');
  expect(requests.filter(path=>path==='/mgr/query_trade_funds')).toEqual([]);
});
