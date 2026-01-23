"""
通知服务模块

使用 Apprise 库发送通知到各种平台（Telegram, Discord, Slack 等）。
支持异步发送，避免阻塞主循环。

使用示例:
    # 在 AppCore 中
    await self.notify.send("余额不足", "BTC 余额低于阈值")

    # 或者使用便捷方法
    await self.notify.error("转账失败", "USDT 转账超时未到账")
    await self.notify.warning("价格异常", "BTC 价格波动超过 5%")
"""
import asyncio
# pylint: disable=import-outside-toplevel
import logging
from typing import TYPE_CHECKING, Optional

from ...plugin import pm

if TYPE_CHECKING:
    from .base import AppCore

logger = logging.getLogger(__name__)


class NotifyService:
    """
    通知服务

    封装 Apprise 库，提供异步通知功能。
    支持多个通知渠道同时发送。

    Apprise URL 格式示例:
    - Telegram: tgram://bottoken/ChatID
    - Discord: discord://WebhookID/WebhookToken
    - Slack: slack://TokenA/TokenB/TokenC/#channel
    - Email: mailto://user:pass@gmail.com
    - 更多: https://github.com/caronc/apprise/wiki
    """

    def __init__(self, app: "AppCore"):
        self._app = app
        self._apprise = None
        self._initialized = False

    def _ensure_initialized(self) -> bool:
        """延迟初始化 Apprise 实例"""
        if self._initialized:
            return self._apprise is not None

        self._initialized = True
        notify_urls = self._app.config.notify_urls

        if not notify_urls:
            logger.debug("No notify_urls configured, notifications disabled")
            return False

        try:
            import apprise
            self._apprise = apprise.Apprise()

            for url in notify_urls:
                if self._apprise.add(url):
                    logger.debug("Added notification URL: %s...", url[:30])
                else:
                    logger.warning("Failed to add notification URL: %s...", url[:30])

            if len(self._apprise) == 0:
                logger.warning("No valid notification URLs configured")
                self._apprise = None
                return False

            logger.info("Notification service initialized with %d channels", len(self._apprise))
            return True

        except ImportError:
            logger.warning("apprise library not installed, notifications disabled")
            return False
        except Exception as e:
            logger.exception("Failed to initialize notification service: %s", e)
            return False

    @property
    def enabled(self) -> bool:
        """通知服务是否启用"""
        return self._ensure_initialized()

    async def send(
        self,
        title: str,
        body: str,
        notify_type: Optional[str] = None,
    ) -> bool:
        """
        发送通知

        Args:
            title: 通知标题
            body: 通知内容
            notify_type: 通知类型 ('info', 'success', 'warning', 'failure')

        Returns:
            是否发送成功
        """
        # 插件钩子：发送通知
        level = notify_type or 'info'
        pm.hook.on_notify(level=level, title=title, message=body)

        if not self._ensure_initialized():
            return False

        try:
            import apprise

            # 映射通知类型
            type_map = {
                'info': apprise.NotifyType.INFO,
                'success': apprise.NotifyType.SUCCESS,
                'warning': apprise.NotifyType.WARNING,
                'failure': apprise.NotifyType.FAILURE,
                'error': apprise.NotifyType.FAILURE,
            }
            apprise_type = type_map.get(notify_type, apprise.NotifyType.INFO)

            # 添加应用标识
            app_name = self._app.config.path
            full_title = f"[{app_name}] {title}"

            # 异步发送
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self._apprise.notify(
                    title=full_title,
                    body=body,
                    notify_type=apprise_type,
                )
            )

            if result:
                logger.debug("Notification sent: %s", title)
            else:
                logger.warning("Failed to send notification: %s", title)

            return result

        except Exception as e:
            logger.exception("Error sending notification: %s", e)
            return False

    async def info(self, title: str, body: str) -> bool:
        """发送信息通知"""
        return await self.send(title, body, 'info')

    async def success(self, title: str, body: str) -> bool:
        """发送成功通知"""
        return await self.send(title, body, 'success')

    async def warning(self, title: str, body: str) -> bool:
        """发送警告通知"""
        return await self.send(title, body, 'warning')

    async def error(self, title: str, body: str) -> bool:
        """发送错误通知"""
        return await self.send(title, body, 'failure')

    # 便捷方法：常见场景

    async def notify_insufficient_balance(
        self,
        exchange: str,
        currency: str,
        available: float,
        required: float,
    ) -> bool:
        """通知余额不足"""
        return await self.error(
            "余额不足",
            f"交易所: {exchange}\n"
            f"币种: {currency}\n"
            f"可用: {available:.6f}\n"
            f"需要: {required:.6f}\n"
            f"缺口: {required - available:.6f}"
        )

    async def notify_deposit_timeout(
        self,
        from_exchange: str,
        to_exchange: str,
        currency: str,
        amount: float,
        withdraw_id: str,
        timeout: float,
    ) -> bool:
        """通知转账超时未到账"""
        return await self.error(
            "转账超时",
            f"从: {from_exchange}\n"
            f"到: {to_exchange}\n"
            f"币种: {currency}\n"
            f"数量: {amount:.6f}\n"
            f"提币ID: {withdraw_id}\n"
            f"已等待: {timeout:.0f}秒"
        )

    async def notify_deposit_success(
        self,
        from_exchange: str,
        to_exchange: str,
        currency: str,
        amount: float,
        received: float,
    ) -> bool:
        """通知转账成功"""
        return await self.success(
            "转账成功",
            f"从: {from_exchange}\n"
            f"到: {to_exchange}\n"
            f"币种: {currency}\n"
            f"发送: {amount:.6f}\n"
            f"到账: {received:.6f}"
        )

    async def notify_exchange_error(
        self,
        exchange: str,
        operation: str,
        error: str,
    ) -> bool:
        """通知交易所操作错误"""
        return await self.error(
            f"交易所错误: {operation}",
            f"交易所: {exchange}\n"
            f"操作: {operation}\n"
            f"错误: {error}"
        )
