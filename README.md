# wtpy-plus

基于 [wtpy](https://github.com/wondertrader/wtpy) 的二次开发。
保留上游来源、MIT 许可和 Git 历史。
本仓库不是 wtpy 官方发行版。

原版 wtpy 是 WonderTrader 的 Python 子框架。
它提供策略接口、回测、数据工具和监控服务。
本项目配合 [WonderTrader-plus](https://github.com/newbigdeng/WonderTrader-plus) 使用。
C++ 负责引擎、委托和账户账簿。
Python 负责任务、策略接口和网页服务。

## 怎么读这个仓库？

先按 Docker 步骤启动空工作台。
再登记数据，选择快速回测或 Plus 回测。
账户配置和运行输出始终放在数据卷里。

## 工作台有哪些功能？

同一个网页管理监控、调度和在线回测。

![监控工作台浅色主题，展示数据均为合成数据](images/monitor-light.png)

![监控工作台深色主题，展示数据均为合成数据](images/monitor-dark.png)

- 监控中心：组合、策略、行情、资金和风险。
- 调度中心：启停进程、定时任务和配置对比。
- 在线回测：策略模板、代码编辑和结果详情。
- 数据工作台：切换策略、组合和通道维度。
- 表格：关键词、时间、列选择和 CSV 导出。
- 风险面板：权益、保证金、可用资金和拒单。
- 策略控制：分开提供暂停决策和原生过滤。
- 通道配置：停机后切换已登记的环境。

界面支持黑白主题，也可跟随系统。
组合侧栏能整体收起，小窗口改用抽屉。
看盘自动刷新可选，图表和页面允许纵向滚动。
Ctrl+K 打开命令入口。
组合和页签保存在链接中，刷新会恢复会话。

风险告警支持确认和定时静默。
自动基准按交易日固定，不随盘中资金重写。
柜台断开或账户过旧时，柜台指标显示未知。
手续费与资金查询来自原生回报。
“已请求”与“已收到新资金回报”分开判断。

## 两种回测怎么选？

先验证信号，再验证接近实盘的执行链。

策略写对，不等于执行过程也处理正确。
拆单、撤单或资金不足都可能改变结果。
所以工作台保留两种独立的回测模式。

![Python、原生引擎与工作台的调用链](images/workbench-flow.svg)

| 模式 | 支持内容 |
| --- | --- |
| 原版快速回测 | CTA、HFT、SEL；CTA 可选成交模型 |
| Plus 回测 | CTA、HFT；使用实盘引擎和模拟柜台 |
| Plus CTA 执行器 | MinImpact、TWAP、VWAP |

1. 新建策略，选择策略类型。
2. 编辑自动生成的类和回调框架。
3. 选择回测模式、数据和时间。
4. Plus 可配置资金、费用、滑点和执行参数。
5. 运行后查看成交、资金、持仓、日志和任务状态。

右上角 API 手册说明策略接口与用法。
例如 CTA 的 `stra_enter_long` 用于增加多头。
实际执行仍受引擎和执行器约束。

快速 CTA 可选择 `legacy_cta`、`causal_touch`。
也可以使用允许部分成交的 `volume_limited`。
Plus 使用原生模拟委托回报和资金账簿。
它不会再在 Python 中独立重算一套成交。

TWAP（按时间拆单）使用原生执行单元。
VWAP（按量分配）使用事先提供的权重曲线。
权重按执行窗口等分，不读取未来 Tick。
HFT 策略直接下单，不选择 CTA 执行器。

## 怎样启动 Docker 工作台？

两个仓库配套构建，运行数据单独保存。
当前 Docker 构建支持 Linux x86_64。

```bash
git clone -b wt-plus https://github.com/newbigdeng/WonderTrader-plus.git
git clone -b py-plus https://github.com/newbigdeng/wtpy-plus.git
docker build --build-arg BUILD_JOBS=2 \
  -t wondertrader-plus:native ./WonderTrader-plus
cd wtpy-plus
docker compose up -d --build
```

打开 `http://127.0.0.1:8100/console/`。
首次用户名为 `admin`。
初始密码只在数据卷文件中保存：

```bash
docker compose exec workbench cat /data/admin-password.txt
```

也可预先挂载密码文件。
用 `WT_ADMIN_PASSWORD_FILE` 指定文件路径。
`WT_ADMIN_USER` 可设置首次用户名。
这些设置只作用于空数据库。
停止工作台使用 `docker compose down`。
不加 `-v` 就会保留数据库和运行结果。

在虚拟机上运行时，Windows 使用 SSH 转发：

```bash
ssh -N -L 127.0.0.1:8100:127.0.0.1:8100 用户名@虚拟机地址
```

随后仍打开本机的 `8100` 地址。
公开镜像不包含 SimNow 凭据或已有组合。

## 怎样接入自己的数据和交易配置？

账户、数据和结果放在挂载目录，不放源码。

行情由 WT 数据组件接收和保存。
Tick 与 K 线使用 WT 的 DMB/DSB 格式。
网页请求后端读取接口，不直接读取虚拟机文件。
已有历史数据可在收盘后继续查询。

| 配置 | 放置方式 |
| --- | --- |
| 历史行情 | 挂载 WT 存储目录 |
| 品种与合约 | 挂载配套 `basefiles` 目录 |
| 数据服务 | `/data/workbench.json` |
| Plus 数据登记 | `/data/plus-datasets.json` |
| 交易账户 | 私有组合配置目录 |
| 监控与回测结果 | Docker 的 `/data` 数据卷 |

数据服务配置示例：

```json
{
  "basefiles": "/data/market/common",
  "storage": "/data/market/storage"
}
```

Plus 数据登记示例见 [docker/plus-datasets.example.json](docker/plus-datasets.example.json)。
HTTP 请求只选择数据编号，不能传任意文件路径。
费用优先取登记数据配套的 WT 配置。
缺失费用时，需要明确补齐后才能运行。

监控用户可分为 viewer、operator 和 admin。
viewer 只读，operator 可操作授权组合。
admin 管理配置、用户，并可执行回测。
原有 researcher、trader、risker 等角色保留。
启停、暂停、过滤和风险处置都有操作记录。

## 前后端和 C++ 怎么一起开发？

前端随 Python 服务一起发布，接口更容易配套。

| 目录 | 职责 |
| --- | --- |
| `console/` | Vue 前端源码和锁文件 |
| `wtpy/monitor/` | 监控、调度、风险和任务接口 |
| `wtpy/wrapper/` | Python 与 C++ 动态库接口 |
| `docker/` | 空工作台初始化与部署入口 |
| 配套 WT 仓库 | 引擎、执行器、模拟柜台和 NNG |

```bash
docker compose exec -w /workspace/src/wtpy/console workbench npm ci
docker compose exec -w /workspace/src/wtpy/console workbench npm run build
docker compose exec -u 0 workbench sh -c \
  'cp -a /workspace/src/wtpy/console/dist/. /opt/wt/static/console/'
```

镜像内保留 Node.js 和前端源码。
更改源码后，重新构建并更新静态文件。
也可在独立开发环境使用 `npm run dev`。
开发模式由 Vite 转发后端接口。
Docker 自动把产物放入 Python 的静态资源目录。
不要将 `node_modules` 或本地账户数据库提交。

Python 仍使用 `import wtpy`。
仓库改名不改变策略导入路径。
运行 Plus 必须使用配套构建的 C++ 库。
官方旧动态库不包含新增账户与回放入口。

## 使用了哪些技术？

前端迁移到 Vue 3，服务与引擎分别构建。

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3.5、Pinia 4、Vue Router 5 |
| 控件与图表 | Element Plus 2、ECharts 6 |
| 代码编辑 | CodeMirror 6 |
| 前端构建 | Node.js 22、Vite 8 |
| 服务 | Python 3.14、FastAPI、Uvicorn、Starlette |
| 数据库与进程 | SQLite、psutil、subprocess |
| 底层引擎 | C++17、GCC 15.2、Boost 1.90、NNG 1.11 |

Python 依赖固定在 [requirements-plus.lock](requirements-plus.lock)。
前端依赖固定在 [console/package-lock.json](console/package-lock.json)。

## 主要源码从哪里看？

| 功能 | 入口 |
| --- | --- |
| 快速回测与策略模板 | [WtBtMon.py](wtpy/monitor/WtBtMon.py)、[BacktestTemplates.py](wtpy/monitor/BacktestTemplates.py) |
| Plus 任务隔离 | [PlusBacktest.py](wtpy/monitor/PlusBacktest.py) |
| Plus 实盘链路回放 | [PlusReplayRunner.py](wtpy/monitor/PlusReplayRunner.py) |
| 费用及执行参数 | [TradeFinance.py](wtpy/monitor/TradeFinance.py)、[PlusExecution.py](wtpy/monitor/PlusExecution.py) |
| 柜台风险与资金基准 | [TradeRiskApi.py](wtpy/monitor/TradeRiskApi.py) |
| 策略暂停与过滤 | [StrategyRuntime.py](wtpy/StrategyRuntime.py)、[StrategyControl.py](wtpy/monitor/StrategyControl.py) |
| 历史行情与交易时段 | [WorkbenchApi.py](wtpy/monitor/WorkbenchApi.py)、[MarketSessions.py](wtpy/monitor/MarketSessions.py) |
| 前端工作台 | [console/src/components](console/src/components) |

## 边界与不能说的结论

Plus 当前支持 CTA 和 HFT，不支持 Python UFT。
回放不等于交易所真实成交。
资金计算不含保证金优惠、强平和每日结算重置。
告警确认与静默不改变账户持仓。
暂停决策也不会自动撤单或平仓。
历史记录存在，不代表当前柜台连接正常。
回测和监控结果不能证明策略盈利。

原版项目：[wtpy](https://github.com/wondertrader/wtpy)。
配套项目：[WonderTrader-plus](https://github.com/newbigdeng/WonderTrader-plus)。
许可：[MIT](LICENSE)。
