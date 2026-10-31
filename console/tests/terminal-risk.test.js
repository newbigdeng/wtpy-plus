import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'

const source=readFileSync(new URL('../src/components/monitor/TradingTerminal.vue',import.meta.url),'utf8')
const body=source.match(/async loadRisk\(\)\{([^\n]+)\},\r?\n  number/)[1]
const loadRisk=new Function('fetch','return async function(){'+body+'}')

function setup(){
 const pending=[],events=[]
 const fetch=(url,options)=>new Promise(resolve=>pending.push({group:JSON.parse(options.body).groupid,
  done:equity=>resolve({json:async()=>({result:0,snapshot:{broker_summary:{valid:true,equity}}})})}))
 const context={groupid:'a',riskSerial:0,riskSnapshot:null,number:String,$emit:(event,value)=>events.push(value)}
 return {context,pending,events,read:loadRisk(fetch)}
}

test('late response from the previous group cannot overwrite the current account',async()=>{
 const {context,pending,events,read}=setup()
 const first=read.call(context);context.groupid='b';++context.riskSerial;context.riskSnapshot=null
 const second=read.call(context)
 pending[1].done(20000);await second
 pending[0].done(10000);await first
 assert.equal(context.riskSnapshot.broker_summary.equity,20000)
 assert.deepEqual(events,[{groupid:'b',text:'权益 20000'}])
})

test('late response within the same group cannot replace a newer receipt',async()=>{
 const {context,pending,read}=setup()
 const first=read.call(context),second=read.call(context)
 pending[1].done(20000);await second
 pending[0].done(10000);await first
 assert.equal(context.riskSnapshot.broker_summary.equity,20000)
})

test('invalidated request does not emit an overview',async()=>{
 const {context,pending,events,read}=setup()
 const request=read.call(context);++context.riskSerial
 pending[0].done(10000);await request
 assert.equal(context.riskSnapshot,null)
 assert.deepEqual(events,[])
})
