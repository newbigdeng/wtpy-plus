<template><div ref="host" class="terminal-chart" :aria-label="label" role="img"/></template>
<script setup>
import {ref,watch,onMounted,onBeforeUnmount,nextTick} from 'vue'
import * as echarts from 'echarts'
const props=defineProps({series:{type:Array,default:()=>[]},graph:{type:Object,default:null},label:{type:String,default:'数据曲线'}})
const host=ref(null);let chart,observer
function draw(){
 if(!chart)return
 const style=getComputedStyle(document.documentElement),color=style.getPropertyValue('--wt-muted').trim(),accent=style.getPropertyValue('--wt-accent').trim(),line=style.getPropertyValue('--wt-line').trim()
 const option=props.graph?{tooltip:{renderMode:'richText'},series:[{type:'graph',layout:'force',roam:true,label:{show:true,color,fontSize:11},data:props.graph.nodes,links:props.graph.edges,lineStyle:{color:accent,opacity:.4},itemStyle:{color:accent},force:{repulsion:180,edgeLength:90}}]}:
 {grid:{top:25,left:65,right:22,bottom:45},tooltip:{trigger:'axis',renderMode:'richText'},xAxis:{type:'category',data:props.series.map(r=>String(r.time)),axisLabel:{color},axisLine:{lineStyle:{color:line}}},yAxis:{type:'value',scale:true,axisLabel:{color},splitLine:{lineStyle:{color:line}}},dataZoom:[{type:'inside'}],series:[{type:'line',showSymbol:false,data:props.series.map(r=>r.value),lineStyle:{color:accent,width:1.7},areaStyle:{color:accent,opacity:.08}}]}
 chart.setOption({...option,backgroundColor:'transparent',textStyle:{color},animation:false},true)
}
watch(()=>[props.series,props.graph],()=>nextTick(draw),{deep:true})
onMounted(()=>{chart=echarts.init(host.value);observer=new ResizeObserver(()=>chart.resize());observer.observe(host.value);window.addEventListener('wt-theme-change',draw);draw()})
onBeforeUnmount(()=>{observer?.disconnect();window.removeEventListener('wt-theme-change',draw);chart?.dispose()})
</script>
