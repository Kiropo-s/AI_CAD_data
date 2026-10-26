# STEP 完整旋转扩展验证（0.3.0）

验证日期：2026-10-26。规则仍为 `step-readable-v1`，识别器为 `analytic-prism-revolution-v3`。本次结果用于几何重建，不代表恢复了原始建模历史或装配层级。

## 本次实现

- 保留解析直线棱柱优先识别；失败后尝试有限个解析曲面轴线候选，提取半边轴截面并验证 360° 旋转重建。
- 新增球、圆锥、圆台、圆环、阶梯轴、空心球、斜轴旋转体，以及同一文件中的拉伸体/旋转体混合输出。能力依赖实际截面和源实体重合证明，不按文件名或面类型直接接受。
- 截面支持直线、圆、圆弧及有界内核 B 样条重放。保留控制点、次数、节点、重数、权重与周期信息，转换器不做采样拟合；内核求交仍可能给出近似 B 样条表示，不能宣称一般自由曲面精确恢复。
- 纯拉伸继续使用 CAD IR 0.1；含旋转时采用独立 `format=step-reconstruction-plan-v1`、`plan_version=1`，不为上游 CAD IR 伪造 revolve 支持。
- 生成代码只导入 `simplecadapi`，组织为公开参数 → 显式截面函数 → 特征函数 → build。每个源实体保持独立绑定和来源映射。
- 原生旋转公开 `PROFILE_SCALE` 与 origin/x_axis/y_axis。比例围绕选定轴原点均匀作用，体积按 s³ 变化；截面内部固定坐标不冒充已恢复的独立半径、长度或设计约束。

## 验证口径

- 使用现有 `F:\AI_CAD\envs\simplecad\Scripts\python.exe`，未安装新依赖。
- 默认 world 坐标；每个形状采样 8192 点、seed=0；CD/L ≤ 0.01、IoU ≥ 0.999、相对 V 误差 ≤ 0.001。实际 build 与 STEP 导出回读也使用 world 核查。
- 旋转候选先进行必要方向/同轴检查，以及 bbox/体积提前拒绝。bbox 阈值为 max(100 × length_tolerance, 0.001 × 源 bbox 对角线)，体积相对误差阈值为 0.001；两者只用于拒绝。
- 接受旋转候选仍要求有效单实体、正有限体积，且与源实体双向 Cut 的相对对称差 ≤ 1e-7。生成后还须通过源覆盖、逐实体、回读及 CD/IoU/V；本次没有放宽验收阈值。
- 默认最多 8 个旋转候选轴、16 个截面面、每面 256 条截面边、每条样条 256 个控制点。超出当前范围保持 provisional，不据此断言数学上不存在其他重建方式。

## 自动测试与保存样例

37 项 unittest 全部通过，用时 14.007 秒，包含旋转、混合实体顺序、独立尺寸/位置编辑、错误旋转拒绝、能力上限、派生截面来源、带权三次 B 样条、部分覆盖拒绝、负体积拒绝、非法计划语义拒绝，以及独立解释器运行/确定性。见 [测试日志](../out/extension-unittest.log)。

10 个单独保存的合成 STEP 能力样例全部 train_ready：sphere、cone、frustum、torus、stepped_shaft、hollow_sphere、oblique_frustum、rational_spline_revolution、mixed_prism_first、mixed_rotation_first。它们与真实 ABC 输入分开统计；每例均实际 build、导出回读、核查源覆盖并进行几何验收。见 [能力样例报告](../out/rotation-review-v3/run/summary.json) 和 [代码结构审阅](../out/rotation-review-v3/run/structure-review.json)。

最终产物一致性检查通过：16 个源 Python 文件语法有效，16 个通过产物（6 个真实 ABC、10 个能力样例）的输入哈希、来源覆盖、回读状态、独立评价标志、导入依赖和函数名称对应均一致。旋转元数据的 scope 描述澄清为“measured kernel meridian sections”；11 个组合输出重新生成的 Python 字节与已完成几何测试的代码完全一致，仅修正文案，不改几何。见 [产物审计](../out/abc-expanded-v3-verified/artifact-audit.json)。

## 真实 ABC 前 20 例

输入为 `F:\AI_CAD\datasets\abc\step\abc_0000`，按文件名排序取 00000000–00000019，每例独立进程限时 120 秒。

| 状态 | 扩展前 0.2.0 | 扩展后 0.3.0 |
|---|---:|---:|
| train_ready | 5 | 6 |
| provisional | 15 | 14 |
| rejected | 0 | 0 |

原先通过的 00000005、00000007、00000017、00000018、00000019 全部继续通过，新增 00000009。20 个源文件与基线 SHA256 一一相同。回归检查为 pass，无 worker_error、超时或崩溃；14 个 provisional 的原因全部是 unsupported_geometry。该有序前缀不能作为全量 ABC 通过率。

6 个通过样本分别测试首实体尺寸 ×1.1 和 origin 平移 (3,-4,8) mm，共 12 项有限编辑全部通过，并检查其他实体保持原状。旋转样本 scale 从 1 改为 1.1，实测体积比 1.3309999999999973，符合 1.1³。编辑状态保留 partial，表示未遍历全部公开参数。assembly_validation 仍为 unsupported，history_recovered 仍为 false。

| ID | 实体数 | CD (mm) | CD/L | IoU 原值 | 相对 V 误差 |
|---|---:|---:|---:|---:|---:|
| 00000005 | 10 | 2.52433421142 | 0.00382065335262 | 1.0000000001961133 | 1.96113550093e-10 |
| 00000007 | 1 | 1.33997632947 | 0.00596966760659 | 1 | 1.75987095950e-16 |
| 00000009 | 1 | 1.18468767278 | 0.00761736982465 | 0.9999999999844705 | 1.69151556050e-11 |
| 00000017 | 64 | 1.60617160609 | 0.00372575990601 | 1 | 0.00000000000 |
| 00000018 | 16 | 1.20621246579 | 0.00381061684709 | 1.000000000026615 | 2.66150886550e-11 |
| 00000019 | 16 | 1.20621246579 | 0.00381061684709 | 1.000000000026615 | 2.66150886550e-11 |

新增 00000009 由一个完整旋转重建，6 个函数、build 7 行，识别阶段的源/候选相对对称差为 0。见 [可读代码](../out/abc-expanded-v3-verified/cases/00000009_9b3d6a97e8de4aa193b81000_step_001/model.scapi.py) 和 [逐例报告](../out/abc-expanded-v3-verified/cases/00000009_9b3d6a97e8de4aa193b81000_step_001/report.json)。采样 CD 不是精确曲面距离，等价 BREP 的网格不同也可产生非零 CD；微小超过 1 的 IoU 保留原值并沿用数值可靠性检查。

## 剩余 14 个 provisional

下表记录当前算法实际停止的实体及诊断。已识别数表示停止前识别成功的源实体数量，不代表其余实体均被检查；只要有一个实体不支持，整个 case 不输出部分通过模型。

| ID | 已识别实体 | 停止于源实体 | 当前失败诊断 |
|---|---:|---:|---|
| 00000000 | 3 | 4 | 候选轴下存在非垂直端面；第 4 实体未被当前策略重建 |
| 00000001 | 0 | 1 | 当前候选轴下解析曲面不同轴或平面方向不兼容 |
| 00000002 | 0 | 1 | 球心偏离候选轴或解析曲面不同轴 |
| 00000003 | 0 | 1 | 当前候选轴下解析曲面不同轴 |
| 00000004 | 0 | 1 | 当前候选轴下解析曲面不同轴 |
| 00000006 | 0 | 1 | 候选轴下存在非垂直平面；拉伸差异检查也未通过 |
| 00000008 | 0 | 1 | 候选轴下存在非垂直平面；当前拉伸轮廓含不支持曲线 |
| 00000010 | 0 | 1 | 候选轴下平面方向不兼容或解析曲面不同轴 |
| 00000011 | 0 | 1 | 当前候选轴下解析曲面不同轴；拉伸轮廓含 B 样条 |
| 00000012 | 0 | 1 | 当前候选轴下解析曲面不同轴；拉伸轮廓含 B 样条 |
| 00000013 | 0 | 1 | 当前候选轴下平面方向不兼容或解析曲面不同轴 |
| 00000014 | 2 | 3 | 第 3 实体旋转候选体积相对误差 0.001092978，提前拒绝 |
| 00000015 | 0 | 1 | 当前候选轴下平面方向不兼容或解析曲面不同轴 |
| 00000016 | 1 | 2 | 第 2 实体拉伸重合检查失败，旋转轴与平面方向不兼容 |

00000000 现已识别前 3 个旋转体，但第 4 实体不支持，因此仍为 provisional。00000014 已识别前 2 个旋转体，第 3 实体候选的体积检查失败；本轮总转换耗时 0.989 秒。开发中此样本曾触发 120 秒限时，原始记录保留在 `out/abc-expanded-v3-regression`；该中间运行不作为本次最终结果。新增提前筛选和回归工具对 worker_error 的失败检查，避免再将超时混作正常能力拒绝。

## 仍未覆盖的能力

一般非轴对称实体的多步布尔特征分解、任意角度旋转、通用样条轮廓拉伸、任意 NURBS 曲面、放样/扫掠、原始圆角/倒角特征恢复仍不支持。某些轴对称过渡可通过旋转重建，但不能标记为恢复了原始圆角历史。原始装配层级和设计参数关系也尚未恢复。

## 产物与复现

- [最终真实 ABC 回归](../out/abc-expanded-v3-verified/regression.json)
- [逐例 summary.json](../out/abc-expanded-v3-verified/summary.json) / [summary.csv](../out/abc-expanded-v3-verified/summary.csv)
- [机器可读验证汇总及代码 SHA256](rotation-v3-validation-summary.json)
- [更新后的结构化规则](specs/STEP_READABLE_CODE_SPEC.md)
- [0.2.0 验证记录](STEP_V1_VALIDATION.md)，保留原版本和原产物。

从 `F:\AI_CAD` 运行，输出使用新的目录：

```powershell
& .\envs\simplecad\Scripts\python.exe -m unittest discover -s step2code\tests -v

& .\envs\simplecad\Scripts\python.exe -m step2code.tests.run_abc_regression `
  --input datasets\abc\step\abc_0000 `
  --out step2code\out\my-rotation-regression `
  --baseline step2code\out\abc-step-readable-v1-regression\summary.json `
  --limit 20 --samples 8192 --timeout 120
```
