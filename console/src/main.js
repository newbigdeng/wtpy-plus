import { createApp, markRaw } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as echarts from 'echarts'
import 'element-plus/dist/index.css'
import './themes/theme-black/icon.css'
import './styles.css'
import './mobile.css'
import './js/theme.js'
import App from './App.vue'
import router from './router.js'
import Api from './js/api.js'
import { push } from './js/push.js'
const app = createApp(App)
app.use(createPinia()).use(router).use(ElementPlus, { locale: zhCn, size: 'small' })
app.config.globalProperties.$api = markRaw(new Api())
app.config.globalProperties.$echarts = markRaw(echarts)
app.config.globalProperties.$push = markRaw(push)
app.config.globalProperties.$toast = app.config.globalProperties.$message
// Chart instances contain private state that Vue must not proxy.
app.mixin({
  mounted() {
    if (!['nvChart','pieChart','kChart','tChart'].some(key=>key in this)) return
    this._resizeCharts = () => {
      clearTimeout(this._resizeTimer)
      this._resizeTimer = setTimeout(() => {
        for (const key of ['nvChart','pieChart','kChart','tChart']) this[key]?.resize?.()
      }, 150)
    }
    window.addEventListener('resize', this._resizeCharts)
    this._themeCharts = () => {
      const style = getComputedStyle(document.documentElement)
      const color = style.getPropertyValue('--wt-muted').trim()
      const line = style.getPropertyValue('--wt-chart-grid').trim()
      for (const key of ['nvChart','pieChart','kChart','tChart']) {
        const chart = this[key]
        if(!chart || chart.isDisposed?.()) continue
        const option = chart.getOption()
        const axes = name => (option[name] || []).map(() => ({axisLabel:{color},axisLine:{lineStyle:{color:line}},splitLine:{lineStyle:{color:line}}}))
        chart.setOption({backgroundColor:'transparent',textStyle:{color},title:(option.title||[]).map(()=>({textStyle:{color},subtextStyle:{color}})),legend:(option.legend||[]).map(()=>({textStyle:{color}})),xAxis:axes('xAxis'),yAxis:axes('yAxis')})
      }
    }
    window.addEventListener('wt-theme-change', this._themeCharts)
    this._chartObserver = new ResizeObserver(() => {this._resizeCharts();this._themeCharts()})
    if(this.$el instanceof Element) this._chartObserver.observe(this.$el)
  },
  beforeUnmount() {
    clearInterval(this.dataInterval); clearInterval(this.logInterval)
    clearTimeout(this._resizeTimer)
    this._chartObserver?.disconnect()
    window.removeEventListener('wt-theme-change', this._themeCharts)
    window.removeEventListener('resize', this._resizeCharts)
    this._unsubscribePush?.()
    for (const key of ['nvChart','pieChart','kChart','tChart']) this[key]?.dispose?.()
  }
})
app.mount('#app')
