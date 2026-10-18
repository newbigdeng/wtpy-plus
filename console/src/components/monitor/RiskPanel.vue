<template>
 <section class="risk-panel">
  <header class="risk-header">
   <div><h2>风控中心 <el-popover placement="bottom-start" width="420" trigger="click"><template #reference><el-button circle text size="small" aria-label="风控来源说明">i</el-button></template><p>{{state?.boundary||'柜台账户与引擎理论账簿分别计算。'}}</p><p>柜台权益 = 静态权益 + 持仓盈亏。查询回执超过 300 秒或通道断线时，柜台指标不可计算。基准按交易日冻结；预警只记录，不自动交易。</p></el-popover></h2><p>分别查看柜台资金与引擎持仓，处理告警。</p></div>
   <el-button :loading="loading" @click="load(false)">刷新状态</el-button>
  </header>
  <el-alert v-if="error" type="error" :closable="false" :title="error"/>
  <template v-if="state">
   <div class="risk-status">
    <el-tag :type="state.snapshot.fresh?'success':'warning'" effect="plain">{{freshnessText}}</el-tag>
    <span>交易日 {{state.snapshot.trading_date || '—'}} · 资金更新距今 {{age(state.snapshot.fund_age_seconds)}}</span>
    <span>后台检查 {{state.observer?.online?'运行中':'未确认'}} · 每 {{state.observer?.period_seconds || 5}} 秒</span>
   </div>
   <p v-if="!state.snapshot.broker_summary.valid&&state.snapshot.engine_reference.equity!=null" class="engine-reference">引擎参考权益 {{amount(state.snapshot.engine_reference.equity)}} · {{state.snapshot.engine_reference.fresh?'引擎账簿有效':'历史记录'}} · 独立账簿，不替代柜台资金。</p>
   <section class="risk-baseline risk-card">
    <div><strong>当日资金基准</strong><el-tag v-if="state.snapshot.baseline?.manual" type="warning" size="small">手工</el-tag>
     <span class="baseline-number">{{amount(state.snapshot.baseline?.value)}}</span>
     <small v-if="state.snapshot.baseline">{{state.snapshot.baseline.source}} · {{state.snapshot.baseline.channel}} / {{state.snapshot.baseline.currency}} · 交易日 {{state.snapshot.baseline.tradingday}} · 当日冻结 {{formatTime(state.snapshot.baseline.frozen_at/1000)}}</small>
     <small v-else>尚无可冻结的柜台资金。{{state.snapshot.broker_summary.reason}}</small>
    </div>
    <div class="baseline-actions"><el-button type="primary" plain :disabled="!state.can_operate||!queryChannel" :loading="busy==='query'" @click="queryBroker">立即读取柜台</el-button><el-button :disabled="!state.can_control||Boolean(state.snapshot.baseline)" @click="openManual">手动指定</el-button></div>
   </section>
   <div class="risk-metrics">
    <article v-for="metric in metricItems" :key="metric.key" :class="{'risk-breached':breached(metric.key)}">
     <span>{{metric.label}}</span><strong>{{metricValue(metric)}}</strong>
     <small>{{state.snapshot.metrics[metric.key].book==='broker'?'柜台':'引擎'}} · {{state.snapshot.metrics[metric.key].reason}}</small>
     <small v-if="state.snapshot.metrics[metric.key].snapshot_time">快照 {{formatTime(state.snapshot.metrics[metric.key].snapshot_time/1000)}}</small>
    </article>
    <article><span>限额使用率</span><strong>{{state.snapshot.metrics.limit_usage_pct.valid?amount(state.snapshot.metrics.limit_usage_pct.value)+'%':'不可计算'}}</strong><el-progress v-if="state.snapshot.metrics.limit_usage_pct.valid" :percentage="Math.min(100,state.snapshot.metrics.limit_usage_pct.value)" :status="state.snapshot.metrics.limit_usage_pct.value>=100?'exception':state.snapshot.metrics.limit_usage_pct.value>=80?'warning':'success'" :show-text="false"/><small>{{state.snapshot.metrics.limit_usage_pct.reason}} · 各限额独立计算</small></article>
   </div>
   <div class="risk-alerts" aria-live="polite">
    <el-alert v-for="alert in state.alerts" :key="alert.key+alert.state"
     :type="alert.disposition==='silenced'?'info':alert.severity==='danger'?'error':'warning'" :closable="false" show-icon
     :title="alert.message+(alert.threshold!=null?' · 阈值 '+alert.threshold:'')+' · '+statusName(alert.disposition)"/>
    <p v-if="!state.policy.enabled">后台预警未开启。</p>
    <p v-else-if="!state.alerts.length">已设阈值当前未触发。</p>
   </div>
   <el-collapse class="broker-details"><el-collapse-item title="柜台手续费、冻结资金与拒单记录" name="broker"><TradeRiskPanel ref="tradeRisk" :groupid="groupid" :show-query="false"/></el-collapse-item></el-collapse>
   <div class="risk-columns">
    <section class="risk-card">
     <h3>后台预警</h3>
     <el-form label-position="top" :disabled="!state.can_control || Boolean(busy)">
      <el-form-item label="启用后台预警"><el-switch v-model="policy.enabled" aria-label="启用后台预警"/></el-form-item>
      <el-form-item label="柜台通道 / 币种"><el-select v-model="accountSelection" placeholder="单账户自动选择；多账户须指定"><el-option v-for="account in state.snapshot.broker.accounts" :key="account.channel+':'+account.currency" :label="account.channel+' / '+account.currency" :value="account.channel+':'+account.currency"/></el-select></el-form-item>
      <el-form-item label="引擎初始资金（只用于引擎权益回撤）"><el-input-number v-model="policy.capital" :min="0" :max="1000000000000000" :step="100000" :controls="false"/></el-form-item>
      <el-form-item label="引擎账簿有效期（秒）"><el-input-number v-model="policy.stale_seconds" :min="5" :max="3600" :precision="0"/></el-form-item>
      <div v-for="metric in metricItems" :key="metric.key" class="risk-threshold">
       <el-checkbox :model-value="enabledThreshold(metric.key)" @change="toggleThreshold(metric.key,$event)">{{metric.label}}{{metric.key==='available_funds'?'下限':'上限'}}{{metric.suffix?'（'+metric.suffix+'）':''}}</el-checkbox>
       <el-input-number v-if="enabledThreshold(metric.key)" v-model="policy.thresholds[metric.key]" :min="0.01" :max="metric.max" :precision="2" :controls="false" :aria-label="metric.label+'阈值'"/>
      </div>
      <el-button type="primary" :loading="busy==='policy'" @click="savePolicy">保存预警设置</el-button>
     </el-form>
     <el-button :disabled="!state.can_operate" :loading="busy==='evaluate'" @click="evaluate">立即检查</el-button>
    </section>
    <section class="risk-card">
     <h3>原生 SimpleRiskMon</h3>
     <p>当前配置：{{state.native.params.active?'启用':'关闭'}}。CTA 插件可调整组合执行目标，保存后需重启。</p>
     <div class="risk-native-scale"><span>账簿风险倍率 {{state.snapshot.native_scale ?? '—'}} · 记录交易日 {{state.snapshot.native_scale_date || '—'}}</span><small>这是持久快照，不能据此确认插件当前在线或通道已退仓。</small></div>
     <el-alert v-if="!state.native.supported" type="warning" :closable="false" :title="state.native.reason"/>
     <el-form v-else label-position="top" :disabled="!state.can_control || Boolean(busy)">
      <el-form-item label="启用原生资金风控"><el-switch v-model="native.active" aria-label="启用原生资金风控"/></el-form-item>
      <el-form-item label="原生基准资金"><el-input-number v-model="native.base_amount" :min="0" :controls="false"/></el-form-item>
      <el-form-item label="检查间隔（秒）"><el-input-number v-model="native.calc_span" :min="1" :max="3600" :precision="0"/></el-form-item>
      <el-form-item label="启用日内盈利回撤"><el-switch v-model="native.inner_day_active"/></el-form-item>
      <div class="risk-native-fields">
       <label>盈利启动线（前日权益 %）<el-input-number v-model="native.basic_ratio" :min="100" :max="10000" :precision="0" :controls="false"/></label>
       <label>盈利回撤阈值（%）<el-input-number v-model="native.inner_day_fd" :min="0.01" :max="100" :controls="false"/></label>
       <label>回撤时间窗（有效交易分钟）<el-input-number v-model="native.risk_span" :min="1" :max="1440" :precision="0" :controls="false"/></label>
       <label>触发后目标倍率（0 至 1）<el-input-number v-model="native.risk_scale" :min="0" :max="1" :step="0.1" :precision="2" :controls="false"/></label>
      </div>
      <el-form-item label="启用跨日权益回撤"><el-switch v-model="native.multi_day_active"/></el-form-item>
      <el-form-item label="跨日回撤阈值（%）"><el-input-number v-model="native.multi_day_fd" :min="0.01" :max="100" :controls="false"/></el-form-item>
      <el-button type="warning" plain :loading="busy==='native'" @click="saveNative">保存原生配置（需重启组合）</el-button>
     </el-form>
     <p class="risk-footnote">日内比例是“从高点回吐的当日盈利 / 高点相对前日的盈利”，不是账户净值回撤。保存配置不会重启组合，也不代表当前进程已加载；请安排重启并核对风控日志。</p>
    </section>
   </div>
   <el-collapse class="risk-manual">
    <el-collapse-item title="人工处置：策略决策暂停与 CTA 目标过滤" name="manual">
     <p>暂停保留原执行目标；过滤撤去该策略对组合目标的贡献。组合净仓位可能保留其他策略的目标。解除过滤可能重新建仓。</p>
     <StrategyRuntime :groupid="groupid"/>
    </el-collapse-item>
   </el-collapse>
   <section class="risk-card risk-events">
    <h3>告警时间线</h3>
    <el-table :data="state.events || []" empty-text="暂无风控记录" size="small">
     <el-table-column label="时间" width="170"><template #default="{row}">{{formatTime(row.at)}}</template></el-table-column>
     <el-table-column label="类型" width="155"><template #default="{row}">{{eventName(row.kind)}}</template></el-table-column>
     <el-table-column prop="detail" label="记录" min-width="280"/>
     <el-table-column label="当前值 / 阈值" min-width="120"><template #default="{row}">{{row.value==null?'—':amount(row.value)}} / {{row.threshold==null?'—':amount(row.threshold)}}</template></el-table-column>
     <el-table-column label="状态" min-width="110"><template #default="{row}"><el-tag v-if="row.kind==='alert'" :type="row.status==='pending'?'danger':row.status==='silenced'?'info':'success'" size="small">{{statusName(row.status)}}</el-tag></template></el-table-column>
     <el-table-column label="操作人" width="130"><template #default="{row}">{{row.handled_by||row.actor}}<small v-if="row.handled_at" class="event-handled">{{formatTime(row.handled_at)}}</small></template></el-table-column>
     <el-table-column label="处置" min-width="180"><template #default="{row}"><template v-if="row.kind==='alert'"><el-button text size="small" :disabled="!state.can_operate||Boolean(busy)||row.status==='confirmed'" @click="handleAlert(row,'confirm')">确认</el-button><el-button text size="small" :disabled="!state.can_operate||Boolean(busy)||row.status==='silenced'" @click="handleAlert(row,'silence')">静默 30 分钟</el-button></template></template></el-table-column>
    </el-table>
   </section>
  </template>
  <el-dialog v-model="manualOpen" title="手工指定当日资金基准" width="min(470px,92vw)"><p>仅用于缺失柜台基准时兜底。冻结后当日不能覆盖。</p><el-form label-position="top"><el-form-item label="交易日"><el-input v-model="manual.tradingday" placeholder="YYYYMMDD"/></el-form-item><el-form-item label="通道 / 币种"><el-select v-model="manual.account" filterable allow-create placeholder="通道:币种，例如 sim:CNY"><el-option v-for="account in state?.snapshot.broker.accounts||[]" :key="account.channel+':'+account.currency" :label="account.channel+' / '+account.currency" :value="account.channel+':'+account.currency"/></el-select></el-form-item><el-form-item label="基准资金"><el-input-number v-model="manual.value" :min="0.01" :controls="false"/></el-form-item><el-form-item label="来源说明"><el-input v-model="manual.reason" placeholder="例如：柜台日结单昨结算权益" maxlength="200"/></el-form-item></el-form><template #footer><el-button @click="manualOpen=false">取消</el-button><el-button type="warning" :loading="busy==='manual'" @click="saveManual">冻结并标注手工来源</el-button></template></el-dialog>
 </section>
</template>
<script>
import StrategyRuntime from './StrategyRuntime.vue'
import TradeRiskPanel from './TradeRiskPanel.vue'
const clone=value=>JSON.parse(JSON.stringify(value))
export default{
 name:'RiskPanel',components:{StrategyRuntime,TradeRiskPanel},props:{groupid:{type:String,required:true}},
 data:()=>({state:null,policy:{enabled:false,capital:0,stale_seconds:30,thresholds:{}},native:{},loading:false,busy:'',error:'',serial:0,manualOpen:false,manual:{tradingday:'',account:'',value:0,reason:''},
  metricItems:[{key:'day_loss_pct',label:'当日亏损比例',suffix:'%',max:100},{key:'drawdown_pct',label:'权益高点回撤',suffix:'%',max:100},{key:'gross_lots',label:'理论持仓总手数',suffix:'手',max:1e9},{key:'margin_usage_pct',label:'保证金占用率',suffix:'%',max:10000},{key:'available_funds',label:'可用资金',suffix:'',max:1e15},{key:'top1_concentration_pct',label:'Top1 品种手数集中度',suffix:'%',max:100}]}),
 computed:{freshnessText(){return {fresh:'柜台回执有效',engine_only:'柜台不可确认 · 仅引擎参考',stale:'数据过期 · 风险不可判定',stopped:'进程已停止 · 历史账簿',missing:'等待资金账簿',invalid:'账簿暂不可读'}[this.state.snapshot.state] || '等待数据'},queryChannel(){return this.policy.account_channel||this.state?.snapshot.broker_summary.channel||(this.state?.snapshot.broker.sources.length===1?this.state.snapshot.broker.sources[0].channel:'')},accountSelection:{get(){return this.policy.account_channel?this.policy.account_channel+':'+this.policy.account_currency:''},set(value){const [channel,currency]=value.split(':');this.policy.account_channel=channel;this.policy.account_currency=currency}}},
 methods:{
  async request(path,data){const response=await fetch('/mgr/'+path,{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(data)});const result=await response.json();if(result.result<0)throw new Error(result.message);return result},
  async load(reset=false){const serial=++this.serial;this.loading=true;try{const result=await this.request('qryrisk_state',{groupid:this.groupid});if(serial!==this.serial)return;const first=!this.state;this.state=result;this.error='';if(reset||first){this.policy=clone(result.policy);this.native=clone(result.native.params)}}catch(error){if(serial===this.serial)this.error=error.message}finally{if(serial===this.serial)this.loading=false}},
  age(value){return value==null?'未知':Math.max(0,Math.round(value))+' 秒'},
  amount(value){return value==null?'不可计算':Number(value).toLocaleString('zh-CN',{maximumFractionDigits:2})},
  breached(key){return this.state.alerts.some(item=>item.key===key&&item.state==='breached')},
  metricValue(metric){const item=this.state.snapshot.metrics[metric.key];if(!item.valid||item.value==null)return '不可计算';return this.amount(item.value)+metric.suffix},
  enabledThreshold(key){return this.policy.thresholds[key]!=null},
  toggleThreshold(key,enabled){this.policy.thresholds[key]=enabled?(key==='gross_lots'?10:key==='available_funds'?10000:5):null},
  formatTime(value){return new Date(value*1000).toLocaleString('zh-CN',{hour12:false})},
  eventName(value){return {policy_updated:'预警配置',native_config_saved:'原生参数待重启',alert:'预警状态变化',recovered:'预警恢复',manual_baseline:'手工基准',alert_confirm:'确认告警',alert_silence:'静默告警'}[value]||value},
  statusName(value){return {pending:'待处理',confirmed:'已确认',silenced:'已静默'}[value]||'待处理'},
  async savePolicy(){const groupid=this.groupid;try{await this.$confirm('保存组合 '+groupid+' 的预警阈值。后台只记录告警，不暂停策略、不撤单、不平仓。','保存预警配置',{type:'warning'});this.busy='policy';const result=await this.request('cmt_risk_policy',{groupid,policy:this.policy});if(groupid!==this.groupid)return;this.policy=clone(result.policy);await this.load();this.$message.success(result.message)}catch(error){if(error instanceof Error)this.$message.error(error.message)}finally{this.busy=''}},
  async queryBroker(){const groupid=this.groupid;this.busy='query';try{const result=await this.request('query_trade_funds',{groupid,channel:this.queryChannel});if(groupid!==this.groupid)return;await this.load();await this.$refs.tradeRisk?.load();this.$message.success(result.message)}catch(error){this.$message.error(error.message)}finally{this.busy=''}},
  openManual(){this.manual={tradingday:String(this.state.snapshot.broker.accounts[0]?.trading_date||this.state.snapshot.trading_date||''),account:this.accountSelection||(this.state.snapshot.broker.accounts[0]?[this.state.snapshot.broker.accounts[0].channel,this.state.snapshot.broker.accounts[0].currency].join(':'):''),value:0,reason:''};this.manualOpen=true},
  async saveManual(){const groupid=this.groupid;this.busy='manual';try{const [channel,currency]=this.manual.account.split(':');const result=await this.request('risk_baseline',{groupid,channel,currency,tradingday:this.manual.tradingday,value:this.manual.value,reason:this.manual.reason});if(groupid!==this.groupid)return;this.manualOpen=false;await this.load();this.$message.success(result.message)}catch(error){this.$message.error(error.message)}finally{this.busy=''}},
  async handleAlert(row,action){const groupid=this.groupid;this.busy='alert';try{const result=await this.request('risk_alert_action',{groupid,event_id:row.id,action,silence_minutes:30});if(groupid!==this.groupid)return;await this.load();this.$message.success(result.message)}catch(error){this.$message.error(error.message)}finally{this.busy=''}},
  async evaluate(){const groupid=this.groupid;this.busy='evaluate';try{await this.request('risk_evaluate',{groupid});if(groupid!==this.groupid)return;await this.load();this.$message.info('已检查并记录预警状态；未执行交易动作')}catch(error){this.$message.error(error.message)}finally{this.busy=''}},
  async saveNative(){const groupid=this.groupid;try{await this.$confirm('保存后必须人工重启当前组合进程，并核对风控参数加载日志。启用原生风控后可能降低或归零整个 CTA 组合的执行目标，实际退仓还取决于调度、执行器和通道成交。确认保存配置？','保存原生风控配置',{type:'warning',confirmButtonText:'保存，之后安排重启',cancelButtonText:'取消'});if(groupid!==this.groupid)return;this.busy='native';const result=await this.request('cmt_native_risk',{groupid,params:this.native,restart_ack:true});if(groupid!==this.groupid)return;this.native=clone(result.native.params);await this.load();this.$message.warning(result.message)}catch(error){if(error instanceof Error)this.$message.error(error.message)}finally{this.busy=''}}
 },
 watch:{groupid(){++this.serial;this.state=null;this.error='';this.load(true)}},
 mounted(){this.load(true);this.poll=setInterval(()=>{if(!this.busy)this.load(false)},5000)},
 beforeUnmount(){++this.serial;clearInterval(this.poll)}
}
</script>
<style scoped>
.risk-panel{min-height:100%;padding:20px;box-sizing:border-box;background:var(--wt-surface);color:var(--wt-text)}
.risk-header{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:18px}.risk-header h2{font-size:19px;margin:0 0 7px}.risk-header p,.risk-card p,.risk-alerts p,.risk-manual p{font-size:12px;line-height:1.8;color:var(--wt-muted);margin:0 0 14px}
.risk-status{display:flex;flex-wrap:wrap;align-items:center;gap:12px;font-size:12px;color:var(--wt-muted);margin:16px 0}
.risk-metrics{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.risk-metrics article,.risk-card{border:1px solid var(--wt-line);border-radius:8px;padding:18px;background:var(--wt-surface)}
.risk-baseline{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px}.risk-baseline>div:first-child{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.risk-baseline small{flex-basis:100%;font-size:11px;line-height:1.7;color:var(--wt-muted)}.baseline-number{font-variant-numeric:tabular-nums;font-size:22px}.baseline-actions{display:flex;gap:8px;flex-shrink:0}.baseline-actions .el-button+.el-button{margin-left:0}
.risk-metrics article{display:flex;flex-direction:column;gap:10px;min-width:0}.risk-metrics span{font-size:12px;color:var(--wt-muted)}.risk-metrics strong{font-size:25px;font-variant-numeric:tabular-nums}.risk-metrics small,.risk-native-scale small{font-size:11px;color:var(--wt-muted);line-height:1.7}.risk-metrics .risk-breached strong{color:var(--el-color-danger)}
.event-handled{display:block;font-size:10px;color:var(--wt-muted)}.engine-reference{font-size:12px;color:var(--wt-muted)}.broker-details{margin:16px 0}.risk-alerts{margin:14px 0}.risk-alerts .el-alert{margin-bottom:8px}.risk-columns{display:grid;grid-template-columns:1fr 1fr;gap:16px}.risk-card h3{margin:0 0 10px;font-size:15px}.risk-card .el-form{margin-top:16px}.risk-threshold{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px;margin:12px 0}.risk-threshold .el-input-number{width:130px}.risk-card :deep(.el-form-item__label){color:var(--wt-muted);font-size:12px}
.risk-native-scale{display:flex;flex-direction:column;gap:5px;font-size:12px;padding:12px;background:var(--wt-hover);border-radius:7px}.risk-native-fields{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:15px}.risk-native-fields label{display:flex;flex-direction:column;gap:6px;font-size:11px;color:var(--wt-muted)}.risk-native-fields .el-input-number{max-width:100%}.risk-footnote{margin-top:14px!important}.risk-manual{margin:20px 0}.risk-manual :deep(.runtime-panel){height:auto;min-height:0;padding:8px 0}.risk-events{margin-top:18px}
@media(max-width:1000px){.risk-columns{grid-template-columns:1fr}.risk-metrics{grid-template-columns:1fr 1fr}.risk-baseline{align-items:flex-start;flex-direction:column}}@media(max-width:650px){.risk-panel{padding:12px}.risk-metrics,.risk-native-fields{grid-template-columns:1fr}.risk-header{align-items:flex-start}.risk-status{gap:8px}}
</style>
