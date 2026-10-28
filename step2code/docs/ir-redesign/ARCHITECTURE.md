# 新架构与运行协议（设计稿）

> 此文保留重构前的架构/实施目标。当前 1.0 的实际模块、能力和验证边界见 [IMPLEMENTED.md](IMPLEMENTED.md)，本轮结果见 [RESULTS.md](RESULTS.md)。

## 1. 模块布局

以下是计划中的目标目录，不是已存在的可调用 API。实施时替换同名旧 .py 文件，避免模块/包并存造成导入歧义。

```text
step2code/
  __main__.py / cli.py
  plan/
    model.py              # 不可变 dataclass + 节点签名、值绑定
    codec.py              # 严格 JSON、canonical hash、版本检查
    schema.json           # 独立结构 schema
    validation.py         # 引用、类型、量纲、DAG、outputs
    graph.py              # 唯一遍历、拓扑排序、按引用重写
    preparation.py        # STEP 几何准备；不依赖 DeepCAD
    passes.py             # 已验证的规范化和子图复用
  kernel/
    import_step.py        # 单位、源拓扑身份、有效性诊断
    curves.py / shapes.py # 曲线、wire/face、基本体构造
    execute.py            # 只读执行 plan，逐节点缓存与事件
    signatures.py         # raw body/bbox/volume/topology 签名
  recognition/
    api.py / context.py   # Strategy、Candidate、Budget、Cancel
    prism.py / revolution.py
    csg.py / partition.py
    selection.py          # 先完整可靠验收，再比较复杂度
  codegen/
    api.py / lowering.py  # 能力检查与后端计划
    scapi.py              # 显式源码生成，无旧 emitter/AST 反解析
    names.py / templates.py / sourcemap.py
  verification/
    geometry.py / metrics.py / reliability.py
    coverage.py / roundtrip.py / parameter_edits.py
    decisions.py
  batch/
    manifest.py / events.py / checkpoint.py / worker.py / runner.py
  tests/
    plan/ geometry/ strategies/ compiler/ verification/ batch/
```

依赖方向：plan 不导入 OCP、simplecadapi、recognition 或 codegen。kernel 可导入 plan/OCP；recognition 使用 plan/kernel；codegen 使用 plan，不导入识别器和检查报告；verification 使用 plan/kernel 和可靠度量；batch 编排这些模块。__main__ 不是另一个转换实现。

运行时不再 import json2scapi。需要的数值、可靠度量和安全构造逻辑抽取/迁移到自己的模块并保留行为测试，不创建一套简单粗糙的新指标来替代现有可靠性检查。json2scapi 的 DeepCAD 转换器保持独立。

## 2. 算法接口

Strategy 输入 SourceBody、GeometryContext、Budget、CandidateSink。SourceBody 持有本次导入的原生形状和稳定到该快照的拓扑 ID；不能把 OCP.Shape 放进 plan 的参数字段。

输出 Candidate：一个可序列化的 body plan + provenance 增量 + strategy/attempt 身份 + 已记录的检查引用。原生 Shape 只是 context 内的可丢弃缓存。没有找到方案时返回 AttemptResult，不能把算法能力缺口包装成 schema 错误。

首轮迁移 prism、revolution、bounded CSG、planar partition，不同时更换其启发式。策略可以产生多个方案；选择器先要求可靠几何通过，再比较节点/布尔数、表达长度估计、参数独立性等。搜索期间可以用便宜残差/体积/bbox 筛选，但不得把评分当成完整验收。

候选构造和 native executor 共用曲线/形状基础构造函数，减少语义分叉。识别器必须从序列化后的候选 plan 独立重放一次，不能只把识别过程中偶然构造正确的 native Shape 当作 plan 正确性的证明。

分块和残差不能直接把切出的 arbitrary BREP 作为叶子；每个子块必须重新由允许节点表达。子图拼接通过 plan.graph 的 typed ID rewrite，source/provenance label 不参与全字符串替换。

## 3. 端到端管线

```text
source import + topology snapshot
  → independent source-body recognition / checkpoint
  → typed candidate plan
  → structural validation
  → STEP geometry preparation (record every adjustment)
  → native plan replay
  → source-body geometry verification
  → select + compose explicit outputs
  → backend capability and lowering
  → readable SCAPI generation + source map
  → isolated actual build
  → native ↔ generated-body checks
  → model wrapper checks
  → export / readback per-body and whole-model checks
  → reliable occupied-space CD / IoU / V
  → parameter-edit / readability evaluations
  → derive final eligibility
```

操作图调度由 inputs 决定；策略尝试顺序不是图的执行顺序。代码参数名称由 node/parameter 身份映射产生，不要求旧 FEATURE_01 前缀或与旧文件字节一致。

## 4. 原生重放与独立源码

原生 executor 用 OCP 进行 plan 重放，暴露每个节点的输入签名、输出签名和内核错误。所有输出从显式 roots 取得，不从上一次 BODIES 或其他全局状态取得。

SCAPI 编译器可以有内部 AST/builder，但不能先调用 StructuredEmitter 生成整份 Python、再 parse/rename/拼接。节点 handlers 使用签名和已准备几何，源映射为 `node_id → function/line`、`parameter_id → public constant`、`output_id → returned body`。

独立程序可保留顶部参数、profile/tool/feature 函数、build 和 _as_model 的清晰层次，但函数合并与模板选择属于 compiler，不污染几何 IR。生成程序只 import simplecadapi；不读 model.plan.json、原 STEP、symbols、provenance 或仓库。

重复 build 返回新对象；共享模板和不可变曲线定义不能造成可编辑实例互相影响。模板按规范化子图与绑定生成，不按数值相等盲目合并。

## 5. 几何准备和 62 的隔离测试

准备器必须能单独接收 plan、输出 prepared plan 和 preparation.json，不导入代码生成器。曲线方向、periodic trim、major arc、hole orientation、source tolerance 分别有明确协议。

62 建立最小重放样例：保存其第二实体孔环的原始曲线、face/wire 方向与内核容差；报告有问题的边对/弧区间。比较源 face、抽取轮廓、prepared profile、native prism 和 generated prism，定位第一个不一致边界。不能把旧异常一概认为 DeepCAD 的错，更不能绕过自交检测。

## 6. 验收边界和 52 的隔离测试

必须保留这些对象的原始签名，不把 union 后的数量称为导出前真实 body 数：

1. 导入 source bodies。
2. 从 plan 原生重放的 bodies。
3. generated build 返回的 bodies。
4. _as_model/Assembly 包装后重新提取的 bodies。
5. exported STEP 回读的 bodies。
6. 各阶段仅用于度量的 occupied-space union。

每份签名：stage、body_id（若可映射）、raw solid/shell/face 数、validity、signed/integrated volume、bbox、位置/单位协议、对象或文件 hash。面积/体积/bbox 不能单独证明等价；它们只帮助定位边界。

52 先检查生成 body 与 wrapper 的世界几何，后检查同一 bodies 的导出/回读，再检查 occupied_space 的可靠性；所有路径使用同一份只读实例或明确复制，不重用会被原地修改的内核缓存。必要时保存 debug BREP/STEP 到证据目录作为诊断，禁止生成代码依赖这些文件。

保留 CD/L≤0.01、IoU≥0.999、相对 V 误差≤0.001，以及原先逐体 1e-7 重建检查的基线意义。若新数值协议确实需要变化，独立版本化并在同源 A/B 中解释；不能为了保留 28/100 修改阈值、单独配准候选或忽略必须的错误。

## 7. 结果状态

新报告取消 train_ready/rejected/provisional 作为所有概念混用的单轴状态。case result 采用：

- outcome: accepted / rejected / incomplete / invalid_input / error。
- stage: import / recognize / prepare / replay / compile / build / package / export / readback / measure / edit。
- reason_code: 稳定枚举；details 结构化，不靠匹配英文错误字符串分类。
- eligibility: geometry、serialization、readability、parameter_edit、assembly、history 各为 pass/fail/not_run/unsupported/indeterminate。

几何通过不等于编辑通过；当前 100 例的 not_run 在基线中保持原样。最终 accepted 要求所有必需检查 pass；可靠 mismatch 产生 rejected，只有缺失可靠证据时为 incomplete/error，不把 metric_error 写成 pass。训练数据的准入由单独任务 policy 根据 eligibility 推导。

示例 reason_code：schema.unsupported_version、schema.missing_reference、type.input_mismatch、preparation.topology_conflict、capability.surface_type、limit.source_faces、limit.source_bodies、search.no_verified_candidate、search.time_budget、search.attempt_budget、native.boolean_failure、worker.timeout、import.invalid_topology、serialization.roundtrip_mismatch、measurement.unreliable_boolean。

异常文本保留在 details，不参与机器统计。能力门槛同时记录 actual type/count 与 supported/limit，解决 35 例无法区分类型与面数的诊断问题。

## 8. 事件、预算和逐体保留

事件协议：单调序号、case_id/source_body_id、strategy_id、attempt_id、phase、event=start/end/fail、elapsed、deadline/remaining、node_id、reason_code。进入昂贵 native call 前 flush start 事件，返回后写 end；进程被终止时能定位最后一次调用，不能把 start 当成完成结果。

每个 case 由父进程 worker 硬超时隔离，软预算向下传递剩余 deadline，不能在每层递归重新获得完整 15 秒。Budget 分别记录候选构造、粗筛、初始评分、组合构造、精确评分、分区时间和次数；算法不能以 catch Exception 后无统计跳过。

原生调用无法被线程计时器安全打断；靠独立进程的硬限时。第一版保持每 case 隔离，逐体 checkpoint；确有证据后才增加更细粒度子进程，不先造通用分布式调度器。

已完成 body 使用同一种 plan 格式（单个输出）写 work/body_xxx/candidate.plan.json，case-state.json 保存源清单和每体状态。全源覆盖才生成最终 model.plan.json。99 在第 11 体失败仍能保留前 10 体，整例保持 incomplete；不能把部分输出宣称为完整成功。

## 9. 缓存与恢复

识别候选缓存 key：source SHA256 + import protocol/kernel version + topology snapshot/body ID + strategy semantic config + strategy revision + plan version。候选缓存不等于验收通过证据。

执行缓存另含 plan hash、parameter-values hash、kernel version、tolerance protocol；编译缓存另含 compiler/backend version；验收缓存另含 artifact hash 和 evaluator/policy 指纹。任一相关输入变化必须使该层证据失效。

resume 是依据这些键继续新运行的显式行为，不覆盖旧报告。不要以文件名、mtime 或之前 train_ready 字符串作为复用条件。不为恢复创建旧格式加载器：旧 baseline 只作比较，所有新 checkpoint 用新格式。

## 10. 运行产物

```text
run/
  manifest.json             # requested/selected/completed、源码/依赖/配置/输入身份
  events.jsonl
  summary.json / cases.csv
  cases/<case_id>/
    source.json             # 导入/拓扑身份；引用原输入，非最终程序依赖
    case-state.json
    work/body_xxx/...
    model.plan.json         # 唯一最终权威可执行计划
    preparation.json
    provenance.json
    model.scapi.py
    model.symbols.json      # 由编译器派生，不反过来决定执行
    model.step
    checks.json / report.json
    debug/...               # 可选复现证据；不进入生成代码依赖
```

manifest 明确 requested_count、selected_count、completed_count 和 run_state=running/completed/interrupted/failed/unknown。只有父进程确认才标记 completed；100 行的部分 summary 不伪装成已完成 1000。manifest/source/report 采用原子替换，events 追加写，独立历史运行不可变。

## 11. 来源与运行身份

旧报告没有实现指纹的事实不能通过事后对当前源码做 hash 修补。新运行在启动时固定 code revision/dirty-source hashes、plan version、compiler、evaluator、kernel、simplecadapi、完整 config 和随机种子；子进程校验一致再执行。

历史 reports 只读。参数编辑测试写入自己的证据文件或新 report revision，不再在 summary 已生成后原地改写 report 导致两者失配。
