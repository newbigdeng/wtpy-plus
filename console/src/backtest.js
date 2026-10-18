import { createApp, markRaw } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as echarts from 'echarts'
import 'element-plus/dist/index.css'
import './styles.css'
import Snooper from './components/backtest/Snooper.vue'
const app=createApp(Snooper)
app.use(ElementPlus,{locale:zhCn,size:'small'})
app.config.globalProperties.$echarts=markRaw(echarts)
app.mount('#app')
