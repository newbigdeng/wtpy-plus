<template>
 <section class="terminal" :class="{'chart-focused':chartFocused}" v-loading="loading && !snapshot">
  <div v-show="!chartFocused" class="terminal-cockpit" aria-label="今日概览"><div v-for="card in cockpit" :key="card.label" :title="card.detail||card.source"><small>{{card.label}}</small><strong :class="card.color">{{card.value}}</strong><span :class="{'cockpit-source-wrap':card.wrapSource}">{{card.source}}</span><el-button v-if="card.label==='账户权益'" size="small" text class="equity-shortcut" @click="$refs.accountCurve.open()">查看曲线</el-button></div></div>
  <div v-if="chartFocused" class="terminal-focus-summary"><span v-for="card in cockpit.filter(c=>['账户权益','今日盈亏','待处理告警'].includes(c.label))" :key="card.label" :title="card.source">{{card.label}} <b :class="card.color">{{card.value}}</b></span><small>{{cockpit[0].source}}</small></div>
  <div ref="toolbar" class="terminal-toolbar">
   <el-select v-model="code" filterable placeholder="选择合约" class="contract-select" aria-label="看盘合约" @change="reload(true)"><el-option v-for="item in catalog" :key="item.code" :value="item.code" :label="item.name+' · '+item.code"><span>{{item.name}}</span><small>{{item.code}}{{item.available?' · 有行情':''}}</small></el-option></el-select>
   <el-button size="small" aria-label="订阅合约" @click="$emit('subscriptions')">订阅合约</el-button>
   <el-radio-group v-model="period" @change="reload(true)" size="small"><el-radio-button value="m1">1 分钟</el-radio-button><el-radio-button value="m5">5 分钟</el-radio-button><el-radio-button value="d">日线</el-radio-button></el-radio-group>
   <div class="terminal-indicators">
    <el-checkbox v-model="showMA" @change="saveIndicators">MA</el-checkbox><el-checkbox v-model="showMacd" @change="saveIndicators">MACD</el-checkbox><el-checkbox v-model="showKdj" @change="saveIndicators">KDJ</el-checkbox><el-checkbox v-model="boll" @change="saveIndicators">BOLL</el-checkbox>
    <el-popover v-model:visible="indicatorEditor" placement="bottom-end" :width="360" trigger="click">
     <template #reference><el-button size="small" aria-label="设置技术指标参数">指标参数</el-button></template>
     <div class="indicator-editor">
      <strong>技术指标参数</strong>
      <label>MA 周期<el-input v-model="indicatorDraft.ma" aria-label="MA周期" placeholder="5,13,50"/></label><small>用逗号分隔，最多 6 条；周期为 1–500 根 K 线。</small>
      <div class="indicator-parameters"><span>MACD</span><label>快线<el-input-number v-model="indicatorDraft.fast" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="MACD快线周期"/></label><label>慢线<el-input-number v-model="indicatorDraft.slow" :min="2" :max="500" :precision="0" controls-position="right" size="small" aria-label="MACD慢线周期"/></label><label>信号<el-input-number v-model="indicatorDraft.signal" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="MACD信号周期"/></label></div>
      <div class="indicator-parameters"><span>KDJ</span><label>窗口<el-input-number v-model="indicatorDraft.kdj" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="KDJ窗口周期"/></label><label>K 平滑<el-input-number v-model="indicatorDraft.k" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="KDJ K平滑周期"/></label><label>D 平滑<el-input-number v-model="indicatorDraft.d" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="KDJ D平滑周期"/></label></div>
      <small>MACD 柱 = 2 × (DIF − DEA)。K、D 初值为 50；窗口高低价相同时 RSV 取 50，J 不截断。</small>
      <span v-if="indicatorError" class="indicator-error" role="alert">{{indicatorError}}</span>
      <div class="indicator-actions"><el-button size="small" @click="resetIndicators">恢复默认</el-button><el-button type="primary" size="small" @click="applyIndicators">应用</el-button></div>
     </div>
    </el-popover>
   </div>
   <el-date-picker v-model="dateRange" type="daterange" value-format="YYYYMMDD" range-separator="至" start-placeholder="行情开始日期" end-placeholder="行情结束日期" aria-label="历史行情日期" class="history-range" @change="reload(true)"/>
   <el-checkbox v-model="autoRefresh" class="terminal-auto" @change="saveRefresh">自动刷新</el-checkbox>
   <el-button :aria-pressed="chartFocused" @click="chartFocused=!chartFocused">{{chartFocused?'完整工作台':'专注图表'}}</el-button>
   <el-button @click="reload(true)" :loading="loading" aria-label="刷新行情">刷新</el-button>
  </div>
  <div v-if="error" class="terminal-error" role="alert">{{error}}；当前保留上次成功读取的快照。</div>
  <div ref="quote" class="terminal-quote" :class="'freshness-'+freshness.state"><span class="freshness-badge" :class="freshness.state" role="status">{{freshness.label}}</span><div class="quote-main"><strong>{{contract?.name || '行情工作台'}}</strong><span :class="priceChange>=0?'market-up':'market-down'">{{number(displayMarket?.quote?.price)}}</span><small :class="priceChange>=0?'market-up':'market-down'">{{priceChange===null?'—':(priceChange>=0?'+':'')+number(priceChange)}}</small></div><div class="quote-details"><span>成交量 <b>{{number(displayMarket?.quote?.volume,0)}}</b></span><span>持仓量 <b>{{number(displayMarket?.quote?.open_interest,0)}}</b></span><span>最小变动 <b>{{contract?.price_tick ?? '—'}}</b></span><span>乘数 <b>{{contract?.volume_scale ?? '—'}}</b></span></div><span class="quote-time" title="这是行情时间，不是页面刷新时间">{{quoteTime}}</span></div>
  <details v-if="rollover" class="terminal-rollover" aria-label="合约换月提醒" :open="rolloverExpanded" @toggle="saveRollover">
   <summary><strong>换月计划</strong><span>{{rollover.planned_candidate?.code || '观察窗口与接续候选'}}</span><small>{{rolloverExpanded?'收起':'展开'}}</small></summary>
   <header><span>{{rollover.recommendation}}</span><el-button size="small" text @click="$emit('subscriptions')">订阅远月比较</el-button></header>
   <div class="rollover-fields">
    <div><span>建议观察窗口</span><b>{{rollover.observation_window?rollover.observation_window.start+' ～ '+rollover.observation_window.end:'按近月流动性变化观察'}}</b><small>提前准备，按持仓、成交和价差决定</small></div>
    <div><span>下一接续候选</span><b>{{rollover.planned_candidate?.code || '待确认'}}</b><small>{{rollover.pattern_months?.length?rollover.pattern_months.join(' / ')+' 月惯例':'尚无常见月份资料'}} · 行情待确认</small></div>
    <div><span>远月行情确认</span><b>{{rollover.candidate?.code || '尚缺远月行情'}}</b><small :title="rollover.scope">{{rollover.candidate?'本机远月持仓量首位':'接续候选尚未经持仓量验证'}} · {{rollover.market_date?'交易日 '+formatDate(rollover.market_date):'无行情日期'}}</small></div>
    <div :title="rollover.plan_basis"><span>保守计划完成日</span><b>{{rollover.plan_deadline || '待核对交易日历与规则'}}</b><small>交易计划提醒，具体期限看柜台</small></div>
   </div>
   <details><summary>查看月份依据与到期规则</summary><p>{{rollover.pattern_basis}}。<a v-if="rollover.pattern_source" :href="rollover.pattern_source" target="_blank" rel="noopener noreferrer">主力月份研究</a></p><p>{{rollover.delivery_note}}<a v-if="rollover.rule_source" :href="rollover.rule_source" target="_blank" rel="noopener noreferrer">交易所品种规则</a><span v-if="rollover.last_trade_date"> · 合约表最后交易日 {{rollover.last_trade_date}}</span></p></details>
  </details>
  <div class="terminal-history" v-if="snapshot?.market?.range">已存行情 {{formatDate(snapshot.market.range.start_date)}} 至 {{formatDate(snapshot.market.range.end_date)}}<span v-if="dateRange?.length"> · 日期按图表时间筛选；清除日期返回最新行情</span><span class="terminal-source" :title="snapshot?.market?.default_selection">默认按已采集合约持仓量选择 · 最后一根可能未完成 · {{compact?'拖动底部滑块缩放':'拖动 / 滚轮缩放'}}</span></div>
  <el-alert v-if="snapshot?.market?.source_warning" :title="snapshot.market.source_warning" type="warning" :closable="false" show-icon/>
  <div class="market-chart-panel" :style="{height:chartHeight+'px',flexBasis:chartHeight+'px'}"><div ref="market" class="market-chart" :style="{height:chartHeight+'px'}"/><div v-if="!bars.length" class="terminal-chart-empty">{{error || snapshot?.market?.market_message || '等待行情数据'}}</div></div>
  <small class="indicator-note">指标基于当前加载的 {{bars.length}} 根 K 线；预热不足留空，最后一根随行情变化。EMA 用首段均值初始化。</small>
  <small class="trade-marker-note">▲ 开多　▼ 开空　● 平仓 · 标记来自通道真实成交。分钟 K 线标注结束时间，悬停标记查看成交时间与价格。{{period==='d'?'夜盘仅在交易日明确时标注。':''}}</small>
  <ResizableDivider v-if="!compact && !chartFocused" v-model="bottomHeight" axis="y" :reverse="true" :min="260" :max="bottomMax" storage-key="wt.terminal.bottom" label="调整看盘下方面板高度"/>
  <div v-show="!chartFocused" ref="bottom" class="terminal-bottom" :style="{flex:'0 0 '+bottomHeight+'px'}">
   <section class="terminal-equity" :class="{'has-capital-reset':snapshot?.accounts?.some(account=>account.capital_events?.length)}" :style="{flex:'0 0 '+equityWidth+'px'}">
    <AccountEquityChart ref="accountCurve" :key="groupid" :accounts="snapshot?.accounts || []" :portfolio="snapshot?.equity || []" :clock="clock"/>
   </section>
   <ResizableDivider v-model="equityWidth" :min="180" :max="equityMax" storage-key="wt.terminal.equity" label="调整权益面板宽度"/>
   <section class="terminal-ledger">
    <el-tabs v-model="ledgerTab"><el-tab-pane label="持仓" name="positions"/><el-tab-pane label="委托" name="orders"/><el-tab-pane label="成交" name="trades"/><el-tab-pane label="执行质量" name="execution"/><el-tab-pane label="账户 / 合约" name="account"/></el-tabs>
    <div class="terminal-table">
     <ExecutionQualityPanel v-if="ledgerTab==='execution'" :quality="snapshot?.execution_quality"/>
     <el-table v-if="ledgerTab==='positions'" class="terminal-positions" :data="snapshot?.positions || []" height="100%" empty-text="通道账本暂无持仓">
      <el-table-column prop="code" label="合约" min-width="130" fixed="left"/>
      <el-table-column label="方向" width="65"><template #default="{row}"><span :class="row.long?'market-up':'market-down'">{{row.long?'多':'空'}}</span></template></el-table-column>
      <el-table-column label="持仓" width="70"><template #default="{row}">{{number(row.volume,0)}}</template></el-table-column>
      <el-table-column label="可平" width="70"><template #default="{row}">{{number(row.available,0)}}</template></el-table-column>
      <el-table-column label="今仓" width="70"><template #default="{row}">{{number(row.new_volume,0)}}</template></el-table-column>
      <el-table-column label="昨仓" width="70"><template #default="{row}">{{number(row.previous_volume,0)}}</template></el-table-column>
      <el-table-column label="开仓均价" width="110"><template #default="{row}"><span :title="positionMetricTitle(row)">{{positionNumber(row.open_price)}}</span></template></el-table-column>
      <el-table-column label="最新价" width="100"><template #default="{row}"><span :title="quoteMetricTitle(row)">{{positionNumber(row.last_price)}}</span></template></el-table-column>
      <el-table-column label="浮动盈亏" width="110"><template #default="{row}"><span :class="pnlColor(row.profit)" :title="positionMetricTitle(row)">{{positionNumber(row.profit)}}</span></template></el-table-column>
      <el-table-column label="收益率（浮盈/保证金）" min-width="165"><template #default="{row}"><span :class="pnlColor(row.return_on_margin_pct)" :title="positionMetricTitle(row)">{{row.return_on_margin_pct==null?'未提供':signedNumber(row.return_on_margin_pct)+'%'}}</span></template></el-table-column>
      <el-table-column label="占用保证金" width="120"><template #default="{row}"><span :title="positionMetricTitle(row)">{{positionNumber(row.margin)}}</span></template></el-table-column>
      <el-table-column label="行情状态" min-width="190"><template #default="{row}"><span :title="quoteMetricTitle(row)">{{marketStateLabel(row.market_state)}}</span><small class="position-detail">{{row.market_message||'等待该合约行情'}}</small><small class="position-detail">行情 {{row.quote_updated_at?tradeTime(row.quote_updated_at):'时间未提供'}}</small><small class="position-detail">持仓查询 {{row.metrics_updated_at?tradeTime(row.metrics_updated_at):'时间未提供'}}</small></template></el-table-column>
      <el-table-column label="归属策略" min-width="170"><template #default="{row}"><span v-if="row.strategy_owners?.length" :title="row.ownership_message"><span v-for="(owner,index) in row.strategy_owners" :key="owner.id">{{index?'、':''}}{{owner.name||owner.id}}</span></span><span v-else :title="row.ownership_message">未确认</span></template></el-table-column>
      <el-table-column label="操作" min-width="130" :fixed="compact?false:'right'"><template #default="{row}"><template v-if="isAdmin&&row.strategy_owners?.length"><el-button v-for="owner in row.strategy_owners" :key="owner.id" link type="warning" :aria-label="'重置持仓策略 '+owner.id" @click="$refs.strategyReset.open(owner)">重置策略</el-button></template><span v-else>{{row.strategy_owners?.length?'仅管理员可操作':'未确认归属'}}</span></template></el-table-column>
     </el-table>
     <el-table v-if="ledgerTab==='orders'" :data="snapshot?.orders || []" height="100%" empty-text="通道账本暂无委托"><el-table-column label="时间（北京）" min-width="160"><template #default="{row}">{{tradeTime(row.time)}}</template></el-table-column><el-table-column prop="code" label="合约" min-width="120"/><el-table-column label="操作" width="75"><template #default="{row}">{{tradeAction(row.action)}}</template></el-table-column><el-table-column prop="price" label="委托价" width="90"/><el-table-column prop="total" label="委托量" width="75"/><el-table-column prop="traded" label="已成交" width="75"/></el-table>
     <el-table v-if="ledgerTab==='trades'" :data="snapshot?.trades || []" height="100%" empty-text="通道账本暂无成交"><el-table-column label="时间（北京）" min-width="160"><template #default="{row}">{{tradeTime(row.time)}}</template></el-table-column><el-table-column prop="code" label="合约" min-width="120"/><el-table-column label="操作" width="75"><template #default="{row}">{{tradeAction(row.action)}}</template></el-table-column><el-table-column prop="price" label="成交价" width="90"/><el-table-column prop="volume" label="数量" width="65"/></el-table>
     <div v-if="ledgerTab==='account'" class="terminal-account">
      <TradeRiskPanel :groupid="groupid" :code="code" :auto-refresh="autoRefresh"/>
       <div class="account-card" v-if="contract"><strong>{{contract.name}} · {{contract.code}}</strong><span>交易所 {{contract.exchange}} · 交易时段 {{contract.session || '—'}}</span><span>最小变动 {{contract.price_tick}} · 合约乘数 {{contract.volume_scale}}</span><span>到期日 {{contract.expire_date || '—'}}</span><small>点击看盘区“订阅合约”，管理数据中台的采集列表。</small></div>
     </div>
    </div>
    <small v-if="ledgerTab==='positions'" class="ledger-position-note">数量、开仓均价、浮盈与保证金取柜台查询回执。收益率 = 浮盈 ÷ 占用保证金。最新价另看行情时间；缺失字段不推算。{{compact?'左右滑动查看全部列。':''}}</small>
   </section>
  </div>
  <StrategyResetPanel v-if="isAdmin" ref="strategyReset" :groupid="groupid" @changed="reload(true)"/>
 </section>
</template>
<script>
import {markRaw} from 'vue'
import ResizableDivider from '../common/ResizableDivider.vue'
import {marketFreshness,mergeMarketQuote} from '@/js/market-freshness.js'
import {displayAccount} from '@/js/account-display.js'
import {movingAverage,calculateMacd,calculateKdj} from '@/js/market-indicators.js'
import {marketTooltip} from '@/js/market-tooltip.js'
import {tradeAction,tradeTime,tradeMarkers} from '@/js/market-trades.js'
import TradeRiskPanel from './TradeRiskPanel.vue'
import AccountEquityChart from './AccountEquityChart.vue'
import StrategyResetPanel from './StrategyResetPanel.vue'
import ExecutionQualityPanel from './ExecutionQualityPanel.vue'
import {useSessionStore} from '@/store.js'
export default{
 name:'TradingTerminal',components:{ResizableDivider,TradeRiskPanel,AccountEquityChart,StrategyResetPanel,ExecutionQualityPanel},props:{groupid:{type:String,required:true}},
 emits:['overview','subscriptions'],
 data:()=>({chartFocused:localStorage.getItem('wt.terminal.chartFocused')==='true',rolloverExpanded:localStorage.getItem('wt.terminal.rollover.expanded')==='1',snapshot:null,riskSnapshot:null,quoteSnapshot:null,quoteLoading:false,quoteSerial:0,clock:Date.now(),nextRefresh:Date.now()+5000,catalog:[],code:'',period:'m1',showMA:true,averages:[5,13,50],showMacd:true,showKdj:true,macdParameters:{fast:12,slow:26,signal:9},kdjParameters:{period:9,k:3,d:3},boll:false,indicatorEditor:false,indicatorDraft:{},indicatorError:'',loading:false,error:'',serial:0,riskSerial:0,autoRefresh:true,dateRange:null,compact:window.innerWidth<=700,bottomMax:440,equityMax:650,bottomHeight:300,equityWidth:360,ledgerTab:'positions',marketChart:null}),
 computed:{
  isAdmin(){return ['admin','superman'].includes(useSessionStore().cache.userinfo?.role)},
  chartExtra(){return this.chartFocused&&!this.compact?160:0},
  chartHeight(){return this.chartExtra+(this.compact?330:380)+(this.compact?110:134)*(Number(this.showMacd)+Number(this.showKdj))},
  displayMarket(){const market=this.snapshot?.market,fast=this.quoteSnapshot;if(!market || fast?.groupid!==this.groupid || fast.market?.code!==market.code || this.dateRange?.length)return market;return mergeMarketQuote(market,fast.market)},
  freshness(){return marketFreshness(this.displayMarket,this.clock)},
  cockpit(){
   const risk=this.riskSnapshot,summary=risk?.broker_summary,metric=risk?.metrics;
   const val=(key,digits=2)=>metric?.[key]?.valid?this.number(metric[key].value,digits):'—';
   const display=displayAccount(risk,this.snapshot),signed=value=>(value>=0?'+':'')+this.number(value);
   const dayValid=display?.daily?.valid,dayPnl=display?.daily?.pnl;
   const receipt=display?.historical?'柜台快照 · '+tradeTime(display.updated_at):'柜台';
   const accounts=this.snapshot?.accounts||[],performance=accounts.length===1?accounts[0].performance:null,theory=this.snapshot?.theoretical_position_summary;
   return [
    {label:'账户权益',wrapSource:true,value:display?this.number(display.equity):'—',source:display?receipt+' · '+(display.currency||''):(summary?.reason||'等待柜台快照')},
    {label:'今日盈亏',wrapSource:true,value:dayValid?signed(dayPnl)+' '+(display.currency||''):'—',source:dayValid?signed(display.daily.pnl_pct)+'% · '+receipt+' · 交易日 '+display.daily.trading_date:display?.historical?receipt+' · 当日基准未确认':'柜台 · 当日基准未确认',color:dayValid?(dayPnl>0?'market-up':dayPnl<0?'market-down':''):''},
    {label:'历史总盈亏',wrapSource:true,value:performance?.valid?signed(performance.pnl)+' '+(performance.currency||accounts[0].currency||''):'—',source:performance?.valid?signed(performance.pnl_pct)+'% · '+(performance.fresh===false?'柜台快照 · '+(performance.snapshot_at?tradeTime(performance.snapshot_at):'时间未知'):'柜台')+' · 本次资金阶段':accounts.length>1?'多账户请分别查看':'柜台 · 本次资金阶段未确认',detail:performance?.message||'从最近一次已确认的资金重置起计算，重置前历史独立保留。无法确认资金基数时不计算。',color:performance?.valid?this.pnlColor(performance.pnl):''},
    {label:'保证金占用',wrapSource:true,value:display?.margin_usage_pct!=null?this.number(display.margin_usage_pct)+'%':'—',source:receipt+' · 已用 / 动态权益'},
    {label:'可用资金',wrapSource:true,value:display?this.number(display.available):'—',source:receipt},
    {label:'理论持仓',value:theory?.valid?this.number(theory.gross_lots,0):'—',source:theory?.valid?(theory.fresh?'引擎 · 手':'账簿快照 · 更新时间 '+(theory.snapshot_at?tradeTime(theory.snapshot_at):'未知')):'等待引擎持仓账簿',detail:(theory?.message||'未读取到完整持仓账簿')+(theory?.snapshot_at?' · 更新时间 '+tradeTime(theory.snapshot_at):'')},
    {label:'成交记录',value:String(this.snapshot?.trades?.length||0),source:'当前通道账簿'},
    {label:'待处理告警',value:risk?String((risk.events||[]).filter(event=>event.kind==='alert'&&event.status==='pending').length):'—',source:'后台预警'}
   ]
  },
  rollover(){return this.snapshot?.market?.rollover},
  bars(){return this.snapshot?.market?.bars || []},contract(){return this.snapshot?.market?.contract},
  priceChange(){const quote=this.displayMarket?.quote;return quote?.price!=null && quote.pre_close>0?quote.price-quote.pre_close:null},
  quoteTime(){const q=this.displayMarket?.quote;if(!q)return this.dateRange?.length?'历史行情 · 无实时Tick':'暂无Tick';const d=String(q.action_date).slice(0,8),t=String(q.action_time).padStart(9,'0');return (q.quote_basis==='closed_bar'?'参考价 · ':'')+d.slice(0,4)+'-'+d.slice(4,6)+'-'+d.slice(6)+' '+t.slice(0,2)+':'+t.slice(2,4)+':'+t.slice(4,6)}
 },
 methods:{
  tradeAction,tradeTime,
  positionNumber(value){return value==null?'未提供':this.number(value)},
  signedNumber(value){return (value>=0?'+':'')+this.number(value)},
  pnlColor(value){return value==null?'':value>0?'market-up':value<0?'market-down':''},
  marketStateLabel(state){return {live:'实时',delayed:'延迟',stale:'已过期',closed:'休市',unknown:'未确认',missing:'暂无行情',history:'历史行情'}[state]||'未确认'},
  quoteMetricTitle(row){return (row.market_message||'行情状态未确认')+(row.quote_updated_at?' · 行情时间 '+tradeTime(row.quote_updated_at):' · 行情时间未提供')},
  positionMetricTitle(row){return (row.metrics_message||'柜台未提供持仓指标')+(row.metrics_updated_at?' · 查询时间 '+tradeTime(row.metrics_updated_at):'')+'。均价与浮盈按柜台回执显示，非逐Tick更新。'},
  formatDate(value){const text=String(value||'');return text.length===8?text.slice(0,4)+'-'+text.slice(4,6)+'-'+text.slice(6):'—'},
  loadRefresh(){this.autoRefresh=localStorage.getItem('wt.terminal.autoRefresh.'+this.groupid)!=='false'},
  saveRollover(event){this.rolloverExpanded=event.currentTarget.open;localStorage.setItem('wt.terminal.rollover.expanded',this.rolloverExpanded?'1':'0')},
  saveRefresh(){localStorage.setItem('wt.terminal.autoRefresh.'+this.groupid,String(this.autoRefresh))},
  fitPanels(){const compact=this.$el.clientWidth<=600;if(compact!==this.compact){this.compact=compact;this.$nextTick(this.paintMarket)}this.bottomMax=520;if(!compact&&!this.chartFocused){this.equityMax=Math.max(180,Math.min(650,(this.$refs.bottom?.clientWidth||0)-325));if(this.equityWidth>this.equityMax)this.equityWidth=this.equityMax}this.resize()},
  async loadRisk(){const serial=++this.riskSerial,groupid=this.groupid;try{const result=await (await fetch('/mgr/qryrisk_state',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupid})})).json();if(serial!==this.riskSerial||groupid!==this.groupid)return;if(result.result===0){this.riskSnapshot=result.snapshot||result;const risk=this.riskSnapshot,fund=risk.broker_summary,lots=risk.metrics?.gross_lots;this.$emit('overview',{groupid,text:(fund?.valid?'权益 '+this.number(fund.equity,0):'柜台快照不可用')+(lots?.valid?' · '+this.number(lots.value,0)+' 手':'')});}}catch{}},
  number(value,digits=2){return value==null || !Number.isFinite(Number(value))?'—':Number(value).toLocaleString('zh-CN',{maximumFractionDigits:digits,minimumFractionDigits:digits})},
  async reload(force=false){if(force)this.loadRisk();if(this.loading && !force)return;const serial=++this.serial;this.loading=true;this.error='';try{
   const response=await fetch('/mgr/workbench',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupid:this.groupid,code:this.code,period:this.period,count:1000,include_catalog:!this.catalog.length,start_date:this.dateRange?.[0]||null,end_date:this.dateRange?.[1]||null})});const result=await response.json();if(result.result<0)throw new Error(result.message);if(serial!==this.serial)return;this.snapshot=result;this.clock=Date.now();if(result.market?.contracts)this.catalog=result.market.contracts;this.code=result.market?.code || this.code;await this.$nextTick();this.paintMarket();this.fitPanels()
  }catch(error){if(serial===this.serial)this.error=error.message}finally{if(serial===this.serial)this.loading=false}},
  async loadMarket(){const serial=this.serial,groupid=this.groupid;try{const result=await (await fetch('/mgr/workbench',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupid,code:this.code,period:this.period,count:600,include_catalog:false,market_only:true})})).json();if(result.result!==0 || serial!==this.serial || groupid!==this.groupid || this.snapshot?.market)return;this.snapshot={market:result.market};this.code=result.market?.code||this.code;await this.$nextTick();this.paintMarket();this.fitPanels()}catch{}},
  async loadQuote(){if(this.quoteLoading || !this.code || this.dateRange?.length)return;const groupid=this.groupid,code=this.code,serial=++this.quoteSerial;this.quoteLoading=true;try{const result=await (await fetch('/mgr/workbench',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},signal:AbortSignal.timeout(4000),body:JSON.stringify({groupid,code,quote_only:true,include_catalog:false,count:20})})).json();if(result.result===0 && serial===this.quoteSerial && groupid===this.groupid && code===this.code && !this.dateRange?.length)this.quoteSnapshot={groupid,market:result.market}}catch{}finally{if(serial===this.quoteSerial)this.quoteLoading=false}},
  indicatorParameters(){return {ma:this.averages.join(','),...this.macdParameters,kdj:this.kdjParameters.period,k:this.kdjParameters.k,d:this.kdjParameters.d}},
  parseIndicatorDraft(draft){
   const pieces=String(draft.ma||'').replace(/，/g,',').split(',').map(value=>value.trim());
   if(!pieces.length || pieces.length>6 || pieces.some(value=>!/^\d+$/.test(value) || Number(value)<1 || Number(value)>500))throw new Error('MA 请填写 1–6 个周期，每个为 1–500 的整数。');
   const values=['fast','slow','signal','kdj','k','d'];
   if(values.some(key=>!Number.isInteger(draft[key]) || draft[key]<1 || draft[key]>500))throw new Error('指标周期须为 1–500 的整数。');
   if(draft.fast>=draft.slow)throw new Error('MACD 快线周期须小于慢线周期。');
   return {averages:[...new Set(pieces.map(Number))],macdParameters:{fast:draft.fast,slow:draft.slow,signal:draft.signal},kdjParameters:{period:draft.kdj,k:draft.k,d:draft.d}}
  },
  editIndicators(){this.indicatorDraft=this.indicatorParameters();this.indicatorError=''},
  resetIndicators(){this.indicatorDraft={ma:'5,13,50',fast:12,slow:26,signal:9,kdj:9,k:3,d:3};this.indicatorError=''},
  applyIndicators(){try{Object.assign(this,this.parseIndicatorDraft(this.indicatorDraft));this.indicatorEditor=false;this.saveIndicators()}catch(error){this.indicatorError=error.message}},
  loadIndicators(){try{const saved=JSON.parse(localStorage.getItem('wt.terminal.indicators'));if(!saved)return;Object.assign(this,this.parseIndicatorDraft(saved));for(const key of ['showMA','showMacd','showKdj','boll'])if(typeof saved[key]==='boolean')this[key]=saved[key]}catch{}},
  saveIndicators(){localStorage.setItem('wt.terminal.indicators',JSON.stringify({...this.indicatorParameters(),showMA:this.showMA,showMacd:this.showMacd,showKdj:this.showKdj,boll:this.boll}));this.$nextTick(()=>{this.paintMarket();this.resize()})},
  css(name){return getComputedStyle(document.documentElement).getPropertyValue(name).trim()},
  paintMarket(){
   if(!this.$refs.market)return;
   if(!this.bars.length){this.tooltipGrids=0;this.marketChart?.clear();return}
   if(!this.marketChart){
    this.marketChart=markRaw(this.$echarts.init(this.$refs.market));
    // Capture before ECharts handles movement so its formatter sees this grid.
    this.tooltipPanel=0;
    this.tooltipPointer=event=>{const rect=this.$refs.market.getBoundingClientRect(),point=[event.clientX-rect.left,event.clientY-rect.top];this.tooltipPanel=-1;for(let gridIndex=0;gridIndex<this.tooltipGrids;gridIndex++)if(this.marketChart.containPixel({gridIndex},point)){this.tooltipPanel=gridIndex;break}};
    this.$refs.market.addEventListener('mousemove',this.tooltipPointer,true)
   }
   const bars=this.bars,closes=bars.map(bar=>bar.close),up=this.css('--wt-up'),down=this.css('--wt-down'),text=this.css('--wt-muted'),grid=this.css('--wt-chart-grid');
   const labels=bars.map(bar=>String(bar.time)),colours=['#bd8a35','#8d7bc7','#4b92be','#aa735b','#5d9b85','#ab659b'];
   const panels=[{name:'K线'+(this.showMA?' · MA('+this.averages.join(',')+')':''),top:24,height:(this.compact?166:226)+this.chartExtra},{name:'成交量',top:(this.compact?220:280)+this.chartExtra,height:this.compact?48:64}];
   const series=[
    {name:'K线',type:'candlestick',data:bars.map(bar=>[bar.open,bar.close,bar.low,bar.high]),itemStyle:{color:up,color0:down,borderColor:up,borderColor0:down}},
    {name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:bars.map(bar=>({value:bar.volume,itemStyle:{color:bar.close>=bar.open?up:down}}))}
   ];
   const line=(name,data,color,axis=0)=>({name,type:'line',data,xAxisIndex:axis,yAxisIndex:axis,showSymbol:false,smooth:false,connectNulls:false,lineStyle:{width:1,color},itemStyle:{color}});
   if(this.showMA)this.averages.forEach((period,index)=>series.push(line('MA'+period,movingAverage(closes,period),colours[index])));
   if(this.boll){
    const middle=movingAverage(closes,20),upper=[],lower=[];
    bars.forEach((bar,index)=>{
     if(index<19){upper.push(null);lower.push(null);return}
     let variance=0;
     for(let cursor=index-19;cursor<=index;cursor++)variance+=(bars[cursor].close-middle[index])**2;
     const width=2*Math.sqrt(variance/20);upper.push(middle[index]+width);lower.push(middle[index]-width)
    });
    for(const [name,values] of [['BOLL上轨',upper],['BOLL中轨',middle],['BOLL下轨',lower]])series.push({...line(name,values,'#6a9ca6'),lineStyle:{width:1,color:'#6a9ca6',type:name==='BOLL中轨'?'dashed':'solid'}})
   }
   if(this.showMacd){
    const {fast,slow,signal}=this.macdParameters,values=calculateMacd(closes,fast,slow,signal),axis=panels.length;
    panels.push({name:'MACD('+[fast,slow,signal].join(',')+') · DIF / DEA / 柱',top:this.chartExtra+(this.compact?302:378)+(this.compact?110:134)*(axis-2),height:this.compact?80:100});
    series.push(line('DIF',values.dif,colours[0],axis),line('DEA',values.dea,colours[1],axis),{name:'MACD柱',type:'bar',xAxisIndex:axis,yAxisIndex:axis,data:values.histogram.map(value=>value===null?null:({value,itemStyle:{color:value>=0?up:down}}))})
   }
   if(this.showKdj){
    const {period,k,d}=this.kdjParameters,values=calculateKdj(bars,period,k,d),axis=panels.length;
    panels.push({name:'KDJ('+[period,k,d].join(',')+') · K / D / J',top:this.chartExtra+(this.compact?302:378)+(this.compact?110:134)*(axis-2),height:this.compact?80:100});
    series.push(line('K',values.k,colours[0],axis),line('D',values.d,colours[1],axis),line('J',values.j,colours[2],axis))
   }
   const markers=tradeMarkers(this.snapshot.trades||[],bars,this.code,this.period,{up,down,background:this.css('--wt-surface')});
   if(markers.length)series[0].markPoint={data:markers,tooltip:{trigger:'item',formatter:({data})=>{
    const trade=data.trade,escape=value=>this.$echarts.format.encodeHTML(String(value??'—'));
    return `${escape(trade.action)} · ${escape(trade.time_label)}<br/>成交价 ${this.number(trade.price)} · ${this.number(trade.volume,0)} 手<br/>通道 ${escape(trade.channel)} · 成交编号 ${escape(trade.tradeid)}`
   }}};
   this.tooltipGrids=panels.length;
   const zoom=this.marketChart.getOption()?.dataZoom?.[0],start=zoom?.start??Math.max(0,100-(this.compact?60:150)/bars.length*100),end=zoom?.end??100,visibleCount=bars.length*(end-start)/100;
   const zoomAxes=panels.map((_,index)=>index);
   this.marketChart.setOption({
    animation:false,backgroundColor:'transparent',
    title:panels.map(panel=>({text:panel.name,left:12,top:panel.top-22,textStyle:{color:text,fontSize:10,fontWeight:'normal'}})),
    tooltip:{trigger:'axis',confine:true,renderMode:'html',axisPointer:{type:'cross'},formatter:parameters=>marketTooltip(parameters,series,bars,this.tooltipPanel),backgroundColor:this.css('--wt-surface'),textStyle:{color:this.css('--wt-text'),fontSize:11},borderColor:grid},
    axisPointer:{link:[{xAxisIndex:'all'}]},
    grid:panels.map(panel=>({left:12,right:70,top:panel.top,height:panel.height})),
    xAxis:panels.map((_,index)=>({
     type:'category',data:labels,gridIndex:index,boundaryGap:true,axisLine:{lineStyle:{color:grid}},axisTick:{show:false},axisPointer:{snap:true},
     axisLabel:{show:index===panels.length-1,interval:Math.max(0,Math.ceil(visibleCount/6)-1),color:text,fontSize:10,hideOverlap:true,formatter:value=>value.length>8?value.slice(8,10)+':'+value.slice(10,12):value.slice(4,6)+'-'+value.slice(6,8)}
    })),
    yAxis:panels.map((_,index)=>({
     type:'value',scale:index!==1,gridIndex:index,position:'right',splitNumber:index===0?5:2,
     axisLabel:{color:text,fontSize:index===0?10:9,formatter:value=>Math.abs(value)>=10000?(value/10000).toFixed(1)+'万':Number(value.toFixed(2))},
     splitLine:{show:index!==1,lineStyle:{color:grid}}
    })),
    dataZoom:this.compact?[{type:'inside',xAxisIndex:zoomAxes,start,end,moveOnMouseMove:false,zoomOnMouseWheel:false},{type:'slider',xAxisIndex:zoomAxes,start,end,height:18,bottom:2,left:12,right:70,showDetail:false,brushSelect:false,borderColor:grid,fillerColor:this.css('--wt-selected'),handleSize:18,textStyle:{color:text}}]:[{type:'inside',xAxisIndex:zoomAxes,start,end}],series
   },true)
  },
  resize(){this.marketChart?.resize()}
 },
 watch:{chartFocused(value){localStorage.setItem('wt.terminal.chartFocused',String(value));this.$nextTick(()=>{this.paintMarket();this.resize()})},indicatorEditor(value){if(value)this.editIndicators()},bottomHeight(){this.$nextTick(this.resize)},equityWidth(){this.$nextTick(this.resize)},groupid(){++this.serial;++this.riskSerial;++this.quoteSerial;this.quoteLoading=false;this.quoteSnapshot=null;this.loading=false;this.catalog=[];this.code='';this.snapshot=null;this.riskSnapshot=null;this.dateRange=null;this.loadRefresh();this.reload();this.loadMarket();this.loadRisk()}},
 mounted(){this.loadRefresh();this.loadIndicators();this.reload();this.loadMarket();this.loadRisk();this.quotePoll=setInterval(()=>{this.clock=Date.now();if(this.autoRefresh && !document.hidden)this.loadQuote()},1000);this.poll=setInterval(()=>{this.clock=Date.now();if(this.autoRefresh && !document.hidden){this.reload();this.loadRisk()}this.nextRefresh=Date.now()+5000},5000);this.observer=new ResizeObserver(()=>{cancelAnimationFrame(this.resizeFrame);this.resizeFrame=requestAnimationFrame(()=>this.fitPanels())});this.observer.observe(this.$el);this.onTheme=()=>this.paintMarket();window.addEventListener('wt-theme-change',this.onTheme)},
 beforeUnmount(){++this.serial;++this.riskSerial;++this.quoteSerial;clearInterval(this.quotePoll);clearInterval(this.poll);cancelAnimationFrame(this.resizeFrame);this.observer?.disconnect();window.removeEventListener('wt-theme-change',this.onTheme);this.$refs.market?.removeEventListener('mousemove',this.tooltipPointer,true);this.marketChart?.dispose()}
}
</script>
<style scoped>
.terminal{height:auto;min-height:860px;display:flex;flex-direction:column;min-width:0;padding-top:4px;overflow:visible;box-sizing:border-box;}
.terminal-rollover>summary{display:flex;align-items:center;gap:10px;list-style:none;outline-offset:4px}.terminal-rollover>summary::-webkit-details-marker{display:none}.terminal-rollover>summary::before{content:'▸';color:var(--wt-muted)}.terminal-rollover[open]>summary::before{content:'▾'}.terminal-rollover>summary>small{margin-left:auto}.terminal-rollover[open]>header{margin-top:10px}
.terminal-rollover{padding:12px;margin-bottom:8px;background:var(--wt-matte);border:1px solid var(--wt-line);border-radius:6px;font-size:12px;line-height:1.6}.terminal-rollover header{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.terminal-rollover header>.el-button{margin-left:auto}.terminal-rollover strong,.terminal-rollover b{font-weight:550}.terminal-rollover span,.terminal-rollover small{color:var(--wt-muted)}.terminal-rollover small{font-size:11px}.rollover-fields{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin-top:10px}.rollover-fields>div{display:flex;flex-direction:column;gap:3px}.terminal-rollover details{margin-top:10px;font-size:11px;color:var(--wt-muted)}.terminal-rollover summary{cursor:pointer}.terminal-rollover details p{margin:6px 0}.terminal-rollover a{color:var(--wt-text);text-decoration:underline}@media(max-width:1100px){.rollover-fields{grid-template-columns:repeat(2,minmax(0,1fr))}}@media(max-width:520px){.rollover-fields{grid-template-columns:1fr}.terminal-rollover header{gap:6px}.terminal-rollover header>span{flex-basis:100%}.terminal-rollover header>.el-button{margin-left:0}}
.terminal-history{display:flex;gap:16px;flex-wrap:wrap;flex:0 0 auto;font-size:10px;color:var(--wt-muted);padding:0 0 6px;}.terminal-toolbar .history-range{flex:0 0 254px;max-width:100%;}.terminal-auto{margin:0;font-size:11px;}
.terminal-error{padding:8px 12px;color:var(--wt-danger);background:var(--wt-matte);font-size:11px;border:1px solid var(--wt-line);border-radius:5px;}
.terminal-toolbar{flex:0 0 auto;display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:4px 0 10px;}.contract-select{width:220px;}
.terminal-toolbar :deep(.el-radio-button__inner){background:var(--wt-matte);color:var(--wt-text);border-color:var(--wt-line);}
.terminal-toolbar :deep(.el-radio-button.is-active .el-radio-button__inner){background:var(--wt-text);color:var(--wt-surface);border-color:var(--wt-text);box-shadow:none;}
.terminal-indicators{display:flex;align-items:center;gap:10px;margin-left:auto;}.terminal-indicators .el-checkbox{margin-right:0;}.terminal-indicators :deep(.el-checkbox__label){font-size:10px;padding-left:4px;}
.indicator-editor{display:flex;flex-direction:column;gap:10px;color:var(--wt-text);font-size:11px}.indicator-editor>strong{font-size:12px;font-weight:550}.indicator-editor>label{display:flex;align-items:center;gap:12px}.indicator-editor>label .el-input{flex:1}.indicator-editor small{color:var(--wt-muted);font-size:10px;line-height:1.6}.indicator-parameters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.indicator-parameters>span{grid-column:1/-1;font-weight:550}.indicator-parameters>label{display:flex;flex-direction:column;gap:5px}.indicator-parameters .el-input-number{width:100%}.indicator-actions{display:flex;justify-content:flex-end;gap:8px}.indicator-error{color:var(--wt-danger);font-size:11px}.indicator-note{padding:0 12px 8px;color:var(--wt-muted);font-size:10px;line-height:1.6}
.position-detail{display:block;color:var(--wt-muted);font-size:10px;line-height:1.5;white-space:normal}
.ledger-position-note{font-size:10px;line-height:1.6;color:var(--wt-muted);padding:6px 0;flex:0 0 auto}
.trade-marker-note{padding:0 12px 8px;color:var(--wt-muted);font-size:10px;line-height:1.6}
.terminal-quote{flex:0 0 auto;display:flex;align-items:center;gap:22px;padding:12px 0;border-top:1px solid var(--wt-line);font-size:11px;flex-wrap:wrap;}.quote-main{display:flex;align-items:baseline;gap:14px;}.quote-main strong{font-size:14px;font-weight:550;}.quote-main>span{font-family:var(--wt-mono);font-size:20px;font-weight:550;}.quote-details{display:flex;gap:18px;color:var(--wt-muted);}.quote-details b{font-weight:400;color:var(--wt-text);padding-left:4px;}.quote-time{margin-left:auto;color:var(--wt-muted);font-family:var(--wt-mono);font-size:10px;}
.quote-stale{font:10px var(--el-font-family);font-weight:400;margin-left:8px;color:var(--wt-warning);padding:2px 5px;border:1px solid var(--wt-line);border-radius:4px;}
.market-up{color:var(--wt-up);}.market-down{color:var(--wt-down);}
.market-chart-panel{position:relative;flex:0 0 360px;min-height:360px;}.market-chart{height:360px;width:100%;}.terminal-source{font-size:10px;color:var(--wt-muted);margin-left:auto;}.terminal-chart-empty{position:absolute;inset:40% 0 auto;text-align:center;font-size:12px;color:var(--wt-muted);}
.terminal-bottom{display:flex;min-height:260px;overflow:hidden;}.terminal-equity{display:flex;flex-direction:column;min-width:0;overflow:hidden;padding:10px 12px 8px 0;}
.terminal-ledger{flex:1;min-width:0;display:flex;flex-direction:column;overflow:hidden;padding-left:12px;}.terminal-ledger>.el-tabs{flex:0 0 40px;}.terminal-ledger :deep(.el-tabs__content){display:none;}.terminal-table{flex:1;min-height:0;overflow:auto;}.terminal-account{display:flex;gap:12px;padding:12px 0;flex-wrap:wrap;}.account-card{display:flex;flex-direction:column;gap:7px;font-size:11px;border:1px solid var(--wt-line);border-radius:7px;padding:12px;min-width:220px;}.account-card strong{font-weight:550;}.account-card b{font-weight:500;font-family:var(--wt-mono);}.account-card small{font-size:10px;color:var(--wt-muted);}
@media(max-width:1100px){.terminal-indicators{margin-left:0;}.terminal-bottom{overflow-x:auto;}.terminal-equity{min-width:180px;}.terminal-ledger{min-width:340px;}.quote-details{gap:10px;}}
.terminal-cockpit{display:grid;grid-template-columns:repeat(8,minmax(110px,1fr));gap:8px;border-bottom:1px solid var(--wt-line);padding:4px 0 14px;margin-bottom:10px}.terminal-cockpit>div{display:flex;flex-direction:column;gap:5px;padding:8px 10px;background:var(--wt-matte);border:1px solid var(--wt-line);border-radius:6px}.terminal-cockpit small{font-size:10px;color:var(--wt-muted)}.terminal-cockpit strong{font:16px var(--wt-mono);white-space:nowrap}.terminal-cockpit span{font-size:9px;color:var(--wt-muted);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.terminal-cockpit span.cockpit-source-wrap{white-space:normal;overflow-wrap:anywhere;line-height:1.4}.freshness-badge{display:inline-flex;padding:4px 6px;border:1px solid currentColor;border-radius:4px;font-size:10px;white-space:nowrap;color:var(--wt-muted)}.freshness-badge.live{color:var(--wt-success)}.freshness-badge.delayed{color:var(--wt-warning)}.freshness-badge.stale{color:var(--wt-danger)}.terminal-quote.freshness-stale{border:1px solid var(--wt-danger);padding:10px}.terminal-cockpit .equity-shortcut{align-self:flex-start;padding:0;height:16px;font-size:10px;margin-top:2px}
@media(max-width:1280px){.terminal-cockpit{grid-template-columns:repeat(4,minmax(100px,1fr))}.quote-time{margin-left:0}.terminal-toolbar{gap:8px}.terminal-history{gap:6px}.terminal-source{margin-left:0}}
@media(max-width:1024px){.terminal-bottom{flex-direction:column;flex-basis:auto!important;min-height:620px;overflow:visible}.terminal-equity{flex:0 0 260px!important;min-height:260px;padding-right:0}.terminal-ledger{flex:0 0 340px;min-height:340px;padding-left:0;border-top:1px solid var(--wt-line)}.terminal-bottom>.wt-resize-divider.x{display:none}.terminal-cockpit{grid-template-columns:repeat(3,minmax(100px,1fr))}.market-chart-panel,.market-chart{min-height:340px;height:340px;flex-basis:340px}.terminal{min-height:1150px}}
</style>
