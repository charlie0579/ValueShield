# ValueShield — A/H 股价值投资管家 v2.6.2

> **设计哲学：算法为辅，主观为主；数据本地化；移动端优先。**
>
> 用户画像：A/H 股价值投资者，遵循格雷厄姆"烟蒂股"策略，管理 20+ 只标的。
> 核心痛点：港股通无法自动网格交易，需通过软件实时监控，辅助用户在手机端（同花顺 / 银河证券）完成手动"分批买入、分批卖出"的闭环。

---

## 一、核心功能

### 1. 持仓录入极简化（PositionSummary）
- 直接录入 **总股数 / 均价 / 底仓股数 / 计划总预算**，无需按格子逐笔记录
- 底仓锁定：`core_shares` 设定后，卖出信号自动屏蔽底仓部分（"底仓保护模式"）
- 波段仓位 = `total_shares - core_shares`；为 0 时卖出提醒静默
- Web 看板"一键对齐账户"功能，3 秒同步实际持仓

### 2. 总预算与现金流风控
- 每只股票配置 `total_budget`（计划总投入上限）
- 风险资金需求 = `max(0, total_budget - market_value)`，预算用完自动归零
- 首页实时展示预算进度条（已投入 % / 总预算）
- 全仓汇总：`compute_total_risk_capital_v2` 按预算公式累加

### 3. 5-10 年估值锚点
- **PB 历史分位**：拉取近 5-10 年市净率序列，实时计算当前分位
  - PB > 80 分位 → 买入提醒**自动熔断**，避免在高估值追买
- **股息率历史分位**：按年聚合历史派息数据，计算 DY 分位
  - DY > 80 分位（高息）→ 卖出提醒**自动钝化**（上移 5%），避免在高息期割肉
- 估值标签 emoji 体系：🚀 极度低估 / 📈 低估 / ⚖️ 合理 / 📉 偏高 / 🔴 高估
- 历史数据独立刷新，不在主监控循环中占用轮询时间

### 4. Watcher 观察者模式（零持仓监控）
- 未持仓标的配置为 `watchers`，设定**安全边际价（base_price）**
- 现价 ≤ 建仓价时触发 Bark 推送（group=`ValueShield-Watch`）
- 侧边栏单独分组显示，展示距建仓价的偏离 %
- "一键转正"：观察者转换为持仓后自动初始化 `PositionSummary`

### 5. 智能监控与时间管理
- 仅在 **周一至周五 09:15-12:00、13:00-16:10** 轮询行情
- 自动识别中国法定节假日，进入静默模式（基于 `chinese_calendar` 库）
- 默认每 30 秒轮询一次（`config.json` 可调）

### 6. 三通道行情获取 + 价格硬拦截（v2.6 ⚡）

| 优先级 | 数据源 | 说明 |
|--------|--------|------|
| A（首选）| AkShare `stock_hk_spot_em` | 东方财富，覆盖全 |
| B（备用）| 新浪财经 `hq.sinajs.cn` | 快速熔断切换 |
| C（兜底）| 东方财富 Web `push2.eastmoney.com` | 终极保障 |

**v2.6 铁甲机制：**
- **20% 偏差校验**：单通道结果偏离上次已知价 > 20% 时，自动触发第三通道（EM Web）交叉验证
- **三通道均发散 → 硬拦截**：若主通道和 EM Web 均超阈值（`_HARD_BLOCK_ON_DIVERGE = True`），`fetch_realtime_price` 返回 `None`，**拒绝写入 state**，防止价格跳空污染仓位计算

### 7. ROE 稳定性监控（v2.6 ⚡）

新增三个函数，支持 ROE 10 年趋势分析：

| 函数 | 作用 |
|------|------|
| `fetch_roe_history(code, years=10)` | 抓取近 N 年年报 ROE，返回升序列表（小数） |
| `_safe_parse_pct(val)` | 将 `"12.34%"` 或 `0.1234` 统一转换为小数，失败返回 `None` |
| `compute_roe_stability(history, ...)` | 分析 ROE 趋势，连续下滑 ≥ 3 年或较峰值下跌 ≥ 20% → `⚠️` 报警 |

`compute_roe_stability` 返回结构：

```python
{
    "stable": bool,             # 无报警时为 True
    "consecutive_decline": int, # 当前连续下滑年数
    "max_drop": float,          # 相对历史峰值的最大跌幅（0.25 = 25%）
    "alert": str,               # "" 或 "⚠️ ROE 已连续 3 年下滑；ROE 较峰值下跌 25%"
}
```

### 8. `trading_mode` 字段（v2.6 ⚡）
- `config.json` 每支股票新增 `"trading_mode": "manual"` | `"auto"` 字段
- `"manual"` 模式（默认）：Web 看板手动补录区顶部显示信息条
  > 🚫 手动模式：系统不执行自动下单，请在券商 APP 操作后使用下方手动补录。
- 为后续自动化交易接口预留标志位

### 9. Web 看板（同花顺风格）
- **侧边栏双模式切换**（顶部 radio，水平排布）：
  - 📈 **仓位管理**：双分组列表 — 📊 当前持仓（显示浮盈 %）+ 🔍 观察名单（显示距建仓价 %）
  - ✨ **市场发现**：全屏神奇公式看板（独立模式，不与标签页共享层级）
- 圆角卡片布局，持仓详情：5 列卡片（总股 / 均价 / 现价 / 浮盈 / 市值）+ 预算进度条
- 估值分位标签（PB / 股息率 emoji 实时显示）
- 配置页"👤 一键对齐账户"expander，手动同步真实持仓

### 10. ✨ 神奇公式全市场扫描器（v2.5）

格林布拉特双因子模型：ROC（资本回报率）+ EY（盈利收益率）综合排名

```
ROC = EBIT / (净营运资本 + 净固定资产)
EY  = EBIT / EV    （EV = 市值 + 净负债）
```

- **全市场覆盖**：A 股（沪深主板 / 创业板 / 科创板）+ H 股（按市值过滤）
- **行业过滤**：自动剔除银行 / 保险 / 证券 / 信托 / 多元金融
- **双排名机制**：ROC 排名 + EY 排名相加，取综合排名最低 Top 30
- **AH 折价标识**：H 股相对 A 股同名股折价率实时显示
- **日缓存机制**：缓存 18 小时，每日盘前自动刷新
- **代理免疫**：ProxyError 自动重试 3 次，第 2 次起切换直连；金融黑名单网络失败时回退 40 只静态兜底
- **扫描断点恢复**：进度写入 session_state，页面刷新后自动显示上次进度
- **UI 交互**：🔄 手动扫描 · ➕ 一键加入 watchers · 📋 复制财务摘要给 Gemini

---

## 二、技术架构

```
ValueShield/
├── app.py                    # Streamlit Web 看板（v2.6.2，双模式导航 + 扫描进度断点恢复）
├── monitor.py                # 后台监控轮询（交易信号 + Watcher + 盘前神奇公式刷新）
├── engine.py                 # 核心算法（GridEngine / PositionSummary / WatcherTarget）
├── crawler.py                # 三通道行情 + 价格硬拦截 + ROE 稳定性 + DCF + 估值历史
├── magic_formula.py          # 神奇公式扫描器（ROC+EY 双因子，A+H 股，代理免疫，静态金融黑名单兜底）
├── notifier.py               # Bark API 推送（买入 / 卖出 / 风险预警 / 建仓机会）
├── config.json               # 静态配置（标的参数含 trading_mode、Bark Token、watchers）
├── state.json                # 实时持仓状态（.tmp + os.replace() 原子写入）
├── magic_formula_cache.json  # 神奇公式扫描缓存（18 小时有效）
├── requirements.txt
└── tests/
    ├── conftest.py
    ├── ut/
    │   ├── test_app_ui.py        # AppTest UI 交互测试（7 个）
    │   ├── test_engine.py        # GridEngine / PositionSummary / WatcherTarget
    │   ├── test_crawler.py       # 三通道 + 价格硬拦截 + ROE + 估值分位
    │   ├── test_magic_formula.py # 神奇公式计算逻辑 + 缓存
    │   ├── test_monitor.py       # 监控循环 + E2E 通知链
    │   └── test_notifier.py      # Bark 推送各通知类型
    └── smoke/
        └── test_live_probe.py    # 真实 AkShare 接口验活（需网络，手动运行）
```

**技术栈：** Python 3.12 · AkShare · Streamlit 1.55 · Requests · chinese_calendar

**数据安全：** `state.json` 采用 `.tmp` + `os.replace()` 原子写入，防止掉电丢失持仓状态

---

## 三、快速启动

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 编辑配置（填入 Bark Token 和局域网 IP）
vim config.json

# 3a. 仅 Web 看板（手动刷新行情）
streamlit run app.py --server.port 8502

# 3b. 完整模式（后台监控 + Web 看板）
nohup python3.12 monitor.py > monitor.log 2>&1 &
streamlit run app.py --server.port 8502 --server.address 0.0.0.0
```

> ⚠️ **首次部署**：需先让 `monitor.py` 完成至少一次轮询，`app.py` 才能正确加载引擎状态。
> 若单独打开 app.py，页面会显示"请先启动 monitor.py"的引导提示，不会崩溃。

---

## 四、配置文件说明

```json
{
  "settings": {
    "bark_token": "YOUR_BARK_TOKEN",
    "web_server_url": "http://192.168.0.69:8502",
    "cash_reserve": 200000,
    "poll_interval_seconds": 30
  },
  "stocks": [
    {
      "code": "01336",
      "name": "新华保险",
      "akshare_code": "01336",
      "base_price": 28.5,
      "hist_min": 14.0,
      "annual_dividend_hkd": 1.80,
      "lot_size": 500,
      "total_budget": 300000.0,
      "trading_mode": "manual",
      "enabled": true
    }
  ],
  "watchers": [
    {
      "code": "02800",
      "name": "盈富基金",
      "akshare_code": "02800",
      "base_price": 80.0,
      "total_budget": 100000.0,
      "enabled": true
    }
  ]
}
```

| 字段 | 含义 |
|------|------|
| `base_price` | 网格基准价（持仓）或建仓触发价（观察者） |
| `hist_min` | 历史最低价，决定网格底部边界 |
| `total_budget` | 计划总投入上限（HKD），用于风险资金计算 |
| `trading_mode` | `"manual"`（默认）：手动模式看板提示；`"auto"`：为未来自动接口预留 |
| `watchers` | 零持仓监控列表，现价 ≤ `base_price` 时推送建仓提醒 |

---

## 五、测试体系

### 三层测试策略

| 层次 | 位置 | 运行时机 | 核心价值 |
|------|------|---------|---------|
| **单元测试** | `tests/ut/` | 每次 commit（自动）| mock 所有 I/O，毫秒级 |
| **UI 交互测试** | `tests/ut/test_app_ui.py` | 每次 commit（自动）| AppTest 防止渲染路径断裂 |
| **影子数据冒烟** | `tests/smoke/` | 手动（见下方运行时机）| 真实 AkShare 接口验活 |

```bash
# 常规 CI（无需网络，370 个测试，约 28s）
python3.12 -m pytest tests/ -q

# AkShare 接口验活（smoke，需真实网络，10 条）
python3.12 -m pytest -m smoke tests/smoke/ -v
```

#### Smoke 测试的运行时机

Smoke 测试（10 条）验证真实 AkShare 网络接口，与 Ollama / 大模型无关，在以下场景**手动触发**：

| 场景 | 原因 |
|------|------|
| 修改 `magic_formula.py` 中的 AkShare 接口调用后 | 验证 A/H 股数据适配未断裂 |
| 收到「数据为空」的用户反馈后 | 定位 AkShare API 是否失效 |
| 节假日后首个交易日 | 验证接口未在假期维护中变更 |
| 定期健康检查（建议每月一次）| 提前发现接口格式变更 |

### 测试覆盖率

| 模块 | 覆盖率 | 说明 |
|------|--------|------|
| engine.py | **100%** | GridEngine / PositionSummary / WatcherTarget 全覆盖 |
| notifier.py | **100%** | 买入 / 卖出 / 风险预警 / 建仓机会推送全覆盖 |
| monitor.py | **89%** | 主监控循环、节假日判断、E2E 通知链 |
| magic_formula.py | **93%** | ROC/EY 计算、缓存读写、并行扫描、代理重试、静态黑名单兜底 |
| crawler.py | **88%** | 三通道行情 + 价格硬拦截 + ROE 稳定性 + DCF + 估值分位 |

**370 passed，10 deselected**（smoke 测试需真实网络，通过 `-m "not smoke"` 自动排除）

### v2.6 新增测试（+21）

| 类 | 文件 | 数 | 覆盖场景 |
|---|---|---|---|
| `TestPriceOutlier` | test_crawler.py | 4 | 三通道均发散硬拦截→None、首次采集接受、EM Web 纠偏、ERROR 日志 |
| `TestROEStability` | test_crawler.py | 8 | 稳定/连续下滑/峰值跌幅/数据不足/`_safe_parse_pct` 解析 |
| `TestPercentileAccuracy` | test_crawler.py | 6 | 中位/极值/低优先/越界截断/数据不足返回 -1 |
| `TestNotificationChain` | test_monitor.py | 3 | E2E：价格触发→pending 写入、无触发、None 不写 state |

### v2.6.2 新增测试（+6）

| 类 | 文件 | 数 | 覆盖场景 |
|---|---|---|---|
| `TestProxyResilientGet` | test_magic_formula.py | 4 | 首次成功、ProxyError 重试+直连切换、全失败抛异常、超时 5 秒 |
| `TestFinancialCodesStaticFallback` | test_magic_formula.py | 2 | 全失败→静态兜底（40 只）、部分成功→用网络数据 |

### v2.6.1 新增测试（+16）

| 类 | 文件 | 数 | 覆盖场景 |
|---|---|---|---|
| `TestDCFValue` | test_crawler.py | 6 | 公式验证、空数据/负值返回 None、自定义参数、years 字段、必要 key |
| `TestFetchOperatingCF` | test_crawler.py | 2 | A 股接口异常回退空列表、空 DataFrame 回退 |
| `TestROEBadgeAndExpander` | test_app_ui.py | 4 | 衰减不崩溃、expander 含 ROE、稳定无"衰减"字样、无历史无 expander |
| `TestDCFInMagicFormulaSummary` | test_app_ui.py | 4 | DCF 有数据不崩溃、None 不崩溃、st.code 含"DCF"、兜底文案"暂无现金流" |

### AppTest 覆盖的核心路径

| 测试用例 | 验证内容 |
|---------|---------|
| `test_first_run_has_no_exception` | 首次渲染无任何异常 |
| `test_sidebar_radio_exists_with_two_options` | 侧边栏 radio 含两个导航选项 |
| `test_empty_engines_shows_warning_not_crash` | 首次部署 engines 空 → st.warning 非 KeyError 白屏 |
| `test_switch_causes_no_nameerror` | 切换「市场发现」无 NameError（核心回归）|
| `test_no_cache_shows_warning_with_guidance` | 无缓存时显示引导 |
| `test_fresh_cache_renders_four_metrics` | 有缓存时渲染 4 个 metric |
| `test_switch_back_to_position_mode_has_no_exception` | 来回切换始终无崩溃 |
| `test_declining_roe_renders_without_exception` | ROE 连续下跌 badge 渲染不崩溃 |
| `test_roe_expander_visible_when_history_present` | 有历史时 expander label 含"ROE" |
| `test_stable_roe_shows_neutral_expander_label` | 稳定时 label 不含"衰减" |
| `test_no_roe_history_no_roe_expander` | 无历史时无 ROE expander（回归保护）|
| `test_dcf_with_mock_data_no_exception` | DCF 有数据渲染不崩溃 |
| `test_dcf_none_still_renders_gracefully` | DCF 返回 None 不崩溃 |
| `test_dcf_line_appears_in_code_block` | st.code 摘要含"DCF"字样 |
| `test_dcf_fallback_text_when_no_cashflow` | 无现金流时含"暂无现金流"兜底文案 |

### 已修复的真实 Bug

| # | 严重度 | 症状 | 根因 | commit |
|---|--------|------|------|--------|
| 1 | 🔴 | 点击「市场发现」整页白屏 | `tab_magic` 因上方 `return` 从不渲染 | b8b6fe3 |
| 2 | 🔴 | 切换模式 `NameError` | `_render_magic_formula_tab` 定义在 `main()` 之后 | 904d5a4 |
| 3 | 🟡 | 首次部署 `KeyError: '01336'` | `engines[code]` 无保护 | 67c3386 |
| 4 | 🟠 | 模块符号重复注册 | 两个 `from magic_formula import` 并存 | b8b6fe3 |

---

## 六、标的参数参考

| 标的 | 代码 | Base | Hist_Min | Step（自动）| 每手 | 计划预算（HKD）|
|------|------|------|----------|------------|------|--------------|
| 新华保险 | 01336.HK | 28.50 | 14.00 | 0.7250 | 500 股 | 300,000 |
| 广深铁路 | 00525.HK | 4.50 | 2.20 | 0.1150 | 1000 股 | 100,000 |
| 盈富基金（观察）| 02800.HK | 80.00 | — | — | — | 100,000 |

---

## 七、版本历史

| 版本 | 核心改动 |
|------|---------|
| **v2.6.2** | 🛡️ 代理免疫力：`_proxy_resilient_get` 3 次重试+直连切换 · 金融黑名单静态兜底 · 扫描进度断点恢复 · smoke 整理 deselected 16→10 · +6 条 UT（370 passed） |
| **v2.6.1** | 🔨 ROE 稳定性 UI 看板（⚠️ badge + 10 年趋势 expander）· DCF 简易估值（Gordon Growth）· monitor ROE 历史写入 · +16 条测试 |
| **v2.6** | 🛡️ 价格硬拦截（三通道均发散→return None）· ROE 稳定性监控（10年趋势+峰值跌幅）· `trading_mode` 字段 · +21 条测试（348 passed） |
| **v2.5.1** | 🔧 双模式导航 Bug 修复 · engines 空值保护 · AppTest 7 条 UI 交互测试 |
| **v2.5** | ✨ 神奇公式全市场扫描器（ROC+EY 双因子，A+H 股并行，日缓存） |
| **v2.4** | 📊 5-10 年估值锚点（PB/DY 历史分位熔断+钝化）· Watcher 观察者模式 |

---

## 八、后续改进方向

### 🔧 功能层面
- [x] **ROE 稳定性看板集成**：badge + 10 年趋势 expander（v2.6.1 ✅）
- [x] **神奇公式代理免疫**：ProxyError 重试 + 直连切换 + 静态黑名单兜底（v2.6.2 ✅）
- [x] **扫描进度断点恢复**：session_state 持久化，刷新不丢进度（v2.6.2 ✅）
- [ ] **PB 实时获取**：目前 PB 熔断依赖历史缓存，可增加实时 PB 触发熔断
- [ ] **Watcher 推送去重**：加 1 小时 cooldown，避免同一机会重复推送
- [ ] **历史成交记录**：卖出后持久化到 SQLite 并展示收益曲线
- [ ] **多账户支持**：区分不同券商账户的持仓

### 📊 算法层面
- [ ] **非均匀网格**：价格越低格距越小（加密底部网格）
- [ ] **动态 Base 自动跟踪**：股价长期上涨后 Base 自动上移
- [ ] **移动止盈**：跌破某价格后触发动态追踪止盈

### ⚙️ 部署层面
- [ ] **Docker 化**：提供 Dockerfile，一键部署到云服务器
- [ ] **HTTPS**：通过 nginx 反向代理 + Let's Encrypt 实现外网安全访问
- [ ] **Telegram 推送**：作为 Bark 的跨平台备用方案
