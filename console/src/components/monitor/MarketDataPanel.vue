<template>
  <section class="market-data" aria-label="历史行情补数">
    <header class="market-heading">
      <div><h2>行情数据</h2><p>从数据源补充历史行情，供看盘和回测读取。</p></div>
      <el-button :loading="loading" @click="load">刷新状态</el-button>
    </header>

    <el-alert v-if="subscriptionError" :title="subscriptionError" type="error" :closable="false"/>
    <MainReplayPanel preview :symbol="historyCode" @calendar="mainCalendar=$event" @select-interval="selectMain"/>
    <article class="market-card">
      <div class="card-heading"><div><h3>历史行情补数</h3><p>CNEquity 先查数据湖，缺失时采集可获取的行情，再补充到 WT。</p></div><div class="history-actions"><el-button size="small" :loading="permissionsLoading" :disabled="!subscriptions?.can_manage" @click="loadProviders(true)">检查数据源</el-button><el-button link @click="openTerminal">返回看盘</el-button></div></div>
      <el-alert v-if="historyError" :title="historyError" type="error" :closable="false" show-icon/>
      <div class="backfill-form">
        <label>数据平台<el-select v-model="provider" placeholder="选择数据平台" aria-label="补数平台" @change="providerChanged"><el-option v-for="item in providers" :key="item.id" :value="item.id" :label="item.label"/></el-select></label>
        <label>具体合约<el-select-v2 v-model="historyCode" filterable :options="historyContractOptions" placeholder="选择合约" aria-label="历史补数合约" @change="clearCoverage"><template #default="{item}"><span :style="item.main?{color:'var(--wt-success)',fontWeight:600}:{}">{{item.label}}</span></template></el-select-v2></label>
        <label>数据频率<el-select v-model="period" aria-label="补数数据频率" @change="clearCoverage"><el-option v-for="item in periods" :key="item.value" :value="item.value" :label="frequencyLabel(item)"/></el-select></label>
        <label class="date-field">日期范围<el-date-picker v-model="dates" type="daterange" value-format="YYYYMMDD" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" aria-label="历史补数日期范围" @change="clearCoverage"/></label>
      </div>
      <p class="provider-note"><el-tag v-if="currentProvider?.configured" size="small" effect="plain">{{ provider==='cnequity'?'研究环境已接入':provider==='tushare'?'凭据已配置':'环境可用' }}</el-tag> {{ currentPeriod?.reason || currentProvider?.reason || '选择数据源后检查环境和数据覆盖；账号权限与历史数据是否存在分别判断。' }}</p>
      <p class="market-note">{{ backfillReason }}<span v-if="currentProvider?.max_days"> · 每次最多 {{ currentProvider.max_days }} 个自然日</span></p>
      <p class="market-note">同合约、同周期、同时间点的 WT 原记录保留，只新增缺失记录。CNEquity 五分钟由完整有效的一分钟线聚合并注明来源；不会用日线或 K 线编造 Tick。</p>
      <div class="backfill-restart"><el-checkbox v-model="allowBackfillRestart" :disabled="Boolean(subscriptions?.process_running)">允许写入时短暂停采并自动恢复（引擎须停止）</el-checkbox><p>下载时继续采集；写入 WT 历史文件前短暂停采。未勾选时，中台运行期间不能补写。</p></div>
      <div class="provider-availability"><span v-for="item in providers.filter(item=>!item.available)" :key="item.id">{{ item.label }}：{{ item.reason || '尚未接入' }}</span></div>
      <p v-if="provider==='cnequity'" class="market-note">交易运行时可以准备数据湖，不写入 WT。停止使用该数据中台的交易引擎后，可继续导入；分钟源仅有最近窗口，缺失的旧日期会明确列出。</p>
      <div class="card-actions"><span class="action-note">{{ coverage?.message || '休市日不等于缺失；日期状态以交易日历和本地记录为准。' }}</span><el-button :disabled="!validHistory" :loading="checking" @click="checkCoverage">{{ provider==='cnequity'?'检查 WT 与数据湖':'检查本地日期' }}</el-button><el-button type="primary" :disabled="!validHistory || !currentProvider?.available || currentPeriod?.available===false || (!canBackfill && !(provider==='cnequity' && canPrepareLake))" :loading="submitting" @click="startBackfill">{{ provider==='cnequity' && subscriptions?.process_running?'准备到数据湖':'读取并补充到 WT' }}</el-button></div>
      <div v-if="coverage" class="coverage-result" aria-live="polite">
        <div class="coverage-heading"><strong>{{ provider==='cnequity'?'WT 与数据湖覆盖':'本地数据情况' }}</strong><span>{{ coverage.storage || '' }}</span></div>
        <el-table :data="coverage.dates || []" max-height="280" size="small" empty-text="此范围内没有可展示的本地记录">
          <el-table-column label="日期" min-width="110"><template #default="{row}">{{ formatDate(row.date) }}</template></el-table-column>
          <el-table-column label="状态" min-width="110"><template #default="{row}"><el-tag size="small" effect="plain" :type="['missing','partial'].includes(row.status)?'warning':row.status==='present'?'success':'info'">{{ coverageLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column prop="rows" label="WT 记录数" min-width="100"/>
          <el-table-column v-if="provider==='cnequity'" prop="lake_rows" label="数据湖记录数" min-width="115"/>
          <el-table-column v-if="provider==='cnequity'" label="数据湖状态" min-width="120"><template #default="{row}">{{ coverageLabel(row.lake_status) }}</template></el-table-column>
          <el-table-column prop="source" label="数据来源" min-width="160"/>
        </el-table>
      </div>
    </article>

    <article class="market-card">
      <div class="card-heading"><div><h3>补数任务</h3><p>只在任务排队或执行时刷新。下载失败会保留原因，不填造行情。</p></div><el-button size="small" :loading="jobsLoading" @click="loadJobs">刷新任务</el-button></div>
      <el-alert v-if="jobsError" :title="jobsError" type="error" :closable="false"/>
      <el-table :data="jobs" size="small" empty-text="还没有历史补数任务">
        <el-table-column prop="code" label="合约" min-width="145"/>
        <el-table-column label="来源 / 频率" min-width="140"><template #default="{row}">{{ providerLabel(row.provider) }} · {{ periodLabel(row.period) }}</template></el-table-column>
        <el-table-column label="申请日期" min-width="185"><template #default="{row}">{{ formatDate(row.start_date) }} — {{ formatDate(row.end_date) }}</template></el-table-column>
        <el-table-column label="状态" min-width="105"><template #default="{row}"><el-tag size="small" :type="jobType(row)">{{ jobLabel(row) }}</el-tag></template></el-table-column>
        <el-table-column label="WT 新增" width="100"><template #default="{row}">{{ row.rows ?? '—' }}</template></el-table-column>
        <el-table-column label="说明" min-width="220"><template #default="{row}"><span :class="{'task-error':row.status==='failed'}">{{ row.error || row.message || '—' }}</span><small v-if="row.lake">数据湖返回 {{row.lake.rows||0}} 条；WT 已有跳过 {{row.already_present_rows??Math.max(0,(row.received_rows||0)-(row.rows||0))}} 条。<template v-if="row.lake.missing_dates?.length">仍有 {{row.lake.missing_dates.length}} 个交易日缺失或不完整。</template></small></template></el-table-column>
        <el-table-column label="操作" width="130"><template #default="{row}"><el-button v-if="row.status==='ready'" size="small" :disabled="Boolean(subscriptions?.process_running) || !canBackfill" @click="importPrepared(row)">继续导入 WT</el-button></template></el-table-column>
      </el-table>
    </article>
  </section>
</template>

<script>
import MainReplayPanel from '../backtest/MainReplayPanel.vue'
const dayString = value => {
  const date = new Date(value);
  return String(date.getFullYear()) + String(date.getMonth() + 1).padStart(2, '0') + String(date.getDate()).padStart(2, '0');
};
export default {
  name: 'MarketDataPanel', components:{MainReplayPanel},
  props: { groupid: { type: String, required: true } },

  data: () => ({
    mainCalendar:null, subscriptions: null, subscriptionError: '', loading: false,
    providers: [], provider: '', permissionsLoading: false, historyCode: '', period: 'd', dates: [dayString(Date.now() - 7 * 86400000), dayString(Date.now())],
    canBackfill: false, canPrepareLake: false, backfillReason: '', allowBackfillRestart: false, historyError: '', coverage: null, checking: false, submitting: false,
    jobs: [], jobsError: '', jobsLoading: false, poll: null, disposed: false
  }),
  computed: {
    catalog() { return this.subscriptions?.catalog || []; },
    historyContractOptions() { return this.catalog.map(item=>{
        const s=this.mainCalendar?.segments.find(s=>this.matchesMain(item,s))
        return {value:item.code,label:item.name+' · '+item.code+(s?' · 主力 '+s.start+'—'+s.end:''),main:!!s}
      }); },
    currentProvider() { return this.providers.find(item => item.id === this.provider); },
    periods() {
      return [{ value: 'd', label: '日线' }, { value: 'm1', label: '1 分钟' }, { value: 'm5', label: '5 分钟' }].map(item => ({ ...item, available: false, ...this.currentProvider?.periods?.find(period => period.value === item.value) }));
    },
    currentPeriod() { return this.periods.find(item => item.value === this.period); },
    validHistory() { return Boolean(this.historyCode && this.period && this.dates?.length === 2); }
  },
  methods: {
    matchesMain(item,s){const ex=this.mainCalendar?.product?.split('.')[0],prod=this.mainCalendar?.product?.split('.')[1];return item.exchange===ex && item.product===prod && (item.code===ex+'.'+prod+'.'+s.month || item.ticker===prod+(ex==='CZCE'?s.month.slice(1):s.month))},
    selectMain({product,segment}){this.dates=[segment.start,segment.end];const item=this.catalog.find(x=>this.matchesMain(x,segment));if(item)this.historyCode=item.code;else this.historyError=product+'.'+segment.month+' 不在当前中台的可写合约表中';this.clearCoverage()},
    async request(path, body = {}) {
      const response = await fetch('/mgr/market/' + path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ groupid: this.groupid, ...body }) });
      const result = await response.json();
      if (!response.ok || result.result < 0) throw new Error(result.message || '行情数据请求失败');
      return result;
    },
    async load() {
      this.loading = true;
      await Promise.all([this.loadSubscriptions(), this.loadProviders(), this.loadJobs()]);
      if (!this.disposed) this.loading = false;
    },
    async loadSubscriptions() {
      try {
        const result = await this.request('subscriptions');
        if (this.disposed) return;
        this.subscriptions = result;
        this.subscriptionError = '';
        if (!this.historyCode) this.historyCode = result.catalog?.find(item => item.selected)?.code || result.catalog?.[0]?.code || '';
      } catch (error) { if (!this.disposed) this.subscriptionError = error.message; }
    },
    async loadProviders(refresh = false) {
      this.permissionsLoading = refresh;
      try {
        const result = await this.request('providers', { refresh });
        if (this.disposed) return;
        this.providers = result.providers || [];
        this.canBackfill = Boolean(result.can_backfill);
        this.canPrepareLake = Boolean(result.can_prepare_lake);
        this.backfillReason = result.reason || '';
        if (!this.provider) this.provider = this.providers.find(item => item.available)?.id || this.providers[0]?.id || '';
        this.providerChanged();
        this.historyError = '';
      } catch (error) { if (!this.disposed) this.historyError = error.message; }
      finally { if (!this.disposed) this.permissionsLoading = false; }
    },
    providerChanged() {
      if (!this.periods.some(item => item.value === this.period)) this.period = this.periods[0]?.value || '';
      this.clearCoverage();
    },
    clearCoverage() { this.coverage = null; },
    frequencyLabel(item) { const state=item.state||this.currentProvider?.state;const label=({not_configured:'未配置',not_connected:'未接入',unsupported:'不支持'})[state]||(this.provider==='tushare'?(this.currentProvider?.configured?'待检查权限':'未配置凭据'):'不可用');return item.label+(item.available===false?' · '+label:''); },
    historyRequest() { return { provider: this.provider, code: this.historyCode, period: this.period, start_date: this.dates[0], end_date: this.dates[1] }; },
    async checkCoverage() {
      this.checking = true;
      this.historyError = '';
      try { this.coverage = await this.request('coverage', this.historyRequest()); }
      catch (error) { this.historyError = error.message; }
      finally { this.checking = false; }
    },
    async startBackfill() {
      this.submitting = true;
      this.historyError = '';
      try {
        const result = await this.request('backfill', { ...this.historyRequest(), restart_collector: this.allowBackfillRestart });
        this.$message.success(result.message || '补数任务已提交');
        await this.loadJobs();
      } catch (error) { this.historyError = error.message; }
      finally { this.submitting = false; }
    },
    async importPrepared(job) {
      this.submitting = true;this.historyError='';
      try { await this.request('backfill',{provider:job.provider,code:job.code,period:job.period,start_date:job.start_date,end_date:job.end_date,restart_collector:this.allowBackfillRestart});await this.loadJobs(); }
      catch(error){this.historyError=error.message;}
      finally{this.submitting=false;}
    },
    async loadJobs() {
      clearTimeout(this.poll);
      this.jobsLoading = true;
      try {
        const result = await this.request('jobs');
        if (this.disposed) return;
        const wasPending = this.jobs.some(item => ['queued', 'running'].includes(item.status));
        this.jobs = result.jobs || [];
        this.jobsError = '';
        const pending = this.jobs.some(item => ['queued', 'running'].includes(item.status));
        if (pending) this.poll = setTimeout(() => this.loadJobs(), 3000);
        else if (wasPending && this.validHistory) await this.checkCoverage();
      } catch (error) { if (!this.disposed) this.jobsError = error.message; }
      finally { if (!this.disposed) this.jobsLoading = false; }
    },
    formatDate(value) { const day = String(value || ''); return day.length === 8 ? day.slice(0, 4) + '-' + day.slice(4, 6) + '-' + day.slice(6) : day || '—'; },
    formatTime(value) { return new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }); },
    coverageLabel(value) { return ({ present: '已有数据', missing: '交易日缺失', closed: '休市', not_listed: '未上市 / 已到期', partial: '部分数据', unknown: '无记录，待确认' })[value] || value || '尚未检查'; },
    jobIncomplete(job) { return job.status==='completed'&&Boolean(job.lake?.missing_dates?.length||job.lake?.warnings?.length); },
    jobLabel(job) { return this.jobIncomplete(job)?'部分完成':({ queued: '排队中', running: '读取中', ready:'湖已准备', completed: '已完成', failed: '失败' })[job.status] || job.status; },
    jobType(job) { return this.jobIncomplete(job)?'warning':({ queued: 'info', running: 'warning', completed: 'success', failed: 'danger' })[job.status] || 'info'; },
    providerLabel(value) { return this.providers.find(item => item.id === value)?.label || value; },
    periodLabel(value) { return ({ d: '日线', m1: '1 分钟', m5: '5 分钟', tick: 'Tick' })[value] || value; },
    openTerminal() { this.$router.replace({ query: { ...this.$route.query, tab: 'terminal' } }); }
  },
  mounted() { this.load(); },
  beforeUnmount() { this.disposed = true; clearTimeout(this.poll); }
};
</script>

<style scoped>
.market-data{padding:4px 0 20px;color:var(--wt-text);min-width:0}.market-heading,.card-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:16px}.market-heading h2{font-size:18px;font-weight:550;margin:0 0 7px}.market-data h3{font-size:14px;font-weight:550;margin:0 0 7px}.market-data p{font-size:12px;line-height:1.8;color:var(--wt-muted);margin:0}.market-card{padding:20px;border:1px solid var(--wt-line);border-radius:10px;background:var(--wt-surface);margin-top:16px}.market-card :deep(.el-alert){margin-bottom:14px}.card-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:16px;flex-wrap:wrap}.action-note{font-size:12px;color:var(--wt-muted);margin-right:auto;line-height:1.8}.backfill-form{display:grid;grid-template-columns:170px minmax(230px,1fr) 130px;gap:14px}.backfill-form label{display:flex;flex-direction:column;gap:8px;font-size:12px;color:var(--wt-muted);min-width:0}.backfill-form .date-field{grid-column:1 / -1;max-width:480px}.backfill-form :deep(.el-date-editor){width:100%;box-sizing:border-box}.provider-note{margin-top:14px!important}.backfill-restart{margin-top:12px}.backfill-restart p{font-size:11px}.history-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.market-note{margin-top:8px!important}.provider-availability{display:flex;gap:8px 20px;flex-wrap:wrap;font-size:11px;color:var(--wt-muted);line-height:1.8;margin-top:8px}.coverage-result{margin-top:20px;border-top:1px solid var(--wt-line);padding-top:16px}.coverage-heading{display:flex;gap:12px;align-items:center;margin-bottom:12px;font-size:12px}.coverage-heading strong{font-weight:550}.coverage-heading span{color:var(--wt-muted);font-size:11px;overflow-wrap:anywhere}.market-data :deep(.el-table){font-size:12px}.task-error{color:var(--wt-danger)}@media(max-width:900px){.backfill-form{grid-template-columns:1fr 1fr}.backfill-form .date-field{max-width:none}.market-card{padding:16px}.market-heading,.card-heading{align-items:flex-start}}@media(max-width:600px){.backfill-form,.backfill-form .date-field{grid-column:auto}.card-actions{justify-content:flex-start}.action-note{width:100%}.market-heading{flex-wrap:wrap}.market-card{padding:14px}}
</style>
