<template>
    <div id="index" class="wt-shell" :class="{'rail-expanded':railExpanded}" v-loading="loading">
        <aside class="wt-rail" aria-label="工作区导航">
            <button class="wt-brand" aria-label="展开或收起导航" :aria-expanded="railExpanded" @click="toggleRail">W<span>+</span></button><span v-if="railExpanded" class="wt-brand-name">WonderTrader</span>
            <nav class="wt-rail-nav">
                <el-tooltip v-for="item in navItems" :key="item.path" :content="item.label" placement="right" :enterable="false">
                    <router-link :to="item.path" class="wt-rail-link" :class="{active:$route.path===item.path}" :aria-label="item.label" :aria-current="$route.path===item.path?'page':undefined">
                        <WtIcon :name="item.icon"/><span class="wt-rail-label">{{item.label}}</span>
                    </router-link>
                </el-tooltip>
            </nav>
            <div class="wt-rail-bottom"><el-tooltip :content="railExpanded?'收起导航':'展开导航'" placement="right"><button class="wt-icon-button" :aria-label="railExpanded?'收起导航':'展开导航'" @click="toggleRail"><WtIcon :name="railExpanded?'fold':'expand'"/></button></el-tooltip>
            </div>
        </aside>
        <div class="wt-shell-body">
            <header class="wt-topbar">
                <div class="wt-breadcrumb"><span>工作台</span><span class="wt-slash">/</span><strong>{{currentPage.label}}</strong></div>
                <span class="wt-topbar-hint">{{currentPage.hint}}</span>
                <div class="wt-topbar-actions"><button class="wt-command-button" aria-label="打开命令面板" @click="openCommands">搜索 / 跳转 <kbd>Ctrl K</kbd></button><button class="wt-notification-button" aria-label="查看事件通知" @click="onClickScroller"><WtIcon name="bell"/><span>{{notifies.length}}</span></button><span class="wt-push-status" :class="{connected:pushConnected}" role="status"><span class="wt-state-dot"></span>{{pushConnected?'推送已连接':'推送未连接'}}</span><button class="wt-theme-toggle" :aria-label="theme==='dark'?'切换白色主题':'切换黑色主题'" @click="toggleTheme"><WtIcon :name="theme==='dark'?'sun':'moon'"/><span>{{theme==='dark'?'浅色':'深色'}}</span></button>                <el-dropdown trigger="click" placement="bottom-end" @command="handleAccount">
                    <button class="wt-avatar" aria-label="账户菜单">{{(cache.userinfo.name || cache.loginid || 'W').slice(0,1).toUpperCase()}}</button>
                    <template #dropdown><el-dropdown-menu class="wt-account-menu">
                        <div class="wt-account-info"><strong>{{cache.userinfo.name || cache.loginid}}</strong><span>{{cache.loginid}} · {{roleName}}</span><small>{{cache.userinfo.logintime}} {{cache.userinfo.loginip}}</small></div>
                        <el-dropdown-item v-if="isAdmin || isGuest" command="admins">用户管理</el-dropdown-item><el-dropdown-item command="config">组合配置</el-dropdown-item><el-dropdown-item command="concepts">概念说明</el-dropdown-item><el-dropdown-item command="theme-light" divided>白色主题</el-dropdown-item><el-dropdown-item command="theme-dark">黑色主题</el-dropdown-item><el-dropdown-item command="theme-system">跟随系统主题</el-dropdown-item><el-dropdown-item command="password" divided><WtIcon name="settings"/>修改密码</el-dropdown-item>
                        <el-dropdown-item command="logout" divided><WtIcon name="logout"/>注销登录</el-dropdown-item>
                    </el-dropdown-menu></template>
                </el-dropdown>
</div>
            </header>
            <main class="wt-workspace" :class="'workspace-'+$route.path.slice(1)">
                <router-view @notify="handleNotify"/>
            </main>

        </div>
        <el-dialog v-model="showCommands" title="跳转与操作" width="min(680px, 94vw)" @opened="$refs.commandInput?.focus()">
            <el-input ref="commandInput" v-model="commandQuery" clearable placeholder="搜索页面或组合，Esc 关闭" aria-label="搜索命令"/>
            <div class="wt-commands"><button v-for="item in commandPages" :key="item.path" class="wt-command-row" @click="runPage(item.path)">{{item.label}}<span>跳转</span></button>
                <div v-for="group in filteredCommandGroups" :key="group.id" class="wt-command-group"><button class="wt-command-row" @click="runPage('/monitor',group.id)">{{group.name}}<small>{{group.id}}</small></button><ProcessControl v-if="canOperate" :id="group.id" :name="group.name" :running="group.running" kind="group" @changed="loadCommandGroups"/></div>
                <button class="wt-command-row" @click="toggleTheme">{{theme==='dark'?'切换白色主题':'切换黑色主题'}}</button>
                <button class="wt-command-row" @click="showConcepts=true;showCommands=false">查看概念说明</button>
            </div>
        </el-dialog>
        <el-drawer v-model="showConcepts" title="工作台里的这些词" size="min(480px, 94vw)">
            <dl class="wt-concepts"><template v-for="item in concepts" :key="item[0]"><dt>{{item[0]}}</dt><dd>{{item[1]}}</dd></template></dl>
        </el-drawer>
        <el-dialog
            title="修改密码"
            v-model="showDlgModPwd"
            width="360px">
            <el-row style="margin:8px 0;">
                <el-input placeholder="请输入旧密码" v-model="oldpwd" show-password></el-input>
            </el-row>
            <el-row style="margin:8px 0;">
                <el-input placeholder="请输入新密码" v-model="newpwd" show-password></el-input>
            </el-row>
            <el-row style="margin:8px 0;">
                <el-input placeholder="请确认新密码" v-model="confirmpwd" show-password></el-input>
            </el-row>
            <template #footer><span class="dialog-footer">
                <el-button type="primary" @click="doModPwd()" plain>确 定</el-button>
            </span></template>
        </el-dialog>
        <el-dialog
            title="通知列表"
            v-model="showNotifies"
            direction="btt"
            width="800px">
            <div style="height:400px;overflow-y:auto;border:solid 1px var(--wt-line);padding:4px;">
                <el-table
                    :data="notifies"
                    stripe
                    :show-header="false">
                    <el-table-column>
                        <template #default="scope">
                            <i :class="getIconStyle(scope.row.title)" style="padding-right:4px;"></i>
                            <span class="time">{{scope.row.time.format("hh:mm:ss")}}</span>
                            <span class="group">{{scope.row.group}}</span>
                            <span class="channel">{{scope.row.channel}}</span>
                            <span :class="getTitleStyle(scope.row.title)">{{scope.row.title}}</span>
                            <span class="message">{{scope.row.message}}</span>
                        </template>
                    </el-table-column>
                </el-table>
            </div>
        </el-dialog>
    </div>
</template>

<script>
import WtIcon from './common/WtIcon.vue';
import ProcessControl from './common/ProcessControl.vue';
import { themeState, setTheme } from '@/js/theme.js';
import { pushState } from "@/js/push.js";
import { mapState } from 'pinia';
import { useSessionStore } from '@/store.js';

export default {
    name: 'index',
    computed: {
        commandPages(){return this.navItems.filter(item=>item.label.includes(this.commandQuery))},
        filteredCommandGroups(){return this.commandGroups.filter(item=>(item.name+' '+item.id).toLowerCase().includes(this.commandQuery.toLowerCase()))},
        theme(){return themeState.value},
        canOperate(){return ['operator','trader','admin','superman'].includes(this.cache.userinfo?.role)},
        isGuest(){return this.cache.userinfo?.role==='guest'},
        roleName(){return {guest:'游客 · 只读',viewer:'只读',operator:'操作员',trader:'交易员',risker:'风控员',researcher:'研究员',admin:'管理员',superman:'超级管理员'}[this.cache.userinfo?.role]||'用户'},
        navItems() {
            return [
                {path:'/monitor',icon:'monitor',label:'监控中心',hint:'组合、通道与实时事件',show:this.isGuest || !this.isResearcher || this.isAdmin},
                {path:'/schedule',icon:'schedule',label:'调度中心',hint:'进程与计划任务',show:this.isGuest || this.isAdmin},
                {path:'/backtest',icon:'backtest',label:'在线回测',hint:'策略研究与历史检验',show:this.isGuest || this.isResearcher || this.isAdmin},
                {path:'/strategies',icon:'book',label:'策略管理',hint:'策略库、版本与源码',show:true},
                {path:'/history',icon:'folder',label:'历史数据',hint:'数据覆盖、导入与清理',show:true},
                {path:'/risk-library',icon:'settings',label:'风控管理',hint:'可组合的风控规则',show:true},
            ].filter(item=>item.show)
        },
        currentPage() { return this.navItems.find(item=>item.path===this.$route.path) || (this.$route.path==='/admins'?{label:'用户管理',hint:'账户与访问权限'}:null) || (this.$route.path==='/api-manual'?{label:'API 手册',hint:'当前版本 Python 策略接口'}:{label:'工作台',hint:''}) },
        pushConnected() { return pushState.connected },
        ...mapState(useSessionStore, [
            'cache'
        ]),
        isAdmin: function(){
            let uInfo = this.cache.userinfo;
            if(uInfo)
                return (uInfo.role == 'admin' || uInfo.role == 'superman');
            else
                return false;        
        },
        isResearcher: function(){
            let uInfo = this.cache.userinfo;
            if(uInfo)
                return (['researcher','admin','superman'].includes(uInfo.role));
            else
                return false;        
        }
    },
    components:{ WtIcon,ProcessControl },
    data () {
        return {
           showConcepts:false,showCommands:false,commandQuery:'',commandGroups:[],
           concepts:[['组合','一起运行的一组策略。组合把各策略目标合并，再交给执行器。'],['策略理论持仓','策略想持有的数量。它可能尚未成交，与账户实际持仓不同。'],['交易通道','连接柜台的适配器。委托、成交、账户资金由它回报。'],['通道环境','柜台地址、账号、数据和运行目录的一套独立配置。切换前须停止组合。'],['引擎账簿','WT 记录的策略和组合损益。它不是柜台资金证明。'],['柜台账簿','交易账户回报的持仓、权益、保证金和手续费。请同时查看快照时间。'],['暂停决策','不再调用策略的决策回调。订单、成交回报仍继续处理；不会自动平仓。'],['过滤策略','把该策略对组合目标的贡献改为零。执行器调整账户净持仓，理论持仓保留。'],['风控预警','提示阈值超限。后台预警默认不下单，人工过滤与原生风控分别配置。'],['Plus 回测','历史行情走实盘引擎和交易适配器，再由模拟柜台撮合。']],
           railExpanded:localStorage.getItem('wt.workspace.railExpanded')==='true',
           showDlgModPwd: false,
           oldpwd:'',
           newpwd:'',
           confirmpwd:'',
           loading:false,
           notifies:[],
           lastNotify:null,
           showNotifies:false
        }
    },
    methods: {
        openCommands(){this.commandQuery='';this.showCommands=true;this.loadCommandGroups()},
        loadCommandGroups(){this.$api.getGroups(result=>{if(result.result===0)this.commandGroups=result.groups||[]})},
        runPage(path,group){this.showCommands=false;this.$router.push({path,query:group?{group}:{}})},
        handleShortcut(event){if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'){event.preventDefault();this.openCommands()}},
        toggleRail(){this.railExpanded=!this.railExpanded;localStorage.setItem('wt.workspace.railExpanded',String(this.railExpanded))},
        toggleTheme(){setTheme(this.theme==='dark'?'light':'dark')},
        handleAccount(command) {
            if(command==='admins')this.$router.push('/admins')
            if(command==='concepts')this.showConcepts=true
            if(command.startsWith('theme-'))setTheme(command.slice(6))
            if(command==='config')this.$router.push({path:'/monitor',query:{...this.$route.query,config:'1'}})
            if(command==='password') this.onModPwd()
            if(command==='logout') this.onLogout()
        },
        handleItemSel: function(index, idxPath, obj, e){
            
        },
        onLogout: function(e){
            this.$confirm('确定要注销登录吗？', '注销登录', {
                confirmButtonText: '确定',
                cancelButtonText: '取消',
                type: 'warning'
            }).then(() => {
                this.$api.logout(()=>{});
                useSessionStore().logoutok();
                this.$push.disconnect();

                this.$router.push("/login");
            }).catch(() => {
      
            });
        },
        onModPwd: function(e){
            if(this.isGuest){this.$message.warning("游客模式不支持操作");return;}
            this.showDlgModPwd = true;
        },
        doModPwd: function(){
            let self = this;
            if(this.newpwd=='' || this.oldpwd=='' || this.confirmpwd==''){
                this.$alert("密码不能为空");
                return;
            }

            if(this.newpwd != this.confirmpwd){
                this.$alert("两次输入的新密码不一致");
                return;
            }

            this.loading = true;
            this.$api.modpwd(this.oldpwd, this.newpwd, (resObj)=>{
                if(resObj.result < 0){
                    self.$notify.error("密码修改失败：" + resObj.message, "修改密码");
                } else {
                    self.$notify.success("密码修改成功", "修改密码");
                    this.showDlgModPwd = false;
                }
                 setTimeout(()=>{
                    this.loading = false;
                },150);
            });
        },
        handleNotify:function(notify){
            this.notifies.push(notify);
            if(this.notifies.length > 100){
                this.notifies = this.notifies.slice(this.notifies.length - 100);
            }
            this.lastNotify = notify;
        },
        onClickScroller:function(e){
            this.showNotifies = !this.showNotifies;
        },
        getTitleStyle: function(title){
            if(title == '成交回报'){
                return 'title-success';
            } else {
                return 'title-warning';
            }
        },
        getIconStyle: function(title){
            if(title == '成交回报'){
                return 'el-icon-news';
            } else {
                return 'el-icon-warning-outline';
            }
        }
    },
    beforeUnmount(){window.removeEventListener('keydown',this.handleShortcut)},
    mounted(){
        window.addEventListener('keydown',this.handleShortcut);
        if(!this.cache.isLogined){
            this.$router.push("/login");
        } 
    }
}
</script>


<style scoped>
.wt-shell{display:flex;width:100%;height:100%;overflow:hidden;background:var(--wt-bg);color:var(--wt-text)}
.wt-rail{width:64px;flex:0 0 64px;display:flex;flex-direction:column;align-items:center;background:var(--wt-sidebar);border-right:1px solid var(--wt-line);padding:16px 0 12px;z-index:5;transition:width .18s,flex-basis .18s}
.wt-brand{width:36px;height:36px;display:flex;align-items:center;justify-content:center;background:var(--wt-text);color:var(--wt-surface);border:0;border-radius:10px;font-size:20px;font-weight:650;letter-spacing:-2px;cursor:pointer}
.wt-brand span{font-size:14px;align-self:flex-start;margin-top:5px;letter-spacing:0;color:var(--wt-surface)}
.wt-brand-name{font-weight:600;font-size:12px;margin-top:10px;letter-spacing:-.2px}
.wt-rail-nav{display:flex;flex-direction:column;gap:7px;margin:22px 0 16px;width:100%;align-items:center;min-height:0;overflow-y:auto;scrollbar-width:thin}.wt-rail-nav .wt-rail-link{flex-shrink:0}
.wt-rail-link{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:5px;width:52px;min-height:54px;border-radius:8px;text-decoration:none;color:var(--wt-muted);transition:background .15s,color .15s;position:relative}
.wt-rail-link:hover{background:var(--wt-hover);color:var(--wt-text)}
.wt-rail-link.active{background:var(--wt-selected);color:var(--wt-text)}
.wt-rail-link.active::before{content:'';position:absolute;left:-6px;height:20px;width:3px;border-radius:0 3px 3px 0;background:var(--wt-text)}
.wt-rail-label{font-size:10px;line-height:14px;white-space:nowrap}
.wt-rail-bottom{margin-top:auto;display:flex;flex-direction:column;align-items:center;gap:15px}
.wt-avatar{border:1px solid var(--wt-line);background:var(--wt-selected);width:32px;height:32px;border-radius:50%;font-size:12px;font-weight:600;color:var(--wt-text);cursor:pointer}
.rail-expanded .wt-rail{width:170px;flex-basis:170px}
.rail-expanded .wt-rail-nav{padding:0 10px}
.rail-expanded .wt-rail-link{width:100%;flex-direction:row;justify-content:flex-start;padding:0 12px;gap:12px;min-height:42px}
.rail-expanded .wt-rail-label{font-size:12px}
.rail-expanded .wt-rail-link.active::before{left:-10px}
.rail-expanded .wt-rail-bottom{width:100%;flex-direction:row;justify-content:center;gap:12px}
.wt-shell-body{flex:1;min-width:0;display:flex;flex-direction:column}
.wt-topbar{flex:0 0 52px;display:flex;align-items:center;gap:16px;padding:0 22px;border-bottom:1px solid var(--wt-line);background:var(--wt-surface)}
.wt-breadcrumb{display:flex;align-items:center;gap:12px;font-size:13px;white-space:nowrap}
.wt-breadcrumb>span:first-child{color:var(--wt-muted)}
.wt-slash{color:var(--wt-muted);opacity:.5}
.wt-breadcrumb strong{font-weight:550}
.wt-topbar-hint{font-size:12px;color:var(--wt-muted)}
.wt-topbar-actions{font-size:10px;margin-left:auto;display:flex;gap:12px;align-items:center}
.wt-theme-toggle{display:flex;gap:6px;align-items:center;background:none;border:1px solid var(--wt-line);border-radius:6px;color:var(--wt-muted);font-size:11px;padding:5px 8px;cursor:pointer;white-space:nowrap}
.wt-theme-toggle .wt-icon{width:14px;height:14px}
.wt-workspace-badge{font-size:10px;font-weight:600;letter-spacing:1.4px;color:var(--wt-muted);white-space:nowrap}
.wt-workspace{flex:1;min-height:0;min-width:0;overflow:auto;background:var(--wt-surface)}
.wt-statusbar{display:flex;align-items:center;gap:20px;flex:0 0 30px;border-top:1px solid var(--wt-line);background:var(--wt-surface);padding:0 18px;font-size:11px;color:var(--wt-muted)}
.wt-last-event{border:0;background:none;min-width:0;display:flex;gap:8px;align-items:center;font:inherit;color:inherit;cursor:pointer;flex:1;text-align:left;padding:0}
.wt-last-event .wt-icon{width:13px;height:13px}
.wt-last-event span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.wt-last-event time{font-variant-numeric:tabular-nums}
.wt-push-status{display:flex;align-items:center;gap:7px;white-space:nowrap}
.wt-push-status.connected{color:var(--wt-success)}
.wt-notify-dot{position:absolute;right:7px;top:6px;width:5px;height:5px;border-radius:50%;background:var(--wt-up)}
.time,.group,.channel,.title-success,.title-warning,.message{padding:2px 4px}
.time{color:var(--wt-muted)}.title-success{color:var(--wt-success)}.title-warning{color:var(--wt-warning)}
@media(max-width:900px){.wt-topbar{padding:0 14px}.wt-topbar-hint,.wt-workspace-badge{display:none}.wt-statusbar{gap:8px;padding:0 10px}.rail-expanded .wt-rail{width:146px;flex-basis:146px}}
.wt-command-button,.wt-notification-button{border:1px solid var(--wt-line);background:var(--wt-surface);color:var(--wt-muted);border-radius:6px;display:flex;align-items:center;gap:8px;padding:5px 8px;cursor:pointer;font-size:11px}.wt-notification-button .wt-icon{width:14px;height:14px}.wt-command-button kbd{font:10px var(--wt-mono);padding:1px 3px;border:1px solid var(--wt-line);border-radius:3px}.wt-commands{display:flex;flex-direction:column;gap:4px;max-height:56vh;overflow:auto;margin-top:12px}.wt-command-row{border:0;background:none;color:var(--wt-text);padding:12px;text-align:left;font:inherit;cursor:pointer;display:flex;gap:12px;align-items:center;flex:1}.wt-command-row:hover{background:var(--wt-hover)}.wt-command-row span,.wt-command-row small{margin-left:auto;color:var(--wt-muted);font-size:10px}.wt-command-group{display:flex;gap:8px;align-items:center}.wt-concepts{margin:0}.wt-concepts dt{font-size:13px;font-weight:600;margin-top:20px}.wt-concepts dt:first-child{margin-top:0}.wt-concepts dd{margin:6px 0 0;line-height:1.8;font-size:12px;color:var(--wt-muted)}@media(max-width:1200px){.wt-topbar-hint,.wt-push-status,.wt-command-button kbd{display:none}.wt-topbar{padding:0 14px;gap:10px}.wt-topbar-actions{gap:8px}.wt-command-button{font-size:10px}} 
</style>
