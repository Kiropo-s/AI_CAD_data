# 识别扩展记录：2026-10-27

本轮在 `097478e` 快照重构之后继续扩展 STEP 识别。目标是让现有 `Profile2 -> extrude` 路径接收更多真实 STEP 轮廓，同时保持严格的原生重放、SCAPI 编译和 CD/IoU/V 验收。

## 实现

- 修复端面拉伸识别没有传递 `RecognitionOptions.max_spline_poles` 的问题。此前 `_curve()` 已有 B 样条提取逻辑，但生产路径使用默认 `0`，因此所有 STEP B 样条边都会被报告为不支持。
- 新增 `curve.ellipse2`，保留完整 OCC 椭圆的中心、长短半轴、局部主轴角度和环方向；内核使用原生 OCC 椭圆，SCAPI 使用 `make_ellipse_redge`，不做采样拟合。
- 完整椭圆可以作为端面轮廓的一部分进入统一 Profile2 DAG；部分椭圆边仍然明确报告为能力限制，避免用近似曲线冒充精确几何。
- 添加端面 B 样条和完整椭圆的原生重放、SCAPI 编译和几何等价测试。

## 验证

71 项单元和内核测试通过。固定实现指纹为：

```text
a3db37fa0d6020bf0d672f9b62ae06e4173fd754a78b1d64e2264b6e19083b47
```

同源前 100 例完整回归结果：

| outcome | 数量 |
| --- | ---: |
| accepted | 30 |
| incomplete | 63 |
| invalid_input | 5 |
| error | 2 |

相比重构完成时的 29 个通过，00000054 新增通过；00000062 继续通过，原先 28 个成功全部保留。输入哈希、历史报告哈希、完整运行状态和当前源码指纹均通过审计，详见 [逐例回归审计](regression-100-recognition-extension-20261027.json)。

00000054 的变化来自 B 样条/椭圆边界可以进入端面拉伸计划，最终方法为 `analytic_cap_prism`，几何、序列化和可读性检查均通过。原先 31 个 `capability.surface_type` 案例仍有 31 个未完成，说明剩余问题主要是 B 样条曲面、部分椭圆和复杂组合特征；原先 20 个 CSG 搜索失败减少为 19 个。

## 当前边界

本轮没有放宽 `CD/L`、IoU、体积或逐体重放阈值，也没有把部分椭圆离散化为折线。完整椭圆新增了原生计划 op；部分椭圆、一般 B 样条曲面、放样和扫掠仍需后续特征识别策略。下一阶段应优先把曲面边界分解、源面邻接和 CSG 候选生成结合起来，而不是继续增加基本曲线类型。

运行目录：`F:\AI_CAD\step2code\out\abc100-recognition-extension-20261027-r1`（逐例产物已清理；摘要归档于 [docs/results](../results/abc100-recognition-extension-20261027-r1/summary.json)）。
