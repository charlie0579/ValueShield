# ValueShield — H股价值投资管家 v2.5

> **设计哲学：算法为辅，主观为主；数据本地化；移动端优先。**
>
> 用户画像：A/H 股价值投资者，遵循格雷厄姆"烟蒂股"策略，管理 20+ 只标的。  
> 核心痛点：港股通无法自动网格交易，需通过软件实时监控，辅助用户在手机端（同花顺/银河）完成手动"分批买入、分批卖出"的闭环。

---

## 一、核心功能（v2.4 现状）

### 1. 持仓录入极简化（PositionSummary）
- 直接录入 **总股数 / 均价 / 底仓股数 / 计划总预算**，无需按格子逐笔记录
- 底仓锁定：`core_shares` 设定后，卖出信号自动屏蔽底仓部分（"底仓保护模式"）
- 波段仓位 = `total_shares − core_shares`；为 0 时卖出提醒静默
- Web 看板"一键对齐账户"功能，3 秒同步实际持仓

### 2. 总预算与现金流风控
- 每只股票配置 `total_budget`（计划总投入上限）
- 风险资金需求 = `max(0, total_budget − market_value)`，预算用完自动归零
- 首页实时展示预算进度条（已投入 % / 总预算）
- 全仓汇总：`compute_total_risk_capital_v2` 按预算公式累加，无预算时退化至旧网格逻辑

### 3. 5-10 年估值锚点插件
- **PB 历史分位**：通过百度股市通接口拉取近 5-10 年市净率序列，实时计算当前分位
  - PB > 80 分位 → 买入提醒**自动熔断**，避免在高估值追买
- **股息率历史分位**：按年聚合历史派息数据，计算 DY 分位
  - DY > 80 分位（高息）→ 卖出提醒**自动钝化**（上移 5%），避免在高息期割肉
- 估值标签 emoji 体系：🚀极度低估 / 📈低估 / ⚖️合理 / 📉偏高 / 🔴高估
- 历史数据独立刷新（不在主监控循环中），可在 UI 手动触发

### 4. Watcher 观察者模式（零持仓监控）
- 未持仓标的配置为 `watchers`，设定**安全边际价（base_price）**
- 现价 ≤ 建仓价时触发 Bark 推送（group=`ValueShield-Watch`）
- Web 看板侧边栏单独分组显示，展示距建仓价的偏离 %
- "一键转正"功能：观察者转换为持仓后自动初始化 `PositionSummary`

### 5. 智能监控与时间管理
- 仅在 **周一至周五 09:15–12:00、13:00–16:10** 轮询行情
- 自动识别中国法定节假日，进入静默模式（基于 `chinese_calendar` 库）
- 默认每 30 秒轮询一次（`config.json` 可调）

### 6. 三通道行情获取（v2.1 保留）
- **通道 A**：AkShare `stock_hk_spot_em`（东方财富数据源）
- **通道 B**：新浪财经 `hq.sinajs.cn/list=hkXXXXX`（快熔断备用）
- **通道 C**：东方财富 Web `push2.eastmoney.com`（终极兜底）
- 20% 偏差校验：三通道结果相互验证，异常时记录警告 + 保留上次有效价

### 7. Web 看板（同花顺风格）
- 侧边栏双分组：📊 **当前持仓**（显示浮盈 %）+ 🔍 **观察名单**（显示距建仓价 %）
- 圆角卡片布局，支持深色/浅色主题
- 持仓详情：PositionSummary 5 列卡片（总股 / 均价 / 现价 / 浮盈 / 市值）+ 预算进度条
- 估值分位标签（PB / 股息率 emoji 实时显示）
- 配置页"👤 一键对齐账户"expander，手动同步真实持仓

### 8. ✨ 神奇公式全市场扫描器（v2.5 新增）
- **格林布拉特双因子模型**：ROC（资本回报率）+ EY（盈利收益率）综合排名
  - $ROC = EBIT / (净营运资本 + 净固定资产)$
  - $EY = EBIT / EV$（EV = 市值 + 净负债）
- **全市场覆盖**：A 股（沪深主板/创业板/科创板）+ H 股（按市值过滤）
- **行业过滤**：自动剔除金融类股票（银行/保险/证券/信托/多元金融）
- **双排名机制**：ROC 排名 + EY 排名 → 综合排名最低 30 只
- **A 股**：使用实际财报数据（资产负债表 + 利润表，data_quality="full"）
- **H 股**：优先使用财报接口，失败时退化为 PE/PB 近似（data_quality="approx"）
- **AH 折价标识**：H 股相对 A 股折价率实时显示
- **日缓存机制**：每日盘前自动扫描一次（`maybe_refresh_magic_formula`），结果缓存 18 小时
- **UI 交互**：
  - 🔄 手动触发"重新扫描全市场"（带进度条）
  - ➕ 一键将标的加入 `watchers` 观察名单（建仓价 = 九折）
  - 📋 复制财务摘要（可粘贴给 Gemini 进行 6+2 深度主观分析）


---

## 二、技术架构

```
ValueShield/
├── app.py          # Streamlit Web 看板（主入口，同花顺风格 v2.4）
├── monitor.py      # 后台监控轮询循环（v2.4 信号逻辑 + Watcher）
├── engine.py       # 核心算法引擎（GridEngine + PositionSummary + WatcherTarget）
├── crawler.py      # 三通道行情获取 + 估值历史（PB / 股息率分位）
├── magic_formula.py# 神奇公式全市场扫描器（ROC + EY 双因子，A+H 股）
├── notifier.py     # Bark API 推送（买入 / 卖出 / 风险预警 / 建仓机会）
├── config.json     # 静态配置（标的参数、Bark Token、watchers 列表）
├── state.json      # 实时持仓与网格状态（原子写入）
├── requirements.txt
└── tests/
    ├── ut/         # 单元测试（251 个，engine 100% / notifier 100% 覆盖）
    └── sct/        # 场景测试（端到端完整交易流程）
```

**技术栈：** Python 3.12 · AkShare · Streamlit · Requests · chinese_calendar  
**数据安全：** `state.json` 采用 `.tmp` + `os.replace()` 原子写入，防止掉电丢失状态

---

## 三、配置文件示例

### config.json（持仓 + 观察者）

```json
{
  "bark_token": "YOUR_BARK_TOKEN",
  "web_server_url": "http://192.168.0.69:8502",
  "cash_reserve": 200000,
  "poll_interval": 30,
  "stocks": [
    {
      "code": "01336",
      "name": "新华保险",
      "akshare_code": "01336",
      "base_price": 28.5,
      "hist_min": 14.0,
      "annual_dividend_hkd": 1.80,
      "lot_size": 500,
      "grid_count": 20,
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
| `total_budget` | 计划总投入上限（HKD），用于风险资金计算 |
| `watchers` | 零持仓监控列表，现价 ≤ `base_price` 时推送建仓提醒 |

---

## 四、标的参数参考

| 标的 | 代码 | Base | Hist_Min | Step（自动） | 每手 | 计划预算 |
|------|------|------|----------|-------------|------|---------|
| 新华保险 | 01336.HK | 28.50 | 14.00 | **0.7250** | 500股 | 300,000 |
| 广深铁路 | 00525.HK | 4.50 | 2.20 | **0.1150** | 1000股 | 100,000 |
| 盈富基金（观察）| 02800.HK | — | — | — | — | 100,000 |

---

## 五、快速启动

```bash
# 安装依赖
pip install -r requirements.txt

# 方式A：仅 Web 看板（手动刷新）
streamlit run app.py --server.port 8502

# 方式B：后台监控 + Web 看板（完整模式）
nohup python3.12 monitor.py > monitor.log 2>&1 &
streamlit run app.py --server.port 8502 --server.address 0.0.0.0

# 运行全套单元测试
python3.12 -m pytest tests/ -q
```

**首次配置：** 编辑 `config.json`，填入 `bark_token` 和 `web_server_url`（局域网 IP）。

---

## 六、测试覆盖率

```
engine.py    100%   GridEngine / PositionSummary / WatcherTarget 全覆盖
notifier.py  100%   买入 / 卖出 / 风险预警 / 建仓机会推送全覆盖
monitor.py    96%   主监控循环、Watcher 循环、估值历史刷新
crawler.py    94%   三通道行情 + 估值分位函数
───────────────────────────────
TOTAL: 320 tests passed (+69 神奇公式)
```

---

## 七、后续改进方向

### 🔧 功能层面
- [ ] **PB 实时获取**：目前 PB 熔断依赖历史缓存（`refresh_valuation_history`），可增加实时 PB 抓取触发熔断
- [ ] **Watcher 推送去重**：建议加 1 小时 cooldown，避免同一机会重复推送
- [ ] **历史成交记录**：卖出后持久化到 SQLite 并展示收益曲线
- [ ] **多账户支持**：区分不同券商账户的持仓

### 📊 算法层面
- [ ] **非均匀网格**：价格越低格距越小（加密底部）
- [ ] **动态 Base 自动跟踪**：股价长期上涨后 Base 自动上移
- [ ] **移动止盈**：跌破某价格止盈（动态追踪）

### ⚙️ 部署层面
- [ ] **Docker 化**：提供 Dockerfile，一键部署到云服务器
- [ ] **HTTPS**：通过 nginx 反向代理 + Let's Encrypt 实现外网安全访问
- [ ] **Telegram 推送**：作为 Bark 的备用方案（Bark 仅支持 iOS）
