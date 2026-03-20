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

    def test_empty_engines_shows_warning_not_crash(self):
        """
        【真实 Bug 回归】首次部署 / state.json 被删除 → engines 为空 {}
        旧代码：engines["01336"] → KeyError，整页白屏
        新代码：engines.get("01336") → None → st.warning 给出引导提示
        """
        patchers = _start_patches(extra={"monitor.build_engines": {}})
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            # 不应有未捕获异常
            assert not at.exception, (
                f"engines 为空时应显示 warning 而非崩溃，实际异常：{at.exception}"
            )
            # 应显示含「引擎」或「monitor」关键字的 warning
            warning_texts = [w.value for w in at.warning]
            assert any(
                kw in text
                for text in warning_texts
                for kw in ("初始化", "monitor", "monitor.py", "引擎", "state.json")
            ), f"engines 为空时未显示引导 warning，实际：{warning_texts!r}"
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



# ── v2.6.1 ROE badge / expander 测试 ─────────────────────────────────────────

class TestROEBadgeAndExpander:
    """ROE 衰减 badge 和 10 年趋势 expander 渲染测试。"""

    # 连续 3 年下跌的 ROE 历史（触发 badge）
    _DECLINING_ROE = [0.30, 0.25, 0.20, 0.15]
    _STABLE_ROE    = [0.28, 0.29, 0.30, 0.31]

    def _state_with_roe(self, roe_list: list) -> dict:
        s = {
            "last_updated": "",
            "positions": {"01336": {"grid_occupied": {}, "holdings": []}},
            "latest_prices": {"01336": 28.5},
            "latest_dividend_ttm": {"01336": 1.8},
            "alerts": [],
            "pending_confirmations": [],
            "valuation_history": {"01336": {"roe": roe_list}},
        }
        return s

    def test_declining_roe_renders_without_exception(self):
        """连续 3 年 ROE 下跌时，页面不崩溃。"""
        roe_info = {
            "stable": False,
            "consecutive_decline": 3,
            "max_drop": 0.50,
            "alert": "ROE 连续 3 年下跌，最大跌幅 50.0%",
        }
        patchers = _start_patches(extra={
            "monitor.load_state": self._state_with_roe(self._DECLINING_ROE),
            "crawler.compute_roe_stability": roe_info,
        })
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            assert not at.exception, f"ROE 衰减时页面崩溃：{at.exception}"
        finally:
            _stop_patches(patchers)

    def test_roe_expander_visible_when_history_present(self):
        """有 ROE 历史数据时，expander label 包含 'ROE' 关键字。"""
        roe_info = {
            "stable": False,
            "consecutive_decline": 3,
            "max_drop": 0.50,
            "alert": "ROE 连续 3 年下跌",
        }
        patchers = _start_patches(extra={
            "monitor.load_state": self._state_with_roe(self._DECLINING_ROE),
            "crawler.compute_roe_stability": roe_info,
        })
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            assert not at.exception
            expander_labels = [e.label for e in at.expander]
            assert any("ROE" in lbl for lbl in expander_labels), (
                f"未找到含 ROE 的 expander，实际 expander：{expander_labels!r}"
            )
        finally:
            _stop_patches(patchers)

    def test_stable_roe_shows_neutral_expander_label(self):
        """ROE 稳定时，expander label 为正向（不含 '衰减'）。"""
        roe_info = {
            "stable": True,
            "consecutive_decline": 0,
            "max_drop": 0.03,
            "alert": "",
        }
        patchers = _start_patches(extra={
            "monitor.load_state": self._state_with_roe(self._STABLE_ROE),
            "crawler.compute_roe_stability": roe_info,
        })
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            assert not at.exception
            expander_labels = [e.label for e in at.expander]
            roe_expanders = [lbl for lbl in expander_labels if "ROE" in lbl]
            assert roe_expanders, "ROE 历史存在时应有 ROE expander"
            assert not any("衰减" in lbl for lbl in roe_expanders), (
                f"ROE 稳定时 expander label 不应含'衰减'，实际：{roe_expanders!r}"
            )
        finally:
            _stop_patches(patchers)

    def test_no_roe_history_no_roe_expander(self):
        """valuation_history 无 roe 键时，不应出现 ROE expander（回归保护）。"""
        patchers = _start_patches()  # _MINIMAL_STATE 无 roe 字段
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            assert not at.exception
            expander_labels = [e.label for e in at.expander]
            assert not any("ROE" in lbl for lbl in expander_labels), (
                f"无 ROE 数据时不应渲染 ROE expander，实际：{expander_labels!r}"
            )
        finally:
            _stop_patches(patchers)


# ── v2.6.1 DCF 财务摘要文本测试 ──────────────────────────────────────────────

class TestDCFInMagicFormulaSummary:
    """神奇公式股票卡片中 DCF 估值行的渲染测试。"""

    _DCF_RESULT = {
        "cf_avg": 58.8,
        "dcf_total": 1234.5,
        "years": 3,
        "note": "g=5% r=10%",
    }

    def _patchers_with_fresh_cache(self, dcf_return_value) -> list:
        return _start_patches(extra={
            "magic_formula.load_cache": _MOCK_CACHE_FRESH,
            "magic_formula.is_cache_fresh": True,
            "crawler.compute_dcf_value": dcf_return_value,
        })

    def test_dcf_with_mock_data_no_exception(self):
        """有现金流数据时，DCF 行渲染不崩溃。"""
        patchers = self._patchers_with_fresh_cache(self._DCF_RESULT)
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            at.sidebar.radio[0].set_value("✨ 市场发现")
            at.run()
            assert not at.exception, f"DCF 有数据时页面崩溃：{at.exception}"
        finally:
            _stop_patches(patchers)

    def test_dcf_none_still_renders_gracefully(self):
        """无现金流数据（DCF 返回 None）时，页面不崩溃，显示兜底文本。"""
        patchers = self._patchers_with_fresh_cache(None)
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            at.sidebar.radio[0].set_value("✨ 市场发现")
            at.run()
            assert not at.exception, f"DCF 为 None 时页面崩溃：{at.exception}"
        finally:
            _stop_patches(patchers)

    def test_dcf_line_appears_in_code_block(self):
        """st.code 摘要块中包含 'DCF' 字样（数据存在时）。"""
        patchers = self._patchers_with_fresh_cache(self._DCF_RESULT)
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            at.sidebar.radio[0].set_value("✨ 市场发现")
            at.run()
            assert not at.exception
            code_texts = [c.value for c in at.code]
            assert any("DCF" in t for t in code_texts), (
                f"st.code 中未找到 DCF 字样，实际 code blocks：{code_texts!r}"
            )
        finally:
            _stop_patches(patchers)

    def test_dcf_fallback_text_when_no_cashflow(self):
        """无现金流时，st.code 中包含兜底文案 '暂无现金流'。"""
        patchers = self._patchers_with_fresh_cache(None)
        try:
            at = AppTest.from_file(APP_PY, default_timeout=30)
            at.run()
            at.sidebar.radio[0].set_value("✨ 市场发现")
            at.run()
            assert not at.exception
            code_texts = [c.value for c in at.code]
            assert any("暂无现金流" in t for t in code_texts), (
                f"无现金流时兜底文案缺失，实际 code blocks：{code_texts!r}"
            )
        finally:
            _stop_patches(patchers)
