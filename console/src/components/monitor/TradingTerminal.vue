<template>
 <section class="terminal" v-loading="loading && !snapshot">
  <div class="terminal-cockpit" aria-label="今日概览"><div v-for="card in cockpit" :key="card.label"><small>{{card.label}}</small><strong :class="card.color">{{card.value}}</strong><span>{{card.source}}</span></div></div>
  <div ref="toolbar" class="terminal-toolbar">
   <el-select v-model="code" filterable placeholder="选择合约" class="contract-select" aria-label="看盘合约" @change="reload(true)"><el-option v-for="item in catalog" :key="item.code" :value="item.code" :label="item.name+' · '+item.code"><span>{{item.name}}</span><small>{{item.code}}{{item.available?' · 有行情':''}}</small></el-option></el-select>
   <el-radio-group v-model="period" @change="reload(true)" size="small"><el-radio-button value="m1">1 分钟</el-radio-button><el-radio-button value="m5">5 分钟</el-radio-button><el-radio-button value="d">日线</el-radio-button></el-radio-group>
   <div class="terminal-indicators"><el-checkbox v-for="n in [5,13,50,200]" :key="n" :model-value="averages.includes(n)" @change="toggleMA(n)">MA{{n}}</el-checkbox><el-checkbox v-model="boll" @change="paintMarket">BOLL</el-checkbox></div>
   <el-date-picker v-model="dateRange" type="daterange" value-format="YYYYMMDD" range-separator="至" start-placeholder="行情开始日期" end-placeholder="行情结束日期" aria-label="历史行情日期" class="history-range" @change="reload(true)"/>
   <el-checkbox v-model="autoRefresh" class="terminal-auto" @change="saveRefresh">自动刷新</el-checkbox>
   <el-button @click="reload(true)" :loading="loading" aria-label="刷新行情">刷新</el-button>
  </div>
  <div v-if="error" class="terminal-error" role="alert">{{error}}；当前保留上次成功读取的快照。</div>
  <div ref="quote" class="terminal-quote" :class="'freshness-'+freshness.state"><span class="freshness-badge" :class="freshness.state" role="status">{{freshness.label}}</span><div class="quote-main"><strong>{{contract?.name || '行情工作台'}}</strong><span :class="priceChange>=0?'market-up':'market-down'">{{number(snapshot?.market?.quote?.price)}}</span><small :class="priceChange>=0?'market-up':'market-down'">{{priceChange===null?'—':(priceChange>=0?'+':'')+number(priceChange)}}</small></div><div class="quote-details"><span>成交量 <b>{{number(snapshot?.market?.quote?.volume,0)}}</b></span><span>持仓量 <b>{{number(snapshot?.market?.quote?.open_interest,0)}}</b></span><span>最小变动 <b>{{contract?.price_tick ?? '—'}}</b></span><span>乘数 <b>{{contract?.volume_scale ?? '—'}}</b></span></div><span class="quote-time" title="这是行情时间，不是页面刷新时间">{{quoteTime}}</span></div>
  <div class="terminal-history" v-if="snapshot?.market?.range">已存行情 {{formatDate(snapshot.market.range.start_date)}} 至 {{formatDate(snapshot.market.range.end_date)}}<span v-if="dateRange?.length"> · 日期按图表时间筛选；清除日期返回最新行情</span><span class="terminal-source">组合行情 · 最后一根可能未完成 · 拖动 / 滚轮缩放</span></div>
  <div class="market-chart-panel"><div ref="market" class="market-chart"/><div v-if="!bars.length" class="terminal-chart-empty">{{error || snapshot?.market?.market_message || '等待行情数据'}}</div></div>
  <ResizableDivider v-model="bottomHeight" axis="y" :reverse="true" :min="260" :max="bottomMax" storage-key="wt.terminal.bottom" label="调整看盘下方面板高度"/>
  <div ref="bottom" class="terminal-bottom" :style="{flex:'0 0 '+bottomHeight+'px'}">
   <section class="terminal-equity" :style="{flex:'0 0 '+equityWidth+'px'}">
    <div class="equity-heading"><strong>{{equityMode==='account'?'账户权益 · '+(selectedAccount?.currency || '—'):'组合净损益'}}</strong><el-select v-model="equityMode" aria-label="权益曲线口径" @change="paintEquity"><el-option value="account" label="柜台权益快照"/><el-option value="portfolio" label="组合净损益（不含本金）"/></el-select></div>
    <el-select v-if="equityMode==='account' && (snapshot?.accounts?.length || 0)>1" v-model="accountKey" aria-label="权益账户币种" @change="paintEquity"><el-option v-for="account in snapshot.accounts" :key="account.channel+':'+account.currency" :value="account.channel+':'+account.currency" :label="account.channel+' · '+account.currency"/></el-select>
    <div v-show="equityRows.length>=2" ref="equity" class="equity-chart"/><div v-if="equityRows.length<2" class="equity-wait"><strong>{{equityRows.length?number(equityRows[0][equityMode==='account'?'equity':'pnl']):'—'}}</strong><span>等待权益采样 · 已采集 {{equityRows.length}} 个点</span><small>{{autoRefresh?'下一次自动读取 '+nextRefreshTime:'自动刷新已关闭，点击刷新继续采样'}}</small></div><small>{{equityMode==='account'?'从首次读取起采样，不补造历史；快照时间见右侧':'读取组合资金账本，未添加默认本金'}}</small>
   </section>
   <ResizableDivider v-model="equityWidth" :min="180" :max="equityMax" storage-key="wt.terminal.equity" label="调整权益面板宽度"/>
   <section class="terminal-ledger">
    <el-tabs v-model="ledgerTab"><el-tab-pane label="持仓" name="positions"/><el-tab-pane label="委托" name="orders"/><el-tab-pane label="成交" name="trades"/><el-tab-pane label="账户 / 合约" name="account"/></el-tabs>
    <div class="terminal-table">
     <el-table v-if="ledgerTab==='positions'" :data="snapshot?.positions || []" height="100%" empty-text="通道账本暂无持仓">
      <el-table-column prop="code" label="合约" min-width="130"/><el-table-column label="方向" width="65"><template #default="{row}"><span :class="row.long?'market-up':'market-down'">{{row.long?'多':'空'}}</span></template></el-table-column><el-table-column prop="volume" label="数量" width="65"/><el-table-column prop="cost" label="成本" width="90"/><el-table-column prop="profit" label="浮盈" min-width="85"><template #default="{row}"><span :class="row.profit>=0?'market-up':'market-down'">{{number(row.profit)}}</span></template></el-table-column>
     </el-table>
     <el-table v-if="ledgerTab==='orders'" :data="snapshot?.orders || []" height="100%" empty-text="通道账本暂无委托"><el-table-column prop="time" label="时间" min-width="150"/><el-table-column prop="code" label="合约" min-width="120"/><el-table-column prop="action" label="操作" width="75"/><el-table-column prop="price" label="委托价" width="90"/><el-table-column prop="total" label="委托量" width="75"/><el-table-column prop="traded" label="已成交" width="75"/></el-table>
     <el-table v-if="ledgerTab==='trades'" :data="snapshot?.trades || []" height="100%" empty-text="通道账本暂无成交"><el-table-column prop="time" label="时间" min-width="150"/><el-table-column prop="code" label="合约" min-width="120"/><el-table-column prop="action" label="操作" width="75"/><el-table-column prop="price" label="成交价" width="90"/><el-table-column prop="volume" label="数量" width="65"/></el-table>
     <div v-if="ledgerTab==='account'" class="terminal-account">
      <TradeRiskPanel :groupid="groupid" :code="code" :auto-refresh="autoRefresh"/>
       <div class="account-card" v-if="contract"><strong>{{contract.name}} · {{contract.code}}</strong><span>交易所 {{contract.exchange}} · 交易时段 {{contract.session || '—'}}</span><span>最小变动 {{contract.price_tick}} · 合约乘数 {{contract.volume_scale}}</span><span>到期日 {{contract.expire_date || '—'}}</span><small>仅展示当前组合基础配置中的合约，新增行情订阅仍由数据中台配置管理。</small></div>
     </div>
    </div>
   </section>
  </div>
 </section>
</template>
<script>
import {markRaw} from 'vue'
import ResizableDivider from '../common/ResizableDivider.vue'
import {marketFreshness} from '@/js/market-freshness.js'
import TradeRiskPanel from './TradeRiskPanel.vue'
export default{
 name:'TradingTerminal',components:{ResizableDivider,TradeRiskPanel},props:{groupid:{type:String,required:true}},
 data:()=>({snapshot:null,riskSnapshot:null,clock:Date.now(),nextRefresh:Date.now()+5000,catalog:[],code:'',period:'m1',averages:[5,13,50],boll:false,loading:false,error:'',serial:0,autoRefresh:true,dateRange:null,bottomMax:440,equityMax:650,bottomHeight:300,equityWidth:330,ledgerTab:'positions',equityMode:'account',accountKey:'',marketChart:null,equityChart:null}),
 computed:{
  selectedAccount(){return this.snapshot?.accounts?.find(account=>account.channel+':'+account.currency===this.accountKey) || this.snapshot?.accounts?.[0]},
  freshness(){return marketFreshness(this.snapshot?.market,this.clock)},
  equityRows(){return this.equityMode==='account'?(this.selectedAccount?.history||[]):(this.snapshot?.equity||[])},
  nextRefreshTime(){return new Date(this.nextRefresh).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'})},
  cockpit(){
   const risk=this.riskSnapshot,summary=risk?.broker_summary,metric=risk?.metrics;
   const val=(key,digits=2)=>metric?.[key]?.valid?this.number(metric[key].value,digits):'—';
   return [
    {label:'账户权益',value:summary?.valid?this.number(summary.equity):'—',source:summary?.valid?'柜台 · '+(summary.currency||''):(summary?.reason||'等待柜台快照')},
    {label:'当日亏损',value:val('day_loss_pct')+(metric?.day_loss_pct?.valid?'%':''),source:'柜台 · 当日冻结基准',color:metric?.day_loss_pct?.value>0?'market-down':''},
    {label:'保证金占用',value:val('margin_usage_pct')+(metric?.margin_usage_pct?.valid?'%':''),source:'柜台'},
    {label:'可用资金',value:val('available_funds'),source:'柜台'},
    {label:'理论持仓',value:val('gross_lots',0),source:'引擎 · 手'},
    {label:'成交记录',value:String(this.snapshot?.trades?.length||0),source:'当前通道账簿'},
    {label:'待处理告警',value:risk?String((risk.events||[]).filter(event=>event.kind==='alert'&&event.status==='pending').length):'—',source:'后台预警'}
   ]
  },
  bars(){return this.snapshot?.market?.bars || []},contract(){return this.snapshot?.market?.contract},
  priceChange(){const quote=this.snapshot?.market?.quote;return quote?.price!=null && quote.pre_close>0?quote.price-quote.pre_close:null},
  quoteTime(){const q=this.snapshot?.market?.quote;if(!q)return this.dateRange?.length?'历史行情 · 无实时Tick':'暂无Tick';const d=String(q.action_date).slice(0,8),t=String(q.action_time).padStart(9,'0');return d.slice(0,4)+'-'+d.slice(4,6)+'-'+d.slice(6)+' '+t.slice(0,2)+':'+t.slice(2,4)+':'+t.slice(4,6)}
 },
 methods:{
  formatDate(value){const text=String(value||'');return text.length===8?text.slice(0,4)+'-'+text.slice(4,6)+'-'+text.slice(6):'—'},
  loadRefresh(){this.autoRefresh=localStorage.getItem('wt.terminal.autoRefresh.'+this.groupid)!=='false'},
  saveRefresh(){localStorage.setItem('wt.terminal.autoRefresh.'+this.groupid,String(this.autoRefresh))},
  fitPanels(){this.bottomMax=520;this.equityMax=Math.max(180,Math.min(650,(this.$refs.bottom?.clientWidth||0)-325));if(this.equityWidth>this.equityMax)this.equityWidth=this.equityMax;this.resize()},
  async loadRisk(){try{const result=await (await fetch('/mgr/qryrisk_state',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupid:this.groupid})})).json();if(result.result===0){this.riskSnapshot=result.snapshot||result;const risk=this.riskSnapshot,fund=risk.broker_summary,lots=risk.metrics?.gross_lots;this.$emit('overview',{groupid:this.groupid,text:(fund?.valid?'权益 '+this.number(fund.equity,0):'柜台快照不可用')+(lots?.valid?' · '+this.number(lots.value,0)+' 手':'')});}}catch{}},
  number(value,digits=2){return value==null || !Number.isFinite(Number(value))?'—':Number(value).toLocaleString('zh-CN',{maximumFractionDigits:digits,minimumFractionDigits:digits})},
  async reload(force=false){if(force)this.loadRisk();if(this.loading && !force)return;const serial=++this.serial;this.loading=true;this.error='';try{
   const response=await fetch('/mgr/workbench',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupid:this.groupid,code:this.code,period:this.period,count:1000,include_catalog:!this.catalog.length,start_date:this.dateRange?.[0]||null,end_date:this.dateRange?.[1]||null})});const result=await response.json();if(result.result<0)throw new Error(result.message);if(serial!==this.serial)return;this.snapshot=result;this.clock=Date.now();if(result.market?.contracts)this.catalog=result.market.contracts;this.code=result.market?.code || this.code;await this.$nextTick();this.paintMarket();this.paintEquity();this.fitPanels()
  }catch(error){if(serial===this.serial)this.error=error.message}finally{if(serial===this.serial)this.loading=false}},
  toggleMA(n){this.averages=this.averages.includes(n)?this.averages.filter(x=>x!==n):[...this.averages,n];this.paintMarket()},
  css(name){return getComputedStyle(document.documentElement).getPropertyValue(name).trim()},
  average(n){const bars=this.bars;let total=0;return bars.map((bar,i)=>{total+=bar.close;if(i>=n)total-=bars[i-n].close;return i<n-1?null:total/n})},
  paintMarket(){if(!this.$refs.market)return;if(!this.bars.length){this.marketChart?.clear();return;}if(!this.marketChart)this.marketChart=markRaw(this.$echarts.init(this.$refs.market));
   const bars=this.bars,up=this.css('--wt-up'),down=this.css('--wt-down'),text=this.css('--wt-muted'),grid=this.css('--wt-chart-grid');const labels=bars.map(b=>String(b.time)),colours=['#bd8a35','#8d7bc7','#4b92be','#9d7a5b'];
   const data=bars.map(b=>[b.open,b.close,b.low,b.high]);const series=[{name:'K线',type:'candlestick',data,itemStyle:{color:up,color0:down,borderColor:up,borderColor0:down}},{name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:bars.map(b=>({value:b.volume,itemStyle:{color:b.close>=b.open?up:down}}))}];
   this.averages.forEach((n,i)=>series.push({name:'MA'+n,type:'line',data:this.average(n),showSymbol:false,smooth:false,lineStyle:{width:1,color:colours[i%4]},itemStyle:{color:colours[i%4]},connectNulls:false}));
   if(this.boll){const middle=this.average(20),upper=[],lower=[];bars.forEach((bar,i)=>{if(i<19){upper.push(null);lower.push(null);return}const variance=bars.slice(i-19,i+1).reduce((sum,b)=>sum+(b.close-middle[i])**2,0)/20;upper.push(middle[i]+2*Math.sqrt(variance));lower.push(middle[i]-2*Math.sqrt(variance))});for(const [name,values] of [['BOLL上轨',upper],['BOLL中轨',middle],['BOLL下轨',lower]])series.push({name,type:'line',data:values,showSymbol:false,lineStyle:{width:1,color:'#6a9ca6',type:name==='BOLL中轨'?'dashed':'solid'}})}
   const markers=(this.snapshot.trades||[]).filter(trade=>trade.code===this.code).map(trade=>{const minute=String(trade.time).slice(0,this.period==='d'?8:12);if(Number(minute)<Number(labels[0])||Number(minute)>Number(labels.at(-1)))return null;const index=labels.findIndex(label=>Number(label)>=Number(minute));if(index<0)return null;return{name:trade.action,coord:[index,trade.price],value:trade.action,symbol:'triangle',symbolSize:9,itemStyle:{color:/买|多|BUY|LONG/i.test(trade.action)?up:down},label:{show:false}}}).filter(Boolean);
   if(markers.length)series[0].markPoint={data:markers};
   const zoom=this.marketChart.getOption()?.dataZoom?.[0],start=zoom?.start??Math.max(0,100-150/bars.length*100),end=zoom?.end??100,visibleCount=bars.length*(end-start)/100;this.marketChart.setOption({animation:false,backgroundColor:'transparent',tooltip:{trigger:'axis',axisPointer:{type:'cross'},backgroundColor:this.css('--wt-surface'),textStyle:{color:this.css('--wt-text')},borderColor:grid},axisPointer:{link:[{xAxisIndex:'all'}]},grid:[{left:12,right:70,top:16,height:'56%'},{left:12,right:70,top:'65%',height:'20%'}],xAxis:[{type:'category',data:labels,boundaryGap:true,axisLine:{lineStyle:{color:grid}},axisLabel:{show:false},axisTick:{show:false}},{type:'category',data:labels,gridIndex:1,axisLine:{lineStyle:{color:grid}},axisTick:{show:false},splitNumber:5,axisLabel:{interval:Math.max(0,Math.ceil(visibleCount/6)-1),color:text,fontSize:10,hideOverlap:true,formatter:value=>value.length>8?value.slice(8,10)+':'+value.slice(10,12):value.slice(4,6)+'-'+value.slice(6,8)}}],yAxis:[{scale:true,splitNumber:5,position:'right',axisLabel:{color:text,fontSize:10},splitLine:{lineStyle:{color:grid}}},{scale:true,gridIndex:1,position:'right',splitNumber:2,axisLabel:{color:text,fontSize:9,formatter:value=>Math.abs(value)>=10000?(value/10000).toFixed(1)+'万':value},splitLine:{show:false}}],dataZoom:[{type:'inside',xAxisIndex:[0,1],start:zoom?.start??Math.max(0,100-150/bars.length*100),end:zoom?.end??100}],series},true)
  },
  paintEquity(){if(!this.$refs.equity)return;if(this.equityRows.length<2){this.equityChart?.clear();return;}if(!this.equityChart)this.equityChart=markRaw(this.$echarts.init(this.$refs.equity));const account=this.selectedAccount;const rows=this.equityRows;if(rows.length<2){this.equityChart.clear();return;}const text=this.css('--wt-muted'),grid=this.css('--wt-chart-grid');this.equityChart.setOption({animation:false,grid:{left:58,right:12,top:16,bottom:28},tooltip:{trigger:'axis'},xAxis:{type:'category',data:rows.map(r=>this.equityMode==='account'?new Date(r.time).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'}):String(r.time)),axisLabel:{fontSize:9,color:text},axisLine:{lineStyle:{color:grid}}},yAxis:{type:'value',scale:true,axisLabel:{fontSize:9,color:text,formatter:value=>Math.abs(value)>=10000?(value/10000).toFixed(1)+'万':value.toFixed(0)},splitLine:{lineStyle:{color:grid}}},series:[{type:'line',data:rows.map(r=>this.equityMode==='account'?r.equity:r.pnl),showSymbol:rows.length<2,lineStyle:{width:1.5,color:'#6995bc'},itemStyle:{color:'#6995bc'},areaStyle:{color:'#6995bc0a'}}]},true)},
  resize(){this.marketChart?.resize();this.equityChart?.resize()}
 },
 watch:{bottomHeight(){this.$nextTick(this.resize)},equityWidth(){this.$nextTick(this.resize)},groupid(){++this.serial;this.loading=false;this.catalog=[];this.code='';this.snapshot=null;this.riskSnapshot=null;this.dateRange=null;this.loadRefresh();this.reload();this.loadRisk()}},
 mounted(){this.loadRefresh();this.reload();this.loadRisk();this.poll=setInterval(()=>{this.clock=Date.now();if(this.autoRefresh && !document.hidden){this.reload();this.loadRisk()}this.nextRefresh=Date.now()+5000},5000);this.observer=new ResizeObserver(()=>{cancelAnimationFrame(this.resizeFrame);this.resizeFrame=requestAnimationFrame(()=>this.fitPanels())});this.observer.observe(this.$el);this.onTheme=()=>{this.paintMarket();this.paintEquity()};window.addEventListener('wt-theme-change',this.onTheme)},
 beforeUnmount(){++this.serial;clearInterval(this.poll);cancelAnimationFrame(this.resizeFrame);this.observer?.disconnect();window.removeEventListener('wt-theme-change',this.onTheme);this.marketChart?.dispose();this.equityChart?.dispose()}
}
</script>
<style scoped>
.terminal{height:auto;min-height:860px;display:flex;flex-direction:column;min-width:0;padding-top:4px;overflow:visible;box-sizing:border-box;}
.terminal-history{display:flex;gap:16px;flex-wrap:wrap;flex:0 0 auto;font-size:10px;color:var(--wt-muted);padding:0 0 6px;}.terminal-toolbar .history-range{flex:0 0 254px;max-width:100%;}.terminal-auto{margin:0;font-size:11px;}
.terminal-error{padding:8px 12px;color:var(--wt-danger);background:var(--wt-matte);font-size:11px;border:1px solid var(--wt-line);border-radius:5px;}
.terminal-toolbar{flex:0 0 auto;display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:4px 0 10px;}.contract-select{width:220px;}
.terminal-toolbar :deep(.el-radio-button__inner){background:var(--wt-matte);color:var(--wt-text);border-color:var(--wt-line);}
.terminal-toolbar :deep(.el-radio-button.is-active .el-radio-button__inner){background:var(--wt-text);color:var(--wt-surface);border-color:var(--wt-text);box-shadow:none;}
.terminal-indicators{display:flex;align-items:center;gap:10px;margin-left:auto;}.terminal-indicators .el-checkbox{margin-right:0;}.terminal-indicators :deep(.el-checkbox__label){font-size:10px;padding-left:4px;}
.terminal-quote{flex:0 0 auto;display:flex;align-items:center;gap:22px;padding:12px 0;border-top:1px solid var(--wt-line);font-size:11px;flex-wrap:wrap;}.quote-main{display:flex;align-items:baseline;gap:14px;}.quote-main strong{font-size:14px;font-weight:550;}.quote-main>span{font-family:var(--wt-mono);font-size:20px;font-weight:550;}.quote-details{display:flex;gap:18px;color:var(--wt-muted);}.quote-details b{font-weight:400;color:var(--wt-text);padding-left:4px;}.quote-time{margin-left:auto;color:var(--wt-muted);font-family:var(--wt-mono);font-size:10px;}
.quote-stale{font:10px var(--el-font-family);font-weight:400;margin-left:8px;color:var(--wt-warning);padding:2px 5px;border:1px solid var(--wt-line);border-radius:4px;}
.market-up{color:var(--wt-up);}.market-down{color:var(--wt-down);}
.market-chart-panel{position:relative;flex:0 0 360px;min-height:360px;}.market-chart{height:360px;width:100%;}.terminal-source{font-size:10px;color:var(--wt-muted);margin-left:auto;}.terminal-chart-empty{position:absolute;inset:40% 0 auto;text-align:center;font-size:12px;color:var(--wt-muted);}
.terminal-bottom{display:flex;min-height:260px;overflow:hidden;}.terminal-equity{display:flex;flex-direction:column;min-width:0;overflow:hidden;padding:10px 12px 8px 0;}.equity-heading{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;font-size:12px;}.equity-heading strong{font-weight:550;white-space:nowrap;}.equity-heading .el-select{width:135px;}.equity-chart{flex:1;min-height:50px;width:100%;}.terminal-equity>small{font-size:9px;color:var(--wt-muted);line-height:1.7;padding:0 8px;}
.terminal-ledger{flex:1;min-width:0;display:flex;flex-direction:column;overflow:hidden;padding-left:12px;}.terminal-ledger>.el-tabs{flex:0 0 40px;}.terminal-ledger :deep(.el-tabs__content){display:none;}.terminal-table{flex:1;min-height:0;overflow:auto;}.terminal-account{display:flex;gap:12px;padding:12px 0;flex-wrap:wrap;}.account-card{display:flex;flex-direction:column;gap:7px;font-size:11px;border:1px solid var(--wt-line);border-radius:7px;padding:12px;min-width:220px;}.account-card strong{font-weight:550;}.account-card b{font-weight:500;font-family:var(--wt-mono);}.account-card small{font-size:10px;color:var(--wt-muted);}
@media(max-width:1100px){.terminal-indicators{margin-left:0;}.terminal-bottom{overflow-x:auto;}.terminal-equity{min-width:180px;}.terminal-ledger{min-width:340px;}.quote-details{gap:10px;}}
.terminal-cockpit{display:grid;grid-template-columns:repeat(7,minmax(110px,1fr));gap:8px;border-bottom:1px solid var(--wt-line);padding:4px 0 14px;margin-bottom:10px}.terminal-cockpit>div{display:flex;flex-direction:column;gap:5px;padding:8px 10px;background:var(--wt-matte);border:1px solid var(--wt-line);border-radius:6px}.terminal-cockpit small{font-size:10px;color:var(--wt-muted)}.terminal-cockpit strong{font:16px var(--wt-mono);white-space:nowrap}.terminal-cockpit span{font-size:9px;color:var(--wt-muted);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.freshness-badge{display:inline-flex;padding:4px 6px;border:1px solid currentColor;border-radius:4px;font-size:10px;white-space:nowrap;color:var(--wt-muted)}.freshness-badge.live{color:var(--wt-success)}.freshness-badge.delayed{color:var(--wt-warning)}.freshness-badge.stale{color:var(--wt-danger)}.terminal-quote.freshness-stale{border:1px solid var(--wt-danger);padding:10px}.equity-wait{flex:1;min-height:110px;display:flex;flex-direction:column;gap:10px;align-items:center;justify-content:center;color:var(--wt-muted);font-size:11px}.equity-wait strong{font:20px var(--wt-mono);color:var(--wt-text)}.equity-wait small{font-size:10px}
@media(max-width:1280px){.terminal-cockpit{grid-template-columns:repeat(4,minmax(100px,1fr))}.quote-time{margin-left:0}.terminal-toolbar{gap:8px}.terminal-history{gap:6px}.terminal-source{margin-left:0}}
@media(max-width:1024px){.terminal-bottom{flex-direction:column;flex-basis:auto!important;min-height:620px;overflow:visible}.terminal-equity{flex:0 0 260px!important;min-height:260px;padding-right:0}.terminal-ledger{flex:0 0 340px;min-height:340px;padding-left:0;border-top:1px solid var(--wt-line)}.terminal-bottom>.wt-resize-divider.x{display:none}.terminal-cockpit{grid-template-columns:repeat(3,minmax(100px,1fr))}.market-chart-panel,.market-chart{min-height:340px;height:340px;flex-basis:340px}.terminal{min-height:1150px}}
</style>
