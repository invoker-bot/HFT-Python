"""
集成测试：真实下单 + 历史记录校验

基于 conf/*/demo 配置，连接 Testnet/Sandbox 执行真实交易验证。

安全机制：
- 唯一开关：INTEGRATION_TEST_ALLOW_LISTS 环境变量控制
- 默认不执行：环境变量未设置或为空时全部跳过
- 强制要求 test: true 配置
- 网络预检：在测试开始前检查网络连通性

分组：
- 0: 交易所 API 级（spot+swap 市价开平 + fetch_my_trades 可见性）
- 1: 限价单校验（far/near 下单参数合理性 + 可查询/可撤单）
- 2: App tick 级（demo app/executor/strategy 组合运行）

运行示例：
- INTEGRATION_TEST_ALLOW_LISTS="0,1" pytest -m integration_test -v -s
- INTEGRATION_TEST_ALLOW_LISTS="*" pytest -m integration_test -v -s

注意：
- 默认不运行（pytest -q 会跳过，见 pytest.ini 的 addopts）
- 需要设置 INTEGRATION_TEST_ALLOW_LISTS 环境变量
- 需要网络连接，否则会明确提示跳过原因
"""
# pylint: disable=import-outside-toplevel,protected-access,too-many-lines
import os
import asyncio
import random
import socket
from pathlib import Path
from glob import glob

import pytest

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent

# ============================================================
# 环境变量配置
# ============================================================

ALLOW_LISTS = os.environ.get("INTEGRATION_TEST_ALLOW_LISTS", "")
DELAY_TIMEOUT = int(os.environ.get("INTEGRATION_TEST_DELAY_TIMEOUT", "30"))
ALLOW_APP_LISTS = os.environ.get("INTEGRATION_TEST_ALLOW_APP_LISTS", "*")

# 测试参数
ORDER_USD = 100.0  # 单笔订单 USD（用于 swap）
SPOT_ORDER_AMOUNT = 1.0  # 现货订单数量（SOL）
SOL_SPOT_SYMBOL = "SOL/USDT"  # 现货测试使用 SOL（OKX Demo Trading 的 ETH 现货有 bug）
ETH_SWAP_SYMBOL = "ETH/USDT:USDT"

# 网络预检主机列表（常用交易所域名）
NETWORK_CHECK_HOSTS = [
    ("www.okx.com", 443),
    ("api.binance.com", 443),
    ("1.1.1.1", 53),  # Cloudflare DNS as fallback
]


def check_network_connectivity(timeout: float = 3.0) -> tuple[bool, str]:
    """
    检查网络连通性

    Returns:
        (is_connected, message): 是否连通及详细信息
    """
    errors = []
    for host, port in NETWORK_CHECK_HOSTS:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect((host, port))
            sock.close()
            return True, f"Network OK (connected to {host}:{port})"
        except socket.gaierror as e:
            errors.append(f"{host}:{port} - DNS resolution failed: {e}")
        except socket.timeout:
            errors.append(f"{host}:{port} - Connection timeout ({timeout}s)")
        except OSError as e:
            errors.append(f"{host}:{port} - Connection failed: {e}")

    return False, "Network unavailable. Tried:\n  - " + "\n  - ".join(errors)


def is_group_allowed(group: int) -> bool:
    """检查分组是否允许执行"""
    if not ALLOW_LISTS:
        return False
    if ALLOW_LISTS.strip() == "*":
        return True
    allowed = [g.strip() for g in ALLOW_LISTS.replace(",", " ").split()]
    return str(group) in allowed


def get_skip_reason(group: int) -> str:
    """获取跳过原因"""
    if not ALLOW_LISTS:
        return "INTEGRATION_TEST_ALLOW_LISTS not set (default: skip all)"
    if not is_group_allowed(group):
        return f"Group {group} not in INTEGRATION_TEST_ALLOW_LISTS={ALLOW_LISTS!r}"
    return ""


def skip_if_group_not_allowed(group: int):
    """如果分组不允许则跳过"""
    if not is_group_allowed(group):
        pytest.skip(get_skip_reason(group))


def get_demo_exchange_configs() -> list[str]:
    """获取 demo 交易所配置列表"""
    pattern = PROJECT_ROOT / "conf" / "exchange" / "demo" / "*.yaml"
    files = glob(str(pattern))
    result = []
    for file in files:
        rel_path = os.path.relpath(file, PROJECT_ROOT / "conf" / "exchange")
        result.append(os.path.splitext(rel_path)[0])
    return result


def get_demo_app_configs() -> list[str]:
    """获取 demo app 配置列表"""
    pattern = PROJECT_ROOT / "conf" / "app" / "demo" / "*.yaml"
    files = glob(str(pattern))
    result = []
    for file in files:
        basename = os.path.splitext(os.path.basename(file))[0]
        # 检查 allow list
        if ALLOW_APP_LISTS != "*":
            allowed = [a.strip() for a in ALLOW_APP_LISTS.replace(",", " ").split()]
            if basename not in allowed:
                continue
        result.append(f"demo/{basename}")
    return result


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(scope="module")
def check_network():
    """
    网络预检 fixture（module 级别）

    在测试模块开始时检查网络连通性，如果不可用则跳过整个模块的测试。
    提供清晰的错误信息，便于排障。
    """
    is_connected, message = check_network_connectivity()
    if not is_connected:
        pytest.skip(
            f"Integration tests require network connectivity.\n"
            f"  {message}\n"
            f"  Please check your network connection and try again."
        )
    print(f"\n  [Network Check] {message}")


@pytest.fixture(scope="module")
def init_fernet():
    """初始化 Fernet 解密（密码: null）"""
    from hft.config.crypto import init_fernet
    init_fernet("null")
    yield


@pytest.fixture
async def exchange_instance(request, init_fernet, check_network):
    """创建交易所实例（自动检查网络）"""
    config_path = request.param
    from hft.exchange.config import BaseExchangeConfig

    config = BaseExchangeConfig.load(config_path, cwd=str(PROJECT_ROOT))

    # 强制要求 test: true
    if not config.test:
        pytest.skip(f"Exchange {config_path} does not have test: true")

    exchange = config.instance

    try:
        await exchange.load_markets()
    except Exception as e:
        # 捕获网络错误，给出更清晰的提示
        error_msg = str(e)
        if "gaierror" in error_msg or "DNS" in error_msg.upper() or "name resolution" in error_msg.lower():
            pytest.skip(
                f"Network/DNS error while connecting to {config_path}:\n"
                f"  {error_msg}\n"
                f"  Please check your network connection."
            )
        raise

    yield exchange

    await exchange.close()


# ============================================================
# 工具函数：清仓与撤单
# ============================================================

async def cancel_all_orders(exchange, symbol: str) -> int:
    """取消指定交易对的所有挂单"""
    try:
        orders = await exchange.fetch_open_orders(symbol)
        cancelled = 0
        for order in orders:
            try:
                await exchange.cancel_order(order["id"], symbol)
                cancelled += 1
            except Exception as e:
                print(f"  Warning: Failed to cancel order {order['id']}: {e}")
        return cancelled
    except Exception as e:
        print(f"  Warning: Failed to fetch open orders for {symbol}: {e}")
        return 0


async def close_swap_position(exchange, symbol: str) -> bool:
    """平掉 swap 仓位（reduceOnly）"""
    try:
        # 使用 ccxt 实例直接获取指定 symbol 的仓位
        swap_ccxt = exchange.exchanges.get("swap")
        if not swap_ccxt:
            return False

        positions = await swap_ccxt.fetch_positions([symbol])
        for pos in positions:
            if pos.get("symbol") != symbol:
                continue
            contracts = abs(float(pos.get("contracts", 0) or 0))
            if contracts <= 0:
                continue
            side = pos.get("side", "")
            # 平仓方向：long -> sell, short -> buy
            close_side = "sell" if side == "long" else "buy"
            await exchange.create_order(
                symbol, "market", close_side, contracts,
                params={"reduceOnly": True}
            )
            print(f"  Closed {side} position: {contracts} contracts")
            return True
    except Exception as e:
        print(f"  Warning: Failed to close swap position for {symbol}: {e}")
    return False


async def sell_spot_balance(exchange, symbol: str, base_currency: str = "ETH") -> bool:
    """卖出现货余额至约 0"""
    try:
        ccxt_keys = list(exchange.exchanges.keys())
        spot_key = "spot" if "spot" in ccxt_keys else ccxt_keys[0]
        balance = await exchange.medal_fetch_balance(spot_key)

        amount = float(balance.get(base_currency, {}).get("free", 0) or 0)
        if amount <= 0.0001:  # 忽略极小余额
            return False

        # 市价卖出
        await exchange.create_order(symbol, "market", "sell", amount)
        print(f"  Sold {amount} {base_currency}")
        return True
    except Exception as e:
        print(f"  Warning: Failed to sell spot balance: {e}")
    return False


async def cleanup_eth(exchange, include_spot: bool = True):
    """清理 ETH/SOL 相关的仓位和挂单"""
    print("\n  [Cleanup] Cancelling orders and closing positions...")

    # 取消挂单
    if include_spot and "spot" in exchange.config.support_types:
        cancelled = await cancel_all_orders(exchange, SOL_SPOT_SYMBOL)
        if cancelled:
            print(f"  Cancelled {cancelled} spot orders")

    if "swap" in exchange.config.support_types:
        cancelled = await cancel_all_orders(exchange, ETH_SWAP_SYMBOL)
        if cancelled:
            print(f"  Cancelled {cancelled} swap orders")

    # 平掉 swap 仓位
    if "swap" in exchange.config.support_types:
        await close_swap_position(exchange, ETH_SWAP_SYMBOL)

    # 卖出现货余额（SOL）
    if include_spot and "spot" in exchange.config.support_types:
        await sell_spot_balance(exchange, SOL_SPOT_SYMBOL, "SOL")

    await asyncio.sleep(1)  # 等待订单处理完成


async def wait_random(min_sec: float = 5, max_sec: float = 15):
    """随机等待"""
    delay = random.uniform(min_sec, max_sec)
    print(f"  Waiting {delay:.1f}s...")
    await asyncio.sleep(delay)


# ============================================================
# 分组 0：交易所 API 级测试
# ============================================================

@pytest.mark.integration_test
class TestGroup0ExchangeAPI:
    """分组 0：交易所 API 级（spot+swap 市价开平 + fetch_my_trades 可见性）"""

    @pytest.fixture(autouse=True)
    def check_group(self):
        skip_if_group_not_allowed(0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exchange_instance", get_demo_exchange_configs(), indirect=True)
    async def test_market_order_spot(self, exchange_instance):
        """测试现货市价单：买入 -> 等待 -> 卖出（使用 SOL）"""
        exchange = exchange_instance

        if "spot" not in exchange.config.support_types:
            pytest.skip("Exchange does not support spot trading")

        # 清理环境
        await cleanup_eth(exchange, include_spot=True)

        # 获取当前价格
        ticker = await exchange.fetch_ticker(SOL_SPOT_SYMBOL)
        price = ticker["last"]
        amount = SPOT_ORDER_AMOUNT  # 固定 1 SOL

        print(f"\n  [Spot Market] Price: {price}, Amount: {amount:.6f} SOL (~{amount * price:.2f} USD)")

        # 市价买入
        print("  Placing buy order...")
        buy_order = await exchange.create_order(SOL_SPOT_SYMBOL, "market", "buy", amount)
        assert buy_order is not None
        assert buy_order.get("id")
        print(f"  Buy order placed: {buy_order['id']}")

        await wait_random(5, 10)

        # 获取实际余额后卖出（考虑手续费）
        print("  Fetching actual balance...")
        spot_key = "spot" if "spot" in exchange.exchanges else list(exchange.exchanges.keys())[0]
        balance = await exchange.medal_fetch_balance(spot_key)
        actual_amount = float(balance.get("SOL", {}).get("free", 0) or balance.get("SOL", 0) or 0)
        if actual_amount <= 0:
            actual_amount = amount * 0.999  # fallback
        print(f"  Actual SOL balance: {actual_amount:.6f}")

        # 市价卖出
        print("  Placing sell order...")
        sell_order = await exchange.create_order(SOL_SPOT_SYMBOL, "market", "sell", actual_amount)
        assert sell_order is not None
        assert sell_order.get("id")
        print(f"  Sell order placed: {sell_order['id']}")

        await wait_random(5, 10)

        # 验证 fetch_my_trades 可见性
        print("  Verifying trades visibility...")
        spot_ccxt = exchange.exchanges.get("spot") or exchange.exchanges.get(list(exchange.exchanges.keys())[0])
        trades = await spot_ccxt.fetch_my_trades(SOL_SPOT_SYMBOL, limit=10)
        assert len(trades) > 0, "No trades found after market orders"
        print(f"  ✓ Found {len(trades)} trades")

        # 清理
        await cleanup_eth(exchange, include_spot=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exchange_instance", get_demo_exchange_configs(), indirect=True)
    async def test_market_order_swap(self, exchange_instance):
        """测试合约市价单：开仓 -> 等待 -> 平仓"""
        exchange = exchange_instance

        if "swap" not in exchange.config.support_types:
            pytest.skip("Exchange does not support swap trading")

        # 清理环境
        await cleanup_eth(exchange, include_spot=False)

        # 获取当前价格
        ticker = await exchange.fetch_ticker(ETH_SWAP_SYMBOL)
        price = ticker["last"]

        # 计算合约数量
        contract_size = exchange.get_contract_size(ETH_SWAP_SYMBOL)
        amount = ORDER_USD / price / contract_size

        print(f"\n  [Swap Market] Price: {price}, Amount: {amount:.6f} contracts (~{ORDER_USD} USD)")

        # 市价开多
        print("  Opening long position...")
        open_order = await exchange.create_order(ETH_SWAP_SYMBOL, "market", "buy", amount)
        assert open_order is not None
        assert open_order.get("id")
        print(f"  Open order placed: {open_order['id']}")

        await wait_random(5, 10)

        # 市价平仓
        print("  Closing position...")
        close_order = await exchange.create_order(
            ETH_SWAP_SYMBOL, "market", "sell", amount,
            params={"reduceOnly": True}
        )
        assert close_order is not None
        assert close_order.get("id")
        print(f"  Close order placed: {close_order['id']}")

        await wait_random(5, 10)

        # 验证 fetch_my_trades 可见性
        print("  Verifying trades visibility...")
        swap_ccxt = exchange.exchanges.get("swap")
        trades = await swap_ccxt.fetch_my_trades(ETH_SWAP_SYMBOL, limit=10)
        assert len(trades) > 0, "No trades found after market orders"
        print(f"  ✓ Found {len(trades)} trades")

        # 清理
        await cleanup_eth(exchange, include_spot=False)


# ============================================================
# 分组 1：限价单校验测试
# ============================================================

@pytest.mark.integration_test
class TestGroup1LimitOrders:
    """分组 1：限价单校验（far/near 下单参数合理性 + 可查询/可撤单）"""

    @pytest.fixture(autouse=True)
    def check_group(self):
        skip_if_group_not_allowed(1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exchange_instance", get_demo_exchange_configs(), indirect=True)
    async def test_limit_order_far(self, exchange_instance):
        """测试远离限价单：偏离 5%，验证挂单状态和撤单"""
        exchange = exchange_instance

        if "swap" not in exchange.config.support_types:
            pytest.skip("Exchange does not support swap trading")

        # 清理环境
        await cleanup_eth(exchange, include_spot=False)

        # 获取当前价格
        ticker = await exchange.fetch_ticker(ETH_SWAP_SYMBOL)
        price = ticker["last"]

        # 计算远离价格（买单偏离 5%）
        far_price = price * 0.95
        contract_size = exchange.get_contract_size(ETH_SWAP_SYMBOL)
        amount = ORDER_USD / price / contract_size

        print(f"\n  [Far Limit] Current: {price}, Limit: {far_price:.2f} (-5%), Amount: {amount:.6f}")

        # 下远离限价单
        print("  Placing far limit buy order...")
        order = await exchange.create_order(
            ETH_SWAP_SYMBOL, "limit", "buy", amount, far_price
        )
        assert order is not None
        order_id = order.get("id")
        assert order_id
        print(f"  Order placed: {order_id}")

        await wait_random(5, 10)

        # 验证订单状态（应为 open）
        print("  Checking order status...")
        open_orders = await exchange.fetch_open_orders(ETH_SWAP_SYMBOL)
        order_ids = [o["id"] for o in open_orders]
        assert order_id in order_ids, f"Order {order_id} not found in open orders"
        print("  ✓ Order is open")

        # 验证价格偏移
        placed_order = next(o for o in open_orders if o["id"] == order_id)
        placed_price = placed_order.get("price", 0)
        price_diff = abs(placed_price - far_price) / far_price
        assert price_diff < 0.01, f"Price deviation too large: {price_diff:.2%}"
        print(f"  ✓ Price deviation: {price_diff:.4%}")

        # 撤单
        print("  Cancelling order...")
        await exchange.cancel_order(order_id, ETH_SWAP_SYMBOL)

        await asyncio.sleep(2)

        # 验证撤单成功
        open_orders = await exchange.fetch_open_orders(ETH_SWAP_SYMBOL)
        order_ids = [o["id"] for o in open_orders]
        assert order_id not in order_ids, f"Order {order_id} still open after cancel"
        print("  ✓ Order cancelled successfully")

        # 清理
        await cleanup_eth(exchange, include_spot=False)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exchange_instance", get_demo_exchange_configs(), indirect=True)
    async def test_limit_order_near(self, exchange_instance):
        """测试靠近限价单：偏离 1%，验证价格方向合理性"""
        exchange = exchange_instance

        if "swap" not in exchange.config.support_types:
            pytest.skip("Exchange does not support swap trading")

        # 清理环境
        await cleanup_eth(exchange, include_spot=False)

        # 获取当前价格
        ticker = await exchange.fetch_ticker(ETH_SWAP_SYMBOL)
        price = ticker["last"]

        # 计算靠近价格（偏离 1%）
        buy_price = price * 0.99  # 买单略低于当前价
        sell_price = price * 1.01  # 卖单略高于当前价
        contract_size = exchange.get_contract_size(ETH_SWAP_SYMBOL)
        amount = ORDER_USD / price / contract_size

        print(f"\n  [Near Limit] Current: {price}")
        print(f"  Buy price: {buy_price:.2f} (-1%), Sell price: {sell_price:.2f} (+1%)")

        # 下买单
        print("  Placing near limit buy order...")
        buy_order = await exchange.create_order(
            ETH_SWAP_SYMBOL, "limit", "buy", amount, buy_price
        )
        assert buy_order is not None
        buy_order_id = buy_order.get("id")
        assert buy_order_id

        # 验证买单价格不高于当前价太多
        placed_buy_price = buy_order.get("price", 0)
        assert placed_buy_price <= price * 1.01, f"Buy price {placed_buy_price} too high (current: {price})"
        print(f"  ✓ Buy order price valid: {placed_buy_price}")

        # 下卖单
        print("  Placing near limit sell order...")
        sell_order = await exchange.create_order(
            ETH_SWAP_SYMBOL, "limit", "sell", amount, sell_price
        )
        assert sell_order is not None
        sell_order_id = sell_order.get("id")
        assert sell_order_id

        # 验证卖单价格不低于当前价太多
        placed_sell_price = sell_order.get("price", 0)
        assert placed_sell_price >= price * 0.99, f"Sell price {placed_sell_price} too low (current: {price})"
        print(f"  ✓ Sell order price valid: {placed_sell_price}")

        await asyncio.sleep(2)

        # 撤单
        print("  Cancelling orders...")
        await exchange.cancel_order(buy_order_id, ETH_SWAP_SYMBOL)
        await exchange.cancel_order(sell_order_id, ETH_SWAP_SYMBOL)
        print("  ✓ Orders cancelled")

        # 清理
        await cleanup_eth(exchange, include_spot=False)


# ============================================================
# 分组 2：App tick 级测试
# ============================================================

@pytest.mark.slow_integration_test
class TestGroup2AppTick:
    """分组 2：App tick 级（demo app/executor/strategy 组合运行）"""

    @pytest.fixture(autouse=True)
    def check_group(self):
        skip_if_group_not_allowed(2)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("app_config_path", get_demo_app_configs())
    async def test_app_tick_cycle(self, app_config_path, init_fernet):
        """测试 App tick 周期：启动 -> 触发交易 -> 清理"""
        from hft.core.app.config import AppConfig

        print(f"\n  [App Tick] Loading config: {app_config_path}")

        config = AppConfig.load(app_config_path, cwd=str(PROJECT_ROOT))

        # 验证使用 demo 交易所
        exchange_id_map = config.exchanges.get_id_map()
        for ex_id in exchange_id_map.keys():
            if not ex_id.startswith("demo/"):
                pytest.skip(f"App uses non-demo exchange: {ex_id}")

        # 创建 app 实例
        app = config.instance

        try:
            # 启动 app
            print("  Starting app...")
            await app.start()

            # 获取交易所实例
            exchanges = list(app.exchanges.values())
            if not exchanges:
                pytest.skip("No exchanges loaded")

            exchange = exchanges[0]

            # 清理环境
            await cleanup_eth(exchange, include_spot=True)

            # 修改策略目标：0 -> +100 USD
            print(f"  Setting target position to +{ORDER_USD} USD...")
            for strategy in app.strategies.values():
                if hasattr(strategy, "positions_usd"):
                    strategy.positions_usd[ETH_SWAP_SYMBOL] = ORDER_USD

            # 运行一段时间
            print(f"  Running for {DELAY_TIMEOUT}s...")
            await asyncio.sleep(DELAY_TIMEOUT)

            # 修改策略目标：+100 USD -> 0
            print("  Setting target position to 0...")
            for strategy in app.strategies.values():
                if hasattr(strategy, "positions_usd"):
                    strategy.positions_usd[ETH_SWAP_SYMBOL] = 0

            # 再运行一段时间
            await asyncio.sleep(min(DELAY_TIMEOUT, 15))

            # 清理
            await cleanup_eth(exchange, include_spot=True)

            print("  ✓ App tick cycle completed")

        finally:
            # 停止 app
            print("  Stopping app...")
            await app.stop()


# ============================================================
# 入口
# ============================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
