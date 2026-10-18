<template>
    <div style="height:100%;width:100%;display:flex;flex-direction:row;">
        <div style="flex:0 320px;min-width:320px;border:1px solid #DCDFE6;border-radius:2px;margin:4px;">
            <div style="height:100%;overflow:auto;">
                <el-tree :data="folders" @node-click="handleFileClick"></el-tree>
            </div>
        </div>
        <div style="flex:1;margin:4px;display:flex;flex-direction:column;overflow:auto;">
            <div style="flex:1;height:100%;overflow:auto;border:1px solid #DCDFE6;border-radius:2px;" v-loading="fileOnway">
                <div style="height:100%">
                    <codemirror
                        ref="mycode"
                        v-model="content_s"
                        :options="cmOptions"
                        style="height:100% !important;">
                    </codemirror>
                </div>
            </div>
            <div style="flex:0 32px;margin-top:8px;">
                <span style="font-size:12px;color:gray;float:left;">当前文件:{{curFilePath}}</span>
                <el-button size="small" style="float:right;" v-show="!edit" :disabled="!curFile || fileOnway" @click="onClickEdit()">
                    <i class="el-icon-edit"/>修改
                </el-button>
                <el-button size="small" style="float:right;" v-show="edit" :loading="saving" @click="onClickCommit()">
                    <i class="el-icon-set-up"/>提交
                </el-button>
                <el-button size="small" style="float:right;" v-show="edit" :disabled="saving" @click="onClickCancel()">
                    <i class="el-icon-refresh-left"/>取消
                </el-button>
            </div>
        </div>
    </div>  
</template>

<script>
import codemirror from "@/components/common/CodeEditor.vue";




export default {
    name: 'editor',
    props:{
        groupid:{
            type:String,
            default(){
                return "";
            }
        }
    },
    computed:{
        curFilePath(){
            if(this.curFile == null)
                return '';
            else
                return this.curFile.path;
        }
    },
    components:{
        codemirror
    },
    watch:{
        groupid: {
            immediate: true,
            handler(newVal, oldVal){
            newVal = newVal || "";
            oldVal = oldVal || "";

            if(newVal.length == 0 || newVal == oldVal)
                return;
            
            this.readSerial++;
            this.curFile = null;
            this.content_s = '';
            this.edit = false;
            this.saving = false;
            this.fileOnway = false;
            this.cmOptions.readOnly = true;
            this.queryFiles();
        }
        }
    },
    data () {
        return {
            folders:[],
            edit: false,
            filename:'',
            content_s:"",
            content_backup:"",
            curFile:null,
            fileOnway: false,
            saving: false,
            readSerial: 0,
            cmOptions:{
                mode:"python",
                keyMap: "sublime", // 快键键风格
                lineNumbers: true, // 显示行号
                smartIndent: true, // 智能缩进
                indentUnit: 4, // 智能缩进单位为4个空格长度
                indentWithTabs: true, // 使用制表符进行智能缩进
                lineWrapping: true, // 
                // 在行槽中添加行号显示器、折叠器、语法检测器
                gutters: ["CodeMirror-linenumbers", "CodeMirror-foldgutter", "CodeMirror-lint-markers"], 
                foldGutter: true, // 启用行槽中的代码折叠
                autofocus: true, // 自动聚焦
                matchBrackets: true, // 匹配结束符号，比如"]、}"
                autoCloseBrackets: true, // 自动闭合符号
                styleActiveLine: true, // 显示选中行的样式
                readOnly:true
            }
        }
    },
    methods: {
        queryFiles: function(){
            let self = this;
            const groupid = self.groupid;
            self.$api.getGroupDir(self.groupid, (resObj)=>{
                if (self.groupid !== groupid) return;
                if(resObj.result < 0){
                    self.$notify.error('获取组合目录结构失败：' + resObj.message);
                } else {
                    this.folders = [resObj.tree];
                }
            });
        },
        handleFileClick: async function(data){
            let self = this;
            if(data.isfile){
                if (self.saving) return;
                if (self.edit && self.content_s !== self.content_backup) {
                    try { await self.$confirm('内容已被修改，确定要放弃修改吗?', '提示', { type:'warning' }); }
                    catch { return; }
                }
                let ay = data.path.split('.');
                let ext = ay[ay.length-1].toLowerCase()
                let exts = ['py','json','js','csv']
                if(exts.indexOf(ext) == -1){
                    this.$toast("该文件不可查看");
                    return;
                }
                self.fileOnway = true;
                self.curFile = data;
                self.edit = false;
                self.cmOptions.readOnly = true;
                const groupid = this.groupid, serial = ++self.readSerial;
                self.$api.getGroupFile(this.groupid, data.path, (resObj)=>{
                    if (serial !== self.readSerial || groupid !== self.groupid) return;
                    self.fileOnway = false;
                    if(resObj.result < 0){
                        self.curFile = null;
                        self.content_s = '';
                        self.$notify.error('获取文件内容失败' + resObj.message);
                    } else {
                        self.content_s = resObj.content;

                        self.cmOptions.mode = (function(ext){
                            if(ext == 'py')
                                return 'python';
                            else if(ext == 'json')
                                return {name:"javascript", json: true};
                            else if(ext == 'js')
                                return "javascript";
                            else
                                return 'text/plain';
                        })(ext);
                        self.fileOnway = false;
                    }
                });
            }
        },
        onClickEdit: function(){
            this.content_backup = this.content_s;
            this.cmOptions.readOnly = false;
            this.edit = true;
        },
        onClickCancel: function(){
            if(this.content_s != this.content_backup){
                this.$confirm('内容已被修改，确定要放弃修改吗?', '提示', {
                        confirmButtonText: '确定',
                        cancelButtonText: '取消',
                        type: 'warning'
                    })
                    .then(() => {
                        this.content_s = this.content_backup;
                        this.cmOptions.readOnly = true;
                        this.edit = false;
                    })
                    .catch(() => {       
                    });
            } else {
                this.content_s = this.content_backup;
                this.cmOptions.readOnly = true;
                this.edit = false;
            }            
        },
        onClickCommit: function(){

            if (!this.curFile || this.saving) return;
            const groupid = this.groupid, filepath = this.curFile.path;
            this.saving = true;
            this.$api.commitGroupFile(groupid, filepath, this.content_s, (resObj)=>{
                if (this.groupid !== groupid || this.curFile?.path !== filepath) return;
                this.saving = false;
                if(resObj.result < 0){
                    this.$message.error("文件提交失败:" + resObj.message);
                    return;
                } else {
                    this.$message({
                        message:"文件提交成功",
                        type:"success"
                    });
                }
                this.edit = false;
                this.cmOptions.readOnly = true;
            });
        }
    },
    mounted(){
        
    }
}
</script>
<style>
.CodeMirror{
    height:100% !important;
}
</style>
