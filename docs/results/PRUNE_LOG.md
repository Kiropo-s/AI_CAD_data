# 清理记录：2026-10-28

本次清理只删除**已被取代、可重新生成**的本地运行产物。所有保留结论的摘要、审计 JSON 和代表性案例都已在 `docs/results/` 中，删除不改变任何历史结论。

## 原则

1. 每个阶段只保留最新一次权威运行；旧轮次若其结论已写入文档和审计 JSON，则删除逐例产物。
2. 被删除运行的 `summary.json`、`cases.csv`、`manifest.json`（若存在）在删除前已归档到 `docs/results/`。
3. 被文档链接的测试日志先复制到 `docs/results/legacy-logs/`，再更新文档链接。
4. 清理前完整清单保存为 [`index-before-prune-20261028.json`](index-before-prune-20261028.json) 和 [`runs-before-prune-20261028.md`](runs-before-prune-20261028.md)。

## 保留

| 目录 | 说明 |
| --- | --- |
| `out/abc100-p05-diagnostics-20261027-r4` | 100 例权威基线（30 accepted / 172 实体），含逐例 plan、代码、STEP 与报告 |
| `out/abc-first20-p2a-20261028` | P2A profile 重构后的最新 20 例 smoke |

归档副本（不依赖 `out/`）：

- `docs/results/abc100-p05-diagnostics-20261027-r4/`
- `docs/results/abc-first20-p2a-20261028/`
- `docs/results/abc100-plan1-verified-20261026/`（1.0 首次完整 100 例）
- `docs/results/abc100-recognition-extension-20261027-r1/`（B 样条/椭圆扩展，30 accepted）
- `docs/results/samples/`（4 个方法的完整案例）
- `docs/results/legacy-logs/`（4 份被 0.x 文档引用的测试日志）

## 删除

总计 28 个有 summary 的运行目录、7 个无 summary 的探测目录和 72 个根级临时脚本/日志，`out/` 从 369.8 MB 降至 57.7 MB。

### 1.0 中间轮次

| 运行 | completed | accepted | size (MB) | 取代者 /  archival 位置 |
| --- | ---: | ---: | ---: | --- |
| `abc100-plan1-20261026` | 46 | 11 | 9.2 | 中断轮次，被 plan1-verified 取代 |
| `abc100-plan1-final-20261026` | 100 | 29 | 29.7 | 被 plan1-verified 取代 |
| `abc100-plan1-verified-20261026` | 100 | 29 | 29.6 | 摘要已归档；逐例审计 `regression-100-result.json` |
| `abc100-recognition-extension-20261027-r1` | 100 | 30 | 30.2 | 摘要已归档；审计 `regression-100-recognition-extension-20261027.json` |
| `abc100-p05-diagnostics-20261027-r1` | 100 | 29 | 43.9 | 修复前轮次，被 r4 取代 |
| `abc100-p05-diagnostics-20261027-r2` | 100 | 30 | 44.7 | 被 r4 取代 |
| `abc100-p05-diagnostics-20261027-r3` | 55 | 15 | 28.3 | 中断轮次 |
| `p05-diagnostics-smoke-20261027-r1` | 1 | 1 | 0.9 | 单例 smoke，已被最终基线覆盖 |
| `p05-diagnostics-smoke-08-20261027-r2` | 1 | 1 | 1.1 | 同上 |
| `p05-diagnostics-parent-smoke-08-20261027-r1` | 1 | 1 | 1.1 | 同上 |

### 0.x 历史轮次

| 运行 | size (MB) | 说明 |
| --- | ---: | --- |
| `abc-first1000-v4-20261026-180058` | 14.5 | 旧 CSG v4 试验 |
| `abc-csg-v4-regression` | 7.2 | 旧 CSG v4 回归 |
| `abc-expanded-v3-regression` | 6.5 | 旧旋转 v3 |
| `abc-expanded-v3-verified` | 6.5 | 旧旋转 v3 |
| `abc-first20-rotation-v3` | 6.5 | 旧旋转 v3 |
| `abc-first20-rotation-v3-final` | 6.5 | 旧旋转 v3 |
| `abc-first20-step-readable-v1` | 6.1 | 旧可读代码 v1 |
| `abc-first20-step-readable-v1-final` | 6.4 | 旧可读代码 v1 |
| `abc-step-readable-v1-regression` | 6.4 | 旧可读代码 v1 |
| `abc-first20-v1` | 5.0 | 旧 0.x 基线 |
| `readable-review-v1` | 0.7 | 旧可读性审阅 |
| `readable-review-step-v1` | 0.8 | 旧可读性审阅 |
| `csg06-first` / `csg06-oriented` / `csg06-replay` | 0.3 | 单例调试 |
| `csg08-partition` / `csg08-union` | 1.1 | 单例调试 |
| `abc14-coarse-v3` | 0.0 | 探测 |

### 无 summary 的探测目录与根级文件

删除：`csg-review-v4`、`recognition-extension-csg-20261027`、`recognition-extension-surface-20261027`、`recognition-extension-targeted-20261027`、`refactor-smoke`、`rotation-review-v3`、`unit-meter-debug-v1`、`__pycache__`。

同时删除 `out/` 根目录下 72 个一次性文件：`probe_*.py`、`debug_csg_build.py`、`audit_csg_artifacts.py`、`run_csg_fixtures.py`、`verify_rotation_artifacts.py`、`example06-standalone.py`、各类 `*.log` 与 `csg-*.json`、`diagnostic62.json`。其中只有 4 份被文档引用的测试日志保留，见 `legacy-logs/`。

## 未处理

- `step2code_direct/out`、`exports/`：已有独立归档目录 `release/step2code-direct/`，且当前有测试进程在运行，未改动。
- `json2scapi/out/corpus`（约 465 MB）：包含训练语料与固定回归证据，不是本轮 step2code 的产物，未改动。
