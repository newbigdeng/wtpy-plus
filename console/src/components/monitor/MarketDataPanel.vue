<template>
  <section class="market-data" aria-label="行情订阅与历史补数">
    <header class="market-heading">
      <div><h2>行情数据</h2><p>订阅决定中台采集哪些合约。历史补数保存到云端，供看盘和回测读取。</p></div>
      <el-button :loading="loading" @click="load">刷新状态</el-button>
    </header>

    <article class="market-card">
      <div class="card-heading"><div><h3>实时采集订阅</h3><p>看盘时切换合约，只改变图表；不会自动增加采集订阅。</p></div><el-tag effect="plain">{{ subscriptions?.profile_label || '读取环境中' }}</el-tag></div>
      <el-alert v-if="subscriptionError" :title="subscriptionError" type="error" :closable="false" show-icon/>
      <template v-if="subscriptions">
        <div class="collector-state"><el-tag :type="subscriptions.datahub_running?'success':'info'">数据中台{{ subscriptions.datahub_running?'运行中':'已停止' }}</el-tag><span>{{ selected.length }} 个待保存订阅 · 当前已保存 {{ subscriptions.selected.length }} 个</span></div>
        <p class="environment-note">SimNow-1 跟随实际交易时段；SimNow-2 用于测试，可能回放历史行情。两套环境分别保存订阅和数据。</p>
        <div class="catalog-refresh"><span>更新只读取 CTP 合约资料，不增加订阅或下单。</span><el-button size="small" :loading="catalogRefreshing" :disabled="!subscriptions.can_refresh || changed" :title="changed?'请先保存或还原当前选择':''" @click="refreshCatalog">从 CTP 更新合约资料</el-button></div>
        <div class="subscription-filters">
          <el-select v-model="exchange" clearable placeholder="全部交易所" aria-label="筛选交易所"><el-option v-for="item in exchanges" :key="item" :value="item" :label="item"/></el-select>
          <el-select-v2 v-model="product" clearable filterable :options="productOptions" placeholder="筛选品种" aria-label="筛选品种"/>
        </div>
        <el-select-v2 v-model="selected" class="subscription-select" multiple filterable clearable collapse-tags collapse-tags-tooltip :max-collapse-tags="6" :options="subscriptionOptions" :disabled="!subscriptions.can_manage || subscriptions.process_running" placeholder="搜索并选择具体合约，例如 rb2701" aria-label="采集订阅合约"/>
        <div class="card-actions"><span v-if="subscriptions.process_running" class="action-note">请先停止交易引擎，再修改采集订阅。</span><span v-else class="action-note">保存不会启动交易。采集进程重启后，新订阅生效。</span><el-button :disabled="!changed" @click="resetSubscriptions">还原选择</el-button><el-button type="primary" :disabled="!changed || !selected.length || !subscriptions.can_apply" :loading="previewing" @click="previewSubscriptions">预览变更</el-button></div>
        <p v-if="subscriptions.message" class="market-note">{{ subscriptions.message }}</p>
        <p v-if="subscriptions.catalog_message" class="market-note">{{ subscriptions.catalog_message }}<span v-if="subscriptions.metadata_time"> · 合约表 {{ formatTime(subscriptions.metadata_time) }}</span></p>
      </template>
    </article>

    <article class="market-card">
      <div class="card-heading"><div><h3>历史行情补数</h3><p>先检查本地日期，再向所选平台申请数据。平台权限决定可取的频率和范围。</p></div><div class="history-actions"><el-button size="small" :loading="permissionsLoading" :disabled="!subscriptions?.can_manage" @click="loadProviders(true)">检查平台权限</el-button><el-button link @click="openTerminal">返回看盘</el-button></div></div>
      <el-alert v-if="historyError" :title="historyError" type="error" :closable="false" show-icon/>
      <div class="backfill-form">
        <label>数据平台<el-select v-model="provider" placeholder="选择数据平台" aria-label="补数平台" @change="providerChanged"><el-option v-for="item in providers" :key="item.id" :value="item.id" :label="item.label"/></el-select></label>
        <label>具体合约<el-select-v2 v-model="historyCode" filterable :options="historyContractOptions" placeholder="选择合约" aria-label="历史补数合约" @change="clearCoverage"/></label>
        <label>数据频率<el-select v-model="period" aria-label="补数数据频率" @change="clearCoverage"><el-option v-for="item in periods" :key="item.value" :value="item.value" :label="item.label+(item.available===false?' · 未授权':'')"/></el-select></label>
        <label class="date-field">日期范围<el-date-picker v-model="dates" type="daterange" value-format="YYYYMMDD" range-separator="至" start-placeholder="开始日期" end-placeholder="结束日期" aria-label="历史补数日期范围" @change="clearCoverage"/></label>
      </div>
      <p class="provider-note"><el-tag v-if="currentProvider?.configured" size="small" effect="plain">服务器已配置凭据</el-tag> {{ currentPeriod?.reason || currentProvider?.reason || '账户凭据由服务器保存，浏览器不读取平台密钥。' }}</p>
      <p class="market-note">{{ backfillReason }}<span v-if="currentProvider?.max_days"> · 每次最多 {{ currentProvider.max_days }} 个自然日</span></p>
      <p class="market-note">日线只补日线。不会把日线拼成分钟或 Tick 行情。已有数据按所选频率保留，新增记录会登记来源。</p>
      <div class="backfill-restart"><el-checkbox v-model="allowBackfillRestart" :disabled="Boolean(subscriptions?.process_running)">允许写入时短暂停采并自动恢复（引擎须停止）</el-checkbox><p>下载时继续采集；写入 WT 历史文件前短暂停采。未勾选时，中台运行期间不能补写。</p></div>
      <div class="provider-availability"><span v-for="item in providers.filter(item=>!item.available)" :key="item.id">{{ item.label }}：{{ item.reason || '尚未接入' }}</span></div>
      <div class="card-actions"><span class="action-note">{{ coverage?.message || '休市日不等于缺失；日期状态以交易日历和本地记录为准。' }}</span><el-button :disabled="!validHistory" :loading="checking" @click="checkCoverage">检查本地日期</el-button><el-button type="primary" :disabled="!validHistory || !currentProvider?.available || currentPeriod?.available===false || !canBackfill || Boolean(subscriptions?.process_running)" :loading="submitting" @click="startBackfill">读取并保存到云端</el-button></div>
      <div v-if="coverage" class="coverage-result" aria-live="polite">
        <div class="coverage-heading"><strong>本地数据情况</strong><span>{{ coverage.storage || '' }}</span></div>
        <el-table :data="coverage.dates || []" max-height="280" size="small" empty-text="此范围内没有可展示的本地记录">
          <el-table-column label="日期" min-width="110"><template #default="{row}">{{ formatDate(row.date) }}</template></el-table-column>
          <el-table-column label="状态" min-width="110"><template #default="{row}"><el-tag size="small" effect="plain" :type="['missing','partial'].includes(row.status)?'warning':row.status==='present'?'success':'info'">{{ coverageLabel(row.status) }}</el-tag></template></el-table-column>
          <el-table-column prop="rows" label="本地记录数" min-width="100"/>
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
        <el-table-column label="状态" min-width="95"><template #default="{row}"><el-tag size="small" :type="jobType(row.status)">{{ jobLabel(row.status) }}</el-tag></template></el-table-column>
        <el-table-column label="保存记录" width="100"><template #default="{row}">{{ row.rows ?? '—' }}</template></el-table-column>
        <el-table-column label="说明" min-width="220"><template #default="{row}"><span :class="{'task-error':row.status==='failed'}">{{ row.error || row.message || '—' }}</span></template></el-table-column>
      </el-table>
    </article>

    <el-dialog v-model="showPreview" title="确认采集订阅变更" width="min(620px, 94vw)">
      <p class="preview-note">仅修改 {{ subscriptions?.profile_label }} 的数据中台。交易引擎保持停止。</p>
      <div class="preview-items"><strong>新增 {{ preview?.added?.length || 0 }}</strong><span>{{ preview?.added?.join('、') || '无' }}</span><strong>移除 {{ preview?.removed?.length || 0 }}</strong><span>{{ preview?.removed?.join('、') || '无' }}</span></div>
      <el-checkbox v-model="restartCollector" :disabled="!subscriptions?.datahub_running">保存后重启正在运行的数据中台</el-checkbox>
      <p class="preview-note">{{ subscriptions?.datahub_running ? '重启期间会短暂停止采集。未勾选时，仅保存配置。' : '数据中台当前已停止；下次启动时使用新订阅。' }}</p>
      <template #footer><el-button @click="showPreview=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveSubscriptions">确认保存</el-button></template>
    </el-dialog>
  </section>
</template>

<script>
const dayString = value => {
  const date = new Date(value);
  return String(date.getFullYear()) + String(date.getMonth() + 1).padStart(2, '0') + String(date.getDate()).padStart(2, '0');
};
export default {
  name: 'MarketDataPanel',
  props: { groupid: { type: String, required: true } },
  data: () => ({
    subscriptions: null, selected: [], exchange: '', product: '', subscriptionError: '', loading: false,
    preview: null, previewing: false, showPreview: false, restartCollector: false, saving: false, catalogRefreshing: false,
    providers: [], provider: '', permissionsLoading: false, historyCode: '', period: 'd', dates: [dayString(Date.now() - 7 * 86400000), dayString(Date.now())],
    canBackfill: false, backfillReason: '', allowBackfillRestart: false, historyError: '', coverage: null, checking: false, submitting: false,
    jobs: [], jobsError: '', jobsLoading: false, poll: null, disposed: false
  }),
  computed: {
    catalog() { return this.subscriptions?.catalog || []; },
    exchanges() { return [...new Set(this.catalog.map(item => item.exchange))].sort(); },
    productOptions() { return (this.subscriptions?.products || []).filter(item => !this.exchange || item.exchange === this.exchange).map(item => ({ value: item.code, label: item.name + ' · ' + item.code })); },
    subscriptionOptions() {
      return this.catalog.filter(item => this.selected.includes(item.subscription_code) || ((!this.exchange || item.exchange === this.exchange) && (!this.product || item.exchange + '.' + item.product === this.product))).map(item => ({ value: item.subscription_code, label: item.name + ' · ' + item.subscription_code }));
    },
    historyContractOptions() { return this.catalog.map(item => ({ value: item.code, label: item.name + ' · ' + item.code })); },
    changed() { return this.selected.slice().sort().join(',') !== (this.subscriptions?.selected || []).slice().sort().join(','); },
    currentProvider() { return this.providers.find(item => item.id === this.provider); },
    periods() {
      return [{ value: 'd', label: '日线' }, { value: 'm1', label: '1 分钟' }, { value: 'm5', label: '5 分钟' }].map(item => ({ ...item, available: false, ...this.currentProvider?.periods?.find(period => period.value === item.value) }));
    },
    currentPeriod() { return this.periods.find(item => item.value === this.period); },
    validHistory() { return Boolean(this.historyCode && this.period && this.dates?.length === 2); }
  },
  methods: {
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
        this.selected = [...result.selected];
        this.subscriptionError = '';
        if (!this.historyCode) this.historyCode = result.catalog?.find(item => item.selected)?.code || result.catalog?.[0]?.code || '';
      } catch (error) { if (!this.disposed) this.subscriptionError = error.message; }
    },
    resetSubscriptions() { this.selected = [...this.subscriptions.selected]; },
    async refreshCatalog() {
      this.catalogRefreshing = true;
      this.subscriptionError = '';
      try {
        const result = await this.request('subscriptions/refresh');
        await this.loadSubscriptions();
        this.$message.success(result.message || '合约资料已更新');
      } catch (error) { this.subscriptionError = error.message; }
      finally { this.catalogRefreshing = false; }
    },
    async previewSubscriptions() {
      this.previewing = true;
      try {
        this.preview = await this.request('subscriptions/preview', { contracts: this.selected });
        this.restartCollector = false;
        this.showPreview = true;
        this.subscriptionError = '';
      } catch (error) { this.subscriptionError = error.message; }
      finally { this.previewing = false; }
    },
    async saveSubscriptions() {
      this.saving = true;
      try {
        const result = await this.request('subscriptions/apply', { contracts: this.preview.selected, restart_collector: this.restartCollector });
        this.showPreview = false;
        await this.loadSubscriptions();
        this.$message.success(result.message || (result.collector_restarted ? '订阅已保存，数据中台已重启' : result.restart_required ? '订阅已保存，重启数据中台后生效' : '订阅已保存'));
      } catch (error) { this.subscriptionError = error.message; }
      finally { this.saving = false; }
    },
    async loadProviders(refresh = false) {
      this.permissionsLoading = refresh;
      try {
        const result = await this.request('providers', { refresh });
        if (this.disposed) return;
        this.providers = result.providers || [];
        this.canBackfill = Boolean(result.can_backfill);
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
    coverageLabel(value) { return ({ present: '已有数据', missing: '交易日缺失', closed: '休市', not_listed: '未上市 / 已到期', partial: '部分数据', unknown: '无记录，待确认' })[value] || value; },
    jobLabel(value) { return ({ queued: '排队中', running: '读取中', completed: '已完成', failed: '失败' })[value] || value; },
    jobType(value) { return ({ queued: 'info', running: 'warning', completed: 'success', failed: 'danger' })[value] || 'info'; },
    providerLabel(value) { return this.providers.find(item => item.id === value)?.label || value; },
    periodLabel(value) { return ({ d: '日线', m1: '1 分钟', m5: '5 分钟', tick: 'Tick' })[value] || value; },
    openTerminal() { this.$router.replace({ query: { ...this.$route.query, tab: 'terminal' } }); }
  },
  watch: { exchange() { this.product = ''; } },
  mounted() { this.load(); },
  beforeUnmount() { this.disposed = true; clearTimeout(this.poll); }
};
</script>

<style scoped>
.market-data{padding:4px 0 20px;color:var(--wt-text);min-width:0}.market-heading,.card-heading{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:16px}.market-heading h2{font-size:18px;font-weight:550;margin:0 0 7px}.market-data h3{font-size:14px;font-weight:550;margin:0 0 7px}.market-data p{font-size:12px;line-height:1.8;color:var(--wt-muted);margin:0}.market-card{padding:20px;border:1px solid var(--wt-line);border-radius:10px;background:var(--wt-surface);margin-top:16px}.market-card :deep(.el-alert){margin-bottom:14px}.collector-state{display:flex;gap:12px;align-items:center;margin-bottom:10px;font-size:12px;color:var(--wt-muted)}.environment-note{margin-bottom:14px!important}.catalog-refresh{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.catalog-refresh span{font-size:11px;color:var(--wt-muted);line-height:1.8}.subscription-filters{display:grid;grid-template-columns:180px minmax(200px,1fr);gap:12px;max-width:650px;margin-bottom:12px}.subscription-select{width:100%}.card-actions{display:flex;align-items:center;justify-content:flex-end;gap:10px;margin-top:16px;flex-wrap:wrap}.action-note{font-size:12px;color:var(--wt-muted);margin-right:auto;line-height:1.8}.backfill-form{display:grid;grid-template-columns:170px minmax(230px,1fr) 130px;gap:14px}.backfill-form label{display:flex;flex-direction:column;gap:8px;font-size:12px;color:var(--wt-muted);min-width:0}.backfill-form .date-field{grid-column:1 / -1;max-width:480px}.backfill-form :deep(.el-date-editor){width:100%;box-sizing:border-box}.provider-note{margin-top:14px!important}.backfill-restart{margin-top:12px}.backfill-restart p{font-size:11px}.history-actions{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.market-note{margin-top:8px!important}.provider-availability{display:flex;gap:8px 20px;flex-wrap:wrap;font-size:11px;color:var(--wt-muted);line-height:1.8;margin-top:8px}.coverage-result{margin-top:20px;border-top:1px solid var(--wt-line);padding-top:16px}.coverage-heading{display:flex;gap:12px;align-items:center;margin-bottom:12px;font-size:12px}.coverage-heading strong{font-weight:550}.coverage-heading span{color:var(--wt-muted);font-size:11px;overflow-wrap:anywhere}.market-data :deep(.el-table){font-size:12px}.task-error{color:var(--wt-danger)}.preview-items{display:grid;grid-template-columns:70px 1fr;gap:12px;margin:18px 0;font-size:12px;line-height:1.8;max-height:280px;overflow:auto}.preview-items strong{font-weight:550}.preview-items span{overflow-wrap:anywhere}.preview-note{font-size:12px;color:var(--wt-muted);line-height:1.8;margin:8px 0}@media(max-width:900px){.backfill-form{grid-template-columns:1fr 1fr}.backfill-form .date-field{max-width:none}.market-card{padding:16px}.market-heading,.card-heading{align-items:flex-start}}@media(max-width:600px){.backfill-form,.subscription-filters{grid-template-columns:1fr}.catalog-refresh{align-items:flex-start;flex-wrap:wrap}.backfill-form .date-field{grid-column:auto}.collector-state{align-items:flex-start;flex-wrap:wrap}.card-actions{justify-content:flex-start}.action-note{width:100%}.market-heading{flex-wrap:wrap}.market-card{padding:14px}}
</style>
