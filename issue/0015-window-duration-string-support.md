# Issue 0015: window 支持 duration 字符串（如 60s/1m/5m）并在文档中明确

> **状态**：全部通过

## 背景

当前配置里的 `window` 多以数值秒（float）表达，但在实际写配置时，`60s/1m/5m/1h` 这类 duration 字符串更直观、更不易出错。

需要明确支持该语法，并把口径写进 docs/examples，避免"文档说可以写、实现不支持"或"实现支持、文档没说导致误用"。

## 目标语义

- `window` 同时支持：
  - `float/int`：单位秒
  - `str`：duration 字符串（例如 `60s`, `1m`, `5m`, `1h`, `1d`, `500ms`）
  - `null`：语义等价于 `0`（仅保留最新点）
- duration 字符串解析应有严格错误信息（指出非法单位/格式）
- docs/examples 明确推荐写法与等价关系（如 `1m == 60s == 60.0`）

## 实现完成

### 新增模块

- `hft/core/duration.py`：
  - `parse_duration(value)` 函数，支持解析 None/int/float/str 为秒数
  - 支持单位：ms (毫秒), s (秒), m (分钟), h (小时), d (天)
  - 严格的格式验证和错误信息

### 代码集成

- `hft/indicator/factory.py`：
  - `IndicatorFactory.__init__()` 调用 `_normalize_params()` 处理 window 参数
  - 自动将 duration 字符串转换为 float（秒数）
  - 非法格式记录警告，保留原值让后续构造函数报错

### 文档更新

- `docs/app-config.md`：
  - 新增 "window 参数格式" 章节，说明支持的格式和单位
  - 更新示例使用 duration 字符串（1m, 5m）
  - 推荐使用 duration 字符串（更直观）

- `docs/indicator.md`：
  - 更新配置示例使用 5m

- `docs/datasource.md`：
  - 更新 ready_condition 示例使用 5m

- `docs/architecture.md`：
  - 更新 indicators 示例使用 1m

### 测试覆盖

- `tests/test_duration_parsing.py`：
  - 16 个测试用例，覆盖所有单位（ms/s/m/h/d）
  - 测试 None/int/float/str 各种输入类型
  - 测试非法格式、不支持的单位、不支持的类型
  - 测试 IndicatorFactory 集成
  - ✅ 所有测试通过

## 影响范围

- `conf/app/*.yaml` 的 indicators params（以及其他出现 window 的配置块）
- `docs/app-config.md`、`docs/indicator.md`、`docs/datasource.md`、`docs/architecture.md`

## 验收标准

- ✅ 文档明确说明 window 支持 duration 字符串及所有单位（ms/s/m/h/d）
- ✅ 关键示例覆盖：`1m`、`5m`（文档中已使用）
- ✅ 单测覆盖：合法/非法 duration 的解析行为与错误信息（16 个测试全部通过）

## TODO

- [x] 明确 duration 支持的单位集合（ms/s/m/h/d）（已通过）
- [x] 配置加载层实现 window 字符串解析（已通过）
- [x] 更新 docs：统一用 `1m/5m` 等更直观写法（已通过）
- [x] 补充单测：window duration 解析（合法/非法/边界）（已通过）


