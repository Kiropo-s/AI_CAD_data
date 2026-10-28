# STEP Plan 1 重构记录

2026-10-26：改造前快照已提交为 `097478e`。当前实现为 step2code 1.0，生产路径只加载 `step-plan/1`；旧 CAD IR、嵌套 STEP recipe 和 json2scapi emitter/preparation 旁路已删除。

- [当前执行契约](PLAN_SPEC.md) 与 [独立 Schema](../../plan/schema.json)。
- [实际实现及边界](IMPLEMENTED.md)，区分已完成机制与后续优化。
- [P0–P3 识别扩展计划](RECOGNITION-ROADMAP-P0-P3.md)。
- [原始 100 例基线分析](BASELINE_100.md)、[摘要](baseline-100-summary.json)、[源和旧报告哈希清单](baseline-100-manifest.json)。
- [重构完成时的回归报告](RESULTS.md)：记录 2026-10-26 的 29 个通过和 69 项测试。
- [最新识别扩展记录（2026-10-27）](RECOGNITION_EXTENSION_20261027.md)：加入 B 样条端面接线和完整椭圆原生识别；当前 100 例为 30 个通过、172 个源实体，71 项测试通过。
- [原始实施计划](IMPLEMENTATION.md) 与 [架构目标](ARCHITECTURE.md)：保留设计依据，实际模块/API 以 IMPLEMENTED.md 与源码为准。
- [62/52 只读复现脚本](diagnose_boundaries.py)，旧格式仅允许在此类历史审计脚本中读取。

历史基线实际只有 100 个记录：28 个通过、70 个暂定、2 个拒绝。目录名中的 1000 不是已完成数量。旧基线未记录实现源码指纹，不能事后假定它等于当前源码；新运行在 manifest/report 中固定源码、依赖和配置身份。

原始 STEP、旧输出及 json2scapi 保持原样。新输出只写入新的运行目录，不迁移旧 payload。加载新计划、原生重放和编译生成代码均不需要 source/provenance/symbols/report。
