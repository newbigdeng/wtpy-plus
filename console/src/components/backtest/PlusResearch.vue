<template>
 <section class="wt-plus-research">
  <div class="plus-heading"><div><h2>Plus 仿真回测</h2><p>历史行情驱动实盘链路，检查策略、执行和回报是否衔接。</p></div><el-button :loading="loading" @click="refresh">刷新任务</el-button></div>
  <div class="plus-path" aria-label="Plus 回测执行链路"><span>历史 Tick</span><b>→</b><span>{{engineType.toUpperCase()}} 实盘引擎</span><b>→</b><span>{{engineType.toLowerCase()==='cta'?'执行器':'策略委托'}}</span><b>→</b><span>TraderAdapter</span><b>→</b><span>TraderMocker</span></div>
  <el-alert title="任务使用隔离目录、历史时钟和模拟交易插件。不会启动 CTP；与快速 Mocker 回测分别保存。" type="info" :closable="false" show-icon/>
  <el-alert v-if="unsupported" :title="unsupported" type="warning" :closable="false"/>
  <el-form label-position="top" class="plus-config" :disabled="launching" @submit.prevent="launch">
   <el-form-item label="历史数据集"><el-select v-model="config.dataset" aria-label="Plus历史数据集" :disabled="launching" @change="selectDataset"><el-option v-for="item in capabilities.datasets||[]" :key="item.id" :value="item.id" :label="item.label"/></el-select></el-form-item>
   <el-form-item label="合约"><el-input :model-value="selectedDataset?.code||'—'" readonly aria-label="Plus合约"/></el-form-item>
   <el-form-item label="开始时间"><el-input v-model="config.start" aria-label="Plus开始时间" placeholder="沿用数据集起点"/></el-form-item>
   <el-form-item label="结束时间"><el-input v-model="config.end" aria-label="Plus结束时间" placeholder="沿用数据集终点"/></el-form-item>
   <el-form-item label="初始资金"><el-input-number v-model="capitalInput" :min="1" :max="1000000000" :step="10000" aria-label="Plus初始资金"/></el-form-item>
   <el-form-item label="主K线周期"><el-select v-model="config.period" aria-label="Plus周期"><el-option label="1分钟" value="m1"/><el-option label="5分钟" value="m5"/></el-select></el-form-item>
   <el-form-item v-if="engineType.toLowerCase()==='cta'" label="CTA 执行器"><el-select v-model="execution.id" aria-label="Plus执行策略" @change="resetExecution"><el-option v-for="item in capabilities.executors||[]" :key="item.id" :value="item.id" :label="item.label"/></el-select><small>与实盘复用 MinImpact、TWAP、VWAP 执行器。</small></el-form-item>
   <el-form-item v-else label="委托方式"><span class="plus-direct-order">{{engineType.toUpperCase()==='HFT'?'HFT 由策略直接委托，不使用 CTA 执行器。':'当前引擎不支持 Plus 回测。'}}</span></el-form-item>
   <div v-if="engineType.toLowerCase()==='cta'" class="plus-custom-fees">
    <el-form-item v-for="item in executionFields" :key="item.key" :label="item.label"><el-input-number v-model="execution.params[item.key]" :min="item.min??0" :max="item.max??1000000" :precision="0" :aria-label="'执行'+item.label"/></el-form-item>
    <el-form-item v-if="execution.id==='vwap'" label="各时段成交量权重（逗号分隔）"><el-input v-model="execution.weights" type="textarea" aria-label="VWAP成交量权重" placeholder="例如 1,2,4,2,1"/><small>窗口等分为这些时段。请填历史估计曲线；不会读取回放未来成交量。</small></el-form-item>
   </div>
   <el-form-item label="手续费模板"><el-select v-model="finance.feeMode" aria-label="Plus手续费模板"><el-option value="dataset" :label="datasetFees?.known?'沿用数据集费用':'数据集费用未配置'"/><el-option value="custom" label="自定义 WT 费用参数"/></el-select></el-form-item>
   <el-form-item label="保证金比例覆盖（%，留空沿用配置）"><el-input-number v-model="finance.marginPercent" :min="0.01" :max="100" :precision="4" :controls="false" placeholder="沿用合约配置" aria-label="Plus保证金比例覆盖"/></el-form-item>
   <el-form-item label="不利滑点（最小变动跳数）"><el-input-number v-model="finance.slippageTicks" :min="0" :max="10000" :precision="0" aria-label="Plus滑点跳数"/></el-form-item>
   <div v-if="finance.feeMode==='custom'" class="plus-custom-fees">
    <el-form-item label="计费方式"><el-select v-model="finance.fees.byvolume" aria-label="Plus计费方式"><el-option :value="true" label="按手数（元/手）"/><el-option :value="false" label="按成交金额比例"/></el-select></el-form-item>
    <el-form-item v-for="item in feeFields" :key="item.key" :label="item.label+(item.key==='minfee'?'（元）':finance.fees.byvolume?'（元/手）':'（比例，例如 0.0001）')"><el-input-number v-model="finance.fees[item.key]" :min="0" :precision="8" :controls="false" :aria-label="'Plus'+item.label"/></el-form-item>
   </div>
   <el-form-item class="plus-launch"><el-button type="success" :loading="launching" :disabled="Boolean(unsupported)||!selectedDataset" @click="launch">保存并运行 Plus</el-button></el-form-item>
  </el-form>
  <p class="plus-finance-note" v-if="selectedDataset">{{financeDescription}}</p>
  <p class="plus-dataset-note" v-if="selectedDataset">{{selectedDataset.source}} · {{selectedDataset.ticks||0}} 条Tick · 时间格式与数据集一致：{{selectedDataset.start}} — {{selectedDataset.end}}</p>
  <p class="plus-finance-note">滑点受委托限价与涨跌停限制，实际成本可能小于配置值。保证金按多、空持仓分别累计，不计算锁仓优惠。</p>
  <details class="plus-limits"><summary>模拟范围与结果口径</summary><ul><li v-for="text in capabilities.limitations||[]" :key="text">{{text}}</li><li>有限档位历史行情无法重建真实排队和市场冲击；结果用于链路验证，收益需结合模拟假设解释。</li></ul></details>
  <div class="plus-task-heading"><h3>Plus 任务</h3><span>{{tasks.length}} 次</span></div>
  <el-table :data="tasks" stripe class="plus-tasks" empty-text="尚无 Plus 任务。选择数据集后保存并运行。">
   <el-table-column label="创建时间" min-width="150"><template #default="{row}">{{time(row.created_at)}}</template></el-table-column>
   <el-table-column prop="engine_type" label="引擎" width="70"/><el-table-column label="执行策略" min-width="115"><template #default="{row}">{{row.execution?.label|| (row.engine_type==='HFT'?'策略直接委托':'MinImpact')}}</template></el-table-column>
   <el-table-column prop="code" label="合约" min-width="130"/>
   <el-table-column label="状态 / 进度" min-width="150"><template #default="{row}"><span :class="'plus-state '+row.status">{{stateName(row.status)}}</span><el-progress v-if="['queued','running'].includes(row.status)" :percentage="Math.min(100,Math.max(0,Number(row.progress)||0))" :stroke-width="3" :show-text="false"/></template></el-table-column>
   <el-table-column label="回放Tick" width="120"><template #default="{row}">{{row.processed||0}} / {{row.total||0}}</template></el-table-column>
   <el-table-column label="操作" width="150"><template #default="{row}"><el-button link @click="open(row)">详情</el-button><el-button v-if="['queued','running'].includes(row.status)" link type="danger" @click="stop(row)">停止</el-button></template></el-table-column>
  </el-table>
  <section v-if="detail?.task" class="plus-detail">
   <div class="plus-detail-heading"><h3>仿真详情</h3><span :class="'plus-state '+detail.task.status">{{stateName(detail.task.status)}}</span><el-button link @click="loadDetail">刷新详情</el-button><el-button link @click="closeDetail">收起</el-button></div>
   <el-alert v-if="detail.task.error" :title="detail.task.error" type="error" :closable="false"/>
   <div class="plus-proof"><span>委托 {{rows('orders').length}}</span><span>成交 {{rows('trades').length}}</span><span>持仓记录 {{rows('positions').length}}</span><span>{{detail.task.processed||0}} 条历史行情</span></div>
   <p class="plus-dataset-note">委托受理不等于成交。资金与费用读取原生模拟柜台记录；拒单原因见“拒单”页签。</p>
   <PlusFinanceReport :detail="detail"/><details v-if="detail.task.execution" class="plus-limits"><summary>执行策略与参数快照 · {{detail.task.execution.label}}</summary><pre class="plus-log">{{JSON.stringify(detail.task.execution,null,2)}}</pre></details>
   <div v-show="equityPoints.length" ref="equityChart" class="plus-equity" role="img" aria-label="Plus模拟权益曲线"/>
   <el-tabs v-model="ledgerTab">
    <el-tab-pane v-for="item in ledgers" :key="item.id" :name="item.id" :label="item.label"/>
   </el-tabs>
   <pre v-if="ledgerTab==='log'" class="plus-log">{{logText||'暂无日志'}}</pre>
   <pre v-else-if="ledgerTab==='route'" class="plus-log">{{routeText}}</pre>
   <el-table v-else :data="visibleRows" stripe max-height="360" empty-text="本任务暂无该类记录。" class="plus-ledger">
    <el-table-column v-for="field in columns" :key="field" :prop="field" :label="fieldName(field)" min-width="125" show-overflow-tooltip><template #default="{row}">{{cell(row[field])}}</template></el-table-column>
   </el-table>
   <small class="plus-dataset-note" v-if="rows(ledgerTab).length>1000">本页展示前1000条，完整记录保存在任务隔离目录。</small>
  </section>
 </section>
</template>

<script>
import * as echarts from 'echarts'
import PlusFinanceReport from './PlusFinanceReport.vue'
const running=task=>['queued','running'].includes(task.status)
export default {
 name:'PlusResearch',components:{PlusFinanceReport},
 props:{strategyid:{type:String,required:true},engineType:{type:String,default:'cta'},beforeSave:{type:Function,required:true},capital:{type:Number,default:1000000},active:{type:Boolean,default:true},guest:{type:Boolean,default:false}},
 emits:['update:capital'],
 data:()=>({capabilities:{datasets:[],engines:[],executors:[],limitations:[]},execution:{id:'minimpact',params:{pricemode:1,offset:0,expire:5,span:0,byrate:false,lots:1},weights:''},config:{dataset:'',start:'',end:'',period:'m1'},finance:{feeMode:'dataset',fees:{byvolume:true,open:null,close:null,closetoday:null,minfee:0},marginPercent:null,slippageTicks:0},feeFields:[{key:'open',label:'开仓费率'},{key:'close',label:'平仓费率'},{key:'closetoday',label:'平今费率'},{key:'minfee',label:'每次成交最低费'}],tasks:[],loading:false,launching:false,detail:null,ledgerTab:'trades',generation:0,ledgers:[{id:'trades',label:'成交'},{id:'orders',label:'委托'},{id:'positions',label:'持仓'},{id:'funds',label:'资金'},{id:'rejections',label:'拒单'},{id:'events',label:'事件'},{id:'route',label:'链路证据'},{id:'log',label:'运行日志'}]}),
 computed:{
  capitalInput:{get(){return this.capital},set(value){this.$emit('update:capital',value)}},
  executionFields(){return this.execution.id==='minimpact'?[{key:'pricemode',label:'价格模式（-1本方／0最新／1对手／2自动）',min:-1,max:2},{key:'offset',label:'委托偏移跳数',min:-1000,max:1000},{key:'expire',label:'挂单超时秒',max:86400},{key:'span',label:'最短发单间隔毫秒',max:86400000},{key:'lots',label:'单次委托手数',min:1}]:[{key:'begin_time',label:'窗口开始（HHMM）',max:2359},{key:'end_time',label:'窗口结束（HHMM）',max:2359},{key:'ord_sticky',label:'挂单超时秒',max:86400},{key:'tail_secs',label:'窗口收尾秒数',max:86400},{key:'total_times',label:'拆单次数',min:1,max:10000},{key:'price_mode',label:'价格模式（0最新／1本方／2对手）',max:2},{key:'price_offset',label:'委托偏移跳数',max:1000},{key:'minopenlots',label:'最小拆单手数',min:1}]},
  selectedDataset(){return(this.capabilities.datasets||[]).find(item=>item.id===this.config.dataset)},
  datasetFees(){return this.selectedDataset?.finance?.fees},
  financeDescription(){const fees=this.datasetFees,margin=this.selectedDataset?.finance?.margin;return (this.finance.feeMode==='custom'?'手续费：自定义 WT 参数':'手续费：'+(fees?.known?fees.source||'数据集费用配置':'缺失，请填写自定义费用'))+' · 保证金：'+(this.finance.marginPercent!=null?this.finance.marginPercent+'%':margin?.known?(margin.source||'合约配置')+'（多 '+this.percentage(margin.long)+' / 空 '+this.percentage(margin.short)+'）':'缺失，请填写覆盖比例')+' · 滑点按不利方向作用于模拟成交，不修改实盘参数'},
  unsupported(){return this.capabilities.available===false?'Plus 原生运行库尚未就绪，请先完成运行环境构建。':this.capabilities.engines.length&&!this.capabilities.engines.includes(this.engineType.toUpperCase())?'当前 Plus 尚不支持 '+this.engineType.toUpperCase()+'；可继续使用快速回测。':''},
  visibleRows(){return this.rows(this.ledgerTab).slice(0,1000)},
  columns(){return [...new Set(this.visibleRows.slice(0,20).flatMap(row=>Object.keys(row)))].filter(key=>!key.startsWith('_')).slice(0,16)},
  logText(){const value=this.detail?.log_tail;return Array.isArray(value)?value.join('\n'):value||''},
  routeText(){return typeof this.detail?.route==='string'?this.detail.route:JSON.stringify(this.detail?.route||{},null,2)},
  equityPoints(){return this.rows('funds').filter(row=>(row.equity??row.balance)!=null&&Number.isFinite(Number(row.equity??row.balance))).map((row,index)=>[String(row.time??row.timestamp??row.date??index),Number(row.equity??row.balance)])}
 },
 methods:{
  async request(path,payload={}){const response=await fetch('/bt/plus/'+path,{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify(payload)});const value=await response.json();if(!response.ok||value.result<0)throw new Error(value.message||'Plus请求失败');return value},
  stateName(value){return{queued:'排队中',running:'运行中',completed:'已完成',stopped:'已停止',failed:'失败',interrupted:'已中断'}[value]||value},
  time(value){if(!value)return'—';if(typeof value==='number')return new Date(value<1e12?value*1000:value).toLocaleString();return String(value).replace('T',' ').slice(0,19)},
  cell(value){return value===null||value===undefined?'—':typeof value==='object'?JSON.stringify(value):String(value)},
  fieldName(key){return{time:'时间',timestamp:'时间戳',date:'日期',code:'合约',price:'价格',volume:'数量',qty:'数量',direction:'方向',offset:'开平',order_id:'订单编号',trade_id:'成交编号',localid:'本地编号',status:'状态',equity:'权益',balance:'余额',fee:'手续费',fees:'手续费',profit:'盈亏',position:'持仓',action:'事件',message:'说明',type:'类型',side:'方向',commission:'手续费',margin:'已用保证金',frozen_margin:'冻结保证金',frozen_commission:'冻结手续费',available:'可用资金',slippage_cost:'滑点成本',error_code:'WT 分类码',broker_error_code:'柜台错误码',category:'拒单类型',reason:'原因'}[key]||key},
  rows(key){return Array.isArray(this.detail?.[key])?this.detail[key]:[]},
  selectDataset(){const item=this.selectedDataset;this.config.start=item?.start?String(item.start):'';this.config.end=item?.end?String(item.end):'';const fees=item?.finance?.fees;this.finance={feeMode:'dataset',fees:{byvolume:fees?.byvolume??true,open:fees?.open??null,close:fees?.close??null,closetoday:fees?.closetoday??null,minfee:fees?.minfee??0},marginPercent:null,slippageTicks:0};this.resetExecution()},
  resetExecution(){const begin=Number(String(this.config.start||this.selectedDataset?.start||'0900').slice(-4)),rawEnd=Number(String(this.config.end||this.selectedDataset?.end||'0905').slice(-4)),minute=Math.floor(begin/100)*60+begin%100+1;const end=rawEnd>begin?rawEnd:Math.floor(minute/60)*100+minute%60;this.execution.params=this.execution.id==='minimpact'?{pricemode:1,offset:0,expire:5,span:0,byrate:false,lots:1}:{begin_time:begin,end_time:end,ord_sticky:5,tail_secs:5,total_times:5,price_mode:2,price_offset:0,lots:1,minopenlots:1}},
  executionPayload(){if(this.engineType.toLowerCase()!=='cta')return null;const value={id:this.execution.id,params:{...this.execution.params}};if(value.id==='vwap'){value.volume_weights=this.execution.weights.split(/[,，\s]+/).filter(Boolean).map(Number);if(value.volume_weights.length<2||value.volume_weights.some(x=>!Number.isFinite(x)||x<0)||!value.volume_weights.some(x=>x>0))throw new Error('VWAP需至少两个非负权重，且总和大于0')}return value},
  percentage(value){return value==null?'未知':(Number(value)*100).toFixed(2)+'%'},
  financePayload(){const value={slippage_ticks:this.finance.slippageTicks};if(!Number.isInteger(value.slippage_ticks)||value.slippage_ticks<0)throw new Error('滑点必须是非负整数跳数');if(this.finance.marginPercent!=null){if(!Number.isFinite(this.finance.marginPercent)||this.finance.marginPercent<=0||this.finance.marginPercent>100)throw new Error('保证金比例须大于 0 且不超过 100%');value.marginrate=this.finance.marginPercent/100}else if(!this.selectedDataset?.finance?.margin?.known)throw new Error('数据集缺少保证金比例，请填写覆盖比例');if(this.finance.feeMode==='custom'){for(const field of this.feeFields){const amount=this.finance.fees[field.key];if(amount==null||!Number.isFinite(amount)||amount<0)throw new Error('请填写完整的非负手续费参数')}value.fees={...this.finance.fees}}else if(!this.datasetFees?.known)throw new Error('数据集缺少手续费，请选择自定义并填写费用');return value},
  async refresh(){const generation=this.generation,id=this.strategyid;this.loading=true;try{const value=await this.request('list',{straid:id});if(generation===this.generation)this.tasks=value.tasks||[]}catch(error){if(generation===this.generation)this.$message.error(error.message)}finally{if(generation===this.generation)this.loading=false}},
  async launch(){
   if(this.guest){this.$message.warning('游客模式不支持操作');return}
   if(this.launching||this.unsupported||!this.selectedDataset)return
   const straid=this.strategyid,generation=this.generation
   this.launching=true
   try{
    for(const value of [this.config.start,this.config.end])if(value&&!/^\d{8,17}$/.test(String(value)))throw new Error('起止时间请使用数据集所示的数字格式')
    const finance=this.financePayload(),execution=this.executionPayload()
    if(!await this.beforeSave())return
    if(generation!==this.generation)return
    const value=await this.request('run',{straid,dataset:this.config.dataset,code:this.selectedDataset.code,start:this.config.start||null,end:this.config.end||null,capital:this.capital,period:this.config.period,finance,execution})
    if(generation!==this.generation)return
    this.$message.success('Plus 仿真任务已启动');await this.refresh();if(value.task)await this.open(value.task)
   }catch(error){this.$message.error(error.message)}finally{this.launching=false}
  },
  async open(task){this.detail={task};await this.loadDetail()},
  async loadDetail(){const id=this.detail?.task?.id,generation=this.generation;if(!id)return;try{const value=await this.request('detail',{taskid:id});if(generation===this.generation&&this.detail?.task?.id===id){this.detail=value;this.$nextTick(this.drawEquity)}}catch(error){this.$message.error(error.message)}},
  async stop(task){if(this.guest){this.$message.warning('游客模式不支持操作');return}try{await this.$confirm('停止本次历史回放并保留已生成记录？','停止 Plus 任务',{confirmButtonText:'停止',cancelButtonText:'取消',type:'warning'});await this.request('stop',{taskid:task.id});await this.refresh();if(this.detail?.task?.id===task.id)await this.loadDetail()}catch(error){if(error instanceof Error)this.$message.error(error.message)}},
  closeDetail(){this._equity?.dispose();this._equity=null;this.detail=null},
  drawEquity(){const node=this.$refs.equityChart;if(!node||!this.equityPoints.length)return;this._equity??=echarts.init(node);const colour=getComputedStyle(document.documentElement).getPropertyValue('--wt-text').trim(),line=getComputedStyle(document.documentElement).getPropertyValue('--wt-success').trim();this._equity.setOption({animation:false,tooltip:{trigger:'axis'},grid:{left:85,right:30,top:20,bottom:35},xAxis:{type:'category',data:this.equityPoints.map(item=>item[0]),axisLabel:{color:colour}},yAxis:{type:'value',scale:true,axisLabel:{color:colour}},series:[{type:'line',showSymbol:false,data:this.equityPoints.map(item=>item[1]),lineStyle:{color:line,width:1.5}}]});this._equity.resize()}
 },
 watch:{strategyid(){++this.generation;this.closeDetail();this.tasks=[];this.refresh()},active(value){if(value)this.$nextTick(this.drawEquity)}},
 async mounted(){try{const value=await this.request('capabilities');if(this._disposed)return;this.capabilities=value;this.config.dataset=value.datasets?.[0]?.id||'';this.selectDataset()}catch(error){if(!this._disposed)this.$message.error(error.message)}if(this._disposed)return;await this.refresh();if(this._disposed)return;this._poll=setInterval(()=>{if(this.tasks.some(running)){this.refresh();if(this.detail?.task&&running(this.detail.task))this.loadDetail()}},1500);this._resize=()=>this._equity?.resize();window.addEventListener('resize',this._resize);this._theme=new MutationObserver(()=>this.drawEquity());this._theme.observe(document.documentElement,{attributes:true,attributeFilter:['data-wt-theme']})},
 beforeUnmount(){this._disposed=true;++this.generation;clearInterval(this._poll);window.removeEventListener('resize',this._resize);this._theme?.disconnect();this._equity?.dispose()}
}
</script>

<style scoped>
.wt-plus-research{flex:1;min-height:0;overflow:auto;padding:20px 0;color:var(--wt-text)}
.plus-heading,.plus-detail-heading,.plus-task-heading{display:flex;align-items:center;gap:14px;justify-content:space-between}.plus-heading h2{font-size:18px;margin:0 0 7px;font-weight:550}.plus-heading p,.plus-dataset-note{font-size:11px;line-height:1.8;color:var(--wt-muted);margin:8px 0}.plus-path{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:22px 0;font-size:11px}.plus-path span{padding:7px 10px;border:1px solid var(--wt-line);border-radius:6px;background:var(--wt-matte)}.plus-path b{color:var(--wt-muted);font-weight:400}
.plus-config{display:grid;grid-template-columns:repeat(3,minmax(130px,1fr));gap:0 16px;margin:20px 0 0}.plus-config .el-select,.plus-config .el-input-number{width:100%}.plus-custom-fees{display:grid;grid-template-columns:repeat(3,minmax(130px,1fr));gap:0 16px;grid-column:1/-1;border:1px solid var(--wt-line);border-radius:7px;padding:14px 14px 0}.plus-finance-note{font-size:11px;line-height:1.8;color:var(--wt-muted);margin:8px 0}.plus-launch{align-self:end}.plus-limits{font-size:11px;line-height:1.8;color:var(--wt-muted);margin:12px 0 24px}.plus-limits summary{cursor:pointer}.plus-limits ul{padding-left:20px}.plus-task-heading{justify-content:flex-start}.plus-task-heading h3,.plus-detail-heading h3{font-size:14px;font-weight:550}.plus-task-heading span{font-size:11px;color:var(--wt-muted)}.plus-tasks{font-size:11px}.plus-state{font-size:11px}.plus-state.completed,.plus-state.running{color:var(--wt-success)}.plus-state.failed{color:var(--wt-danger)}.plus-state.stopped{color:var(--wt-muted)}
.plus-detail{margin-top:24px;padding-top:12px;border-top:1px solid var(--wt-line)}.plus-detail-heading{justify-content:flex-start}.plus-detail-heading .el-button:first-of-type{margin-left:auto}.plus-proof{display:flex;gap:20px;font-size:12px;margin:18px 0;color:var(--wt-muted)}.plus-log{font:11px/1.8 var(--wt-mono);white-space:pre-wrap;word-break:break-word;padding:15px;border:1px solid var(--wt-line);border-radius:7px;max-height:360px;overflow:auto;background:var(--wt-matte);color:var(--wt-text)}.plus-equity{height:220px;width:100%;margin-top:16px}.plus-ledger{width:100%}
@media(max-width:1000px){.plus-config,.plus-custom-fees{grid-template-columns:repeat(2,minmax(130px,1fr))}.plus-proof{flex-wrap:wrap;gap:10px}}
</style>
