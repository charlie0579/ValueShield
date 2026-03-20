"""
tests/ut/test_app_ui.py — Streamlit AppTest UI 交互测试

验证目标（无需真实网络）：
  1. 默认「仓位管理」模式下 app 正常渲染，无任何异常
  2. 切换「市场发现」模式后无 NameError / AttributeError
     （根本原因：_render_magic_formula_tab 曾定义在 if __name__==__main__ 之后）
  3. 市场发现模式下无缓存时显示可读的警告/提示文字
  4. 市场发现模式下有新鲜缓存时渲染 metric 汇总行

运行：
    pytest tests/ut/test_app_ui.py -v
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from streamlit.testing.v1 import AppTest

from engine import GridEngine  # 用真实引擎填充 build_engines mock

# ── app.py 绝对路径 ──────────────────────────────────────────────────────────
APP_PY = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "app.py")
)

# ── 最小化 fixtures ───────────────────────────────────────────────────────────
_MINIMAL_CONFIG: dict = {
    "settings": {
        "poll_interval_seconds": 30,
        "web_server_url": "http://localhost:8501",
        "bark_api_url": "https://api.day.app",
        "bark_token": "FAKE_TOKEN",
        "cash_reserve": 100_000.0,
        "lot_size_default": 500,
        "grid_levels": 10,
        "default_take_profit_pct": 0.07,
        "min_holding_limit": 0,
        "max_capital_usage": 0.0,
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
            "step": 2.0,
            "take_profit_pct": 0.07,
            "enabled": True,
            "annual_dividend_hkd": 1.8,
            "total_budget": 300_000.0,
        }
    ],
    "watchers": [],
}

_MINIMAL_STATE: dict = {
    "last_updated": "",
    "positions": {"01336": {"grid_occupied": {}, "holdings": []}},
    "latest_prices": {"01336": 28.5},
    "latest_dividend_ttm": {"01336": 1.8},
    "alerts": [],
    "pending_confirmations": [],
    "valuation_history": {},
}

# 神奇公式有效缓存（1 只 A 股模拟数据）
_MOCK_CACHE_FRESH: dict = {
    "cached_at": "2026-03-20T08:00:00",
    "scanned_count": 120,
    "universe_size": 4800,
    "top_stocks": [
        {
            "code": "600519",
            "name": "贵州茅台",
            "market": "A",
            "price": 1700.0,
            "roc": 0.35,
            "ey": 0.08,
            "ebit": 5e10,
            "ev": 6e11,
            "roc_rank": 1,
            "ey_rank": 15,
            "combined_rank": 16,
            "data_quality": "full",
            "ah_discount_pct": None,
            "industry": "",
            "market_cap": 2_000_000_000_000,
        }
    ],
}


def _make_engines() -> dict:
    """为 MINIMAL_CONFIG 中的每只股票构建真实 GridEngine（空仓状态）。"""
    return {
        "01336": GridEngine(
            code="01336",
            name="新华保险",
            base_price=28.5,
            hist_min=14.0,
            lot_size=500,
            grid_levels=10,
            take_profit_pct=0.07,
        )
    }


def _start_patches(extra: dict | None = None) -> list:
    """
    启动覆盖所有外部 I/O 依赖的 mock patch，返回已 start 的 patcher 列表。
    extra: 覆盖默认 patch 的 mapping，key 为 target 字符串，value 为返回值。
    """
    defaults: dict[str, object] = {
        "monitor.load_config": _MINIMAL_CONFIG,
        "monitor.load_state": _MINIMAL_STATE,
        "monitor.build_engines": _make_engines(),
        "monitor.build_watchers": [],
        "magic_formula.load_cache": None,
        "magic_formula.is_cache_fresh": False,
        "crawler.fetch_realtime_price": 28.5,
        "crawler.fetch_dividend_ttm": 1.8,
        "crawler.fetch_div_yield_history": [],
        "crawler.fetch_pb_history": [],
        "crawler.fetch_stock_name": "新华保险",
    }
    if extra:
        defaults.update(extra)

    patchers = []
    for target, return_val in defaults.items():
        p = patch(target, return_value=return_val)
        p.start()
        patchers.append(p)
    return patchers


def _stop_patches(patchers: list) -> None:
    for p in patchers:
        p.stop()


# ── 测试类 ────────────────────────────────────────────────────────────────────

class TestPositionManagementMode:
    """默认「仓位管理」模式：基础渲染检查。"""

    def test_first_run_has_no_exception(self):
        patchers = _start_patches()
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            assert not at.exception, (
                f"首次渲染异常：{at.exception}"
            )
        finally:
            _stop_patches(patchers)

    def test_sidebar_radio_exists_with_two_options(self):
        patchers = _start_patches()
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            assert not at.exception
            # 侧边栏必须有 radio（导航模式切换器）
            radios = at.sidebar.radio
            assert len(radios) >= 1, "侧边栏缺少模式切换 radio"
            options = radios[0].options
            assert "📈 仓位管理" in options
            assert "✨ 市场发现" in options
        finally:
            _stop_patches(patchers)


class TestDiscoveryModeSwitch:
    """
    切换「市场发现」模式的核心测试。

    历史根因：_render_magic_formula_tab 定义在 if __name__==__main__ 之后，
    导致第二次渲染（用户切换后）触发 NameError。
    """

    def test_switch_causes_no_nameerror(self):
        """切换到「市场发现」后不应出现任何异常，包括 NameError。"""
        patchers = _start_patches()
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            assert not at.exception, f"首次运行异常: {at.exception}"

            # 模拟用户点击「✨ 市场发现」
            at.sidebar.radio[0].set_value("✨ 市场发现")
            at.run()

            assert not at.exception, (
                f"切换「市场发现」后出现异常（可能是 NameError）：{at.exception}"
            )
        finally:
            _stop_patches(patchers)

    def test_no_cache_shows_warning_with_guidance(self):
        """无缓存时应显示含有「扫描」关键字的友好提示，而非空白页面。"""
        patchers = _start_patches()  # load_cache=None, is_cache_fresh=False
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            at.sidebar.radio[0].set_value("✨ 市场发现")
            at.run()

            assert not at.exception

            # 应有 info / warning / success 任意一种包含关键字的文字
            all_texts: list[str] = (
                [e.value for e in at.info]
                + [e.value for e in at.warning]
                + [e.value for e in at.success]
            )
            assert any(
                kw in text
                for text in all_texts
                for kw in ("缓存", "扫描", "数据")
            ), (
                f"无缓存时未显示引导文字，实际文本：{all_texts!r}"
            )
        finally:
            _stop_patches(patchers)

    def test_fresh_cache_renders_four_metrics(self):
        """有新鲜缓存时应渲染 4 个汇总 metric（A股入选/H股入选/平均ROC/平均EY）。"""
        patchers = _start_patches(
            extra={
                "magic_formula.load_cache": _MOCK_CACHE_FRESH,
                "magic_formula.is_cache_fresh": True,
            }
        )
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            at.sidebar.radio[0].set_value("✨ 市场发现")
            at.run()

            assert not at.exception, f"有缓存时渲染异常：{at.exception}"
            assert len(at.metric) >= 4, (
                f"期望至少 4 个 metric（A股入选/H股入选/ROC/EY），实际：{len(at.metric)}"
            )
        finally:
            _stop_patches(patchers)

    def test_switch_back_to_position_mode_has_no_exception(self):
        """来回切换模式后应始终无异常（回归测试）。"""
        patchers = _start_patches()
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            assert not at.exception

            at.sidebar.radio[0].set_value("✨ 市场发现")
            at.run()
            assert not at.exception

            at.sidebar.radio[0].set_value("📈 仓位管理")
            at.run()
            assert not at.exception, (
                f"切回「仓位管理」后出现异常：{at.exception}"
            )
        finally:
            _stop_patches(patchers)
