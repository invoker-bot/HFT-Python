# Proposal 对比分析：遗漏和不一致

## 发现的问题

### 1. 变量命名不一致

**问题**：proposal 中使用 `trading_pair_std_price`，我的文档使用 `fair_price`。

**Proposal 原文**（第 95 行）：
```yaml
trading_pair_fair_price: fair_price  # 来自FairPriceIndicator注入
```

**Proposal 原文**（第 106-111 行）：
```
计算出变量顺序为 ratio_est -> trading_pair_std_price
delta_min_price = trading_pair_std_price - fair_price_min
delta_max_price = fair_price_max - trading_pair_std_price
```

**结论**：应该统一使用 `trading_pair_std_price` 或明确说明 `fair_price` 是别名。

---

### 2. ratio_est 计算层级混淆

**Proposal 中有两处 ratio_est**：

#### 位置 1：trading_pair level（第 60 行）
```yaml
trading_pair:
  vars:
    weight: weights[symbol]
    ratio_est: weight * (group_min_price * amount) / max_position_usd
```

#### 位置 2：trading_pair_class_group level（第 99 行）
```yaml
ratio_est: sum([scope["ratio_est"] for scope in children.values()])
```

**问题**：
- trading_pair level 计算初始 ratio_est
- trading_pair_class_group level 聚合所有 children 的 ratio_est

**我的文档中**：只有 trading_pair level 的 ratio_est，缺少 group level 的聚合。

---

### 3. weight 变量缺失

**Proposal 第 59 行**：
```yaml
trading_pair:
  vars:
    weight: weights[symbol]  # 从 global weights 字典中获取
```

**我的文档中**：直接使用 `weight` 但没有定义如何计算。

**修正**：需要在 trading_pair scope 中添加 `weight` 变量的计算。

---

### 4. group_condition 字段缺失

**Proposal 第 101 行**：
```yaml
group_condition: #  (这里提供了一种动态能力，可以舍弃某些pairs)
```

**我的文档中**：使用了 `condition` 而非 `group_condition`。

**修正**：应该在 trading_pair_class_group scope 中添加 `group_condition` 字段。

---

### 5. 计算流程步骤不完整

**Proposal 第 106 行**：
> 对每个币对，运行在trading pair class level上，计算出变量顺序为 ratio_est -> trading_pair_std_price，并排除掉那些trading_pair_std_price为None的。如果len(group)为0，则该group不在参与后续计算了，也不再传入executor。**再次计算ratio_est变量**。

**关键点**：
1. 先计算 ratio_est
2. 再计算 trading_pair_std_price
3. 过滤掉 trading_pair_std_price 为 None 的
4. **再次计算 ratio_est**（这一步我的文档中缺失）

**我的文档中**：没有"再次计算 ratio_est"这一步。

---

### 6. 标准价格的标准化说明不清晰

**Proposal 第 95 行**：
> 对每一个exchage_class, symbol，都计算标准价格，**标准价格为1**

**我的文档中**：
> 组内最小价格标准化为 1.0

**问题**：proposal 说的是"标准价格为1"，但没有明确说是"最小价格为1"。需要澄清。

---

### 7. FAQ 内容缺失

**Proposal 第 84-88 行**：
```
FAQ：
- executor的执行scope是哪个？当然是trading pair instance level scope
- indicator的scope是哪个？由indicator的特性决定
- trading pair class group level scope处于哪个level？exchange class level scope的下游
```

**我的文档中**：Feature 0012 中没有包含这些 FAQ。

---

## 需要修正的文档

### Feature 0013 需要修正的部分

1. **配置示例中的 scopes 部分**：
   - 添加 `weight` 变量计算
   - 添加 trading_pair_class_group level 的 `ratio_est` 聚合
   - 使用 `group_condition` 而非 `condition`

2. **计算流程**：
   - 明确 ratio_est 的两次计算
   - 明确变量计算顺序：ratio_est → trading_pair_std_price → 过滤 → 再次计算 ratio_est

3. **变量命名**：
   - 统一使用 `trading_pair_std_price` 或明确说明别名关系

### Feature 0012 需要补充的部分

1. **FAQ 章节**：
   - Executor 的执行 scope
   - Indicator 的 scope 层级
   - 自定义 Scope 的层级关系

---

## 修正优先级

### P0（必须修正）
1. ratio_est 的两次计算逻辑
2. weight 变量的定义
3. 变量命名统一（trading_pair_std_price vs fair_price）

### P1（建议修正）
1. group_condition 字段
2. 计算流程步骤的完整性
3. FAQ 章节补充

### P2（可选）
1. 标准价格标准化的说明
2. 示例配置的完善
