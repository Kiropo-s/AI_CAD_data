# B-rep 识别扩展优化计划：P0.5–P3D

日期：2026-10-27  
状态：基于 100 例识别扩展回归结果的执行版计划  
适用范围：STEP → B-rep → step-plan/1 → SCAPI 的识别扩展基础设施  
不包含：学习模型、参数化设计历史恢复、自动恢复原始 CAD 建模历史

本文是对 [RECOGNITION-ROADMAP-P0-P3.md](RECOGNITION-ROADMAP-P0-P3.md) 的优化版本。原路线图保留作为历史设计记录；本文件用于后续实施和阶段验收。

## 1. 执行结论

路线图总体可行，但不能把四个阶段当作一条连续的成功率提升流水线。优化后的执行顺序是：

```text
P0.5 诊断契约和 case matrix
  ↓
P1A 只读 B-rep graph
  ↓
P1B B-rep 关系索引
  ↓
P2A 曲线、trim、loop 和 profile
  ↓
P2B 拓扑驱动 prism
  ↓
P2C 受控 revolve
  ↓
P3A CSG 候选生命周期和失败诊断
  ↓
P3B 拓扑约束候选生成
  ↓
P3C beam search 和局部 repair
  ↓
P3D 大模型、超时和缓存治理
```

阶段定位如下：

| 阶段 | 可行性 | 主要目标 | 是否直接承诺提升 accepted |
| --- | --- | --- | --- |
| P0.5 | 高 | 固定实验契约，统一失败诊断 | 否 |
| P1A/P1B | 中高 | 建立稳定、只读、可序列化的 B-rep 图 | 否 |
| P2A/P2B | 中高 | 解决曲线/profile 和端面拉伸主瓶颈 | 以专用案例为门槛 |
| P2C | 中 | 扩展受控旋转识别 | 以专用案例为门槛 |
| P3A | 高 | 解释当前 CSG 搜索为什么失败 | 否 |
| P3B/P3C | 中低 | 使用拓扑约束减少 CSG 搜索空间 | 先承诺 2–3 个案例 |
| P3D | 中高 | 处理多实体、大模型和超时 | 否 |

当前最主要的技术瓶颈是统一的面级拓扑和特征语义识别，而不是几何验证阈值。当前回归中，31 个案例因为 `capability.surface_type` 未完成，19 个案例因为 `search.no_verified_candidate` 未完成；两类合计占未 accepted 案例的大部分。

## 2. 当前正式基线

本计划使用最近一轮 100 例完整回归作为正式开发基线：

| outcome | 数量 |
| --- | ---: |
| accepted | 30 |
| incomplete | 63 |
| invalid_input | 5 |
| error | 2 |
| 合计 | 100 |

当前 accepted 结果覆盖 172 个源实体，方法分布为：

| 方法 | 实体数 |
| --- | ---: |
| `analytic_cap_prism` | 160 |
| `verified_bounded_csg` | 6 |
| `verified_partition_csg` | 3 |
| `verified_meridian_revolution` | 3 |

当前主要 reason code：

| reason code | 数量 | 处理阶段 |
| --- | ---: | --- |
| `capability.surface_type` | 31 | P2A/P2B |
| `search.no_verified_candidate` | 19 | P3A/P3B/P3C |
| `import.invalid_topology` | 5 | P3D 或独立输入治理 |
| `domain.loose_faces` / `domain.loose_edges` / `domain.no_solids` | 6 | P3D 或输入域诊断 |
| `limit.source_faces` / `limit.source_bodies` | 4 | P3D |
| `worker.timeout` | 2 | P3D |
| `recognition.replay_mismatch` | 2 | P0.5、P2/P3 验收链路 |
| `measurement.unreliable` | 1 | P0.5、验证链路 |

正式基线产物：

- 回归运行目录：`F:\AI_CAD\step2code\out\abc100-recognition-extension-20261027-r1`（逐例产物已清理，摘要见 `docs/results/abc100-recognition-extension-20261027-r1/`）
- 回归审计：`docs/ir-redesign/regression-100-recognition-extension-20261027.json`
- 当前实现指纹：`a3db37fa0d6020bf0d672f9b62ae06e4173fd754a78b1d64e2264b6e19083b47`
- 当前测试结果：71 项测试全部通过

正式回归要求是保留当前 30 个 accepted 文件和 172 个 accepted bodies。历史路线图中的 29 个 accepted 只作为旧基线对照，不能再作为当前验收数字。

## 3. 不变的工程约束

以下约束贯穿所有阶段：

1. `step-plan/1` 是唯一生产执行计划。
2. B-rep graph、provenance、candidate diagnostics 和 repair evidence 都是 sidecar，不改变 plan 的执行语义。
3. 原始 STEP、历史回归结果和历史 report 保持只读。
4. 任何新增结果必须通过 plan validation、native replay、SCAPI 编译/构建、STEP roundtrip 以及 CD、IoU、体积和包围盒检查。
5. 不通过放宽 CD、IoU、体积、重放或逐体验证阈值提高成功率。
6. 不把 OCP Shape 或网格采样结果直接作为最终代码生成结果。
7. 不把异常文本作为机器统计依据；异常文本只能保存在 details 中。
8. 不以增加全局 timeout、CSG 候选上限或 Boolean 次数替代识别策略改进。
9. 识别器只能生成候选，不能绕过统一验证流程。
10. 一个阶段的诊断改动不得覆盖历史 report；新结果使用新的 run 目录或 report revision。

## 4. 目标架构和接口边界

```text
原始 STEP
   │
   ├── import protocol
   │       └── source identity / input topology status
   │
   └── OCP Shape
           │
           └── BrepGraph（只读来源图）
                    │
                    ├── curve/profile recognizer
                    ├── prism recognizer
                    ├── revolve recognizer
                    └── CSG candidate generator/search
                              │
                              └── FeatureCandidate
                                      │
                                      └── step-plan/1 candidate
                                              │
                                              ├── plan validation
                                              ├── native replay
                                              ├── SCAPI compile/build
                                              ├── STEP roundtrip
                                              └── CD / IoU / volume / topology
```

建议引入两个明确的中间对象。

### 4.1 `BrepGraph`

`BrepGraph` 只表达来源几何和拓扑，不包含可执行 plan 节点：

```text
BrepGraph
  ├── entities
  │     ├── solid
  │     ├── shell
  │     ├── face
  │     ├── loop
  │     ├── edge_occurrence
  │     ├── edge
  │     ├── vertex
  │     ├── surface
  │     └── curve
  ├── relations
  ├── geometric signatures
  ├── source topology summary
  └── topology hash
```

图中的关系必须区分 `true`、`false` 和 `unknown`。无法在规定容差和协议下可靠判断的关系不能默认为 `false`。

### 4.2 `FeatureCandidate`

所有识别策略都输出统一候选结构：

```text
FeatureCandidate
  ├── strategy_id
  ├── source_entity_ids
  ├── inferred_parameters
  ├── plan_fragment
  ├── coverage_evidence
  ├── candidate_score
  ├── validation_status
  └── rejection_reason
```

`plan_fragment` 必须仍然是 `step-plan/1` 的合法子图。候选搜索树、原始 Shape、临时 Boolean 结果和采样数据不能写入最终 plan。

## 5. P0.5：诊断契约和 case matrix

### 5.1 目标

把当前已经存在的 manifest、report、events、checkpoint、provenance 和 source topology 统一成稳定诊断协议，使每个案例都能回答：

- 输入是否有效；
- 哪些实体被读取；
- 哪些识别策略被尝试；
- 每个候选为什么被接受或淘汰；
- 最后失败发生在哪个阶段、哪个 body、哪个实体；
- 失败是能力不足、搜索失败、预算耗尽还是验证不可靠。

### 5.2 产物

新增或规范化：

```text
docs/ir-redesign/recognition-case-matrix.json
docs/ir-redesign/reason-code-registry.json
out/<run>/cases/<case>/source-topology.json
out/<run>/cases/<case>/recognition-diagnostics.json
out/<run>/cases/<case>/candidate-summary.json
```

每个案例的 diagnostics 至少包含：

```json
{
  "case_id": "00000002",
  "source_body_id": "body_001",
  "stage": "recognize",
  "strategies": [
    {
      "strategy_id": "prism",
      "attempt_id": "...",
      "status": "failed",
      "reason_code": "capability.profile_boundary",
      "source_entities": ["face_001", "edge_004"],
      "elapsed_seconds": 0.8
    }
  ],
  "candidate_count": 96,
  "last_failure": {
    "reason_code": "csg.candidate_budget_exhausted",
    "candidate_id": "candidate_095"
  }
}
```

### 5.3 reason code 分层

保留历史 reason code 的映射，同时逐步启用更细的枚举：

```text
import.*
domain.*
capability.*
limit.*
search.*
recognition.*
backend.*
measurement.*
worker.*
```

P0.5 不要求立即把所有旧案例重新细分到最终粒度，但要求保存足够证据，避免以后只能根据英文异常文本补造分类。

### 5.4 P0.5 验收

- 当前 100 例使用同一诊断 schema 重新生成；
- 30 个当前 accepted 文件和 172 个 accepted bodies 无回退；
- 输入 hash、历史 report 和历史输出不被覆盖；
- 所有失败至少定位到 case、body、阶段和 strategy；
- CSG 案例记录候选数量、预算、最后一次操作和 termination 状态；
- 新增诊断文件不参与 plan 执行；
- 诊断 schema 有版本号和实现 fingerprint。

P0.5 不以 accepted 数量增加作为完成条件。

## 6. P1A：只读 B-rep graph

### 6.1 目标

建立统一来源图，替代 `prism.py`、`csg.py` 和 `partition.py` 中重复的 face/edge 摘要构造。建议新增：

```text
kernel/brep_graph.py
```

图节点至少包括：

```text
Solid
Shell
Face
Loop
EdgeOccurrence
Edge
Vertex
Surface
Curve
```

Face 至少保存：

```text
surface_type
surface_parameters
area
bbox
center
orientation
uv_domain
outer_loop_id
inner_loop_ids
adjacent_face_ids
```

Edge 至少保存：

```text
curve_type
curve_parameters
length
first_parameter
last_parameter
orientation
start_vertex
end_vertex
edge_id
left_face
right_face
```

### 6.2 ID 规则

不能使用 Python 对象地址或 OCC 临时句柄作为稳定 ID。建议：

- 按 source body、shell、face、loop、edge occurrence、vertex 的确定性遍历顺序分配局部 ID；
- 区分几何 edge 和 edge occurrence；
- orientation 作为 occurrence 属性保存；
- 对不完整或非法输入也生成部分图，并记录缺失关系；
- 使用 topology snapshot、实体计数、几何签名和遍历协议生成 graph hash。

### 6.3 P1A 验收

- 100 例都能生成完整图或带原因的部分图；
- 所有 solid、face、edge、vertex 有稳定局部 ID；
- 外环、内孔环、边方向可以重建；
- 解析曲面和 B 样条曲面不会被静默删除；
- graph 可序列化、恢复并通过 hash 校验；
- 30 个 accepted 文件无回退。

P1A 不要求识别器立即改用 graph。先完成只读构建和对照测试，降低重构风险。

## 7. P1B：B-rep 关系索引

### 7.1 关系范围

第一版只实现对后续识别有直接价值的关系：

```text
face-edge
edge-vertex
face-face shared-edge adjacency
parallel
coplanar
coaxial
perpendicular
same-normal
containment
```

`tangent`、复杂相交和曲面连续性关系可以先作为可选关系，必须带判定状态、容差和证据。

### 7.2 关系记录

```json
{
  "relation": "coplanar",
  "left": "face_003",
  "right": "face_009",
  "status": "true",
  "tolerance": 1e-6,
  "evidence": {
    "normal_angle": 0.0,
    "plane_distance": 0.0
  }
}
```

### 7.3 P1B 验收

- graph 能重建每个 face 的 loop 和 edge occurrence；
- 共享边、平行面、共面面和同轴面可以被识别；
- 关系判断不会改变原始 plan；
- 关系结果可供识别器查询，但不直接生成代码；
- 现有 30 个 accepted 文件无回退。

## 8. P2A：曲线、trim、loop 和 profile

### 8.1 目标

解决当前 `capability.surface_type` 中可以通过精确平面 profile 表达的子集。重点是完整边界语义，不是简单增加曲线枚举。

优先顺序：

1. B 样条曲线的完整参数、trim range、方向和权重；
2. 完整椭圆和部分椭圆；
3. 有理圆弧；
4. 修剪曲线；
5. 混合直线、圆、圆弧、椭圆和 B 样条的闭合 loop；
6. 外环、多个孔环和反向环；
7. profile 的平面一致性、自交和重叠诊断。

禁止使用采样折线替代精确曲线作为最终 plan 几何。

### 8.2 候选表示

每个 profile candidate 应保存：

```text
profile_candidate
  ├── plane_frame
  ├── outer_loop
  ├── inner_loops
  ├── ordered_edge_occurrences
  ├── curve_parameters
  ├── orientation_evidence
  ├── self_intersection_status
  └── source_face_ids
```

### 8.3 验收

- B 样条外环、B 样条孔环、反向 loop 各有专用夹具；
- 椭圆外轮廓、椭圆孔、部分椭圆各有专用夹具；
- kernel 和 SCAPI 使用同一精确语义；
- profile 几何通过原生重放和 CD/IoU/V；
- 失败能区分 `profile.open_loop`、`profile.self_intersection`、`profile.non_planar`、`profile.curve_unsupported`；
- 现有 30 个 accepted 文件无回退。

## 9. P2B：拓扑驱动 prism

### 9.1 目标

把当前以平面面积排序为主的端面拉伸识别，升级为由 B-rep 关系驱动的确定性识别。

候选生成应综合：

- 平行端面；
- 端面 profile 的拓扑对应；
- 外环和孔环数量；
- 侧面与两端面的邻接关系；
- 拉伸方向；
- 两端面间距离；
- 包围盒和体积；
- source face coverage；
- profile 的局部坐标系一致性。

候选最终必须降低为：

```text
curve.*
wire.loop2
profile.face2
solid.extrude
shape.transform
```

### 9.2 实施顺序

1. 从 graph 中寻找可能的端面组；
2. 比较端面 loop 结构和边类型；
3. 用侧面邻接确认候选拉伸方向；
4. 生成少量 profile/extrude candidates；
5. 先进行廉价包围盒、体积和面覆盖筛选；
6. 对最终候选执行 native replay 和完整几何验证。

### 9.3 阶段门槛

P2B 不要求一次解决全部 31 个 `capability.surface_type` 案例。建议门槛为：

- 先在专用夹具上稳定通过 B 样条和椭圆 profile；
- 在 100 例中新增至少 2 个可解释 accepted；
- 新增结果全部通过 native、SCAPI、STEP roundtrip、CD/IoU/V；
- 再评估是否把阶段目标扩展到 5 个 accepted；
- 不能以单一同构文档重复出现作为泛化证据。

## 10. P2C：受控 revolve

### 10.1 目标

在已有旋转识别基础上，扩展确定性强的轴对称案例，不与通用 CSG 混合。

优先支持：

- 轴候选提取；
- 同轴圆柱、圆锥、球面和环面分组；
- 轴向截面恢复；
- 完整旋转和受控角度旋转；
- 旋转体内孔；
- 受控 B 样条截面。

必须区分：

```text
revolve.no_axis_candidate
revolve.non_coaxial_faces
revolve.section_not_supported
revolve.angle_not_verified
revolve.geometry_mismatch
```

### 10.2 阶段门槛

- 每种新增截面类型都有独立夹具；
- 轴、角度、截面方向都能在 provenance 中追踪；
- kernel 和 SCAPI 的体积、CD、IoU 和包围盒一致；
- 现有旋转成功案例无回退；
- 不把无法恢复的历史顺序伪装成 revolve 识别成功。

## 11. P3A：CSG 候选生命周期和失败诊断

### 11.1 目标

先让当前 19 个 `search.no_verified_candidate` 案例变得可解释，再优化搜索。P3A 不直接承诺新增 accepted。

当前 CSG 实现已经有 primitive、cap profile、Boolean、候选数和时间限制，但候选搜索仍主要依靠包围盒、体积和有限 primitive 组合。新增诊断字段：

```text
candidate_count
expanded_count
pruned_count
native_boolean_count
candidate_budget
step_budget
elapsed_seconds
termination_reason
best_volume_error
best_coverage
best_topology_status
best_plan_hash
```

### 11.2 失败原因

将笼统的 `search.no_verified_candidate` 拆为：

```text
csg.no_primitive_candidates
csg.no_valid_operation
csg.candidate_budget_exhausted
csg.depth_limit
csg.topology_mismatch
csg.residual_too_large
csg.native_boolean_failure
csg.geometry_verification_failed
```

每个候选都必须有 candidate ID、父候选、操作类型、输入来源面、局部评分和淘汰原因。

### 11.3 P3A 验收

- 当前 19 个 CSG 搜索失败案例都有细化 termination reason；
- 日志可以重现候选数量、剪枝数量和最佳候选；
- native Boolean 失败与候选评分不足分开；
- 现有 30 个 accepted 文件无回退；
- 诊断不会把临时 candidate 当作最终验收证据。

## 12. P3B：拓扑约束候选生成

### 12.1 候选约束

使用 P1 的关系索引约束 primitive 和 Boolean 候选：

```text
face adjacency
coplanar relation
parallel relation
coaxial relation
shared boundary edges
containment
local volume change
source face coverage
```

生成候选时优先保留有来源证据的 primitive，降低无来源的全局包围盒和随机组合。

### 12.2 局部评分

候选评分只能用于搜索排序，最终仍由严格几何验证决定。建议使用：

```text
score =
    w_coverage * source_face_coverage
  - w_volume * local_volume_error
  - w_residual * residual_volume
  - w_topology * topology_penalty
  - w_complexity * boolean_complexity
```

评分必须记录各分量，不能只保存一个不可解释的总分。

### 12.3 关键风险

source face coverage 不是简单的 face ID 相等。一个 primitive 的面可能覆盖多个源面，一个源面也可能被多个 Boolean 结果共同解释。因此第一版应允许：

- `covered`；
- `partially_covered`；
- `uncovered`；
- `unknown`。

不能为了提高 coverage 数值而强行建立不可靠的面对应。

## 13. P3C：beam search、局部 repair 和验证

### 13.1 beam search

在 P3A/P3B 完成后再引入 beam search：

```text
primitive candidates
      ↓
cheap topology pruning
      ↓
union / cut / intersect expansion
      ↓
local coverage and volume scoring
      ↓
retain top N partial candidates
      ↓
exact native Boolean validation
      ↓
step-plan/1 lowering
```

beam search 必须有独立的：

- depth limit；
- candidate budget；
- native Boolean budget；
- elapsed deadline；
- body deadline。

不能把所有预算合并成一个全局计数器。

### 13.2 repair

repair 作为独立候选输入路径，不修改原始 STEP：

```text
原始 STEP
   ↓
问题检测
   ↓
repair candidate
   ↓
重新导入
   ↓
BRepCheck 和实体清单复核
   ↓
进入识别器
```

repair 必须记录：

- 原始输入 hash；
- repair candidate hash；
- 改变的实体数量；
- 体积和包围盒变化；
- 新旧拓扑差异；
- 是否允许进入 accepted 统计。

如果 repair 改变了源几何，默认作为独立 outcome 统计，不把它伪装成原始 STEP 的直接识别结果。

### 13.3 P3C 阶段门槛

不直接承诺 5 个新增 CSG accepted。采用递进门槛：

1. 19 个失败案例都有明确终止原因；
2. 选择 3 个拓扑结构清晰的案例作为开发目标；
3. 至少 2 个案例新增 accepted 且完整通过所有验证；
4. 只有在 2 个案例稳定后，才把阶段目标扩展到 3–5 个；
5. 全程保持当前 30 个 accepted 文件和 172 个 bodies 无回退。

## 14. P3D：大模型、超时和缓存治理

当前已经存在父进程 hard timeout、逐体 checkpoint 和部分 worker 隔离。P3D 只补足其边界，不重新设计通用调度系统。

需要完成：

- 每个 body 独立 deadline；
- CSG 搜索时间和 native 调用时间分开统计；
- 进入昂贵 Boolean 前先做廉价拓扑剪枝；
- 已完成 body 可以继续 checkpoint；
- worker 被终止时记录最后一个 start 事件和 active body；
- 多实体模型支持逐体识别和逐体验证；
- 大面数、大实体数案例区分能力限制与预算限制；
- 识别候选、执行结果、编译结果和验收结果使用不同缓存 key。

缓存 key 至少包含：

```text
source SHA256
import protocol / kernel version
topology snapshot hash
body id
strategy semantic configuration
strategy revision
plan version
plan hash
parameter values hash
evaluator / compiler / backend fingerprint
```

## 15. 统一验收协议

每个识别扩展都执行：

1. 单元测试和内核测试；
2. 新能力专用夹具；
3. 当前 100 例完整回归；
4. 按源文档 token 分组的验证集；
5. 失败原因变化对照；
6. plan、code、STEP、report hash；
7. 运行时间、候选数量和 native 调用次数统计。

验收指标必须同时记录：

```text
accepted files
accepted bodies
source coverage
geometry pass
serialization pass
readability pass
parameter_edit status
average recognition time
p95 recognition time
candidate search cost
native Boolean count
reason-code distribution
regressions
```

成功率不能单独作为阶段完成依据。尤其要区分：

- accepted 文件数；
- accepted body 数；
- 新增能力覆盖的 source entity 数；
- accepted 是否来自同一文档族；
- 是否因运行时间大幅上升换取少量通过。

## 16. 阶段门禁

### Gate 0：诊断可用

满足以下条件才能进入 P1：

- 当前 100 例可重复运行；
- 30 个 accepted 无回退；
- 每个失败都有阶段、body、strategy 和主要 reason；
- source topology 和事件可以关联到同一 case；
- 历史输出未被覆盖。

### Gate 1：B-rep 图可用

满足以下条件才能进入 P2B：

- 100 例都能生成完整或部分 graph；
- graph ID、关系和 hash 稳定；
- profile 识别器可以查询 graph；
- graph 不直接改变 plan；
- 现有 accepted 无回退。

### Gate 2：profile 识别可用

满足以下条件才能进入 P3B：

- B 样条、椭圆、孔环和反向 loop 有可复现夹具；
- profile 的失败可以定位到 edge/loop；
- 至少 2 个新的 profile 案例完整 accepted，或已经证明阻塞点在曲面而不在 profile；
- native 和 SCAPI 结果一致。

### Gate 3：CSG 搜索可用

满足以下条件才能扩大 CSG 搜索：

- 19 个当前 CSG 失败都有候选统计和终止原因；
- 候选评分可以复现；
- Boolean 预算与 worker deadline 独立；
- 3 个选定案例中至少 2 个能稳定进入完整验证；
- 当前 30 个 accepted 无回退。

## 17. 明确不做的事情

在本计划完成前不做以下事情：

- 不引入学习模型替代未稳定的规则识别；
- 不把 OCP B-rep 或网格直接嵌入 SCAPI 代码；
- 不用离散折线近似椭圆、B 样条或复杂 profile；
- 不通过放宽 CD、IoU、体积和重放阈值提高通过率；
- 不通过无限增加 CSG candidates、Boolean steps 或 global timeout 解决搜索问题；
- 不把 repair 后的几何结果默认为原始 STEP 的直接结果；
- 不在 P1 graph 完成前大规模优化 CSG；
- 不在 profile 和 feature candidate 接口稳定前引入训练数据生成；
- 不把 accepted 文件数外推为整个 ABC 数据集的总体成功率。

## 18. 最终完成定义

P0.5–P3D 完成时，系统应达到：

- 输入、B-rep graph、feature candidate、plan 和验证证据的边界清晰；
- 所有输入实体都能进入完整或部分 B-rep graph；
- B 样条、椭圆、trim curve 和复杂 profile 有明确能力状态；
- prism、revolve 和 CSG 分开统计；
- CSG 每个候选都有可追踪的生命周期和终止原因；
- repair 不修改原始 STEP，修复证据独立保存；
- 多实体和大模型支持逐体 checkpoint、deadline 和恢复；
- 当前 30 个 accepted 文件和 172 个 accepted bodies 无回退；
- P2/P3 新增结果通过 native、SCAPI、STEP roundtrip、CD、IoU、体积和拓扑验证；
- accepted 数量提升来自可解释的能力扩展，而不是阈值放宽或结果分类放松。

系统最终应能对每个结果回答：

```text
读取了哪些源实体？
源实体的拓扑关系是什么？
识别出了什么 feature candidate？
候选 plan 如何生成？
为什么淘汰其他候选？
哪些源面被覆盖？
原生执行和 SCAPI 是否一致？
CD / IoU / volume / topology 是否通过？
如果失败，具体卡在什么实体、什么候选和什么预算？
```
