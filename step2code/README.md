# step2code 1.0

将 STEP 的边界几何重建为自包含的 `step-plan/1`，再原生重放并生成独立、可读的 SCAPI Python。当前只有这一条生产路径。

输出是经几何检验的重建方案，`history_recovered=false`；不声称恢复原始特征树、约束或装配关系。`json2scapi` 是独立工具，本项目不再运行时导入它。

## 使用

在 `F:/AI_CAD` 运行，使用现有 `envs/simplecad` 环境：

```powershell
./envs/simplecad/Scripts/python.exe -m step2code --input datasets/abc/step/abc_0000 --limit 100 --out step2code/out/my-run --timeout 120
```

本仓库需检出为 `step2code/` 目录，并从其父目录以 `-m step2code` 执行。运行时依赖见 `requirements.txt`（Python 3.13、NumPy、SciPy、cadquery-ocp、simplecadapi），测试额外需要 `requirements-dev.txt`。

输入可以是单个 STEP/STP 或目录。默认 CD 采样 8192 点，验收只使用源世界坐标。输出目录必须为空；每例在独立进程运行，原生调用由父进程硬超时隔离。

中断后，用相同输入、配置、代码和依赖版本恢复：

```powershell
./envs/simplecad/Scripts/python.exe -m step2code --input datasets/abc/step/abc_0000 --limit 100 --out step2code/out/my-run --timeout 120 --resume
```

恢复按源码、输入内容、配置、内核和编译器身份校验；候选重新原生重放并验证，不把缓存视为通过证据。已完成实体保存在检查点中；某个后续实体失败时，整例保持 incomplete/error，不输出假完整模型。

计划工具无需源 STEP、provenance、symbols 或 report：

```powershell
./envs/simplecad/Scripts/python.exe -m step2code --action validate --input model.plan.json
./envs/simplecad/Scripts/python.exe -m step2code --action replay --input model.plan.json --out replay.step
./envs/simplecad/Scripts/python.exe -m step2code --action compile --input model.plan.json --out standalone.py
```

生成代码只依赖 Python 标准库 `math` 和 `simplecadapi`。调用 `build()` 得到真实 Solid 或以恒等 placement 包装的 Assembly；每次构建创建新几何。顶部参数在构建时读取。

## 新契约

见 [STEP Plan 1](docs/ir-redesign/PLAN_SPEC.md)、[独立 JSON Schema](plan/schema.json) 和 [代码结构规范](docs/specs/STEP_READABLE_CODE_SPEC.md)。

- 顶层仅 `format/version/units/parameters/nodes/outputs`，版本为整数 1，单位 mm/rad。
- 节点仅 `op/inputs/args`；所有节点依赖经 `inputs` 声明，参数值通过 literal/param 显式绑定。
- 明确的每体输出；无隐式累计 body、sequence/results 或嵌套可执行 recipe。
- 严格拒绝旧格式、未知字段/版本/op、重复 JSON key、非有限值、错维度、悬空引用、循环及不可达节点。
- plan 是唯一几何权威。source/provenance/preparation/symbols/checks 是独立产物，不反向决定执行。

内核、编译器、识别器和验收分别位于 `kernel/`、`codegen/`、`recognition/`、`verification/`。`plan/` 管类型、图、加载和准备；`batch/` 管身份、事件、检查点及父子进程。

## 几何能力与编辑边界

保留经验证的端面拉伸、完整旋转、有界解析 CSG、残差识别和有限平面分块策略。解析工具包括 box/cylinder/cone/sphere/torus；轮廓支持有向直线、圆、大圆弧及有界精确 B 样条。原生 executor 与 SCAPI 后端独立实现同一计划。

当前识别器不提取一般样条拉伸、放样、扫掠、一般自由曲面操作或部分旋转。游离面/边/顶点、无实体输入、无效源拓扑和规模超限都有独立 reason_code，不静默删除源几何。

第一版为每个源实体公开独立的整体尺度、平移与坐标轴参数；内部测得曲线和工具尺寸默认固定。轴必须有限、正交且单位化；不自动修正编辑后的 frame。固定轮廓的模板可复用，实体和公开参数保持独立。SCAPI 当前无法表达的多实体/空中间态，以及需要运行时重验证的可编辑轮廓会在编译能力阶段拒绝。

原生图可表达有效多实体/空 Region3 中间态，最终每个输出必须恰好一个有效正体积 solid。关联 union 在识别结束时显式展平，保留有序操作数。

## 产物与结果

每次运行有 `manifest.json`、`summary.json`、`cases.csv`；每例有：

```text
source.json / case-state.json / events.jsonl
work/body_001/candidate.plan.json + provenance.json + preparation.json
model.plan.json / preparation.json / provenance.json
model.scapi.py / model.symbols.json / model.step
checks.json / report.json
```

结果为 `accepted/rejected/incomplete/invalid_input/error`，同时记录 stage 和 reason_code。manifest 分别记录 requested/selected/completed 及 running/completed/interrupted/failed，不把部分运行当作全量完成。

独立 eligibility 包含 geometry、serialization、readability、parameter_edit、assembly、history。默认整批参数编辑为 not_run；单元测试中的有限编辑验证不冒充这 100 个模型都完成了编辑测试。

冻结的 100 例基线、P2A 20 例 smoke 和四个代表性案例的完整产物归档在 [docs/results](docs/results/README.md)。逐例大产物保留在本地 `out/`，由 `.gitignore` 排除，可用 `tools/archive_runs.py` 重新生成运行索引。

## 几何验收

维持共同坐标调理和阈值：`CD/L <= 0.01`、`IoU >= 0.999`、`|Vout−Vref|/Vref <= 0.001`。逐源实体重放和代码覆盖额外验证相对双向差体积及体积误差不超过 `1e-7`；该体积交叉检查在重构中新增，会单独记录失败值。

源/原生/实际返回值/导出回读都有逐体签名。占据空间求并只用于度量视图；若求并体积小于任一输入或大于输入之和，测量立即标记不可靠。可靠的任一必需指标失败仍产生 rejected，不被另一项测量错误覆盖。

## 验证与历史

```powershell
./envs/simplecad/Scripts/python.exe -m unittest discover -s step2code/tests -p 'test_*.py' -v
```

环境需要 simplecadapi、cadquery-ocp、NumPy、SciPy；schema 测试另用 jsonschema。原有几何行为夹具已转写到新 API。基线快照提交为 `097478e`，旧输出只读，旧格式不进入生产 loader。

[100 例基线与重构记录](docs/ir-redesign/README.md) 区分历史事实、设计目标、已实现能力和实际回归结果。0.x 的其他验证文档是历史记录，不是 1.0 的新验收报告。
