<template>
  <el-button @click="visible=true">如何添加策略</el-button>
  <el-dialog v-model="visible" title="把 Python 策略接入回测和组合" width="min(840px, 94vw)" append-to-body>
    <div class="strategy-setup-guide">
      <p>源码文件保存在哪里，与引擎是否加载它是两件事。回测通过研究列表登记；模拟盘和实盘通过组合的启动计划登记。</p>
      <h3>先加入策略库，再选择用途</h3>
      <ol><li>进入左侧“策略管理”，导入 .py 或创建 CTA、HFT、SEL 框架。</li><li>在线回测选择库版本，形成独立研究副本。研究编辑不会回写策略库。</li><li>选择原生或 Plus 回测。CTA Plus 可选择 MinImpact、TWAP、VWAP。</li></ol>
      <p>每份源码只定义一个对应类型的策略类。原生回测的构造函数须接受 <code>name</code>；Plus 按构造函数支持的参数传入名称、合约和周期。将文件直接复制进目录，不会自动登记到研究列表。</p>
      <h3>模拟盘／实盘：选择库中的固定版本</h3>
      <ol><li>停止目标组合，关闭进程守护和自动调度，核对持仓与未成交委托。</li><li>进入“策略与启动计划”，从策略库选择匹配引擎类型的策略。</li><li>填写唯一实例 ID、显示名称及构造参数，选择交易通道和执行器。</li><li>保存草稿，核对所有策略后应用到启动配置。保存与应用都不会启动进程。</li><li>需要交易时，由管理员明确开启交易授权并应用，再手动启动。游客只能查看。</li></ol>
      <p>实例 ID 决定策略账簿，显示名称可以单独更改。已有组合可以继续添加实例。库中修改代码后产生新版本，已有组合不会悄悄更新。组合移除实例不会删除库策略。旧入口第一次切换时，原来手工注册的策略不会自动迁移，应先完整核对。</p>
      <h3>原有手写入口还能使用吗？</h3>
      <p>可以继续手动维护。页面管理使用独立的 <code>run_managed.py</code>，不会覆盖原 <code>run.py</code>。手写入口仍按下列方式注册策略。</p>
      <pre>{{example}}</pre>
      <p>组合引擎类型必须与策略一致，CTA、HFT 分别建组合。HFT 使用 <code>engine.add_hft_strategy(strategy, trader="通道ID")</code>。页面托管先支持 CTA 与 HFT；SEL 原生回测保留，SEL 实盘暂不提供页面管理。Python 策略不写入 C++ 插件策略的 <code>strategies</code> 配置段。</p>
      <h3>怎样新建组合？</h3>
      <p>点击组合列表旁的加号，选择已有组合作为环境模板，填写 ID、名称与引擎类型。服务器建立独立运行目录，再进入启动计划导入策略。也可以通过“登记已有运行目录”继续接入手工配置的组合。</p>
      <p class="guide-note">研究策略不会自动挂到交易组合。应用启动计划也不会启动交易。真正运行前，还需核对账户环境、执行器、风控与行情订阅；更改源码后不会热加载。</p>
    </div>
  </el-dialog>
</template>

<script>
export default {
  name:'StrategySetupGuide',
  data:()=>({visible:false,example:`# 组合启动脚本：保留已有初始化、配置和控制桥
from strategies.MyStrategy import MyStrategy

first = MyStrategy(name="trend_rb", code="SHFE.rb.2701", period="m1")
engine.add_cta_strategy(first)

# 已有组合再加一个实例：名称必须唯一
second = MyStrategy(name="trend_cu", code="SHFE.cu.2701", period="m1")
engine.add_cta_strategy(second)

# 所有策略注册完成后，再按原入口启动引擎
# engine.run(True)`})
}
</script>

<style scoped>
.strategy-setup-guide{color:var(--wt-text);font-size:13px;line-height:1.9}.strategy-setup-guide h3{font-size:15px;font-weight:550;margin-top:24px}.strategy-setup-guide pre{padding:16px;overflow:auto;background:var(--wt-bg);border:1px solid var(--wt-line);border-radius:8px;font-size:12px;line-height:1.8}.strategy-setup-guide code{font-family:Consolas,monospace}.guide-note{color:var(--wt-muted);border-top:1px solid var(--wt-line);padding-top:14px}
</style>
