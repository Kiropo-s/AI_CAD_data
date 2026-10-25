# 文档索引

当前命令与模块导航见 [项目 README](../README.md)，源码分层、依赖边界和迁移对照见 [模块说明](MODULE_LAYOUT.md)。

## 有效规范

| 文档 | 内容 |
|---|---|
| [IR_SPEC.md](specs/IR_SPEC.md) | IR 0.1、ir-geometry-v1 准备规则、单位/坐标与布尔语义 |
| [READABLE_CODE_SPEC.md](specs/READABLE_CODE_SPEC.md) | readable v3 的代码层次、命名、参数化与追溯 |
| [EVALUATION_SPEC.md](specs/EVALUATION_SPEC.md) | similarity-shape-v4 生产验收与状态决策 |

## 实验与回归报告

按阶段阅读；不同评估协议的通过数不能直接比较。

| 文档 | 阶段 |
|---|---|
| [IR2CODE_V3_RESULTS.md](reports/IR2CODE_V3_RESULTS.md) | 2026-10-25：当前 IR→Code 几何契约修复与固定/前 50 回归 |
| [ANALYSIS_0000_20261024.md](reports/ANALYSIS_0000_20261024.md) | 2026-10-24：旧协议下 0000 全量日志分析 |
| [READABLE_ONLY_RESULTS.md](reports/READABLE_ONLY_RESULTS.md) | 2026-10-23：readable 单路径迁移与 mixed-86 |
| [STRUCTURED_V2_RESULTS.md](reports/STRUCTURED_V2_RESULTS.md) | 历史结构化 v2、双轨验证与代码审阅 |

原始 manifest、STEP、代码和指标仍保存在 [out/corpus](../out/corpus/) 的原批次内。2026-10-25 已清理运行缓存、临时探针及旧 batches/0000-all 的模型中间产物；保留诊断与汇总，不重写历史实验结论。详见 [输出保留规则](../out/README.md) 和 [清理记录](reports/evidence/cleanup-20261025.json)。

## 历史资料

- [整理前的 README 快照](archive/README_BEFORE_LAYOUT_20261025.md)：保留旧命令及上下文，不作为当前规范；历史样本路径可能因先前清理而不存在。
- [专项诊断脚本说明](../tools/README.md)：历史脚本的作用、数据依赖及副作用。

文档移动时同步更新相对链接；不在根目录保留重复版本。
