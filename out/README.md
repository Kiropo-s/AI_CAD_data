# 输出目录约定

所有生成物统一放在本目录。源码、可复用诊断脚本和汇总文档分别放项目根目录、tools/ 和 docs/，不放在临时输出中。

~~~text
out/
├─ README.md
├─ cases/<case_id>/             # 单例转换；再次运行可能覆盖同名文件
├─ corpus/<run_name>/           # 独立验收与训练导出；拒绝覆盖已有批次
├─ batches/<batch_id>/          # 旧 verify/batch_verify 批次
└─ tmp/                        # 临时 STEP、JSON、调试日志和可丢弃工作文件
~~~

## cases：单例 IR→Code

| 文件 | 含义 |
|---|---|
| model.ir.json | adapter 生成的原始 IR |
| model.readable.ftc.py | standalone readable SCAPI 代码 |
| model.readable.symbols.json | 参数绑定、原始字段和几何准备调整 |
| model.readable.prepared.ir.json | 实际用于生成的准备后 IR |

单例 CLI 支持自定义输出路径；无特殊需要时使用默认目录。此处的代码生成不自动执行 CAD 建模；要生成并验收 STEP，使用 build_corpus.py。

## corpus：生产语料与回归

批次根目录：

| 文件 | 含义 |
|---|---|
| manifest.json | 全部样本状态、来源、产物路径及指标 |
| errors.csv | 可筛选的逐例结果 |
| progress.jsonl | 逐例追加的进度 |
| train_ready.jsonl | 独立验收通过、包含代码的训练数据；full 模式 |
| train_ready_index.jsonl | 仅日志模式的通过样本索引；不包含训练代码 |
| LOCAL_REGRESSION.md | 固定回归脚本生成的摘要；其他批次不保证有此文件 |

每例 case 目录：

| 文件 | 含义 |
|---|---|
| fidelity.ir.json | 原始 IR |
| prepared.ir.json | 准备后的 IR |
| model.readable.ftc.py、symbols.json | 候选代码与完整追溯元数据 |
| readable.step、readable.result.json | 实际执行及 STEP 往返结果 |
| reference.step、reference.json | 独立参考及其构建报告；参考失败时可能没有 STEP |
| report.json | 最终状态、阶段、指标和错误证据 |
| .work/ | worker 临时工作目录 |

失败样本可能只保存到失败阶段，不补造缺失产物。--log-only 会在每例完成后移除逐例产物，只保留批次日志和索引，不能用作包含代码的训练集。

## 历史产物与清理

旧工具产生的 model.ftc.py、model.step、gt.*、candidate.*、verify.json 以及 faithful.* 保留其历史含义，不当作当前 readable v3 / similarity-shape-v4 的通过证据。

2026-10-25 清理：清空 tmp/，移除可再生的 Python 缓存、.work/ 与输出内 .simplecad/。旧 batches/0000-all 删除 model.ir.json、model.ftc.py、gt.ir.json、gt.ftc.py、gt.step 中间产物，保留 batch_summary.json、batch_results.csv、run.log 及逐例诊断 JSON。该批次是诊断留档，不能再作为完整模型产物集使用；历史报告内的产物路径可能已不存在。corpus/ 的 IR、代码、候选/参考 STEP、报告和训练 JSONL 保留。

- docs/reports 中的汇总文档继续链接到原批次；汇总、状态与指标不因清理改写。已清理的旧中间产物不补造。
- tmp/、.work/、输出内 .simplecad/ 和 __pycache__/ 是可丢弃输出。需要长期引用的日志/诊断先精简归档到 docs/reports/evidence，再清理临时目录。
- 不在 out/ 根目录散放模型和日志；新诊断输出放 tmp/<topic>/ 或所属批次。
- out 下仅本说明和 .gitignore 纳入版本管理；代码、规范、tests 和 examples 位于输出目录之外。

入口与命令见 [项目 README](../README.md)，规范与阶段结果见 [文档索引](../docs/README.md)。

本次清理记录见 [cleanup-20261025.json](../docs/reports/evidence/cleanup-20261025.json)。模块迁移的最小验证证据已归入 docs/reports/evidence/module-layout-20261025/。
