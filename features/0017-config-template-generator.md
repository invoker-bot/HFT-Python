# Feature 0017: 配置模板生成器

## 概述

实现 `hft config gen` 命令的 `--template` 参数，允许用户快速生成预定义的配置文件模板。

## 动机

当前用户需要手动编写配置文件，对于常见的策略模式（如 PCA、Grid、AS 做市等）需要重复编写相似的配置。通过提供模板系统，可以：

1. **降低学习曲线**：新用户可以从成熟的模板开始
2. **提高效率**：避免重复编写常见配置
3. **最佳实践**：模板包含注释和推荐配置
4. **减少错误**：预定义的模板经过验证，减少配置错误

## 用户接口

### 命令格式

```bash
hft -p null config gen <type> --template <template_name> <config_path>
```

### 示例

```bash
# 生成 PCA executor 配置
hft -p null config gen executor --template pca pca/main

# 生成 Grid executor 配置
hft -p null config gen executor --template grid grid/btc

# 生成 AS 做市 executor 配置
hft -p null config gen executor --template as as/main

# 生成 RSI 策略配置
hft -p null config gen strategy --template rsi rsi/main

# 生成完整的 app 配置（已配好 scope）
hft -p null config gen app --template market_making mm_app
```

### 列出可用模板

```bash
# 列出所有可用模板
hft -p null config gen executor --list-templates
hft -p null config gen strategy --list-templates
hft -p null config gen app --list-templates
```

## 模板目录结构

```
templates/
├── executor/
│   ├── pca.yaml           # PCA 金字塔加仓
│   ├── grid.yaml          # 网格交易
│   ├── as.yaml            # Avellaneda-Stoikov 做市
│   ├── limit_basic.yaml   # 基础限价单
│   ├── market_basic.yaml  # 基础市价单
│   └── twap.yaml          # TWAP 执行
├── strategy/
│   ├── rsi.yaml           # RSI 策略
│   ├── static.yaml        # 静态仓位
│   └── market_neutral.yaml # 市场中性
└── app/
    ├── market_making.yaml  # 做市应用（含 scope）
    ├── trend_following.yaml # 趋势跟踪应用
    └── arbitrage.yaml      # 套利应用
```

## 模板内容规范

### 1. 模板必须包含详细注释

每个模板文件应包含：
- **用途说明**：该模板适用的场景
- **参数说明**：每个参数的含义和推荐值
- **使用示例**：如何调整参数以适应不同市场
- **注意事项**：使用时需要注意的风险点

### 2. Order 配置说明

**重要**：关于 `price` 和 `spread` 的行为：

- `price` 的默认值根据 `order_amount` 或 `order_usd` 的方向自动决定：
  - 买单（正值）：默认为买一价（bid）
  - 卖单（负值）：默认为卖一价（ask）
- `spread` 默认为 0，**始终**作用于 `price` 的偏移：
  - 买单：`final_price = price - spread`（向下偏移，更保守）
  - 卖单：`final_price = price + spread`（向上偏移，更保守）
- `price` 和 `spread` 可以同时使用，`spread` 会在 `price` 基础上进行偏移
- 如果只设置 `spread` 而不设置 `price`，则 `price` 使用默认值（买一/卖一价）

**示例**：
```yaml
# 示例 1：只使用 spread（price 使用默认值）
orders:
  - order_amount: 0.1      # 买单，price 默认为 bid
    spread: 0.0005         # 最终价格 = bid - 0.0005

# 示例 2：同时使用 price 和 spread
orders:
  - price: 'mid_price'     # 显式指定价格为中间价
    order_amount: 0.1      # 买单
    spread: 0.001          # 最终价格 = mid_price - 0.001

# 示例 3：只使用 price（spread 默认为 0）
orders:
  - price: 'mid_price - 0.002'
    order_amount: 0.1      # 最终价格 = mid_price - 0.002
```

## 核心模板列表

### Executor 模板

#### 1. PCA (Price Cost Averaging) - 金字塔加仓

**适用场景**：趋势跟踪、逆势加仓

**特点**：
- 基于 RSI 或其他指标触发
- 金字塔式分层加仓
- 自动止盈出场

**模板文件**：`templates/executor/pca.yaml`

#### 2. Grid - 网格交易

**适用场景**：震荡市场、区间交易

**特点**：
- 在中心价格上下设置多层订单
- 低买高卖，自动套利
- 适合波动率适中的市场

**模板文件**：`templates/executor/grid.yaml`

#### 3. AS (Avellaneda-Stoikov) - 做市策略

**适用场景**：流动性提供、做市商

**特点**：
- 基于库存和波动率动态调整价差
- 风险厌恶系数可调
- 适合高频做市

**模板文件**：`templates/executor/as.yaml`

#### 4. Limit Basic - 基础限价单

**适用场景**：简单的限价单执行

**特点**：
- 单层或多层限价单
- 可配置刷新容忍度和超时

**模板文件**：`templates/executor/limit_basic.yaml`

### Strategy 模板

#### 1. RSI - RSI 策略

**适用场景**：基于 RSI 指标的趋势跟踪

**特点**：
- RSI 超买超卖信号
- 可配置阈值和仓位大小

**模板文件**：`templates/strategy/rsi.yaml`

#### 2. Static - 静态仓位

**适用场景**：固定仓位配置

**特点**：
- 简单的固定仓位设置
- 适合测试和简单策略

**模板文件**：`templates/strategy/static.yaml`

### App 模板

#### 1. Market Making - 做市应用

**适用场景**：完整的做市系统

**特点**：
- 预配置好的 scope 系统
- 包含必要的 indicators
- 开箱即用的做市配置

**模板文件**：`templates/app/market_making.yaml`

## 实现任务

### 阶段 1：基础架构

- [ ] 创建 `templates/` 目录结构（待实现）
- [ ] 实现模板加载器 `TemplateLoader` 类（待实现）
- [ ] 修改 `hft config gen` 命令，添加 `--template` 参数（待实现）
- [ ] 实现 `--list-templates` 功能（待实现）

### 阶段 2：Executor 模板

- [ ] 创建 PCA executor 模板（待实现）
- [ ] 创建 Grid executor 模板（待实现）
- [ ] 创建 AS executor 模板（待实现）
- [ ] 创建 Limit Basic executor 模板（待实现）
- [ ] 创建 Market Basic executor 模板（待实现）

### 阶段 3：Strategy 和 App 模板

- [ ] 创建 RSI strategy 模板（待实现）
- [ ] 创建 Static strategy 模板（待实现）
- [ ] 创建 Market Making app 模板（待实现）

### 阶段 4：文档和测试

- [ ] 编写模板使用文档（待实现）
- [ ] 添加单元测试（待实现）
- [ ] 更新 CLI 帮助文档（待实现）

## 技术设计

### 1. 模板加载器

```python
# hft/cli/template_loader.py
class TemplateLoader:
    """配置模板加载器"""

    def __init__(self, template_dir: str = "templates"):
        self.template_dir = Path(template_dir)

    def list_templates(self, config_type: str) -> list[str]:
        """列出指定类型的所有可用模板"""
        pass

    def load_template(self, config_type: str, template_name: str) -> str:
        """加载指定模板内容"""
        pass

    def get_template_info(self, config_type: str, template_name: str) -> dict:
        """获取模板元信息（描述、参数等）"""
        pass
```

### 2. CLI 命令修改

修改 `hft/cli/config_commands.py` 中的 `config gen` 命令：

```python
@config.command("gen")
@click.argument("config_type", type=click.Choice(["app", "strategy", "executor", "exchange"]))
@click.argument("name", required=False)
@click.option("--template", "-t", help="使用指定模板生成配置")
@click.option("--list-templates", is_flag=True, help="列出可用模板")
def gen_config(config_type: str, name: str, template: str, list_templates: bool):
    """生成配置文件"""
    if list_templates:
        # 列出可用模板
        loader = TemplateLoader()
        templates = loader.list_templates(config_type)
        click.echo(f"Available {config_type} templates:")
        for tmpl in templates:
            info = loader.get_template_info(config_type, tmpl)
            click.echo(f"  - {tmpl}: {info.get('description', '')}")
        return

    if not name:
        click.echo("Error: NAME is required when not using --list-templates")
        return

    if template:
        # 使用模板生成
        loader = TemplateLoader()
        content = loader.load_template(config_type, template)
        # 保存到文件
        save_config(config_type, name, content)
    else:
        # 原有的生成逻辑
        generate_default_config(config_type, name)
```

### 3. 模板文件格式

每个模板文件包含两部分：
1. **元信息注释**（文件开头）
2. **配置内容**（YAML 格式）

示例：

```yaml
# Template: PCA Executor
# Description: 金字塔式加仓执行器，基于 RSI 指标触发
# Use Case: 适用于趋势跟踪和逆势加仓策略
# Parameters:
#   - entry_order_levels: 入场档位数量（默认 10）
#   - rsi_oversold: RSI 超卖阈值（默认 30）
#   - rsi_overbought: RSI 超买阈值（默认 70）

class_name: pca

requires:
  - ticker
  - rsi

# ... 配置内容 ...
```

## 注意事项

1. **模板不可变性**：模板文件应该是只读的，用户生成后可以修改生成的配置文件
2. **版本兼容性**：模板应该与当前版本的 HFT 系统兼容
3. **文档同步**：模板更新时需要同步更新文档
4. **测试覆盖**：每个模板都应该有对应的测试用例

## 相关文档

- `examples/002-executor-configurations.md` - Executor 配置详解
- `docs/executor.md` - Executor 设计文档
- `docs/app-config.md` - App 配置文档

