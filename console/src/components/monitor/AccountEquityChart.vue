<template>
 <section class="account-equity" aria-label="账户权益曲线">
  <header><strong>{{mode==='account'?'账户权益曲线':'组合净损益曲线'}}</strong><el-button size="small" text @click="expanded=true">展开曲线</el-button></header>
  <div class="equity-controls">
   <el-select v-model="mode" size="small" aria-label="权益曲线口径"><el-option value="account" label="柜台动态权益"/><el-option value="portfolio" label="组合净损益（不含本金）"/></el-select>
   <el-select v-if="mode==='account'&&accounts.length>1" v-model="accountKey" size="small" aria-label="权益账户币种"><el-option v-for="item in accounts" :key="key(item)" :value="key(item)" :label="item.channel+' · '+item.currency"/></el-select>
   <el-select v-model="range" size="small" aria-label="权益曲线时间范围"><el-option value="today" label="今日"/><el-option value="week" label="近 7 天"/><el-option value="all" label="全部采样"/></el-select>
  </div>
  <div class="equity-summary"><strong>{{amount(latestValue)}}</strong><span>{{mode==='account'?(account?.currency||'—'):'净损益'}}</span><small v-if="rows.length>1">区间变化 <b :class="delta>=0?'up':'down'">{{delta>=0?'+':''}}{{amount(delta)}}</b></small></div>
  <p class="equity-status" :class="{warning:mode==='account'&&!currentAccount}" role="status">{{status}}</p>
  <div class="equity-canvas"><div ref="chart" class="equity-chart"/><div v-if="!rows.length" class="equity-empty">{{emptyMessage}}</div></div>
  <small class="equity-source">{{source}}</small>
  <el-dialog v-model="expanded" :title="mode==='account'?'账户权益曲线':'组合净损益曲线（不含本金）'" width="92%" top="6vh" append-to-body @opened="paintExpanded" @closed="disposeExpanded">
   <div class="expanded-summary"><strong>{{amount(latestValue)}} {{mode==='account'?(account?.currency||''):''}}</strong><span>{{status}}</span><el-select v-model="range" size="small" aria-label="展开权益曲线时间范围"><el-option value="today" label="今日"/><el-option value="week" label="近 7 天"/><el-option value="all" label="全部采样"/></el-select></div>
   <div class="expanded-canvas"><div ref="expandedChart" class="expanded-chart"/><div v-if="!rows.length" class="equity-empty">{{emptyMessage}}</div></div>
   <p class="equity-source">{{source}}</p>
  </el-dialog>
 </section>
</template>
<script>
import {markRaw} from 'vue'
export default{
 name:'AccountEquityChart',
 props:{accounts:{type:Array,default:()=>[]},portfolio:{type:Array,default:()=>[]},clock:{type:Number,default:()=>Date.now()}},
 data:()=>({mode:'account',accountKey:'',range:'all',expanded:false,chart:null,largeChart:null}),
 computed:{
  account(){return this.accounts.find(item=>this.key(item)===this.accountKey)||this.accounts[0]},
  points(){return (this.mode==='account'?(this.account?.history||[]):this.portfolio).filter(row=>row.time&&Number.isFinite(this.mode==='account'?row.equity:row.pnl))},
  rows(){if(this.range==='all')return this.points;const date=new Date(this.clock+8*3600000),today=Date.UTC(date.getUTCFullYear(),date.getUTCMonth(),date.getUTCDate())-8*3600000,from=this.range==='today'?today:today-6*86400000;return this.points.filter(row=>this.timestamp(row)>=from)},
  latestValue(){return this.mode==='account'?this.account?.equity:this.points.at(-1)?.pnl},
  currentAccount(){return this.account?.valid&&!this.account.stale&&this.account.updated_at&&this.clock-this.account.updated_at<=300000},
  delta(){return this.rows.length>1?this.value(this.rows.at(-1))-this.value(this.rows[0]):null},
  status(){if(this.mode!=='account')return '组合账本 · '+this.rows.length+' 个点';if(!this.account)return '尚无柜台资金回执';const timestamp=this.account.updated_at,aged=this.account.stale||timestamp&&this.clock-timestamp>300000;return (this.currentAccount?'柜台回执有效':(aged?'柜台快照过期':this.account.reason||'资金状态未知'))+' · '+this.formatTime(timestamp)},
  emptyMessage(){return this.points.length?'该时间范围暂无已保存的采样点':'尚无权益采样。收到柜台资金回执后显示；一个回执显示一个点。'},
  source(){if(this.mode!=='account')return '来自组合资金账本，不含默认本金。';return '权益 = 余额 + 浮动盈亏 · '+this.rows.length+' / '+this.points.length+' 个采样点。仅保存已读取的回执，刷新旧回执不增加点。'+(this.account?.legacy_balance_samples?'旧版仅记录余额的 '+this.account.legacy_balance_samples+' 个点已保留归档，不混入权益曲线。':'')}
 },
 methods:{
  open(){this.expanded=true},
  key(account){return account.channel+':'+account.currency},
  amount(value){return value==null||!Number.isFinite(value)?'—':value.toLocaleString('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2})},
  formatTime(value){return value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false}):'回执时间未知'},
  timestamp(row){if(this.mode==='account')return Number(row.time);const date=String(row.time);return Date.parse(date.slice(0,4)+'-'+date.slice(4,6)+'-'+date.slice(6,8)+'T00:00:00+08:00')},
  value(row){return this.mode==='account'?row.equity:row.pnl},
  css(name){return getComputedStyle(document.documentElement).getPropertyValue(name).trim()},
  draw(chart){
   if(!this.rows.length){chart.clear();return}
   const text=this.css('--wt-muted'),grid=this.css('--wt-chart-grid'),color=this.css('--wt-chart-line')||'#6995bc',single=this.rows.length===1;
   chart.setOption({animation:false,backgroundColor:'transparent',grid:{left:60,right:18,top:18,bottom:48},tooltip:{trigger:'axis',backgroundColor:this.css('--wt-surface'),borderColor:grid,textStyle:{color:this.css('--wt-text'),fontSize:11}},xAxis:{type:'category',boundaryGap:false,data:this.rows.map(row=>this.mode==='account'?this.formatTime(row.time):String(row.time)),axisLine:{lineStyle:{color:grid}},axisTick:{show:false},axisLabel:{color:text,fontSize:9,hideOverlap:true}},yAxis:{type:'value',scale:true,axisLabel:{color:text,fontSize:9,formatter:value=>Math.abs(value)>=10000?(value/10000).toFixed(1)+'万':value.toFixed(0)},splitLine:{lineStyle:{color:grid}}},dataZoom:this.rows.length>20?[{type:'inside',xAxisIndex:0}]:[],series:[{name:this.mode==='account'?'动态权益':'组合净损益',type:'line',data:this.rows.map(this.value),connectNulls:false,showSymbol:single,symbolSize:single?8:4,lineStyle:{width:1.8,color},itemStyle:{color},areaStyle:{color:'#6995bc0a'}}]},true)
  },
  paint(){if(this.$refs.chart){if(!this.chart)this.chart=markRaw(this.$echarts.init(this.$refs.chart));this.draw(this.chart)}if(this.largeChart)this.draw(this.largeChart)},
  paintExpanded(){if(this.$refs.expandedChart){this.largeChart=markRaw(this.$echarts.init(this.$refs.expandedChart));this.draw(this.largeChart)}},
  disposeExpanded(){this.largeChart?.dispose();this.largeChart=null},
  resize(){this.chart?.resize();this.largeChart?.resize()}
 },
 watch:{rows(){this.$nextTick(this.paint)}},
 mounted(){this.paint();this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(this.$el);this.onTheme=()=>this.paint();window.addEventListener('wt-theme-change',this.onTheme);window.addEventListener('resize',this.resize)},
 beforeUnmount(){this.observer?.disconnect();window.removeEventListener('wt-theme-change',this.onTheme);window.removeEventListener('resize',this.resize);this.chart?.dispose();this.disposeExpanded()}
}
</script>
<style scoped>
.account-equity{height:100%;min-height:230px;display:flex;flex-direction:column;min-width:0;color:var(--wt-text);padding:10px 12px;box-sizing:border-box;border:1px solid var(--wt-line);border-radius:7px;background:var(--wt-surface)}.account-equity header{display:flex;justify-content:space-between;gap:6px;align-items:center}.account-equity header strong{font-size:12px;font-weight:550}.equity-controls{display:flex;gap:6px;flex-wrap:wrap;margin:6px 0}.equity-controls .el-select:first-child{flex:1;min-width:135px}.equity-controls .el-select{width:108px}.equity-summary{display:flex;align-items:baseline;gap:7px;flex-wrap:wrap;font-size:10px}.equity-summary>strong{font:20px var(--wt-mono)}.equity-summary>span,.equity-summary small{color:var(--wt-muted)}.equity-summary b{font-weight:500}.equity-status{font-size:9px;color:var(--wt-muted);line-height:1.5;margin:5px 0 0}.equity-status.warning{color:var(--wt-warning)}.equity-canvas{position:relative;flex:1;min-height:90px}.equity-chart{width:100%;height:100%;position:absolute;inset:0}.equity-empty{position:absolute;inset:20px;display:flex;align-items:center;justify-content:center;text-align:center;color:var(--wt-muted);font-size:11px;line-height:1.8;pointer-events:none}.equity-source{font-size:9px;line-height:1.7;color:var(--wt-muted)}.up{color:var(--wt-up)}.down{color:var(--wt-down)}.expanded-summary{display:flex;align-items:center;gap:16px;flex-wrap:wrap;color:var(--wt-muted);font-size:11px}.expanded-summary>strong{color:var(--wt-text);font:20px var(--wt-mono)}.expanded-summary .el-select{width:130px;margin-left:auto}.expanded-canvas{position:relative}.expanded-chart{height:65vh;min-height:260px;width:100%}
</style>
