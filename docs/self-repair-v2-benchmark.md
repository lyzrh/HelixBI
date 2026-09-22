# Agent Self-Repair — Baseline(V1) vs V2

- 场景：12 条（acceptance_failed, column_error, empty_result, environment, exhausted, first_pass, multi_error, repeated_error, resource_limit, syntax_error, timeout, type_error），全局修复上限 MAX_FIX_ATTEMPTS=3
- V1 = 冻结的无差别重试（同一段提示 + 只数次数）；V2 = 错误分类 → 定向修复 → 每类额度 + 复读提前终止
- **这是离线策略仿真，不是实测**：LLM 与沙箱执行被桩化，分类 / 策略 / 复读检测 / 额度控制 / 路由 / prompt 组装 / token 记账均为真实代码
- 建模假设：注入的修复提示与错误类型匹配 → 下一轮即修复成功；否则同一错误再次出现（见 evaluation/repair_cases.py 顶部）
- token 口径：按真实 prompt 文本长度折算（字符数/3.5），并与真实 TokenUsage 记账交叉验证

## 1. 成功率与重试

| Metric | Baseline (V1) | V2 | Delta |
| --- | ---: | ---: | ---: |
| First-pass Success Rate | 8.3% | 8.3% | +0.0pp |
| Repair Success Rate | 18.2% | 80.0% | +61.8pp |
| Overall Success Rate | 25.0% | 75.0% | +50.0pp |
| Average Repair Attempts | 2.67 | 1.17 | -1.50 |
| Repeated Error Rate | 0.0% | 8.3% | +8.3pp |
| Repair Exhaustion Rate | 75.0% | 16.7% | -58.3pp |
| Fallback Rate（不可修复直接兜底） | 0.0% | 8.3% | +8.3pp |
| Unresolved Rate（未给出可信结果） | 75.0% | 25.0% | -50.0pp |

## 2. 成本与延迟

| Metric | Baseline (V1) | V2 | Delta |
| --- | ---: | ---: | ---: |
| LLM Calls / Query | 6.08 | 5.00 | -1.08 |
| Tokens / Query | 4218 | 2785 | -1433.17 |
| Repair Latency p50 (ms) | 14700 | 4900 | -9800.00 |
| Repair Latency p95 (ms) | 14700 | 14700 | +0.00 |
| Cycle Latency p50 (ms) | 23400 | 15500 | -7900.00 |
| 估算成本 / Query | 未配置单价 | 未配置单价 | — |

> 延迟口径：折算常量：单次 LLM 1900ms、单次沙箱执行 3000ms（沙箱本机不可实测，按声明常量折算，仅用于策略相对比较）；沙箱执行耗时本机无法实测，未测量不编造。
> 结论只应读作**策略差异**：V1 多花的调用与执行来自「提示词与病因无关 → 反复修同一个错」。

## 3. Skill 重放不进入 Self-Repair

- 断言通过：2/2（重放成功 = 0 次修复 / 0 次 LLM；重放失败 = 交上层 fallback，不在重放里自修复）

## 4. 逐场景明细（V2）

| 场景 | 结局 | 执行次数 | 修复次数 | 错误类别 | 策略 |
| --- | --- | ---: | ---: | --- | --- |
| sr01_first_pass_success | success | 1 | 0 | - | - |
| sr02_syntax_fixed | success | 2 | 1 | syntax_error | code_structure |
| sr03_column_fixed | success | 2 | 1 | column_error | schema_alignment |
| sr04_type_fixed | success | 2 | 1 | type_error | dtype_conversion |
| sr05_empty_fixed | success | 2 | 1 | empty_result | filter_and_scope |
| sr06_timeout_fixed | success | 2 | 1 | timeout | bounded_compute |
| sr07_resource_limit_fixed | success | 2 | 1 | resource_limit | memory_bound |
| sr08_repeated_identical | exhausted | 2 | 1 | column_error | schema_alignment |
| sr09_multi_error_then_success | success | 4 | 3 | type_error | dtype_conversion |
| sr10_max_attempts_exhausted | exhausted | 4 | 3 | empty_result | dtype_conversion |
| sr11_acceptance_failed_code_ok | success | 2 | 1 | empty_result | filter_and_scope |
| sr12_environment_unavailable | fallback | 1 | 0 | environment | - |
