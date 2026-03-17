"""
conftest.py - 全局测试夹具
提供标准化的 GridEngine 实例、config 字典、state 字典等复用夹具。
"""
import json
import os
import sys
import tempfile
import pytest

# 将项目根目录加入 sys.path，确保可以 import engine, crawler 等
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import GridEngine, Holding


# ── 基础参数常量 ──────────────────────────────────────────────────────────────
STOCK_1336 = dict(
    code="01336",
    name="新华保险",
    base_price=28.5,
    hist_min=14.0,
    lot_size=500,
    grid_levels=20,
    take_profit_pct=0.07,
)

STOCK_0525 = dict(
    code="00525",
    name="广深铁路",
    base_price=4.5,
    hist_min=2.2,
    lot_size=1000,
    grid_levels=20,
    take_profit_pct=0.07,
)


@pytest.fixture
def engine_1336() -> GridEngine:
    """新华保险网格引擎（干净状态）。"""
    return GridEngine(**STOCK_1336)


@pytest.fixture
def engine_0525() -> GridEngine:
    """广深铁路网格引擎（干净状态）。"""
    return GridEngine(**STOCK_0525)


@pytest.fixture
def engine_with_holdings(engine_1336: GridEngine) -> GridEngine:
    """新华保险引擎，预先买入第 0、2、5 格。"""
    engine_1336.confirm_buy(0)
    engine_1336.confirm_buy(2)
    engine_1336.confirm_buy(5)
    return engine_1336


@pytest.fixture
def sample_config(tmp_path) -> dict:
    """标准 config 字典，config.json 写入临时目录。"""
    cfg = {
        "settings": {
            "poll_interval_seconds": 30,
            "web_server_url": "http://localhost:8501",
            "bark_api_url": "https://api.day.app",
            "bark_token": "TEST_TOKEN",
            "cash_reserve": 100000.0,
            "lot_size_default": 500,
            "grid_levels": 20,
            "default_take_profit_pct": 0.07,
        },
        "stocks": [
            {
                "code": "01336",
                "name": "新华保险",
                "exchange": "HK",
                "akshare_code": "01336",
                "base_price": 28.5,
                "hist_min": 14.0,
                "lot_size": 500,
                "step": None,
                "take_profit_pct": 0.07,
                "enabled": True,
                "annual_dividend_hkd": 1.8,
            },
            {
                "code": "00525",
                "name": "广深铁路",
                "exchange": "HK",
                "akshare_code": "00525",
                "base_price": 4.5,
                "hist_min": 2.2,
                "lot_size": 1000,
                "step": None,
                "take_profit_pct": 0.07,
                "enabled": True,
                "annual_dividend_hkd": 0.22,
            },
        ],
    }
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    return cfg


@pytest.fixture
def sample_state() -> dict:
    """干净的 state 字典。"""
    return {
        "last_updated": "",
        "positions": {
            "01336": {"grid_occupied": {}, "holdings": []},
            "00525": {"grid_occupied": {}, "holdings": []},
        },
        "latest_prices": {},
        "latest_dividend_ttm": {},
        "alerts": [],
    }


@pytest.fixture
def tmp_state_path(tmp_path) -> str:
    """在临时目录创建 state.json，返回路径字符串。"""
    state_data = {
        "last_updated": "",
        "positions": {
            "01336": {"grid_occupied": {}, "holdings": []},
            "00525": {"grid_occupied": {}, "holdings": []},
        },
        "latest_prices": {},
        "latest_dividend_ttm": {},
        "alerts": [],
    }
    p = tmp_path / "state.json"
    p.write_text(json.dumps(state_data, ensure_ascii=False))
    return str(p)
