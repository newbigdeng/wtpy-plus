<template>
 <section class="managed-strategies" v-loading="loading">
  <template v-if="create">
   <p class="managed-intro">从已有组合复用运行环境，创建独立的策略、账簿和启动计划。账户凭据只在服务器端处理。</p>
   <el-form label-position="top" @submit.prevent="createGroup">
    <div class="managed-form-grid">
     <el-form-item label="组合 ID"><el-input v-model="newGroup.groupid" aria-label="新组合ID" placeholder="例如 trend_rb" maxlength="40"/></el-form-item>
     <el-form-item label="显示名称"><el-input v-model="newGroup.name" aria-label="新组合名称" placeholder="例如 螺纹趋势组合" maxlength="80"/></el-form-item>
     <el-form-item label="环境模板"><el-select v-model="newGroup.template_groupid" aria-label="新组合环境模板"><el-option v-for="item in catalog.templates||[]" :key="item.id" :value="item.id" :label="item.name+' · '+item.engine_type"/></el-select></el-form-item>
     <el-form-item label="引擎类型"><el-select v-model="newGroup.engine_type" aria-label="新组合引擎类型"><el-option v-for="item in catalog.engine_types||['CTA','HFT']" :key="item" :value="item" :label="item"/></el-select></el-form-item>
    </div>
    <el-alert title="创建后先导入策略、配置参数并应用启动计划。不会启动进程，进程守护与定时启动默认关闭。" type="info" :closable="false" show-icon/>
    <div class="managed-footer"><el-button @click="$emit('cancel')">取消</el-button><el-button type="primary" :loading="saving" @click="createGroup">创建并配置策略</el-button></div>
   </el-form>
  </template>
  <template v-else>
   <div class="managed-heading">
    <div><h3>启动计划</h3><p>从策略库选择版本，配置实例、执行器和通道。保存、应用与启动分开操作。</p></div>
    <el-button :loading="loading" @click="load">刷新计划</el-button>
   </div>
   <div class="managed-status"><el-tag :type="processRunning?'success':'info'">{{processRunning?'进程运行中 · 配置只读':'进程已停止'}}</el-tag><el-tag effect="plain">{{state.managed?'页面管理入口':'页面计划待应用'}}</el-tag><span>{{state.pending_changes?'有草稿等待应用':hasApplied?'启动配置与已保存计划一致':'尚未应用页面计划'}}</span></div>
   <div class="managed-applied" role="status" aria-label="当前启动配置授权"><strong>当前启动配置</strong><template v-if="state.managed&&hasApplied"><el-tag :type="state.applied_trading_enabled?'danger':'info'">{{state.applied_trading_enabled?'允许交易':'禁止启动策略'}}</el-tag><span>应用版本 {{String(state.applied_revision).slice(0,8)}}</span></template><span v-else>页面计划尚未成为有效启动配置</span><p v-if="state.pending_changes||dirty">草稿尚未应用。实际启动继续使用当前启动配置，草稿中的交易授权不会改变它。</p></div>
   <el-alert v-if="!state.managed" type="warning" :closable="false" show-icon title="原有入口中的策略不会自动迁移。先逐一导入并核对；应用时将切换到页面管理入口，原 run.py 保留。"/>
   <el-alert v-if="!supportedEngine" type="info" :closable="false" show-icon title="页面托管交易组合目前支持 CTA 和 HFT；SEL 原生回测保持可用。现有 SEL 启动入口保持原样。"/>
   <p v-if="processRunning" class="managed-note">请先停止组合，再修改策略计划。停止进程不会自动平仓，请先核对持仓与未成交委托。</p>
   <template v-if="state.existing_strategies?.length"><h4>已有运行或账簿策略</h4><el-table :data="state.existing_strategies" stripe><el-table-column label="策略" min-width="170"><template #default="{row}"><strong>{{row.name||row.id}}</strong><small>{{row.id}}</small></template></el-table-column><el-table-column label="持仓" width="150"><template #default="{row}"><el-tag :type="row.has_positions?'warning':'info'">{{positionLabel(row)}}</el-tag></template></el-table-column><el-table-column label="启动归属" min-width="230"><template #default="{row}">尚未加入页面计划<small>保留原入口与历史账簿；不会自动接管或复制源码。</small></template></el-table-column></el-table></template>
   <div class="managed-toolbar"><strong>{{plan.engine_type}} 页面策略 · {{plan.strategies.length}}</strong><el-button type="primary" plain :disabled="!isGuest&&!editable" @click="addStrategy">从策略库添加</el-button></div>
   <el-table :data="plan.strategies" stripe empty-text="尚无页面管理策略。请从策略库选择策略并配置实例。">
    <el-table-column label="策略实例" min-width="160"><template #default="{row}"><strong>{{row.name||row.id}}</strong><small>{{row.id}} · {{row.class_name}}</small><small>{{row.library_id?row.library_id+' · v'+row.library_version:'旧计划固定快照'}}</small></template></el-table-column>
    <el-table-column label="运行合约 / 计划合约" min-width="210"><template #default="{row}"><strong>{{appliedContracts(row).join('、')||'尚无已应用合约'}}</strong><small>{{processRunning?'当前启动配置':'启动配置 · 进程已停止'}}</small><small>计划：{{plannedContracts(row).join('、')||'未识别'}}</small><el-button v-if="isAdmin&&typeof row.params?.code==='string'" link :aria-label="'管理策略合约 '+row.id" @click="openContracts(row)">添加 / 删除合约</el-button></template></el-table-column>
    <el-table-column label="来源与持仓" min-width="180"><template #default="{row}"><el-tag effect="plain">{{state.strategy_status?.[row.id]?.new_strategy!==false?'新增实例':'已有策略'}}</el-tag><small>{{positionLabel(state.strategy_status?.[row.id])}}</small><small v-if="state.strategy_status?.[row.id]?.plan_pending">等待应用或下次启动</small></template></el-table-column>
    <el-table-column label="下次启动" width="110"><template #default="{row}"><el-tag :type="row.enabled?'success':'info'" effect="plain">{{row.enabled?'加载':'不加载'}}</el-tag></template></el-table-column>
    <el-table-column label="交易通道" min-width="130"><template #default="{row}">{{channelName(row.trader)}}</template></el-table-column>
    <el-table-column label="执行方式" min-width="130"><template #default="{row}">{{row.engine_type==='HFT'?'策略直接委托':executorName(row.execution?.id)}}</template></el-table-column>
    <el-table-column label="操作" width="190"><template #default="{row}"><el-button link @click="view(row)">查看</el-button><el-button link :disabled="!isGuest&&!editable" @click="edit(row)">编辑</el-button><el-button link type="danger" :disabled="!isGuest&&!editable" @click="remove(row)">移除</el-button></template></el-table-column>
   </el-table>
   <RiskSelection v-model="plan.risk" :engine-type="plan.engine_type" :disabled="!editable" @update:model-value="dirty=true"/>
   <p class="managed-note">风控选择随启动计划生效。请停机应用并使用页面管理入口；原 run.py 不会自动接入交易前风控。</p>
   <div class="managed-trading"><div><strong>草稿交易授权</strong><p>{{plan.trading_enabled?'应用并手动启动后，启用策略可向所选通道提交交易。':'应用关闭授权的草稿后，页面管理入口将拒绝启动策略；不影响独立数据中台采集。'}}</p></div><el-switch :model-value="Boolean(plan.trading_enabled)" :disabled="!isGuest&&!editable" active-text="允许交易" inactive-text="禁止启动策略" aria-label="组合草稿交易授权" @change="setTrading"/></div>
   <p class="managed-note">CTA 执行器按策略路由。相同账户、相同合约使用多个执行器时，应避免相互抵消或反向委托。策略“加载”设置在下次启动生效。</p>
   <div class="managed-footer"><span>{{dirty?'页面有未保存修改':('草稿版本 '+(plan.revision??0))}}</span><el-button :disabled="!isGuest&&(!editable||saving)" :loading="saving" @click="save(false)">保存草稿</el-button><el-button type="primary" :disabled="!isGuest&&(!editable||saving)" :loading="applying" @click="save(true)">应用到启动配置</el-button></div>
   <p class="managed-note">应用会生成独立的启动入口与配置，并关闭该组合的守护和定时启动。应用完成后仍需管理员手动启动进程。</p>
  </template>

  <el-dialog v-model="showContracts" title="策略合约与预算" width="min(760px, 96vw)" append-to-body :close-on-click-modal="false">
   <p class="managed-note">每个合约使用独立实例与账簿；保证金和预计风险预算按所选合约均分。只修改页面草稿，之后仍需保存、应用并手动启动。</p>
   <p class="managed-note">快捷添加限于当前品种的其他月份。跨品种运行前，请先确认策略兼容；采集其他品种行情请使用“订阅合约”。新实例的合约也需先加入采集订阅。</p>
   <el-alert v-if="processRunning" title="组合运行中，只能查看。请先处理持仓和委托，再停止组合修改合约。" type="info" :closable="false"/>
   <el-alert v-if="contractError" :title="contractError" type="error" :closable="false"/>
   <el-select-v2 v-model="contractSelection" class="managed-contract-select" :options="contractOptions" multiple filterable clearable :disabled="!editable||contractLoading" placeholder="选择运行合约" aria-label="策略运行合约" @change="contractPreview=null"/>
   <p class="managed-note">删除全部合约会从计划移除此组实例，历史账簿保留。均分后预算不足一手时，策略将跳过开仓。</p>
   <el-table v-if="contractPreview" :data="contractPreview.instances" size="small" empty-text="将移除此策略的全部合约实例">
    <el-table-column prop="code" label="计划合约" min-width="160"/>
    <el-table-column label="订阅配置" min-width="110"><template #default="{row}">{{contractCatalog.find(item=>item.code===row.code)?.selected?'已保存订阅':'尚未订阅'}}</template></el-table-column>
    <el-table-column label="保证金预算上限" min-width="150"><template #default="{row}">{{contractBudget(row,'margin_fraction')}}</template></el-table-column>
    <el-table-column label="预计风险预算上限" min-width="150"><template #default="{row}">{{contractBudget(row,'risk_fraction')}}</template></el-table-column>
   </el-table>
   <p v-if="contractPreview" class="managed-note">新增：{{contractPreview.added.join('、')||'无'}}；移除：{{contractPreview.removed.join('、')||'无'}}。尚未改变启动配置。</p>
   <template #footer><el-button @click="showContracts=false">关闭</el-button><el-button :disabled="!editable||contractLoading" :loading="contractPreviewing" @click="previewContracts">预览合约与均分预算</el-button><el-button type="primary" :disabled="!editable||!contractPreview" @click="commitContracts">加入页面草稿</el-button></template>
  </el-dialog>

  <el-dialog v-model="showEditor" :title="(viewOnly?'查看策略：':editingId?'编辑策略：':'导入策略')+(draft.name||draft.id||'')" width="min(900px, 96vw)" append-to-body :close-on-click-modal="false">
   <div class="managed-editor">
    <el-alert v-if="!viewOnly" title="只静态解析类与构造参数，不执行导入的代码。源码会在管理员之后手动启动组合时运行，请只导入可信策略。" type="info" :closable="false"/>
    <el-alert v-if="!isAdmin" title="可以查看策略实例、通道和执行器。源码与构造参数仅管理员可见。" type="info" :closable="false"/>
    <div v-if="!viewOnly" class="managed-file"><el-select v-model="selectedLibrary" aria-label="组合选择库策略" placeholder="从对应类型的策略库选择" filterable @change="selectLibrary"><el-option v-for="item in library" :key="item.id" :value="item.id" :label="item.name+' · v'+item.version+' · '+item.class_name"/></el-select><el-button v-if="draft.library_id" :loading="analyzing" @click="selectLibrary(draft.library_id)">读取库最新版本</el-button><span v-if="draft.library_id">已固定 {{draft.library_id}} v{{draft.library_version}}</span><router-link to="/strategies">打开策略管理</router-link></div>
    <el-form label-position="top" :disabled="viewOnly" @submit.prevent="commitStrategy">
     <div class="managed-form-grid">
      <el-form-item label="实例 ID（用于引擎与账簿）"><el-input v-model="draft.id" :disabled="Boolean(editingId)" aria-label="策略实例ID" placeholder="例如 rb_trend_1" maxlength="40"/></el-form-item>
      <el-form-item label="显示名称"><el-input v-model="draft.name" aria-label="策略显示名称" placeholder="例如 螺纹趋势" maxlength="80"/></el-form-item>
      <el-form-item label="策略类"><el-select v-model="draft.class_name" aria-label="导入策略类" :disabled="!classes.length" @change="selectClass"><el-option v-for="item in classes" :key="item.name" :value="item.name" :label="item.name+' · '+item.engine_type"/><el-option v-if="!classes.length&&draft.class_name" :value="draft.class_name" :label="draft.class_name"/></el-select></el-form-item>
      <el-form-item label="交易通道"><el-select v-model="draft.trader" aria-label="策略交易通道"><el-option v-for="item in catalog.channels||[]" :key="item.id" :value="item.id" :label="item.label||item.id"/></el-select></el-form-item>
      <el-form-item label="下次启动加载"><el-switch v-model="draft.enabled" active-text="加载策略" inactive-text="保留配置，不加载"/></el-form-item>
      <el-form-item label="策略信号滑点（最小变动跳数）"><el-input-number v-model="draft.slippage" :min="0" :max="1000" :precision="0" aria-label="策略信号滑点"/></el-form-item>
      <el-form-item v-if="draft.engine_type==='HFT'" label="HFT 数据托管"><el-switch v-model="draft.agent" active-text="WT 记录资金与成交" inactive-text="不生成策略记录"/></el-form-item>
     </div>
     <h4>构造参数</h4><p v-if="!isAdmin" class="managed-note">构造参数仅管理员可见。</p><template v-else><p class="managed-note">实例 ID 作为构造函数的 name 传入。其余参数根据默认值识别类型，也可手动选择文字、数字、开关或 JSON。</p>
     <div v-if="parameterFields.length" class="managed-form-grid">
      <el-form-item v-for="field in parameterFields" :key="field.name" :label="field.name+(field.required?' *':'')">
       <div class="managed-parameter"><el-select v-model="field.kind" :aria-label="'参数类型 '+field.name" @change="parameterKindChanged(field)"><el-option value="string" label="文字"/><el-option value="number" label="数字"/><el-option value="boolean" label="开关"/><el-option value="json" label="JSON"/></el-select>
       <el-switch v-if="field.kind==='boolean'" v-model="field.value" :aria-label="'策略参数 '+field.name"/>
       <el-input-number v-else-if="field.kind==='number'" v-model="field.value" :aria-label="'策略参数 '+field.name" :controls="false"/>
       <el-input v-else v-model="field.value" :type="field.kind==='json'?'textarea':'text'" :aria-label="'策略参数 '+field.name" :placeholder="field.kind==='json'?'JSON 值，例如 [5,20]':field.required?'必填':'沿用默认值'"/>
       </div>
      </el-form-item>
     </div>
     <p v-else class="managed-note">此策略没有额外构造参数。</p>
     <details class="managed-advanced"><summary>高级参数 JSON</summary><p>填写其他命名参数。与上方表单重名的参数以上方为准。</p><el-input v-model="extraParams" type="textarea" :rows="4" aria-label="策略额外参数JSON" spellcheck="false"/></details>
     </template>
     <template v-if="draft.engine_type!=='HFT'">
      <h4>执行器</h4><el-form-item label="执行方式"><el-select v-model="draft.execution.id" aria-label="策略执行器" @change="resetExecution"><el-option v-for="item in catalog.executors||[]" :key="item.id" :value="item.id" :label="item.label"/></el-select></el-form-item>
      <template v-if="isAdmin&&draft.execution.params"><div class="managed-form-grid"><el-form-item v-for="field in executionFields" :key="field.key" :label="field.label"><el-input-number v-model="draft.execution.params[field.key]" :min="field.min??0" :max="field.max??1000000" :precision="0" :aria-label="'策略执行 '+field.label"/></el-form-item></div>
       <p v-if="draft.execution.id==='minimpact'" class="managed-note">MinImpact 使用固定手数拆单，与当前 Plus 回测参数一致。</p>
       <el-form-item v-if="draft.execution.id==='vwap'" label="各时段成交量权重"><el-input v-model="weights" type="textarea" aria-label="策略VWAP成交量权重" placeholder="例如 1,2,4,2,1"/><small>使用事先估计的成交量曲线。不要输入尚未发生的真实成交量。</small></el-form-item>
      </template><p v-else class="managed-note">执行参数仅管理员可见。</p>
     </template>
     <el-alert v-else title="HFT 由策略直接委托到所选通道，不使用 CTA 执行器。" type="info" :closable="false"/>
     <details v-if="draft.source&&isAdmin" class="managed-advanced"><summary>查看源码</summary><pre class="managed-source">{{draft.source}}</pre></details><p v-else-if="!isAdmin" class="managed-note">策略源码仅管理员可见。</p>
    </el-form>
   </div>
   <template #footer><el-button @click="showEditor=false">{{viewOnly?'关闭':'取消'}}</el-button><el-button v-if="!viewOnly" type="primary" :disabled="analyzing" @click="commitStrategy">加入页面草稿</el-button></template>
  </el-dialog>
 </section>
</template>

<script>
import {useSessionStore} from '@/store.js'
import RiskSelection from '@/components/common/RiskSelection.vue'
const copy=value=>JSON.parse(JSON.stringify(value))
const defaults=id=>id==='minimpact'?{pricemode:1,offset:0,expire:5,span:0,byrate:false,lots:1}:{begin_time:900,end_time:1500,ord_sticky:5,tail_secs:5,total_times:10,price_mode:2,price_offset:0,lots:1,minopenlots:1}
const blankStrategy=engine=>({id:'',name:'',class_name:'',engine_type:engine,source:'',library_id:'',library_version:null,params:{},trader:'',execution:{id:'minimpact',params:defaults('minimpact')},enabled:true,slippage:0,agent:true})
export default {
 name:'ManagedStrategies',
 components:{RiskSelection},
 props:{groupid:{type:String,default:''},create:{type:Boolean,default:false},running:{type:Boolean,default:undefined}},
 emits:['created','changed','cancel'],
 data:()=>({catalog:{},state:{},library:[],selectedLibrary:'',plan:{schema:1,revision:0,engine_type:'CTA',strategies:[],trading_enabled:false},newGroup:{groupid:'',name:'',template_groupid:'',engine_type:'CTA'},loading:false,saving:false,applying:false,dirty:false,showEditor:false,viewOnly:false,editingId:'',draft:blankStrategy('CTA'),classes:[],parameterFields:[],extraParams:'{}',weights:'',filename:'',analyzing:false,serial:0,showContracts:false,contractRow:null,contractCatalog:[],contractSelection:[],contractPreview:null,contractError:'',contractLoading:false,contractPreviewing:false}),
 computed:{
  isGuest(){return useSessionStore().cache.userinfo?.role==='guest'},
  isAdmin(){return['admin','superman'].includes(useSessionStore().cache.userinfo?.role)},
  processRunning(){return Boolean(this.running||this.state.running)},
  hasApplied(){return this.state.has_applied??Boolean(this.state.applied_revision)},
  supportedEngine(){return['CTA','HFT'].includes(this.plan.engine_type)},
  editable(){return this.isAdmin&&this.state.can_manage!==false&&!this.processRunning&&this.supportedEngine},
  contractMembers(){const group=this.contractRow?.contract_group||this.contractRow?.id;return this.plan.strategies.filter(row=>(row.contract_group||row.id)===group)},
  contractOptions(){const reference=this.contractCatalog.find(row=>row.code===this.contractRow?.params?.code);const rows=this.contractCatalog.filter(row=>row.exchange===reference?.exchange&&row.product===reference?.product);for(const member of this.contractMembers)if(!rows.some(row=>row.code===member.params.code))rows.push({code:member.params.code,name:member.params.code});return rows.map(row=>({value:row.code,label:row.name+' · '+row.code}))},
  executionFields(){return this.draft.execution.id==='minimpact'?[{key:'pricemode',label:'价格模式（-1本方／0最新／1对手／2自动）',min:-1,max:2},{key:'offset',label:'委托偏移跳数',min:-1000,max:1000},{key:'expire',label:'挂单超时秒',max:86400},{key:'span',label:'最短发单间隔毫秒',max:86400000},{key:'lots',label:'单次委托手数',min:1}]:[{key:'begin_time',label:'窗口开始（HHMM）',max:2359},{key:'end_time',label:'窗口结束（HHMM）',max:2359},{key:'ord_sticky',label:'挂单超时秒',max:86400},{key:'tail_secs',label:'窗口收尾秒数',max:86400},{key:'total_times',label:'拆单次数',min:1,max:10000},{key:'price_mode',label:'价格模式（0最新／1本方／2对手）',max:2},{key:'price_offset',label:'委托偏移跳数',max:1000},{key:'lots',label:'单次委托手数',min:1},{key:'minopenlots',label:'最小拆单手数',min:1}]}
 },
 methods:{
  async request(path,body={}){const response=await fetch('/mgr/'+path,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const value=await response.json();if(!response.ok||value.result<0)throw new Error(value.message||'运行计划请求失败');return value},
  permit(){if(this.isGuest){this.$message.warning('游客模式不支持操作');return false}if(!this.isAdmin){this.$message.warning('仅管理员可以修改运行计划');return false}if(!this.create&&!this.editable){this.$message.warning(this.processRunning?'请先停止组合，再修改运行计划':'此组合暂不可修改');return false}return true},
  channelName(id){return this.catalog.channels?.find(row=>row.id===id)?.label||id||'尚未选择'},
  executorName(id){return this.catalog.executors?.find(row=>row.id===id)?.label||id||'尚未选择'},
  positionLabel(row){return row?.has_positions?'有持仓 · '+row.position_volume+'手':row?.position_known?'账簿无持仓':'尚无持仓快照'},
  plannedContracts(row){return typeof row.params?.code==='string'?[row.params.code]:Array.isArray(row.params?.codes)?row.params.codes:row.contracts||[]},
  appliedContracts(row){return this.state.strategy_status?.[row.id]?.applied_contracts||[]},
  contractBudget(row,key){const value=row[key];if(value===undefined||value===null)return'由策略配置';if(!value)return'未启用此限制';return row.capital_limit?Number((row.capital_limit*value).toFixed(2)).toLocaleString()+'元':Number((value*100).toFixed(4))+'%权益'},
  async openContracts(row){this.contractRow=row;this.contractSelection=this.plan.strategies.filter(item=>(item.contract_group||item.id)===(row.contract_group||row.id)).map(item=>item.params.code);this.contractPreview=null;this.contractError='';this.showContracts=true;this.contractLoading=true;try{this.contractCatalog=(await this.request('market/subscriptions',{groupid:this.groupid})).catalog||[]}catch(error){this.contractError=error.message}finally{this.contractLoading=false}},
  async previewContracts(){if(!this.permit())return;this.contractPreviewing=true;this.contractPreview=null;this.contractError='';try{this.contractPreview=await this.request('preview_strategy_contracts',{groupid:this.groupid,strategyid:this.contractRow.id,contracts:this.contractSelection,plan:{...this.plan,strategies:this.plan.strategies.map(({source,...row})=>row)}})}catch(error){this.contractError=error.message}finally{this.contractPreviewing=false}},
  commitContracts(){if(!this.permit()||!this.contractPreview)return;this.plan=this.contractPreview.plan;this.dirty=true;this.showContracts=false;this.$message.success('合约与均分预算已加入页面草稿，尚未保存或应用')},
  async load(){const serial=++this.serial;this.loading=true;try{const catalog=await this.request('strategy_catalog',this.groupid?{groupid:this.groupid}:{});if(serial!==this.serial)return;this.catalog=catalog;if(this.create){this.newGroup.template_groupid=catalog.templates?.[0]?.id||'';return}const value=await this.request('strategy_plan',{groupid:this.groupid});if(serial!==this.serial)return;this.state=value;this.library=(await this.request('strategy_library',{engine_type:value.plan?.engine_type})).strategies||[];this.plan={...value.plan,engine_type:String(value.plan?.engine_type||'CTA').toUpperCase(),strategies:value.plan?.strategies||[]};this.dirty=false}catch(error){if(serial===this.serial)this.$message.error(error.message)}finally{if(serial===this.serial)this.loading=false}},
  async createGroup(){if(!this.permit()||this.saving)return;try{if(!/^[A-Za-z][A-Za-z0-9_-]{0,39}$/.test(this.newGroup.groupid))throw new Error('组合 ID 须以字母开头，只使用字母、数字、下划线或横线，最多40字符');if(!this.newGroup.name.trim()||!this.newGroup.template_groupid)throw new Error('请填写名称并选择环境模板');this.saving=true;const value=await this.request('create_strategy_group',{...this.newGroup,name:this.newGroup.name.trim()});this.$message.success('组合草稿已创建，进程未启动');this.$emit('created',{...value,groupid:this.newGroup.groupid})}catch(error){this.$message.error(error.message)}finally{this.saving=false}},
  async addStrategy(){if(!this.permit())return;this.editingId='';this.viewOnly=false;this.selectedLibrary='';this.draft=blankStrategy(this.plan.engine_type);this.draft.trader=this.catalog.channels?.[0]?.id||'';this.classes=[];this.parameterFields=[];this.extraParams='{}';this.weights='';this.filename='';try{this.library=(await this.request('strategy_library',{engine_type:this.plan.engine_type})).strategies||[];this.showEditor=true}catch(error){this.$message.error(error.message)}},
  async selectLibrary(id){this.analyzing=true;try{const item=(await this.request('strategy_library_get',{library_id:id})).strategy;if(item.engine_type!==this.plan.engine_type)throw new Error('策略类型与组合引擎不一致');this.draft.library_id=item.id;this.draft.library_version=item.version;this.draft.source=item.source;this.draft.class_name=item.class_name;this.classes=[{...item,name:item.class_name}];this.selectClass();if(!this.draft.id)this.draft.id=item.id;if(!this.draft.name)this.draft.name=item.name}catch(error){this.$message.error(error.message)}finally{this.analyzing=false}},
  selectClass(){const info=this.classes.find(row=>row.name===this.draft.class_name);this.draft.engine_type=info?.engine_type||this.plan.engine_type;this.setParameters(info?.parameters||[],{});this.extraParams='{}'},
  parameterKindChanged(field){const value=field.value;if(field.kind==='number')field.value=value!==''&&Number.isFinite(Number(value))?Number(value):undefined;else if(field.kind==='boolean')field.value=value===true||value==='true'||value===1;else if(field.kind==='json')field.value=typeof value==='string'?value:JSON.stringify(value??null);else field.value=String(value??'')},
  setParameters(specs,values){this.parameterFields=specs.filter(row=>row.name!=='name').map(row=>{const value=Object.hasOwn(values,row.name)?values[row.name]:row.default;const type=String(row.type||'').toLowerCase();const kind=typeof value==='boolean'||type==='bool'||type==='boolean'?'boolean':typeof value==='number'||['int','float','number','integer'].includes(type)?'number':typeof value==='object'||['dict','list','tuple','json'].includes(type)?'json':'string';return{name:row.name,required:row.required,kind,value:kind==='json'?(value===undefined?'':JSON.stringify(value)):value??(kind==='boolean'?false:kind==='number'?undefined:'')}})},
  async openStrategy(row,readOnly){this.viewOnly=readOnly;this.editingId=row.id;this.selectedLibrary=row.library_id||'';this.draft=copy(row);this.draft.engine_type=String(row.engine_type||this.plan.engine_type).toUpperCase();this.draft.execution??={id:'minimpact',params:defaults('minimpact')};this.classes=[];this.parameterFields=[];this.filename='';this.weights=(row.execution?.volume_weights||row.execution?.params?.volume_weights||[]).join(',');this.extraParams=JSON.stringify(row.params||{},null,2);this.showEditor=true;if(this.isGuest||!row.source)return;try{const value=await this.request('analyze_strategy',{groupid:this.groupid,source:row.source});if(!this.showEditor||this.editingId!==row.id)return;this.classes=value.classes||[];const specs=this.classes.find(item=>item.name===row.class_name)?.parameters||[];this.setParameters(specs,row.params||{});const names=new Set(specs.map(item=>item.name));this.extraParams=JSON.stringify(Object.fromEntries(Object.entries(row.params||{}).filter(([key])=>!names.has(key))),null,2)}catch(error){this.$message.error(error.message)}},
  view(row){this.openStrategy(row,true)},
  edit(row){if(this.permit())this.openStrategy(row,false)},
  async remove(row){if(!this.permit())return;try{await this.$confirm('从下次启动计划移除“'+(row.name||row.id)+'”？策略库条目、历史账簿和成交记录保留。','移除策略',{type:'warning',confirmButtonText:'从草稿移除',cancelButtonText:'取消'});this.plan.strategies=this.plan.strategies.filter(item=>item.id!==row.id);this.dirty=true}catch(error){if(error instanceof Error)this.$message.error(error.message)}},
  resetExecution(){this.draft.execution={id:this.draft.execution.id,params:defaults(this.draft.execution.id)};this.weights=''},
  commitStrategy(){if(!this.permit())return;try{const row=copy(this.draft);if(!/^[A-Za-z][A-Za-z0-9_-]{0,39}$/.test(row.id))throw new Error('实例 ID 须以字母开头，只使用字母、数字、下划线或横线');if(this.plan.strategies.some(item=>item.id===row.id&&item.id!==this.editingId))throw new Error('实例 ID 已存在，请使用唯一 ID');if(!row.name.trim()||!row.class_name||!row.source||!row.trader)throw new Error('请填写名称、选择策略库版本和交易通道');row.params=JSON.parse(this.extraParams||'{}');if(!row.params||typeof row.params!=='object'||Array.isArray(row.params))throw new Error('高级参数必须是 JSON 对象');for(const field of this.parameterFields){if(field.value===undefined||field.value===''){if(field.required)throw new Error('请填写策略参数 '+field.name);continue}row.params[field.name]=field.kind==='json'?JSON.parse(field.value):field.value}delete row.params.name;if(row.engine_type==='HFT')row.execution=null;else if(row.execution.id==='vwap'){const weights=this.weights.split(/[,，\s]+/).filter(Boolean).map(Number);if(weights.length<2||weights.some(item=>!Number.isFinite(item)||item<0)||!weights.some(item=>item>0))throw new Error('VWAP 请填写至少两个非负权重，且总和大于 0');row.execution.volume_weights=weights}const index=this.plan.strategies.findIndex(item=>item.id===this.editingId);if(index<0)this.plan.strategies.push(row);else this.plan.strategies.splice(index,1,row);this.dirty=true;this.showEditor=false;this.$message.success('已加入页面草稿，尚未保存或应用')}catch(error){this.$message.error(error instanceof SyntaxError?'参数 JSON 格式有误，请检查':error.message)}},
  async setTrading(enabled){if(!this.permit())return;if(enabled)try{await this.$confirm('开启后，之后手动启动组合时，已加载策略可以向所选通道提交真实委托。请先核对账户环境、策略及风控。此操作不会启动进程。','允许此组合交易',{type:'warning',confirmButtonText:'允许之后手动启动交易',cancelButtonText:'保持只读'})}catch{return}this.plan.trading_enabled=Boolean(enabled);this.dirty=true},
  async save(apply){if(!this.permit()||this.saving||this.applying)return;try{if(apply){await this.$confirm((!this.state.managed?'应用后调度入口切换为页面管理程序。原 run.py 会保留，但其中原有策略不会自动加载。请确认已完整迁移。':'应用策略草稿到下次启动配置。')+'进程守护与定时启动将关闭，当前不会启动交易。'+(!this.plan.strategies.some(row=>row.enabled)?'计划没有启用策略，应用后无法启动交易进程。':'')+(this.plan.trading_enabled?'交易授权已开启，之后手动启动可能下单。':'交易授权保持关闭。'),'应用到启动配置',{type:'warning',confirmButtonText:'应用，不启动',cancelButtonText:'取消'})}this.saving=!apply;this.applying=apply;const value=await this.request('save_strategy_plan',{groupid:this.groupid,plan:{...this.plan,strategies:this.plan.strategies.map(({source,...row})=>row)},apply,confirm_takeover:apply&&!this.state.managed});this.$message.success(value.message||(apply?'启动配置已应用，进程未启动':'草稿已保存'));await this.load();this.$emit('changed',value)}catch(error){if(error instanceof Error)this.$message.error(error.message)}finally{this.saving=false;this.applying=false}}
 },
 watch:{groupid(){this.load()}},
 mounted(){this.load()},beforeUnmount(){++this.serial}
}
</script>

<style scoped>
.managed-contract-select{width:100%;margin:14px 0}.managed-strategies{color:var(--wt-text);min-width:0}.managed-intro,.managed-note,.managed-heading p,.managed-trading p{color:var(--wt-muted);font-size:12px;line-height:1.8;margin:8px 0}.managed-heading,.managed-toolbar,.managed-footer,.managed-status,.managed-file,.managed-trading{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.managed-heading{justify-content:space-between}.managed-heading h3{font-size:17px;margin:0 0 6px;font-weight:600}.managed-status{margin:16px 0;font-size:12px;color:var(--wt-muted)}.managed-toolbar{justify-content:space-between;margin:20px 0 12px;font-size:13px}.managed-strategies small{display:block;font-size:11px;color:var(--wt-muted);line-height:1.7;margin-top:4px}.managed-form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 20px}.managed-form-grid .el-select,.managed-form-grid .el-input-number{width:100%}.managed-footer{justify-content:flex-end;margin-top:18px}.managed-footer>span{margin-right:auto;font-size:11px;color:var(--wt-muted)}.managed-file{font-size:12px;color:var(--wt-muted);margin:18px 0}.managed-applied{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:14px 16px;border:1px solid var(--wt-line);border-radius:8px;background:var(--wt-matte);margin:14px 0;font-size:12px}.managed-applied>span{color:var(--wt-muted)}.managed-applied p{width:100%;margin:0;color:var(--wt-danger);line-height:1.7}.managed-trading{justify-content:space-between;margin-top:18px;padding:16px;border:1px solid var(--wt-line);border-radius:8px;background:var(--wt-matte)}.managed-trading strong{font-size:13px}.managed-trading p{margin-bottom:0}.managed-editor{max-height:69vh;overflow:auto;padding:0 8px}.managed-editor h4{font-size:14px;margin:24px 0 10px}.managed-editor .el-select{width:100%}.managed-parameter{display:flex;align-items:center;gap:8px;width:100%}.managed-parameter .el-select{width:90px;flex:0 0 90px}.managed-parameter>.el-input,.managed-parameter>.el-input-number{flex:1;min-width:0}.managed-advanced{font-size:12px;color:var(--wt-muted);margin:16px 0;line-height:1.8}.managed-advanced summary{cursor:pointer}.managed-source{font:12px/1.8 var(--wt-mono);max-height:340px;overflow:auto;white-space:pre-wrap;word-break:break-word;padding:14px;border:1px solid var(--wt-line);border-radius:7px;background:var(--wt-matte);color:var(--wt-text)}
@media(max-width:640px){.managed-form-grid{grid-template-columns:1fr}.managed-heading{align-items:flex-start}.managed-editor{padding:0}.managed-footer{gap:8px}}
</style>
