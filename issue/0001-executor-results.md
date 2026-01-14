# Issue: BaseExecutor 结果列表丢失 & 依赖未安装导致测试阻塞

> **状态**: ✅ 已完成，审核通过
> **发现版本**: `main`（2024-XX-XX）

## 背景

迁移 Executor 代码时，`BaseExecutor.on_tick()` 的结果收集逻辑没有同步更新，另外 `requirements.txt` 新增了 `promptantic` 依赖，但当前开发环境尚未安装，导致测试无法运行。

## 问题描述

1. `hft/executor/base.py` 第 522-556 行：`results` 初始为列表，却被 `_process_targets()` 的 `None` 覆盖，最终 `on_execution_complete()` 钩子收到 `None`。任何插件若期望可迭代结果都会在运行期崩溃，且无法落库单个目标的执行信息。
2. `hft/config/base.py` 现直接导入 `promptantic`。因为尚未执行 `pip install -r requirements.txt`，运行 `pytest tests/test_executor.py -q --maxfail=1` 在导入阶段即失败，无法验证 Executor 改动。

## 影响

- Executor 插件无法可靠读取执行结果，风控 / 审计扩展全部失效。
- 无法运行 `tests/test_executor.py`，阻断本次交付的基本验证流程。

## 审核结果（当前）

- ✅ `_process_targets()` 已返回结果列表，`on_execution_complete()` 可收到完整列表（hft/executor/base.py:522-564）
- ✅ `pip install -r requirements.txt` 已确认依赖齐全；`pytest tests/test_executor.py -q --maxfail=1` 通过（24 passed, 0.68s）

## 修复建议

- [x] 重写 `_process_targets()`，累积每个目标的执行结果并返回列表，确保 `on_execution_complete()` 能获取完整数据。（审核完成，见 hft/executor/base.py:522-564）
- [x] 在当前开发环境执行 `pip install -r requirements.txt`，并重新运行 `pytest tests/test_executor.py -q --maxfail=1` 以验证修复效果。（审核完成，测试通过）

---

## 实现报告（2026-01-14，历史记录，未在本次审核复核）

### 已完成工作

1. **依赖安装** ✅
   ```bash
   $ pip install -r requirements.txt
   # promptantic>=1.1.0 及所有依赖已安装
   ```

2. **代码修复** ✅
   - 修改文件：`hft/executor/base.py:532`
   - 修改内容：
     ```python
     # 返回类型改为：-> list[Optional[ExecutionResult]]
     # 添加 results = [] 列表收集逻辑
     # 每次调用 _process_single_target() 收集返回值
     # 失败/异常时追加 None 到结果列表
     # 返回完整结果列表
     ```

3. **测试验证** ✅
   ```bash
   $ pytest tests/test_executor.py -q --maxfail=1
   ........................                                                 [100%]
   24 passed in 0.69s
   ```

### 技术细节

**修复前问题**：
- `_process_targets()` 返回 `None`
- `on_tick()` 中 `results = []` 被覆盖为 `None`
- 插件钩子收到 `None` 导致迭代失败

**修复后**：
- `_process_targets()` 返回 `list[Optional[ExecutionResult]]`
- 每个目标的执行结果被正确收集
- 失败的目标用 `None` 标记
- 插件钩子收到完整结果列表

### 影响文件

- `hft/executor/base.py` - 核心修复

### 审核总结

- 代码修复与依赖验证均通过，测试覆盖 `tests/test_executor.py` 全部通过，无残留待办

---

## 备注

本 issue 已完成并审核通过，如需后续优化可另起 issue 追踪。
