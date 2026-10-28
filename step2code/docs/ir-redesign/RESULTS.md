# STEP Plan 1 重构验证

> 说明：本文件记录 2026-10-26 IR 重构完成时的 69 项测试与 29 个通过结果。2026-10-27 的识别扩展已加入完整椭圆和 B 样条端面提取，最新 71 项测试与 30 个通过结果见 [识别扩展记录](RECOGNITION_EXTENSION_20261027.md) 和 [最新 100 例审计](regression-100-recognition-extension-20261027.json)。

日期：2026-10-26。改造前快照提交：`097478e`；生产代码版本：1.0.0。本轮重构保留为该提交之后的工作区差异，未追加第二次提交。

## 最终结论

同源前 100 例完整回归已完成：**29 个文件通过，共 171 个源实体；原来 28 个成功全部保留，新增 62 号通过，没有成功案例回退。** 本轮建立独立执行契约、原生执行器、SCAPI 编译器与可恢复运行机制；成功率从本组的 28% 变为 29%，不将其解读为识别能力的大幅扩展。

| 最终 outcome | 文件数 |
| --- | ---: |
| accepted | 29 |
| incomplete | 64 |
| invalid_input | 5 |
| error | 2 |
| rejected | 0 |
| 合计 | 100 |

`requested_count = selected_count = completed_count = 100`，`manifest.run_state = completed`。完整证据见 [逐例对照审计](regression-100-result.json)、[运行 manifest](../results/abc100-plan1-verified-20261026/manifest.json)、[运行 summary](../results/abc100-plan1-verified-20261026/summary.json) 和 [逐例 CSV](../results/abc100-plan1-verified-20261026/cases.csv)。

## 测试与运行身份

**69 项测试全部通过，用时 27.499 秒**，见 [测试结果身份](test-suite-result.json)。测试覆盖严格格式/类型/图校验、拉伸/旋转/CSG 几何夹具、大圆弧、有理 B 样条及裁剪反向、孔归属与自交、独立参数与重复构建、源码脱离仓库执行、STEP 单位、真实 Windows 后代进程终止、检查点和 CLI 恢复。测试源码与原始日志 SHA256 已核对一致。

最终运行目录：`F:\AI_CAD\step2code\out\abc100-plan1-verified-20261026`（逐例产物已清理，摘要归档于 `docs/results/abc100-plan1-verified-20261026/`）。

固定实现指纹：

```text
9e749b2352d3ed1fff2dced1b157440ec62f9da31455b40d5e3f371869be7f4e
```

审计确认运行身份与当前执行源码一致，100 个输入内容哈希与历史基线一致，100 份历史报告哈希未变，回退列表为空。Git revision 标识快照提交，逐文件源码 hash 另行固定本轮未提交的改造；不能只凭 revision 推断实际执行源码。

环境：Python 3.13.14，simplecadapi 2.2.6，cadquery-ocp 7.9.3.1.1，NumPy 2.5.4，SciPy 1.18.1。每例 8192 个采样点、120 秒硬限时，世界坐标模式。整体阈值维持 `CD/L <= 0.01`、`IoU >= 0.999`、相对体积误差 `<= 0.001`。逐体重放与生成覆盖增加相对体积交叉检查 `<= 1e-7`，与双向差体积一起记录。

## 失败分类与关键边界

未通过的 71 例中：曲面类型能力限制 31、搜索未找到可验证候选 20、无效源拓扑 5、源面/实体数量上限 4、游离面 4、游离边 1、无实体 1、硬超时 2、度量不可靠 1、原生重放不一致 2。当前主要剩余问题仍是识别覆盖和搜索能力；结果分类不沿用旧 provisional/rejected 的含混含义。

**62 号：新增通过。** 旧 preparation 在相邻相切圆弧 004/005 上计算出距共享端点 `1.0115241622088438e-6 mm` 的伪交点，略大于 `1e-6 mm` 阈值。新路径保留精确圆心/半径/有向区间，原生 wire 自交、face 有效性及最终 CD/IoU/V 均通过。没有扩大容差或删除自交检查。两实体均由解析端面拉伸重建。

**52 号：仍为 measurement.unreliable。** 原生、实际返回和 STEP 回读均保留 14 个实体；回读逐体相对体积误差最大约 `2.70e-13`。异常发生在度量的占据空间求并，结果违反 `max(Va,Vb) <= Vunion <= Va+Vb`。稳健求并仍待改进，当前不授予成功。详细复现见 [边界诊断](boundary-diagnostics.json) 与 [只读脚本](diagnose_boundaries.py)。

**00/56 号：recognition.replay_mismatch。** 新增逐体体积交叉检查发现，双向 Cut 差体积为零时，积分体积仍分别存在约 `3.68e-7`、`1.24e-4` 相对差异。两例在旧基线也未通过；新版保留已完成检查点，不作为整例成功。

**46/48 号：worker.timeout。** 父进程硬限时终止整个进程树。**92 号：domain.no_solids**，纯表面输入不会误列为源拓扑无效。**99 号：**第 11 体搜索失败后保留前 10 体完整候选，整例仍为 incomplete，没有输出假完整的 model.plan.json。

## 验证范围

几何通过、序列化通过与参数编辑通过独立记录。整批 `parameter_edit = not_run`；有限夹具中的编辑测试不外推到这 100 个模型。当前识别器公开每个源实体独立的整体缩放/刚性变换，内部测得尺寸默认固定；不声称恢复完整设计参数、装配关系或建模历史。

前 100 例仅含 35 个源文档 token 组，29% 是本固定样本的通过率，不能外推全 ABC。此前的 [第一轮完整过程审计](regression-100-first-complete.json) 使用不同源码指纹，仅保留为过程证据；本报告和 regression-100-result.json 才是最终实现的验收结果。

另见 [实际实现边界](IMPLEMENTED.md)、[执行契约](PLAN_SPEC.md) 和 [10 个生成源码的结构审阅](readability-review.json)。

## 复现

在 `F:\AI_CAD` 执行，使用新的输出目录：

```powershell
./envs/simplecad/Scripts/python.exe -m unittest discover -s step2code/tests -p 'test_*.py' -v
./envs/simplecad/Scripts/python.exe -m step2code --input datasets/abc/step/abc_0000 --limit 100 --out step2code/out/abc100-new-run --samples 8192 --timeout 120
./envs/simplecad/Scripts/python.exe step2code/tests/run_abc_regression.py --run step2code/out/abc100-new-run --out step2code/out/abc100-new-run-audit.json
```
