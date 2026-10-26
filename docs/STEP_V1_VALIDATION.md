# STEP 可读代码 v1 验证记录

日期：2026-10-26。版本：step2scapi 0.2.0 / analytic-prism-v2 / step-prism-emitter-v1。环境：已有 `F:\AI_CAD\envs\simplecad`，Python 3.13.14、SimpleCADAPI 2.2.6、cadquery-ocp 7.9.3.1.1；实际依赖版本见逐例 report 的 runtime_versions。

## 回归范围及结论

- 26 项 unittest 全部通过：[测试日志](../out/step-readable-v1-unittest.log)。覆盖原有 15 项和新加入的模板、实例尺寸独立、定位/方向编辑、覆盖错配/缺失、单位转换、游离几何拒绝、保存后的编辑及 Python `-I` 隔离执行。
- 12 类不同结构样例均实际 build、导出回读并通过 world CD/IoU/V 和逐实体覆盖检查：[样例报告](../out/readable-review-step-v1/summary.json)、[结构审阅](../out/readable-review-step-v1/structure-review.json)。草图、轮廓/孔、混合线弧、斜平面、多实体和重叠实体表达沿用明确函数层次。
- 真实输入为 `F:\AI_CAD\datasets\abc\step\abc_0000`，按文件名排序取前 20 个 STEP（00000000–00000019），未递归、未修改源数据。
- 每例独立进程，120 秒限时；world 模式，参考与候选各 8192 点，seed=0；保持 CD/L≤0.01、IoU≥0.999、相对 V 误差≤0.001。候选回读检查始终为 world。
- 结果 **5 train_ready / 15 provisional / 0 rejected**。15 个 provisional 均为 unsupported_geometry，无超时/崩溃/必需测量缺失。
- 按文件名和源 SHA256 与旧版前 20 例逐一对应，20 个状态均一致，没有失去通过资格的旧样本。回归工具结果 pass 表示这些检查通过，不表示 20 个输入均能重建。
- 5 个通过样本各测试首实体的拉伸距离乘 1.1 和 origin 平移 `(3,-4,8)` mm，共 10 项有限编辑，全部通过。检查真实重新 build、有效拓扑、体积变化关系、位置变化和其余实体不变；报告状态为 partial，其他参数未全测。
- assembly_validation=unsupported，history_recovered=false。独立记录这些限制，几何通过只用于 geometry_reconstruction。

## 真实样本的实测指标

| ID | 实体数 | 模板数 | CD (mm) | CD/L | IoU 原值 | 相对 V 误差 |
|---|---:|---:|---:|---:|---:|---:|
| 00000005 | 10 | 9 | 2.52433421142 | 0.00382065335262 | 1.0000000001961133 | 1.96113550093e-10 |
| 00000007 | 1 | 1 | 1.33997632947 | 0.00596966760659 | 1 | 1.7598709595e-16 |
| 00000017 | 64 | 15 | 1.60617160609 | 0.00372575990601 | 1 | 0 |
| 00000018 | 16 | 16 | 1.20621246579 | 0.00381061684709 | 1.0000000000266149 | 2.6615088655e-11 |
| 00000019 | 16 | 16 | 1.20621246579 | 0.00381061684709 | 1.0000000000266149 | 2.6615088655e-11 |

微小大于 1 的 IoU 保留原值，沿用双向 Cut 的数值可靠性核查；没有截断成 1。采样 CD 会随等价 BREP 的表达和网格而变化，状态回归保持正式阈值，不要求距离逐位相等。

## 结构变化

| ID | 函数数（旧 → 新） | build 行数（旧 → 新） |
|---|---:|---:|
| 00000005 | 22 → 21 | 66 → 16 |
| 00000007 | 4 → 5 | 11 → 7 |
| 00000017 | 131 → 42 | 262 → 62 |
| 00000018 | 35 → 36 | 72 → 22 |
| 00000019 | 35 → 36 | 72 → 22 |

64 实体样本仍保留全部独立尺寸、位置和源覆盖，使用 15 个严格匹配的模板；没有按近似坐标强行合并其余结构。多边形的固定局部坐标仍是显式几何，当前未自动转成矩形长宽等语义参数。

## 产物及复现

- [真实 ABC 回归报告](../out/abc-step-readable-v1-regression/regression.json)
- [每例指标 summary.json](../out/abc-step-readable-v1-regression/summary.json)
- [summary.csv](../out/abc-step-readable-v1-regression/summary.csv)
- [结构与指标机器记录](step-v1-validation-summary.json)
- [旧版验证记录](VALIDATION.md)，保持原版本和原产物。

```powershell
& .\envs\simplecad\Scripts\python.exe -m step2code.tests.run_abc_regression `
  --input .\datasets\abc\step\abc_0000 `
  --out .\step2code\out\my-abc-regression `
  --baseline .\step2code\out\abc-first20-v1\summary.json `
  --limit 20 --samples 8192 --timeout 120
```

输出目录须为新目录。转换失败保持逐例原始原因；编辑失败只改变独立的编辑评价，不伪装为默认几何重建失败。当前仍只支持可证明的解析直线棱柱，不支持一般旋转、多层实体分解、样条或自由曲面；本次不是全量数据集评估。
