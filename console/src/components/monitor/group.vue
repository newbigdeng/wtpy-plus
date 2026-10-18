<template>
    <div class="wt-group-panels">
        <section class="wt-data-panel" aria-label="组合数据">
            <el-tabs :model-value="selData" class="wt-primary-tabs" @tab-click="handleClickTab">
                <el-tab-pane label="交易工作台" name="terminal"/>
                <el-tab-pane label="策略管理" name="runtime"/>
                <el-tab-pane label="风控中心" name="risk"/>
                <el-tab-pane label="数据" name="data"/>
            </el-tabs>
            <div class="wt-data-content">
                <TradingTerminal v-if="selData==='terminal'" :groupid="groupid" @overview="$emit('overview',$event)"/>
                <StrategyRuntime v-if="selData==='runtime'" :groupid="groupid"/>
                <RiskPanel v-if="selData==='risk'" :groupid="groupid"/>
                <DataWorkbench v-if="selData==='data'" :groupid="groupid"/>
            </div>
        </section>
        <el-drawer v-model="showConfig" title="组合配置" size="min(900px, 96vw)" append-to-body>
            <el-tabs v-model="configTab"><el-tab-pane label="通道环境" name="profiles"/><el-tab-pane label="配置文件" name="editor"/></el-tabs>
            <ChannelProfiles v-if="configTab==='profiles'" :groupid="groupid" @changed="$emit('profile-changed')"/>
            <Editor v-else :groupid="groupid" style="height:70vh"/>
        </el-drawer>
        <ResizableDivider v-if="showLogs" v-model="logHeight" axis="y" :reverse="true" :min="100" :max="360" storage-key="wt.monitor.logHeight" label="调整日志面板高度"/>
        <section class="wt-log-panel" :style="{flexBasis:showLogs?logHeight+'px':'44px'}" :class="{collapsed:!showLogs}" aria-label="运行日志">
            <div class="wt-log-toolbar">
                <button class="wt-log-toggle" :aria-expanded="showLogs" aria-controls="wt-runtime-log" @click="showLogs=!showLogs"><WtIcon :name="showLogs?'down':'up'"/><strong>运行日志</strong><span class="wt-log-kind">{{logScroll?'实时流':'文件日志'}}</span></button>
                <span class="wt-log-events">事件 {{eventCount}} · {{pushConnected?'推送已连接':'推送未连接'}}</span><time class="wt-log-updated">{{logTime}}</time>
                <el-tooltip content="实时日志由推送追加；文件日志每15秒刷新" :enterable="false"><el-checkbox v-model="autoLog" @change="handleCheckAutoLog">{{logScroll?'实时追加':'自动刷新'}}</el-checkbox></el-tooltip>
                <button class="wt-icon-button" aria-label="刷新日志" title="刷新日志" @click="handleClickQryLog"><WtIcon name="refresh"/></button>
            </div>
            <div v-show="showLogs" id="wt-runtime-log" class="wt-log-content" v-loading="logOnway"><textarea readonly ref="logs" aria-label="运行日志内容" autocomplete="off" placeholder="等待日志数据…" :value="logs"/></div>
        </section>
    </div>
</template>

<script>
import WtIcon from '../common/WtIcon.vue';
import ResizableDivider from '../common/ResizableDivider.vue';
import TradingTerminal from './TradingTerminal.vue';
import StrategyRuntime from './StrategyRuntime.vue';
import RiskPanel from './RiskPanel.vue';
import ChannelProfiles from './ChannelProfiles.vue';
import logControls from "@/js/log-controls.js";
import { mapState } from 'pinia';
import { useSessionStore } from '@/store.js';
import DataWorkbench from './DataWorkbench.vue'
import {pushState} from '@/js/push.js'
import Editor from './editor.vue'
export default {
    name: 'empty',
    components: {
        DataWorkbench, Editor, WtIcon, ResizableDivider, TradingTerminal, StrategyRuntime, RiskPanel, ChannelProfiles
    },
    computed: {
        pushConnected(){return pushState.connected},
        ...mapState(useSessionStore, [
            'cache'
        ]),
        isAdmin(){
            let uInfo = this.cache.userinfo;
            if(uInfo)
                return (uInfo.role == 'admin' || uInfo.role == 'superman');
            else
                return false;        
        },
        groupid(){
            if(this.groupinfo == null)
                return "";
            else
                return this.groupinfo.id;
        }
    },
    props:{
        groupinfo:{
            type: Object,
            default() {
                return {
                    id:"",
                    name:"",
                    path:"",
                    info:"",
                    gtype:"cta",
                    datmod:"auto"
                }
            }
        }
    },
    watch:{
        '$route.query.tab'(value){if(['terminal','runtime','risk','data'].includes(value))this.selData=value},
        '$route.query.config'(value){if(value==='1')this.showConfig=true},
        showConfig(value){this.$router.replace({query:{...this.$route.query,config:value?'1':undefined}})},
        groupinfo(newGrp, oldVal){
            this.$push.subscribe(newGrp?.id || "");
            let self = this;

            if(newGrp == null)
                return;

            if(oldVal != null && newGrp.id == oldVal.id)
                return;

            logControls.selectGroup(this, newGrp);
            this.queryLogs(true);
        }
    },
    data () {
        return {
            showLogs: false,
            logHeight: 190,
            selData:['terminal','runtime','risk','data'].includes(this.$route.query.tab)?this.$route.query.tab:(['terminal','runtime','risk','data'].includes(localStorage.getItem('wt.monitor.tab.'+this.groupinfo.id))?localStorage.getItem('wt.monitor.tab.'+this.groupinfo.id):'terminal'),
            showConfig:this.$route.query.config==='1',configTab:'profiles',eventCount:0,
            logs:"",
            logfilter:"total",
            logCache:"",
            logLines:0,
            logScroll: true,
            logOnway: false,
            autoLog: false,
            logInterval: 0,
            logTime:new Date().format('yyyy.MM.dd hh:mm:ss')
        }
    },
    methods: {
        receiveNotify(data) {
            const self = this;
            if(data.groupid != self.groupinfo.id)
                return;

            if(data.type == "gplog"){
                if(!logControls.shouldAppend(self))
                    return;

                self.processLog(data);
            } else if(data.type == "chnlevt"){
                self.processEvent(data);
            }

        },
        handleClickTab: function(tab, event){
            this.selData = tab.props.name;
            localStorage.setItem('wt.monitor.tab.'+this.groupid,this.selData);
            this.$router.replace({query:{...this.$route.query,tab:this.selData}});
        },
        openConfig(){this.showConfig=true;},
        handleClickQryLog: function(){
            setTimeout(()=>{
                this.queryLogs(true);
            }, 300);
        },
        handleCheckAutoLog: function(val){
            logControls.save(this);
            this.resetLogInterval();
        },
        resetLogInterval: function(){ logControls.reset(this); },
        queryLogs: function(needReset){
            needReset = needReset || false;
            let self = this;
            if(this.groupinfo.id == "")
                return;

            const request = logControls.beginRead(self);
            self.logOnway = true;
            this.$api.getLogs(this.groupinfo.id, this.logfilter, (resObj)=>{
                if (!logControls.acceptRead(self, request)) return;
                if(resObj.result < 0){
                    this.logs = '';
                    this.logLines = 0;
                    this.$notify.error('组合日志拉取失败：' + resObj.message);
                    self.$nextTick(()=>{
                        self.$refs.logs?.scrollTo(0,0);
                    });
                } else {
                    this.logs = resObj.content;
                    this.logLines = resObj.lines||0;
                    self.$nextTick(()=>{
                        let height = self.$refs.logs?.scrollHeight || 0;
                        self.$refs.logs?.scrollTo(0,height);
                    });
                }
                self.logTime = new Date().format('yyyy.MM.dd hh:mm:ss');
                self.logOnway = false;

                if(needReset)
                    self.resetLogInterval();
            });
        },
        processLog: function(data){
            let self = this;
            let message = data.message || "";
            if(message.length > 0){
                let isEmpty = self.logCache.length == 0;
                let date = new Date(data.time);
                let line = "[" + date.format("yyyy.MM.dd hh:mm:ss") + " - " + data.tag + "] " + message + "\n";
                self.logCache += line;
                self.logLines ++;
                if(isEmpty){
                    self.$nextTick(()=>{
                        if(self.logLines >= 200){
                            self.logs = "";
                            self.logLines = 0;
                        }

                        self.logs += self.logCache;
                        self.logCache = "";
                        self.$nextTick(()=>{
                            let height = self.$refs.logs?.scrollHeight || 0;
                            self.$refs.logs?.scrollTo(0,height);
                        });
                    });
                }
            }
        },
        processEvent: function(data){
            let self = this;
            let evttype = data.evttype || "";
            this.eventCount++;
            if(evttype == '')
                return;

            if(evttype == 'notify'){
                self.$notify({
                    title:"订单回报",
                    type:"error",
                    message: "交易通道{0}错误：{1}".format(data.channel,data.message),
                    duration: 0
                });
            } else if(evttype == 'order'){
                if(!data.data.canceled)
                    return;

                self.$notify({
                    title:"订单回报",
                    type:"error",
                    message: "交易通道{0}订单已撤销，本地订单号：{1}".format(data.channel,data.data.localid),
                    duration: 0
                });
            } else if(evttype == 'trade'){
                let action = (data.data.isopen?"开":"平") + (data.data.islong?"多":"空");
                self.$notify({
                    title:"成交回报",
                    type:"success",
                    message: "交易通道：{0}，操作：{1}，代码：{2}，数量：{3}，成交价：{4}，本地订单号：{5}".format(
                        data.channel, action, data.data.code, data.data.volume, data.data.price, data.data.localid)
                });
            }
        }
    },
    beforeUnmount() { logControls.dispose(this); },
    mounted() {
        this.$push.subscribe(this.groupinfo?.id || "");
        logControls.selectGroup(this, this.groupinfo);
        this.queryLogs(true);
        let self = this;

    }
}
</script>


<style scoped>
.wt-group-panels{height:auto;min-height:100%;display:flex;flex-direction:column;}
.wt-data-panel{flex:1;min-height:700px;display:flex;flex-direction:column;}
.wt-primary-tabs{flex:0 0 48px;}
.wt-primary-tabs :deep(.el-tabs__header){height:48px;margin:0;}
.wt-primary-tabs :deep(.el-tabs__item){height:48px;font-size:13px;}
.wt-primary-tabs :deep(.el-tabs__content){display:none;}
.wt-data-content{flex:1;min-height:600px;overflow:visible;padding-top:12px;}
.wt-log-panel{flex:0 0 190px;min-height:0;border-top:1px solid var(--wt-line);display:flex;flex-direction:column;margin-top:12px;}
.wt-log-panel.collapsed{flex-basis:44px;max-height:44px;}
.wt-log-toolbar{display:flex;gap:16px;align-items:center;flex:0 0 44px;min-width:0;}
.wt-log-toggle{background:none;border:0;font:inherit;display:flex;gap:8px;align-items:center;padding:0;cursor:pointer;color:var(--wt-text);}
.wt-log-toggle .wt-icon{height:13px;width:13px;color:var(--wt-muted);}
.wt-log-toggle strong{font-weight:550;font-size:12px;white-space:nowrap;}
.wt-log-kind{font-size:10px;color:var(--wt-muted);border:1px solid var(--wt-line);border-radius:4px;padding:2px 5px;white-space:nowrap;}
.wt-log-events{font-size:10px;color:var(--wt-muted);white-space:nowrap;}.wt-log-updated{font-size:10px;color:var(--wt-muted);margin-left:auto;font-family:var(--wt-mono);}
.wt-log-toolbar .el-checkbox{height:24px;}
.wt-log-toolbar :deep(.el-checkbox__label){font-size:11px;}
.wt-log-content{flex:1;min-height:0;padding-bottom:12px;}
.wt-log-content textarea{box-sizing:border-box;width:100%;height:100%;resize:none;border:1px solid var(--wt-line);border-radius:8px;background:var(--wt-matte);color:var(--wt-text);padding:10px 12px;outline:none;font:11px/1.85 var(--wt-mono);}
.wt-log-content textarea:focus{border-color:var(--wt-muted);}
@media(max-width:1000px){.wt-log-updated{display:none;}.wt-log-toolbar{gap:10px;}.wt-log-toggle{margin-right:auto;}}
</style>
