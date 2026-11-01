<template>
  <section class="market-data">
    <article class="market-card">
      <div class="card-heading"><div><h3>实时采集订阅</h3><p>看盘时切换合约，只改变图表；不会自动增加采集订阅。</p></div><el-tag effect="plain">{{ subscriptions?.profile_label || '读取环境中' }}</el-tag></div>
      <el-alert v-if="subscriptionError" :title="subscriptionError" type="error" :closable="false" show-icon/>
      <template v-if="subscriptions">
        <div class="collector-state"><el-tag :type="subscriptions.datahub_running?'success':'info'">数据中台{{ subscriptions.datahub_running?'运行中':'已停止' }}</el-tag><span>{{ selected.length }} 个待保存订阅 · 当前已保存 {{ subscriptions.selected.length }} 个</span></div>
        <p v-if="deferred?.symbols" class="market-note">下次启动采用 {{deferred.symbols.join('、')}} <el-button size="small" :disabled="!subscriptions.can_manage" @click="cancelQueue">取消待生效订阅</el-button></p>
        <p v-if="deferred?.message" class="market-note">{{deferred.message}}</p>
        <p class="environment-note">{{ subscriptions.datahub_id }} · 此环境单独保存订阅和历史数据。<el-button link :disabled="changed || loading" @click="loadSubscriptions">刷新状态</el-button></p>
        <div class="catalog-refresh"><span>更新只读取 CTP 合约资料，不增加订阅或下单。</span><el-button size="small" :loading="catalogRefreshing" :disabled="!subscriptions.can_refresh || changed" :title="changed?'请先保存或还原当前选择':''" @click="refreshCatalog">从 CTP 更新合约资料</el-button></div>
        <div class="subscription-filters">
          <el-select v-model="exchange" clearable placeholder="全部交易所" aria-label="筛选交易所"><el-option v-for="item in exchanges" :key="item" :value="item" :label="item"/></el-select>
          <el-select-v2 v-model="product" clearable filterable :options="productOptions" placeholder="筛选品种" aria-label="筛选品种"/>
        </div>
        <el-select-v2 v-model="selected" class="subscription-select" multiple filterable clearable collapse-tags collapse-tags-tooltip :max-collapse-tags="6" :options="subscriptionOptions" :disabled="!canEdit" placeholder="搜索并选择具体合约，例如 rb2701" aria-label="采集订阅合约"/>
        <div class="card-actions"><span v-if="subscriptions.process_running && !canQueue" class="action-note">请先停止交易引擎，再修改采集订阅。</span><span v-else-if="canQueue" class="action-note">保存后，下次启动此中台时生效。</span><span v-else class="action-note">保存不会启动交易。采集进程重启后，新订阅生效。</span><el-button :disabled="!changed" @click="resetSubscriptions">还原选择</el-button><el-button type="primary" :disabled="!changed || !selected.length || !canEdit" :loading="previewing" @click="previewSubscriptions">预览变更</el-button></div>
        <p v-if="subscriptions.message" class="market-note">{{ subscriptions.message }}</p>
        <p v-if="subscriptions.catalog_message" class="market-note">{{ subscriptions.catalog_message }}<span v-if="subscriptions.metadata_time"> · 合约表 {{ formatTime(subscriptions.metadata_time) }}</span></p>
      </template>
    </article>

    <el-dialog v-model="showPreview" title="确认采集订阅变更" width="min(620px, 94vw)">
      <p class="preview-note">仅修改 {{ subscriptions?.profile_label }} 的数据中台。交易引擎保持停止。</p>
      <div class="preview-items"><strong>新增 {{ preview?.added?.length || 0 }}</strong><span>{{ preview?.added?.join('、') || '无' }}</span><strong>移除 {{ preview?.removed?.length || 0 }}</strong><span>{{ preview?.removed?.join('、') || '无' }}</span></div>
      <el-checkbox v-if="!canQueue" v-model="restartCollector" :disabled="!subscriptions?.datahub_running">保存后重启正在运行的数据中台</el-checkbox>
      <p v-if="!canQueue" class="preview-note">{{ subscriptions?.datahub_running ? '重启期间会短暂停止采集。未勾选时，仅保存配置。' : '数据中台当前已停止；下次启动时使用新订阅。' }}</p>
      <template #footer><el-button @click="showPreview=false">取消</el-button><el-button type="primary" :loading="saving" @click="saveSubscriptions">确认保存</el-button></template>
    </el-dialog>
  </section>
</template>

<script>
export default {
  name: 'FuturesSubscriptions',
  props: { groupid: String, profileid: String },
  data: () => ({
    subscriptions: null, selected: [], exchange: '', product: '', subscriptionError: '',
    preview: null, previewing: false, showPreview: false, restartCollector: false, saving: false, catalogRefreshing: false,
    disposed: false, deferred: null, loading: false,
  }),
  computed: {
    catalog() { return this.subscriptions?.catalog || []; },
    exchanges() { return [...new Set(this.catalog.map(item => item.exchange))].sort(); },
    productOptions() { return (this.subscriptions?.products || []).filter(item => !this.exchange || item.exchange === this.exchange).map(item => ({ value: item.code, label: item.name + ' · ' + item.code })); },
    subscriptionOptions() {
      return this.catalog.filter(item => this.selected.includes(item.subscription_code) || ((!this.exchange || item.exchange === this.exchange) && (!this.product || item.exchange + '.' + item.product === this.product))).map(item => ({ value: item.subscription_code, label: item.name + ' · ' + item.subscription_code }));
    },
    changed() { return this.selected.slice().sort().join(',') !== (this.deferred?.symbols || this.subscriptions?.selected || []).slice().sort().join(','); },
    canQueue() { return Boolean(this.deferred?.editable); },
    canEdit() { return this.subscriptions?.can_manage && (this.canQueue || (this.subscriptions.can_apply && !this.deferred?.symbols)); }
  },
  methods: {
    async queue(action, contracts=[]) {
      const response=await fetch('/mgr/assets/futures/deferred',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({action,groupid:this.groupid,profileid:this.profileid,contracts,revision:this.deferred?.revision || 0,base:this.subscriptions?.selected || []})});
      if(action==='status' && [404,403].includes(response.status))return null;
      const result=await response.json();
      if(result.result<0){if(action==='status' && result.result===-403)return null;throw new Error(result.message);}
      return result;
    },
    async cancelQueue() {
      try{await this.queue('cancel');await this.loadSubscriptions();}
      catch(error){this.subscriptionError=error.message;}
    },
    async request(path, body = {}) {
      const response = await fetch('/mgr/market/' + path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ groupid: this.groupid, profileid: this.profileid, ...body }) });
      const result = await response.json();
      if (!response.ok || result.result < 0) throw new Error(result.message || '行情数据请求失败');
      return result;
    },
    async loadSubscriptions() {
      this.loading = true;
      try {
        const result = await this.request('subscriptions');
        if (this.disposed) return;
        this.subscriptions = result;
        this.deferred = await this.queue('status');
        this.selected = [...(this.deferred?.symbols || result.selected)];
        this.subscriptionError = '';
      } catch (error) { if (!this.disposed) this.subscriptionError = error.message; }
      finally { this.loading=false; }
    },
    resetSubscriptions() { this.selected = [...(this.deferred?.symbols || this.subscriptions.selected)]; },
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
        const result = this.canQueue ? await this.queue('save',this.preview.selected) : await this.request('subscriptions/apply', { contracts: this.preview.selected, restart_collector: this.restartCollector });
        this.showPreview = false;
        await this.loadSubscriptions();
        this.$message.success(result.message || (result.collector_restarted ? '订阅已保存，数据中台已重启' : result.restart_required ? '订阅已保存，重启数据中台后生效' : '订阅已保存'));
      } catch (error) { this.subscriptionError = error.message; }
      finally { this.saving = false; }
    },
    formatTime(value) { return new Date(value).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}); }
  },
  watch: { exchange() { this.product = ''; } },
  mounted() { this.loadSubscriptions(); },
  beforeUnmount() { this.disposed=true; }
};
</script>
<style scoped>
.market-data{color:var(--wt-text);min-width:0}.market-card{padding:16px;border:1px solid var(--wt-line);border-radius:8px;background:var(--wt-surface)}.card-heading,.card-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.market-card p,.action-note{font-size:12px;color:var(--wt-muted);line-height:1.7}.card-actions{margin-top:12px}.market-note{margin:8px 0}
.collector-state{display:flex;gap:12px;align-items:center;margin-bottom:10px;font-size:12px;color:var(--wt-muted)}.environment-note{margin-bottom:14px!important}.catalog-refresh{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.catalog-refresh span{font-size:11px;color:var(--wt-muted);line-height:1.8}.subscription-filters{display:grid;grid-template-columns:180px minmax(200px,1fr);gap:12px;max-width:650px;margin-bottom:12px}.subscription-select{width:100%}.preview-items{display:grid;grid-template-columns:70px 1fr;gap:12px;margin:18px 0;font-size:12px;line-height:1.8;max-height:280px;overflow:auto}.preview-items strong{font-weight:550}.preview-items span{overflow-wrap:anywhere}.preview-note{font-size:12px;color:var(--wt-muted);line-height:1.8;margin:8px 0}
@media(max-width:600px){.subscription-filters{grid-template-columns:1fr}.card-heading,.catalog-refresh,.collector-state{flex-wrap:wrap}}
</style>
