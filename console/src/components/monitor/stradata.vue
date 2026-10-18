<template>
    <div style="height:100%;overflow:auto;">
        <el-container style="height:100%;overflow:auto;">
            <el-header class="data-toolbar" style="height:40px; overflow-y:hidden;">
                <div style="height:100%;display:flex;flex-direction:row;">
                    <div style="flex:0 0 auto;height:100%;">
                        <el-tabs :model-value="selCat" tab-position="top" @tab-click="handleCatChange" style="height:100%;margin:0;">
                            <el-tab-pane label="持仓明细" name="pos">
                            </el-tab-pane>
                            <el-tab-pane label="成交明细" name="trd">
                            </el-tab-pane>
                            <el-tab-pane label="信号明细" name="sig">
                            </el-tab-pane>
                            <el-tab-pane label="回合明细" name="rnd">
                            </el-tab-pane>
                            <el-tab-pane label="每日绩效" name="fnd" v-if="isAdmin">
                            </el-tab-pane>
                        </el-tabs>
                    </div>
                    <div class="data-tools" style="flex:1;border-bottom:2px solid #E4E7ED;margin-top: 4px;">
                        <div style="float:right">
                            <el-row>
                                <el-col :span="4">
                                    <el-tooltip popper-class="wt-explanation-tooltip" :enterable="false" class="item" effect="dark" content="每隔30秒刷新一次" placement="top">
                                        <el-checkbox v-model="autoData" style="float:right;margin-top:6px;" @change="handleCheckAutoData">自动刷新</el-checkbox>
                                    </el-tooltip>
                                </el-col>
                                <el-col :offset="1" :span="12">
                                    <el-select v-model="strafilter" placeholder="请选择" size="small" @change="onStraSwitch">
                                        <el-option label="全部策略" value="all" v-if="selCat=='pos' || selCat=='fnd'">
                                            <i class="el-icon-collection"/>
                                            <span>全部策略</span>
                                        </el-option>
                                        <el-option :label="sid" :value="sid" :key="sid" v-for="sid in strategies">
                                            <i class="el-icon-tickets"/>
                                            <span>{{sid}}</span>
                                        </el-option>
                                    </el-select>
                                </el-col>
                                <el-col :span="4">
                                    <el-button type="primary" size="small" plain @click="queryData()"><i class="el-icon-refresh"></i>刷新</el-button>
                                </el-col>
                            </el-row>
                        </div>
                    </div> 
                </div>
            </el-header>
            <el-main style="overflow:auto;border-bottom: 1px solid #E4E7ED;">
                <div style="max-height:100%;overflow:auto;" v-show="selCat=='pos'" v-loading="loading.position">
                    <DataTable :storage-key="'wt.data.'+groupid+'.stradata.1'"
                        border
                        stripe
                        :data="positions"
                        :summary-method="getPosSum"
                        show-summary
                        class="table">
                        <el-table-column
                            prop="strategy"
                            label="策略"
                            width="140"
                            sortable>
                        </el-table-column>
                        <el-table-column
                            prop="code"
                            label="品种"
                            width="120"
                            sortable>
                        </el-table-column>
                        <el-table-column
                            label="数量"
                            width="64">
                            <template #default="scope">
                                <span :class="scope.row.qty>=0?'text-danger':'text-success'">{{scope.row.qty}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="浮盈"
                            width="100"
                            sortable>
                            <template #default="scope">
                                <span :class="scope.row.profit>=0?'text-danger':'text-success'">{{scope.row.profit.toFixed(1)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="开仓时间"
                            width="120">
                            <template #default="scope">
                                <span>{{fmtTime(scope.row.opentime)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="开仓价格"
                            width="100">
                            <template #default="scope">
                                <span>{{fmtPrice(scope.row.price)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="最大浮盈"
                            width="120"
                            sortable>
                            <template #default="scope">
                                <span class="text-danger">{{scope.row.maxprofit.toFixed(1)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="最大浮亏"
                            width="120"
                            sortable>
                            <template #default="scope">
                                <span class="text-success">{{scope.row.maxloss.toFixed(1)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            prop="opentag"
                            label="标记"
                            sortable>
                        </el-table-column>
                    </DataTable>
                </div>
                <div style="max-height:100%;overflow:auto;" v-show="selCat=='trd'" v-loading="loading.trade">
                    <DataTable :storage-key="'wt.data.'+groupid+'.stradata.2'"
                        border
                        stripe
                        :data="trades"
                        :summary-method="getTrdSum"
                        show-summary
                        class="table">
                        <el-table-column
                            prop="strategy"
                            label="策略"
                            width="140">
                        </el-table-column>
                        <el-table-column
                            prop="code"
                            label="品种"
                            width="120"
                            sortable>
                        </el-table-column>
                        <el-table-column
                            label="时间"
                            width="120">
                            <template #default="scope">
                                <span>{{fmtTime(scope.row.time)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="动作"
                            width="80">
                            <template #default="scope">
                                <span :class="(scope.row.action=='开多'||scope.row.action=='平空')?'text-danger':'text-success'">{{scope.row.action}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="价格"
                            width="80">
                            <template #default="scope">
                                <span>{{fmtPrice(scope.row.price)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            prop="volume"
                            label="数量"
                            width="80">
                        </el-table-column>
                        <el-table-column
                            prop="tag"
                            label="标记"
                            sortable>
                        </el-table-column>
                    </DataTable>
                </div>
                <div style="max-height:100%;overflow:auto;"  v-show="selCat=='sig'" v-loading="loading.signal">
                    <DataTable :storage-key="'wt.data.'+groupid+'.stradata.3'"
                        border
                        stripe
                        :data="signals"
                        :summary-method="getSigSum"
                        show-summary
                        class="table">
                        <el-table-column
                            prop="strategy"
                            label="策略"
                            width="140">
                        </el-table-column>
                        <el-table-column
                            prop="code"
                            label="品种"
                            width="120"
                            sortable>
                        </el-table-column>
                        <el-table-column
                            prop="target"
                            label="目标数量"
                            width="80">
                            <template #default="scope">
                                <span :class="fmtProfit(scope.row.target)">{{scope.row.target}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="触发价格"
                            width="100">
                            <template #default="scope">
                                <span>{{fmtPrice(scope.row.sigprice)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="触发时间"
                            width="180">
                            <template #default="scope">
                                <span>{{fmtTime(scope.row.gentime, true)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            prop="tag"
                            label="标记"
                            sortable>
                        </el-table-column>
                    </DataTable>
                </div>
                <div style="max-height:100%;overflow:auto;"  v-show="selCat=='rnd'" v-loading="loading.round">
                    <DataTable :storage-key="'wt.data.'+groupid+'.stradata.4'"
                        border
                        stripe
                        :summary-method="getRndSum"
                        show-summary
                        :data="rounds"
                        class="table">
                        <el-table-column
                            prop="strategy"
                            label="策略"
                            width="140">
                        </el-table-column>
                        <el-table-column
                            prop="code"
                            label="品种"
                            width="120"
                            sortable>
                        </el-table-column>
                        <el-table-column
                            label="方向"
                            width="64">
                            <template #default="scope">
                                <span :class="scope.row.direct=='LONG'?'text-danger':'text-success'">{{scope.row.direct=='LONG'?'多':'空'}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="开仓时间"
                            width="120">
                            <template #default="scope">
                                <span>{{fmtTime(scope.row.opentime)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="开仓价格"
                            width="80">
                            <template #default="scope">
                                <span>{{fmtPrice(scope.row.openprice)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="平仓时间"
                            width="120">
                            <template #default="scope">
                                <span>{{fmtTime(scope.row.closetime)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            label="平仓价格"
                            width="80">
                            <template #default="scope">
                                <span>{{fmtPrice(scope.row.closeprice)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            prop="qty"
                            label="数量"
                            width="64">
                        </el-table-column>
                        <el-table-column
                            label="盈亏"
                            width="100">
                            <template #default="scope">
                                <span :class="scope.row.profit>=0?'text-danger':'text-success'">{{scope.row.profit.toFixed(1)}}</span>
                            </template>
                        </el-table-column>
                        <el-table-column
                            prop="entertag"
                            label="进场标记"
                            width="100">
                        </el-table-column>
                        <el-table-column
                            prop="exittag"
                            label="出场标记"
                            width="100">
                        </el-table-column>
                    </DataTable>
                </div>
                <div style="height:100%;display:flex;flex-direction:column;"  v-show="selCat=='fnd'" v-loading="loading.fund"  v-if="isAdmin">
                    <div style="flex:1;width:100%;overflow:auto;height:50%;border-bottom:1px solid #E4E7ED;">
                        <div style="max-height:100%;">
                            <DataTable :storage-key="'wt.data.'+groupid+'.stradata.5'"
                                border
                                stripe
                                :data="funds"
                                class="table">
                                <el-table-column
                                    prop="strategy"
                                    label="策略"
                                    width="140">
                                </el-table-column>
                                <el-table-column
                                    label="日期"
                                    width="120">
                                    <template #default="scope">
                                        <span>{{fmtDate(scope.row.date)}}</span>
                                    </template>
                                </el-table-column>
                                <el-table-column label="当日盈亏" width="130">
                                    <template #default="scope"><span :class="fmtProfit(scope.row.addition)">{{scope.row.addition.toFixed(2)}}</span></template>
                                </el-table-column>
                                <el-table-column
                                    label="平仓盈亏"
                                    width="100">
                                    <template #default="scope">
                                        <span :class="scope.row.profit>=0?'text-danger':'text-success'">{{scope.row.profit.toFixed(1)}}</span>
                                    </template>
                                </el-table-column>
                                <el-table-column
                                    label="浮动盈亏"
                                    width="100">
                                    <template #default="scope">
                                        <span :class="scope.row.dynprofit>=0?'text-danger':'text-success'">{{scope.row.dynprofit.toFixed(1)}}</span>
                                    </template>
                                </el-table-column>
                                <el-table-column label="累计平仓盈亏" width="130">
                                    <template #default="scope"><span :class="fmtProfit(scope.row.closeprofit)">{{scope.row.closeprofit.toFixed(1)}}</span></template>
                                </el-table-column>
                                <el-table-column prop="fee" label="累计手续费" width="120" :formatter="formatAmount" />
                                <el-table-column prop="thisfee" label="当日手续费" width="120" :formatter="formatAmount" />
                                <el-table-column
                                    prop="dynbalance"
                                    label="动态权益">
                                    <template #default="scope">
                                        <span :class="scope.row.dynbalance>=0?'text-danger':'text-success'">{{scope.row.dynbalance.toFixed(1)}}</span>
                                    </template>
                                </el-table-column>
                            </DataTable>
                        </div>
                    </div>
                    <div style="flex:1;width:100%;overflow:auto;display:flex;flex-direction:column;margin:4px;">
                        <div style="height:40px;display:inline-block;flex:0;margin:4px;">
                            <el-row>
                                <el-col :span="2" style="margin-top:2px;">
                                    <i class="el-icon-money"></i>
                                    <a>资金规模</a>
                                </el-col>
                                <el-col :span="3" >
                                    <el-input v-model="capital" placeholder="请输入资金规模" size="small" type="number" min="1000000" step="1000000"></el-input>
                                </el-col>
                                <el-col :span="2" style="margin-left:2px;">
                                    <el-button size="small" plain type="danger" @click="onClickPaintChart">重新绘图</el-button>
                                </el-col>
                            </el-row>
                        </div>
                        <div style="flex:1;width:100%;border-top:1px solid #E4E7ED;">
                            <div id="trend" style="width:100%;height:100%;">
                            </div>
                        </div>                        
                    </div>
                </div>
            </el-main>
            <el-footer style="height:24px;">
                <span style="font-size:12px;color:gray;line-height:24px;">数据刷新时间: {{refreshTime}}</span>
            </el-footer>
        </el-container>
    </div>    
</template>

<script>
import DataTable from '../common/DataTable.vue'
import { portfolioFunds, strategyFunds } from "@/funds.js";
import { markRaw } from "vue";
import { mapState } from 'pinia';
import { useSessionStore } from '@/store.js';
export default {
    name: 'stradata',
    props:{
        groupid:{
            type:String,
            default(){
                return "";
            }
        }
    },
    computed:{
        ...mapState(useSessionStore, [
            'cache'
        ]),
        isAdmin(){
            let uInfo = this.cache.userinfo;
            if(uInfo)
                return (uInfo.role == 'admin' || uInfo.role == 'superman');
            else
                return false;        
        }
    },
    components: {DataTable,
    },
    watch:{
        groupid: {
            immediate: true,
            handler(newVal, oldVal){
            newVal = newVal || "";
            oldVal = oldVal || "";

            if(newVal.length == 0 || newVal == oldVal)
                return;

            //console.log(oldVal, newVal);

            setTimeout(()=>{
                //this.queryData();
                this.$api.getStrategies(newVal, (resObj)=>{
                    //console.log(resObj);
                    if(resObj.result < 0){
                        this.$notify.error('获取策略列表出错：' + resObj.message);
                    } else {
                        this.strategies = resObj.strategies;

                        if(!['pos', 'fnd'].includes(this.selCat) && this.strafilter=='all')
                            this.strafilter = this.strategies[0];
                        else if(this.selCat=='pos')
                            this.strafilter = 'all';

                        this.queryData();
                    }
                });
            }, 0);
        }
        }
    },
    data () {
        return {
            selCat:['pos', 'trd', 'sig', 'rnd', 'fnd'].includes(this.$route.query.cat)?this.$route.query.cat:'pos',
            strafilter:"all",
            strategies:[],
            loading:{
                trade: false,
                signal: false,
                round: false,
                position: false,
                fund: false
            },
            capital:5000000,
            trades:[],
            positions:[],
            signals:[],
            rounds:[],
            strategies:[],
            funds:[],
            nvChart:null,
            autoData: false,
            dataInterval: 0,
            refreshTime:new Date().format('yyyy.MM.dd hh:mm:ss')
        }
    },
    methods: {
        handleCheckAutoData: function(val){
            this.resetDataInterval();
        },
        resetDataInterval: function(){
            if(this.autoData){
                if(this.dataInterval != 0){
                    clearInterval(this.dataInterval);
                }

                this.dataInterval = setInterval(()=>{
                    this.queryData();
                }, 30000);
            } else if(this.dataInterval != 0){
                clearInterval(this.dataInterval);
            }
        },
        getTrdSum: function(param){
            const { columns, data } = param;
            const sums = [];
            columns.forEach((column, index) => {
                if (index < 3 || index > 5) {
                    sums[index] = '';
                    return;
                } else if (index == 3){
                    sums[index] = '总计';
                    return;
                } else if (index == 4){
                    sums[index] = data.length + "笔";
                } else if (index == 5){
                    const values = data.map(item => Number(item.volume));
                    if (!values.every(value => isNaN(value))) {
                        sums[index] = values.reduce((prev, curr) => {
                            const value = Number(curr);
                            if (!isNaN(value)) {
                                return prev + curr;
                            } else {
                                return prev;
                            }
                            }, 0) + '手';
                    } else {
                        sums[index] = 'N/A';
                    }
                }                
            });

            return sums;
        },
        getSigSum: function(param){
            const { columns, data } = param;
            const sums = [];
            columns.forEach((column, index) => {
                if (index > 1) {
                    sums[index] = '';
                    return;
                } else if (index == 0){
                    sums[index] = '总计';
                    return;
                } else if (index == 1){
                    sums[index] = data.length + "笔";
                }                
            });

            return sums;
        },
        getPosSum: function(param){
            const { columns, data } = param;
            const sums = [];
            columns.forEach((column, index) => {
                if (index != 0 && index != 1 && index != 2 && index != 3) {
                    sums[index] = '';
                    return;
                } else if (index == 0){
                    sums[index] = '总计';
                    return;
                } else if (index == 1){
                    sums[index] = data.length + "笔";
                } else if (index == 2){
                    const values = data.map(item => Number(item.qty));
                    if (!values.every(value => isNaN(value))) {
                        sums[index] = values.reduce((prev, curr) => {
                            const value = Number(curr);
                            if (!isNaN(value)) {
                                return prev + Math.abs(curr);
                            } else {
                                return prev;
                            }
                            }, 0) + '手';
                    } else {
                        sums[index] = 'N/A';
                    }
                } else if (index == 3){
                    const values = data.map(item => Number(item.profit));
                    if (!values.every(value => isNaN(value))) {
                        sums[index] = values.reduce((prev, curr) => {
                            const value = Number(curr);
                            if (!isNaN(value)) {
                                return prev + curr;
                            } else {
                                return prev;
                            }
                            }, 0).toFixed(1);
                    } else {
                        sums[index] = 'N/A';
                    }
                }
                
            });

            return sums;
        },
        getRndSum: function(param){
            const { columns, data } = param;
            const sums = [];
            columns.forEach((column, index) => {
                if (index != 5 && index != 6 && index != 7 && index != 8) {
                    sums[index] = '';
                    return;
                } else if (index == 5){
                    sums[index] = '总计';
                    return;
                } else if (index == 6){
                    sums[index] = data.length + "笔";
                    return;
                }

                if(index == 7){
                    const values = data.map(item => Number(item.qty));
                    if (!values.every(value => isNaN(value))) {
                        sums[index] = values.reduce((prev, curr) => {
                            const value = Number(curr);
                            if (!isNaN(value)) {
                                return prev + curr;
                            } else {
                                return prev;
                            }
                            }, 0) + '手';
                    } else {
                        sums[index] = 'N/A';
                    }
                } else if(index == 8) {
                    const values = data.map(item => Number(item.profit));
                    if (!values.every(value => isNaN(value))) {
                        sums[index] = values.reduce((prev, curr) => {
                            const value = Number(curr);
                            if (!isNaN(value)) {
                                return prev + curr;
                            } else {
                                return prev;
                            }
                            }, 0).toFixed(1);
                    } else {
                        sums[index] = 'N/A';
                    }
                }
                
            });

            return sums;
        },
        handleCatChange: function(tab, event){
            if(this.selCat == tab.props.name)
                return;

            this.selCat = tab.props.name;
            this.$router.replace({query:{...this.$route.query,cat:this.selCat}});

            if(!['pos', 'fnd'].includes(this.selCat) && this.strafilter=='all')
                this.strafilter = this.strategies[0];
            else if(this.selCat=='pos')
                this.strafilter = 'all';

            this.queryData();
        },
        onStraSwitch: function(){
            this.queryData();
        },
        formatAmount: function(row,col){
            return row[col.property].toFixed(2);
        },
        fmtPrice:function(val){
            let ret = val.toFixed(4);
            let idx=0;
            for(; idx < ret.length; idx++){
                if(ret[ret.length-1-idx] != '0')
                    break;
            }
            if(ret[ret.length-1-idx] == ".")
                idx ++;

            let len = ret.length-idx;
            return ret.substr(0, len);
        },
        fmtTime:function(val, bSignal){
            bSignal = bSignal || false;
            if(!bSignal){
                let ret = val + "";
                return ret.substr(2,2) + "." + ret.substr(4,2) + "." + ret.substr(6,2) + " " + ret.substr(8,2) + ":" + ret.substr(10,2);
            } else {
                let ret = val + "";
                return ret.substr(2,2) + "." + ret.substr(4,2) + "." + ret.substr(6,2) + " " + ret.substr(8,2) + ":" + ret.substr(10,2)+ ":" + ret.substr(12,2)+ "," + ret.substr(14,3);
            }
        },
        fmtDate:function(val){
            let ret = val + "";
            return ret.substr(0,4) + "." + ret.substr(4,2) + "." + ret.substr(6,2);
        },
        fmtProfit:function(val){
            if(val > 0)
                return 'text-danger';
            else if(val < 0)
                return 'text-success';
            else
                return '';
        },
        onClickPaintChart: function(){
            this.paintChart();
        },
        paintChart: function(){
            let self = this;
            if(this.nvChart == null) 
                this.nvChart = markRaw(this.$echarts.init(document.getElementById('trend')));

            let options = {
                title: {
                    text: '净值走势'
                },
                tooltip: {
                    trigger: 'axis',
                    axisPointer: {
                        label: {
                            backgroundColor: '#6a7985',
                            formatter: function(params) {
                                var val = params.value + '';
                                return val.substr(0, 4) + '.' + val.substr(4, 2) + '.' + val.substr(6, 2);
                            }
                        }
                    },
                    formatter: function(params) {
                        if(params.length == 0)
                            return "收益曲线";

                        var ret = params[0].axisValueLabel;

                        for(var idx in params) {
                            if(params[idx].seriesName == '净值')
                                ret += '<br/>净值: ' + params[idx].value.toFixed(4);
                            else
                                ret += '<br/>' + params[idx].seriesName + ': ' + params[idx].value;
                        }

                        return ret;
                    }
                },
                grid: {
                    top: 42,
                    left: '8',
                    right: '8',
                    bottom: '8',
                    containLabel: true
                },
                xAxis: {
                    type: 'category',
                    data: [],
                    boundaryGap: false,
                    axisLabel: {
                        textStyle: {
                            color: '#000'
                        },
                        formatter: function(val, idx) {
                            val = val + '';
                            return val.substr(0, 4) + '.' + val.substr(4, 2) + '.' + val.substr(6, 2);
                        }
                    },
                    axisLine: {
                        lineStyle: {
                            color: '#000'
                        }
                    }
                },
                yAxis: [{
                    type: 'value',
                    axisLabel: {
                        textStyle: {
                            color: '#000'
                        },
                        formatter: function(val, idx) {
                            return val.toFixed(4);
                        }
                    },
                    axisLine: {
                        lineStyle: {
                            color: '#000'
                        }
                    },
                    scale: true
                }],
                series: [{
                    name: '净值',
                    type: 'line',
                    smooth: true,
                    stack: '净值',
                    areaStyle: {
                        normal: {
                            color: {
                                type: 'linear',
                                x: 0,
                                y: 0,
                                x2: 0,
                                y2: 1,
                                colorStops: [{
                                    offset: 0,
                                    color: 'rgba(102,156,214,0.5)' // 0% 处的颜色
                                }, {
                                    offset: 1,
                                    color: 'rgba(242,242,242,0.3)' // 100% 处的颜色
                                }],
                                globalCoord: false // 缺省为 false
                            }
                        }
                    },
                    data: [],
                    lineStyle: {
                        normal: {
                            color: 'rgb(102,156,214)',
                            width: 2
                        }
                    },
                    itemStyle: {
                        normal: {
                            color: 'rgb(102,156,214)',
                            borderWidth: 1
                        }
                    }
                }]
            };

            let dates = [],
                prices = [];
            
            let baseamt = parseInt(self.capital);
            for(let idx = self.funds.length-1; idx >= 0; idx--) {
                let item = self.funds[idx];
                dates.push(item.date);
                let dynbal = baseamt + item.dynbalance;
                prices.push(dynbal/baseamt);
            }

            let maxPx = Math.max.apply(null, prices);
            let minPx = Math.min.apply(null, prices);

            if(maxPx == minPx) {
                maxPx *= 1.05;
                minPx *= 0.95;
            } else {
                var diff = maxPx - minPx;
                maxPx += diff * 0.05;
                minPx = Math.max(0, minPx - diff * 0.05);
            }

            options.xAxis.data = dates;
            options.series[0].data = prices;
            options.yAxis[0].max = maxPx;
            options.yAxis[0].min = minPx;

            this.nvChart.setOption(options);
        },
        queryData: function(needReset){
            needReset = needReset || false;
            let self = this;
            let curCat = this.selCat;
            let groupid = this.groupid || "";
            let straid = this.strafilter || "";

            if(groupid.length == 0){
                this.$notify.error("组合ID不能为空");
                return;
            }

            if(straid.length == 0){
                this.$notify.error("策略ID不能为空");
                return;
            }

            if(curCat == "trd"){
                self.loading.trade = true;
                setTimeout(()=>{
                    this.$api.getTrades(groupid, straid, (resObj)=>{
                        if (resObj.result < 0) {
                            this.$notify.error("查询成交出错：" + resObj.message);
                        } else {
                            resObj.trades.forEach((tItem)=>{
                                let action = "";
                                if(tItem.offset == "OPEN")
                                    action += "开";
                                else 
                                    action += "平";

                                if(tItem.direction == "LONG")
                                    action += "多";
                                else 
                                    action += "空";

                                tItem.action = action;
                            });
                            self.trades = resObj.trades;
                            self.trades.reverse();
                        }

                        self.loading.trade = false;
                        self.refreshTime = new Date().format('yyyy.MM.dd hh:mm:ss');
                        if(needReset)
                            self.resetDataInterval();
                    });
                }, 0);                
            } else if(curCat == "sig"){
                self.loading.signal = true;
                setTimeout(()=>{
                    this.$api.getSignals(groupid, straid, (resObj)=>{
                        if (resObj.result < 0) {
                            this.$notify.error("查询信号出错：" + resObj.message);
                        } else {
                            self.signals = resObj.signals;
                            self.signals.reverse();
                        }

                        self.loading.signal = false;
                        self.refreshTime = new Date().format('yyyy.MM.dd hh:mm:ss');
                        if(needReset)
                            self.resetDataInterval();
                    });
                }, 0);   
            } else if(curCat == "rnd"){
                self.loading.round = true;
                setTimeout(()=>{
                    this.$api.getRounds(groupid, straid, (resObj)=>{
                        if (resObj.result < 0) {
                            this.$notify.error("查询回合出错：" + resObj.message);
                        } else {
                            self.rounds = resObj.rounds;
                            self.rounds.reverse();
                        }

                        self.loading.round = false;
                        self.refreshTime = new Date().format('yyyy.MM.dd hh:mm:ss');
                        if(needReset)
                            self.resetDataInterval();
                    });
                }, 0);   
            } else if(curCat == "pos"){
                self.loading.position = true;
                setTimeout(()=>{
                    this.$api.getPositions(groupid, straid, (resObj)=>{
                        if (resObj.result < 0) {
                            this.$notify.error("查询持仓出错：" + resObj.message);
                        } else {
                            resObj.positions.forEach((pItem)=>{
                                pItem.qty = pItem.volume*(pItem.long?1:-1);
                            })
                            self.positions = resObj.positions;
                        }

                        self.loading.position = false;
                        self.refreshTime = new Date().format('yyyy.MM.dd hh:mm:ss');
                        if(needReset)
                            self.resetDataInterval();
                    });
                }, 0);   
            } else if(curCat == "fnd"){
                self.loading.fund = true;
                setTimeout(()=>{
                    this.$api.getFunds(groupid, straid, (resObj)=>{
                        if (resObj.result < 0) {
                            this.$notify.error("查询绩效出错：" + resObj.message);
                        } else {
                            self.funds = strategyFunds(resObj.funds, straid === 'all');                            
                            self.funds.reverse();
                            self.paintChart();
                        }
                        self.loading.fund = false;
                        self.refreshTime = new Date().format('yyyy.MM.dd hh:mm:ss');
                        if(needReset)
                            self.resetDataInterval();
                    });
                }, 0);   
            }
        }
    },
    mounted(){}
}
</script>

<!-- Add "scoped" attribute to limit CSS to this component only -->
<style scoped>
.el-header{
    padding: 0px 4px;
}

.table{
    width:100%;
    height:100%;
}

.el-select{
    width: 120px;
}
</style>
