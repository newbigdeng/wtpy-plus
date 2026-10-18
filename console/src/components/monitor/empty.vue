<template>
    <div class="empty-box">
        <div class="content">
            <h2>尚未添加组合</h2><p>连接一个运行目录，查看行情和策略账簿。</p><el-button type="primary" @click="$emit('create')">添加组合</el-button>
        </div>
    </div>    
</template>

<script>
export default {
    name: 'empty',
    components: {
    },
    data () {
        return {
            
        }
    },
    methods: {
    }
}
</script>

<!-- Add "scoped" attribute to limit CSS to this component only -->
<style scoped>
.empty-box {
    display: flex;
    justify-content: center;
    align-items: center;
    height:70vh;
    width:100%;
}

.content{
    width: 240px;
    height: 400px;
    text-align: center;
}
</style>
