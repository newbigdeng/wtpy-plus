<template><section class="main-panel">
<h3>{{preview?'主力合约时间表':'品种连续回测'}}</h3>
<p>{{preview?'颜色区分不同的主力月份，日期为实际生效交易日。':'单个区间最多两年。可选逐 K 回放或真实 Tick 撮合。连续价格用于信号，平旧开新使用真实合约原价。'}}</p>
<form @submit.prevent="launch"><label>交易所与品种<input v-model.trim="product" placeholder="CZCE.TA" required></label>
<label v-if="asset==='option'">起始期权<input v-model.trim="option" placeholder="DCE.m2609-C-2900" required></label>
<label>开始交易日<input type="date" v-model="start" required></label><label>结束交易日<input type="date" :max="yesterday" v-model="end" required></label>
<template v-if="!preview"><label>成交模型<select v-model="model"><option value="bars">收盘 K 线</option><option v-if="dataset && asset==='option'" value="option_tick">期权 · 真实 Tick 盘口</option><option v-if="dataset && asset==='futures'" value="plus_tick">Plus · 真实 Tick</option></select></label><label v-if="model==='plus_tick'">模拟保证金比例<input type="number" v-model.number="margin" min="0.001" max="1" step="0.001"></label><label>信号周期<select v-model="period"><option value="m15">15 分钟</option><option value="d1">日线</option></select></label>
<template v-if="model==='option_tick'"><label>期权盘口撮合<select v-model="fill"><option value="causal_touch">报价触达</option><option value="volume_limited">限制成交参与率</option></select></label><label>延迟事件数<input type="number" v-model.number="delay" min="0" max="2147483647" required></label><label v-if="fill==='volume_limited'">成交参与率（%）<input type="number" v-model.number="participation" min="0.01" max="100" step="0.01" required></label></template>
<label>每手每边手续费（元）<input v-model.number="fee" type="number" min="0" step="any" required></label>
<label>初始资金<input v-model.number="capital" type="number" min="1" required></label></template>
<button :disabled="busy||(!preview&&!strategy)">{{busy?'处理中…':preview?'查询主力区间':'准备历史并启动回测'}}</button></form>
<p v-if="asset==='option'">换月保留看涨 / 看跌方向，以前一交易日收盘价匹配最接近的相对行权价。</p>
<p v-if="product==='CZCE.TA'">月份观察示例：TA2705 → TA2709（3 月中旬）、TA2709 → TA2801（7 月中旬）、TA2801 → TA2805（11 月中旬）。实测区间按前一交易日持仓量判定。</p>
<p role="status">{{message}}</p><div class="intervals"><button type="button" @click="emit('select-interval',{product:productShown,segment:s})" v-for="(s,i) in intervals" :key="s.start" :style="{'--tint':palette[i%palette.length]}"><b>{{s.code || (productShown+"."+s.month)}}</b> {{s.start}} — {{s.end}} · 系数 {{Number(s.factor).toFixed(4)}}<span v-if="s.old_close"> · {{s.prior}} 旧收 {{s.old_close}} / 新收 {{s.new_close}}</span><span v-if="s.target_strike"> · 目标行权价 {{s.target_strike.toFixed(2)}} → {{s.strike}}</span></button></div>
<router-link v-if="!preview && done" :to="{name:'run-report',query:{asset,mode:resultMode,task:done}}">查看本次报告</router-link>
</section></template>
<script setup>
import {onBeforeUnmount,ref,watch} from 'vue'
const emit=defineEmits(['calendar','select-interval']);const productShown=ref('')
const props=defineProps({asset:{type:String,default:'futures'},preview:Boolean,dataset:String,strategy:String,version:Number,symbol:String,initialCapital:Number,params:String})
const dateString=d=>d.toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}),yesterday=dateString(new Date(Date.now()-86400000))
const product=ref('CZCE.TA'),option=ref(''),start=ref(dateString(new Date(Date.now()-91*86400000))),end=ref(yesterday),period=ref('d1'),capital=ref(props.initialCapital||100000),fee=ref(1)
watch(()=>props.symbol,s=>{const m=s?.match(/^([A-Z]+)\.([A-Za-z]+)/);if(m)product.value=m[1]+'.'+m[2];if(props.asset==='option'&&s)option.value=s},{immediate:true})
const fill=ref('causal_touch'),delay=ref(0),participation=ref(10)
const model=ref('bars'),margin=ref(.12),resultMode=ref('main')
const busy=ref(false),message=ref(''),intervals=ref([]),done=ref(''),palette=['#247b66','#a05f2d','#4565a6','#88467c']
let timer,active=0
async function post(action,body){const res=await fetch('/mgr/assets/'+props.asset+'/main-'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await res.json();if(d.result!==0)throw Error(d.message);return d}
async function launch(){
 const ticket=++active;productShown.value=product.value;busy.value=true;done.value='';intervals.value=[];message.value='正在读取本地行情，缺少的历史按合约补取…'
 try{
  const d=await post('run',{strategy:props.strategy||'',version:props.version,params:JSON.parse(props.params||'{}'),product:product.value,option:option.value,start:start.value,end:end.value,model:model.value,fill_model:fill.value,event_delay:model.value==='option_tick'?delay.value:0,participation_rate:model.value==='option_tick'&&fill.value==='volume_limited'?participation.value/100:0,margin:margin.value,dataset:props.dataset||'',period:period.value,capital:capital.value,fee:fee.value,preview:props.preview})
  async function poll(){
   if(ticket!==active)return
   try{const r=await post('detail',{task:d.task.id});if(ticket!==active)return
    if(r.task.status==='running'){timer=setTimeout(poll,2000);return}
    busy.value=false;intervals.value=r.segments;emit('calendar',{product:productShown.value,segments:r.segments});message.value=r.task.error||'已完成 · 根据前一交易日持仓量选择，自换月起向后复权'
    if(r.task.status==='completed'){done.value=r.task.id;resultMode.value=r.task.plus_id?'plus':'main'}
   }catch(e){if(ticket===active){message.value=e.message;busy.value=false}}
  }
  await poll()
 }catch(e){if(ticket===active){busy.value=false;message.value=e.message}}
}
watch(()=>props.asset,()=>{active++;clearTimeout(timer);busy.value=false;intervals.value=[];done.value="";message.value=""})
onBeforeUnmount(()=>{active++;clearTimeout(timer)})
</script>
<style scoped>.main-panel{border:1px solid var(--wt-line);border-radius:6px;margin:14px 0;padding:14px}.main-panel h3{margin:0}.main-panel p{font-size:12px;color:var(--wt-muted)}form,.intervals{display:flex;gap:10px;align-items:end;flex-wrap:wrap}label{display:grid;gap:4px;font-size:12px}input,select,button{font:inherit;background:var(--wt-surface);color:inherit;border:1px solid var(--wt-line);border-radius:3px;padding:6px}.intervals button{padding:7px;border-left:4px solid var(--tint);font-size:12px}.intervals b{color:var(--tint)}</style>
