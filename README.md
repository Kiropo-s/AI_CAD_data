# step2code_direct：直接 STEP → SCAPI，支持草图拉伸与 CSG 混合重建

当前版本 **0.4.0**。目录独立于原 `step2code`，使用现有 `F:\AI_CAD\envs\simplecad` 环境。

0.4.0 新增默认 lookahead 搜索，保留 guided / legacy 开关。固定首 20 例中，当前 guided 通过 6 例，lookahead 审计后通过 7 例（30%→35%），新增 00000016，原有成功案例保留。该例因报告文件锁中断而重跑，报告实现差异和原始记录详见验证文档。lookahead 在采样占据上先规划短 CSG 序列，再构造真实几何并精确验收。历史 0.3.0 对照仍保留于 [搜索对照记录](docs/SEARCH_IMPROVEMENT.md)，本版见 [前瞻搜索记录](docs/LOOKAHEAD_VALIDATION.md)。

```text
STEP / STP（显式导入为 mm）
  → 优先验证单次草图拉伸
  → 不支持时：提取原生拉伸/基本体候选，进行有预算的 CSG 搜索
  → 将选中的原生几何和运算直接展开成可读 SCAPI 源码
  → 执行 build()、逐源实体核查、STEP 导出回读
  → CD / IoU / V 验收
```

不调用现有 CAD IR 识别器、schema、prepare_ir、StructuredEmitter 或 StepEmitter，不输出 `*.ir.json`。识别和搜索会使用瞬时原生几何对象以及候选运算记录；这不是“完全没有中间数据”，也不是旧 IR 换名。不存在可序列化的通用 CAD 操作图或运行时指令解释器。

最终 `model.scapi.py` **只导入 `simplecadapi`**，不读取原始 STEP、BREP、IR、symbols、项目源码或网络。诊断中的残差几何不会被嵌入输出。

## 运行

在 `F:\AI_CAD` 执行：

```powershell
& .\envs\simplecad\Scripts\python.exe -m step2code_direct --help

# 直接混合路线，前 20 例。指定输出目录必须为新目录或空目录。
& .\envs\simplecad\Scripts\python.exe -m step2code_direct `
  --input .\datasets\abc\step\abc_0000 --limit 20 `
  --samples 8192 --timeout 120 `
  --out .\step2code_direct\out\my-hybrid-run

# 同一套当前代码和评估器，关闭混合策略做消融对照。
& .\envs\simplecad\Scripts\python.exe -m step2code_direct `
  --input .\datasets\abc\step\abc_0000 --limit 20 `
  --no-hybrid --samples 8192 --timeout 120 `
  --out .\step2code_direct\out\my-prism-only-run
```

也支持 `step2code_direct\step2scapi.py`，可以传入单文件。省略 `--out` 时创建新时间戳目录。支持 `.step`/`.stp` 大小写后缀，目录按路径排序；`--recursive` 递归。每个案例独立进程，默认硬超时 300 秒，失败后继续后续案例。已有产物不覆盖，不提供 resume。

退出码：全部 train_ready 为 0；含 provisional/rejected 为 1；参数错误为 2。

## 混合策略

优先沿用单次解析直线拉伸，避免为简单模型引入多余布尔步骤。失败后：

1. 从源解析曲面提取圆柱、截锥/圆锥、球体、圆环的参数；另有显式记录为 derived 的世界 bbox 盒体候选。
2. 从平面端面提取可构造的草图轮廓，按平行端面间距生成拉伸工具体；允许保留原孔或填孔后作为基础体。
3. 默认 `--hybrid-search lookahead`：保留单次拉伸阶段最多 3 个接近的有效候选（相对对称差不超过 25%，且不是成功判定），供组合搜索继续使用。
4. 用固定 Halton 点集（源 bbox 中 512 点、覆盖候选的联合 bbox 中 256 点）分类占据情况，粗排候选。采样软预算最多 3 秒且不超过总软预算的 20%；分类失败或部分候选未完成时，保留精确搜索回退。
5. 先精确检查两个高优先级单基本体候选，优先保留球/环等简单、可编辑的解。随后在采样占据位集上规划多步 CSG：默认近似束宽 48、深度上限仍为 8，有限保留多个占据相同但构造不同的序列，并允许有限的采样误差退步或持平。
6. 将排名靠前的序列用 OCC 实际构造；缓存公共前缀及失败前缀。每个真实中间体都须是单个闭合、有效、正体积实体；最终结果必须经过精确双向 Cut 和独立体积一致性检查，不能用采样误差接受。
7. 前瞻阶段最多使用总布尔尝试的一半，另有最多配置软预算 35% 的阶段额度（单次原生调用仍可能超时）；没有精确解则转入 guided 回退，共享总预算。guided 每次扩展仍只保留精确误差改善的分支。
8. `--hybrid-search guided` 保留 0.3.0 的采样粗排、分批扩展和单调改善搜索，不加入前瞻或近似拉伸候选；`legacy` 保留 0.2.0 的搜索策略。所有模式的搜索布尔调用都在 Build 之前开启 NonDestructive，防止多个分支修改共享的输入几何。
9. 完整方案须满足相对对称差 ≤ `1e-7`，随后生成代码，执行逐实体核查、STEP 回读和 CD/IoU/V 验收。

采样点是两个空间分布的混合，采样误差仅是搜索启发式，不是可靠体积估计或最终验收指标。新搜索仍可能错过薄壁、小孔或更优的组合。

例如，输出中会明确出现：

```python
body = make_operand_01_01(...)       # 草图拉伸基础体
boss = make_operand_01_02(...)       # 圆柱凸台
body = scad.union_rsolid(body, boss)
hole_tool = make_operand_01_03(...)  # 孔或锥形沉孔工具体
body = scad.cut_rsolid(body, hole_tool)
```

这是一种有边界的候选搜索，不是任意 CAD 模型的通用分解器。lookahead 的候选序列允许中间采样误差有限退步；guided 和 legacy 的扩展仍要求每步精确误差下降。仍可能错过正确方案。候选生成只使用现有源面及几个平行端面层级，不搜索任意自由参数。

| 参数 | 默认值 | 含义 |
| --- | ---: | --- |
| `--max-candidates` | 64 | 每实体单次拉伸端面候选数 |
| `--max-solids` | 64 | 每文件源实体上限 |
| `--max-hybrid-atoms` | 48 | 每实体混合操作数候选总上限，预算分配给解析体和拉伸 |
| `--max-boolean-steps` | 8 | 单方案布尔运算深度上限 |
| `--max-boolean-trials` | 256 | 每源实体布尔尝试上限 |
| `--hybrid-beam-width` | 3 | 保留的最佳改善分支数；guided 另有限保留 missing/extra 各一个备选，初始起点单独保留 |
| `--hybrid-search` | lookahead | lookahead 为占据前瞻＋精确验收；guided 为 0.3.0 排序搜索；legacy 为原搜索 |
| `--hybrid-timeout` | 20 | 每源实体混合搜索软预算，秒 |
| `--timeout` | 300 | 每文件独立 worker 硬超时，秒 |

原生 OCC 单次调用不能被软预算中途打断，因此软预算可能超出；外层 worker 超时提供最终隔离。耗尽预算属于 provisional，不能被当成证明几何不可重建。

## 支持范围及边界

- 闭合直线拉伸、外轮廓和孔；直线、完整圆、三点圆弧，包括大圆弧；任意方向和位置。
- 从解析源曲面提取的圆柱、圆锥/截锥、球，以及完整或部分圆环。圆环在生成代码中用圆面旋转显式构造，并不代表支持一般截面旋转体。
- 拉伸体与基本体的 union / cut / intersect 混合方案，多源实体仍分别重建。
- 原始孔线框按解析有向面积统一方向，由 SCAPI 按孔语义反转；不折线化圆弧。

仍不支持一般 B 样条/椭圆草图、自由曲面、任意旋转轮廓、通用圆角/倒角、放样/扫掠、无限制特征分解和装配树恢复。圆角若恰好能由已提取的球/环面 CSG 表达，也仍需真实搜索和几何验证，不保证支持。

一个源实体失败时整个文件不获得完整通过，游离面/边/点不静默忽略。所有重建操作为推断方案，`history_recovered=false`。

## 可读结构与参数

单次拉伸保留顶部参数、`make_profile_*`、`apply_feature_*`、`build()`。混合实体增加：

- `make_profile_<实体>_<操作数>()`：构造拉伸操作数轮廓。
- `make_operand_<实体>_<编号>()`：明确创建拉伸体、圆柱、圆锥、球、盒或环体。
- `make_body_<实体>()`：以显式 `scad.union_rsolid` / `cut_rsolid` / `intersect_rsolid` 组合。
- `build()`：读取最新顶部参数，每次创建新几何，保存世界坐标 `BODIES` 并返回真实 Solid 或恒等定位 Assembly。

混合实体的 `BODY_*_ORIGIN_MM / X_AXIS / Y_AXIS` 表示**作用在完整 CSG 结果上的世界刚性增量**，默认恒等；旋转绕世界原点。操作数各自的尺寸与位置为测得的世界参数；单次拉伸实体的 BODY 参数仍直接定位草图，symbols 明确区分。

定位采用刚性 rotate/translate，不采用会改变解析表示的通用 GTransform。为维持生成代码仅依赖 SimpleCADAPI，刚性旋转的小型角度助手使用半角缩减与收敛级数，已核查任意轴及接近 180° 的情况。

参数在 build 时显式传入；不恢复隐藏约束，不自动推断所有矩形长宽。复杂布尔后的参数修改可能导致分离或无效拓扑，默认转换只验证默认参数；`parameter_edit_validation=not_run`。测试中的有限编辑不能替代完整编辑验收。

## 验收与状态

复用原项目的 STEP 单位导入、返回值提取、STEP 导出和 CD/IoU/V 评估服务；没有复用 IR 或重建识别/生成层。默认 world 模式保留尺寸和相对位置，只对双方施加同一个数值条件变换，不独立配准或缩放候选。

| 指标 | 通过阈值 |
| --- | ---: |
| CD / 参考特征尺度 L | ≤ 0.01 |
| 体积 IoU | ≥ 0.999 |
| 相对体积误差 | ≤ 0.001 |

默认每形状 8192 点、seed=0。world 报告 mm / mm³；显式 similarity 模式允许整体配准缩放，CD 标明归一化测量单位。导出回读总用 world。微超 1 的 IoU 不强制截断，沿用可靠性核查。逐源实体对称差检查不能用整个模型并集的指标替代。

- `train_ready`：默认 build、逐实体、回读与参考几何指标全部通过，仅用于 geometry_reconstruction。
- `provisional`：不支持、无已验证方案、输入不可读、度量不确定、预算耗尽、worker 超时/异常。
- `rejected`：代码生成/构建失败、无效候选或可靠度量确认形状错误。

## 输出与对照

```text
<run>/
  summary.json / summary.csv
  cases/<序号>_<input-stem>/
    model.scapi.py
    model.symbols.json
    model.step
    report.json
    worker.log
```

失败时保留已完成阶段的产物及识别/搜索原因。报告记录源 SHA256、能力、启用策略、配置、依赖与源码指纹、候选来源、选中布尔步骤、最佳误差、预算停止原因及完整验证。guided/lookahead 诊断另记录采样/精确评分/布尔构造耗时、调用数、采样与操作错误，以及超时前正在执行的操作；lookahead 另记录近似扩展数量、实际评估序列数、前缀缓存命中和解的来源。symbols 是旁置审阅数据，生成程序不读取它。

```powershell
# 既可比较原 STEP->IR/plan->code，也可比较本工具混合开关。
& .\envs\simplecad\Scripts\python.exe -m step2code_direct.compare `
  --baseline .\step2code_direct\out\my-prism-only-run\summary.json `
  --direct .\step2code_direct\out\my-hybrid-run\summary.json `
  --out .\step2code_direct\out\my-comparison
```

对照按文件名 + 历史源 SHA256 匹配，明确未匹配样本和分母，输出 JSON、CSV、Markdown。核查采样/坐标模式、可用阈值、运行时与共享模块哈希。历史报告缺少指纹或共享评估器变化会明确提示。混合策略增加能力与预算，不能把通过率变化归因于取消 IR；耗时也不能冒充同负载的受控速度结论。

## 验证记录

```powershell
& .\envs\simplecad\Scripts\python.exe -X utf8 -m unittest discover -s step2code_direct\tests -v
```

- [0.4.0 前瞻搜索与对照](docs/LOOKAHEAD_VALIDATION.md)
- [0.3.0 搜索改进与对照](docs/SEARCH_IMPROVEMENT.md)
- [0.2.0 混合版验证记录](docs/HYBRID_VALIDATION.md)
- [旧 0.1.0 直接拉伸版验证记录](docs/VALIDATION.md)，旧报告和输出保留。
