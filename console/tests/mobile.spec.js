import {test, expect} from '@playwright/test'
import {fixture} from './fixtures/console.js'

async function fitsScreen(page, selector) {
  const bounds = await page.locator(selector).evaluate(element => {
    const box = element.getBoundingClientRect()
    return {left: box.left, right: box.right, width: window.innerWidth}
  })
  expect(bounds.left).toBeGreaterThanOrEqual(-1)
  expect(bounds.right).toBeLessThanOrEqual(bounds.width + 1)
}

for (const width of [360, 390]) {
  test(`phone ${width}px: group drawer, chart controls and ledger stay usable`, async ({page}) => {
    await page.setViewportSize({width, height: 844})
    const f = await fixture(page)
    await expect(page.getByRole('button', {name: '验收组合二', exact: true})).toBeHidden()
    await page.getByRole('button', {name: '展开组合栏', exact: true}).click()
    await page.getByRole('button', {name: '验收组合二', exact: true}).click()
    await expect(page.locator('.wt-group-title h1')).toHaveText('验收组合二')
    await expect(page.getByRole('button', {name: '验收组合二', exact: true})).toBeHidden()

    const bars = Array.from({length: 100}, (_, index) => ({
      time: 202610210900 + index, open: 3300 + index,
      close: 3302 + index, high: 3305 + index, low: 3298 + index, volume: 100 + index
    }))
    await page.route('**/mgr/workbench', route => route.fulfill({json: {
      result: 0, market: {code: 'SHFE.rb.2701', bars, contracts: [{code: 'SHFE.rb.2701', name: '螺纹钢', available: true}],
        contract: {code: 'SHFE.rb.2701', name: '螺纹钢', price_tick: 1, volume_scale: 10}},
      positions: [{code: 'SHFE.rb.2701', long: true, volume: 3, cost: 3310, profit: 360}],
      orders: [{time: '2026-10-21 09:30:00', code: 'SHFE.rb.2701', action: '开多', price: 3310, total: 3, traded: 3}]
    }}))
    await page.getByRole('tab', {name: '交易工作台', exact: true}).click()
    await expect(page.locator('.market-chart canvas')).toBeVisible()
    for (const selector of ['.wt-topbar', '.wt-rail', '.terminal-cockpit', '.terminal-toolbar', '.terminal-bottom', '.terminal-ledger']) {
      await fitsScreen(page, selector)
    }
    expect(await page.locator('.terminal-cockpit').evaluate(element => getComputedStyle(element).gridTemplateColumns.split(' ').length)).toBe(2)
    const equity = await page.locator('.terminal-equity').boundingBox()
    const ledger = await page.locator('.terminal-ledger').boundingBox()
    expect(ledger.y).toBeGreaterThanOrEqual(equity.y + equity.height - 1)

    await page.getByRole('button', {name: '设置技术指标参数'}).click()
    await fitsScreen(page, '.el-popover:visible')
    await expect(page.getByRole('spinbutton', {name: 'MACD快线周期', exact: true})).toHaveValue('12')
    await page.getByRole('textbox', {name: 'MA周期', exact: true}).fill('5,10,20')
    await page.getByRole('button', {name: '应用', exact: true}).click()
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem('wt.terminal.indicators')).ma)).toBe('5,10,20')
    await page.locator('.terminal-ledger').getByRole('tab', {name: '委托', exact: true}).click()
    await expect(page.locator('.terminal-ledger .el-table__body')).toContainText('SHFE.rb.2701')
    const scroll = page.locator('.terminal-ledger .el-table__body-wrapper .el-scrollbar__wrap')
    await scroll.evaluate(element => {element.scrollLeft = element.scrollWidth})
    expect(await scroll.evaluate(element => element.scrollLeft)).toBeGreaterThan(0)

    await page.setViewportSize({width: 1600, height: 1000})
    await expect(page.locator('.wt-rail')).toHaveCSS('position', 'static')
    await expect(page.locator('.terminal-cockpit')).toHaveCSS('grid-template-columns', /(?:\S+ ){6}\S+/)
    expect(f.errors).toEqual([])
    expect(f.calls.some(call => /start|stop|trade|order|cmt/.test(call.path))).toBe(false)
  })
}

test('phone research drawer selects a strategy and bottom navigation changes pages', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844})
  const f = await fixture(page)
  await page.route('**/bt/**', route => {
    const path = new URL(route.request().url()).pathname
    const response = path === '/bt/qrystras' ? {strategies: [{id: 'trend', name: '趋势研究', strategy_type: 'cta'}]} :
      path === '/bt/qrycode' ? {content: '# phone research example'} : {backtests: [], types: [], datasets: [], engines: [], executors: [], limitations: []}
    return route.fulfill({json: {result: 0, ...response}})
  })
  await page.getByRole('link', {name: '在线回测', exact: true}).click()
  await expect(page.getByRole('complementary', {name: '回测策略列表'})).toBeHidden()
  await page.getByRole('button', {name: '选择回测策略', exact: true}).click()
  await page.locator('.stra-item').filter({hasText: '趋势研究'}).click()
  await expect(page.getByRole('complementary', {name: '回测策略列表'})).toBeHidden()
  await expect(page.locator('.wt-research-top h1')).toContainText('趋势研究')
  await expect(page.locator('.cm-content')).toContainText('# phone research example')
  await fitsScreen(page, '.wt-research-content')
  await page.getByRole('link', {name: '历史数据', exact: true}).click()
  await expect(page.locator('.history-manager h1')).toHaveText('历史数据管理')
  await fitsScreen(page, '.history-filters')
  await page.getByRole('button', {name: '从外部导入', exact: true}).click()
  await fitsScreen(page, '.el-dialog:visible')
  expect(f.errors).toEqual([])
})
