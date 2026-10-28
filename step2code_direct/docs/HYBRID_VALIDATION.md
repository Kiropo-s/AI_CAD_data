# 草图拉伸 / CSG 混合版验证记录

日期：2026-10-26。版本 `step2scapi-direct 0.2.0`，代码结构 `step-direct-readable-v2`。

## 实现

- 单次解析直线拉伸优先；失败后才尝试有界原生几何混合重建。
- 候选来自可构造的源端面拉伸、解析圆柱/圆锥/球/圆环，以及明确标记为 derived 的世界 bbox 盒体。
- 从候选中搜索 union/cut/intersect，使用双向 Cut 对称差评估，每一步须保留一个有效闭合实体。
- 保留少量外表面解析候选，避免空心体的外壳因初始多余体积较大而被过早淘汰。
- 最终源码是显式 SCAPI 基本体、拉伸与布尔调用；不调用旧 CAD IR 层，不嵌入源 BREP/STEP/残差，也没有运行时 opcode 解释器。
- 各源实体最终仍单独核查；再执行 STEP 回读和 CD/IoU/V 验收。参数只在 build 时读取。
- 基本体位置为测得的世界坐标；混合实体 BODY 定位是最后施加的世界刚性增量，默认恒等，旋转绕世界原点。刚性定位使用 rotate/translate，避免通用 GTransform 改变解析表示的数值问题。

原 `step2code` 和旧版直接路线的产物保留。

## 自动测试与保留样例

**31 项 unittest 全部通过**，包含原有 20 项与 11 项混合测试。日志：[unittest-hybrid-final-v2.log](../out/unittest-hybrid-final-v2.log)。

12 类原有几何样例继续覆盖；另有 12 类混合/基本体样例实际 build、导出回读、逐实体及三指标验证，全部 train_ready，并保留完整产物：

| 样例 | 验证重点 |
| --- | --- |
| sphere | 直接球体重建 |
| cone | 圆锥/截锥基本体 |
| boss_union | 草图拉伸 + 圆柱并集 |
| countersink_cut | 拉伸孔体 + 圆锥切除 |
| blind_hole_cut | 盲孔切除 |
| tilted_hole_cut | 斜孔工具体 |
| hemisphere_intersect | 球体与盒体交集 |
| full_torus | 圆面旋转构造完整圆环 |
| rounded_elbow | 部分圆环与端部球体并集 |
| oblique_boss | 任意方向底座与凸台 |
| oblique_countersink | 任意方向锥形沉孔 |
| mixed_source_bodies | 普通拉伸与新增基本体的多源实体输出 |

产物：[hybrid-review-final-v2/summary.json](../out/hybrid-review-final-v2/summary.json)。测试确认示例中的 union、cut、intersect 调用确实出现在生成源码中。

还验证了：Python `-I` 独立运行、仅导入 simplecadapi、确定性、重复 build 新建几何、球半径编辑、刚性位置/任意轴旋转/接近 180° 的定位、单实体对应检查、关闭混合回退、软预算/尝试预算耗尽、Bezier 曲面不支持时明确停止，以及先切内锥再并底座的空心锥体搜索。

这些是有限参数测试。普通输出仍标记 parameter_edit_validation=not_run，history_recovered=false，装配树 unsupported。

## 同版本开关对照：ABC 首 20 例

输入 `F:\AI_CAD\datasets\abc\step\abc_0000`，按文件名取 `00000000–00000019`。相同源 SHA256；相同环境；world 模式；每形状 8192 点；每案例 120 秒硬超时；阈值 CD/L≤0.01、IoU≥0.999、相对 V 误差≤0.001。启用混合时每源实体另有 20 秒软搜索预算、最多 256 次布尔尝试。

| 配置 | train_ready | provisional | rejected | 通过率 |
| --- | ---: | ---: | ---: | ---: |
| 当前 0.2.0，--no-hybrid | 5 | 15 | 0 | 25% |
| 当前 0.2.0，启用混合 | 6 | 14 | 0 | 30% |

全部 20 个文件匹配，原有 5 个成功样本保持通过，新增 `00000009`。**这是加入混合策略带来的该批次新增成功，不是“删除 IR 导致提升”的证据**；新增搜索也消耗更多时间。

- [关闭混合的 summary](../out/abc-first20-prism-only-v02/summary.json)
- [混合版审计后的 summary](../out/abc-first20-hybrid-audited-v02/summary.json)
- [开关对照 Markdown](../out/compare-hybrid-on-off-v02/COMPARISON.md)
- [逐例对照 JSON](../out/compare-hybrid-on-off-v02/comparison.json)
- [与原 0.3.0 IR/plan 路线的历史对照](../out/compare-hybrid-vs-irplan-v03/COMPARISON.md)

开关对照中全部病例的共享导入/运行时/度量源码指纹一致。原 0.3.0 历史路线同样通过 6 例，但该历史对照含能力和评估器版本差异，以报告提示为准。

完整初次运行在 `out/abc-first20-hybrid-final-v2`。运行期间 `00000010` 读到了一个随后撤回的源码结构统计改动，且该例在识别阶段停止，没有生成代码。为固定源码指纹，单独以最终源码重跑该例，源哈希、配置、provisional 状态保持一致；审计 summary 引用其新 report，保留原 report。全部 20 个原始输入均保留，未按成功结果筛选。审计内容在 summary.audit 中。

## 新成功样本 00000009

重建表达式：

```text
body = 外截锥 − 内截锥
body = body ∪ 底部圆柱
```

这是推断方案，不声明恢复原建模历史。实际代码、执行结果及回读验证均通过：

| 指标 | 结果 |
| --- | ---: |
| CD/L | 0.007547214997607582 |
| IoU | 0.9999999999688404 |
| 相对体积误差 | 3.241018753689771e-11 |

[生成代码](../out/abc-first20-hybrid-final-v2/cases/00010_00000009_9b3d6a97e8de4aa193b81000_step_001/model.scapi.py) · [生成 STEP](../out/abc-first20-hybrid-final-v2/cases/00010_00000009_9b3d6a97e8de4aa193b81000_step_001/model.step) · [报告](../out/abc-first20-hybrid-final-v2/cases/00010_00000009_9b3d6a97e8de4aa193b81000_step_001/report.json)

## 剩余限制

14 个 provisional 中，13 个为未找到已验证方案，1 个（00000014）触发 120 秒 worker 超时。搜索停止原因：

- 10 个达到 20 秒软预算。
- 2 个达到 256 次布尔尝试上限。
- 1 个在当前候选、深度和单调改善约束下没有解。
- 1 个 worker 超时。

耗尽预算不是证明模型不可重建。原生 OCC 的单次调用无法被软预算中途打断，worker 硬超时仍必须保留。00000014 的部分源实体已找到候选，但没有完整输出，不能算作成功。

一般 B 样条/椭圆草图、自由曲面、任意旋转截面、通用圆角/倒角、放样/扫掠和无限制特征分解仍不支持。整个实验只处理首 20 个真实 ABC 输入，没有验证全部 10,000 例，不能外推总体成功率。

## 导出的查看文件

已将 6 个通过案例的代码生成 STEP 复制到 [exports/abc-first20-hybrid-code-generated-step-v02](../exports/abc-first20-hybrid-code-generated-step-v02)，每份副本经过 SHA256 核对，manifest 记录对应源码和 report。

机器记录：[hybrid-validation-summary.json](hybrid-validation-summary.json)。
