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
默认连接 SimNow 第二套仿真行情前置。

```bash
python -m pip install openctp-ctp==6.7.11.0
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

用 CTP 合约加载器导出商品期权合约资料。
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

## 边界与不能说的结论

- 若采集器没运行，临时看盘不会补出历史 Tick。
- 币对逐笔成交没有买卖盘口，仅支持分钟线回放。
- 商品期权回放仅支持到期前的价格和成交研究。
- 通用回放不计算提前行权、指派或卖方保证金。
- 股票和币对看盘成功，不能证明交易柜台已接通。
