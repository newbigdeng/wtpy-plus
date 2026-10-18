<template>
 <section class="data-workbench">
  <div class="data-dimensions"><el-radio-group v-model="dimension" aria-label="账本维度" @change="selectDimension"><el-radio-button value="strategy">策略</el-radio-button><el-radio-button value="portfolio">组合</el-radio-button><el-radio-button value="channel">通道</el-radio-button></el-radio-group><small>{{dimension==='channel'?'柜台账簿：交易账户回报':dimension==='strategy'?'引擎账簿：策略理论持仓与信号':'引擎账簿：组合合并目标与损益'}}</small></div>
  <component :is="panel" :key="groupid+dimension" :groupid="groupid" class="data-dimension-panel"/>
 </section>
</template>
<script>
import {defineAsyncComponent} from 'vue'
const panels={strategy:defineAsyncComponent(()=>import('./stradata.vue')),portfolio:defineAsyncComponent(()=>import('./portdata.vue')),channel:defineAsyncComponent(()=>import('./trddata.vue'))}
export default{
 props:{groupid:{type:String,required:true}},data(){return{dimension:['strategy','portfolio','channel'].includes(this.$route.query.dim)?this.$route.query.dim:(['strategy','portfolio','channel'].includes(localStorage.getItem('wt.data.dimension.'+this.groupid))?localStorage.getItem('wt.data.dimension.'+this.groupid):'strategy')}},
 computed:{panel(){return panels[this.dimension]}},
 methods:{selectDimension(){localStorage.setItem('wt.data.dimension.'+this.groupid,this.dimension);this.$router.replace({query:{...this.$route.query,dim:this.dimension,cat:undefined}})}},
 watch:{'$route.query.dim'(value){if(panels[value])this.dimension=value}}
}
</script>
<style scoped>.data-workbench{height:100%;min-height:560px;display:flex;flex-direction:column;min-width:0}.data-dimensions{display:flex;gap:16px;align-items:center;padding:0 0 12px;flex-wrap:wrap}.data-dimensions small{font-size:11px;color:var(--wt-muted)}.data-dimension-panel{flex:1;min-height:480px}</style>
