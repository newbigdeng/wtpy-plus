<template><component :is="icons[name] || icons.monitor" class="wt-icon" aria-hidden="true" /></template>
<script setup>
import { Monitor, Timer, TrendCharts, Upload, User, Setting, SwitchButton, Bell, Search, Plus, Folder, Fold, Expand, ArrowDown, ArrowUp, Refresh, Connection, Sunny, Moon, Reading, Document, VideoPlay, Check, Close } from '@element-plus/icons-vue'
defineProps({name:{type:String,default:'monitor'}})
const icons={monitor:Monitor,schedule:Timer,backtest:TrendCharts,deploy:Upload,admins:User,settings:Setting,logout:SwitchButton,bell:Bell,search:Search,plus:Plus,folder:Folder,fold:Fold,expand:Expand,down:ArrowDown,up:ArrowUp,refresh:Refresh,connection:Connection,sun:Sunny,moon:Moon,book:Reading,document:Document,play:VideoPlay,check:Check,close:Close}
</script>
<style>.wt-icon{width:20px;height:20px;display:inline-block;flex-shrink:0;vertical-align:middle;}</style>
