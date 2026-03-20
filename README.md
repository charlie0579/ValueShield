# ValueShield — A/H 股价值投资管家 v2.5.1

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
- 全仓汇总：`compute_total_risk_capital_v2` 按预算公式累加，无预算时退化至旧网格逻辑

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

### 6. 三通道行情获取

| 优先级 | 数据源 | 说明 |
|--------|--------|------|
| A（首选）| AkShare `stock_hk_spot_em` | 东方财富，覆盖全 |
| B（备用）| 新浪财经 `hq.sinajs.cn` | 快速熔断切换 |
| C（兜底）| 东方财富 Web `push2.eastmoney.com` | 终极保障 |

20% 偏差校验：三通道结果相互验证，异常时记录警告并保留上次有效价。

### 7. Web 看板（同花顺风格）
- **侧边栏双模式切换**（顶部 radio，水平排布）：
  - 📈 **仓位管理**：双分组列表 — 📊 当前持仓（显示浮盈 %）+ 🔍 观察名单（显示距建仓价 %）
  - ✨ **市场发现**：全屏神奇公式看板（独立模式，不与标签页共享层级）
- 圆角卡片布局，支持深色 / 浅色主题切换
- 持仓详情：5 列卡片（总股 / 均价 / 现价 / 浮盈 / 市值）+ 预算进度条
- 估值分位标签（PB / 股息率 emoji 实时显示）
- 配置页"👤 一键对齐账户"expander，手动同步真实持仓

### 8. ✨ 神奇公式全市场扫描器（v2.5）

格林布拉特双因子模型：ROC（资本回报率）+ EY（盈利收益率）综合排名

```
ROC = EBIT / (净营运资本 + 净固定资产)
EY  = EBIT / EV    （EV = 市值 + 净负债）
```

- **全市场覆盖**：A 股（沪深主板 / 创业板 / 科创板）+ H 股（按市值过滤）
- **行业过滤**：自动剔除银行 / 保险 / 证券 / 信托 / 多元金融
- **双排名机制**：ROC 排名 + EY 排名相加，取综合排名最低 Top 30
- **A 股**：实际财报数据（资产负债表 + 利润表，`data_quality="full"`）
- **H 股**：优先财报接口，失败时退化 PE/PB 近似（`data_quality="approx"`）
- **AH 折价标识**：H 股相对 A 股同名股折价率实时显示
- **日缓存机制**：每日盘前自动扫描（`maybe_refresh_magic_formula`），缓存 18 小时
- **UI 交互**：
  - 🔄 手动"重新扫描全市场"（带实时进度条）
  - ➕ 一键加入 `watchers` 观察名单（建仓价默认 = 九折）
  - 📋 复制财务摘要（可粘贴给 Gemini 进行 6+2 深度主观分析）

---

## 二、技术架构

```
ValueShield/
├── app.py                    # Streamlit Web 看板（v2.5.1，双模式导航）
├── monitor.py                # 后台监控轮询（交易信号 + Watcher + 盘前神奇公式刷新）
├── engine.py                 # 核心算法（GridEngine / PositionSummary / WatcherTarget）
├── crawler.py                # 三通道行情获取 + 估值历史（PB / 股息率分位）
├── magic_formula.py          # 神奇公式扫描器（ROC+EY 双因子，A+H 股，并行抓取）
├── notifier.py               # Bark API 推送（买入 / 卖出 / 风险预警 / 建仓机会）
├── config.json               # 静态配置（标的参数、Bark Token、watchers 列表）
├── state.json                # 实时持仓状态（.tmp + os.replace() 原子写入）
├── magic_formula_cache.json  # 神奇公式扫描缓存（18 小时有效）
├── requirements.txt
└── tests/
    ├── conftest.py           # 全局 fixtures（GridEngine、config、state）
    ├── ut/                   # 单元测试（mock 所有外部 I/O，无网络依赖）
    │   ├── test_app_ui.py        # Streamlit AppTest UI 交互测试（7 个）
    │   ├── test_engine.py        # GridEngine / PositionSummary / WatcherTarget
    │   ├── test_crawler.py       # 三通道行情 + 估值分位函数
    │   ├── test_magic_formula.py # 神奇公式计算逻辑 + 缓存
    │   ├── test_monitor.py       # 监控循环 + 节假日判断
    │   └── test_notifier.py      # Bark 推送各通知类型
    └── smoke/                # 影子数据冒烟测试（需真实网络，手动运行）
        └── test_live_probe.py    # 真实 AkShare 接口验活 + ROC/EY 可算性验证
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
| `watchers` | 零持仓监控列表，现价 ≤ `base_price` 时推送建仓提醒 |

---

## 五、测试体系

### 三层测试策略

| 层次 | 位置 | 运行时机 | 核心价值 |
|------|------|---------|---------|
| **单元测试** | `tests/ut/` | 每次 commit（自动）| mock 所有 I/O，毫秒级，覆盖计算逻辑 |
| **UI 交互测试** | `tests/ut/test_app_ui.py` | 每次 commit（自动）| AppTest 模拟用户点击，防止渲染路径断裂 |
| **影子数据冒烟** | `tests/smoke/` | 手动（有网络时）| 真实 AkShare 接口验活，发现数据格式变更 |

```bash
# 常规 CI（无需网络，327 个测试，约 15s）
python3.12 -m pytest tests/ -q

# AkShare 接口验活（节假日后 / 更新 magic_formula.py 后运行）
python3.12 -m pytest -m smoke tests/smoke/ -v
```

### 测试覆盖率

| 模块 | 覆盖率 | 说明 |
|------|--------|------|
| engine.py | **100%** | GridEngine / PositionSummary / WatcherTarget 全覆盖 |
| notifier.py | **100%** | 买入 / 卖出 / 风险预警 / 建仓机会推送全覆盖 |
| crawler.py | 94% | 三通道行情 + 估值分位函数 |
| magic_formula.py | 91% | ROC/EY 计算、缓存读写、宇宙构建、并行扫描 |
| monitor.py | 89% | 主监控循环、Watcher 循环、节假日判断 |

**327 passed**（16 smoke 测试在 CI 中自动排除，`-m "not smoke"`）

### AppTest 覆盖的核心交互路径

| 测试用例 | 验证内容 |
|---------|---------|
| `test_first_run_has_no_exception` | 首次渲染无任何异常 |
| `test_sidebar_radio_exists_with_two_options` | 侧边栏 radio 含两个导航选项 |
| `test_empty_engines_shows_warning_not_crash` | 首次部署 engines 为空 → st.warning 而非 KeyError 白屏 |
| `test_switch_causes_no_nameerror` | 切换「✨ 市场发现」无 NameError（核心回归） |
| `test_no_cache_shows_warning_with_guidance` | 无缓存时显示扫描引导，而非空白页面 |
| `test_fresh_cache_renders_four_metrics` | 有缓存时渲染 4 个汇总 metric |
| `test_switch_back_to_position_mode_has_no_exception` | 来回切换模式始终无崩溃 |

### 已发现并修复的真实 Bug

| # | 严重度 | 触发场景 | 症状 | 根因 | 修复 commit |
|---|--------|---------|------|------|------------|
| 1 | 🔴 严重 | 点击「✨ 市场发现」 | 整页白屏 | `with tab_magic:` 因上方 `return` 被跳过，神奇公式从不渲染 | b8b6fe3 |
| 2 | 🔴 严重 | 切换「市场发现」模式 | `NameError` | `_render_magic_formula_tab` 定义在 `if __name__==__main__: main()` **之后** | 904d5a4 |
| 3 | 🟡 中等 | 首次部署 / 删除 state.json | `KeyError: '01336'` 整页崩溃 | `engine = engines[code]` 无保护，monitor 未运行时 `engines` 为空 `{}` | 67c3386 |
| 4 | 🟠 较高 | 多次追加导入块 | 模块符号重复注册 | 两个 `from magic_formula import` 并存 | b8b6fe3 |

> **Bug #3 是写 AppTest 时直接发现的**：将 `build_engines` fixture mock 为 `{}`（真实首次部署状态），
> AppTest 立即暴露 `KeyError`。这正是 UI 交互测试相比单纯单元测试的额外价值所在。

---

## 六、标的参数参考

| 标的 | 代码 | Base | Hist_Min | Step（自动）| 每手 | 计划预算（HKD）|
|------|------|------|----------|------------|------|--------------|
| 新华保险 | 01336.HK | 28.50 | 14.00 | 0.7250 | 500 股 | 300,000 |
| 广深铁路 | 00525.HK | 4.50 | 2.20 | 0.1150 | 1000 股 | 100,000 |
| 盈富基金（观察）| 02800.HK | 80.00 | — | — | — | 100,000 |

---

## 七、后续改进方向

### 🔧 功能层面
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
