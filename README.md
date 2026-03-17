# ValueShield H股价值投资助手 v1.0

## 项目结构

```
ValueShield/
├── app.py            # Streamlit Web 看板（主入口）
├── monitor.py        # 后台监控轮询循环
├── engine.py         # 网格算法引擎
├── crawler.py        # AkShare 数据获取（含重试）
├── notifier.py       # Bark 推送模块
├── config.json       # 静态配置（标的参数、Bark Token 等）
├── state.json        # 实时持仓与网格状态（自动维护）
└── requirements.txt  # Python 依赖
```

## 环境准备

```bash
# Python 3.12+（支持 Mac M5 Pro 及 Linux）
pip install -r requirements.txt
```

## 配置步骤

1. 编辑 `config.json`，填入你的 **Bark Token**：
   ```json
   "bark_token": "你的Token"
   ```
2. 按需修改 `web_server_url`（局域网访问时改为服务器 IP）：
   ```json
   "web_server_url": "http://192.168.1.100:8501"
   ```
3. 按需调整每只股票的 `base_price`、`hist_min`、`lot_size`、`annual_dividend_hkd`。

### 初始参数说明

| 标的 | Base | Min | Step (自动) | 每手 |
|------|------|-----|-------------|------|
| 新华保险 01336 | 28.50 | 14.00 | (28.5-14)/20=0.725 | 500 |
| 广深铁路 00525 | 4.50  | 2.20  | (4.5-2.2)/20=0.115  | 1000|

## 启动

### 方式一：纯 Web 看板（手动刷新数据）
```bash
cd /var/fpwork/charlche/gemini/ValueShield
streamlit run app.py --server.port 8501
```

### 方式二：同时启动后台监控 + Web 看板
```bash
# 终端1：后台监控（自动获取行情、推送 Bark）
python monitor.py

# 终端2：Web 看板
streamlit run app.py --server.port 8501
```

### 方式三：Linux 服务器后台运行
```bash
nohup python monitor.py > monitor.log 2>&1 &
streamlit run app.py --server.port 8501 --server.address 0.0.0.0 &
```

## 核心功能

### 🔄 监控逻辑
- 仅在 **周一至周五 09:15-12:00、13:00-16:10** 轮询
- 自动识别中国法定节假日，进入静默模式
- 默认 30 秒轮询一次（可在 config.json 调整）

### 📐 网格算法
- `Step = (Base_Price - Hist_Min) / 20`
- 支持在 Web 界面手动修改 Base 或直接指定 Step
- 修改后自动重算所有格子触发价

### 📱 Bark 推送 + 反馈闭环
1. 价格触发买入/止盈格位 → 推送 Bark 通知（含回调链接）
2. 用户在手机 App 完成操作 → 点击通知链接跳转 Web
3. 在 Web 看板点击【确认成交】→ state.json 立即更新

### ⚠️ 压力测试预警
- 实时计算所有未占用下方格子的资金需求
- 超过 `cash_reserve` 设定时，Web 首页显示红色预警横幅，同时推送 Bark

## 数据安全
- `state.json` 采用**原子写入**（先写 .tmp 再 rename），防止掉电丢失
- 所有 AkShare 网络调用均有 **3次自动重试**

## 影子网格对账
若手动操作与程序记录不一致：
1. 打开 Web 看板 → 展开【手动确认买入（影子网格补录）】
2. 选择对应格子 → 点击【确认买入】完成补录
