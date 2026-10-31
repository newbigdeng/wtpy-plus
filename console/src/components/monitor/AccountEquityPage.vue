<template>
  <section class="equity-page" aria-label="账户权益分析">
    <header class="equity-heading">
      <div><h2>账户权益</h2><p>查看柜台资金、已记录收益和每日表现。</p></div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </header>

    <div class="equity-filters">
      <label>账户 / 币种<el-select v-model="accountKey" aria-label="选择权益账户和币种" :disabled="!accounts.length" placeholder="等待柜台账户" @change="changeAccount"><el-option v-for="item in accounts" :key="item.account_key" :value="item.account_key" :label="item.channel+' · '+item.currency"/></el-select></label>
      <label>资金阶段<el-select v-model="segmentId" aria-label="选择资金阶段" :disabled="!account" @change="load"><el-option value="current" label="当前资金阶段"/><el-option v-for="item in previousSegments" :key="item.id" :value="item.id" :label="segmentLabel(item)"/></el-select></label>
      <div class="equity-periods" role="group" aria-label="权益日期范围"><button v-for="item in periods" :key="item.id" :class="{active:range===item.id}" :aria-pressed="range===item.id" @click="chooseRange(item.id)">{{item.label}}</button></div>
      <el-date-picker v-if="range==='custom'" v-model="customDates" type="daterange" value-format="YYYYMMDD" range-separator="至" start-placeholder="开始交易日" end-placeholder="结束交易日" aria-label="自定义权益交易日范围" @change="load"/>
    </div>

    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon/>
    <div v-if="!account&&!loading" class="equity-no-account" role="status">尚无柜台权益记录。收到资金回执后，这里显示真实账户数据。</div>

    <div v-if="account" v-loading="loading" class="equity-analysis">
      <div class="equity-receipt" :class="{'historical':account.receipt?.valid!==true}" role="status">
        <strong>{{receiptLabel}}</strong><span v-if="account.receipt?.updated_at">柜台回执 {{formatTime(account.receipt.updated_at)}}</span>
        <span>刷新只读取已有回执，不发起柜台查询。</span>
      </div>
      <div class="equity-meta"><span>{{selectedSegmentLabel}} · {{account.currency}}</span><span>最新交易日 {{isoDate(anchor)}} · {{points.length}} 个采样点</span><span v-if="summary.last_time">末次采样 {{formatTime(summary.last_time)}}</span></div>
      <div class="equity-metrics">
        <article><span>已记录收益率</span><strong :class="tone(summary.return_pct)">{{percent(summary.return_pct)}}</strong></article>
        <article><span>已记录净损益</span><strong :class="tone(summary.pnl)">{{amount(summary.pnl)}}</strong></article>
        <article><span>区间末权益</span><strong>{{amount(summary.end_equity)}}</strong></article>
        <article><span>采样最大回撤</span><strong>{{amount(summary.max_drawdown)}}</strong><small v-if="Number.isFinite(summary.max_drawdown_pct)">{{percent(summary.max_drawdown_pct)}}</small></article>
      </div>

      <article class="equity-card curve-card">
        <div class="equity-card-heading"><div><h3>{{curveTitle}}</h3><span>{{rangeLabel}}</span></div><el-radio-group v-model="curveMetric" size="small" aria-label="曲线指标"><el-radio-button value="return_pct">收益率</el-radio-button><el-radio-button value="pnl">盈亏金额</el-radio-button><el-radio-button value="equity">总权益</el-radio-button></el-radio-group></div>
        <div class="equity-chart-wrap"><div ref="curve" class="equity-main-chart" aria-label="账户权益趋势图"/><p v-if="!hasCurve" class="equity-chart-empty" role="status">{{points.length?'这些采样还不能计算此项收益，可切换总权益查看。':'所选交易日和资金阶段暂无采样。'}}</p></div>
      </article>

      <div class="equity-breakdown">
        <article class="equity-card">
          <div class="equity-card-heading"><div><h3>月度已记录表现</h3><span>只累计已采到的交易日</span></div><el-radio-group v-model="monthMetric" size="small" aria-label="月度指标"><el-radio-button value="pnl">金额</el-radio-button><el-radio-button value="return_pct">收益率</el-radio-button></el-radio-group></div>
          <div class="equity-chart-wrap"><div ref="monthly" class="equity-month-chart" aria-label="月度收益柱状图"/><p v-if="!hasMonthly" class="equity-chart-empty">所选范围没有可计算的月度记录。</p></div>
        </article>
        <article class="equity-card calendar-card">
          <div class="equity-card-heading"><div><h3>交易日盈亏</h3><span>按柜台交易日归属</span></div><el-radio-group v-model="dayMetric" size="small" aria-label="日历指标"><el-radio-button value="pnl">金额</el-radio-button><el-radio-button value="return_pct">收益率</el-radio-button></el-radio-group></div>
          <div class="equity-calendar-nav"><button :disabled="monthIndex>=months.length-1" aria-label="上个月" @click="moveMonth(1)">‹</button><el-select v-model="calendarMonth" aria-label="日历月份" size="small"><el-option v-for="month in months" :key="month" :value="month" :label="month"/></el-select><button :disabled="monthIndex<=0" aria-label="下个月" @click="moveMonth(-1)">›</button></div>
          <div class="equity-weekdays"><span v-for="day in weekdays" :key="day">{{day}}</span></div>
          <div class="equity-calendar" role="grid" aria-label="每日盈亏日历"><div v-for="cell in cells" :key="cell.key" role="gridcell" class="equity-day" :class="[tone(metricValue(cell.row,dayMetric)),{blank:!cell.day,missing:cell.day&&metricValue(cell.row,dayMetric)===null}]" :title="dayTitle(cell)"><template v-if="cell.day"><time :datetime="isoDate(cell.key)">{{cell.day}}</time><span>{{cell.row?metricText(metricValue(cell.row,dayMetric),dayMetric):'—'}}</span></template></div></div>
          <p class="equity-calendar-note">— 没有可计算记录，未按零盈亏填补。</p>
          <p v-if="account.coverage?.legacy_samples_without_trading_date" class="equity-calendar-note">部分旧采样缺交易日和日初资金。总权益保留原值，日历不能补算。</p>
        </article>
      </div>

      <el-collapse class="equity-method"><el-collapse-item title="收益、资金阶段与采样怎样计算" name="method">
        <p>权益来自柜台回执。净损益使用柜台昨结算权益及出入金数据，手续费不重复扣除。</p>
        <p>收益率仅在资金基准明确时计算。缺字段或现金流无法调整时显示“—”。月度和曲线只汇总已记录的交易日，不代表完整期间业绩。</p>
        <p>更换账户和重置资金后分开统计。页面每次只显示一个资金阶段，资金变化不当作交易利润。</p>
        <p>夜盘按柜台交易日归属。仅有时间和权益的旧采样可画总权益，不补造每日盈亏。近三月包含当前月和此前两个月。</p>
        <p>回撤来自已记录净损益的高点回落。采样之间的更深回撤可能没有记录。此页只读历史，不触发柜台查询或交易。</p>
        <p v-if="account.coverage?.legacy_samples_without_trading_date">此账户有 {{account.coverage.legacy_samples_without_trading_date}} 个旧采样缺少交易日，仅保留总权益展示。</p>
      </el-collapse-item></el-collapse>
    </div>
  </section>
</template>

<script>
import {markRaw} from 'vue';
import {calendarCells, dateKey, isoDate, metricValue, monthChoices, numberText, presetDates, segmentPoints, shanghaiDay} from '@/js/account-analytics.js';

export default {
  name: 'AccountEquityPage',
  props: {groupid: {type: String, required: true}},
  data: () => ({
    payload: null, loading: false, error: '', serial: 0, accountKey: '', segmentId: 'current', range: 'all', customDates: [],
    curveMetric: 'return_pct', monthMetric: 'pnl', dayMetric: 'pnl', calendarMonth: '', curveChart: null, monthChart: null,
    periods: [{id:'today',label:'当日'},{id:'month',label:'本月'},{id:'quarter',label:'近三月'},{id:'year',label:'今年'},{id:'all',label:'全部'},{id:'custom',label:'自定义'}],
    weekdays: ['一','二','三','四','五','六','日']
  }),
  computed: {
    accounts() {return this.payload?.accounts || [];},
    account() {return this.accounts.find(item => item.account_key === this.accountKey);},
    summary() {return this.account?.summary || {};},
    receiptLabel() {const receipt=this.account?.receipt;if(receipt?.analytics_identity_valid===false)return '当前账号回执尚未确认';if(receipt?.valid===true)return '读取时柜台回执有效';return receipt?.updated_at?'历史柜台回执 · '+(receipt.reason||'当前状态未确认'):'尚无可确认的柜台回执';},
    selectedId() {return this.summary.selected_segment_id || this.summary.segment_id || this.account?.segments?.at(-1)?.id;},
    previousSegments() {return (this.account?.segments || []).slice(0, -1).reverse();},
    selectedSegmentLabel() {const segment = this.account?.segments?.find(item => item.id === this.selectedId);return segment ? this.segmentLabel(segment) : '当前资金阶段';},
    anchor() {return dateKey(this.account?.latest_trading_date) || dateKey(this.summary.latest_trading_date) || shanghaiDay();},
    points() {return segmentPoints(this.account, this.selectedId);},
    daily() {return (this.account?.daily || []).filter(row => row.segment_id === this.selectedId);},
    monthly() {return (this.account?.monthly || []).filter(row => row.segment_id === this.selectedId);},
    months() {return monthChoices(this.daily, this.monthly, this.anchor);},
    monthIndex() {return this.months.indexOf(this.calendarMonth);},
    cells() {return calendarCells(this.calendarMonth, this.daily);},
    hasCurve() {return this.points.some(row => metricValue(row, this.curveMetric) !== null);},
    hasMonthly() {return this.monthly.some(row => metricValue(row, this.monthMetric) !== null);},
    curveTitle() {return {return_pct:'已记录收益率',pnl:'已记录净损益',equity:'账户总权益'}[this.curveMetric];},
    rangeLabel() {const dates = presetDates(this.range, this.anchor, this.customDates);return dates.start_date ? `${isoDate(dates.start_date)} 至 ${isoDate(dates.end_date)}` : '所选资金阶段的全部采样';}
  },
  methods: {
    isoDate, metricValue,
    amount: numberText,
    percent(value) {return Number.isFinite(value) ? numberText(value, value !== 0 && Math.abs(value) < .01 ? 4 : 2) + '%' : '—';},
    metricText(value, metric) {return metric === 'return_pct' ? this.percent(value) : numberText(value);},
    tone(value) {return !Number.isFinite(value) || value === 0 ? '' : value > 0 ? 'positive' : 'negative';},
    formatTime(value) {return Number.isFinite(value) ? new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) : '—';},
    segmentLabel(segment) {return `${segment.id === 'initial' ? '初始阶段' : '资金重置后'} · ${this.formatTime(segment.start_at)}`;},
    dayTitle(cell) {if (!cell.day) return '';const reason = this.dayMetric === 'return_pct' ? cell.row?.return_reason : cell.row?.reason;return `${isoDate(cell.key)} · ${cell.row ? this.metricText(metricValue(cell.row, this.dayMetric), this.dayMetric) : '无记录'}${reason ? ' · ' + reason : ''}`;},
    chooseRange(value) {this.range = value;if (value !== 'custom' || this.customDates?.length === 2) this.load();},
    changeAccount() {this.segmentId = 'current';this.load();},
    moveMonth(delta) {const month = this.months[this.monthIndex + delta];if (month) this.calendarMonth = month;},
    async load() {
      if (!this.groupid) return;
      if (this.range === 'custom' && (!this.customDates || this.customDates.length !== 2)) return;
      const dates = presetDates(this.range, this.anchor, this.customDates);
      if (this.range === 'custom' && !dates.start_date) {this.error = '请选择有效的起止交易日。';return;}
      const serial = ++this.serial;
      this.controller?.abort();this.controller = new AbortController();this.loading = true;this.error = '';
      try {
        const response = await fetch('/mgr/account_equity', {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},signal:this.controller.signal,body:JSON.stringify({groupid:this.groupid,segment_id:this.segmentId,...dates})});
        const result = await response.json();
        if (!response.ok || result.result < 0) throw new Error(result.message || '账户权益读取失败');
        if (serial !== this.serial) return;
        this.payload = result;
        if (!this.accounts.some(item => item.account_key === this.accountKey)) this.accountKey = this.accounts[0]?.account_key || '';
        const observed = [...this.monthly.map(row => row.month), ...this.daily.map(row => isoDate(row.trading_date).slice(0, 7))].sort();
        this.calendarMonth = observed.at(-1) || this.months[0] || '';
        await this.$nextTick();this.paint();
      } catch (error) {if (serial === this.serial && error.name !== 'AbortError') this.error = error.message;}
      finally {if (serial === this.serial) this.loading = false;}
    },
    css(name) {return getComputedStyle(document.documentElement).getPropertyValue(name).trim();},
    chartBase() {
      const muted = this.css('--wt-muted'), grid = this.css('--wt-chart-grid');
      return {animation:false,backgroundColor:'transparent',grid:{left:64,right:24,top:22,bottom:38},tooltip:{trigger:'axis',backgroundColor:this.css('--wt-surface'),borderColor:grid,textStyle:{color:this.css('--wt-text'),fontSize:12}},xAxis:{type:'category',axisLine:{lineStyle:{color:grid}},axisTick:{show:false},axisLabel:{color:muted,fontSize:10,hideOverlap:true}},yAxis:{type:'value',scale:true,axisLabel:{color:muted,fontSize:10},splitLine:{lineStyle:{color:grid}}}};
    },
    drawCurve() {
      if (!this.curveChart) return;
      if (!this.hasCurve) {this.curveChart.clear();return;}
      const option = this.chartBase(), metric = this.curveMetric, color = this.css('--wt-chart-line') || '#6995bc';
      option.xAxis.data = this.points.map(row => this.formatTime(row.time));option.xAxis.boundaryGap = false;
      option.yAxis.axisLabel.formatter = value => metric === 'return_pct' ? numberText(value, Math.abs(value) < .1 ? 3 : 1) + '%' : numberText(value, 0);
      option.tooltip.formatter = items => `${items[0].axisValue}<br/>${this.curveTitle} ${this.metricText(items[0].value, metric)}${metric !== 'return_pct' ? ' ' + this.account.currency : ''}`;
      const values = this.points.map(row => metricValue(row,metric));
      option.series = [{name:this.curveTitle,type:'line',data:values,connectNulls:false,showSymbol:values.filter(value => value !== null).length === 1,symbolSize:7,lineStyle:{width:2,color},itemStyle:{color},areaStyle:{color,opacity:.045}}];
      option.dataZoom = this.points.length > 50 ? [{type:'inside'}] : [];
      this.curveChart.setOption(option,true);
    },
    drawMonths() {
      if (!this.monthChart) return;
      if (!this.hasMonthly) {this.monthChart.clear();return;}
      const option = this.chartBase(), metric = this.monthMetric;
      option.xAxis.data = this.monthly.map(row => row.month);
      option.yAxis.scale = false;
      option.yAxis.axisLabel.formatter = value => metric === 'return_pct' ? numberText(value,Math.abs(value) < .1 ? 3 : 1) + '%' : numberText(value,0);
      option.tooltip.formatter = items => {const row = this.monthly[items[0].dataIndex];return `${items[0].axisValue}<br/>已记录 ${this.metricText(items[0].value,metric)}${metric !== 'return_pct' ? ' ' + this.account.currency : ''}${Number.isFinite(row.observed_days) ? '<br/>' + row.observed_days + ' 个已采样交易日' : ''}`;};
      option.series = [{name:'已记录表现',type:'bar',barMaxWidth:42,data:this.monthly.map(row => ({value:metricValue(row,metric),itemStyle:{color:this.css(row[metric] < 0 ? '--wt-down' : '--wt-up')}}))}];
      this.monthChart.setOption(option,true);
    },
    paint() {
      if (!this.account) {this.disposeCharts();return;}
      if (this.$refs.curve && !this.curveChart) this.curveChart = markRaw(this.$echarts.init(this.$refs.curve));
      if (this.$refs.monthly && !this.monthChart) this.monthChart = markRaw(this.$echarts.init(this.$refs.monthly));
      this.drawCurve();this.drawMonths();
    },
    resize() {this.curveChart?.resize();this.monthChart?.resize();},
    disposeCharts() {this.curveChart?.dispose();this.monthChart?.dispose();this.curveChart = null;this.monthChart = null;}
  },
  watch: {
    groupid() {++this.serial;this.controller?.abort();this.disposeCharts();this.payload = null;this.accountKey = '';this.segmentId = 'current';this.load();},
    curveMetric() {this.drawCurve();},
    monthMetric() {this.drawMonths();}
  },
  mounted() {this.load();this.observer = new ResizeObserver(() => this.resize());this.observer.observe(this.$el);this.onTheme = () => this.paint();window.addEventListener('wt-theme-change', this.onTheme);},
  beforeUnmount() {++this.serial;this.controller?.abort();this.observer?.disconnect();window.removeEventListener('wt-theme-change', this.onTheme);this.disposeCharts();}
};
</script>

<style scoped>
.equity-page{width:100%;height:100%;overflow:auto;box-sizing:border-box;padding:20px 22px 32px;color:var(--wt-text);background:var(--wt-bg)}
.equity-receipt{display:flex;flex-wrap:wrap;gap:6px 16px;color:var(--wt-muted);font-size:11px;line-height:1.6;margin-bottom:12px}.equity-receipt strong{font-weight:500;color:var(--wt-text)}.equity-receipt.historical strong{color:var(--el-color-warning)}
.equity-heading,.equity-card-heading{display:flex;align-items:center;justify-content:space-between;gap:16px}.equity-heading h2{margin:0;font-size:20px;font-weight:600}.equity-heading p{margin:7px 0 0;color:var(--wt-muted);font-size:12px}.equity-heading>.el-button{flex:none}
.equity-filters{display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;margin:22px 0 16px}.equity-filters label{display:flex;flex-direction:column;gap:6px;font-size:11px;color:var(--wt-muted)}.equity-filters label .el-select{width:220px}.equity-periods{display:flex;flex-wrap:wrap;gap:3px;padding:3px;border:1px solid var(--wt-line);border-radius:6px;background:var(--wt-surface)}.equity-periods button{border:0;border-radius:4px;background:transparent;color:var(--wt-muted);padding:6px 11px;cursor:pointer;font-size:12px;line-height:18px}.equity-periods button.active{background:var(--wt-hover);color:var(--wt-text);box-shadow:inset 0 -2px var(--wt-chart-line)}.equity-periods button:hover{color:var(--wt-text)}
.equity-meta{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:10px;color:var(--wt-muted);margin:0 0 18px}.equity-metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;margin-bottom:22px}.equity-metrics article{display:flex;align-items:baseline;flex-wrap:wrap;gap:5px 9px;padding:0 18px;border-left:1px solid var(--wt-line)}.equity-metrics article:first-child{padding-left:0;border:0}.equity-metrics article>span{width:100%;color:var(--wt-muted);font-size:11px;margin-bottom:5px}.equity-metrics strong{font:24px var(--wt-mono);font-weight:500;overflow-wrap:anywhere}.equity-metrics small{font-size:11px;color:var(--wt-muted)}
.equity-card{border:1px solid var(--wt-line);border-radius:8px;background:var(--wt-surface);padding:17px 18px;min-width:0}.equity-card h3{margin:0;font-weight:550;font-size:13px}.equity-card-heading>div:first-child>span{display:block;font-size:10px;color:var(--wt-muted);margin-top:6px}.equity-card-heading .el-radio-group{flex:none}.equity-chart-wrap{position:relative}.equity-main-chart{height:340px;width:100%;margin-top:12px}.equity-month-chart{height:282px;width:100%;margin-top:12px}.equity-chart-empty,.equity-no-account{display:flex;align-items:center;justify-content:center;text-align:center;font-size:12px;line-height:1.8;color:var(--wt-muted)}.equity-chart-empty{position:absolute;inset:0;padding:24px;pointer-events:none;margin:0}.equity-no-account{min-height:300px}
.equity-breakdown{display:grid;grid-template-columns:minmax(0,1.1fr) minmax(380px,1fr);gap:16px;margin-top:16px}.equity-calendar-nav{display:flex;align-items:center;justify-content:center;gap:12px;margin:14px 0 10px}.equity-calendar-nav .el-select{width:126px}.equity-calendar-nav button{background:none;border:0;color:var(--wt-text);font-size:24px;cursor:pointer;padding:0 10px}.equity-calendar-nav button:disabled{opacity:.25;cursor:default}.equity-weekdays,.equity-calendar{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));gap:4px}.equity-weekdays{font-size:10px;color:var(--wt-muted);text-align:center;margin-bottom:7px}.equity-day{min-height:47px;display:flex;flex-direction:column;gap:4px;padding:5px 4px;border-radius:4px;background:var(--wt-hover);text-align:right;box-sizing:border-box;font-size:10px}.equity-day time{font-size:10px;color:var(--wt-muted)}.equity-day span{font-family:var(--wt-mono);font-size:11px;overflow-wrap:anywhere}.equity-day.positive{background:color-mix(in srgb,var(--wt-up) 9%,transparent)}.equity-day.negative{background:color-mix(in srgb,var(--wt-down) 9%,transparent)}.equity-day.missing{background:transparent;color:var(--wt-muted)}.equity-day.blank{background:transparent}.equity-calendar-note{font-size:10px;color:var(--wt-muted);margin:10px 0 0}.positive{color:var(--wt-up)}.negative{color:var(--wt-down)}.equity-method{margin-top:18px}.equity-method p{font-size:12px;line-height:1.8;color:var(--wt-muted);margin:8px 0;max-width:950px}
@media(min-width:1600px){.equity-main-chart{height:400px}}@media(max-width:1000px){.equity-breakdown{grid-template-columns:1fr}.equity-main-chart{height:300px}}@media(max-width:640px){.equity-page{padding:15px 12px 24px}.equity-heading h2{font-size:18px}.equity-filters{gap:10px;margin-top:18px}.equity-filters label{flex:1;min-width:150px}.equity-filters label .el-select{width:100%}.equity-periods{width:100%;box-sizing:border-box;justify-content:space-between}.equity-periods button{padding:5px 8px}.equity-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:18px 8px}.equity-metrics article{padding-left:12px}.equity-metrics article:nth-child(3){border:0;padding-left:0}.equity-metrics strong{font-size:21px}.equity-card{padding:13px 11px}.equity-card-heading{align-items:flex-start;flex-wrap:wrap;gap:10px}.equity-main-chart{height:270px}.equity-meta{line-height:1.6}.equity-day{min-height:45px;padding:4px 2px}.equity-day span{font-size:10px}.equity-month-chart{height:240px}}
</style>
