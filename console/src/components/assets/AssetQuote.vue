<template>
  <section class="asset-quote">
    <h2>{{ names[asset] }}行情</h2>
    <AssetShortcuts v-if="asset==='crypto'||asset==='option'" :asset="asset" :group="$route.query.group" @pick="shortcut"/>
    <form @submit.prevent="view">
      <label v-if="asset==='option'||asset==='futures'">{{asset==='option'?'看盘模式':'行情环境'}}
        <select v-model="mode" @change="switchMode"><option v-if="asset==='option'" value="history">历史看盘</option><option value="session">CTP 交易时段行情</option><option value="simulation">CTP 7×24 仿真回放</option></select>
      </label>
      <label v-if="asset==='option'">期权合约 <OptionPicker v-model="draft" label="期权合约" placeholder="输入 DCE、品种或合约代码" @pick="selectContract"/></label>
      <label v-else>交易所代码 <input v-model.trim="draft" :placeholder="examples[asset]" required maxlength="42"></label>
      <button>查看行情</button><button v-if="symbol" type="button" @click="stop">停止</button>
    </form>
    <p v-if="asset==='option'" class="hint">输入交易所或品种，按 Tab 选中当前候选。休市也能看日 K、分钟 K 和历史估值；CTP 模式另接实时快照。</p>
    <p v-else class="hint">输入一个标的开始接收快照。切换标的或离开页面会停止接收。</p>
    <p role="status">{{ symbol }} {{ status }}</p>
    <dl v-if="quote">
      <div v-for="[key,title] in fields" :key="key"><dt>{{title}}</dt><dd>{{quote[key] ?? '—'}}</dd></div>
    </dl>
    <OptionDaily v-if="asset==='option' && symbol" :symbol="symbol"/>
    <OptionChain v-if="asset==='option'" ref="optionChain" :mode="mode==='history'?'session':mode" @analyze="stop" @view="selectContract"/>
  </section>
</template>
<script setup>
import {ref, onMounted, onBeforeUnmount} from 'vue'
import OptionChain from './OptionChain.vue'
import OptionPicker from './OptionPicker.vue'
import OptionDaily from './OptionDaily.vue'
import AssetShortcuts from './AssetShortcuts.vue'
const props=defineProps({asset:{type:String,required:true}})
const examples={stock:'SSE.600000',futures:'SHFE.rb2701',option:'DCE.m2701-C-3000',crypto:'BTC-USD'}
const names={stock:'股票',futures:'期货',option:'商品期权',crypto:'加密货币'}
const fields=[['last','最新成交'],['bid','买一'],['ask','卖一'],['time','源端行情时间'],['trading_day','交易日'],['source','行情源']]
const optionChain=ref(null)
const mode=ref(props.asset==='option'?'history':'session'),draft=ref(''), symbol=ref(''), quote=ref(null), status=ref('')
let request, timer
function shortcut(code){
  if(props.asset==='option')optionChain.value?.openChain(code)
  else{draft.value=code;view()}
}
function cancel(){
  clearTimeout(timer)
  request?.abort()
  request=null
}
function stop(){cancel();symbol.value='';quote.value=null;status.value='已停止'}
function selectContract(code){draft.value=code;view()}
function switchMode(){optionChain.value?.stop();cancel();quote.value=null;status.value='';if(symbol.value && mode.value!=='history')refresh()}
function view(){
  optionChain.value?.stop()
  cancel();symbol.value=draft.value.trim();quote.value=null;status.value=''
  if(symbol.value && mode.value!=='history') refresh()
}
async function refresh(){
  if(document.hidden || !symbol.value)return
  const controller=new AbortController()
  request=controller
  status.value='正在连接行情源…'
  try{
    const response=await fetch('/mgr/assets/'+props.asset+'/quote',{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({symbol:symbol.value,mode:mode.value}),signal:controller.signal})
    if(response.headers.get('content-type')?.includes('ndjson')){
      const reader=response.body.pipeThrough(new TextDecoderStream()).getReader()
      let buffer=''
      while(true){
        const {done,value}=await reader.read()
        if(done)throw new Error('行情连接已关闭')
        if(request!==controller)return
        buffer+=value
        const lines=buffer.split('\n');buffer=lines.pop()
        for(const line of lines.filter(Boolean)){
          const event=JSON.parse(line)
          if(event.error)throw new Error(event.error)
          if(event.status){quote.value=null;status.value=event.status}
          if(event.source){quote.value=event;status.value='已收到快照'}
        }
      }
    }
    const result=await response.json()
    if(request!==controller)return
    if(result.result!==0)throw new Error(result.message || '行情请求失败')
    if(result.client_feed==='coinbase'){
      const remote=await fetch('https://api.exchange.coinbase.com/products/'+encodeURIComponent(result.product)+'/ticker',
        {signal:controller.signal,cache:'no-store',credentials:'omit',referrerPolicy:'no-referrer'})
      if(!remote.ok)throw new Error('浏览器无法连接 Coinbase 行情源')
      const data=await remote.json()
      if(request!==controller)return
      quote.value={source:'Coinbase Exchange · 浏览器直连',last:data.price,bid:data.bid,ask:data.ask,time:data.time}
    }else{quote.value=result.quote}
    status.value='已收到快照'
    timer=setTimeout(refresh,5000)
  }catch(error){
    if(request===controller){
      controller.abort();quote.value=null;status.value=error.message
      // A bounded retry also recovers transient provider or front-end failures.
      timer=setTimeout(refresh,15000)
    }
  }
}
function visibility(){
  cancel()
  if(mode.value==='history')return
  if(document.hidden){quote.value=null;status.value='页面已隐藏，暂停接收'}
  else if(symbol.value)refresh()
}
onMounted(()=>document.addEventListener('visibilitychange',visibility))
onBeforeUnmount(()=>{cancel();document.removeEventListener('visibilitychange',visibility)})
</script>
<style scoped>
.asset-quote{padding:18px 22px;overflow:auto;color:var(--wt-text)}
h2{font-size:19px;margin:0 0 16px}form,dl{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
input,button,select{background:var(--wt-surface);color:inherit;border:1px solid var(--wt-line);padding:7px 10px;border-radius:4px}button{cursor:pointer}
.hint,dt{color:var(--wt-muted);font-size:12px}dd{margin:5px 0;font-variant-numeric:tabular-nums}
dl>div{padding:10px 15px;border-left:2px solid var(--wt-line)}
</style>
