<template>
 <section class="trade-risk" aria-label="柜台资金与交易风险">
  <header><div><h3>柜台资金与交易风险</h3><p>读取通道资金快照与拒单记录，不执行交易动作。</p></div><el-button size="small" :loading="loading" @click="load">刷新柜台风险</el-button></header>
  <el-alert v-if="error" :title="error" type="error" :closable="false"/>
  <template v-if="state">
   <div class="trade-alerts" aria-live="polite"><el-alert v-for="(alert,index) in state.alerts||[]" :key="index" :type="alert.severity==='danger'||alert.severity==='error'?'error':alert.severity==='info'?'info':'warning'" :title="alert.message" :closable="false" show-icon/></div>
   <div class="trade-accounts">
    <article v-for="account in state.accounts||[]" :key="account.channel+':'+account.currency" class="trade-account" :class="{'is-stale':account.stale}">
     <div class="account-title"><strong>{{account.channel}} · {{account.currency}}</strong><el-tag :type="account.valid?'success':'warning'" size="small" effect="plain">{{account.valid?'资金回执有效':account.reason||'资金不可确认'}}</el-tag></div>
     <dl><template v-for="metric in metrics" :key="metric.key"><dt>{{metric.label}}</dt><dd :class="{'is-danger':metric.key==='available'&&account.valid&&account.available<0}">{{account.valid?amount(account[metric.key]):'不可计算'}}</dd></template><dt>保证金 / 权益</dt><dd>{{ratio(account.margin_ratio)}}</dd></dl>
     <small>交易日 {{account.trading_date||'—'}} · {{time(account.updated_at)}}</small>
     <small v-if="!account.valid">{{account.reason}}。历史金额可在下方展开。</small>
     <details v-if="!account.valid" class="historical-account"><summary>上次回执金额（历史参考）</summary><dl><template v-for="metric in metrics" :key="metric.key"><dt>{{metric.label}}</dt><dd>{{amount(account[metric.key])}}</dd></template></dl></details>
     <el-button v-if="showQuery&&state.can_query" size="small" :loading="querying===account.channel" :disabled="Boolean(querying)" @click="queryFunds(account.channel)">立即读取柜台</el-button>
    </article>
   </div>
   <p v-if="!(state.accounts||[]).length" class="trade-note">尚无柜台资金快照。缺失金额不按零处理。</p>
   <details class="trade-fee"><summary>合约手续费配置 · {{code||state.fee_template?.code||'未选择合约'}}</summary><p>{{state.fee_template?.source||'未找到手续费配置'}}</p><dl v-if="state.fee_template?.known"><dt>计费方式</dt><dd>{{state.fee_template.byvolume?'按手数（元/手）':'按成交金额比例'}}</dd><dt>开仓</dt><dd>{{feeValue(state.fee_template.open)}}</dd><dt>平仓</dt><dd>{{feeValue(state.fee_template.close)}}</dd><dt>平今</dt><dd>{{feeValue(state.fee_template.closetoday)}}</dd><dt>最低手续费配置</dt><dd>{{amount(state.fee_template.minfee)}}</dd></dl><p v-else>费率未知。通道累计手续费仍以柜台资金快照为准。</p><p>这是 WT 配置估算，不能代替期货公司实际费率。</p></details>
   <div class="trade-rejections"><h4>最近拒单</h4><el-table :data="state.rejections||[]" size="small" max-height="230" empty-text="暂无结构化拒单记录；不代表所有订单均能成交。"><el-table-column label="时间" min-width="145"><template #default="{row}">{{rejectionTime(row)}}</template></el-table-column><el-table-column prop="channel" label="通道" min-width="100"/><el-table-column prop="code" label="合约" min-width="120"/><el-table-column prop="error_code" label="WT 分类码" width="95"/><el-table-column prop="broker_error_code" label="柜台错误码" width="100"/><el-table-column label="原因" min-width="230"><template #default="{row}"><span :class="{'is-danger':row.category==='insufficient_funds'}">{{row.message||'柜台未提供原因'}}</span></template></el-table-column></el-table></div>
   <p class="trade-note">保证金不足由柜台拒单回报确认。可用资金大于零，也不保证下一笔委托成功；报单受理不等于成交。</p>
  </template>
 </section>
</template>
<script>
export default{
 name:'TradeRiskPanel',props:{groupid:{type:String,required:true},code:{type:String,default:''},autoRefresh:{type:Boolean,default:true},showQuery:{type:Boolean,default:true}},
 data:()=>({state:null,error:'',loading:false,querying:'',serial:0,metrics:[{key:'equity',label:'柜台动态权益'},{key:'available',label:'可用资金'},{key:'margin',label:'已用保证金'},{key:'fee',label:'累计手续费'},{key:'frozen_margin',label:'冻结保证金'},{key:'frozen_commission',label:'冻结手续费'}]}),
 methods:{
  amount(value){return value==null||!Number.isFinite(Number(value))?'—':Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})},
  feeValue(value){return value==null?'—':String(value)},
  ratio(value){return value==null||!Number.isFinite(Number(value))?'—':(Number(value)*100).toFixed(2)+'%'},
  time(value){return value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'快照时间未知'},
  rejectionTime(row){const value=Number(row.time);return Number.isFinite(value)&&value>=1e12&&value<1e13?this.time(value):[row.date,row.time].filter(value=>value!=null&&value!=='').join(' ')||'—'},
  async load(){const serial=++this.serial;this.loading=true;try{const response=await fetch('/mgr/traderisk',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupid:this.groupid,code:this.code||''})});const result=await response.json();if(!response.ok||result.result<0)throw new Error(result.message||'柜台风险读取失败');if(serial===this.serial){this.state=result;this.error=''}}catch(error){if(serial===this.serial)this.error=error.message}finally{if(serial===this.serial)this.loading=false}},
  async queryFunds(channel){this.querying=channel;try{const response=await fetch('/mgr/query_trade_funds',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupid:this.groupid,channel})});const result=await response.json();if(!response.ok||result.result<0)throw new Error(result.message||'柜台查询失败');await this.load();this.$message.success(result.message)}catch(error){this.$message.error(error.message)}finally{this.querying=''}}
 },
 watch:{groupid(){++this.serial;this.state=null;this.error='';this.load()},code(){this.load()}},
 mounted(){this.load();this.poll=setInterval(()=>{if(this.autoRefresh&&!this.loading&&!document.hidden)this.load()},5000)},
 beforeUnmount(){++this.serial;clearInterval(this.poll)}
}
</script>
<style scoped>
.trade-risk{width:100%;box-sizing:border-box;padding:14px 0;color:var(--wt-text)}.trade-risk header{display:flex;justify-content:space-between;gap:12px;align-items:center;margin-bottom:12px}.trade-risk h3{font-size:14px;margin:0 0 5px;font-weight:550}.trade-risk h4{font-size:12px;font-weight:550;margin:14px 0 8px}.trade-risk p,.trade-note{font-size:11px;color:var(--wt-muted);line-height:1.8;margin:0}.trade-alerts .el-alert{margin-bottom:8px}.trade-accounts{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:12px}.trade-account{border:1px solid var(--wt-line);border-radius:8px;padding:14px;background:var(--wt-surface)}.account-title{display:flex;align-items:center;justify-content:space-between;gap:10px;font-size:12px}.account-title strong{font-weight:550}.trade-account dl,.trade-fee dl{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;font-size:11px;margin:14px 0}.trade-account dt,.trade-fee dt{color:var(--wt-muted)}.trade-account dd,.trade-fee dd{margin:0;text-align:right;font-family:var(--wt-mono)}.trade-account small{display:block;font-size:10px;line-height:1.8;color:var(--wt-muted)}.is-stale .account-title strong{color:var(--wt-muted)}.trade-fee{font-size:11px;border:1px solid var(--wt-line);border-radius:8px;padding:12px;margin-top:12px}.trade-fee summary{cursor:pointer}.trade-fee p{margin-top:8px}.trade-note{margin-top:10px!important}.is-danger{color:var(--wt-danger)}.trade-rejections :deep(.el-table){font-size:11px}@media(max-width:650px){.trade-risk header{align-items:flex-start}.trade-accounts{grid-template-columns:1fr}}
</style>
