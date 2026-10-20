<template>
  <section class="channel-profiles">
    <div class="channel-heading">
      <div><h2>通道环境</h2><p>组合停止后选择环境。各环境独立保存行情、持仓记录和策略控制状态。</p></div>
      <el-button :loading="loading" @click="load">刷新状态</el-button>
    </div>
    <el-alert type="info" :closable="false" show-icon title="切换不会启动进程或下单。先启动对应数据中台，再启动组合；一键启动会跟随当前环境。" />
    <el-table :data="profiles" stripe empty-text="此组合尚未登记可切换环境">
      <el-table-column label="环境" min-width="190"><template #default="{ row }">
        <strong>{{ row.label }}</strong><el-tag v-if="row.current" size="small" class="channel-current">当前</el-tag>
        <small>{{ row.description }}</small>
      </template></el-table-column>
      <el-table-column label="服务时段" min-width="240"><template #default="{ row }">
        <span>{{ row.session_hint }}</span><small>{{ row.reason }}</small>
      </template></el-table-column>
      <el-table-column label="数据中台" width="130"><template #default="{ row }">
        <el-tag :type="row.datahub_running ? 'success' : 'info'">{{ row.datahub_running ? '运行中' : '已停止' }}</el-tag>
      </template></el-table-column>
      <el-table-column label="操作" width="150"><template #default="{ row }">
        <el-button :disabled="!isGuest && (!canSwitch || running || !row.ready || row.current)" :loading="busy === row.id" @click="select(row)">
          {{ row.current ? '已选定' : '选择环境' }}
        </el-button>
      </template></el-table-column>
    </el-table>
    <p class="channel-note">{{ running ? '组合正在运行，请先停止组合再切换。' : '组合已停止，可选择环境。' }} 选择后保留组合名称和策略标识。切回原环境仍能查看原来的记录。</p>
  </section>
</template>

<script>
import {useSessionStore} from "@/store.js";
export default {
  name: 'ChannelProfiles',
  props: { groupid: { type: String, required: true } },
  emits: ['changed'],
  data: () => ({ profiles: [], loading: false, busy: '', running: false, canSwitch: false, serial: 0 }),
  computed:{isGuest(){return useSessionStore().cache.userinfo?.role==='guest'}},
  methods: {
    async request(path, body) {
      const response = await fetch('/mgr/' + path, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const result = await response.json();
      if (result.result < 0) throw new Error(result.message);
      return result;
    },
    async load() {
      const serial = ++this.serial;
      this.loading = true;
      try {
        const result = await this.request('qrychannels', { groupid: this.groupid });
        if (serial !== this.serial) return;
        this.profiles = result.profiles || [];
        this.running = Boolean(result.process_running);
        this.canSwitch = Boolean(result.can_switch);
      } catch (error) { if (serial === this.serial) this.$message.error(error.message); }
      finally { if (serial === this.serial) this.loading = false; }
    },
    async select(row) {
      if(this.isGuest){this.$message.warning('游客模式不支持操作');return;}
      try {
        await this.$confirm('切换到“' + row.label + '”后，将显示该环境独立的行情和账户记录。已有挂单及持仓不会随之转移。确认切换？', '切换通道环境', { type: 'warning', confirmButtonText: '确认切换', cancelButtonText: '取消' });
        this.busy = row.id;
        const result = await this.request('switchchannel', { groupid: this.groupid, profileid: row.id });
        await this.load();
        this.$message.success(result.message);
        this.$emit('changed', result);
      } catch (error) { if (error instanceof Error) this.$message.error(error.message); }
      finally { this.busy = ''; }
    }
  },
  watch: { groupid() { this.load(); } },
  mounted() { this.load(); },
  beforeUnmount() { ++this.serial; }
};
</script>

<style scoped>
.channel-profiles { padding: 20px; min-width: 0; color: var(--wt-text, #202124); }
.channel-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
h2 { font-size: 17px; margin: 0 0 8px; }
p, small { color: var(--wt-muted, #7d828b); font-size: 12px; line-height: 1.7; }
p { margin: 0; }
small { display: block; }
.channel-current { margin-left: 8px; }
.channel-profiles :deep(.el-alert) { margin-bottom: 16px; }
.channel-note { margin-top: 16px; }
</style>
