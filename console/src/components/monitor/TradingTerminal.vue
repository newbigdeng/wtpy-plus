<template>
 <section class="terminal" v-loading="loading && !snapshot">
  <div class="terminal-cockpit" aria-label="今日概览"><div v-for="card in cockpit" :key="card.label"><small>{{card.label}}</small><strong :class="card.color">{{card.value}}</strong><span>{{card.source}}</span><el-button v-if="card.label==='账户权益'" size="small" text class="equity-shortcut" @click="$refs.accountCurve.open()">查看曲线</el-button></div></div>
  <div ref="toolbar" class="terminal-toolbar">
   <el-select v-model="code" filterable placeholder="选择合约" class="contract-select" aria-label="看盘合约" @change="reload(true)"><el-option v-for="item in catalog" :key="item.code" :value="item.code" :label="item.name+' · '+item.code"><span>{{item.name}}</span><small>{{item.code}}{{item.available?' · 有行情':''}}</small></el-option></el-select>
   <el-radio-group v-model="period" @change="reload(true)" size="small"><el-radio-button value="m1">1 分钟</el-radio-button><el-radio-button value="m5">5 分钟</el-radio-button><el-radio-button value="d">日线</el-radio-button></el-radio-group>
   <div class="terminal-indicators">
    <el-checkbox v-model="showMA" @change="saveIndicators">MA</el-checkbox><el-checkbox v-model="showMacd" @change="saveIndicators">MACD</el-checkbox><el-checkbox v-model="showKdj" @change="saveIndicators">KDJ</el-checkbox><el-checkbox v-model="boll" @change="saveIndicators">BOLL</el-checkbox>
    <el-popover v-model:visible="indicatorEditor" placement="bottom-end" :width="360" trigger="click" @show="editIndicators">
     <template #reference><el-button size="small" aria-label="设置技术指标参数">指标参数</el-button></template>
     <div class="indicator-editor">
      <strong>技术指标参数</strong>
      <label>MA 周期<el-input v-model="indicatorDraft.ma" aria-label="MA周期" placeholder="5,13,50"/></label><small>用逗号分隔，最多 6 条；周期为 1–500 根 K 线。</small>
      <div class="indicator-parameters"><span>MACD</span><label>快线<el-input-number v-model="indicatorDraft.fast" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="MACD快线周期"/></label><label>慢线<el-input-number v-model="indicatorDraft.slow" :min="2" :max="500" :precision="0" controls-position="right" size="small" aria-label="MACD慢线周期"/></label><label>信号<el-input-number v-model="indicatorDraft.signal" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="MACD信号周期"/></label></div>
      <div class="indicator-parameters"><span>KDJ</span><label>窗口<el-input-number v-model="indicatorDraft.kdj" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="KDJ窗口周期"/></label><label>K 平滑<el-input-number v-model="indicatorDraft.k" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="KDJ K平滑周期"/></label><label>D 平滑<el-input-number v-model="indicatorDraft.d" :min="1" :max="500" :precision="0" controls-position="right" size="small" aria-label="KDJ D平滑周期"/></label></div>
      <small>MACD 柱 = 2 × (DIF − DEA)。K、D 初值为 50；窗口高低价相同时 RSV 取 50，J 不截断。</small>
      <span v-if="indicatorError" class="indicator-error" role="alert">{{indicatorError}}</span>
      <div class="indicator-actions"><el-button size="small" @click="resetIndicators">恢复默认</el-button><el-button type="primary" size="small" @click="applyIndicators">应用</el-button></div>
     </div>
    </el-popover>
   </div>
   <el-date-picker v-model="dateRange" type="daterange" value-format="YYYYMMDD" range-separator="至" start-placeholder="行情开始日期" end-placeholder="行情结束日期" aria-label="历史行情日期" class="history-range" @change="reload(true)"/>
   <el-checkbox v-model="autoRefresh" class="terminal-auto" @change="saveRefresh">自动刷新</el-checkbox>
   <el-button @click="reload(true)" :loading="loading" aria-label="刷新行情">刷新</el-button>
  </div>
  <div v-if="error" class="terminal-error" role="alert">{{error}}；当前保留上次成功读取的快照。</div>
  <div ref="quote" class="terminal-quote" :class="'freshness-'+freshness.state"><span class="freshness-badge" :class="freshness.state" role="status">{{freshness.label}}</span><div class="quote-main"><strong>{{contract?.name || '行情工作台'}}</strong><span :class="priceChange>=0?'market-up':'market-down'">{{number(snapshot?.market?.quote?.price)}}</span><small :class="priceChange>=0?'market-up':'market-down'">{{priceChange===null?'—':(priceChange>=0?'+':'')+number(priceChange)}}</small></div><div class="quote-details"><span>成交量 <b>{{number(snapshot?.market?.quote?.volume,0)}}</b></span><span>持仓量 <b>{{number(snapshot?.market?.quote?.open_interest,0)}}</b></span><span>最小变动 <b>{{contract?.price_tick ?? '—'}}</b></span><span>乘数 <b>{{contract?.volume_scale ?? '—'}}</b></span></div><span class="quote-time" title="这是行情时间，不是页面刷新时间">{{quoteTime}}</span></div>
  <div class="terminal-history" v-if="snapshot?.market?.range">已存行情 {{formatDate(snapshot.market.range.start_date)}} 至 {{formatDate(snapshot.market.range.end_date)}}<span v-if="dateRange?.length"> · 日期按图表时间筛选；清除日期返回最新行情</span><span class="terminal-source">组合行情 · 最后一根可能未完成 · 拖动 / 滚轮缩放</span></div>
  <div class="market-chart-panel" :style="{height:chartHeight+'px',flexBasis:chartHeight+'px'}"><div ref="market" class="market-chart" :style="{height:chartHeight+'px'}"/><div v-if="!bars.length" class="terminal-chart-empty">{{error || snapshot?.market?.market_message || '等待行情数据'}}</div></div>
  <small class="indicator-note">指标基于当前加载的 {{bars.length}} 根 K 线；预热不足留空，最后一根随行情变化。EMA 用首段均值初始化。</small>
  <ResizableDivider v-model="bottomHeight" axis="y" :reverse="true" :min="260" :max="bottomMax" storage-key="wt.terminal.bottom" label="调整看盘下方面板高度"/>
  <div ref="bottom" class="terminal-bottom" :style="{flex:'0 0 '+bottomHeight+'px'}">
   <section class="terminal-equity" :style="{flex:'0 0 '+equityWidth+'px'}">
    <AccountEquityChart ref="accountCurve" :key="groupid" :accounts="snapshot?.accounts || []" :portfolio="snapshot?.equity || []" :clock="clock"/>
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
import {movingAverage,calculateMacd,calculateKdj} from '@/js/market-indicators.js'
import TradeRiskPanel from './TradeRiskPanel.vue'
import AccountEquityChart from './AccountEquityChart.vue'
export default{
 name:'TradingTerminal',components:{ResizableDivider,TradeRiskPanel,AccountEquityChart},props:{groupid:{type:String,required:true}},
 data:()=>({snapshot:null,riskSnapshot:null,clock:Date.now(),nextRefresh:Date.now()+5000,catalog:[],code:'',period:'m1',showMA:true,averages:[5,13,50],showMacd:true,showKdj:true,macdParameters:{fast:12,slow:26,signal:9},kdjParameters:{period:9,k:3,d:3},boll:false,indicatorEditor:false,indicatorDraft:{},indicatorError:'',loading:false,error:'',serial:0,autoRefresh:true,dateRange:null,bottomMax:440,equityMax:650,bottomHeight:300,equityWidth:360,ledgerTab:'positions',marketChart:null}),
 computed:{
  chartHeight(){return 380+134*(Number(this.showMacd)+Number(this.showKdj))},
  freshness(){return marketFreshness(this.snapshot?.market,this.clock)},
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
   const response=await fetch('/mgr/workbench',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({groupid:this.groupid,code:this.code,period:this.period,count:1000,include_catalog:!this.catalog.length,start_date:this.dateRange?.[0]||null,end_date:this.dateRange?.[1]||null})});const result=await response.json();if(result.result<0)throw new Error(result.message);if(serial!==this.serial)return;this.snapshot=result;this.clock=Date.now();if(result.market?.contracts)this.catalog=result.market.contracts;this.code=result.market?.code || this.code;await this.$nextTick();this.paintMarket();this.fitPanels()
  }catch(error){if(serial===this.serial)this.error=error.message}finally{if(serial===this.serial)this.loading=false}},
  indicatorParameters(){return {ma:this.averages.join(','),...this.macdParameters,kdj:this.kdjParameters.period,k:this.kdjParameters.k,d:this.kdjParameters.d}},
  parseIndicatorDraft(draft){
   const pieces=String(draft.ma||'').replace(/，/g,',').split(',').map(value=>value.trim());
   if(!pieces.length || pieces.length>6 || pieces.some(value=>!/^\d+$/.test(value) || Number(value)<1 || Number(value)>500))throw new Error('MA 请填写 1–6 个周期，每个为 1–500 的整数。');
   const values=['fast','slow','signal','kdj','k','d'];
   if(values.some(key=>!Number.isInteger(draft[key]) || draft[key]<1 || draft[key]>500))throw new Error('指标周期须为 1–500 的整数。');
   if(draft.fast>=draft.slow)throw new Error('MACD 快线周期须小于慢线周期。');
   return {averages:[...new Set(pieces.map(Number))],macdParameters:{fast:draft.fast,slow:draft.slow,signal:draft.signal},kdjParameters:{period:draft.kdj,k:draft.k,d:draft.d}}
  },
  editIndicators(){this.indicatorDraft=this.indicatorParameters();this.indicatorError=''},
  resetIndicators(){this.indicatorDraft={ma:'5,13,50',fast:12,slow:26,signal:9,kdj:9,k:3,d:3};this.indicatorError=''},
  applyIndicators(){try{Object.assign(this,this.parseIndicatorDraft(this.indicatorDraft));this.indicatorEditor=false;this.saveIndicators()}catch(error){this.indicatorError=error.message}},
  loadIndicators(){try{const saved=JSON.parse(localStorage.getItem('wt.terminal.indicators'));if(!saved)return;Object.assign(this,this.parseIndicatorDraft(saved));for(const key of ['showMA','showMacd','showKdj','boll'])if(typeof saved[key]==='boolean')this[key]=saved[key]}catch{}},
  saveIndicators(){localStorage.setItem('wt.terminal.indicators',JSON.stringify({...this.indicatorParameters(),showMA:this.showMA,showMacd:this.showMacd,showKdj:this.showKdj,boll:this.boll}));this.$nextTick(()=>{this.paintMarket();this.resize()})},
  css(name){return getComputedStyle(document.documentElement).getPropertyValue(name).trim()},
  paintMarket(){
   if(!this.$refs.market)return;
   if(!this.bars.length){this.marketChart?.clear();return}
   if(!this.marketChart)this.marketChart=markRaw(this.$echarts.init(this.$refs.market));
   const bars=this.bars,closes=bars.map(bar=>bar.close),up=this.css('--wt-up'),down=this.css('--wt-down'),text=this.css('--wt-muted'),grid=this.css('--wt-chart-grid');
   const labels=bars.map(bar=>String(bar.time)),colours=['#bd8a35','#8d7bc7','#4b92be','#aa735b','#5d9b85','#ab659b'];
   const panels=[{name:'K线'+(this.showMA?' · MA('+this.averages.join(',')+')':''),top:24,height:226},{name:'成交量',top:280,height:64}];
   const series=[
    {name:'K线',type:'candlestick',data:bars.map(bar=>[bar.open,bar.close,bar.low,bar.high]),itemStyle:{color:up,color0:down,borderColor:up,borderColor0:down}},
    {name:'成交量',type:'bar',xAxisIndex:1,yAxisIndex:1,data:bars.map(bar=>({value:bar.volume,itemStyle:{color:bar.close>=bar.open?up:down}}))}
   ];
   const line=(name,data,color,axis=0)=>({name,type:'line',data,xAxisIndex:axis,yAxisIndex:axis,showSymbol:false,smooth:false,connectNulls:false,lineStyle:{width:1,color},itemStyle:{color}});
   if(this.showMA)this.averages.forEach((period,index)=>series.push(line('MA'+period,movingAverage(closes,period),colours[index])));
   if(this.boll){
    const middle=movingAverage(closes,20),upper=[],lower=[];
    bars.forEach((bar,index)=>{
     if(index<19){upper.push(null);lower.push(null);return}
     let variance=0;
     for(let cursor=index-19;cursor<=index;cursor++)variance+=(bars[cursor].close-middle[index])**2;
     const width=2*Math.sqrt(variance/20);upper.push(middle[index]+width);lower.push(middle[index]-width)
    });
    for(const [name,values] of [['BOLL上轨',upper],['BOLL中轨',middle],['BOLL下轨',lower]])series.push({...line(name,values,'#6a9ca6'),lineStyle:{width:1,color:'#6a9ca6',type:name==='BOLL中轨'?'dashed':'solid'}})
   }
   if(this.showMacd){
    const {fast,slow,signal}=this.macdParameters,values=calculateMacd(closes,fast,slow,signal),axis=panels.length;
    panels.push({name:'MACD('+[fast,slow,signal].join(',')+') · DIF / DEA / 柱',top:378+134*(axis-2),height:100});
    series.push(line('DIF',values.dif,colours[0],axis),line('DEA',values.dea,colours[1],axis),{name:'MACD柱',type:'bar',xAxisIndex:axis,yAxisIndex:axis,data:values.histogram.map(value=>value===null?null:({value,itemStyle:{color:value>=0?up:down}}))})
   }
   if(this.showKdj){
    const {period,k,d}=this.kdjParameters,values=calculateKdj(bars,period,k,d),axis=panels.length;
    panels.push({name:'KDJ('+[period,k,d].join(',')+') · K / D / J',top:378+134*(axis-2),height:100});
    series.push(line('K',values.k,colours[0],axis),line('D',values.d,colours[1],axis),line('J',values.j,colours[2],axis))
   }
   const markers=(this.snapshot.trades||[]).filter(trade=>trade.code===this.code).map(trade=>{
    const minute=String(trade.time).slice(0,this.period==='d'?8:12);
    if(Number(minute)<Number(labels[0])||Number(minute)>Number(labels.at(-1)))return null;
    const index=labels.findIndex(label=>Number(label)>=Number(minute));
    if(index<0)return null;
    return {name:trade.action,coord:[index,trade.price],value:trade.action,symbol:'triangle',symbolSize:9,itemStyle:{color:/买|多|BUY|LONG/i.test(trade.action)?up:down},label:{show:false}}
   }).filter(Boolean);
   if(markers.length)series[0].markPoint={data:markers};
   const zoom=this.marketChart.getOption()?.dataZoom?.[0],start=zoom?.start??Math.max(0,100-150/bars.length*100),end=zoom?.end??100,visibleCount=bars.length*(end-start)/100;
   this.marketChart.setOption({
    animation:false,backgroundColor:'transparent',
    title:panels.map(panel=>({text:panel.name,left:12,top:panel.top-22,textStyle:{color:text,fontSize:10,fontWeight:'normal'}})),
    tooltip:{trigger:'axis',axisPointer:{type:'cross'},backgroundColor:this.css('--wt-surface'),textStyle:{color:this.css('--wt-text'),fontSize:11},borderColor:grid},
    axisPointer:{link:[{xAxisIndex:'all'}]},
    grid:panels.map(panel=>({left:12,right:70,top:panel.top,height:panel.height})),
    xAxis:panels.map((_,index)=>({
     type:'category',data:labels,gridIndex:index,boundaryGap:true,axisLine:{lineStyle:{color:grid}},axisTick:{show:false},axisPointer:{snap:true},
     axisLabel:{show:index===panels.length-1,interval:Math.max(0,Math.ceil(visibleCount/6)-1),color:text,fontSize:10,hideOverlap:true,formatter:value=>value.length>8?value.slice(8,10)+':'+value.slice(10,12):value.slice(4,6)+'-'+value.slice(6,8)}
    })),
    yAxis:panels.map((_,index)=>({
     type:'value',scale:index!==1,gridIndex:index,position:'right',splitNumber:index===0?5:2,
     axisLabel:{color:text,fontSize:index===0?10:9,formatter:value=>Math.abs(value)>=10000?(value/10000).toFixed(1)+'万':Number(value.toFixed(2))},
     splitLine:{show:index!==1,lineStyle:{color:grid}}
    })),
    dataZoom:[{type:'inside',xAxisIndex:panels.map((_,index)=>index),start,end}],series
   },true)
  },
  resize(){this.marketChart?.resize()}
 },
 watch:{bottomHeight(){this.$nextTick(this.resize)},equityWidth(){this.$nextTick(this.resize)},groupid(){++this.serial;this.loading=false;this.catalog=[];this.code='';this.snapshot=null;this.riskSnapshot=null;this.dateRange=null;this.loadRefresh();this.reload();this.loadRisk()}},
 mounted(){this.loadRefresh();this.loadIndicators();this.reload();this.loadRisk();this.poll=setInterval(()=>{this.clock=Date.now();if(this.autoRefresh && !document.hidden){this.reload();this.loadRisk()}this.nextRefresh=Date.now()+5000},5000);this.observer=new ResizeObserver(()=>{cancelAnimationFrame(this.resizeFrame);this.resizeFrame=requestAnimationFrame(()=>this.fitPanels())});this.observer.observe(this.$el);this.onTheme=()=>this.paintMarket();window.addEventListener('wt-theme-change',this.onTheme)},
 beforeUnmount(){++this.serial;clearInterval(this.poll);cancelAnimationFrame(this.resizeFrame);this.observer?.disconnect();window.removeEventListener('wt-theme-change',this.onTheme);this.marketChart?.dispose()}
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
.indicator-editor{display:flex;flex-direction:column;gap:10px;color:var(--wt-text);font-size:11px}.indicator-editor>strong{font-size:12px;font-weight:550}.indicator-editor>label{display:flex;align-items:center;gap:12px}.indicator-editor>label .el-input{flex:1}.indicator-editor small{color:var(--wt-muted);font-size:10px;line-height:1.6}.indicator-parameters{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.indicator-parameters>span{grid-column:1/-1;font-weight:550}.indicator-parameters>label{display:flex;flex-direction:column;gap:5px}.indicator-parameters .el-input-number{width:100%}.indicator-actions{display:flex;justify-content:flex-end;gap:8px}.indicator-error{color:var(--wt-danger);font-size:11px}.indicator-note{padding:0 12px 8px;color:var(--wt-muted);font-size:10px;line-height:1.6}
.terminal-quote{flex:0 0 auto;display:flex;align-items:center;gap:22px;padding:12px 0;border-top:1px solid var(--wt-line);font-size:11px;flex-wrap:wrap;}.quote-main{display:flex;align-items:baseline;gap:14px;}.quote-main strong{font-size:14px;font-weight:550;}.quote-main>span{font-family:var(--wt-mono);font-size:20px;font-weight:550;}.quote-details{display:flex;gap:18px;color:var(--wt-muted);}.quote-details b{font-weight:400;color:var(--wt-text);padding-left:4px;}.quote-time{margin-left:auto;color:var(--wt-muted);font-family:var(--wt-mono);font-size:10px;}
.quote-stale{font:10px var(--el-font-family);font-weight:400;margin-left:8px;color:var(--wt-warning);padding:2px 5px;border:1px solid var(--wt-line);border-radius:4px;}
.market-up{color:var(--wt-up);}.market-down{color:var(--wt-down);}
.market-chart-panel{position:relative;flex:0 0 360px;min-height:360px;}.market-chart{height:360px;width:100%;}.terminal-source{font-size:10px;color:var(--wt-muted);margin-left:auto;}.terminal-chart-empty{position:absolute;inset:40% 0 auto;text-align:center;font-size:12px;color:var(--wt-muted);}
.terminal-bottom{display:flex;min-height:260px;overflow:hidden;}.terminal-equity{display:flex;flex-direction:column;min-width:0;overflow:hidden;padding:10px 12px 8px 0;}
.terminal-ledger{flex:1;min-width:0;display:flex;flex-direction:column;overflow:hidden;padding-left:12px;}.terminal-ledger>.el-tabs{flex:0 0 40px;}.terminal-ledger :deep(.el-tabs__content){display:none;}.terminal-table{flex:1;min-height:0;overflow:auto;}.terminal-account{display:flex;gap:12px;padding:12px 0;flex-wrap:wrap;}.account-card{display:flex;flex-direction:column;gap:7px;font-size:11px;border:1px solid var(--wt-line);border-radius:7px;padding:12px;min-width:220px;}.account-card strong{font-weight:550;}.account-card b{font-weight:500;font-family:var(--wt-mono);}.account-card small{font-size:10px;color:var(--wt-muted);}
@media(max-width:1100px){.terminal-indicators{margin-left:0;}.terminal-bottom{overflow-x:auto;}.terminal-equity{min-width:180px;}.terminal-ledger{min-width:340px;}.quote-details{gap:10px;}}
.terminal-cockpit{display:grid;grid-template-columns:repeat(7,minmax(110px,1fr));gap:8px;border-bottom:1px solid var(--wt-line);padding:4px 0 14px;margin-bottom:10px}.terminal-cockpit>div{display:flex;flex-direction:column;gap:5px;padding:8px 10px;background:var(--wt-matte);border:1px solid var(--wt-line);border-radius:6px}.terminal-cockpit small{font-size:10px;color:var(--wt-muted)}.terminal-cockpit strong{font:16px var(--wt-mono);white-space:nowrap}.terminal-cockpit span{font-size:9px;color:var(--wt-muted);overflow:hidden;white-space:nowrap;text-overflow:ellipsis}.freshness-badge{display:inline-flex;padding:4px 6px;border:1px solid currentColor;border-radius:4px;font-size:10px;white-space:nowrap;color:var(--wt-muted)}.freshness-badge.live{color:var(--wt-success)}.freshness-badge.delayed{color:var(--wt-warning)}.freshness-badge.stale{color:var(--wt-danger)}.terminal-quote.freshness-stale{border:1px solid var(--wt-danger);padding:10px}.terminal-cockpit .equity-shortcut{align-self:flex-start;padding:0;height:16px;font-size:10px;margin-top:2px}
@media(max-width:1280px){.terminal-cockpit{grid-template-columns:repeat(4,minmax(100px,1fr))}.quote-time{margin-left:0}.terminal-toolbar{gap:8px}.terminal-history{gap:6px}.terminal-source{margin-left:0}}
@media(max-width:1024px){.terminal-bottom{flex-direction:column;flex-basis:auto!important;min-height:620px;overflow:visible}.terminal-equity{flex:0 0 260px!important;min-height:260px;padding-right:0}.terminal-ledger{flex:0 0 340px;min-height:340px;padding-left:0;border-top:1px solid var(--wt-line)}.terminal-bottom>.wt-resize-divider.x{display:none}.terminal-cockpit{grid-template-columns:repeat(3,minmax(100px,1fr))}.market-chart-panel,.market-chart{min-height:340px;height:340px;flex-basis:340px}.terminal{min-height:1150px}}
</style>
