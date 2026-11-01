# 怎样给工作台接入四类行情？

**临时看盘只收当前标的。长期留存另开采集进程。**

## 怎么选标的？

先选资产，再输入交易所代码。

| 页签 | 输入示例 | 网页读取什么 |
| --- | --- | --- |
| 股票 | `SSE.600000` | 腾讯证券的单股快照 |
| 期货 | `SHFE.rb2701` | 普通 CTP 的合约快照 |
| 期权 | `DCE.m2701-C-3000` | 普通 CTP 的商品期权快照 |
| 加密货币 | `BTC-USD` | Coinbase Exchange 的现货快照 |

期货页点「按需看盘」即可输入新合约。
期权页也可按标的期货查询本地合约链。
点击某个行权价后，才会连接行情前置。

股票和币对在上次请求结束五秒后再取价。
CTP 直接推送。切换标的会关闭旧会话。
隐藏标签页会暂停接收，回到页面会重新连接。
盘后请看源端行情时间。

## 看盘服务怎么启动？

股票和加密货币通过 HTTPS 取价。
若服务器无法直连 Coinbase，可设 `WT_CRYPTO_BROWSER=1`。
这样只有当前浏览器会请求所选交易对。
需要服务端出网时，可单独运行只读报价转发器。
`WT_COINBASE_RELAY` 指向其回环地址。
仅转发器持有代理配置。
期货和商品期权需要 CTP 行情 SDK。
默认使用 SimNow 交易时段前置。
休市联调时，可选「7×24 仿真回放」。
两个环境都显示源端交易日。
Linux 行情镜像还需生成厂商使用的中文字符环境。

```bash
# Debian 或 Ubuntu 已安装 locales 时
localedef --no-archive -i zh_CN -f GB18030 zh_CN.GB18030
```

```bash
python -m pip install openctp-ctp==6.7.11.0 QuantLib==1.39
```

可用 `WT_QUOTE_CTP_PYTHON` 指定独立解释器。
行情商另有前置时，设置 `WT_QUOTE_CTP_FRONT`。
需要登录时，在服务环境内设置以下变量。

```text
WT_QUOTE_CTP_BROKER
WT_QUOTE_CTP_USER
WT_QUOTE_CTP_PASSWORD
```

监控主程序已经注册资产接口。
已有监控服务也可旁挂独立进程。

```bash
python -m uvicorn wtpy.monitor.AssetServer:create_app --factory \
  --host 127.0.0.1 --port 8107
```

将 `/mgr/assets/` 转发到该进程，并关闭代理缓冲。
用 `WT_MONITOR_UPSTREAM` 指向原监控服务。
转发应保留 Host、Origin、Cookie 和公开只读标记。
新进程通过原服务检查账户会话。

## 长期订阅的数据放在哪里？

每类资产由自己的采集进程写入。

若某个行情源掉线，其他采集器仍可继续运行。
期货沿用原来的组合、采集器、策略库和回测目录。
新增工作区位于 `WT_ASSET_ROOT` 下。
该变量默认指向监控部署目录的 `assets` 子目录。

```text
assets/
  futures/quotes/       # 按需看期货时的临时会话
  stock/                # 股票
  option/               # 国内商品期权
  crypto/               # Coinbase 现货
```

| 每个工作区的子目录 | 用途 |
| --- | --- |
| `common/` | 合约、品种、时段、日历、费用 |
| `datahub/` | 采集器工作目录与行情配置 |
| `storage/` | 本资产的长期行情 |
| `quotes/` | 临时 CTP 会话，退出后释放 |
| `strategies/` | 本资产的策略源码与版本 |
| `backtests/` | 按账户保存的回测任务与结果 |
| `run/` | 独立模拟策略进程的工作目录 |

在 Linux 上用监控与采集服务共用的数据卷运行。
给每个采集进程明确列出要长期保留的合约。

```bash
export WT_ASSET_ROOT=/var/lib/wt/assets
python -m wtpy.monitor.AssetCollector option DCE.m2701-C-3000
python -m wtpy.monitor.AssetCollector stock SSE.600000 SZSE.000001
python -m wtpy.monitor.AssetCollector crypto BTC-USD ETH-USD
```

股票和期权复用 WT 数据中台的存储器。
它写入 Tick、分钟线，并按时段转储历史。
币对采集器将逐笔成交写入 `storage/matches.sqlite`。
按交易对和成交编号去重，按 UTC 时间聚合分钟线。

## 采集前要准备什么？

股票和期权分别准备自己的基础资料。

- 在 `common/` 放 `commodities.json`、`contracts.json`。
- 同处放 `sessions.json`、`holidays.json`、`fees.json`。
- 在 `datahub/` 放本资产的 `statemonitor.yaml`。
- 在 `datahub/feed.json` 放单个行情通道对象。

商品期权用 `ParserCTP`。股票采集用 `ParserXTP`。
`feed.json` 沿用对应 WT 通道的原有字段。
启动器按命令行合约设置订阅过滤器。
股票 XTP 行情权限由券商提供。

只看合约链时，可导入 OpenCTP 的公开合约字典。
文件只进 `option/reference/`，用于查找合约。

```bash
python -m wtpy.monitor.OptionChain instruments.json
```

用 CTP 合约加载器导出运行策略所需的基础资料。
保留标的期货、看涨看跌、行权价、到期日。
采集配置和凭据只放在本地数据卷。

币对采集器会读取交易所的价格和数量步长。
回测前，按自己的费用设置 `common/fees.json`。

## 如何回放或启动商品期权策略？

在「策略管理」选择期权，再保存策略版本。

打开期权回测页，选择该版本、数据集和日期。
选择分钟线时，CTA（趋势策略引擎）按闭合行情回放。
选择盘口 Tick 时，CTA 可按报价触达撮合。
点「查看结果」可读任务状态和收益摘要。

要联调柜台，先准备专用 SimNow 模拟账号。
将开平规则文件放在 `option/common/actpolicy.yaml`。
下面的第一条命令只校验所选合约和版本。
策略构造函数需接受 `name` 和 `code`。

```bash
python -m wtpy.monitor.CommodityOptionSim DCE.m2701-C-3000 Example 1
# 单独设置 WT_OPTION_SIM_USER、WT_OPTION_SIM_PASSWORD、
# WT_OPTION_SIM_APPID、WT_OPTION_SIM_AUTHCODE 后再启动。
python -m wtpy.monitor.CommodityOptionSim DCE.m2701-C-3000 Example 1 --start
```

启动命令只连接内置的 SimNow 第二套模拟柜台。
首次运行请使用独立的空仓仿真账号。
成交、资金、持仓以该柜台回报为准。

## 怎样分析多个合约？

在合约链勾选要比较的期权，再点「开始分析」。

可以继续搜索其他品种和月份，保留已选合约。
服务用一条行情连接订阅这些期权和标的期货。
显示整条合约目录时不会自动订阅整条链。
关闭页面会释放行情会话和共享快照。

| 屏幕上的值 | 怎样读 |
| --- | --- |
| IV（隐含波动率） | 从期权与期货的双边中价反推 |
| Delta、Gamma | 期货价格变化时的斜率与曲率 |
| Theta | 期限减少一个日历日时的理论价格变化 |
| Vega、Rho | 波动率或年利率增加一个百分点的局部敏感度 |
| 试算敞口 | 每份合约按有符号手数和乘数折算 |

Theta 用期限缩短一天后的价格减去当前价格。
估值日期使用行情交易日，年分数按实际天数除以 365。
页面默认年利率为 2%，可按研究条件修改。
美式模型使用 QuantLib 的 BAW 提前行权近似。
Black‑76 给出欧洲式期货期权参考值。
报价无双边盘口、跨交易日或不同步时，该行不计算。

## 微笑、期限结构与价差怎么看？

先看同一月份的行权价，再比较不同月份。

- 微笑曲线展示同一标的、同一方向的盘口 IV。
- 平值 IV 取期货价格处的行权价插值。
- 看涨与看跌曲面分开，期限方向插值总方差。
- 曲面只覆盖各期限共有的行权价区间。
- 加权差对同执行价的看涨与看跌配对。

配对权重取双方较小的 Vega。图中记录每分钟的差值。
Z 分数只用此前已完成的分钟，最多取 60 个样本。
配对集合改变时重建历史，避免把换合约误当成信号。
10Δ 与 25Δ 按相同的绝对 Delta 比较两侧 IV。

跨品种估值使用 QuantLib 的 Kirk 引擎。
先选两份不同标的的期权，分别取其 IV。
再填写假设相关系数、单位换算倍数和价差行权价。
模拟标的是第一腿期货减去换算后的第二腿期货。
估值期限取所选合约的较早到期日。

## 策略怎样读取分析结果？

分析进程发布完整的一帧，策略按会话号读取。

Linux 进程对同一资产卷使用文件映射共享内存。
更新时替换完整映射。读取方一次只看到一个版本。
读取函数会检查会话环境及快照年龄。

```python
from wtpy.monitor.OptionFrames import read_option_frame

frame = read_option_frame(option_root, session_id, mode="simulation")
usable = [row for row in frame["rows"] if "iv" in row]
```

多合约回测在期权页选择主数据集和附加合约。
先在断网环境验证首根次周期行情的可见时间。
在 `WT_BACKTEST_RUNTIME` 指向的配置中指定原生库。
通过跨周期回放后才启用 `multi_period_replay`。
策略可从 `codes` 读取代码，从 `periods` 读取周期。
历史回测应在当时的行情上重算指标。

独立模拟运行用 `--also` 指定额外期权。
用 `--hedge-underlyings` 加入相应标的期货。
用 `--analysis-session` 把会话号传给策略。
这些参数只选择策略能读取的合约和数据。

## 边界与不能说的结论

- 若采集器没运行，临时看盘不会补出历史 Tick。
- 币对逐笔成交没有买卖盘口，仅支持分钟线回放。
- 商品期权回放仅支持到期前的价格和成交研究。
- 定价近似不等于行权结算。回放未模拟指派或卖方保证金。
- Kirk 估值与曲面差值不能证明多腿组合可以同时成交。
- 股票和币对看盘成功，不能证明交易柜台已接通。
