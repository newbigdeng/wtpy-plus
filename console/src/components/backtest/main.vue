<template>
  <div class="wt-research">
    <aside class="wt-research-sidebar" :style="{flex:'0 0 '+sidebarWidth+'px'}" aria-label="回测策略列表">
      <div class="wt-research-heading"><span>策略研究</span><button class="wt-icon-button" aria-label="刷新策略列表" @click="refreshStras"><WtIcon name="refresh"/></button><button class="wt-icon-button" aria-label="新建策略" @click="onAddStrategy"><WtIcon name="plus"/></button></div>
      <el-input v-model="search" placeholder="搜索策略" clearable aria-label="搜索回测策略"><template #prefix><WtIcon name="search" style="width:14px"/></template></el-input>
      <div class="wt-strategy-count">{{filteredStrategies.length}} 个策略 · CTA / HFT / SEL</div>
      <div v-loading="strasOnWay">
        <div class="stra-item" v-for="item in filteredStrategies" :key="item.id" :class="{active:curStra?.id===item.id}" @click="selectStrategy(item)" role="button" tabindex="0" @keydown.enter="selectStrategy(item)">
          <div class="stra-line"><span class="wt-type-badge">{{(item.strategy_type||'cta').toUpperCase()}}</span><span class="stra-title" :title="item.name">{{item.name}}</span><button class="wt-icon-button" :aria-label="'查看策略 '+item.name" @click.stop="selectStrategy(item)"><i class="el-icon-view"/></button><button class="wt-icon-button" :aria-label="'删除策略 '+item.name" @click.stop="onDelStrategy(item.id)"><i class="el-icon-delete"/></button></div>
          <div class="stra-line wt-stra-metrics"><span>年化 <b :class="profitClass(item.perform?.annual_return)">{{num(item.perform?.annual_return)}}%</b></span><span>回撤 <b>{{num(item.perform?.max_falldown)}}%</b></span></div>
        </div>
        <div v-if="!filteredStrategies.length&&!strasOnWay" class="wt-research-empty">{{search?'没有匹配的策略':'创建一个策略，从框架开始研究。'}}</div>
      </div>
      <div class="wt-research-note">CTA 是持仓驱动，HFT 是委托驱动，SEL 是定时选股。类型决定 Mocker 与回调。</div>
    </aside>
    <ResizableDivider v-model="sidebarWidth" :min="200" :max="440" storage-key="wt.backtest.sidebar" label="调整策略列表宽度"/>
    <section class="wt-research-content">
      <header class="wt-research-top">
        <div><div class="wt-page-kicker">RESEARCH / BACKTEST</div><h1>{{curStra?.name||'在线回测'}}<span v-if="curStra" class="wt-type-badge">{{strategyType.toUpperCase()}}</span></h1></div>
        <el-button v-if="curStra" link @click="renameStrategy" aria-label="重命名研究策略">重命名</el-button><router-link class="wt-manual-link" :to="{path:'/api-manual',query:{type:strategyType}}" target="_blank"><WtIcon name="book"/>API 手册 ↗</router-link>
      </header>
      <template v-if="curStra">
        <div class="wt-backtest-mode"><label for="wt-backtest-mode">回测系统</label><el-select id="wt-backtest-mode" v-model="backtestMode" aria-label="选择回测系统"><el-option value="fast" label="原版快速回测 · 带成交模型"/><el-option value="plus" label="Plus 回测 · 实盘执行链路"/></el-select><span>{{backtestMode==='plus'?'复用执行器与交易适配器；使用历史行情和模拟柜台。':'使用策略 Mocker 与原版成交模型，快速验证信号。'}}</span></div><div class="wt-research-tabs"><button :class="{active:selData==='editor'}" @click="selData='editor'">策略代码<span v-if="dirty" class="wt-dirty-dot"/></button><button :class="{active:selData==='backtest'}" :disabled="!curBT" @click="selData='backtest'">回测详情</button><button :class="{active:selData==='plus'}" @click="backtestMode='plus';selData='plus'">Plus 仿真</button><span class="wt-save-state">{{saving?'保存中…':dirty?'有未保存的修改':'代码已保存'}}</span></div>
        <PlusResearch v-if="selData==='plus'" :strategyid="curStra.id" :engine-type="strategyType" :before-save="onSaveCode"/>
        <div v-show="selData==='editor'" class="wt-editor-workspace">
          <section class="wt-editor-pane" :style="{flex:'0 0 '+editorHeight+'px'}">
            <div class="wt-editor-toolbar"><span class="wt-code-file"><WtIcon name="document"/>MyStrategy.py</span><span class="wt-editor-hint">{{typeInfo?.base_class||'Base'+strategyType.toUpperCase()+'Strategy'}}</span><el-button @click="onClickCancel" :disabled="!dirty">撤销修改</el-button><el-button :loading="saving" :disabled="!dirty" @click="onSaveCode">保存代码</el-button><el-button class="wt-run-button" type="success" :disabled="saving" @click="configureBacktest"><WtIcon name="play"/>{{backtestMode==='plus'?'配置 Plus 回测':'一键回测'}}</el-button></div>
            <div class="wt-editor-frame"><codemirror ref="mycode" v-model="content" :options="cmOptions"/></div>
          </section>
          <ResizableDivider v-model="editorHeight" axis="y" :min="180" :max="780" storage-key="wt.backtest.editorHeight" label="调整代码区高度"/>
          <section class="wt-history-pane"><div class="wt-history-heading"><span>回测记录</span><span>{{backtests.length}} 次</span><button class="wt-icon-button" aria-label="刷新回测记录" @click="refreshBacktests"><WtIcon name="refresh"/></button></div>
            <el-table :data="backtests" stripe height="100%" class="wt-backtests" empty-text="尚无回测记录，保存策略后点击一键回测。">
              <el-table-column prop="runtime" label="回测时间" min-width="148"/>
              <el-table-column label="回放区间" min-width="190"><template #default="{row}">{{fmtTime(row.state?.stime)}} — {{fmtTime(row.state?.etime)}}</template></el-table-column>
              <el-table-column label="收益率" width="90"><template #default="{row}"><span :class="profitClass(row.perform?.total_return)">{{num(row.perform?.total_return)}}%</span></template></el-table-column>
              <el-table-column label="最大回撤" width="90"><template #default="{row}">{{num(row.perform?.max_falldown)}}%</template></el-table-column>
              <el-table-column label="夏普率" width="80"><template #default="{row}">{{num(row.perform?.sharpe_ratio)}}</template></el-table-column>
              <el-table-column label="状态 / 进度" min-width="135"><template #default="{row}"><span class="wt-bt-status" :class="row.status">{{statusText(row)}}</span><span v-if="row.status==='running' && row.state?.progress_kind==='completion'" class="wt-sel-progress">（完成时更新）</span><el-progress v-if="row.status==='running' && row.state?.progress_kind!=='completion'" :percentage="Math.min(100,Math.max(0,Number(row.state?.progress)||0))" :stroke-width="3" :show-text="false" color="var(--wt-success)"/></template></el-table-column>
              <el-table-column label="耗时" width="70"><template #default="{row}">{{num((row.state?.elapse||0)/1e9)}}s</template></el-table-column>
              <el-table-column label="操作" width="145" fixed="right"><template #default="{row}"><el-button v-if="row.status==='running'" link type="danger" @click="onStopBacktest(row)">停止</el-button><el-button link @click="onViewBacktest(row)" :aria-label="'查看回测详情 '+row.id"><i class="el-icon-view"/>详情</el-button><button class="wt-icon-button" aria-label="删除回测记录" :disabled="row.status==='running'" @click="onDelBacktest(row)"><i class="el-icon-delete"/></button></template></el-table-column>
            </el-table>
          </section>
        </div>
        <div v-if="selData==='backtest'&&curBT" class="wt-bt-detail">
          <div class="wt-detail-banner"><span class="wt-bt-status" :class="curBT.status">{{statusText(curBT)}}</span><span v-if="curBT.fill_config">{{curBT.fill_config.fill_model}} · 延迟 {{curBT.fill_config.event_delay}} · 参与率 {{curBT.fill_config.participation_rate}}</span><span>{{fmtTime(curBT.state?.stime)}} → {{fmtTime(curBT.state?.etime)}}</span><span class="wt-detail-id" :title="curBT.id">{{curBT.id}}</span><el-button link @click="loadDetail">刷新详情</el-button><el-button link @click="showRunLog=!showRunLog">{{showRunLog?'收起':'查看'}}运行日志</el-button></div>
          <el-alert v-if="detail?.error||detail?.backtest?.error||['failed','interrupted'].includes(curBT.status)" :title="detail?.error||detail?.backtest?.error||'回测未正常完成，请查看运行日志。'" type="error" :closable="false" show-icon/>
          <pre v-if="showRunLog" class="wt-run-log">{{detailLog||'暂无运行日志。'}}</pre>
          <div class="wt-bt-charts"><BTComp :key="curBT.id" :btInfo="curBT" :straInfo="curStra"/></div>
        </div>
      </template>
      <div v-else class="wt-research-welcome"><WtIcon name="backtest"/><h2>把想法写成策略</h2><p>选择左侧策略继续研究，或创建 CTA、HFT、SEL 策略框架。</p><el-button type="primary" @click="onAddStrategy">新建策略</el-button></div>
    </section>
    <el-dialog title="新建策略" v-model="showCreate" width="480px">
      <el-form label-position="top" @submit.prevent="createStrategy"><el-form-item label="策略名称"><el-input v-model="createForm.name" maxlength="80" placeholder="例如：均线趋势 CTA" @keyup.enter="createStrategy"/></el-form-item><el-form-item label="策略类型"><el-select v-model="createForm.strategy_type" aria-label="策略类型" style="width:100%"><el-option v-for="item in strategyTypes" :key="item.id" :value="item.id" :label="item.label+' · '+item.base_class"/></el-select></el-form-item><p class="wt-create-description">{{createType?.description}}</p><div class="wt-callback-list"><code v-for="callback in createType?.callbacks||[]" :key="callback">{{callback}}</code></div><p class="wt-research-note">新建后自动生成可编辑的类与回调空实现。UFT 暂无对应 Python Mocker，不提供此类型。</p></el-form>
      <template #footer><el-button @click="showCreate=false">取消</el-button><el-button type="primary" :loading="creating" :disabled="!createForm.name.trim()" @click="createStrategy">创建并编写</el-button></template>
    </el-dialog>
    <el-dialog title="原版快速回测 · 成交模型配置" v-model="showBTCfg" width="420px">
      <el-alert title="先保存代码，再运行所选 Mocker。内置固定样例：SHFE.rb.2701 / m1 / 2026-10-12；研究其他合约或日期需先准备相应数据。" type="info" :closable="false"/>
      <el-form label-width="86px" class="wt-bt-form"><el-form-item label="策略类型"><span>{{strategyType.toUpperCase()}} / {{typeInfo?.engine||strategyType.toUpperCase()+' Mocker'}}</span></el-form-item><template v-if="strategyType==='cta'"><el-form-item label="成交模型"><el-select v-model="btConfig.fill_model" aria-label="快速CTA成交模型" @change="resetFillOptions"><el-option value="legacy_cta" label="legacy_cta · 原版规则"/><el-option value="causal_touch" label="causal_touch · 顺序报价"/><el-option value="volume_limited" label="volume_limited · 部分成交"/></el-select></el-form-item><el-form-item v-if="btConfig.fill_model!=='legacy_cta'" label="额外等待事件"><el-input-number v-model="btConfig.event_delay" :min="0" :max="1000000" :precision="0" aria-label="快速回测事件延迟"/></el-form-item><el-form-item v-if="btConfig.fill_model==='volume_limited'" label="成交量参与率"><el-input-number v-model="btConfig.participation_rate" :min="0.0001" :max="1" :step="0.1" :precision="4" aria-label="快速回测成交量参与率"/></el-form-item></template><el-form-item label="开始日期"><el-date-picker v-model="btConfig.startTime" type="date" format="YYYY-MM-DD" placeholder="开始日期" style="width:100%"/></el-form-item><el-form-item label="结束日期"><el-date-picker v-model="btConfig.endTime" type="date" format="YYYY-MM-DD" placeholder="结束日期" style="width:100%"/></el-form-item><el-form-item label="初始资金"><el-input-number v-model="btConfig.capital" :min="1" :max="1000000000" :step="10000" style="width:100%"/></el-form-item><el-form-item label="价格滑点"><el-input-number v-model="btConfig.slippage" :min="0" :max="100" style="width:100%"/></el-form-item></el-form>
      <template #footer><el-button @click="showBTCfg=false">取消</el-button><el-button type="success" :loading="launching" @click="onRunBacktest">保存并启动</el-button></template>
    </el-dialog>
  </div>
</template>
<script>
import codemirror from '@/components/common/CodeEditor.vue'
import WtIcon from '@/components/common/WtIcon.vue'
import ResizableDivider from '@/components/common/ResizableDivider.vue'
import BTComp from './btcomp.vue'
import PlusResearch from './PlusResearch.vue'
export default {
  name:'Backtest',components:{codemirror,WtIcon,ResizableDivider,BTComp,PlusResearch},
  data(){return{sidebarWidth:264,editorHeight:Math.max(200,Math.min(460,window.innerHeight*.43)),search:'',strategies:[],strategyTypes:[],strasOnWay:false,showCreate:false,creating:false,createForm:{name:'',strategy_type:'cta'},showBTCfg:false,launching:false,saving:false,curStra:null,curBT:null,backtests:[],content:'',content_bak:'',selData:'editor',backtestMode:'fast',showRunLog:false,detail:null,btConfig:{startTime:new Date(2026,9,12),endTime:new Date(2026,9,12),capital:500000,slippage:0,fill_model:'causal_touch',event_delay:0,participation_rate:0},cmOptions:{mode:'python',lineWrapping:false,indentUnit:4,extraKeys:{'Ctrl-S':()=>this.onSaveCode()}}}},
  computed:{
    dirty(){return this.content!==this.content_bak},
    filteredStrategies(){const term=this.search.trim().toLowerCase();return this.strategies.filter(s=>(s.name+' '+(s.strategy_type||'cta')).toLowerCase().includes(term))},
    strategyType(){return this.curStra?.strategy_type||'cta'},
    typeInfo(){return this.strategyTypes.find(item=>item.id===this.strategyType)},
    createType(){return this.strategyTypes.find(item=>item.id===this.createForm.strategy_type)},
    detailLog(){const value=this.detail?.logs??this.detail?.log_tail??this.detail?.log??'';return Array.isArray(value)?value.join('\n'):typeof value==='string'?value:JSON.stringify(value,null,2)}
  },
  methods:{
    resetFillOptions(){if(this.btConfig.fill_model==='legacy_cta')this.btConfig.event_delay=0;this.btConfig.participation_rate=this.btConfig.fill_model==='volume_limited'?0.1:0},
    configureBacktest(){if(this.backtestMode==='plus')this.selData='plus';else this.showBTCfg=true},
    num(value){return(Number(value)||0).toFixed(2)},profitClass(value){return Number(value)>0?'wt-up':Number(value)<0?'wt-down':''},
    fmtTime(value){if(!value)return '—';const text=String(value);return text.slice(0,4)+'.'+text.slice(4,6)+'.'+text.slice(6,8)+(text.length>8?' '+text.slice(8,10)+':'+text.slice(10,12):'')},
    statusText(info){return{running:'运行中',completed:'已完成',failed:'失败',stopped:'已停止',interrupted:'已中断'}[info?.status]||'历史记录'},
    async selectStrategy(item){if(this.curStra?.id===item.id){this.selData='editor';return}if(this.dirty){try{await this.$confirm('当前代码尚未保存，切换后将放弃这些修改。','切换策略',{confirmButtonText:'放弃并切换',cancelButtonText:'继续编写',type:'warning'})}catch{return}}this.onOpenStrategy(item)},
    onOpenStrategy(item){this.curStra=item;this.curBT=null;this.backtests=[];this.detail=null;this.selData='editor';this.content='';this.content_bak='';localStorage.setItem('wt.backtest.selected',item.id);const id=item.id;this.$api.getBtStraCode(id,result=>{if(this.curStra?.id!==id)return;if(result.result<0)this.$message.error('拉取策略代码失败：'+result.message);else{this.content=result.content||'';this.content_bak=this.content}});this.refreshBacktests()},
    refreshStras(){this.strasOnWay=true;this.$api.getBtStrategies(result=>{this.strasOnWay=false;if(result.result<0){this.$message.error(result.message);return}this.strategies=result.strategies||[];if(this.curStra&&!this.strategies.some(s=>s.id===this.curStra.id)){this.curStra=null;this.content='';this.content_bak='';this.backtests=[];this.curBT=null}if(!this.curStra){const saved=localStorage.getItem('wt.backtest.selected');const selected=this.strategies.find(s=>s.id===saved);if(selected)this.onOpenStrategy(selected)}})},
    refreshBacktests(){if(!this.curStra)return;const id=this.curStra.id;this.$api.getBacktests(id,result=>{if(this.curStra?.id!==id)return;if(result.result<0){this.$message.error(result.message);return}this.backtests=result.backtests||[];if(this.curBT){const found=this.backtests.find(bt=>bt.id===this.curBT.id);if(found)this.curBT=found}})},
    async renameStrategy(){
      const id=this.curStra?.id;if(!id)return
      try{
        const {value}=await this.$prompt('修改名称，策略ID与已有回测记录保持不变。','重命名策略',{inputValue:this.curStra.name,confirmButtonText:'保存',cancelButtonText:'取消',inputValidator:value=>Boolean(value?.trim())&&value.trim().length<=80||'请输入1至80个字符'})
        const response=await fetch('/bt/renamestra',{method:'POST',headers:{'Content-Type':'application/json'},credentials:'same-origin',body:JSON.stringify({straid:id,name:value.trim()})})
        const result=await response.json();if(!response.ok||result.result<0)throw new Error(result.message||'改名失败')
        const item=this.strategies.find(item=>item.id===id);if(item)item.name=result.name
        this.$message.success('策略名称已修改')
      }catch(error){if(error instanceof Error)this.$message.error(error.message)}
    },
    onAddStrategy(){this.createForm={name:'',strategy_type:this.strategyType};this.showCreate=true},
    createStrategy(){if(this.creating||!this.createForm.name.trim())return;this.creating=true;this.$api.addBtStrategy(this.createForm.name.trim(),this.createForm.strategy_type,result=>{this.creating=false;if(result.result<0){this.$message.error('新建失败：'+result.message);return}this.strategies.push(result.strategy);this.showCreate=false;this.selectStrategy(result.strategy)})},
    async onDelStrategy(id){try{await this.$confirm('删除策略会同时删除其回测记录，是否继续？','删除策略',{confirmButtonText:'删除',cancelButtonText:'取消',type:'warning'})}catch{return}this.$api.delBtStrategy(id,result=>{if(result.result<0)this.$message.error(result.message);else{this.$message.success('策略已删除');this.refreshStras()}})},
    onSaveCode(){if(!this.curStra||!this.dirty)return Promise.resolve(true);if(this.saving)return Promise.resolve(false);this.saving=true;const id=this.curStra.id,content=this.content;return new Promise(resolve=>this.$api.commitBtStraCode(id,content,result=>{this.saving=false;if(result.result<0){this.$message.error('保存失败：'+result.message);resolve(false)}else{if(this.curStra?.id===id)this.content_bak=content;this.$message.success('策略代码已保存');resolve(true)}}))},
    async onClickCancel(){if(!this.dirty)return;try{await this.$confirm('放弃尚未保存的修改？','撤销修改',{confirmButtonText:'放弃修改',cancelButtonText:'继续编写'})}catch{return}this.content=this.content_bak},
    async onRunBacktest(){if(this.launching||!this.curStra)return;const cfg=this.btConfig;const fill=this.strategyType==='cta'?{fill_model:cfg.fill_model,event_delay:cfg.event_delay,participation_rate:cfg.participation_rate}:{};if(!(cfg.startTime instanceof Date)||!(cfg.endTime instanceof Date)||cfg.startTime>cfg.endTime){this.$message.error('请选择有效的起止日期');return}try{await this.$confirm('使用当前代码与配置启动历史回测？','启动回测',{confirmButtonText:'启动',cancelButtonText:'取消'})}catch{return}this.launching=true;if(!await this.onSaveCode()){this.launching=false;return}const id=this.curStra.id;this.$api.runBacktest(id,cfg.startTime.format('yyyyMMdd'),cfg.endTime.format('yyyyMMdd'),cfg.capital,cfg.slippage,result=>{this.launching=false;if(result.result<0){this.$message.error('启动失败：'+result.message);return}this.showBTCfg=false;this.$message.success('回测任务已启动');if(this.curStra?.id===id){this.backtests.push(result.backtest);this.selData='editor'}},fill)},
    onStopBacktest(info){this.$api.stopBacktest(this.curStra.id,info.id,result=>{if(result.result<0)this.$message.error(result.message);else this.refreshBacktests()})},
    async onDelBacktest(info){if(info.status==='running'){this.$message.warning('请先停止回测');return}try{await this.$confirm('删除该回测记录？','删除回测',{confirmButtonText:'删除',cancelButtonText:'取消'})}catch{return}this.$api.delBacktest(info.id,result=>{if(result.result<0)this.$message.error(result.message);else{this.backtests=this.backtests.filter(bt=>bt.id!==info.id);if(this.curBT?.id===info.id){this.curBT=null;this.selData='editor'}}})},
    onViewBacktest(info){this.curBT=info;this.selData='backtest';this.detail=null;this.showRunLog=['failed','interrupted'].includes(info.status);this.loadDetail()},
    loadDetail(){if(!this.curStra||!this.curBT)return;const id=this.curBT.id;this.$api.getBtDetail(this.curStra.id,id,result=>{if(this.curBT?.id!==id)return;if(result.result<0){this.$message.error(result.message);return}this.detail=result;const info=result.backtest||result.detail;if(info?.state)this.curBT={...this.curBT,...info};else if(result.state)this.curBT={...this.curBT,status:result.status||this.curBT.status,state:result.state}})}
  },
  mounted(){this.refreshStras();this.$api.getBtStrategyTypes(result=>{if(result.result===0)this.strategyTypes=result.types||[];else this.$message.error('策略类型读取失败：'+result.message)});this._btRefresh=setInterval(()=>{if(this.backtests.some(bt=>bt.status==='running')){this.refreshBacktests();if(this.selData==='backtest')this.loadDetail()}},1500)},
  beforeUnmount(){clearInterval(this._btRefresh)},
  beforeRouteLeave(){if(this.dirty)return this.$confirm('代码尚未保存，离开后将放弃修改。','离开策略编辑器',{confirmButtonText:'放弃并离开',cancelButtonText:'继续编写'}).then(()=>true).catch(()=>false)}
}
</script>
<style scoped>
.wt-research{height:100%;display:flex;min-width:0;overflow:hidden}.wt-strategy-count{font-size:10px;color:var(--wt-muted);margin:14px 0 12px}.wt-stra-metrics{justify-content:space-between;color:var(--wt-muted);font-variant-numeric:tabular-nums}.wt-stra-metrics b{font-weight:400;margin-left:3px}
.wt-type-badge{display:inline-flex;align-items:center;background:var(--wt-hover);color:var(--wt-muted);font:10px/18px var(--wt-mono);border-radius:4px;padding:0 5px;letter-spacing:.2px;white-space:nowrap}.wt-research-note{font-size:11px;color:var(--wt-muted);line-height:1.8;margin:20px 0 0}
.wt-research-content{display:flex;flex-direction:column}.wt-research-top{display:flex;align-items:center;justify-content:space-between;gap:10px;flex:0 0 60px}.wt-page-kicker{font-size:9px;color:var(--wt-muted);letter-spacing:1.6px;margin-bottom:7px}h1{font-size:18px;font-weight:550;margin:0;display:flex;align-items:center;gap:12px}.wt-manual-link{display:flex;gap:7px;align-items:center;color:var(--wt-muted);text-decoration:none;font-size:11px}.wt-manual-link .wt-icon{width:15px;height:15px}
.wt-backtest-mode{display:flex;align-items:center;gap:12px;margin:12px 0;flex-wrap:wrap;font-size:12px}.wt-backtest-mode .el-select{width:285px}.wt-backtest-mode>span{font-size:11px;color:var(--wt-muted)}
.wt-research-tabs{display:flex;align-items:center;border-bottom:1px solid var(--wt-line);flex:0 0 42px;gap:24px}.wt-research-tabs button{display:flex;align-items:center;gap:6px;height:100%;padding:0;background:none;border:0;border-bottom:2px solid transparent;color:var(--wt-muted);font-size:12px;cursor:pointer}.wt-research-tabs button.active{color:var(--wt-text);border-bottom-color:var(--wt-text)}.wt-research-tabs button:disabled{opacity:.4;cursor:default}.wt-save-state{font-size:10px;color:var(--wt-muted);margin-left:auto}.wt-dirty-dot{width:5px;height:5px;border-radius:50%;background:var(--wt-warning)}
.wt-editor-workspace{display:flex;flex-direction:column;min-height:0;flex:1}.wt-editor-pane{min-height:180px;max-height:calc(100% - 100px);display:flex;flex-direction:column;padding:10px 0}.wt-editor-toolbar{display:flex;gap:8px;align-items:center;flex:0 0 42px;min-width:0;overflow:auto;white-space:nowrap}.wt-code-file{font-size:11px;display:flex;align-items:center;gap:6px}.wt-code-file .wt-icon{width:14px;height:14px}.wt-editor-hint{font:10px var(--wt-mono);color:var(--wt-muted);margin-right:auto}.wt-run-button .wt-icon{width:14px;height:14px;margin-right:4px}.wt-editor-frame{flex:1;min-height:0;border:1px solid var(--wt-line);border-radius:7px;overflow:hidden}
.wt-history-pane{min-height:90px;display:flex;flex-direction:column;flex:1;overflow:hidden}.wt-history-heading{display:flex;align-items:center;gap:10px;font-size:12px;flex:0 0 40px}.wt-history-heading>span:nth-child(2){color:var(--wt-muted);font-size:10px}.wt-history-heading .wt-icon-button{margin-left:auto}.wt-backtests{flex:1;min-height:0}.wt-bt-status{font-size:11px}.wt-bt-status.running,.wt-bt-status.completed{color:var(--wt-success)}.wt-bt-status.failed,.wt-bt-status.interrupted{color:var(--wt-danger)}.wt-bt-status.stopped{color:var(--wt-muted)}
.wt-bt-detail{flex:1;min-height:0;display:flex;flex-direction:column;overflow:auto}.wt-detail-banner{display:flex;align-items:center;gap:16px;font-size:11px;min-height:42px;flex-wrap:wrap}.wt-detail-banner>span:not(:first-child){color:var(--wt-muted)}.wt-detail-id{max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.wt-run-log{flex:0 0 auto;max-height:180px;overflow:auto;padding:12px 14px;background:var(--wt-matte);border:1px solid var(--wt-line);border-radius:6px;font:11px/1.7 var(--wt-mono);white-space:pre-wrap;word-break:break-word;color:var(--wt-muted);margin:8px 0}.wt-bt-charts{flex:1;min-height:400px;overflow:hidden}.wt-bt-charts>div{height:100%}
.wt-research-welcome{display:flex;flex:1;align-items:center;justify-content:center;flex-direction:column;text-align:center;color:var(--wt-muted)}.wt-research-welcome>.wt-icon{width:36px;height:36px;opacity:.5}.wt-research-welcome h2{font-size:20px;font-weight:450;color:var(--wt-text);margin-bottom:8px}.wt-research-welcome p{font-size:12px;line-height:1.8;margin-bottom:24px}
.wt-create-description{font-size:12px;color:var(--wt-muted);line-height:1.8}.wt-callback-list{display:flex;flex-wrap:wrap;gap:7px}.wt-callback-list code{font:10px var(--wt-mono);padding:4px 6px;border-radius:4px;background:var(--wt-matte)}.wt-bt-form{margin-top:20px}
@media(max-width:1100px){.wt-editor-hint{display:none}.wt-code-file{margin-right:auto}.wt-detail-id{display:none}.wt-research-top{flex-basis:56px}.wt-manual-link{font-size:10px}}
</style>
