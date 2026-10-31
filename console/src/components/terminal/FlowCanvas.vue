<template>
 <div>
  <div class="terminal-actions" style="margin-bottom:12px"><el-input v-model="flow.name" placeholder="流程名称" aria-label="流程名称" style="width:180px"/><el-button :disabled="!canEdit" @click="save">保存流程</el-button><el-button type="primary" :disabled="!canEdit||!flow.id||dirty" :loading="running" @click="run">运行已保存流程</el-button><el-button @click="reset">新建</el-button></div>
  <div class="terminal-actions"><el-select :model-value="flow.id||''" placeholder="打开已保存流程" style="width:210px" aria-label="打开流程" @update:model-value="openFlow"><el-option v-for="f in savedFlows" :key="f.id" :value="f.id" :label="f.name"/></el-select><el-select model-value="" placeholder="添加节点" style="width:140px" :disabled="!canEdit||flow.nodes.length>=8" aria-label="添加节点" @update:model-value="addNode"><el-option v-for="(name,type) in names" :key="type" :value="type" :label="name"/></el-select><el-button :disabled="!canEdit||!current" @click="removeNode">删除选中节点</el-button></div>
  <p class="note">拖动节点排版，选中节点编辑参数和上游。此流程分析外部价格收益，不改变因子准入或 WT 策略。</p>
  <div class="terminal-flow-canvas" @pointermove="move" @pointerup="drag=null" @pointercancel="drag=null">
   <svg ref="svg" viewBox="0 0 1000 310" aria-label="分析流程画布">
    <path v-for="(edge,i) in flow.edges" :key="i" :d="edgePath(edge)"/>
    <g v-for="node in flow.nodes" :key="node.id" :transform="`translate(${node.x},${node.y})`" :class="{selected:selected===node.id}" tabindex="0" role="button" :aria-label="names[node.type]+'节点'" @click="selected=node.id" @keydown.enter="selected=node.id" @pointerdown="begin($event,node)">
     <rect width="174" height="73" rx="3"/><circle cx="0" cy="36" r="3"/><circle cx="174" cy="36" r="3"/><text x="15" y="25">{{names[node.type]}}</text><text x="15" y="50" class="node-type">{{node.type==='data'?node.params.dataset:node.type==='risk'?'VaR · CVaR · Sharpe':node.type==='returns'?'相邻价格 → 简单收益':'保留来源与结果'}}</text>
    </g>
   </svg>
  </div>
  <div v-if="current" class="terminal-panel-body" style="border-top:1px solid var(--wt-line)">
   <div class="terminal-actions"><strong>{{names[current.type]}}</strong><small class="note">可用连线由数据类型决定</small></div>
   <el-form label-position="top" style="margin-top:14px;max-width:400px">
    <el-form-item v-if="current.type==='data'" label="日频参考价格"><el-select v-model="current.params.dataset" :disabled="!canEdit" aria-label="流程数据集"><el-option v-for="d in datasets.filter(d=>d.frequency==='日度')" :key="d.id" :value="d.id" :label="d.name"/></el-select></el-form-item>
    <template v-if="current.type==='risk'"><el-form-item label="分析本金"><el-input-number v-model="current.params.capital" :min="1" :disabled="!canEdit"/></el-form-item><el-form-item label="每年观测次数"><el-input-number v-model="current.params.periods" :min="1" :max="366" :disabled="!canEdit"/></el-form-item></template>
    <el-form-item v-if="current.type!=='data'" label="上游节点"><el-select :model-value="parent(current.id)" :disabled="!canEdit" @update:model-value="link(current.id,$event)"><el-option v-for="n in possibleParents" :key="n.id" :value="n.id" :label="names[n.type]+' · '+n.id"/></el-select></el-form-item>
   </el-form>
  </div>
  <div v-if="runs.length" class="terminal-panel-body"><strong>运行记录</strong><el-table :data="runs.slice(0,8)" @row-click="showRun"><el-table-column prop="status" label="状态" width="120"/><el-table-column label="运行时间"><template #default="{row}">{{new Date(row.started_at*1000).toLocaleString()}}</template></el-table-column><el-table-column prop="error" label="停止原因"/></el-table></div>
  <div v-if="result" class="terminal-panel-body"><el-alert v-if="result.error" :title="result.error" type="warning" :closable="false"/><p class="note">{{lastOutput?.note}}</p><div v-if="lastOutput?.metrics" class="metric-grid"><div v-for="(v,k) in lastOutput.metrics" :key="k" class="metric"><span>{{k}}</span><strong>{{formatMetric(k,v)}}</strong></div></div><TerminalChart v-if="lastOutput?.series" :series="lastOutput.series"/></div>
 </div>
</template>
<script setup>
import {ref,computed,watch,onMounted} from 'vue'
import {ElMessage} from 'element-plus'
import TerminalChart from './TerminalChart.vue'
import {formatMetric} from './format.js'
const props=defineProps({request:Function,datasets:Array,canEdit:Boolean})
const names={data:'公开数据',returns:'收益变换',risk:'风险分析',result:'保存结果'}
const fresh=()=>({name:'参考价格风险分析',nodes:[{id:'data1',type:'data',x:30,y:70,params:{dataset:'sp500'}},{id:'returns1',type:'returns',x:270,y:140,params:{}},{id:'risk1',type:'risk',x:510,y:70,params:{capital:10000,periods:252,confidence:.95,risk_free:.02}},{id:'result1',type:'result',x:750,y:140,params:{}}],edges:[{source:'data1',target:'returns1'},{source:'returns1',target:'risk1'},{source:'risk1',target:'result1'}]})
const flow=ref(fresh()),selected=ref('data1'),svg=ref(null),drag=ref(null),dirty=ref(true),running=ref(false),runs=ref([]),result=ref(null),saved=ref(''),savedFlows=ref([])
const current=computed(()=>flow.value.nodes.find(n=>n.id===selected.value))
const possibleParents=computed(()=>flow.value.nodes.filter(n=>n.type===({returns:'data',risk:'returns',result:'risk'})[current.value?.type]))
const lastOutput=computed(()=>{const values=Object.values(result.value?.outputs||{});return values[values.length-1]})
watch(flow,()=>{dirty.value=JSON.stringify(flow.value)!==saved.value},{deep:true})
function parent(id){return flow.value.edges.find(e=>e.target===id)?.source}
function link(target,source){flow.value.edges=flow.value.edges.filter(e=>e.target!==target);flow.value.edges.push({source,target})}
function edgePath(e){const a=flow.value.nodes.find(n=>n.id===e.source),b=flow.value.nodes.find(n=>n.id===e.target);return a&&b?`M${a.x+174},${a.y+36} C${a.x+210},${a.y+36} ${b.x-35},${b.y+36} ${b.x},${b.y+36}`:''}
function point(event){const p=svg.value.createSVGPoint();p.x=event.clientX;p.y=event.clientY;return p.matrixTransform(svg.value.getScreenCTM().inverse())}
function begin(event,node){selected.value=node.id;if(!props.canEdit)return;const p=point(event);drag.value={id:node.id,dx:p.x-node.x,dy:p.y-node.y};event.currentTarget.setPointerCapture(event.pointerId)}
function move(event){if(!drag.value)return;const n=flow.value.nodes.find(n=>n.id===drag.value.id),p=point(event);n.x=Math.max(5,Math.min(820,p.x-drag.value.dx));n.y=Math.max(5,Math.min(232,p.y-drag.value.dy))}
function reset(){flow.value=fresh();selected.value='data1';result.value=null;saved.value=''}
function openFlow(id){const item=savedFlows.value.find(f=>f.id===id);if(!item)return;flow.value=JSON.parse(JSON.stringify(item));selected.value=flow.value.nodes[0]?.id;saved.value=JSON.stringify(flow.value);dirty.value=false;result.value=null}
function addNode(type){let i=1;while(flow.value.nodes.some(n=>n.id===type+i))i++;const id=type+i;flow.value.nodes.push({id,type,x:60+40*(flow.value.nodes.length%6),y:40+25*(flow.value.nodes.length%6),params:type==='data'?{dataset:'sp500'}:type==='risk'?{capital:10000,periods:252,confidence:.95,risk_free:.02}:{}});selected.value=id}
function removeNode(){const id=selected.value;flow.value.nodes=flow.value.nodes.filter(n=>n.id!==id);flow.value.edges=flow.value.edges.filter(e=>e.source!==id&&e.target!==id);selected.value=flow.value.nodes[0]?.id}
function showRun(row){result.value=row}
async function load(){const data=await props.request('workflow_list');runs.value=data.runs||[];savedFlows.value=data.workflows||[];if(savedFlows.value.length)openFlow(savedFlows.value[0].id)}
async function save(){try{const data=await props.request('workflow_save',flow.value);flow.value=data.workflow;saved.value=JSON.stringify(flow.value);dirty.value=false;savedFlows.value=[data.workflow,...savedFlows.value.filter(f=>f.id!==data.workflow.id)];ElMessage.success('流程已保存')}catch(e){ElMessage.error(e.message)}}
async function run(){running.value=true;try{const data=await props.request('workflow_run',{id:flow.value.id});result.value=data.run;runs.value.unshift(data.run)}catch(e){ElMessage.error(e.message)}finally{running.value=false}}
onMounted(()=>load().catch(e=>ElMessage.error(e.message)))
</script>
