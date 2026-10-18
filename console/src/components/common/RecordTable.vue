<template>
<div class="record-table"><DataTable :storage-key="storageKey" :data="sortedRows" paginate border stripe height="100%" @sort-change="onSort">
<el-table-column v-for="column in columns" :key="column.prop" :prop="column.prop" :label="column.label" :min-width="column.width || 110" sortable="custom" show-overflow-tooltip/>
</DataTable></div>
</template>
<script>
import DataTable from './DataTable.vue'
const labels={code:'代码',date:'日期',time:'时间',direction:'方向',offset:'开平',price:'价格',volume:'数量',qty:'数量',profit:'盈亏',fee:'手续费',strategy:'策略',channel:'通道',balance:'权益',dynbalance:'动态权益',dynprofit:'浮动盈亏',closeprofit:'平仓盈亏',opentime:'开仓时间',closetime:'平仓时间',openprice:'开仓价',closeprice:'平仓价',tag:'标记',usertag:'标记'}
export default {
 components:{DataTable},
 props: { storageKey:{type:String,default:'wt.records'}, rows:{type:Array,default:()=>[]}, fields:{type:Array,default:()=>[]} },
 data:()=>({sortProp:'',sortOrder:null}),
 computed:{
  columns(){ return this.fields.length ? this.fields : Object.keys(this.rows[0]||{}).map(prop=>({prop,label:labels[prop]||prop})) },
  sortedRows(){
   if (!this.sortOrder) return this.rows;
   const prop=this.sortProp,sign=this.sortOrder==='ascending'?1:-1;
   return [...this.rows].sort((left,right)=>{
    const a=left[prop],b=right[prop];
    if (a==null || b==null) return a==null?(b==null?0:1):-1;
    return sign*(typeof a==='number' && typeof b==='number'?a-b:String(a).localeCompare(String(b),'zh-CN',{numeric:true}));
   });
  }
 },
 methods:{onSort({prop,order}){this.sortProp=prop;this.sortOrder=order}}
}
</script>
<style scoped>.record-table{height:100%;display:flex;flex-direction:column;min-height:120px}.record-table .el-table{flex:1}.el-pagination{padding:8px 4px;flex-shrink:0}</style>
