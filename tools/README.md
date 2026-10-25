# 专项工具与历史脚本

正式入口仍在项目根目录，见 [主 README](../README.md)。本目录收纳从 out/tmp 迁出的历史诊断源码；临时日志、STEP 和 JSON 仍保留在原输出目录。

## archive/

| 脚本 | 用途与限制 |
|---|---|
| audit_ir_contract.py | 针对 2026-10-25 两个 IR→Code v3 批次，复现代码、prepared IR，比较旧候选的原空间不变量并提取 00000093 圆弧冲突证据。会更新指定批次的审计文件、代码版本说明、symbols 和训练导出；不是只读工具。 |
| audit_shape_local_v4.py | 针对早期 v4 固定回归检查 manifest、训练 JSONL、日志并写回报告；依赖历史批次和 out/tmp 中的测试日志。 |
| ir_contract_probe_20261024.py | 修复前问题的原始探针，依赖工作目录为 F:\AI_CAD；严格规则更新后可能提前报错。长期回归已转入 tests/test_ir_preparation.py。 |

这些脚本使用固定批次名，不能当作通用 CLI 或生产验收入口，也不要无目的地重跑。脚本保留原有逻辑；它们的新位置与原 out/tmp 位置具有相同目录深度，历史 ROOT 推导保持有效。

新工具应有明确的参数和用途说明；可复用检查优先进入 tests，运行产物一律放 out。
