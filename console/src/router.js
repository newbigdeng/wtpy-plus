import { createRouter, createWebHashHistory } from 'vue-router'
import {useSessionStore} from './store.js'
import {push} from './js/push.js'

import Login from '@/components/login.vue'

const Index=()=>import('@/components/index.vue')
const Monitor=()=>import('@/components/monitor/main.vue')

const Backtest=()=>import('@/components/backtest/main.vue')
const Schedule=()=>import('@/components/schedule/main.vue')
const Admins=()=>import('@/components/admins/main.vue')
const ApiManual=()=>import('@/components/backtest/ApiManual.vue')
const StrategyLibrary=()=>import('@/components/strategies/StrategyLibrary.vue')
const HistoryManager=()=>import('@/components/history/HistoryManager.vue')
const RiskLibrary=()=>import('@/components/risk-library/main.vue')


const router=createRouter({
    history: createWebHashHistory(),
    routes: [
        { 
            path: '/',
            redirect: '/login', 
        },
        {
            path: '/login',
            name: 'Login',
            component: Login,
        },
        {path:'/api-manual',name:'api-manual',component:ApiManual},
        {
            path: '/index',
            name: 'Index',
            component: Index,
            redirect: '/monitor', 
            children: [
                {path: '/monitor', name:"monitor", component: Monitor},
                {path:'/deploy',redirect:'/monitor'},
                {path: '/backtest', name:"backtest", component: Backtest},
                {path: '/strategies', name:"strategies", component: StrategyLibrary},
                {path: '/history', name:"history", component: HistoryManager},
                {path: '/risk-library', name:"risk-library", component: RiskLibrary},
                {path: '/schedule', name:"schedule", component: Schedule},
                {path: '/admins', name:"admins", component: Admins}
            ]
        }
    ]
})

router.beforeEach(async to=>{
 if(['/login','/api-manual','/'].includes(to.path))return true
 const session=useSessionStore()
 if(!session.cache.isLogined){
  try{
   const result=await (await fetch('/mgr/session',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:'{}'})).json()
   if(result.result===0 && result.userinfo?.loginid){session.loginok({userinfo:result.userinfo,loginid:result.userinfo.loginid});push.connect()}
   else return {path:'/login',query:{redirect:to.fullPath}}
  }catch{return {path:'/login',query:{redirect:to.fullPath}}}
 }
 const role=session.cache.userinfo.role
 if(role==='guest')return true
 if(to.path==='/admins'&&!['admin','superman'].includes(role))return '/monitor'
 if(to.path==='/backtest'&&!['researcher','admin','superman'].includes(role))return '/monitor'
 if(to.path==='/schedule'&&!['admin','superman'].includes(role))return '/monitor'
 return true
})
export default router
