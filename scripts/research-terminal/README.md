# 研究终端怎样运行？

这是现有 WT 研究服务的按需分析后端。
网页请求先经过原会话认证，再转入回环服务。
代码不创建账户、交易进程或策略准入通道。

| 文件 | 职责 |
|---|---|
| terminal_service.py | 调度、权限投影和串行计算 |
| terminal_analytics.py | 估值、风险和定价 |
| terminal_data.py | 固定来源、限时请求和缓存 |
| terminal_research.py | 原文研究、共享预算和任务状态 |
| terminal_workflows.py | 节点类型检查和分析执行 |
| terminal_mcp.py | 本机标准输入输出工具 |
| research_budget.py | 与 RD-Agent 共用的调用计数 |
| research_records.py | 复用原研究库的记录读写 |

## 怎样接入原环境？

沿用研究虚拟环境和原研究数据库。

`WT_REVIEW_OPS` 指向现有研究脚本目录。
该目录需有 `factor-automation-config.json`。
先由原研究服务建立研究数据库。
其中包含原数据库、研究锁和桥接配置。
原 RD-Agent 从 `research_budget` 导入 `reserve_call`。
不要再保留另一套调用计数器。

配置示例见 `factor-automation-config.example.json`。
复制后填写研究库位置和已配置的模型桥接地址。
本目录不包含个人账户、认证信息或研究资料。

依赖包括 NumPy、SciPy、fincore、FastAPI、uvicorn。
新增依赖版本见 `requirements-extra.txt`。
服务模板见 `wt-terminal.service`。
部署时替换其中的机器路径。

`wtpy/monitor/TerminalRoutes.py` 负责认证转发。
`ResearchServer.py` 注册这些路由。
公开网关只放行目录、缓存及空的私有列表。
计算、模型和流程写入只允许研究账户。

## 怎样检查计算和权限？

使用定价关系、约束和隔离数据库验证。

```sh
python -m unittest test_terminal -v
python test_mcp.py
```

第二条命令针对本目录的 MCP 入口。
它检查初始化、工具列表和一次定价调用。
第一条使用替身模型，不消耗桥接额度。

## 哪些事情不能混在一起？

分析结果、预测相关性和策略收益分别保存。

如果把标的价格收益叫作策略收益，就会漏算成本。
如果把最新宏观值回填历史，就会引入未来数据。
画布和 AI 助手不能替代原生准入结果。
更完整的使用说明在个人阅读库第 96 篇。
