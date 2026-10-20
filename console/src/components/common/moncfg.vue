<template>
    <div style="display:flex;flex-direction:column;margin:4px 20px;">
        <div style="padding-bottom: 12px;">
            <h4>调度设置</h4><p class="config-help">先选择进程，再修改运行路径与定时任务。</p><p v-for="error in validationErrors" :key="error" class="config-error">{{error}}</p>
        </div>
        <el-divider content-position="left">任务信息</el-divider>
        <div class="config-label">
            <el-row>
                <el-col :span="4">
                    <a>任务ID：</a>
                </el-col>
                <el-col :span="7">
                    <el-input size="small" :disabled="fixinfo" v-model="config.id" placeholder="例如 research_worker" :class="{'invalid-field':submitted&&!config.id?.trim()}"></el-input>
                </el-col>
                <el-col :span="4" :offset="2">
                    <a>启动参数：</a>
                </el-col>
                <el-col :span="7">
                    <el-input size="small" v-model="config.param" placeholder="run.py"></el-input>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="4">
                    <a>执行程序：</a>
                </el-col>
                <el-col :span="20">
                    <el-input size="small" v-model="config.path" placeholder="例如 /opt/wt-venv/bin/python" :class="{'invalid-field':submitted&&!config.path?.trim()}">
                        <template #append><el-tooltip popper-class="wt-explanation-tooltip" :enterable="false"  effect="dark" content="直接获取python路径" placement="top">
                            <el-button @click="onLinkPython"><i class="el-icon-link"></i></el-button>
                        </el-tooltip></template>
                    </el-input>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="4">
                    <a>工作目录：</a>
                </el-col>
                <el-col :span="20">
                    <el-input size="small" :disabled="fixinfo" v-model="config.folder" placeholder="例如 /workspace/live/demo/run" :class="{'invalid-field':submitted&&!config.folder?.trim()}">
                        <template #append><el-tooltip popper-class="wt-explanation-tooltip" :enterable="false"  effect="dark" content="选择组合所在的目录" placement="top">
                            <el-button :disabled="fixinfo" @click="handlePickFolder"><i class="el-icon-folder"></i></el-button>
                        </el-tooltip></template>
                    </el-input>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="4">
                    <a>消息地址：</a>
                </el-col>
                <el-col :span="20">
                    <el-input size="small" v-model="config.mqurl" placeholder="例如 ipc:///workspace/live/demo/events.ipc" :disabled="forapp"></el-input>
                </el-col>
            </el-row>
        </div>
        <el-divider content-position="left">监控设置</el-divider>
        <div class="config-row">
            <div style="flex:0;margin-top:6px;padding-right:16px;">
                <el-checkbox v-model="config.guard">进程守护</el-checkbox>
            </div>
            <div style="flex:0:display:inline">
                <span class="config-label">检测间隔</span>
                <el-input-number size="small" :min="1" :max="100" label="检测间隔" v-model="config.span"></el-input-number>
                <span class="config-label">s</span>
            </div>
            <div style="flex:1">
                
            </div>
            <div style="flex:0;margin-top:6px;">
                <el-checkbox v-model="config.schedule.active">计划任务</el-checkbox>
            </div>
        </div>
        <el-divider content-position="left">计划任务</el-divider>
        <div class="config-row">
            <div class="week-marker">
                <el-checkbox v-model="config.schedule.weekmask[0]" :disabled="!config.schedule.active">周日</el-checkbox>
            </div>
            <div class="week-marker">
                <el-checkbox v-model="config.schedule.weekmask[1]" :disabled="!config.schedule.active">周一</el-checkbox>
            </div>
            <div class="week-marker">
                <el-checkbox v-model="config.schedule.weekmask[2]" :disabled="!config.schedule.active">周二</el-checkbox>
            </div>
            <div class="week-marker">
                <el-checkbox v-model="config.schedule.weekmask[3]" :disabled="!config.schedule.active">周三</el-checkbox>
            </div>
            <div class="week-marker">
                <el-checkbox v-model="config.schedule.weekmask[4]" :disabled="!config.schedule.active">周四</el-checkbox>
            </div>
            <div class="week-marker">
                <el-checkbox v-model="config.schedule.weekmask[5]" :disabled="!config.schedule.active">周五</el-checkbox>
            </div>
            <div class="week-marker">
                <el-checkbox v-model="config.schedule.weekmask[6]" :disabled="!config.schedule.active">周六</el-checkbox>
            </div>
        </div>
        <el-row>
            <el-col :span="5">
                <el-checkbox v-model="config.schedule.tasks[0].active" :disabled="!config.schedule.active">任务一</el-checkbox>
            </el-col>
            <el-col :span="6">
                <el-input
                    placeholder="请输入时间"
                    suffix-icon="el-icon-time"
                    size="small"
                    type="time"
                    :disabled="!config.schedule.tasks[0].active || !config.schedule.active"
                    v-model="config.schedule.tasks[0].time">
                </el-input>
            </el-col>
            <el-col :span="5" :offset="1">
                <el-select v-model="config.schedule.tasks[0].action" placeholder="请选择" size="small" :disabled="!config.schedule.tasks[0].active || !config.schedule.active">
                    <el-option
                        v-for="(item,idx) in actions"
                        :key="idx"
                        :label="item"
                        :value="idx">
                    </el-option>
                </el-select>
            </el-col>
        </el-row>
        <el-row>
            <el-col :span="5">
                <el-checkbox v-model="config.schedule.tasks[1].active" :disabled="!config.schedule.active">任务二</el-checkbox>
            </el-col>
            <el-col :span="6">
                <el-input
                    placeholder="请输入时间"
                    suffix-icon="el-icon-time"
                    size="small"
                    type="time"
                    :disabled="!config.schedule.tasks[1].active || !config.schedule.active"
                    v-model="config.schedule.tasks[1].time">
                </el-input>
            </el-col>
            <el-col :span="5" :offset="1">
                <el-select v-model="config.schedule.tasks[1].action" placeholder="请选择" size="small" :disabled="!config.schedule.tasks[1].active || !config.schedule.active">
                    <el-option
                        v-for="(item,idx) in actions"
                        :key="idx"
                        :label="item"
                        :value="idx">
                    </el-option>
                </el-select>
            </el-col>
        </el-row>
        <el-row>
            <el-col :span="5">
                <el-checkbox v-model="config.schedule.tasks[2].active" :disabled="!config.schedule.active">任务三</el-checkbox>
            </el-col>
            <el-col :span="6">
                <el-input
                    placeholder="请输入时间"
                    suffix-icon="el-icon-time"
                    size="small"
                    type="time"
                    :disabled="!config.schedule.tasks[2].active || !config.schedule.active"
                    v-model="config.schedule.tasks[2].time">
                </el-input>
            </el-col>
            <el-col :span="5" :offset="1">
                <el-select v-model="config.schedule.tasks[2].action" placeholder="请选择" size="small" :disabled="!config.schedule.tasks[2].active || !config.schedule.active">
                    <el-option
                        v-for="(item,idx) in actions"
                        :key="idx"
                        :label="item"
                        :value="idx">
                    </el-option>
                </el-select>
            </el-col>
        </el-row>
        <el-row>
            <el-col :span="5">
                <el-checkbox v-model="config.schedule.tasks[3].active" :disabled="!config.schedule.active">任务四</el-checkbox>
            </el-col>
            <el-col :span="6">
                <el-input
                    placeholder="请输入时间"
                    suffix-icon="el-icon-time"
                    size="small"
                    type="time"
                    :disabled="!config.schedule.tasks[3].active || !config.schedule.active"
                    v-model="config.schedule.tasks[3].time">
                </el-input>
            </el-col>
            <el-col :span="5" :offset="1">
                <el-select v-model="config.schedule.tasks[3].action" placeholder="请选择" size="small" :disabled="!config.schedule.tasks[3].active || !config.schedule.active">
                    <el-option
                        v-for="(item,idx) in actions"
                        :key="idx"
                        :label="item"
                        :value="idx">
                    </el-option>
                </el-select>
            </el-col>
        </el-row>
        <el-row>
            <el-col :span="5">
                <el-checkbox v-model="config.schedule.tasks[4].active" :disabled="!config.schedule.active">任务五</el-checkbox>
            </el-col>
            <el-col :span="6">
                <el-input
                    placeholder="请输入时间"
                    suffix-icon="el-icon-time"
                    size="small"
                    type="time"
                    :disabled="!config.schedule.tasks[4].active || !config.schedule.active"
                    v-model="config.schedule.tasks[4].time">
                </el-input>
            </el-col>
            <el-col :span="5" :offset="1">
                <el-select v-model="config.schedule.tasks[4].action" placeholder="请选择" size="small" :disabled="!config.schedule.tasks[4].active || !config.schedule.active">
                    <el-option
                        v-for="(item,idx) in actions"
                        :key="idx"
                        :label="item"
                        :value="idx">
                    </el-option>
                </el-select>
            </el-col>
        </el-row>
        <el-row>
            <el-col :span="5">
                <el-checkbox v-model="config.schedule.tasks[5].active" :disabled="!config.schedule.active">任务六</el-checkbox>
            </el-col>
            <el-col :span="6">
                <el-input
                    placeholder="请输入时间"
                    suffix-icon="el-icon-time"
                    size="small"
                    type="time"
                    :disabled="!config.schedule.tasks[5].active || !config.schedule.active"
                    v-model="config.schedule.tasks[5].time">
                </el-input>
            </el-col>
            <el-col :span="5" :offset="1">
                <el-select v-model="config.schedule.tasks[5].action" placeholder="请选择" size="small" :disabled="!config.schedule.tasks[5].active || !config.schedule.active">
                    <el-option
                        v-for="(item,idx) in actions"
                        :key="idx"
                        :label="item"
                        :value="idx">
                    </el-option>
                </el-select>
            </el-col>
        </el-row>
        <div style="padding:8px 4px;">
            <div style="flex:1:display:inline;float:right">
                <el-button type="primary" size="small" style="float:right;" plain @click="onConfigCommit()">提交设置</el-button>
            </div>
        </div>
        <el-dialog
            title="选择目录"
            v-model="showfolders"
            width="25%">
            <div style="width:100%;height:300px;overflow:auto;border:1px solid #E4E7ED;">
                <el-tree :data="folders" @node-click="handleFolderClick"></el-tree>
            </div>
            <template #footer><span class="dialog-footer">
                <el-button @click="showfolders = false">取 消</el-button>
                <el-button type="primary" @click="handleFolderPicked" plain>确 定</el-button>
            </span></template>
        </el-dialog>
    </div>   
</template>

<script>
import { mapState } from 'pinia';
import { useSessionStore } from '@/store.js';

export default {
    name: 'schedule',
    computed: {
        ...mapState(useSessionStore, [
            'folders'
        ])
    },
    props:{
        fixinfo:{
            type:Boolean,
            default(){
                return false;
            }
        },
        forapp:{
            type:Boolean,
            default(){
                return false;
            }
        },
        config:{
            type:Object,
            default(){
                return {
                    "id": "",
                    "folder": "",
                    "path": "",
                    "param": "",
                    "mqurl":"",
                    "span":3,
                    "guard":false,
                    "redirect":false,
                    "schedule":{
                        "active": true,
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
        }
    },
    data () {
        return {
            showfolders:false,
            selfolder:"",
            actions:["启动","停止","重启"],submitted:false,originalConfig:null
        }
    },
    computed:{
        validationErrors(){if(!this.submitted)return [];return [['id','任务 ID'],['path','执行程序'],['folder','工作目录']].filter(([key])=>!this.config[key]?.trim()).map(([,label])=>label+'不能为空')},
    },
    watch:{config:{immediate:true,handler(value){this.originalConfig=JSON.parse(JSON.stringify(value));this.submitted=false;if(!value.path)this.onLinkPython(true)}}},
    methods: {
        onLinkPython: function(onlyEmpty=false){
            if(useSessionStore().cache.userinfo?.role==='guest'){if(!onlyEmpty)this.$message.warning('游客模式不支持操作');return;}
            const target=this.config;
            this.$api.getPythonPath((resObj)=>{
                if(resObj.result < 0){
                    this.$alert(resObj.message);
                } else {
                    if(onlyEmpty===true && (target!==this.config||this.config.path))return;
                    this.config.path = resObj.path;
                }
            })
        },
        handlePickFolder: function(e){
            if(useSessionStore().cache.userinfo?.role==='guest'){this.$message.warning('游客模式不支持操作');return;}
            let self = this;
            self.selfolder = "";
            if(self.folders.length == 0){
                this.$api.getFolders((resObj)=>{
                    if(resObj.result < 0){
                        self.$alert(resObj.message);
                    } else {
                        useSessionStore().setfolders({
                            folders: [resObj.tree]
                        });
                        self.showfolders = true;
                    }
                })
            } else {
                self.showfolders = true;
            }
        },
        handleFolderPicked: function(){
            let self = this;
            if(self.selfolder == ''){
                self.$alert("请选择目录");
            } else {
                self.config.folder = self.selfolder;
                self.showfolders = false;
            }
        },
        handleFolderClick: function(data){
            this.selfolder = data.path;
        },
        onConfigCommit: function(){
            if(useSessionStore().cache.userinfo?.role==='guest'){this.$message.warning('游客模式不支持操作');return;}
            let self = this;
            this.submitted=true;
            if(this.validationErrors.length)return;
            let config = JSON.parse(JSON.stringify(this.config));
            if(config.path.length == 0){
                this.$alert("执行程序路径不能为空");
                return;
            }

            if(config.id.length == 0){
                this.$alert("应用ID不能为空");
                return;
            }

            if(config.folder.length == 0){
                this.$alert("执行目录不能为空");
                return;
            }

            //转换日期掩码
            config.schedule.weekflag = "";
            for(let idx = 0; idx < 7; idx++){
                config.schedule.weekflag += config.schedule.weekmask[idx]?"1":"0";
            }

            //转换时间
            for(let idx = 0; idx < 6; idx++){
                let timestr = config.schedule.tasks[idx].time;
                timestr = timestr.replace(":","");
                config.schedule.tasks[idx].time = parseInt(timestr);
            }

            config.redirect = !self.forapp;
            config.isapp = self.forapp;

            const labels={path:'执行程序',param:'启动参数',folder:'工作目录',mqurl:'消息地址',guard:'进程守护',span:'检查间隔',schedule:'定时任务'};
            const changes=Object.entries(labels).filter(([key])=>JSON.stringify(this.originalConfig?.[key])!==JSON.stringify(this.config[key])).map(([key,label])=>label+'：'+(key==='schedule'?'任务计划有修改':String(this.originalConfig?.[key]??'空')+' → '+String(this.config[key]??'空')));
            this.$confirm('提交「'+config.id+'」的调度设置？\n'+(changes.join('\n')||'当前配置与已读取内容一致')+'\n开启守护或定时任务后，系统可自动启动进程。', '提交调度配置', {customClass:'config-confirm',
                confirmButtonText: '确定',
                cancelButtonText: '取消',
                type: 'warning'
            }).then(() => {
                delete config.schedule.weekmask;
                this.$api.commitMonCfg(config, (resObj)=>{
                    if(resObj.result < 0){
                        this.$message.error(resObj.message);
                    } else {
                        this.$message({
                            message: "监控配置已提交成功",
                            type:"success"
                        });

                        this.originalConfig=JSON.parse(JSON.stringify(this.config));
                        this.$emit('cfgudt');
                    }
                });
            }).catch(()=>{});
        }
    }
}
</script>

<!-- Add "scoped" attribute to limit CSS to this component only -->
<style scoped>
.week-marker{
    flex: 0;
    padding-right: 12px;
}

.config-row{
    display:flex;
    flex-direction:row;
    padding:8px 4px;
}

.el-row{
    padding: 8px 4px;
}

.task-item{
    width: 100%;
    flex: 1;
    display: inline;
}

.config-label{
    color:#606266;
    font-weight: 500;
    font-size: 14px;
}
.config-help{font-size:11px;color:var(--wt-muted);line-height:1.7}.config-error{font-size:11px;color:var(--wt-danger);margin:4px 0}.invalid-field :deep(.el-input__wrapper){box-shadow:0 0 0 1px var(--wt-danger) inset}.config-row{flex-wrap:wrap;gap:8px}.week-marker{padding-right:4px}.config-label{color:var(--wt-text)}
</style>
<style>.config-confirm .el-message-box__message{white-space:pre-line;font-size:12px}</style>
