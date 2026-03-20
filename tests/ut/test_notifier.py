"""
tests/ut/test_notifier.py - notifier.py 单元测试
Mock requests.post，验证推送格式、URL 拼接、Token 缺失时的静默逻辑。
"""
import pytest
from unittest.mock import patch, MagicMock

from notifier import BarkNotifier


@pytest.fixture
def notifier() -> BarkNotifier:
    return BarkNotifier(
        bark_url="https://api.day.app",
        bark_token="TESTTOKEN123",
        web_server_url="http://localhost:8501",
    )


@pytest.fixture
def notifier_no_token() -> BarkNotifier:
    return BarkNotifier(
        bark_url="https://api.day.app",
        bark_token="YOUR_BARK_TOKEN_HERE",  # 默认未配置值
        web_server_url="http://localhost:8501",
    )


# ─────────────────────────────────────────────────────────────────────────────
# BarkNotifier._send 内部逻辑
# ─────────────────────────────────────────────────────────────────────────────
class TestBarkNotifierSend:
    def test_send_success_returns_true(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = notifier._send("title", "body", url="http://x.com")
        assert result is True
        mock_post.assert_called_once()

    def test_send_with_url_included_in_payload(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier._send("t", "b", url="http://callback")
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert payload["url"] == "http://callback"

    def test_send_without_url_omits_key(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier._send("t", "b")
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "url" not in payload

    def test_send_network_error_returns_false(self, notifier: BarkNotifier):
        with patch("requests.post", side_effect=ConnectionError("timeout")):
            result = notifier._send("t", "b")
        assert result is False

    def test_send_skipped_when_token_not_configured(self, notifier_no_token: BarkNotifier):
        with patch("requests.post") as mock_post:
            result = notifier_no_token._send("t", "b")
        assert result is False
        mock_post.assert_not_called()

    def test_send_uses_correct_endpoint(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier._send("t", "b")
        url_called = mock_post.call_args[0][0]
        assert url_called == "https://api.day.app/TESTTOKEN123"


# ─────────────────────────────────────────────────────────────────────────────
# notify_buy
# ─────────────────────────────────────────────────────────────────────────────
class TestNotifyBuy:
    def test_title_contains_stock_name  (self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_buy(
                code="01336", name="新华保险",
                current_price=27.5, dividend_yield=0.065,
                grid_level=2, grid_price=27.05,
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "[操作建议]" in payload["title"]
        assert "新华保险" in payload["title"]
        assert "买入" in payload["body"]

    def test_body_contains_grid_price       (self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_buy(
                code="01336", name="新华保险",
                current_price=27.5, dividend_yield=0.065,
                grid_level=2, grid_price=27.05,
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "买入" in payload["body"]
        assert "27.050" in payload["body"]
        assert "HKD" in payload["body"]

    def test_body_uses_grid_price_not_current(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_buy(
                code="01336", name="新华保险",
                current_price=27.5, dividend_yield=0.065,
                grid_level=4, grid_price=25.0,
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "25.000" in payload["body"]
        assert "27.5" not in payload["body"]  # 当前价不在 body 中

    def test_callback_url_contains_code_and_level(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_buy(
                code="01336", name="新华保险",
                current_price=27.5, dividend_yield=0.065,
                grid_level=3, grid_price=25.5,
                holding_id="abc123",
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "code=01336" in payload["url"]
        assert "level=3" in payload["url"]
        assert "holding_id=abc123" in payload["url"]
        assert "confirm_buy" in payload["url"]


# ─────────────────────────────────────────────────────────────────────────────
# notify_sell
# ─────────────────────────────────────────────────────────────────────────────
class TestNotifySell:
    def test_title_sell_contains_stock_name  (self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_sell(
                code="01336", name="新华保险",
                current_price=30.0, dividend_yield=0.06,
                grid_level=1, buy_price=27.5,
                profit_pct=0.0909,
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "[操作建议]" in payload["title"]
        assert "新华保险" in payload["title"]
        assert "止盈" in payload["body"]

    def test_body_sell_contains_current_price(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_sell(
                code="01336", name="新华保险",
                current_price=30.0, dividend_yield=0.06,
                grid_level=1, buy_price=27.5,
                profit_pct=0.09,
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "止盈" in payload["body"]
        assert "30.000" in payload["body"]
        assert "HKD" in payload["body"]

    def test_callback_url_contains_confirm_sell(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_sell(
                code="00525", name="广深铁路",
                current_price=4.8, dividend_yield=0.046,
                grid_level=0, buy_price=4.5,
                profit_pct=0.066,
                holding_id="xyz789",
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "confirm_sell" in payload["url"]
        assert "xyz789" in payload["url"]


# ─────────────────────────────────────────────────────────────────────────────
# notify_risk_warning
# ─────────────────────────────────────────────────────────────────────────────
class TestNotifyRiskWarning:
    def test_title_contains_risk_keyword(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_risk_warning(
                total_risk=150000, cash_reserve=100000,
                details=[{"code": "01336", "name": "新华保险", "risk": 80000}],
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "风险" in payload["title"]

    def test_body_shows_excess_amount(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_risk_warning(
                total_risk=150000, cash_reserve=100000,
                details=[],
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "50,000" in payload["body"]

    def test_body_lists_all_stock_details(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_risk_warning(
                total_risk=200000, cash_reserve=100000,
                details=[
                    {"code": "01336", "name": "新华保险", "risk": 120000},
                    {"code": "00525", "name": "广深铁路", "risk": 80000},
                ],
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "新华保险" in payload["body"]
        assert "广深铁路" in payload["body"]



# ─────────────────────────────────────────────────────────────────────────────
# notify_watcher
# ─────────────────────────────────────────────────────────────────────────────
class TestNotifyWatcher:
    def test_title_contains_stock_name(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            result = notifier.notify_watcher(
                code="02800", name="盈富基金",
                current_price=78.5, base_price=80.0,
            )
        assert result is True
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "盈富基金" in payload["title"]

    def test_body_shows_prices(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_watcher(
                code="02800", name="盈富基金",
                current_price=78.5, base_price=80.0,
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert "78.500" in payload["body"]
        assert "80.000" in payload["body"]

    def test_group_is_watcher(self, notifier: BarkNotifier):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        with patch("requests.post", return_value=mock_resp) as mock_post:
            notifier.notify_watcher(
                code="02800", name="盈富基金",
                current_price=78.5, base_price=80.0,
            )
        payload = mock_post.call_args.kwargs.get("json") or mock_post.call_args[1]["json"]
        assert payload.get("group") == "ValueShield-Watch"

    def test_silent_when_no_token(self, notifier_no_token: BarkNotifier):
        with patch("requests.post") as mock_post:
            result = notifier_no_token.notify_watcher(
                code="02800", name="盈富基金",
                current_price=78.5, base_price=80.0,
            )
        mock_post.assert_not_called()
        assert result is False
