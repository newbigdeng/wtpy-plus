<template>
  <div class="backtest-workspace">
    <el-tabs class="workspace-tabs" :model-value="lab?'lab':'reports'" @update:model-value="switchTab">
      <el-tab-pane name="lab" label="策略回测"/><el-tab-pane name="reports" label="回测报告"/>
    </el-tabs>
    <div v-if="visited" v-show="lab" class="backtest-body">
      <FuturesLab v-if="asset==='futures'"/>
      <AssetResearch v-else :asset="asset" :name="assetNames[asset]" backtest/>
    </div>
    <div v-if="!lab" class="backtest-body"><router-view/></div>
  </div>
</template>
<script setup>
import {computed,ref,watch,defineAsyncComponent} from 'vue'
import {useRoute,useRouter} from 'vue-router'
import AssetResearch from '../assets/AssetResearch.vue'
const FuturesLab=defineAsyncComponent(()=>import('./main.vue'))
const assetNames={stock:'股票',option:'商品期权',crypto:'加密货币'}
const route=useRoute(),router=useRouter(),visited=ref(false)
const asset=computed(()=>Object.hasOwn(assetNames,route.query.asset)?route.query.asset:'futures')
const lab=computed(()=>route.name==='backtest')
watch(lab,value=>{if(value)visited.value=true},{immediate:true})
function switchTab(key){router.push({name:key==='lab'?'backtest':'backtest-reports',query:{asset:asset.value}})}
</script>
<style scoped>
.backtest-workspace{height:100%;min-height:0;display:flex;flex-direction:column}
.workspace-tabs{padding:0 18px;flex:none}.workspace-tabs :deep(.el-tabs__header){margin:0}
.backtest-body{flex:1;min-height:0;overflow:auto}
</style>
