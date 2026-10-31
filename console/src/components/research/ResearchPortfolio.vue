<template>
 <section class="portfolio-research" aria-label="多因子趋势研究">
  <div class="portfolio-heading"><div><h3>多因子趋势</h3><p>先固定因子和品种预算，再做独立检验与 WT 回测。</p></div><el-button :loading="loading" @click="load">刷新</el-button></div>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <p v-if="!state.policy?.enabled" class="note">等待后台研究配置。</p>
  <template v-else>
   <div class="budget-summary"><span>资金上限 <b>{{ money(state.policy.capital_limit) }}</b></span><span>组合总仓位 <b>{{ state.policy.max_total_lots }} 手</b></span><span>周期 <b>1 分钟</b></span></div>
   <el-form label-position="top" :disabled="!canEdit || saving">
    <el-form-item label="因子权重"><el-select v-model="method" aria-label="因子权重"><el-option label="训练拟合，验证集选择" value="select"/><el-option label="等权基准" value="equal"/><el-option label="训练期风险权重" value="risk"/></el-select></el-form-item>
    <div class="instrument-grid"><div v-for="item in state.policy.instruments" :key="item.code" class="instrument"><el-checkbox v-model="selected[item.code]" :disabled="item.code===state.policy.anchor">{{ item.name || item.code }}</el-checkbox><small>{{ item.code }}</small><label>品种预算 <el-input-number v-model="percentages[item.code]" :min="1" :max="100" :step="5" :disabled="!selected[item.code]" :aria-label="item.code+'预算百分比'"/> %</label></div></div>
    <p class="note">已选预算 {{ totalPercent }}%。不足一手时保留现金；缺失品种的预算不会自动转给其他品种。</p>
    <details><summary>查看固定因子与验证要求</summary><p v-for="factor in state.policy.factors" :key="factor.id"><strong>{{ factor.name || factor.id }}</strong><br><code>{{ factor.expression }}</code></p><p>{{ state.policy.cost_note }}</p><p>这些是明示定义的趋势候选。只有通过历史、前向与成本检验后，才可能在安全时段应用。</p></details>
    <el-button type="primary" :disabled="!projectId || totalPercent>100 || totalPercent<=0" :loading="saving" @click="run">开始研究</el-button>
   </el-form>
  </template>
  <div class="portfolio-history"><h4>研究进度</h4><p v-if="!state.jobs?.length" class="note">后台会自动研究默认组合，也可在当前项目中提交固定配置。</p><div v-for="job in state.jobs || []" :key="job.id" class="job"><strong>{{ status(job.status) }}</strong><span>{{ (job.spec?.codes || []).join(' · ') }}</span><small>{{ job.message || job.reasons?.join('；') }}</small></div></div>
  <details v-for="version in state.versions || []" :key="version.id" class="version">
   <summary>{{ status(version.status) }} · {{ date(version.created_at) }}</summary>
   <p>{{ version.message }}</p>
   <div v-if="version.native_validation" class="native-validation">
    <h4>原生账户回测</h4><p>{{ nativeStatus(version.native_validation.status) }}</p>
    <div class="native-table-scroll"><table class="native-results"><thead><tr><th>场景</th><th>原策略净盈亏</th><th>候选净盈亏</th><th>候选平仓笔数</th></tr></thead><tbody><tr v-for="(scenario,name) in version.native_validation.scenarios || {}" :key="name"><td>{{ {base:'基础成本',stress:'压力成本'}[name] || name }}</td><td :class="pnlClass(scenario.baseline?.net)">{{ nativeMoney(scenario.baseline?.net) }}</td><td :class="pnlClass(scenario.candidate?.net)">{{ nativeMoney(scenario.candidate?.net) }}</td><td>{{ scenario.candidate?.closed_trades ?? '—' }}</td></tr></tbody></table></div>
    <p v-for="(reason,index) in version.native_validation.reasons || []" :key="index" class="native-reason">{{ reason }}</p>
   </div>
   <p v-if="version.model?.weights">冻结权重：{{ weights(version.model.weights) }}</p>
   <p v-if="version.test">因子诊断（非账户盈亏）：保留测试样本 {{ version.test.rows }}，扣成本均值 {{ number(version.test.mean_net_return) }}</p>
   <p>{{ version.promotion?.reason || '原策略继续运行；候选须完成原有前向门槛。' }}</p>
  </details>
 </section>
</template>

<script>
export default {
 props:{request:{type:Function,required:true},projectId:String,canEdit:Boolean},
 data:()=>({state:{},loading:false,saving:false,error:'',method:'select',selected:{},percentages:{}}),
 computed:{totalPercent(){return Object.keys(this.selected).reduce((sum,code)=>sum+(this.selected[code] ? Number(this.percentages[code] || 0) : 0),0)}},
 watch:{projectId:{immediate:true,handler(){this.state={};this.selected={};this.percentages={};this.load()}}},
 methods:{
  async load(){if(!this.projectId)return;this.loading=true;this.error='';try{this.state=await this.request('portfolio_status',{project_id:this.projectId});const items=this.state.policy?.instruments || [];for(const item of items){if(!(item.code in this.selected)){this.selected[item.code]=true;this.percentages[item.code]=Math.floor(100/items.length)}}}catch(error){this.error=error.message}finally{this.loading=false}},
  async run(){this.saving=true;this.error='';try{const codes=Object.keys(this.selected).filter(code=>this.selected[code]);await this.request('portfolio_run',{project_id:this.projectId,codes,weight_method:this.method,allocations:Object.fromEntries(codes.map(code=>[code,Number(this.percentages[code])/100]))});await this.load()}catch(error){this.error=error.message}finally{this.saving=false}},
  status(value){return {ready:'等待研究',running:'隔离研究中',waiting_data:'等待数据',waiting_validation:'等待 WT 验证',research_accepted:'研究检验通过',research_rejected:'研究检验未通过',completed:'已完成',failed:'执行失败，保留输入',collecting:'积累前向样本',deferred:'等待验证条件',passed:'等待安全应用',applied:'已安全应用',rolled_back:'已回退'}[value] || '后台持续核对'},
  nativeStatus(value){return {passed:'原生回测通过',rejected:'原生回测未通过',failed:'原生回测未通过',waiting_validation:'等待原生回测'}[value] || '原生回测结果'},
  nativeMoney(value){return typeof value==='number'&&Number.isFinite(value) ? value.toLocaleString('zh-CN',{maximumFractionDigits:2})+' 元' : '—'},
  pnlClass(value){return typeof value==='number'&&Number.isFinite(value) ? value>0?'native-profit':value<0?'native-loss':'' : ''},
  money(value){return Number(value || 0).toLocaleString('zh-CN')+' 元'},number(value){return Number.isFinite(value) ? (value*100).toFixed(3)+'%' : '—'},date(value){return value ? new Date(value*1000).toLocaleString('zh-CN',{hour12:false}) : ''},weights(value){return Object.entries(value).map(([key,weight])=>key+' '+(weight*100).toFixed(1)+'%').join('，')}
 }
}
</script>

<style scoped>
.portfolio-research{padding:20px}.portfolio-heading{display:flex;justify-content:space-between;gap:16px}.portfolio-heading h3{margin-top:0}.portfolio-heading p,.note,.portfolio-history small,details{color:var(--wt-muted);font-size:13px;line-height:1.7}.budget-summary{display:flex;flex-wrap:wrap;gap:22px;padding:14px 0}.budget-summary span{font-size:13px;color:var(--wt-muted)}.budget-summary b{color:var(--wt-text);margin-left:6px}.instrument-grid{display:flex;gap:16px;flex-wrap:wrap}.instrument{border:1px solid var(--wt-line);border-radius:8px;padding:14px;min-width:230px}.instrument small,.instrument label{display:block;margin:8px 0;color:var(--wt-muted)}.portfolio-history{margin-top:26px}.job{padding:12px 0;border-bottom:1px solid var(--wt-line);display:grid;gap:6px}.job span,.job small{overflow-wrap:anywhere}.version{padding:14px 0}details{margin:14px 0}summary{cursor:pointer}code{overflow-wrap:anywhere}@media(max-width:600px){.portfolio-research{padding:12px}.portfolio-heading{display:block}.instrument{min-width:0;width:100%}}
.native-validation{margin:18px 0}.native-validation h4{color:var(--wt-text);margin-bottom:6px}.native-table-scroll{overflow-x:auto}.native-results{width:100%;border-collapse:collapse;white-space:nowrap}.native-results th,.native-results td{text-align:right;padding:10px 12px;border-bottom:1px solid var(--wt-line)}.native-results th:first-child,.native-results td:first-child{text-align:left}.native-results th{font-weight:500;color:var(--wt-text)}.native-profit{color:var(--wt-up)}.native-loss{color:var(--wt-down)}.native-reason{color:var(--wt-text)}
</style>
