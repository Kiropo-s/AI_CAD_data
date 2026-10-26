# CSG 与草图拉伸混合验证（0.4.0）

日期：2026-10-26。工具为 step2scapi 0.4.0，识别器为 analytic-prism-revolution-csg-v4，代码结构规则仍为 step-readable-v1。使用现有 `F:\AI_CAD\envs\simplecad\Scripts\python.exe`，修改限于 step2code，未改 json2scapi 或安装新依赖。

## 实现范围

原有拉伸和完整旋转优先识别；两者失败时，对当前源实体尝试：

1. 从源端面提取明确外轮廓/孔并拉伸，或由包围盒创建矩形拉伸主体。
2. 从解析平面、圆柱、圆锥、球和圆环构造有限工具体，以 cut / intersect / union 形成候选；累计核查切削是否删除源材料。
3. 残余缺料/多料只有在可独立识别为受支持拉伸或旋转时，才生成显式局部操作。
4. 直接 CSG 失败后按源平面有限分区，各局部体重新识别为拉伸、旋转或短 CSG 配方，再联合验证原始实体的完整覆盖。
5. 合并关联并集，避免为分离的中间区域强制创建单一 Solid。没有人工加桥或扩大布尔容差来连接零件。

生成程序仅导入 simplecadapi。公开独立的 origin/x_axis/y_axis 和 PROFILE_SCALE；截面函数、工具体工厂及特征操作显式可读，build 只负责组合。内部固定曲线与工具尺寸不冒充原始设计尺寸或约束。每次 build 新建几何。

含 CSG 的计划使用 `format=step-reconstruction-plan-v1`、`plan_version=1`，不写 CAD IR 0.1 的 ir_version；纯拉伸保留原格式。残余/分区来源使用 derived_residual / derived_partition 和对应局部索引，不将派生边面编号写成原始源索引。源覆盖依然将每个原始实体映射到一个真实返回 body，不把 12 个内部局部体当成 12 个源实体。

## 测试和最终回归

- **44 项 unittest 全部通过**，19.813 秒；包含实际几何、导出回读、混合孔、盲孔、局部凸台、锥面多边形、参数独立性、限额、非法 recipe 拒绝、确定性与隔离解释器运行。见 [测试日志](../out/csg-unittest-final.log)。
- **4 个保存的合成混合样例全部 train_ready**：blind_hole、crossed_blind_holes、localized_boss、tapered_polygon。每例实际 build、导出回读并验收，独立于真实 ABC 统计。见 [样例报告](../out/csg-review-v4/run/summary.json)。
- **真实 ABC 同一前 20 例：8 train_ready / 12 provisional / 0 rejected**。原 0.3.0 的 6 个通过样本全部保留，新增 00000006 与 00000008。20 个源文件 SHA256 与基线全部一致；原 6 个通过样本的生成 Python 代码字节也保持一致。
- **回归检查 pass，无 worker_error、超时或进程崩溃**；12 个 provisional 均为 unsupported_geometry。有限搜索未找到解不表示数学上无解。
- **8 个通过样本共 16 项有限编辑全部通过**：首实例的 distance ×1.1，或原生旋转/CSG 的 profile_scale ×1.1；以及 origin 平移 (3,-4,8) mm。检查真实重新构建、拓扑有效、预期体积比、位置与其他实体不变。状态保留 partial，未遍历全部公开参数。
- 产物一致性审计 pass：20 个源 Python 文件语法有效；24 个真实/合成源哈希核对；12 个通过产物的导入、函数/参数绑定、来源标志、逐体/回读及独立评价一致。组合程序重新生成的代码字节完全一致。见 [审计报告](../out/abc-csg-v4-regression/artifact-audit.json)。

| 状态 | 0.3.0 | 0.4.0 |
|---|---:|---:|
| train_ready | 6 | 8 |
| provisional | 14 | 12 |
| rejected | 0 | 0 |
| 本组通过比例 | 30% | 40% |

提升为 **10 个百分点、增加 2 个文件**。样本选择是按文件名排序的前 20 个 STEP，非随机样本；未运行全量 1,000 例，不能据此声明全量成功概率。

## 两个新增通过样本

| ID | 重建策略 | CD (mm) | CD/L | IoU 原值 | 相对 V 误差 | 转换耗时 |
|---|---|---:|---:|---:|---:|---:|
| 00000006 | 带孔六边形草图拉伸 ∩ 圆锥 | 0.0290669666021 | 0.00412726934087 | 0.9999999443164165 | 7.27960703658e-8 | 2.161 s |
| 00000008 | 源平面分区，局部拉伸/旋转/CSG 并集 | 0.702653167034 | 0.00652521335175 | 1.000000001258186 | 1.80092970651e-9 | 46.521 s |

00000006：1 次布尔运算，108 行、10 个函数、build 7 行。重放时按解析有向面积调整环输入方向，匹配 SCAPI 对孔环自动反向的约定，不拟合或移动曲线点。见 [生成代码](../out/abc-csg-v4-regression/cases/00000006_d4fe04f0f5f84b52bd4f10e4_step_001/model.scapi.py) 和 [逐例报告](../out/abc-csg-v4-regression/cases/00000006_d4fe04f0f5f84b52bd4f10e4_step_001/report.json)。

00000008：12 个局部体、26 次二元等价布尔运算，596 行、62 个函数、build 7 行。完成了自动重建和验证，但配方较长，尚未进一步合并等价相邻分区或恢复简洁设计特征。见 [生成代码](../out/abc-csg-v4-regression/cases/00000008_9b3d6a97e8de4aa193b81000_step_000/model.scapi.py) 和 [逐例报告](../out/abc-csg-v4-regression/cases/00000008_9b3d6a97e8de4aa193b81000_step_000/report.json)。

表中是最终源参考与实际导出回读候选的指标；不能把识别阶段的对称差 0 当作导出后误差 0。采样 CD 不是精确曲面距离；微小大于 1 的 IoU 保留原值并沿用数值可靠性检查。

## 几何门槛和预算

验收沿用 world 坐标、每形状 8192 点、seed=0：CD/L ≤ 0.01、IoU ≥ 0.999、相对 V 误差 ≤ 0.001。候选识别及实际返回模型逐源实体覆盖仍要求双向 Cut 相对对称差 ≤ 1e-7，STEP 回读继续独立检查。没有放宽门槛。

默认每次直接 CSG 搜索最多 96 个候选、边界最多 80 面；每源实体最终 recipe 最多 32 次二元等价布尔操作；分区最多 24 个局部体尝试、深度 3。CSG 的 15 秒是协作式识别预算，不能中断正在运行的原生调用，整例以 120 秒独立进程限时约束。本次单例转换耗时包含导出与几何测量，不等于 CSG 识别耗时。

## 剩余 12 个 provisional

| ID | 停止于源实体 | 停止前已识别 | 当前原因 |
|---|---:|---:|---|
| 00000000 | 4 | 3 | 第 4 实体含非解析边界，当前 CSG 门槛拒绝；前 3 实体仍已识别 |
| 00000001 | 1 | 0 | 失败实体含 B 样条面；当前解析 CSG 不覆盖 |
| 00000002 | 1 | 0 | 解析边界候选在本轮预算内未构成完整重建 |
| 00000003 | 1 | 0 | 失败实体含 B 样条面；当前解析 CSG 不覆盖 |
| 00000004 | 1 | 0 | 失败实体含 B 样条面；当前解析 CSG 不覆盖 |
| 00000010 | 1 | 0 | 失败实体含 B 样条面；当前解析 CSG 不覆盖 |
| 00000011 | 1 | 0 | 含大量 B 样条面且边界面数超限 |
| 00000012 | 1 | 0 | 含大量 B 样条面且边界面数超限 |
| 00000013 | 1 | 0 | 含 B 样条面且边界面数超限 |
| 00000014 | 3 | 2 | 第 3 实体含 B 样条面；前 2 个旋转体已识别 |
| 00000015 | 1 | 0 | 失败实体含 B 样条面；当前解析 CSG 不覆盖 |
| 00000016 | 2 | 1 | 第 2 实体的直接 CSG 与有限分区仍未完整重建；整例不通过 |

其中 10 个失败实体触发当前解析边界/面数门槛；00000002 和 00000016 在当前有限候选与分区策略下没有完成重建。存在任一未覆盖源实体时，整例不输出部分通过模型。一般自由曲面分解、任意 NURBS、放样/扫掠、通用样条拉伸和原始圆角/倒角历史仍未恢复。assembly_validation=unsupported，history_recovered=false。

## 文件与复现

- [最终回归报告](../out/abc-csg-v4-regression/regression.json)
- [逐例 summary.json](../out/abc-csg-v4-regression/summary.json) / [summary.csv](../out/abc-csg-v4-regression/summary.csv)
- [机器可读汇总及代码 SHA256](csg-v4-validation-summary.json)
- [结构化规则](specs/STEP_READABLE_CODE_SPEC.md)
- [0.3.0 验证记录](ROTATION_V3_VALIDATION.md)，历史产物保留原版本。

从 `F:\AI_CAD` 运行，输出目录使用新的名称：

```powershell
& .\envs\simplecad\Scripts\python.exe -m unittest discover -s step2code\tests -v

& .\envs\simplecad\Scripts\python.exe -m step2code.tests.run_abc_regression `
  --input datasets\abc\step\abc_0000 `
  --out step2code\out\my-csg-regression `
  --baseline step2code\out\abc-expanded-v3-verified\summary.json `
  --limit 20 --samples 8192 --timeout 120
```
