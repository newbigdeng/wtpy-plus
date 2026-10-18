<template>
<el-container class="snooper" v-loading="loading"><el-aside width="260px" class="sidebar">
<div class="toolbar"><strong>工作空间</strong><el-button @click="refresh">刷新</el-button><el-button @click="showAdd=true">添加</el-button></div>
<el-select v-model="workspace" placeholder="选择工作空间" @change="loadStrategies"><el-option v-for="item in workspaces" :key="item.id" :label="item.name" :value="item.id"/></el-select>
<el-button v-if="workspace" link type="danger" @click="removeWorkspace">删除工作空间</el-button>
<el-menu :default-active="strategy" @select="selectStrategy"><el-menu-item v-for="item in strategies" :key="typeof item==='string'?item:item.id" :index="typeof item==='string'?item:item.id">{{typeof item==='string'?item:item.name || item.id}}</el-menu-item></el-menu>
</el-aside><el-main class="results">
<div class="toolbar"><strong>{{strategy || '回测结果查看'}}</strong></div>
<el-empty v-if="!strategy" description="选择工作空间和回测策略"/>
<template v-else>
<el-tabs v-model="tab" @tab-change="loadTab">
<el-tab-pane label="绩效概览" name="summary"/><el-tab-pane label="信号分析" name="bars"/>
<el-tab-pane label="成交明细" name="trades"/><el-tab-pane label="信号明细" name="signals"/>
<el-tab-pane label="回合明细" name="rounds"/><el-tab-pane label="每日绩效" name="funds"/>
<el-tab-pane label="交易分析" name="analysis"/><el-tab-pane label="平仓明细" name="closes"/>
</el-tabs>
<div v-if="tab==='summary'" class="summary"><el-descriptions title="回测绩效" :column="3" border><el-descriptions-item v-for="(value,key) in info.summary" :key="key" :label="labels[key] || key">{{value}}</el-descriptions-item></el-descriptions><el-descriptions title="回测参数" :column="3" border><el-descriptions-item v-for="(value,key) in info.env" :key="key" :label="labels[key] || key">{{value}}</el-descriptions-item></el-descriptions></div>
<div ref="chart" class="chart" v-show="tab==='bars' || tab==='funds'"></div>
<RecordTable v-if="['trades','signals','rounds','funds'].includes(tab)" :rows="rows" :fields="tableFields"/>
<div v-if="tab==='analysis'" class="summary"><el-descriptions v-for="(section,key) in analysis" :key="key" :title="labels[key] || key" :column="3" border><el-descriptions-item v-for="(value,name) in section" :key="name" :label="labels[name] || name">{{value}}</el-descriptions-item></el-descriptions></div>
<div v-if="tab==='closes'" class="closes"><el-select v-model="closeKind"><el-option v-for="key in Object.keys(closes)" :key="key" :value="key" :label="labels[key] || key"/></el-select><RecordTable :rows="closes[closeKind] || []"/></div>
</template></el-main>
<el-dialog v-model="showAdd" title="添加工作空间" width="500px"><el-form label-width="90px"><el-form-item label="名称"><el-input v-model="newWorkspace.name"/></el-form-item><el-form-item label="服务器路径"><el-input v-model="newWorkspace.path"/></el-form-item></el-form><template #footer><el-button @click="showAdd=false">取消</el-button><el-button type="primary" @click="addWorkspace">添加</el-button></template></el-dialog>
</el-container>
</template>
<script>
import { markRaw } from 'vue'
import RecordTable from '@/components/common/RecordTable.vue'
import { request } from '@/js/snooper-api.js'
const endpoints={bars:'/bt/qrybars',trades:'/bt/qrybttrds',signals:'/bt/qrybtsigs',rounds:'/bt/qrybtrnds',funds:'/bt/qrybtfunds',analysis:'/bt/qrybtanalysis',closes:'/bt/qrybtcloses'}
export default {
 components:{RecordTable},
 data:()=>({workspaces:[],workspace:'',strategies:[],strategy:'',tab:'summary',info:{summary:{},env:{}},rows:[],analysis:{},closes:{},closeKind:'closes_all',showAdd:false,newWorkspace:{name:'',path:''},loading:false,chartInstance:null,labels:{days:'回测天数',total_return:'总收益率',annual_return:'年化收益率',max_falldown:'最大回撤',win_rate:'胜率',sharpe_ratio:'夏普比率',capital:'初始资金',start:'开始时间',end:'结束时间',code:'代码',period:'周期',summary_all:'全部交易',summary_long:'多头交易',summary_short:'空头交易',closes_all:'全部平仓',closes_long:'多头平仓',closes_short:'空头平仓',closes_month:'月度汇总',closes_year:'年度汇总'}}),
 computed:{tableFields(){const map={code:'代码',date:'日期',time:'时间',direction:'方向',offset:'开平',price:'价格',volume:'数量',qty:'数量',profit:'收益',fee:'手续费',balance:'权益',dynbalance:'动态权益',dynprofit:'浮动收益',closeprofit:'平仓盈亏',tag:'标记',target:'目标仓位',opentime:'开仓时间',closetime:'平仓时间',openprice:'开仓价',closeprice:'平仓价'};return Object.keys(this.rows[0]||{}).map(prop=>({prop,label:map[prop]||prop}))}},
 methods:{
  async perform(action){this.loading=true;try{await action()}catch(error){this.$notify.error(error.message)}finally{this.loading=false}},
  refresh(){return this.perform(async()=>{this.workspaces=(await request('/bt/qryws')).workspaces||[];if(this.workspace&&!this.workspaces.some(item=>item.id===this.workspace)){this.workspace='';this.strategy='';this.strategies=[]}})},
  loadStrategies(){this.strategy='';this.rows=[];return this.perform(async()=>{this.strategies=(await request('/bt/qrybtstras',{wsid:this.workspace})).strategies||[]})},
  selectStrategy(id){this.strategy=id;this.tab='summary';return this.loadTab()},
  loadTab(){const selected=this.strategy,workspace=this.workspace,tab=this.tab;return this.perform(async()=>{if(!selected)return;const result=await request(tab==='summary'?'/bt/qrybtinfo':endpoints[tab],{wsid:workspace,straid:selected});if(selected!==this.strategy||workspace!==this.workspace||tab!==this.tab)return;if(tab==='summary')this.info=result.info||{summary:{},env:{}};else if(tab==='analysis')this.analysis=result.analysis||{};else if(tab==='closes'){this.closes=Object.fromEntries(Object.entries(result).filter(([key])=>key.startsWith('closes_')))}else{this.rows=result[tab]||[];if(tab==='bars'||tab==='funds'){await this.$nextTick();this.draw(tab,result)}}})},
  draw(tab,result){const chart=this.chartInstance||(this.chartInstance=markRaw(this.$echarts.init(this.$refs.chart)));const rows=this.rows;if(tab==='bars'){chart.setOption({tooltip:{trigger:'axis'},xAxis:{type:'category',data:rows.map(row=>row.time||row.date)},yAxis:{scale:true},dataZoom:[{type:'inside'},{type:'slider'}],series:[{name:result.code||'价格',type:'candlestick',data:rows.map(row=>[row.open,row.close,row.low,row.high])}]},true)}else{chart.setOption({tooltip:{trigger:'axis'},xAxis:{type:'category',data:rows.map(row=>row.date)},yAxis:{scale:true},series:[{name:'动态权益',type:'line',data:rows.map(row=>row.dynbalance??row.balance??row.dynprofit??row.closeprofit)}]},true)}chart.resize()},
  addWorkspace(){if(!this.newWorkspace.name.trim()||!this.newWorkspace.path.trim()){this.$message.warning('请填写名称和服务器路径');return}return this.perform(async()=>{await request('/bt/addws',this.newWorkspace);this.showAdd=false;this.workspaces=(await request('/bt/qryws')).workspaces||[]})},
  async removeWorkspace(){try{await this.$confirm('确定删除这个工作空间记录吗？','删除工作空间',{type:'warning'});await this.perform(async()=>{await request('/bt/delws',{wsid:this.workspace});this.workspace='';this.strategy='';this.strategies=[];this.workspaces=(await request('/bt/qryws')).workspaces||[]})}catch{}}
 },
 mounted(){this.refresh();this.resize=()=>this.chartInstance?.resize();window.addEventListener('resize',this.resize)},
 beforeUnmount(){window.removeEventListener('resize',this.resize);this.chartInstance?.dispose()}
}
</script>
<style scoped>.snooper{height:100vh}.sidebar{padding:12px;border-right:1px solid #dcdfe6}.sidebar .el-select{width:100%;margin:12px 0}.toolbar{display:flex;gap:8px;align-items:center;min-height:36px}.results{display:flex;flex-direction:column;overflow:auto}.summary{overflow:auto}.summary .el-descriptions{margin:12px 0}.chart{height:300px;flex-shrink:0}.results>.record-table,.closes{flex:1;min-height:200px}.closes{display:flex;flex-direction:column}</style>
