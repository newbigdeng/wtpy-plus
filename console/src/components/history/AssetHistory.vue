<template>
  <section class="catalog" :aria-label="name+'历史数据'">
    <header><div><h1>{{ name }}历史数据</h1><p>{{ intro[asset] }}</p></div><el-button :loading="loading" @click="load">刷新目录</el-button></header>
    <div class="filters">
      <el-input v-model="query" clearable :placeholder="placeholder" aria-label="搜索历史数据"/>
      <label v-for="facet in facets" :key="facet.key">{{ facet.label }}
        <select v-model="filters[facet.key]" :aria-label="facet.label"><option value="">全部</option><option v-for="v in choices(facet.key)" :key="v" :value="v">{{pretty(facet.key,v)}}</option></select>
      </label>
      <template v-if="asset==='option'">
        <label>最低行权价<input type="number" min="0" step="any" v-model.number="low" placeholder="不限"></label>
        <label>最高行权价<input type="number" min="0" step="any" v-model.number="high" placeholder="不限"></label>
      </template>
      <label>起始日期<input type="date" v-model="start" :max="end || undefined"></label>
      <label>结束日期<input type="date" v-model="end" :min="start || undefined"></label>
      <el-button link @click="reset" :disabled="!hasFilters">清除筛选</el-button>
    </div>
    <p v-if="error" role="alert">{{error}}</p>
    <p v-if="invalid" role="alert">{{invalid}}</p>
    <div class="count" role="status">{{loading?'正在读取目录…':filtered.length+' / '+rows.length+' 个数据集'}}<span>覆盖时间{{asset==='crypto'?' · UTC':' · 北京时间'}}</span></div>
    <el-table :data="visible" row-key="id" stripe :empty-text="empty">
      <el-table-column label="合约 / 代码" min-width="185"><template #default="{row}"><b>{{row.code}}</b><small>{{profile(row)}}</small></template></el-table-column>
      <el-table-column v-if="asset==='option'" label="标的 / 合约类型" min-width="145"><template #default="{row}">{{row.underlying || '—'}}<small>{{kind(row)}}</small></template></el-table-column>
      <el-table-column v-if="asset==='option'" label="行权价 / 到期日" min-width="140"><template #default="{row}">{{row.strike ?? '—'}}<small>{{stamp(row.expiry)}}</small></template></el-table-column>
      <el-table-column label="周期" width="96"><template #default="{row}">{{pretty('period',row.period)}}</template></el-table-column>
      <el-table-column label="实际覆盖时间" min-width="150"><template #default="{row}">{{stamp(row.start)}}<small>至 {{stamp(row.end)}}</small></template></el-table-column>
      <el-table-column label="记录数" min-width="92"><template #default="{row}">{{row.rows?.toLocaleString() ?? '—'}}</template></el-table-column>
      <el-table-column label="操作" width="76"><template #default="{row}"><el-button link @click="detail=row">详情</el-button></template></el-table-column>
    </el-table>
    <el-pagination v-if="filtered.length>size" v-model:current-page="page" :page-size="size" layout="prev, pager, next" :total="filtered.length"/>
    <el-drawer :model-value="!!detail" @close="detail=null" title="数据集详情" size="min(440px, 92vw)">
      <template v-if="detail"><h2>{{detail.code}}</h2><dl>
        <template v-for="[key,value] in facts" :key="key"><dt>{{key}}</dt><dd>{{value}}</dd></template>
      </dl><p v-if="detail.error" role="alert">{{detail.error}}</p>
      <details v-if="detail.dates?.length"><summary>有记录的交易日 · {{detail.dates.length}} 天</summary><p class="days">{{detail.dates.map(stamp).join('　')}}</p></details>
      </template>
    </el-drawer>
  </section>
</template>
<script setup>
import {computed,onMounted,reactive,ref,watch} from 'vue'
const props=defineProps({asset:String,name:String})
const intro={option:'按标的、方向和行权价查找已采集或导入的合约。标的期货单独标记。',stock:'按市场、证券代码和周期查找股票工作区的历史数据。',crypto:'查找币对的逐笔成交和分钟线。分钟数含首尾时段，回测只使用完整分钟。'}
const periods={d:'日线',m1:'1 分钟',m5:'5 分钟',tick:'Tick 快照',trades:'逐笔成交'}
const order=['d','m5','m1','tick','trades']
const placeholder=computed(()=>({option:'搜索合约、标的或行权价',stock:'搜索证券代码或市场',crypto:'搜索币对，如 BTC-USD'})[props.asset])
const rows=ref([]),loading=ref(true),error=ref(''),query=ref(''),start=ref(''),end=ref(''),detail=ref(null),page=ref(1),size=25
const low=ref(''),high=ref('')
const filters=reactive({venue:'',period:'',profile:'',underlying:'',kind:'',expiry:''})
const facets=computed(()=>[['venue',props.asset==='stock'?'市场':'交易所'],['period','周期'],['profile','来源'],...(props.asset==='option'?[['underlying','标的期货'],['kind','合约类型'],['expiry','到期月份']]:[])].map(([key,label])=>({key,label})))
const profile=r=>r.profileid==='local'?'本地工作区':r.profileid?.startsWith('expiry-')?'历史导入 · '+r.profileid.slice(7):r.profileid || r.source || ''
const kind=r=>r.leg_type==='future'?'标的期货':({C:'看涨',P:'看跌'})[r.side] || '未标注'
const day=v=>String(v || '').slice(0,8)
function stamp(v){const s=String(v||'');return /^\d{8}/.test(s)?s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6,8)+(s.length>=12?' '+s.slice(8,10)+':'+s.slice(10,12):''):'—'}
function field(r,k){if(k==='profile')return profile(r);if(k==='kind')return kind(r);if(k==='venue')return r.code?.split('.')[0];if(k==='expiry')return day(r.expiry).slice(0,6);return r[k]}
function pretty(k,v){return k==='period'?periods[v]||v:k==='expiry'?v.slice(0,4)+'-'+v.slice(4):v}
function choices(k){return [...new Set(rows.value.map(r=>field(r,k)).filter(Boolean))].sort()}
const invalid=computed(()=>start.value&&end.value&&start.value>end.value?'起始日期不能晚于结束日期。':low.value!==''&&high.value!==''&&low.value>high.value?'最低行权价不能超过最高行权价。':'')
const hasFilters=computed(()=>Boolean(query.value||start.value||end.value||low.value!==''||high.value!==''||Object.values(filters).some(Boolean)))
const filtered=computed(()=>{
  if(invalid.value)return []
  const terms=query.value.trim().toLowerCase().split(/\s+/).filter(Boolean),a=start.value.replaceAll('-',''),b=end.value.replaceAll('-','')
  return rows.value.filter(r=>{
    const search=[r.code,r.label,r.underlying,r.product,r.strike,r.name,kind(r),r.code?.replace(/[.-]/g,'')].join(' ').toLowerCase()
    if(!terms.every(t=>search.includes(t)||search.includes(t.replace(/[.-]/g,''))&&/[a-z0-9]/i.test(t)))return false
    if(!facets.value.every(({key})=>!filters[key]||field(r,key)===filters[key]))return false
    if(low.value!==''||high.value!==''){
      if(r.strike==null||(low.value!==''&&r.strike<low.value)||(high.value!==''&&r.strike>high.value))return false
    }
    if(!a&&!b)return true
    if(r.dates?.length)return r.dates.some(d=>(!a||day(d)>=a)&&(!b||day(d)<=b))
    return r.start&&r.end&&(!a||day(r.end)>=a)&&(!b||day(r.start)<=b)
  }).sort((x,y)=>x.code.localeCompare(y.code,undefined,{numeric:true})||order.indexOf(x.period)-order.indexOf(y.period))
})
watch(filtered,()=>{page.value=1})
const visible=computed(()=>filtered.value.slice((page.value-1)*size,page.value*size))
const empty=computed(()=>loading.value?'正在读取目录…':error.value?'目录未能读取，请刷新重试。':rows.value.length?'没有匹配的数据，请调整筛选条件。':'当前'+props.name+'工作区还没有历史数据。')
const facts=computed(()=>{const r=detail.value;if(!r)return [];return [['来源',profile(r)],['周期',pretty('period',r.period)],['覆盖时间',stamp(r.start)+' 至 '+stamp(r.end)],['记录数',r.rows?.toLocaleString() ?? '—'],...(props.asset==='option'?[['标的',r.underlying],['类型',kind(r)],['乘数',r.multiplier],['行权价',r.strike],['到期日',stamp(r.expiry)]]:[])].map(([k,v])=>[k,v??'—'])})
function reset(){query.value=start.value=end.value=low.value=high.value='';for(const k in filters)filters[k]=''}
async function load(){loading.value=true;error.value='';try{
 const res=await fetch('/mgr/assets/'+props.asset+'/history',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
 const data=await res.json();if(!res.ok||data.result!==0)throw Error(data.message||'读取历史目录失败')
 rows.value=data.datasets;detail.value=null
}catch(e){error.value=e.message}finally{loading.value=false}}
onMounted(load)
</script>
<style scoped>
.catalog{height:100%;overflow:auto;box-sizing:border-box;padding:24px clamp(14px,2vw,32px);color:var(--wt-text)}header,.count{display:flex;justify-content:space-between;align-items:center;gap:16px}h1{font-size:20px;margin:0}p,small,.count,dt{color:var(--wt-muted);font-size:12px}small{display:block}.filters{display:flex;flex-wrap:wrap;gap:10px 14px;align-items:end;margin:20px 0 14px}.filters .el-input{width:min(240px,100%)}label{display:grid;gap:4px;font-size:12px;color:var(--wt-muted)}select,input{box-sizing:border-box;max-width:220px;height:32px;padding:0 8px;background:var(--wt-surface);border:1px solid var(--wt-line);border-radius:3px;color:var(--wt-text)}.count{padding:10px 0;border-top:1px solid var(--wt-line)}.el-pagination{margin:12px 0}dl{display:grid;grid-template-columns:85px 1fr;gap:12px}dd{margin:0}.days{line-height:2}summary{cursor:pointer}
</style>
