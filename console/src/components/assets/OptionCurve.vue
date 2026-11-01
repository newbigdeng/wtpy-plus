<template><div class="plot" ref="host" role="img" :aria-label="title"/></template>
<script setup>
import {computed,getCurrentInstance,onBeforeUnmount,onMounted,ref,watch} from 'vue'
const props=defineProps({points:Array,history:Array,terms:Array})
const title=computed(()=>props.history?'看涨减看跌 IV 差历史':props.terms?'平值波动率期限结构':'行权价与隐含波动率')
const echarts=getCurrentInstance().appContext.config.globalProperties.$echarts
const host=ref(null)
let chart,observer
function draw(){
  if(!chart)return
  const styles=getComputedStyle(host.value)
  const color=styles.getPropertyValue('--wt-muted').trim()
  const axis=styles.getPropertyValue('--wt-line').trim()
  const timeAxis=Boolean(props.history||props.terms)
  let lines
  if(props.history){
    lines=[{name:'加权 IV 差',data:props.history.map(([s,v])=>[Date.UTC(+s.slice(0,4),+s.slice(4,6)-1,+s.slice(6,8),+s.slice(9,11),+s.slice(12,14)),v*100])}]
  }else if(props.terms){
    lines=['call_atm','put_atm'].map((k,i)=>({name:i?'看跌':'看涨',data:props.terms.filter(r=>r[k]!=null).map(r=>{
      const s=String(r.expiry);return [Date.UTC(+s.slice(0,4),+s.slice(4,6)-1,+s.slice(6)),r[k]*100]})}))
  }else{
    lines=['看涨','看跌'].map(side=>({name:side,data:props.points.filter(r=>r.side===side).map(r=>[r.strike,r.iv*100]).sort((a,b)=>a[0]-b[0])}))
  }
  chart.setOption({animation:false,useUTC:true,color:['#408ede','#ba608e'],textStyle:{color},tooltip:{trigger:'axis',renderMode:'richText'},
    legend:{right:8,textStyle:{color}},grid:{left:52,right:25,top:30,bottom:28},
    xAxis:{type:timeAxis?'time':'value',scale:true,axisLine:{lineStyle:{color:axis}},axisLabel:{color}},
    yAxis:{type:'value',scale:true,axisLabel:{color,formatter:'{value}%'},splitLine:{lineStyle:{color:axis}}},
    series:lines.map(s=>({...s,type:'line',symbolSize:5,showSymbol:true}))},true)
}
onMounted(()=>{chart=echarts.init(host.value);observer=new ResizeObserver(()=>chart?.resize());observer.observe(host.value);draw();window.addEventListener('wt-theme-change',draw)})
watch(()=>[props.points,props.history,props.terms],draw)
onBeforeUnmount(()=>{observer?.disconnect();window.removeEventListener('wt-theme-change',draw);chart?.dispose();chart=null})
</script>
<style scoped>.plot{height:175px;max-width:800px;width:100%}</style>
