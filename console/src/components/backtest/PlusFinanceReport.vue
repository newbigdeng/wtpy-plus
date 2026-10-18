<template>
 <section class="plus-finance-report" aria-label="Plus财务结果">
  <div class="finance-cards"><article v-for="metric in metrics" :key="metric.key"><span>{{metric.label}}</span><strong :class="{'finance-danger':metric.key==='available'&&summary.available<0}">{{metric.key==='rejected_insufficient_funds'?count(summary[metric.key]):amount(summary[metric.key])}}</strong></article></div>
  <p>手续费由原生模拟柜台随成交扣取。部分成交可多次触发最低费。冻结金额随委托受理、成交与撤单更新。缺少记录的金额显示为 —。</p>
  <details><summary>本任务生效的费用、保证金与滑点</summary><dl><dt>手续费来源</dt><dd>{{finance.fees?.source||'未知'}}</dd><dt>计费方式</dt><dd>{{finance.fees?.known===false?'未知':finance.fees?.byvolume===true?'按手数（元/手）':finance.fees?.byvolume===false?'按成交金额比例':'未知'}}</dd><dt>开仓 / 平仓 / 平今</dt><dd>{{rate(finance.fees?.open)}} / {{rate(finance.fees?.close)}} / {{rate(finance.fees?.closetoday)}}</dd><dt>每次成交最低费</dt><dd>{{amount(finance.fees?.minfee)}}</dd><dt>保证金来源</dt><dd>{{finance.margin?.source||'未知'}}</dd><dt>多头 / 空头保证金比例</dt><dd>{{percent(finance.margin?.long)}} / {{percent(finance.margin?.short)}}</dd><dt>不利滑点</dt><dd>{{finance.slippage_ticks==null?'未知':finance.slippage_ticks+' 跳'}}</dd></dl></details>
 </section>
</template>
<script>
export default{
 name:'PlusFinanceReport',props:{detail:{type:Object,required:true}},
 data:()=>({metrics:[{key:'commission',label:'累计手续费'},{key:'margin',label:'已用保证金'},{key:'frozen_margin',label:'冻结保证金'},{key:'frozen_commission',label:'冻结手续费'},{key:'available',label:'可用资金'},{key:'slippage_cost',label:'累计滑点成本'},{key:'rejected_insufficient_funds',label:'资金不足拒单'}]}),
 computed:{summary(){return this.detail.summary||{}},finance(){return this.detail.summary?.finance||this.detail.task?.finance||{}}},
 methods:{amount(value){return value==null||!Number.isFinite(Number(value))?'—':Number(value).toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})},count(value){return value==null?'—':String(value)+' 次'},rate(value){return value==null?'—':String(value)},percent(value){return value==null?'—':(Number(value)*100).toFixed(2)+'%'}}
}
</script>
<style scoped>
.plus-finance-report{margin:18px 0;color:var(--wt-text)}.finance-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:10px}.finance-cards article{padding:13px;border:1px solid var(--wt-line);border-radius:7px;display:flex;flex-direction:column;gap:9px;background:var(--wt-surface)}.finance-cards span{font-size:11px;color:var(--wt-muted)}.finance-cards strong{font-size:16px;font-weight:550;font-family:var(--wt-mono);font-variant-numeric:tabular-nums}.finance-danger{color:var(--wt-danger)}.plus-finance-report p{font-size:11px;line-height:1.8;color:var(--wt-muted)}.plus-finance-report details{font-size:11px;padding:12px;border:1px solid var(--wt-line);border-radius:7px}.plus-finance-report summary{cursor:pointer}.plus-finance-report dl{display:grid;grid-template-columns:minmax(130px,1fr) 2fr;gap:9px 16px;margin:15px 0 3px}.plus-finance-report dt{color:var(--wt-muted)}.plus-finance-report dd{margin:0;overflow-wrap:anywhere}
</style>
