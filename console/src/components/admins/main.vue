<template>
	<div style="height:100%;width:100%;">
        <el-row style="height:100%;">
            <el-col :span="12" style="height:100%;">
                <div style="height:100%;display:flex;flex-direction:column;">
                    <div style="flex:0;margin:2px 4px 0px 4px;min-height:39px;display:flex;flex-direction:row;">
                        <div style="flex:0;" class="simtab">
                            <span>用户列表</span>
                        </div> 
                        <div style="flex:1;border-bottom: 1px solid #E4E7ED;margin-top:6px;">
                            <el-dropdown style="float:right;" @command="handleUserCmd" trigger="click">
                                <el-button size="small" aria-label="用户管理操作">管理</el-button>
                                <template #dropdown><el-dropdown-menu>
                                    <el-dropdown-item command="add">添加用户</el-dropdown-item>
                                    <el-dropdown-item command="mod">修改用户</el-dropdown-item>
                                    <el-dropdown-item command="del">删除用户</el-dropdown-item>
                                    <el-dropdown-item command="reset">重置密码</el-dropdown-item>
                                    <el-dropdown-item divided  command="refresh">刷新数据</el-dropdown-item>
                                </el-dropdown-menu></template>
                            </el-dropdown>
                        </div> 
                    </div>
                    <div style="flex:1;margin:10px 4px;height:100%;">
                        <div style="max-height:100%;overflow:auto;">
                            <el-table
                                border
                                stripe
                                :data="users"
                                class="table"
                                v-loading="loading_user"
                                highlight-current-row
                                @current-change="handleSelectUser">
                                <el-table-column
                                    prop="loginid"
                                    label="登录名"
                                    width="100">
                                </el-table-column>
                                <el-table-column
                                    prop="name"
                                    label="姓名"
                                    width="80">
                                </el-table-column>
                                <el-table-column prop="role" label="角色" width="110"><template #default="{row}">{{roles.find(item=>item.id===row.role)?.name||row.role}}</template></el-table-column>
                                <el-table-column
                                    prop="createtime"
                                    label="创建时间"
                                    width="100">
                                </el-table-column>
                                <el-table-column
                                    prop="createby"
                                    label="创建人"
                                    width="100">
                                </el-table-column>
                                <el-table-column
                                    prop="modifytime"
                                    label="修改时间"
                                    width="100">
                                </el-table-column>
                                <el-table-column
                                    prop="modifyby"
                                    label="修改人"
                                    width="100">
                                </el-table-column>
                                <el-table-column
                                    prop="remark"
                                    label="备注">
                                </el-table-column>
                            </el-table>
                        </div>
                    </div>
                </div>
            </el-col>
            <el-col :span="12" style="height:100%;border-left: 1px solid #E4E7ED;">
                <div style="height:100%;display:flex;flex-direction:column;">
                    <div style="flex:0;margin:2px 4px 0px 4px;min-height:39px;display:flex;flex-direction:row;">
                        <div style="flex:0;" class="simtab">
                            <span>操作日志</span>
                        </div> 
                        <div style="flex:1;border-bottom: 1px solid #E4E7ED;margin-top:6px;">
                            <div class="admin-log-actions">
                                
                                    <el-date-picker
                                        v-model="daterange"
                                        value-format="YYYY-MM-DD"
                                        aria-label="操作日志日期范围"
                                        type="daterange"
                                        align="right"
                                        size="small"
                                        style="float:right;"
                                        unlink-panels
                                        range-separator="至"
                                        start-placeholder="开始日期"
                                        end-placeholder="结束日期"
                                        @change="handleDtRangeChange">
                                    </el-date-picker>
                                
                                
                                    <el-button type="primary" size="small" plain style="float:right;" aria-label="刷新操作日志" @click="onClickQryActions">刷新</el-button>
                                
                            </div>                            
                        </div> 
                    </div>
                    <div style="flex:1;margin:10px 4px;height:100%;">
                        <div style="max-height:100%;overflow:auto;">
                            <el-table
                                border
                                stripe
                                :data="actions"
                                class="table">
                                <el-table-column
                                    prop="loginid"
                                    label="登录名"
                                    width="100">
                                </el-table-column>
                                <el-table-column
                                    prop="actiontime"
                                    label="操作时间"
                                    width="100">
                                </el-table-column>
                                <el-table-column
                                    prop="actionip"
                                    label="来源IP"
                                    width="120">
                                </el-table-column>
                                <el-table-column
                                    prop="action"
                                    label="类型"
                                    width="80">
                                </el-table-column>
                                 <el-table-column
                                    prop="remark"
                                    label="备注">
                                </el-table-column>
                            </el-table>
                        </div>
                    </div>
                </div>
            </el-col>
        </el-row>
        <el-dialog
            :title="addUser?'添加用户':'修改用户'"
            v-model="showUserDlg"
            class="dialog-user"
            width="min(560px, 94vw)">
            <el-row>
                <el-col :span="6">
                    <a>登录名：</a>
                </el-col>
                <el-col :span="18">
                    <el-input v-model="curUser.loginid" size="small" :disabled="!addUser"></el-input>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="6">
                    <a>用户姓名：</a>
                </el-col>
                <el-col :span="18">
                    <el-input v-model="curUser.name" size="small"></el-input>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="6">
                    <a>用户类型：</a>
                </el-col>
                <el-col :span="18">
                    <el-select v-model="curUser.role" aria-label="用户角色" style="width:100%"><el-option v-for="item in roles" :key="item.id" :value="item.id" :label="item.name"/></el-select><small class="role-help">{{roles.find(item=>item.id===curUser.role)?.hint}}</small>
                </el-col>
            </el-row>
            <el-row>
                <el-col :span="6">
                    <a>登录密码：</a>
                </el-col>
                <el-col :span="18">
                    <el-input v-model="curUser.passwd" size="small" type="password" show-password :disabled="!addUser"></el-input>
                </el-col>
            </el-row>
            <el-row style="height:60px;">
                <el-col :span="6">
                    <a>IP限制：</a>
                </el-col>
                <el-col :span="18">
                    <el-input type="textarea" v-model="curUser.iplist" size="small"></el-input>
                </el-col>
            </el-row>
            <el-row style="height:60px;">
                <el-col :span="6">
                    <a>备注信息：</a>
                </el-col>
                <el-col :span="18">
                    <el-input type="textarea" v-model="curUser.remark" style="min-height:80px;"></el-input>
                </el-col>
            </el-row>
            <template #footer><span class="dialog-footer">
                <el-button type="primary" plain size="small" @click="onCommitUser">提交数据</el-button>
            </span></template>
        </el-dialog>
    </div>
</template>

<script>
import {useSessionStore} from "@/store.js";
export default {
    name: 'UserManagement',
    data() {
        return {
            roles:[{id:'viewer',name:'只读用户',hint:'查看已授权组合，不执行控制操作。'},{id:'operator',name:'操作员',hint:'查看并控制已授权组合，可确认和静默告警。'},{id:'admin',name:'管理员',hint:'管理监控、用户、配置与策略研究。'},{id:'researcher',name:'研究员',hint:'策略编辑与回测。'},{id:'risker',name:'旧版风控员',hint:'兼容原有账户，查看已授权组合。'},{id:'trader',name:'旧版交易员',hint:'兼容操作员的控制权限。'},{id:'superman',name:'超级管理员',hint:'管理和研究全部功能。'}],
            users:[],
            actions:[],
            curUser:{
                loginid:"",
                name:"",
                remark:"",
                role:"viewer",
                passwd:"",
                iplist:""
            },
            daterange:[],
            showUserDlg: false,
            addUser: true,
            loading_user:false
        }
    },
    methods:{
        handleSelectUser: function(usrInfo){
            if(!usrInfo)return;
            this.curUser = {...usrInfo};
        },
        handleDtRangeChange: function(){
        },
        onClickQryActions: function(){
            if(!this.daterange || this.daterange.length!==2){
                this.$message.warning("请选择操作日志的日期范围");
                return;
            }
            this.$api.getActions(this.daterange[0]+" 00:00:00", this.daterange[1]+" 23:59:59", (resObj)=>{
                if(resObj.result >= 0)this.actions = resObj.actions || [];
                else this.$message.error(resObj.message);
            });
        },
        onCommitUser: function(){
            if(!this.curUser.loginid?.trim() || !this.curUser.name?.trim()){this.$message.warning("请填写登录名和用户姓名");return;}
            if(this.addUser && !this.curUser.passwd){this.$message.warning("请填写登录密码");return;}
            this.$api.commitUser(this.curUser, this.addUser?"add":"mod", (resObj)=>{
                if(resObj.result < 0){
                    this.$notify.error(resObj.message);
                } else {
                    this.$notify({
                        message:"用户信息提交成功",
                        type:"success"
                    });
                    this.showUserDlg = false;
                    this.queryUsers();
                }
            });
        },
        queryUsers: function(){
            this.loading_user = true;
            this.$api.getUsers((resObj)=>{
                if(resObj.result >= 0){
                    this.users = resObj.users || [];
                    if(!this.users.some(user=>user.loginid===this.curUser.loginid))this.curUser={loginid:"",name:"",remark:"",role:"viewer",passwd:"",iplist:""};
                } else {
                    this.$notify.error(resObj.message);
                }
                this.loading_user = false;
            });
        },
        handleUserCmd: function(command){
                if(useSessionStore().cache.userinfo?.role==='guest' && command!=='refresh'){this.$message.warning('游客模式不支持操作');return;}
            if(command == 'add'){
                this.curUser = {
                    loginid:"",
                    name:"",
                    remark:"",
                    role:"viewer",
                    passwd:"",
                    iplist:""
                };
                this.addUser = true;
                this.showUserDlg = true;
            } else if(command == 'mod'){
                if(this.curUser.loginid == ""){
                    this.$alert("请选择要修改的用户");
                    return;
                }
                this.curUser.passwd = "********";
                this.addUser = false;
                this.showUserDlg = true;
            } else if(command == 'del'){
                if(this.curUser.loginid == ""){
                    this.$alert("请选择要删除的用户");
                    return;
                }
                
                this.$confirm('确定要删除用户' + this.curUser.loginid + '吗?', '删除用户', {
                    confirmButtonText: '确定',
                    cancelButtonText: '取消',
                    type: 'warning'
                }).then(() => {
                    this.$api.delUser(this.curUser.loginid, (resObj)=>{
                        if(resObj.result < 0){
                            this.$notify.error(resObj.message);
                        } else {
                            this.$notify({
                                message: "用户已删除",
                                type:"success"
                            });
                            this.queryUsers();
                        }
                    });
                }).catch(()=>{});
            } else if(command == 'reset'){
                if(this.curUser.loginid == ""){
                    this.$alert("请选择要重置密码的用户");
                    return;
                }

                this.$prompt('请输入新的密码', '重置密码', {
                    confirmButtonText: '确定',
                    cancelButtonText: '取消',
                    inputType: 'password'
                }).then(({ value }) => {
                    if(!value){this.$message.warning("新密码不能为空");return;}
                    this.$confirm('确定要重置用户' + this.curUser.loginid + '的密码吗?', '重置密码', {
                        confirmButtonText: '确定',
                        cancelButtonText: '取消',
                        type: 'warning'
                    }).then(() => {
                        this.$api.resetpwd(this.curUser.loginid, value, (resObj)=>{
                            if(resObj.result < 0){
                                this.$notify.error(resObj.message);
                            } else {
                                this.$notify({
                                    message: "密码重置成功",
                                    type:"success"
                                });
                            }
                        });
                    }).catch(()=>{});
                }).catch(() => {
                           
                });
                
                
            } else if(command == "refresh"){
                this.queryUsers();
            }

        }
    },
    mounted (){
        this.$nextTick(()=>{
            this.queryUsers();
        })       
    }
}

</script>

<!-- Add "scoped" attribute to limit CSS to this component only -->
<style scoped>
.admin-log-actions{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap;}.admin-log-actions .el-date-editor{width:250px;max-width:100%;}
.simtab{
    padding: 0px 20px;
    height:100%;
    line-height:39px; 
    border: 1px solid #E4E7ED;
    border-bottom: none;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
    min-width:56px;
    font-size:14px;
}

.dialog-user .el-row{
    margin: 4px 8px;
    padding: 4px 0px;
    align-items: center;
    align-content: center;
    vertical-align: middle;
    height: 36px;
}
.role-help{display:block;color:var(--wt-muted);font-size:11px;line-height:1.6;margin-top:6px}
</style>
