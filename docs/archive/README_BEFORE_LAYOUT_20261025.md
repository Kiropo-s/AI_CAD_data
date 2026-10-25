# 整理前的使用说明（2026-10-25 快照）

这是历史文档快照，包含旧接口、旧文件名及多个评估协议。当前入口与有效用法以 [项目 README](../../README.md) 为准。

# json2scapi

当前生产语料验收已改为**形状一致性**：忽略一个整体正等比缩放、平移和旋转，比较所有实体空间并集；禁止镜像、分轴缩放及逐实体独立对齐。`train_ready` 表示容差内形状一致，不承诺原始绝对尺寸、世界坐标、body 身份或完整历史一致。新流程为 `readable-only-v2` / `similarity-shape-v4`，规范见 [EVALUATION_SPEC.md](../specs/EVALUATION_SPEC.md)。下文旧 mixed-86 等统计是历史协议结果，不是新评估的回归结果。

当前仅进行固定局部回归，不运行前50、mixed-86或全量。入口：

```powershell
.\envs\simplecad\Scripts\python.exe json2scapi/run_shape_regression.py --out json2scapi/out/corpus/similarity-shape-v4-local-20261025
```

独立新目录内保留局部代码、STEP、配准矩阵、逐指标错误和 `LOCAL_REGRESSION.md`。source bbox和body数量差异只作信息诊断；IoU为必需指标，测量失败保留provisional。候选 STEP 无效则拒绝；参考、必需测量或退化配准无法确认则 provisional。低层旧 `compare_shapes` 和历史 `verify_deepcad/batch_verify` 保留原世界坐标口径，不用于新语料入库。


以 DeepCAD 为基础的 CAD 中间表示（IR）与 SimpleCADAPI 代码生成管线。

```text
DeepCAD cad_json ─┐
WHUCAD / HistCAD ─┼─→ adapter ─→ IR v0.1 ─→ emitter ─→ FTC / STEP / STL
其他 JSON ────────┘                                  └→ build123d / CadQuery（后续）
```

核心用途是 **json2ir2code**：不同来源的 JSON 先转成统一 IR，再由 IR 生成不同 CAD API 的代码。


v4当前实现采用CD、IoU、归一化体积误差三项硬门槛，仅局部表面偏差进入warnings；其他差异保留为信息诊断。2026-10-25验证：61项单元测试全部通过；23例固定局部回归为15个train_ready、4个provisional、4个rejected。完整证据见 [LOCAL_REGRESSION.md](../../out/corpus/similarity-shape-v4-local-20261025/LOCAL_REGRESSION.md)，未运行0000全量测试。

## 当前状态

- IR 版本：`0.1`（未冻结草案；profile 现要求显式 `regions`）
- 输入 profile：`deepcad-1`
- 支持范围：`sketch.line / sketch.arc / sketch.circle / sketch.loop / sketch.profile / feature.extrude`
- 已实现：
  - `cad_json → IR`（DeepCAD 适配器，含单位换算、坐标系、loop 闭合与排序，以及 region/孔归属）
  - `IR → FTC`（SimpleCADAPI，支持多轮廓 profile、内孔、多 body、`one_side / symmetric / two_sides`、`new / join / cut / intersect`）
  - `IR → STEP / STL`（通过 `_validate_worker.py` 执行 FTC）
  - 候选 FTC 的自洽检查；提供独立 STEP 时才做几何验收
- 目标语言：FTC（SimpleCADAPI）已实现；build123d / CadQuery 后续扩展

MVP 的跨目标约定是**共享同一份 IR，而非共享生成代码**：草图几何、平面、profile、拉伸参数及线性 body 集合操作均不包含目标 API 名称。当前仅 SCAPI emitter 落地；CadQuery 与 build123d 尚未做生成和几何对账，不能宣称三目标兼容已经验证。

本轮实现与回归见 [IR2CODE_V3_RESULTS.md](../reports/IR2CODE_V3_RESULTS.md)：79 项测试通过；固定 23 例为 18/1/4，前 50 例为 39/2/9（train_ready/provisional/rejected）。

## IR→Code 几何准备

生产生成入口统一执行来源无关的几何准备：原始圆弧辅助字段严格校验、容差内相邻端点有界统一、解析轮廓包含与自交检查。输入 IR 不修改；readable v3 元数据记录全部调整，数值表达不再按九位小数合并或归零。DeepCAD 的不对称双侧 taper 明确拒绝。当前 similarity-shape-v4 评估及 worker 保持现状；评估允许整体变换，不允许生成器自行改写 IR 尺度或坐标。

## Readable 单路径训练语料

当前迁移与 mixed-86 结果见 [READABLE_ONLY_RESULTS.md](../reports/READABLE_ONLY_RESULTS.md)：67 train_ready / 10 provisional / 9 rejected，原先 64 个通过样本无退化。

`ir2code.py` 默认且仅支持 `--style readable`；生产入口已关闭 faithful，不再生成、执行或比较 faithful 产物。可读代码为 v3（ir-geometry-v1）：顶部尺寸 → profile 函数 → 源特征函数 → `build()`。只依赖 SCAPI，保留显式 region、特征顺序和 body 集合语义，不推断设计意图。单文件同时保存 `.symbols.json` 和 `.prepared.ir.json`。完整契约见 [READABLE_CODE_SPEC.md](../specs/READABLE_CODE_SPEC.md)。

```powershell
cd F:\AI_CAD\json2scapi
& F:\AI_CAD\envs\simplecad\Scripts\python.exe .\ir2code.py `
  --input .\out\cases\00000007\model.ir.json --style readable

& F:\AI_CAD\envs\simplecad\Scripts\python.exe .\build_corpus.py `
  --cad-dir F:\AI_CAD\datasets\DeepCAD\smoke\core `
  --name readable-only-core-v1
```

语料流水线写入新目录 `out/corpus/<run_name>/`，拒绝覆盖已有批次。每例保存原始 `fidelity.ir.json`、准备后的 `prepared.ir.json`、`model.readable.ftc.py`、`symbols.json`、`readable.step`、`readable.result.json`、`reference.step`、`reference.json` 和 `report.json`。`train_ready.jsonl` 只导出 readable 构建有效且独立参考验收通过的样本；参考不支持、构建不可用或度量错误保持 `provisional`，明确几何不匹配或候选构建错误为 `rejected`。报告标记 `pipeline_version=readable-only-v2`、`evaluation_version=similarity-shape-v4`。数据缺损仍拒绝，不自动补造草图。

加 `--log-only` 时，每例完成评估后立即删除该例的源码、STEP 和临时产物，只保留根目录的 `manifest.json`、`errors.csv`、`progress.jsonl` 和 `train_ready_index.jsonl`；不会再生成包含代码的 `train_ready.jsonl`。`progress.jsonl` 每完成一例立即追加，运行中即可分析；`manifest.json` 和 `errors.csv` 仍在整批结束后写入。该模式适合全量 0000 的错误码扫描，不适合直接作为训练导出。

全量扫描建议同时加 `--isolate`：每个样本在独立子进程中执行，OCC 原生崩溃会记为 `provisional/process_error`，不会终止整个批次。

```powershell
& F:\AI_CAD\envs\simplecad\Scripts\python.exe .\build_corpus.py `
  --cad-dir F:\AI_CAD\datasets\DeepCAD\cad_json\0000 `
  --name readable-only-0000-log-v1 `
  --cd-samples 8192 `
  --log-only `
  --isolate
```

参考入口在 `../deepcad_official/local_adapter/`，保持官方克隆源码不变，沿用官方 profile 解析及操作顺序；本地恢复原始笛卡尔草图坐标，避免官方角度转换丢失象限。建模后统一缩放到 mm，不调用整模型 normalize。此参考是适配实现，不是原 pythonocc 脚本原样执行。

以下双轨试点是历史结果，不能作为当前单路径版本的验收结果：

`smoke/core` 14 例试点中，11 例 `train_ready`、3 例因 B-rep 交集数值不可信保持 `provisional`；`smoke/edge` 5 例没有入库（4 `rejected`、1 `provisional`）。这些是固定试点，不代表全部 DeepCAD 数据的独立验收率。

结构化 v2 的实现、前 50 例验收和十例代码审阅见 [STRUCTURED_V2_RESULTS.md](../reports/STRUCTURED_V2_RESULTS.md)。总行数会因函数与参数传递增加，当前没有训练效果改善的实验证据。

每个 `sketch.profile.params.regions` 显式列出 `{outer, holes}`；所有 loop 恰好归属一个 region。`is_outer` 是 IR 归一化角色，DeepCAD 原标志保存在 `source.deepcad_is_outer`。校验器核对包含关系，emitter 直接消费 `regions`，不再自行推断。**旧草案 IR（包括历史 `out/batches/` 内的 `model.ir.json`）不含 `regions`，须从原 DeepCAD JSON 重生成，不能直接交给当前 emitter；历史报告保持原样。**

## 文件

| 文件 | 作用 |
| --- | --- |
| `IR_SPEC.md` | IR v0.1 规范：结构、实体、DeepCAD 映射、语义校验、版本演进 |
| `schema/ir-0.1.schema.json` | JSON Schema（Draft 2020-12） |
| `validate_ir.py` | Schema + 语义校验器 |
| `ir2code.py` | IR → readable SimpleCADAPI/FTC 唯一生产入口 |
| `readable_codegen.py` | 可读版内部 CodePlan 与独立 SCAPI 结构化生成器 |
| `READABLE_CODE_SPEC.md` | 可读版函数层次、命名、参数及追溯契约 |
| `deepcad_to_ir.py` | DeepCAD `cad_json` → IR v0.1 适配器 |
| `profile_regions.py` | 按轮廓几何包含关系分组实体区域/内孔；接触或重叠时报错 |
| `deepcad_render.py` | DeepCAD JSON → IR → FTC → STEP/STL 渲染管线 |
| `verify_deepcad.py` | 候选 FTC 的自洽检查，或与独立 STEP 的几何验收 |
| `tests/test_mvp.py` | 不依赖 CAD 内核的 IR/发射/报告契约回归 |
| `examples/deepcad_00000007.ir.json` | 由 `cad_json/0000/00000007.json` 转换的样例 IR |
| `examples/line_arc_plate.ir.json` | 线 + 圆弧 + 拉伸的合成样例 IR |
| `examples/plate_with_hole.ir.json` | 外框 + 内孔的多 loop 样例 IR |
| `out/README.md` | `out/` 目录约定与清理规则 |

## 快速开始

所有生成物只写入 `out/cases/<case_id>/` 或 `out/batches/<batch_id>/`；不要在 `out/` 根目录直接写文件。

### 1. DeepCAD JSON → IR

```powershell
python deepcad_to_ir.py `
  --input F:\AI_CAD\datasets\DeepCAD\cad_json\0000\00000007.json
```

默认输出：`out\cases\00000007\model.ir.json`。

### 2. IR → FTC

```powershell
python ir2code.py --input out\cases\00000007\model.ir.json
```

默认输出：`out\cases\00000007\model.ftc.py`。

生成的 FTC 定义 `build()` 与模块级 `BODIES`：

- 单个 body：`build()` 返回 `Solid`。
- 多个分离 body：`build()` 返回 `Assembly`，`BODIES` 为各 `Solid` 列表。
- `join` 会优先做布尔并集；若两个实体在空间上分离，则保留为独立 body，而不是让整个模型失败。
- 非 `new` 特征作用于当前 body 集合；布尔失败会报错，不会静默保留旧模型。
- FTC emitter 只接受毫米 IR、无 `unsupported` 项；一个 profile 可以包含多个实体区域，每个区域恰好一个 outer loop，并拥有直属内孔。

### 3. IR / DeepCAD → STEP / STL

```powershell
python deepcad_render.py `
  --input F:\AI_CAD\datasets\DeepCAD\cad_json\0000\00000007.json
```

默认输出到 `out\cases\00000007\`：

- `model.ir.json`
- `model.ftc.py`
- `model.step`
- `model.stl`
- `result.json`
- `render.json`

### 4. 候选 FTC 检查

```powershell
python verify_deepcad.py `
  --deepcad-json F:\AI_CAD\datasets\DeepCAD\cad_json\0000\00000007.json `
  --candidate-ftc out\cases\00000007\model.ftc.py
```

默认写入同一个 case 目录：`out\cases\00000007\verify.json`、`gt.*`、`candidate.result.json`。

不提供 `--reference-step` 时，所谓 GT 是由**相同的 adapter 和 emitter**从该 JSON 再生成的；这只能检查两份代码是否自洽，`quality=self_consistent`，不能证明符合独立的 DeepCAD 真值。正式验收需要增加 `--reference-step <独立来源的 STEP>`，且 `quality=accepted` 仅在独立对账通过时出现。

比较项：

- 体积、表面积、包围盒、实体数量
- GT 包围盒 vs DeepCAD JSON 自带包围盒
- 候选模型与 GT STEP 的对称差体积
- 可选：体积 IoU、Chamfer Distance

相对误差阈值默认 `1e-3`。

### 4.1 CD / IoU 评测

通用几何指标在 `geometry_metrics.py` 中实现，可直接读取 STEP 并计算：

- volume / area / bbox / solid count
- `volume_iou`
- `chamfer_distance_mm2`
- `chamfer_mean_mm`
- `chamfer_relative`

单样本评测：

```powershell
python verify_deepcad.py `
  --deepcad-json F:\AI_CAD\datasets\DeepCAD\cad_json\0000\00000007.json `
  --candidate-ftc out\cases\00000007\model.ftc.py `
  --reference-step out\cases\00000007\reference.step `
  --cd-iou
```

默认 `--cd-samples 8192`、`--cd-seed 0`、`--cd-deflection-mm 0.1`。只有启用 `--cd-iou` 时才额外导出 `candidate.step`；普通验证不产生该文件。

- IoU 默认要求 `>= 0.999999`；
- CD 相对值默认要求 `<= 0.01`；
- CD 采样数过低会不稳定，不建议用 2048 以下做验收；
- 同源 GT 的 CD 通常为 0；独立 STEP 因为三角化和采样，CD 可能非零。

`verify.json` 新增：

- `cd_iou`: 完整指标和阈值
- `cd_iou_error`: 指标计算失败原因
- `comparison.volume_iou`
- `comparison.chamfer_distance_mm2`
- `comparison.chamfer_relative`
- `comparison.cd_iou_passed`

报告写入 case 目录下的 `verify.json`，包含：

- `quality`: `self_consistent` / `accepted` / `rejected`
- `reference_kind`: `generated_from_same_ir` / `independent_step`
- `reasons`: 失败原因
- `warnings`: 数据或 STEP 往返导致的非阻塞警告
- `gt` / `candidate`: 两侧测量结果
- `comparison`: 各项误差

## 最小独立 JSON→STEP 对比

官方 DeepCAD 仓库稀疏克隆在主目录 `../deepcad_official/`（提交 `f7b3507`，`cadlib/` 与 `utils/export2step.py`）。其脚本依赖旧 `pythonocc` 的 `OCC.*`、`h5py` 等，本机现有 CAD 环境不能原样运行；且该脚本的 JSON 路径会调用 `cad_seq.normalize()`，训练尺度 STEP 不能直接与毫米模型比较。

本机先对 `00000007.json` 使用 `compare_minimal.py`：参考侧只读取**原始 JSON** 的圆、草图坐标系和单侧拉伸值，按官方 `create_by_extrude` 的圆面拉伸思路用 OCP 构建，不调用本项目的 IR；候选侧执行 `deepcad_to_ir → ir2code → build()`。两侧导出并重新导入 STEP 后比较体积、世界坐标包围盒、双向差集、实体 IoU 和表面 Chamfer Distance（CD）。IoU/CD 复用 `geometry_metrics.py`，与批量评测路径保持同一套实现。此脚本**不是官方 export2step.py 原样执行**，仅支持这一类单圆单拉伸模型。

```powershell
cd F:\AI_CAD
& .\envs\simplecad\Scripts\python.exe .\json2scapi\compare_minimal.py `
  --input .\datasets\DeepCAD\cad_json\0000\00000007.json `
  --out .\json2scapi\out\cases\00000007
```

IoU 使用精确 B-rep 布尔：`V(A ∩ B) / [V(A) + V(B) - V(A ∩ B)]`，默认要求 `≥ 0.999999`。CD 以 `0.1 mm` 网格挠度对每侧 STEP 表面按三角形面积采样 8192 点、固定种子 `0`，用最近邻计算 `mean(d(A→B)²) + mean(d(B→A)²)`（单位 `mm²`）；另报告双向平均距离（mm）及其相对于参考包围盒对角线的比值，默认要求该比值 `≤ 0.01`。可用 `--samples`、`--seed`、`--mesh-deflection-mm`、`--cd-relative-tolerance` 调整。**CD 是采样近似，不是精确曲面距离**；两份几何完全重合时，由于独立 STEP 的三角化与采样也可能非零，不宜用零作为阈值。

实测 `00000007`：参考与候选体积均约 `661499.1269175 mm³`，包围盒最大偏差 `0 mm`，双向差集 `0 mm³`，**IoU `1.0`**，CD `4.9858 mm²`（双向平均距离 `1.3816 mm`，归一化 `0.00534`），`passed=true`。半径 `+1 mm` 的反例产生 `14610.49 mm³` 差集、IoU `0.97839`。报告和两份 STEP 在 `out/cases/00000007/`；这个结论只证明该最小样本在**本机独立参数解释**下的最终几何一致，不证明复杂 DeepCAD 特征或完整历史语义。

## 批量回归

```powershell
python batch_verify.py `
  --cad-dir F:\AI_CAD\datasets\DeepCAD\cad_json\0000 `
  --limit 10
```

默认输出到 `out\batches\0000-first10\`：`batch_summary.json`、`batch_results.csv` 加每个 case 的子目录。

统计字段包括 `success_count`、`failure_count`、`success_rate`、`status_counts` 和 `failures`；默认把 `accepted` 与 `self_consistent` 都计为成功，使用 `--require-independent` 时只把独立 STEP 验收的 `accepted` 计为成功。

批量评测 CD/IoU：

```powershell
python batch_verify.py `
  --cad-dir F:\AI_CAD\datasets\DeepCAD\smoke\core `
  --all `
  --name deepcad-smoke-core-cd `
  --cd-iou
```

`batch_results.csv` 新增列：

- `volume_iou`
- `chamfer_distance_mm2`
- `chamfer_relative`
- `cd_iou_passed`

`batch_summary.json` 新增：

- `cd_iou_enabled`
- `cd_iou_evaluated`
- `cd_iou_passed`

`out/batches/0000-all/` 是修复前的历史批次（1437 例：661 自洽、130 拒绝、646 转换错误）；其中 540 个转换错误来自多 outer loop。原始文件保留不覆盖。现在按空间包含深度整理轮廓：奇数层为孔，偶数层为实体区域；原始 `is_outer` 记录在 `source.deepcad_is_outer`。这不是恢复原设计约束，而是确定几何填充区域。

对历史的 540 个多轮廓错误样本，**537 个现在可生成并编译 FTC**；2 个轮廓接触、1 个轮廓重叠，被明确拒绝。抽取的 20 个真实多轮廓样本实际构建后 19 个同源自洽、1 个因源 bbox 与模型 bbox 不一致被拒绝（`00000349`）。样例结果保存在 `out/tmp/region-regression-20/`。这些数字不是独立 STEP 几何正确率；尚未重跑全量构建。

对 `cad_json/0000` 全部 1437 个原始文件重新做无 CAD 内核的转换/源码编译扫描：**1336 个可生成 FTC（93.0%）**，78 个 adapter 错误、20 个不完整 IR 被 emitter 拒绝、3 个边界接触/重叠。该比例仅表示**代码可生成率**，不能替代构建率和独立几何准确率。新的批量运行会在 CSV 中记录 `stage` 并保留失败前已经写出的 IR/FTC 路径，历史 `0000-all` CSV 不会被覆盖。

固定前 50 个原始文件的新批次保存在 `out/batches/0000-first50-region-v1/`：**37/50（74%）同源自洽**，旧批次同一范围是 17/50（34%）；20 个旧多轮廓失败样本恢复构建。其余 13 例：6 个 adapter 错误、1 个 emitter 不完整 IR、1 个布尔构建错误、5 个源 bbox 失配。CSV 的 `stage` 和已生成文件路径用于继续排查。这个提升不能解读为独立 JSON→STEP 准确率。

`00000062` 的 IR 描述线性操作后的 body 集合，允许多个空间分离实体；当前环境未复测其 SCAPI 几何构建。

## 已知限制

- IR v0.1 只描述 part，不支持约束、装配层级、草图约束和高级特征。
- v0.1 `results` 只引用最后一个拉伸步骤，表示执行完整条 `sequence` 后的 body 集合状态；实际可以包含多个实体，FTC 通过 `Assembly` 表达。
- DeepCAD 的 `empty_profiles` 会记录为 `unsupported`；若因此导致后续操作缺少 body，adapter 直接失败。严格 FTC emitter 也拒绝其他不完整 IR。
- 多 profile 的 `intersect` 需要先定义组合刀具语义，当前 adapter 明确拒绝。
- 多 outer loop 可拆成多个实体区域，每个区域拥有直属内孔；边界接触、互相重叠或无法可靠建立包含关系的轮廓仍拒绝。
- 官方 `cadlib.create_CAD` 会忽略 taper；独立参考入口对非零 taper 明确报告 `reference_unsupported`，不能据此授予训练通过。
- 部分 DeepCAD JSON 含非 UTF-8 字节；适配器与 worker 结果读取均使用 `errors="replace"`。
- STEP 布尔度量在薄壁、重合面或容差边界上可能不可用；语料流水线把参考度量不可靠的样本保留为 `provisional`，可靠度量确认不匹配才记 `rejected`。readable 构建成功但未通过独立验收，不能授予训练通过。

## 输出目录清理

`out/` 只保留四类目录：`cases/`、`batches/`、`corpus/`、`tmp/`。不要在 `out/` 根目录写文件。

```powershell
# 只清理临时文件
Remove-Item -Recurse -Force .\out\tmp\* -ErrorAction SilentlyContinue
Get-ChildItem .\out\cases -Directory | ForEach-Object {
    Remove-Item -Recurse -Force (Join-Path $_.FullName '.work') -ErrorAction SilentlyContinue
}

# 清空全部生成物（保留目录约定）
Get-ChildItem -Force .\out | Where-Object {
    $_.Name -notin @('README.md', '.gitignore', 'cases', 'batches', 'corpus', 'tmp')
} | Remove-Item -Recurse -Force
Get-ChildItem -Force .\out\cases, .\out\batches, .\out\corpus, .\out\tmp | Remove-Item -Recurse -Force
```

完整约定见 `out/README.md`。

## 下一步

1. 诊断参考侧多体布尔及 3 个 B-rep 交集不可信的 core 样本，不自动放宽训练门槛。
2. 扩大 readable 单路径语料批次，并人工抽检代码及可编辑尺寸。
3. `build123d` / `CadQuery` 最小 emitter：复用同一 IR 验证跨 API 一致性。
4. IR v0.2：约束、选择指纹、特征参数表达式。

本地快速契约回归：`python -m unittest discover -s tests -v`（在 `json2scapi` 目录运行）。几何构建和独立 STEP 对账需另行在可用的 SimpleCADAPI 环境执行。
