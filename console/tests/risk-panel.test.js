import {test} from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {createRequire} from 'node:module'
import {parse,compileTemplate} from '@vue/compiler-sfc'
import {createSSRApp,h} from 'vue'
import {renderToString} from 'vue/server-renderer'
import ElementPlus,{ID_INJECTION_KEY,ZINDEX_INJECTION_KEY} from 'element-plus'

const require=createRequire(import.meta.url)
const filename=new URL('../src/components/monitor/RiskPanel.vue',import.meta.url)
const {descriptor}=parse(readFileSync(filename,'utf8'),{filename:filename.pathname})
const child={render:()=>h('section',{'aria-label':'组合交易前风控'},'组合交易前风控')}
const component=new Function('StrategyRuntime','TradeRiskPanel','GroupRiskSelection',
 descriptor.script.content.replace(/^import .*$/gm,'').replace('export default','return'))(child,child,child)
const compiled=compileTemplate({source:descriptor.template.content,filename:filename.pathname,id:'risk-panel-test',ssr:true,ssrCssVars:[],compilerOptions:{mode:'function'}})
assert.equal(compiled.errors.length,0)
component.ssrRender=new Function('require',compiled.code)(require)

function guestState(){
 const metrics=Object.fromEntries([...component.data().metricItems.map(row=>row.key),'limit_usage_pct'].map(key=>[key,{valid:false,value:null,book:'engine',reason:'等待资金账簿'}]))
 return {native:{active:true,supported:true,reason:'需重启'},policy:{enabled:false,capital:0,stale_seconds:30,thresholds:{}},
  snapshot:{state:'stopped',fresh:false,broker_summary:{valid:false,reason:'尚无资金回执'},engine_reference:{equity:null},baseline:null,metrics},
  alerts:[],events:[],can_control:false,can_operate:false}
}
async function render(state){
 const app=createSSRApp({...component,data:()=>({...component.data(),state,policy:state.policy,native:state.native.params||{}})},{groupid:'riskview'})
 app.use(ElementPlus)
 app.provide(ID_INJECTION_KEY,{prefix:0,current:0})
 app.provide(ZINDEX_INJECTION_KEY,{current:0})
 app.directive('loading',{getSSRProps:()=>({})})
 return renderToString(app)
}

test('redacted guest schema renders risk choices and read-only native status',async()=>{
 const state=guestState(),html=await render(state)
 assert.match(html,/组合交易前风控/)
 assert.match(html,/当前配置：启用/)
 assert.match(html,/原生风控参数仅管理员可查看和修改/)
 assert.doesNotMatch(html,/保存原生配置（需重启组合）/)
 const context={...component.data(),groupid:'riskview',request:async()=>state}
 await component.methods.load.call(context,true)
 assert.equal(context.error,'')
 assert.deepEqual(context.native,{})
 assert.deepEqual(component.computed.brokerAccounts.call(context),[])
 assert.equal(component.computed.queryChannel.call(context),'')
})
test('absent status is unknown and administrator retains the native parameter form',async()=>{
 const state=guestState();delete state.native.active
 assert.match(await render(state),/当前配置：未知/)
 state.native.params={active:false,base_amount:1000000,calc_span:5};state.can_control=true
 const html=await render(state)
 assert.match(html,/当前配置：关闭/)
 assert.match(html,/保存原生配置（需重启组合）/)
})
