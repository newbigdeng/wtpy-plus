<template>
    <div class="wt-monitor" :class="{'sidebar-drawer':narrow && sidebarOpen}">
        <aside v-show="sidebarOpen" :style="{width:sidebarWidth+'px',flexBasis:sidebarWidth+'px'}" id="wt-monitor-portfolios" class="wt-project-sidebar" aria-label="组合列表">
            <div class="wt-sidebar-heading"><span>组合</span><span class="wt-count">{{groups.length}}</span><button class="wt-icon-button wt-add-group" aria-label="添加组合" title="添加组合" @click="handleGrpCmd('addgrp')"><WtIcon name="plus"/></button></div>
            <div class="wt-project-search"><WtIcon name="search"/><input v-model="groupSearch" aria-label="搜索组合" placeholder="搜索组合名称或 ID"/></div>
            <div class="wt-sidebar-section"><span>全部组合</span><span>选择组合查看账簿</span></div>
            <nav class="wt-project-list" aria-label="选择组合">
                <button v-for="item in filteredGroups" :key="item.id" class="wt-project-item" :class="{selected:selectedIdx===item.id}" :aria-label="item.name" :aria-current="selectedIdx===item.id?'true':undefined" @click="handleTabSel(item.id)">
                    <WtIcon name="folder"/><span class="wt-project-name"><strong>{{item.name}}</strong><small>{{item.id}}</small><small class="wt-project-summary">{{groupSummaries[item.id]||'尚未读取资金快照'}}</small></span><span class="wt-state-dot" :class="{running:item.running}" :title="item.running?'进程运行中':'进程已停止'"></span>
                </button>
                <div v-if="!filteredGroups.length" class="wt-sidebar-empty">{{groups.length?'没有匹配的组合':'添加组合，开始监控'}}</div>
            </nav>

        </aside>
        <button v-if="narrow && sidebarOpen" class="wt-sidebar-mask" aria-label="关闭组合列表" @click="toggleSidebar"/>
        <ResizableDivider v-if="sidebarOpen && !narrow" v-model="sidebarWidth" :min="180" :max="420" storage-key="wt.monitor.sidebar" label="调整组合栏宽度"/>
        <section class="wt-monitor-workspace">
            <header class="wt-group-header">
                <button class="wt-icon-button wt-sidebar-toggle" aria-controls="wt-monitor-portfolios" :title="sidebarOpen?'隐藏整个组合列表，专注看盘':'显示组合列表'" :aria-label="sidebarOpen?'收起组合栏':'展开组合栏'" :aria-expanded="sidebarOpen" @click="toggleSidebar"><WtIcon :name="sidebarOpen?'fold':'expand'"/><span>{{sidebarOpen?'收起组合栏':'展开组合栏'}}</span></button>
                <div class="wt-group-title"><h1>{{curGroup?curGroup.name:'监控中心'}}</h1><div class="wt-group-meta" v-if="curGroup"><span>{{curGroup.gtype?.toUpperCase()}} 组合</span><span>·</span><span>{{curGroup.datmod==='auto'?'实时推送':'文件读取'}}</span></div></div>
                <div class="wt-group-actions" v-if="curGroup">
                    <ProcessControl :id="curGroup.id" :name="curGroup.name" :running="curGroup.running" kind="group" :disabled="!canOperate" @changed="refreshProcesses"/>
                    <el-button v-if="isAdmin" size="small" @click="$refs.group.openConfig()">配置</el-button><el-button size="small" @click="handleViewGrop">组合信息</el-button>
                    <el-button v-if="isAdmin" size="small" @click="handleClickSchedule">调度</el-button>
                    <el-dropdown trigger="click" @command="handleGrpCmd"><el-button size="small" aria-label="组合管理">管理<WtIcon name="down"/></el-button><template #dropdown><el-dropdown-menu>
                        <el-dropdown-item command="addgrp">添加组合</el-dropdown-item>
                        <el-dropdown-item v-if="isAdmin" command="renamegrp">重命名组合</el-dropdown-item>
                        <el-dropdown-item command="delgrp">删除组合</el-dropdown-item>
                    </el-dropdown-menu></template></el-dropdown>
                </div>
            </header>
            <div class="wt-group-content"><Empty v-if="!curGroup" @create="handleGrpCmd('addgrp')"/><Group v-else ref="group" :key="curGroup.id+':'+profileVersion" :groupinfo="curGroup" @profile-changed="handleProfileChanged" @overview="updateOverview"/></div>
        </section>
        <el-drawer
            title="计划任务"
            :with-header="false"
            v-model="schedule"
            direction="rtl"
            size="500px">
            <Schedule :config="curMonCfg" :fixinfo="true"/>
        </el-drawer>
        <el-dialog
            title="选择目录"
            v-model="showfolders"
            width="min(560px, 92vw)">
            <div style="width:100%;height:300px;overflow:auto;border:1px solid #E4E7ED;">
                <el-tree :data="folders" @node-click="handleFolderClick"></el-tree>
            </div>
            <template #footer><span class="dialog-footer">
                <el-button @click="showfolders = false">取 消</el-button>
                <el-button type="primary" @click="handleFolderPicked" plain>确 定</el-button>
            </span></template>
        </el-dialog>
        <el-dialog
            :title="addGroup?'添加组合':'修改组合'"
            v-model="showgrpdlg"
            width="min(560px, 92vw)"
            class="dialog-group"
            :before-close="onCloseGrpDlg">  
            <el-row>
                <el-col :span="6">
                    <a>组合ID：</a>
                </el-col>
                <el-col :span="18">
                    <el-input v-model="copyGroup.id" size="small" :disabled="!editGroup"></el-input>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="6">
                    <a>组合名称：</a>
                </el-col>
                <el-col :span="18">
                    <el-input v-model="copyGroup.name" size="small" :disabled="!editGroup"></el-input>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="6">
                    <a>组合路径：</a>
                </el-col>
                <el-col :span="18">
                    <el-input v-model="copyGroup.path" size="small" :disabled="!editGroup">
                        <template #append><el-tooltip popper-class="wt-explanation-tooltip" :enterable="false"  effect="dark" content="选择组合所在的目录" placement="top">
                            <el-button @click="handlePickFolder" :disabled="!editGroup"><i class="el-icon-folder"></i></el-button>
                        </el-tooltip></template>
                    </el-input>
                 </el-col>
            </el-row>
            <el-row>
                <el-col :span="6">
                    <a>组合类型：</a>
                </el-col>
                <el-col :span="18">
                    <el-radio v-model="copyGroup.gtype" value="cta" :disabled="!editGroup">CTA组合</el-radio>
                    <el-radio v-model="copyGroup.gtype" value="hft" :disabled="!editGroup">HFT组合</el-radio>
                    <el-radio v-model="copyGroup.gtype" value="sel" :disabled="!editGroup">SEL组合</el-radio>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="6">
                    <a>数据模式：</a>
                </el-col>
                <el-col :span="18">
                    <el-tooltip popper-class="wt-explanation-tooltip" :enterable="false" class="item" effect="dark" content="组合会自动向监控服务推送数据" placement="top">
                        <el-radio v-model="copyGroup.datmod" value="auto" :disabled="!editGroup">自动</el-radio>
                    </el-tooltip>
                    <el-tooltip popper-class="wt-explanation-tooltip" :enterable="false" class="item" effect="dark" content="监控服务只在有请求的时候去读取数据" placement="top">
                        <el-radio v-model="copyGroup.datmod" value="mannual" :disabled="!editGroup">手动</el-radio>
                    </el-tooltip>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="6">
                    <a>消息地址：</a>
                </el-col>
                <el-col :span="18">
                    <el-tooltip popper-class="wt-explanation-tooltip" :enterable="false" class="item" effect="dark" content="消息订阅地址要和配置文件中的notifier一致" placement="top">
                        <el-input v-model="copyGroup.mqurl" size="small" :disabled="!editGroup || copyGroup.datmod!='auto'" placeholder="请输入消息队列的订阅地址"></el-input>
                    </el-tooltip>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="6">
                    <a>组合环境：</a>
                </el-col>
                <el-col :span="18">
                    <el-radio v-model="copyGroup.env" value="product" :disabled="!editGroup">生产环境</el-radio>
                    <el-radio v-model="copyGroup.env" value="backtest" :disabled="!editGroup">回测环境</el-radio>
                </el-col>
            </el-row>
            <el-row style="height:60px;">
                <el-col :span="6">
                    <a>组合介绍：</a>
                </el-col>
                <el-col :span="18">
                    <el-input type="textarea" v-model="copyGroup.info" style="min-height:80px;" :disabled="!editGroup"></el-input>
                </el-col>
            </el-row>
            <template #footer><span class="dialog-footer">
                <el-button type="primary" plain size="small" v-show="!addGroup && !editGroup" @click="editGroup=true"><i class="el-icon-edit"></i>编辑</el-button>
                <el-button type="primary" @click="onCommitGroup()" plain size="small" ><i class="el-icon-thumb"></i>提交</el-button>
            </span></template>
        </el-dialog>

    </div>
</template>

<script>
import { mapState } from 'pinia';
import { useSessionStore } from '@/store.js';

import WtIcon from '../common/WtIcon.vue'
import ProcessControl from '../common/ProcessControl.vue'
import ResizableDivider from '../common/ResizableDivider.vue'
import Group from './group.vue'
import Empty from './empty.vue'
import Schedule from '../common/moncfg.vue'

export default {
    name: 'monitor',
    computed: {
        canOperate(){return ['operator','trader','admin','superman'].includes(this.cache.userinfo?.role)},
        ...mapState(useSessionStore, [
            'folders',
            'cache'
        ]),
        filteredGroups() {
            const query=this.groupSearch.trim().toLowerCase()
            return this.groups.filter(group=>(group.name+' '+group.id).toLowerCase().includes(query))
        },
        runningCount() { return this.groups.filter(group=>group.running).length },
        selectionKey() { return 'wtpy.monitor.selected.'+this.cache.loginid },
        grpname(){
            if(this.curGroup == null)
                return "组合基本信息";
            else
                return this.curGroup.name;
        },
        isAdmin(){
            let uInfo = this.cache.userinfo;
            if(uInfo)
                return (uInfo.role == 'admin' || uInfo.role == 'superman');
            else
                return false;        
        }
    },
    components: {
        Group, Empty, Schedule, WtIcon, ResizableDivider, ProcessControl
    },
    data () {
        return {
            groups:[
            ],
            selectedIdx: "",
            groupSearch: "",
            sidebarOpen: true,
            narrow:window.innerWidth<=1360,
            profileVersion: 0,
            groupSummaries:{},
            sidebarWidth: 248,
            schedule: false,
            baseinfo: false,
            showgrpdlg: false,
            curGroup: null,
            addGroup: false,
            editGroup: false,
            copyGroup:{
                id:"",
                name:"",
                path:"",
                info:"",
                gtype:"cta",
                datmod:"mannual",
                env:"product",
                mqurl:""
            },
            showfolders:false,
            selfolder:"",
            monitors:{},
            curMonCfg:{
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
            }
        }
    },
    methods: {
        updateOverview(summary){this.groupSummaries[summary.groupid]=summary.text},
        onViewport(){const narrow=window.innerWidth<=1360;if(narrow&&!this.narrow)this.sidebarOpen=false;this.narrow=narrow;},
        refreshProcesses(){this.$api.getGroups(result=>{if(result.result<0){this.$message.error(result.message);return;}for(const group of this.groups){const current=result.groups?.find(row=>row.id===group.id);if(current)Object.assign(group,current)}})},
        handleProfileChanged(){const selected=this.curGroup?.id;this.$api.getGroups(result=>{if(result.result<0){this.$message.error(result.message);return;}this.groups=result.groups||[];this.handleTabSel(selected);this.profileVersion++;})},
        toggleSidebar(){this.sidebarOpen=!this.sidebarOpen;localStorage.setItem('wt.monitor.sidebarOpen.'+this.cache.loginid,String(this.sidebarOpen));this.$nextTick(()=>window.dispatchEvent(new Event('resize')))},
        async renameGroup(){if(!this.curGroup||!this.isAdmin)return;try{const {value}=await this.$prompt('只更改显示名称；组合ID、配置路径和账簿不变。','重命名组合',{inputValue:this.curGroup.name,inputValidator:value=>Boolean(value?.trim())||'名称不能为空',confirmButtonText:'保存',cancelButtonText:'取消'});const group={...this.curGroup,name:value.trim()};this.$api.commitGroup(group,'mod',result=>{if(result.result<0)this.$message.error(result.message);else{Object.assign(this.curGroup,group);this.$message.success('组合名称已更新')}})}catch(error){if(error instanceof Error)this.$message.error(error.message)}},
        handlePickFolder: function(e){
            let self = this;
            self.selfolder = "";
            if(self.folders.length == 0){
                this.$api.getFolders((resObj)=>{
                    if(resObj.result < 0){
                        self.$notify.error('查询目录结构出错：' + resObj.message);
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
                self.copyGroup.path = self.selfolder;
                self.showfolders = false;
            }
        },
        handleFolderClick: function(data){
            this.selfolder = data.path;
        },
        handleTabSel(key) {
            const group=this.groups.find(item=>item.id===key)
            if(!group) return
            this.selectedIdx=key
            this.curGroup=group
            localStorage.setItem(this.selectionKey,key)
            if(this.$route.query.group!==key)this.$router.replace({query:{...this.$route.query,group:key}})
            if(this.narrow)this.sidebarOpen=false
        },
        handleClickSchedule: function(){
            let self = this;
            let grpid = self.curGroup.id;
            if(!self.monitors[grpid]){
                self.$api.getMonCfg(grpid, (resObj)=>{
                    if(resObj.result < 0){
                        self.$notify.error(resObj.message);
                    } else if(resObj.config) {
                        let config = resObj.config;
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
                        self.monitors[grpid] = config;
                        self.curMonCfg = config;
                        self.schedule = true;
                    } else {
                        //新建调度任务
                        this.$confirm('该组合尚未配置自动调度，马上去配置?', '自动调度', {
                            confirmButtonText: '确定',
                            cancelButtonText: '取消',
                            type: 'warning'
                        }).then(() => {
                            let config = {
                                "id": "",
                                "folder": "",
                                "path": "",
                                "param": "",
                                "span":3,
                                "guard":false,
                                "redirect":false,
                                "mqurl":"",
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
                            config.id = self.curGroup.id;
                            config.folder = self.curGroup.path;
                            config.redirect = true;
                            config.mqurl = self.curGroup.mqurl;
                            self.curMonCfg = config;
                            self.schedule = true;
                        });
                    }
                })
            } else {
                self.curMonCfg = self.monitors[grpid];
                self.schedule = true;
            }
        },
        handleGrpCmd: function(cmdid){
            let self = this;
            if(cmdid == "renamegrp"){this.renameGroup();return;}
            if(cmdid == "addgrp"){
                this.copyGroup = {
                    id:"",
                    name:"",
                    path:"",
                    info:"",
                    gtype:"cta",
                    datmod:"mannual",
                    env:"product",
                    mqurl:""
                };
                this.addGroup = true;
                this.showgrpdlg = true;
                this.editGroup = true;
            } else if(cmdid == "delgrp"){
                this.addGroup = false;
                this.showgrpdlg = false;

                let grpInfo = self.curGroup;

                this.$confirm('确定要删除该组合?', '组合管理', {
                    confirmButtonText: '确定',
                    cancelButtonText: '取消',
                    type: 'warning'
                }).then(() => {
                    this.$api.delGroup(grpInfo.id, (resObj)=>{
                        if(resObj.result < 0){
                            self.$notify.error('删除组合失败：' + resObj.message);
                        } else {
                            let nextIdx = 0;
                            for(let i = 0; i < self.groups.length; i++){
                                if(self.groups[i].id == grpInfo.id){
                                    self.groups.splice(i, 1);
                                    nextIdx = i;
                                    break;
                                }
                            }
                            self.curGroup=null;
                            self.selectedIdx="";
                            if(self.groups.length) self.handleTabSel(self.groups[Math.min(nextIdx,self.groups.length-1)].id);
                            else { localStorage.removeItem(self.selectionKey); self.$push.subscribe(""); }
                        }
                    });
                }).catch(()=>{});
                
            }
        },
        onCommitGroup: function(){
            let self = this;
            let grpInfo = this.copyGroup;
            this.$api.commitGroup(grpInfo, this.addGroup?"add":"mod", (resObj)=>{
                if(resObj.result < 0){
                    self.$notify.error('提交组合信息失败：' + resObj.message);
                } else {
                    if(this.addGroup)
                        self.groups.push(grpInfo);
                    else {
                        const current=self.groups.find(group=>group.id===grpInfo.id);
                        if(current) Object.assign(current,grpInfo);
                    }
                    self.showgrpdlg = false;
                    if(self.curGroup == null){
                        self.handleTabSel(grpInfo.id);
                    }                    
                }
            });
        },
        onCloseGrpDlg: function(done){
            done();
        },
        handleViewGrop: function(){
            if(this.curGroup == null || this.curGroup.id == "")
                return;

            this.copyGroup = JSON.parse(JSON.stringify(this.curGroup));
            this.copyGroup.datmod = this.copyGroup.datmod || "mannual";

            this.addGroup = false;
            this.editGroup = false;
            this.showgrpdlg = true;            
        }
    },

    watch:{'$route.query.group'(id){if(id && id!==this.selectedIdx)this.handleTabSel(id)}},
    beforeUnmount(){window.removeEventListener('resize',this.onViewport)},
    mounted() {
        this.sidebarOpen=!this.narrow && localStorage.getItem('wt.monitor.sidebarOpen.'+this.cache.loginid)!=='false';
        window.addEventListener('resize',this.onViewport);
        let self = this;
        self.$nextTick(()=>{
            self.$api.getGroups((resObj) => {
                //console.log(resObj);
                if (resObj.result < 0) {
                    self.$notify.error("查询组合出错：" + resObj.message);
                } else {
                    self.groups = resObj.groups;
                    if(self.groups.length > 0){
                        const previous=self.$route.query.group || localStorage.getItem(self.selectionKey)
                        self.handleTabSel(self.groups.some(group=>group.id===previous)?previous:self.groups[0].id);
                    }

                    self._unsubscribePush = self.$push.onMessage((data) => {
                        if(data.type === 'gpevt') {
                            const affected=self.groups.find(group=>group.id===data.groupid);
                            if(affected && ['start','stop'].includes(data.evttype)) affected.running=data.evttype==='start';
                        }
                        if(data.type == 'gpevt'){
                            if(data.evttype == "start"){
                                setTimeout(()=>{
                                    self.$notify({message:"组合"+data.groupid+"已启动",type:"success"});
                                }, 300);
                                
                            } else if(data.evttype == "stop") {
                                setTimeout(()=>{
                                    self.$notify.error("组合"+data.groupid+"已停止");
                                }, 300);
                            }
                        }

                        if(data.type == 'chnlevt'){
                            let obj = null;
                            let evttype = data.evttype || "";

                            let thisGrp = null;
                            self.groups.forEach((grpInfo)=>{
                                if(grpInfo.id == data.groupid)
                                    thisGrp = grpInfo;
                            });

                            if(thisGrp != null){
                                if(evttype == 'notify'){
                                    obj = {
                                        time:new Date(),
                                        group: thisGrp.name,
                                        channel: data.channel,
                                        title:"事件通知",
                                        type:"error",
                                        message: "错误：{1}".format(data.channel,data.message)
                                    };
                                } else if(evttype == 'order'){
                                    if(data.data.canceled){
                                        obj = {
                                            time:new Date(),
                                            group: thisGrp.name,
                                            channel: data.channel,
                                            title:"订单回报",
                                            type:"danger",
                                            message: "订单已撤销，本地订单号：{1}".format(data.channel,data.data.localid)
                                        };
                                    }
                                    
                                } else if(evttype == 'trade'){
                                    let action = (data.data.isopen?"开":"平") + (data.data.islong?"多":"空");

                                    obj = {
                                        time:new Date(),
                                        group: thisGrp.name,
                                        channel: data.channel,
                                        title:"成交回报",
                                        type:"success",
                                        message: "操作：{1}，代码：{2}，数量：{3}，成交价：{4}，本地订单号：{5}".format(
                                            data.channel, action, data.data.code, data.data.volume, data.data.price, data.data.localid)
                                    };
                                }

                                if(obj != null){
                                    self.$emit("notify", obj);
                                }
                            }
                        }

                        if(self.curGroup == null || data.groupid != self.curGroup.id)
                            return;

                        if(data.type == 'gplog' || data.type == 'chnlevt'){
                            if(self.$refs.group)
                                self.$refs.group.receiveNotify(data);
                        } else if(data.type == 'gpevt') {
                            if(data.evttype == "start"){
                                self.curGroup.running = true;
                            } else if(data.evttype == "stop") {
                                self.curGroup.running = false;
                            }
                        }
                    }); 
                }
            });
        })
    }
}
</script>


<style scoped>
.wt-sidebar-mask{position:absolute;inset:0;border:0;background:#0004;z-index:7}.sidebar-drawer .wt-project-sidebar{position:absolute;top:0;bottom:0;left:0;z-index:8;max-width:85vw}.wt-monitor{position:relative;height:100%;width:100%;display:flex;min-height:0;overflow:hidden;}
.wt-project-sidebar{width:248px;flex:0 0 248px;background:var(--wt-sidebar);border-right:1px solid var(--wt-line);display:flex;flex-direction:column;padding:22px 12px 12px;box-sizing:border-box;min-height:0;}
.wt-sidebar-heading{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600;padding:0 8px;}
.wt-count{font-size:11px;font-weight:400;color:var(--wt-muted);background:var(--wt-hover);padding:1px 6px;border-radius:5px;}
.wt-add-group{margin-left:auto;}
.wt-project-search{display:flex;align-items:center;gap:7px;background:var(--wt-surface);border:1px solid var(--wt-line);border-radius:8px;margin:18px 4px 22px;padding:8px 10px;color:var(--wt-muted);}
.wt-project-search .wt-icon{width:15px;height:15px;}
.wt-project-search input{width:100%;min-width:0;border:0;outline:none;font:inherit;font-size:12px;background:none;color:var(--wt-text);}
.wt-project-search:focus-within{border-color:var(--wt-muted);box-shadow:0 0 0 2px transparent;}
.wt-project-search input::placeholder{color:var(--wt-muted);}
.wt-sidebar-section{display:flex;justify-content:space-between;color:var(--wt-muted);font-size:10px;padding:0 9px 10px;}
.wt-project-list{flex:1;min-height:0;overflow:auto;display:flex;flex-direction:column;gap:4px;}
.wt-project-item{display:flex;gap:10px;align-items:center;border:1px solid transparent;background:transparent;text-align:left;padding:12px 10px;border-radius:8px;cursor:pointer;color:var(--wt-muted);min-width:0;flex-shrink:0;font:inherit;}
.wt-project-item:hover{background:var(--wt-hover);}
.wt-project-item.selected{background:var(--wt-selected);color:var(--wt-text);border-color:var(--wt-line);}
.wt-project-item>.wt-icon{width:18px;height:18px;}
.wt-project-name{display:flex;flex-direction:column;gap:5px;min-width:0;flex:1;}
.wt-project-name strong{font-size:12px;font-weight:550;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--wt-text);}
.wt-project-name small{font-size:10px;color:var(--wt-muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-family:var(--wt-mono);}
.wt-sidebar-empty{font-size:12px;color:var(--wt-muted);padding:24px 8px;text-align:center;}
.wt-sidebar-foot{display:flex;align-items:center;gap:7px;padding:14px 8px 4px;color:var(--wt-muted);font-size:10px;border-top:1px solid var(--wt-line);}
.wt-monitor-workspace{flex:1;min-width:0;min-height:0;display:flex;flex-direction:column;overflow:hidden;}
.wt-group-header{min-height:92px;flex:0 0 auto;display:flex;align-items:center;gap:10px;padding:18px 24px;box-sizing:border-box;border-bottom:1px solid var(--wt-line);}
.wt-sidebar-toggle{width:auto;gap:6px;padding:7px;flex:0 0 auto;}.wt-sidebar-toggle span{font-size:10px;white-space:nowrap;}
.wt-group-title{min-width:0;}.wt-group-title h1{font-size:18px;line-height:26px;font-weight:550;margin:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.wt-group-meta{display:flex;gap:8px;align-items:center;color:var(--wt-muted);font-size:11px;margin-top:5px;}
.wt-group-actions{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-left:auto;flex-shrink:0;}
.wt-group-actions .el-button+.el-button{margin-left:0;}.wt-group-actions .wt-icon{width:12px;height:12px;margin-left:7px;}
.wt-process-state{font-size:11px;color:var(--wt-muted);display:flex;align-items:center;gap:6px;margin-right:8px;white-space:nowrap;}
.wt-process-state.running{color:var(--wt-success);}
.wt-group-content{flex:1;min-height:0;overflow-y:auto;overflow-x:hidden;padding:0 24px;}
.wt-stop-button{color:var(--wt-danger);border-color:var(--wt-line);background:var(--wt-surface);}
.dialog-group .el-row{margin:4px 8px;padding:4px 0;align-items:center;min-height:36px;}
@media(max-width:1200px){.wt-project-sidebar{width:218px;flex-basis:218px;}.wt-group-header{padding:16px;flex-wrap:wrap;}.wt-group-content{padding:0 16px;}.wt-group-actions{gap:6px;}.wt-process-state{margin-right:0;}}
@media(max-width:900px){.wt-project-sidebar{width:190px;flex-basis:190px;padding-left:6px;padding-right:6px;}.wt-group-header{gap:8px;min-height:82px;}.wt-group-title h1{font-size:16px;}.wt-group-actions{width:100%;justify-content:flex-end;}.wt-process-state{margin-right:auto;}.wt-group-content{padding:0 12px;}}
.wt-project-summary{font-size:9px!important;line-height:1.6;color:var(--wt-muted);max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
</style>
