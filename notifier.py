"""
notifier.py - 消息推送模块
通过 Bark API 向 iOS 发送买入/卖出/风险预警通知，包含 Web 回调链接。
"""

import logging
import requests

logger = logging.getLogger(__name__)

TIMEOUT_SECONDS = 8


class BarkNotifier:
    """Bark 推送客户端。"""

    def __init__(self, bark_url: str, bark_token: str, web_server_url: str):
        """
        bark_url: Bark 服务地址，如 https://api.day.app
        bark_token: Bark 设备 Token
        web_server_url: 本地 Web 服务地址，如 http://localhost:8501
        """
        self.bark_url = bark_url.rstrip("/")
        self.bark_token = bark_token
        self.web_server_url = web_server_url.rstrip("/")

    def _send(self, title: str, body: str, url: str = "", group: str = "ValueShield") -> bool:
        """底层发送函数，失败时记录日志但不抛出异常。"""
        if not self.bark_token or self.bark_token == "YOUR_BARK_TOKEN_HERE":
            logger.warning("Bark Token 未配置，跳过推送: %s", title)
            return False
        endpoint = f"{self.bark_url}/{self.bark_token}"
        payload = {
            "title": title,
            "body": body,
            "group": group,
            "sound": "minuet",
            "badge": 1,
            "isArchive": 1,
        }
        if url:
            payload["url"] = url
        try:
            resp = requests.post(endpoint, json=payload, timeout=TIMEOUT_SECONDS)
            resp.raise_for_status()
            logger.info("Bark 推送成功: %s", title)
            return True
        except Exception as exc:
            logger.error("Bark 推送失败: %s -> %s", title, exc)
            return False

    def notify_buy(
        self,
        code: str,
        name: str,
        current_price: float,
        dividend_yield: float,
        grid_level: int,
        grid_price: float,
        holding_id: str = "",
    ) -> bool:
        """发送买入提醒。"""
        title = f"[操作建议] {name}"
        body = f"买入 @ {grid_price:.3f} HKD"
        callback_url = (
            f"{self.web_server_url}?action=confirm_buy"
            f"&code={code}&level={grid_level}&holding_id={holding_id}"
        )
        return self._send(title, body, url=callback_url)

    def notify_sell(
        self,
        code: str,
        name: str,
        current_price: float,
        dividend_yield: float,
        grid_level: int,
        buy_price: float,
        profit_pct: float,
        holding_id: str = "",
    ) -> bool:
        """发送止盈提醒。"""
        title = f"[操作建议] {name}"
        body = f"止盈 @ {current_price:.3f} HKD"
        callback_url = (
            f"{self.web_server_url}?action=confirm_sell"
            f"&code={code}&holding_id={holding_id}"
        )
        return self._send(title, body, url=callback_url)

    def notify_risk_warning(
        self,
        total_risk: float,
        cash_reserve: float,
        details: list[dict],
    ) -> bool:
        """发送现金风险预警。"""
        title = "⚠️ [风险预警] 资金不足"
        detail_lines = "\n".join(
            f"  {d['name']}({d['code']}): {d['risk']:.0f} HKD" for d in details
        )
        body = (
            f"总风险资金需求：{total_risk:,.0f} HKD\n"
            f"现金预留设定：{cash_reserve:,.0f} HKD\n"
            f"超出金额：{total_risk - cash_reserve:,.0f} HKD\n\n"
            f"各标的剩余风险：\n{detail_lines}"
        )
        callback_url = f"{self.web_server_url}?action=risk_warning"
        return self._send(title, body, url=callback_url, group="ValueShield-Risk")
