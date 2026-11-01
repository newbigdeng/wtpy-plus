<template><section class="factor-charts">
  <h3>IC 时序与净值</h3>
  <p>IC 衡量这个合约的因子值与后续收益的相关性。Rank IC 先对两列分别排名。每个点取一个不重叠窗口，横轴是最后一笔收益已知的时间。</p>
  <label>每窗有效配对数 <select v-model.number="window"><option v-for="n in [5,20,60,120]" :key="n">{{n}}</option></select></label>
  <p role="status">{{error || (busy?'读取完整逐点流水…':'')}}</p>
  <template v-if="data"><p>{{data.pairs}} 个有效配对，{{data.blocks.length}} 个完整窗口，末尾 {{data.remainder}} 个配对未满一窗。常数列的相关系数留空。</p>
    <template v-if="data.blocks.length">
      <TerminalChart class="plot" :plot="icPlot" label="分窗 IC 与 Rank IC"/>
      <h4>IC 综合统计</h4>
      <table><thead><tr><th>指标</th><th>有效窗口</th><th>均值</th><th>样本标准差</th><th>ICIR¹</th><th>正值占比</th></tr></thead><tbody>
      <tr v-for="(s,key) in data.stats" :key="key"><th>{{key==='ic'?'IC':'Rank IC'}}</th><td>{{s.n}}</td><td>{{fmt(s.avg)}}</td><td>{{fmt(s.sd)}}</td><td>{{fmt(s.ir)}}</td><td>{{s.positive==null?'—':(s.positive*100).toFixed(1)+'%'}}</td></tr></tbody></table>
      <small>¹ 分窗均值除以样本标准差，未年化。</small>
      <TerminalChart class="plot" :plot="hist" label="IC 和 Rank IC 的分布"/>
    </template><p v-else>有效配对不足一窗，可减小窗口或扩大实验区间。</p>
    <h4>信号模拟净值</h4><p>从 1 开始，因子为正做多、为负做空、为零空仓。下一根收盘进入，持有实验设定的根数。各笔持有期不重叠，基准在相同区间做多。两线均为未扣费的诊断曲线。</p>
    <TerminalChart v-if="data.nav.length" class="plot" :plot="navPlot" label="因子信号与同期做多基准净值"/><p v-else>没有完整的持有区间。</p>
  </template>
</section></template>
<script setup>
import {ref,computed,watch,onBeforeUnmount} from 'vue'
import TerminalChart from '../terminal/TerminalChart.vue'
const props=defineProps({id:String}),window=ref(20),data=ref(null),busy=ref(false),error=ref('')
let worker,seq=0
function request(){if(!worker)return;busy.value=true;error.value='';data.value=null;worker.postMessage({id:props.id,window:window.value,sequence:++seq})}
watch(()=>props.id,()=>{
  worker?.terminate()
  worker=new Worker(new URL('./factorCharts.worker.js',import.meta.url),{type:'module'})
  worker.onmessage=({data:r})=>{if(r.sequence===seq){data.value=r.data;error.value=r.error;busy.value=false}}
  worker.onerror=()=>{error.value='无法运行因子图表计算';busy.value=false}
  request()
},{immediate:true})
watch(window,request)
onBeforeUnmount(()=>worker?.terminate())
function fmt(x){return x==null?'—':x.toFixed(3)}
const icPlot=computed(()=>({labels:data.value.blocks.map(x=>x.time),axis:{min:-1,max:1},series:['ic','rank'].map((k,i)=>({name:i?'Rank IC':'IC',showSymbol:true,data:data.value.blocks.map(b=>b[k])}))}))
const hist=computed(()=>({labels:Array.from({length:10},(_,i)=>((i-5)/5).toFixed(1)+' ~ '+((i-4)/5).toFixed(1)),series:['ic','rank'].map((k,i)=>({name:i?'Rank IC 窗口数':'IC 窗口数',type:'bar',data:data.value.stats[k].histogram}))}))
const navPlot=computed(()=>({labels:data.value.nav.map(x=>x.time),series:['signal','benchmark'].map((k,i)=>({name:i?'同期做多':'因子信号',data:data.value.nav.map(b=>b[k])}))}))
</script>
<style scoped>.plot{height:255px;width:100%}.factor-charts p,small{color:var(--wt-muted);line-height:1.7}td,th{padding:5px 9px;text-align:right}</style>
