# B-rep 识别扩展计划：P0–P3

日期：2026-10-27

本计划覆盖 P0–P3 四个阶段，目标是把当前的 STEP → B-rep → step-plan/1 识别流程从“少量几何规则 + 有限 CSG 搜索”扩展为可诊断、可验证、可持续增加特征的识别系统。

P4 学习模型和 P5 参数化/设计历史恢复不在本文件范围内。它们必须建立在 P0–P3 的数据、图结构、候选表示和验证协议稳定之后。

## 1. 当前基线

当前实现版本为 step2code 1.0，生产执行契约为 `step-plan/1`。固定的 100 例回归结果为：

| outcome | 数量 |
| --- | ---: |
| accepted | 29 |
| incomplete | 64 |
| invalid_input | 5 |
| error | 2 |
| 合计 | 100 |

通过结果覆盖 171 个源实体。现有成功实体中，159 个使用端面拉伸识别，6 个使用有界 CSG，3 个使用分块 CSG，3 个使用旋转识别。

当前主要失败集中在：

| 失败类别 | 数量 | 优先级判断 |
| --- | ---: | --- |
| `capability.surface_type` | 31 | 需要扩展曲线、曲面和 profile 识别 |
| `search.no_verified_candidate` | 20 | 需要拓扑约束的 CSG 候选搜索 |
| 输入拓扑/游离几何/无实体 | 11 | 需要独立输入域和 repair 诊断 |
| 数量限制 | 4 | 需要规模治理和分层预算 |
| 超时 | 2 | 需要更好的隔离、剪枝和事件记录 |
| 重放/度量边界 | 3 | 需要修正测量和错误分类 |

P0–P3 的硬约束如下：

- 不通过放宽 CD、IoU、体积阈值提高成功率。
- `step-plan/1` 仍是唯一生产执行计划。
- B-rep 图、provenance 和诊断都属于 sidecar，不能改变计划执行语义。
- 任何新增识别结果都必须通过原生执行、SCAPI 编译、源覆盖和 CD/IoU/V 验证。
- 原始 STEP、历史基线和旧输出保持只读。
- 每个阶段都必须保留原有 29 个成功案例。

## 2. 总体架构

目标架构为：

```text
原始 STEP
   │
   └── OCP B-rep
          │
          └── 标准化 B-rep 图
                   │
                   └── 特征候选生成
                          │
                          ├── prism / profile
                          ├── revolve
                          └── inferred CSG
                                  │
                                  └── step-plan/1 候选 DAG
                                          │
                                          ├── plan validation
                                          ├── native replay
                                          ├── SCAPI compile/build
                                          └── CD / IoU / volume / topology
```

各层职责必须分开：

| 层 | 职责 | 不负责的事情 |
| --- | --- | --- |
| STEP 导入 | 单位、OCP Shape、导入有效性 | 推断建模历史 |
| B-rep 图 | 面、边、顶点、曲面、曲线和邻接关系 | 直接决定最终代码 |
| 特征识别 | 产生候选特征和候选关系 | 绕过 plan 验证 |
| CSG 搜索 | 选择可验证 Boolean DAG | 直接修改源数据 |
| plan | 保存唯一可执行结构 | 保存搜索树和原始 Shape |
| kernel | 精确执行和拓扑检查 | 恢复原始设计意图 |
| codegen | 将合法 plan 编译为 SCAPI | 在编译阶段偷偷修复几何 |
| verification | 判断几何和序列化是否通过 | 用宽松阈值掩盖错误 |

## 3. P0：冻结基线和识别评价体系

### 3.1 目标

建立可重复的识别实验协议，使后续每项扩展都能回答：

- 增加了哪些能力？
- 解决了哪些具体案例？
- 是否引入回退？
- 失败是在导入、识别、计划、编译还是验收阶段？

### 3.2 工作内容

1. 固定当前 100 例输入、报告、plan、code、STEP 和 hash。
2. 按源文档 token 分组，而不是只按文件编号划分数据集。
3. 建立固定开发集、验证集和最终回归集。
4. 统一 reason_code 层级：

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

5. 为每个失败案例保存：

```text
source topology summary
surface type histogram
curve type histogram
face adjacency summary
recognizer attempts
candidate counts
last failure
elapsed time
```

6. 把旧的笼统原因拆成可操作原因，但保留历史 reason_code 映射，确保基线仍可比较。

### 3.3 新增产物

```text
docs/ir-redesign/baseline-100-manifest.json
docs/ir-redesign/recognition-case-matrix.json
docs/ir-redesign/reason-code-registry.json
out/<run>/cases/<case>/source-topology.json
out/<run>/cases/<case>/recognition-diagnostics.json
```

### 3.4 P0 验收

- 固定回归运行状态为 completed。
- 29 个历史通过案例全部保留。
- 100 个输入和历史报告 hash 无变化。
- 新增诊断文件不参与 plan 执行。
- 任意失败案例可以定位到具体阶段和具体实体。
- 当前测试集全部通过。

P0 的结果不是提升成功率，而是确保后续提升可以被可靠地测量。

## 4. P1：标准化 OCP B-rep 图

P1 解决“识别器没有统一 B-rep 结构信息”的问题。

### 4.1 标准化 B-rep 图

统一生成：

```text
Solid
Shell
Face
Loop
Edge
Vertex
Surface
Curve
```

Face 节点至少包含：

```text
surface_type
surface_parameters
area
bbox
center
orientation
uv_domain
edge_loop_ids
adjacent_face_ids
```

Edge 节点至少包含：

```text
curve_type
curve_parameters
length
first_parameter
last_parameter
orientation
start_vertex
end_vertex
left_face
right_face
```

关系至少包含：

```text
face-edge
edge-vertex
face-face adjacency
parallel
coplanar
coaxial
tangent
intersecting
```

### 4.3 P1 验收

- 所有 source solid、face、edge、vertex 都有稳定局部 ID。
- 外环、内孔环、边方向和面方向可以重建。
- 解析曲面和 B 样条曲面都能进入图，不被静默删除。
- 100 例原有通过结果无回退。
- 31 个 `capability.surface_type` 案例都能提供具体曲线/曲面阻塞信息。
- B-rep 图可以序列化、恢复并通过 hash 校验。

## 5. P2：基础特征识别扩展

P2 目标是先扩展确定性强、可以由精确几何验证的特征，不急于引入学习模型。

### 5.1 曲线和 profile

优先支持：

1. B 样条曲线；
2. 椭圆；
3. 有理圆弧；
4. 修剪曲线；
5. 混合直线、圆、圆弧、椭圆和 B 样条的闭合 profile。

B 样条必须保留：

```text
degree
poles
knots
multiplicities
weights
periodic
trim range
orientation
```

椭圆优先采用精确表达：

- 增加 `curve.ellipse2` 并同步实现 kernel、schema、registry 和 SCAPI；或
- 使用精确有理 B 样条表达，不采用采样拟合。

### 5.2 拓扑驱动端面拉伸

端面拉伸候选不再只依靠最大平面面积，而是综合：

- 平行端面；
- 轮廓拓扑对应；
- 外环和孔环；
- 侧面邻接关系；
- 拉伸方向和距离；
- 体积；
- 包围盒；
- source face coverage。

候选必须生成标准 `step-plan/1`：

```text
curve.*
wire.loop2
profile.face2
solid.extrude
shape.transform
```

### 5.3 旋转特征

增加：

- 轴候选提取；
- 同轴圆柱/圆锥/球面分组；
- 轴向截面恢复；
- B 样条截面；
- 旋转角和完整旋转判断；
- 旋转体的孔和内轮廓处理。

诊断需要区分：

```text
revolve.no_axis_candidate
revolve.non_coaxial_faces
revolve.section_not_supported
revolve.angle_not_verified
revolve.geometry_mismatch
```

### 5.4 P2 验收

- B 样条 profile 有端面拉伸、孔和反向回归夹具。
- 椭圆 profile 有外轮廓和孔回归夹具。
- 原生 kernel 和 SCAPI 对新增 profile 的体积、CD 和包围盒一致。
- 现有 29 个成功案例无回退。
- 31 个 surface capability 案例被细分为具体阻塞类型。
- 至少 5 个新增 surface capability 案例达到完整 accepted，作为阶段目标。

“至少 5 个”是工程目标，不改变最终验收阈值，也不代表所有 surface 案例都应由 P2 解决。

## 6. P3：推断式 CSG、输入治理和规模化运行

P3 处理复杂模型、大模型、坏拓扑和昂贵搜索。

### 6.1 拓扑约束 CSG 候选

当前 CSG 搜索主要依靠 primitive、包围盒和体积。P3 增加：

```text
face adjacency
coplanar relation
coaxial relation
shared boundary edges
containment
local volume change
source face coverage
```

候选状态至少记录：

```json
{
  "root": "node_07",
  "operations": 5,
  "covered_faces": 37,
  "uncovered_faces": 4,
  "volume_error": 0.0003,
  "topology_valid": true,
  "complexity": 8
}
```

搜索采用保留多个部分结果的 beam search，而不是只保留一个当前候选：

```text
初始化 primitive candidates
        ↓
生成 union/cut/intersect 扩展
        ↓
拓扑约束剪枝
        ↓
局部覆盖和体积评分
        ↓
保留前 N 个 beam
        ↓
达到完整覆盖后原生验证
```

### 6.2 CSG 失败原因细化

将当前 `search.no_verified_candidate` 拆为：

```text
csg.no_primitive_candidates
csg.no_valid_operation
csg.candidate_budget_exhausted
csg.depth_limit
csg.topology_mismatch
csg.residual_too_large
csg.native_boolean_failure
```

每个候选阶段都要落盘：

```text
candidate_count
expanded_count
pruned_count
termination_reason
best_volume_error
best_coverage
best_plan_hash
```

### 6.3 输入拓扑和 repair 分支

对下列情况建立独立路径：

```text
import.invalid_topology
domain.loose_faces
domain.loose_edges
domain.no_solids
```

repair 必须生成新的候选输入或 sidecar，不得直接修改原始 STEP。流程为：

```text
原始 STEP
   ↓
检测问题
   ↓
生成 repair candidate
   ↓
重新导入
   ↓
重新检查 BRepCheck 和实体清单
   ↓
进入识别器
```

如果 repair 改变了实体数量、体积或边界，应在报告中明确记录，不能把修复结果伪装成原始输入结果。

### 6.4 大模型和超时治理

P3 不通过简单增加全局超时解决问题。需要：

- 每个 body 独立 deadline；
- CSG 搜索预算和原生调用预算分开；
- 先做廉价拓扑剪枝，再进入 Boolean；
- 已完成 body 继续保存 checkpoint；
- 父进程可以终止完整 worker 进程树；
- 对超过实体/面数量限制的模型生成分层诊断；
- 对多实体模型支持先分体、后组合验证。

### 6.5 P3 验收

- 20 个 `search.no_verified_candidate` 案例都有细化终止原因。
- 至少 5 个当前 CSG 失败案例新增 accepted，作为阶段目标。
- 00000099 类型的部分成功继续保留已完成 body，不输出不完整总计划。
- 复杂搜索不能破坏现有 29 个通过案例。
- 120 秒整例硬限时保持不变。
- CSG 搜索日志可复现候选数量、剪枝原因和最佳候选。
- 输入 repair 不改变原始 STEP，修复证据独立保存。
- 多实体和大面数案例的失败不再使用含混的单一 reason_code。

## 7. 实验协议

每次识别扩展必须执行以下实验：

1. 单元和内核测试。
2. 新特征专用夹具。
3. 当前 100 例完整回归。
4. 按源文档 token 分组的验证集。
5. 失败原因变化对照。
6. 输出 plan/code/report hash。
7. 记录运行时间、候选数和原生调用次数。

每项实验必须输出：

```text
implementation_fingerprint
configuration
input_hashes
outcome_counts
reason_counts
new_acceptances
regressions
runtime_summary
```

成功率不能单独作为判断依据，还要同时观察：

```text
accepted files
accepted bodies
source coverage
geometry pass
serialization pass
readability pass
parameter_edit status
average recognition time
candidate search cost
```

## 8. 目录和模块建议

P0–P3 的新增职责建议如下：

```text
kernel/import_step.py       OCP STEP 导入
kernel/brep_graph.py        标准化 B-rep 图
recognition/curves.py       STEP 曲线和 profile 提取
recognition/features.py     特征候选公共结构
recognition/prism.py        拓扑驱动端面拉伸
recognition/revolution.py   旋转特征
recognition/csg.py          推断式 CSG 搜索
recognition/repair.py       输入 repair candidate
batch/diagnostics.py        识别诊断和失败统计
```

建议保持以下边界：

- `kernel/brep_graph.py` 只生成来源图，不生成可执行节点。
- `recognition/*` 可以生成 plan candidate，但不能绕过 plan validation。
- `verification/` 不承担识别器的候选搜索职责。
- `codegen/` 不承担几何识别和修复职责。

## 9. 阶段依赖和决策点

```text
P0 基线和诊断
       ↓
P1 OCP B-rep 图
       ↓
P2 曲线/profile、prism、revolve
       ↓
P3 拓扑约束 CSG、repair、规模治理
```

P1 未完成前，不进入大规模 CSG 搜索优化，因为缺少可靠的面邻接和来源映射。

P2 未完成前，不引入学习模型，因为训练标签和特征候选定义还不稳定。

P3 完成后再评估 P4：如果规则和搜索已经解决主要失败，学习模型只用于排序；如果仍有大量重复候选失败，再考虑 B-rep 图编码器。

## 10. 完成定义

P0–P3 完成时，应达到：

- OCP B-rep 图和执行 plan 两层边界清晰。
- B-rep graph 能表达所有输入实体，即使暂时无法识别。
- B 样条、椭圆和复杂 profile 有明确能力状态。
- CSG 搜索和其他特征识别分开统计。
- CSG 搜索每个候选都有可追踪的终止原因。
- 输入 repair 不修改原始数据。
- 复杂模型支持逐 body checkpoint 和可恢复诊断。
- 29 个基线成功案例无回退。
- P2 和 P3 的新增通过案例都经过 native、SCAPI、STEP roundtrip 和 CD/IoU/V 验证。
- 参数化历史和学习模型仍然保持独立后续阶段，不混入本轮识别基础设施。

最终目标不是把所有 STEP 都强行转换，而是让每个结果都能回答：

```text
读取了哪些源几何？
识别出了什么特征？
候选计划如何生成？
为什么选择这个 Boolean 顺序？
哪些源面被覆盖？
原生和 SCAPI 是否一致？
最终几何是否通过 CD / IoU / volume？
如果失败，具体卡在哪里？
```
