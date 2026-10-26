# step2code / step2scapi v0.4

将 STEP/STP 的边界几何识别为可验证的直线拉伸、完整旋转或有界 CSG 组合方案，生成独立可读的 SimpleCADAPI 程序，并以原始 STEP 为参考，验证实际 `build()` 返回值和导出的 STEP。

当前链路：

```text
原始 STEP（记录源单位，显式导入为 mm）
  → 按实体识别拉伸 / 旋转，失败后搜索有限 CSG 与平面分区组合
  → CAD IR 0.1（纯拉伸）/ STEP 重建计划 v1（含旋转或 CSG）
  → step-readable-v1 SCAPI（复用 readable v3 草图准备）
  → 实际执行 build()
  → 导出、回读 STEP
  → CD + IoU + V 验收及报告
```

转换工具依赖同仓库的 `json2scapi` 代码生成器和几何度量模块。**生成的 `model.scapi.py` 只导入 `simplecadapi`，不读取原始 STEP、不依赖 IR 或本工具运行时。**

## 运行

使用已有 `F:\AI_CAD\envs\simplecad` 环境。以下命令在 `F:\AI_CAD` 执行：

```powershell
# 帮助；也支持 python -m step2code
& .\envs\simplecad\Scripts\python.exe .\step2code\step2scapi.py --help

# 单个已验证可支持的 ABC 圆柱
& .\envs\simplecad\Scripts\python.exe .\step2code\step2scapi.py `
  --input .\datasets\abc\step\abc_0000\00000007_b33a147f86da49879455d286_step_000.step `
  --out .\step2code\out\my-cylinder

# 按文件名排序，先批量处理前 20 个 STEP；不递归、不处理压缩副本
& .\envs\simplecad\Scripts\python.exe .\step2code\step2scapi.py `
  --input .\datasets\abc\step\abc_0000 `
  --limit 20 --out .\step2code\out\my-abc-first20
```

省略 `--out` 会创建带时间戳的新目录。指定的输出目录必须为空，已有产物不会覆盖。子目录输入可加 `--recursive`；同一次运行中不允许文件 stem 重名。支持 `.step`、`.stp`，后缀大小写不敏感。移除 `--limit` 可处理整个输入目录，但这次开发仅执行了前 20 例，没有处理全部 1,000 例。

每例在独立子进程中运行，默认总超时为 300 秒，可用 `--timeout` 修改。单例超时、进程崩溃或不支持不会终止后续批处理。退出码：全部通过为 `0`，存在 rejected/provisional 为 `1`，命令参数错误为 `2`。

## 当前支持范围

- 闭合、有正体积的直线拉伸实体，方向可任意；支持多个实体及装配位置烘焙后的几何。
- 平面轮廓由直线、完整圆、三点圆弧构成；支持大于半圆的弧。
- 保留外轮廓和孔，圆半径及拉伸距离使用顶部独立参数。
- 端面按面积、平面位置、法向及原始面序号确定性选择；候选必须通过 bbox 和双向 Cut 的对称差检查，默认对称差/实体体积不大于 `1e-7`，不能只靠体积相等确认形状。
- 拉伸路径中，每个源实体生成一个 `new` 拉伸。多实体继续使用真实 body 集合及 SCAPI Assembly 输出，不按 bbox 拼接或把原文件封装为代码。

- 新增完整 360° 旋转体路径：支持经验证的球、圆锥/圆台、圆环、空心球、轴向台阶，以及拉伸和旋转实体混合的模型。
- 从源曲面轴线生成有限候选，先检查边界平面的方向及解析曲面的同轴性，再提取半边轴截面。旋转候选先用宽松 bbox 与相对体积误差进行提前拒绝，避免对明显不匹配的候选执行耗时布尔运算；这些筛选不能证明重建成功。保留下来的候选必须通过与源实体的双向 Cut，相对差异仍不大于 `1e-7`。
- 轴截面支持直线、圆弧、完整圆及有界的内核 B 样条。B 样条保留裁剪后的控制点、节点、重数、权重和周期性，转换器不进行采样拟合；内核求交自身的误差仍需通过源实体与最终输出验证。
- 闭合圆截面在生成代码中写成两段精确半圆弧，改善圆环周期接缝的 STEP 回读稳定性。

- 新增有界 CSG 后备路径：以源端面草图拉伸或包围盒拉伸为主体，组合解析圆柱、圆锥、球和圆环工具体的 cut / intersect / union。
- 每次单调切削检查累计缺失的源体积；剩余差异仅在能独立识别为拉伸/旋转时，才写成显式残余体操作。必要时按源平面有限分区，逐块识别并组合。
- 分区或残余体的边、面编号明确标记为派生来源；与原始 STEP 的 source_edge_index / source_face_index 分开。整个源实体仍须通过双向 Cut 和生成后验收。
- 关联并集合并成一次多操作数调用，避免强制输出分离的中间 Solid。公开 CSG 的 PROFILE_SCALE 与位置/方向参数；工具体内部尺寸目前保持固定局部几何，不声称恢复了原始设计关系。

**尚不支持**任意角度旋转识别、无界多步布尔搜索、一般自由曲面特征分解、任意圆角/倒角恢复、放样、扫掠、通用样条轮廓拉伸，以及任意 NURBS 曲面重建。轴对称过渡若能通过完整旋转验证则可重建，不代表恢复了原始圆角特征。仅表面、无闭合实体的文件也不支持。存在任一不支持实体时，整个 case 保持 provisional；诊断记录已识别实体数量，但不输出部分通过模型。

可用 `--max-revolve-candidates`（默认 8）、`--max-section-faces`（16）、`--max-section-edges`（256）、`--max-spline-poles`（每曲线 256）限制识别规模。

CSG 默认限额：`--max-csg-candidates 96`、`--max-csg-steps 32`（按二元等价操作数计）、`--max-csg-faces 80`、`--max-csg-parts 24`、`--max-csg-depth 3`、`--max-csg-seconds 15`。深度 0 可关闭分区，仍保留直接 CSG。15 秒为协作式识别预算，不能中断正在执行的原生布尔调用；整例硬限时由 `--timeout` 控制。候选上限按每次直接 CSG 搜索计，分区范围另外受层数、局部体尝试数和共享时间约束。

STEP 通常不包含可恢复的完整建模过程。本工具输出的是**经几何证据确认的重建方案**，不是原始草图/特征历史；IR、symbols 和 report 均记录 `history_recovered=false`。

## 可读代码结构

STEP 专用规则见 [STEP_READABLE_CODE_SPEC.md](docs/specs/STEP_READABLE_CODE_SPEC.md)，规范标识为 `step-readable-v1`。它定义重建操作、参数来源与编辑策略、形状和定位分离、受控模板以及分层验收，不改变 DeepCAD 原有规则。

`step2scapi 0.4.0` 在 STEP 专用层组合拉伸、旋转和 CSG 生成器；DeepCAD 的原 readable v3 emitter 保持原逻辑。STEP 层只复用其 IR 准备、数值和安全草图构建能力。

1. 顶部保留各实体独立的 `FEATURE_*` 尺寸和 `BODY_*` 定位参数，不因默认值相等而共享参数。
2. `make_profile_01(*, origin, x_axis, y_axis, ...)` 显式接收定位；局部轮廓坐标保留在草图函数中。
3. `apply_feature_01(bodies, ...)` 明确执行拉伸或完整旋转；方向/轴线从坐标架取得，并检查其有限、正交且单位化。旋转路径显式构造截面曲线、wire 和 face，草图函数统一返回有序 face 元组。
4. 纯拉伸模型中，准备后的草图 AST 在参数、定位显式绑定后完全一致时才复用模板；这是代码复用，不是恢复阵列或设计约束。固定局部坐标不同的多边形不会强制归并。
5. `build()` 调用时读取全部顶部参数。纯拉伸路径中，相邻的三个及以上同模板实例使用命名字段列表和明确的工厂循环；每次构建均创建新几何，`BODIES` 存储世界实体。
6. `_as_model()` 以恒等定位包装多实体，避免重复施加变换。

`model.symbols.json` 记录参数来源/编辑策略/绑定/证据、模板实例、重建操作和源面边索引。拉伸距离优先测量平行端面间距；必要时的 `V/A` 回退明确记为 derived。生成后逐源实体核查双向 Cut，缺失、错配或未覆盖面不能获得完整通过。

不生成通用几何解释器、不嵌入 BREP/STEP/base64，也不导入 OCP。坐标采用往返 float 表达；拉伸准备沿用 `ir-geometry-v1`，旋转采用 `verified-kernel-meridian-v1`，各操作记录自己的 preparation。`symbols.json` 保留函数、参数、源面/边、结构指标等信息。

## CD / IoU / V 验证

默认 `--validation-mode world` 使用 **fixed-frame-shape-v1**：比较原始 STEP 与生成后回读 STEP 的实体空间并集，保留双方原始尺寸和相对位置，不单独配准候选。为数值稳定，只对双方施加同一个由参考形状确定的平移和等比缩放；报告中的 CD 距离和 V 还原为源 mm / mm³。

复用现有 v4 度量实现、阈值和布尔可靠性核查：

| 指标 | 定义 | 通过条件 |
|---|---|---|
| CD | 面积加权表面采样，双向最近邻平均距离；另保存双向平方距离均值之和 CD² | `CD / L <= 0.01` |
| IoU | `V(A ∩ B) / (V(A) + V(B) - V(A ∩ B))`，使用 BREP Common | `IoU >= 0.999` |
| V | `abs(V_candidate - V_reference) / V_reference` | `<= 0.001` |

`L = sqrt(6 × trace(参考体中心惯性张量) / V_reference)`。默认每个形状采样 8,192 点，seed=0，网格线性偏差为 `0.001 × L`；`--samples` 可调整采样数，下限 256。采样 CD 不是精确曲面距离，即使两个 BREP 几何相同，采样点/网格不同也可能得到非零 CD。

IoU 测量失败不是 IoU=0，也不被 CD 替代；微小正向数值越界保留原值，并由双向 Cut 核查后判断，不能直接截断成 1。体积采用实体空间并集，避免多实体重叠区域重复计数。原始实体数量不作为额外形状门槛。

可显式指定 `--validation-mode similarity`，完整复用 [similarity-shape-v4](../json2scapi/docs/specs/EVALUATION_SPEC.md) 的整体等比缩放、平移及正规旋转验收。该模式允许候选整体尺寸变化，V 误差是归一化体积误差；原始 V 保留在 geometry.raw 中，summary 的体积列也保留原始值。该模式的 CD 距离为归一化测量空间单位，summary 的 `chamfer_coordinate_space` 明确标记 `normalized_mm`。无论选择哪个模式，内存 build 模型与导出回读 STEP 的等价检查始终使用 world 模式。

状态规则：

- `train_ready`：用于 `geometry_reconstruction`，真实 build 有效、源覆盖及逐实体核查通过、STEP 回读和源参考 CD、IoU、V 全部通过。
- `rejected`：代码生成/执行失败，候选导出无效，或可靠测量已确认几何不匹配。
- `provisional`：识别范围不支持、源参考不可读、必需度量失败、超时或原生进程异常。不能作为通过样本。

## 输出

```text
out/<run>/
├─ summary.json                   # 每例状态、指标、报告路径
├─ summary.csv
└─ cases/<输入文件 stem>/
   ├─ model.scapi.py               # 独立 SCAPI 程序
   ├─ model.ir.json
   ├─ model.prepared.ir.json
   ├─ model.symbols.json
   ├─ model.step                  # 实际执行并回读验证的候选
   ├─ report.json                 # 阶段、源文件 SHA256、识别证据、全部验收指标
   └─ worker.log
```

不支持/早期失败的 case 只保留相应阶段已生成的产物及原因。不会伪造缺失的代码、几何或度量。

## 测试和本次实测

```powershell
& .\envs\simplecad\Scripts\python.exe -m unittest discover -s .\step2code\tests -v
```

44 项测试全部通过，覆盖 12 类可重建几何、独立代码重复 build、尺寸编辑、确定性、复杂形状拒绝识别、world 模式下平移/缩放错误、必需度量异常、超时/崩溃隔离，以及保留已有输出。

2026-10-26 对 `abc_0000` 同一前 20 例，0.3.0 为 **6 train_ready / 14 provisional**，0.4.0 为 **8 train_ready / 12 provisional / 0 rejected**。新增通过 `00000006`、`00000008`，原 6 个通过样本均保留，20 个源 SHA256 全部对应一致。本组通过比例从 30% 提高到 40%，增加 10 个百分点；12 个 provisional 均为 unsupported_geometry，无超时/进程错误。8 个通过样本共 16 项有限编辑检查全部通过。该有序前缀不是全量数据集通过率。

当前 CSG 扩展见 [CSG 混合验证](docs/CSG_V4_VALIDATION.md)。[0.3.0 旋转扩展验证](docs/ROTATION_V3_VALIDATION.md) 保留原版本结果。[0.2.0 验证记录](docs/STEP_V1_VALIDATION.md) 和 [0.1.0 记录](docs/VALIDATION.md) 保留原产物。

含旋转或 CSG 时，`model.ir.json` 和 `model.prepared.ir.json` 的内容使用独立标识 `format=step-reconstruction-plan-v1`、`plan_version=1`，不写 CAD IR 的 `ir_version=0.1`，不冒充上游 CAD IR 支持 revolve / CSG。纯拉伸仍输出 CAD IR 0.1。

旋转公开 `FEATURE_*_PROFILE_SCALE` 作为关于所选轴原点的均匀比例参数，并保留 origin/x_axis/y_axis；截面坐标、B 样条控制点和节点等保持局部固定几何，尚不自动推断独立的原始半径、长度或设计约束。
## STEP v1 回归

```powershell
& .\envs\simplecad\Scripts\python.exe -m unittest discover -s step2code\tests -v

# 真实 ABC 前 20 个样本；每例限时 120 秒，8192 点 CD，默认 world。
# 输出目录必须不存在或为空；请为后续运行使用新的目录。
& .\envs\simplecad\Scripts\python.exe -m step2code.tests.run_abc_regression `
  --input .\datasets\abc\step\abc_0000 `
  --out .\step2code\out\my-abc-regression `
  --limit 20 --samples 8192 --timeout 120
```

回归工具可用 `--baseline <旧 summary.json>` 按文件名及 SHA256 比较状态；同时对每个通过样本的第一个实体测试拉伸距离乘 1.1（旋转路径为 profile_scale 乘 1.1，期望体积比 1.1³）、位置平移 `(3,-4,8)` mm，并核查其他实体未受影响。只测试有限参数，成功时记为 `parameter_edit_validation.status=partial`。普通转换不自动运行这些编辑测试，记为 `not_run`。`assembly_validation=unsupported` 表示尚未恢复原始装配层级；`history_recovered=false` 独立保留。

新版本测试记录见 [CSG_V4_VALIDATION.md](docs/CSG_V4_VALIDATION.md)，旧版产物和验证记录保留原版本。


## 基于 100 例的下一代 IR 重构设计

已基于 2026-10-26 保留的 ABC 前 100 例（28 通过、70 provisional、2 rejected）形成一次不兼容的 STEP 专用计划重构方案。设计、基线审计和实施验收见 [ir-redesign/README.md](docs/ir-redesign/README.md)。这是实施前设计；上文仍描述当前 0.4.0 实现。
