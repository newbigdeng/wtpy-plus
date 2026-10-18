<script>
import {defineComponent,h,ref,computed,watch,Fragment,resolveComponent,cloneVNode} from 'vue'

const textValue=value=>value && typeof value==='object'?JSON.stringify(value):String(value??'')
const flatten=nodes=>(nodes||[]).flatMap(node=>node.type===Fragment?flatten(node.children):[node])
const columnKey=node=>node.props?.prop||node.props?.label
const nodeText=nodes=>(Array.isArray(nodes)?nodes:[nodes]).map(node=>typeof node==='string'||typeof node==='number'?String(node):Array.isArray(node?.children)?nodeText(node.children):typeof node?.children==='string'?node.children:'').join('')
const readTime=row=>{
 const value=row.time??row.closetime??row.opentime??row.date
 if(value==null)return null
 const s=String(value).replace(/[^0-9]/g,'')
 if(s.length>=8 && /^(19|20)\d{6}/.test(s))return s.slice(0,8)
 const stamp=Number(value),date=new Date(stamp<1e11?stamp*1000:stamp)
 if(Number.isNaN(date.getTime()))return null
 return new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai'}).format(date).replaceAll('-','')
}
export default defineComponent({
 inheritAttrs:false,
 props:{data:{type:Array,default:()=>[]},storageKey:{type:String,required:true},paginate:Boolean},
 setup(props,{attrs,slots}){
  const table=resolveComponent('el-table'), input=resolveComponent('el-input'),picker=resolveComponent('el-date-picker'),button=resolveComponent('el-button'),popover=resolveComponent('el-popover'),checkbox=resolveComponent('el-checkbox'),pagination=resolveComponent('el-pagination')
  let saved={};try{saved=JSON.parse(localStorage.getItem(props.storageKey)||'{}')}catch{}
  const page=ref(1),pageSize=ref(100)
  const query=ref(saved.query||''),dates=ref(saved.dates||null),hidden=ref(saved.hidden||[])
  const filtered=computed(()=>props.data.filter(row=>{
   if(query.value && !Object.values(row).some(v=>textValue(v).toLowerCase().includes(query.value.trim().toLowerCase())))return false
   if(dates.value?.length){const d=readTime(row);if(!d || d<dates.value[0] || d>dates.value[1])return false}
   return true
  }))
  watch([query,dates,()=>props.data,pageSize],()=>{page.value=1})
  const displayed=computed(()=>props.paginate?filtered.value.slice((page.value-1)*pageSize.value,page.value*pageSize.value):filtered.value)
  watch([query,dates,hidden],()=>localStorage.setItem(props.storageKey,JSON.stringify({query:query.value,dates:dates.value,hidden:hidden.value})),{deep:true})
  function exportCsv(){
   const columns=flatten(slots.default?.()).filter(node=>columnKey(node) && !hidden.value.includes(columnKey(node)))
   const escape=value=>'"'+textValue(value).replaceAll('"','""')+'"'
   const cell=(column,row,index)=>{
    const renderer=column.children?.default
    if(renderer)return nodeText(renderer({row,$index:index,column:{property:column.props.prop}}))
    if(typeof column.props.formatter==='function')return column.props.formatter(row,{property:column.props.prop})
    return column.props.prop?.split('.').reduce((value,key)=>value?.[key],row)
   }
   const content=[columns.map(column=>escape(column.props.label||column.props.prop)).join(','),...filtered.value.map((row,index)=>columns.map(column=>escape(cell(column,row,index))).join(','))].join('\r\n')
   const url=URL.createObjectURL(new Blob(['\uFEFF'+content],{type:'text/csv;charset=utf-8'})),link=document.createElement('a');link.href=url;link.download=props.storageKey.replaceAll('.','-')+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
  }
  return()=>{
   const nodes=flatten(slots.default?.()),columns=nodes.filter(n=>columnKey(n)),visible=nodes.filter(n=>!columnKey(n) || !hidden.value.includes(columnKey(n)))
   const firstColumn=visible.findIndex(columnKey)
   return h('div',{class:'data-table'},[
    h('div',{class:'data-table-tools'},[
     h(input,{modelValue:query.value,'onUpdate:modelValue':v=>query.value=v,clearable:true,placeholder:'合约 / 关键词','aria-label':'筛选表格关键词',class:'data-table-search'}),
     h(picker,{modelValue:dates.value,'onUpdate:modelValue':v=>dates.value=v,type:'daterange',valueFormat:'YYYYMMDD',startPlaceholder:'开始日期',endPlaceholder:'结束日期','aria-label':'筛选表格日期',class:'data-table-dates'}),
     h('small',filtered.value.length+' / '+props.data.length+' 条'),
     h(popover,{trigger:'click',width:220,placement:'bottom-end'},{reference:()=>h(button,{'aria-label':'设置显示列'},()=> '列设置'),default:()=>h('div',{class:'data-column-options'},columns.map(c=>h(checkbox,{modelValue:!hidden.value.includes(columnKey(c)),disabled:visible.filter(columnKey).length===1&&!hidden.value.includes(columnKey(c)),'onUpdate:modelValue':v=>hidden.value=v?hidden.value.filter(x=>x!==columnKey(c)):[...hidden.value,columnKey(c)]},()=>c.props.label||c.props.prop)))}),
     h(button,{onClick:exportCsv,disabled:!filtered.value.length,'aria-label':'导出当前表格 CSV'},()=> '导出 CSV')
    ]),
    h(table,{...attrs,data:displayed.value,class:['data-table-grid',attrs.class],height:attrs.height||'100%'}, {...slots,default:()=>window.innerWidth<=1200?visible.map((node,index)=>index===firstColumn&&columnKey(node)?cloneVNode(node,{fixed:'left'}):node):visible}),
    props.paginate?h(pagination,{currentPage:page.value,'onUpdate:currentPage':value=>page.value=value,pageSize:pageSize.value,'onUpdate:pageSize':value=>pageSize.value=value,pageSizes:[50,100,200],layout:'total,sizes,prev,pager,next',total:filtered.value.length}):null
   ])
  }
 }
})
</script>
<style scoped>
.data-table{height:100%;min-height:240px;display:flex;flex-direction:column;min-width:0}.data-table-tools{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 0 10px;flex:0 0 auto}.data-table-search{width:190px}.data-table-tools .data-table-dates{flex:0 0 250px;width:250px}.data-table-tools small{color:var(--wt-muted);font-size:10px;margin-left:auto}.data-table>.el-pagination{flex:0 0 auto;padding:8px 4px}.data-table-grid{flex:1;min-height:180px;width:100%}.data-column-options{display:flex;flex-direction:column;max-height:300px;overflow:auto}.data-column-options .el-checkbox{margin:0} @media(max-width:1200px){.data-table :deep(.el-table__body-wrapper){overflow-x:auto}.data-table-tools{gap:6px}}
</style>
