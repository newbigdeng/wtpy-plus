<template>
 <div class="scheduler-workspace">
  <section class="scheduler-list">
   <div class="scheduler-toolbar"><el-tabs v-model="selCat" @tab-change="value=>value==='logs'&&queryLogs()"><el-tab-pane label="调度列表" name="list"/><el-tab-pane label="调度日志" name="logs"/></el-tabs><el-button @click="queryData()">刷新</el-button><el-button @click="handleAppCommand('add')">添加应用</el-button><el-button :disabled="!curMonCfg.id" @click="handleAppCommand('del')">删除</el-button></div>
   <textarea v-if="selCat==='logs'" readonly ref="logs" aria-label="调度日志" class="scheduler-logs" :value="logs" v-loading="logOnway"/>
   <el-table v-else :data="monitors" border stripe v-loading="loading" highlight-current-row @current-change="handleSelectApp" empty-text="尚无调度任务，点击添加应用">
    <el-table-column prop="id" label="进程" min-width="160" fixed="left" show-overflow-tooltip/>
    <el-table-column label="状态" width="90"><template #default="{row}"><span :class="row.running?'text-success':'text-muted'">{{row.running?'运行中':'已停止'}}</span></template></el-table-column>
    <el-table-column prop="guardstr" label="守护" width="60"/><el-table-column prop="task" label="定时" width="60"/>
    <el-table-column prop="path" label="执行程序" min-width="200" show-overflow-tooltip/><el-table-column prop="param" label="参数" min-width="170" show-overflow-tooltip/><el-table-column prop="folder" label="工作目录" min-width="200" show-overflow-tooltip/>
    <el-table-column label="进程操作" width="128" fixed="right"><template #default="{row}"><ProcessControl :id="row.id" :running="row.running" :show-state="false" @changed="queryData"/></template></el-table-column>
   </el-table>
  </section>
  <aside class="scheduler-config"><MoniforCfg :config="curMonCfg" :fixinfo="!addApp" :forapp="curMonCfg.type==0" @cfgudt="onCfgUpdated"/></aside>
 </div>
</template>

<script>
import MoniforCfg from '../common/moncfg.vue'
import ProcessControl from '../common/ProcessControl.vue'
export default {
    name: 'Schedule',
    components: {
        MoniforCfg,ProcessControl
    },
    data() {
        return {
            selCat:"list",
            showfolders:false,
            selfolder:"",
            loading: false,
            committing: false,
            processBusy: '',
            addApp: false,
            monitors:[],
            logs: "",
            logLines: 0,
            logOnway: false,
            curMonCfg:{
                "id": "",
                "folder": "",
                "path": "",
                "param": "",
                "type": 0,
                "span":3,
                "guard":false,
                "redirect":false,
                "schedule":{
                    "active": false,
                    "weekflag":"0111110",
                    "weekmask":[false, true, true, true, true, true, false],
                    "tasks":[
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        }
                    ]
                }
            }
        }
    },
    methods:{
        handleClickTab: function(tab, event){
            this.selCat = tab.props.name;
            if(tab.props.name == "logs")
                this.queryLogs();
        },
        onCfgUpdated: function(){
            this.queryData();
        },
        handleSelectApp: function(appInfo){
            if(!appInfo)return;
            let config = JSON.parse(JSON.stringify(appInfo));
            config.schedule.weekmask = [];
            for(let idx = 0; idx < config.schedule.weekflag.length; idx++){
                config.schedule.weekmask.push(config.schedule.weekflag[idx]=='1');
            }

            for(let idx = 0; idx < 6; idx++){
                let curTime = config.schedule.tasks[idx].time + '';
                if(curTime.length == 1)
                    config.schedule.tasks[idx].time = "00:0" + curTime;
                else if(curTime.length == 2)
                    config.schedule.tasks[idx].time = "00:" + curTime;
                else if(curTime.length == 3)
                    config.schedule.tasks[idx].time = '0' + curTime[0] + ":" + curTime.substr(1);
                else
                    config.schedule.tasks[idx].time = curTime.substr(0,2) + ":" + curTime.substr(2);
            }

            this.curMonCfg = config;
        },
        resetConf: function(){
            this.curMonCfg = {
                "id": "",
                "folder": "",
                "path": "",
                "param": "",
                "span":3,
                "guard":false,
                "redirect":false,
                "schedule":{
                    "active": false,
                    "weekflag":"0111110",
                    "weekmask":[false, true, true, true, true, true, false],
                    "tasks":[
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        },
                        {
                            "active":false,
                            "time":"00:00",
                            "action": 0
                        }
                    ]
                }
            };
        },
        queryLogs: function(){
            let self = this;

            self.logOnway = true;
            this.$api.getMonLogs((resObj)=>{
                if(resObj.result < 0){
                    this.$alert(resObj.message);
                } else {
                    this.logs = resObj.content;
                    this.logLines = resObj.lines||0;
                    self.$nextTick(()=>{
                        let height = self.$refs.logs?.scrollHeight||0;
                        self.$refs.logs?.scrollTo(0,height);
                    });
                }
                self.logOnway = false;
            });
        },
        queryData: function(quiet=false){
            if(!quiet)this.loading = true;
            this.$api.getMonApps((resObj)=>{
                if(resObj.result < 0)this.$message.error(resObj.message);
                else{
                    this.monitors=Object.values(resObj.schedules || {}).map(app=>({...app,state:app.running?"运行中":"未启动",guardstr:app.guard?"是":"否",task:app.schedule.active?"是":"否"}));
                    const selected=this.monitors.find(app=>app.id===this.curMonCfg.id);
                    if(selected)this.curMonCfg.running=selected.running;
                    else if(this.curMonCfg.id)this.resetConf();
                }
                this.loading = false;
            });
        },
        handleAppCommand: function(command){
            let self = this;
            if(command == 'add'){
                this.resetConf();
                this.addApp = true;
            } else if(command == "del"){
                if(!self.curMonCfg.id){self.$message.warning("请先选择调度任务");return;}
                this.$confirm('确定要删除该调度任务吗?', '调度管理', {
                    confirmButtonText: '确定',
                    cancelButtonText: '取消',
                    type: 'warning'
                }).then(() => {
                    this.$api.delApp(self.curMonCfg.id, (resObj)=>{
                        if(resObj.result < 0){
                            self.$alert(resObj.message);
                        } else {
                            let nextIdx = 0;
                            for(let i = 0; i < self.monitors.length; i++){
                                if(self.monitors[i].id == self.curMonCfg.id){
                                    self.monitors.splice(i, 1);
                                    nextIdx = i;
                                    break;
                                }
                            }

                        }
                    });
                }).catch(()=>{});
            } else if(command == 'refresh'){
                this.queryData();
            }
        }
    },
    mounted (){
        this.$nextTick(()=>{
            this.queryData();
            this.queryLogs();
        });
        this.poll=setInterval(()=>{if(!document.hidden && !this.processBusy)this.queryData(true)},5000);
    },
    beforeUnmount(){clearInterval(this.poll);}
}

</script>

<!-- Add "scoped" attribute to limit CSS to this component only -->
<style scoped>
.scheduler-workspace{height:100%;min-height:650px;display:grid;grid-template-columns:minmax(0,1.6fr) minmax(380px,1fr);overflow:auto}.scheduler-list{min-width:0;padding:12px 16px;display:flex;flex-direction:column;gap:12px}.scheduler-list>.el-table{flex:1;min-height:350px}.scheduler-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.scheduler-toolbar>.el-tabs{margin-right:auto}.scheduler-toolbar :deep(.el-tabs__header){margin:0}.scheduler-toolbar :deep(.el-tabs__content){display:none}.scheduler-config{border-left:1px solid var(--wt-line);overflow:auto}.scheduler-logs{flex:1;min-height:400px;resize:none;background:var(--wt-matte);color:var(--wt-text);border:1px solid var(--wt-line);padding:12px;font:11px/1.8 var(--wt-mono)}@media(max-width:1280px){.scheduler-workspace{grid-template-columns:minmax(0,1fr);height:auto}.scheduler-config{border:0;border-top:1px solid var(--wt-line)}.scheduler-list{min-height:440px}}
</style>
