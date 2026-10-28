# 1.0 已实现的规则与代码边界

改造前快照：`097478e`。本轮改造保持为工作区差异供审阅；不改写历史输出，不自动迁移旧文件。

## 统一执行契约

`plan/model.py` 提供不可变的 canonical JSON Plan 及 SHA256；`codec.py` 严格拒绝重复 key/NaN/Infinity，限制大小与深度，原子写入对 Windows 共享冲突有限重试。`registry.py` 集中规定每个 op 的输出类型、输入角色和参数维度。`validation.py` 在任何内核工作之前检查完整图。

`graph.py` 是依赖遍历、可达性、稳定拓扑排序与节点 ID 重写的唯一入口。ID 重写只触及 inputs，不递归改任意字符串。无 sequence/results、隐式 body、内嵌 recipe、profile_id/feature_id 隐式依赖。

独立 `schema.json` 约束 JSON 结构；跨引用、参数维度、曲线和内核拓扑由语义校验补充，不能仅凭 JSON Schema 就声称几何可执行。

## 原生内核与编译器

`kernel/curves.py` 精确构造线、周期圆、有向大弧和有界有理 B 样条。`kernel/execute.py` 独立执行统一节点图；所有最终输出必须是有效单实体且体积为正。

准备阶段检验有序曲线连续性、wire 闭合、BRepCheck 的精确自交及 face 有效性。没有为通过 62 删除自交检查，也没有放宽模型容差或拟合几何。

`codegen/scapi.py` 直接展开 SCAPI 调用，只读取 plan 和它的原生验证结果。固定轮廓函数可复用，实体工厂保持独立。symbols/source map 由新编译器派生；没有旧 emitter 源码的 AST 解析重命名桥接。

刚性变换使用旋转/平移，避免当前 SCAPI 通用仿射变换对解析曲面的转换。盒体 XYZ 到 SCAPI width/height/depth 的参数名映射显式测试。原语的公开数字、完整旋转和 frame 参数在每次构建时验证；等半径 cone 显式 lowering 为 cylinder。

SCAPI 不能承载的有效多体/空 Region3 中间态、可编辑轮廓和运行时 spline trim 在能力阶段拒绝。无 BREP/OCP 逃生输出。

## 识别策略

`recognition/prism.py`、`revolution.py`、`csg.py`、`partition.py` 保留原先成功的几何识别方法，直接构建新节点。CSG 的候选描述只属于内存搜索状态，选中后即展开为同一 DAG，不再序列化嵌套 recipe。

曲线测量来源独立捕获；残差与分块通过明确的派生命名空间记录，不冒充原始源边。整体 plan 不需要这些来源信息即可执行。每个完整 body 的新计划先原生重放并验证，再提交 checkpoint。

源表面类型与面数门槛分别报告；源体数、游离几何、无实体、搜索未找到结果和超时均有独立 reason。CSG/分块共享一次 body 的 CSG 时间 deadline；协作 deadline 不能中断已进入的原生调用，父进程另有硬限时。

## 逐体恢复和运行身份

`batch/identity.py` 固定输入内容、完整配置、执行源码和 Schema、Git revision、Python/OCP/SCAPI/NumPy/SciPy 版本。子进程验证与 manifest 身份一致后运行。源码 dirty 状态通过逐文件 hash 明确表示。

`checkpoint.py` 记录 pending/running/complete/failed/unknown。每个候选是相同 step-plan/1 格式，plan 与来源侧文件均做内容校验。恢复重新执行候选并验证；不复用旧验收报告来授予新通过状态。已接受的同身份运行记录可按 artifact hash 跳过。

事件逐行 flush，含单调 sequence、case/body、attempt、phase、node 或操作、elapsed/remaining。原生调用入口留 start，退出留 end/fail。父进程超时时把活跃体标记为 unknown，不删除已提交体。

Windows 必须终止 worker 进程树，覆盖 venv 启动器的子进程。测试包含真实后代进程终止，以及第 11 体受控中断后前 10 体候选仍可加载和验证。

## 验收与诊断

`verification/` 独立拥有必要的 v4 数学实现，删除了无关配准/旧比较 API。只做双方共享的源坐标调理，不独立对齐候选。CD/IoU/V 阈值保持，可靠失败优先于另一指标的测量错误。

源体覆盖、单体体积交叉检查、实际 build 返回、STEP 回读与占据空间度量分层记录。求并结果必须满足 max(Va,Vb) <= Vunion <= Va+Vb 的可靠性边界；越界不作为可靠几何差异。

geometry/serialization/readability/parameter_edit/assembly/history 的 eligibility 独立。批次默认 parameter_edit=not_run，有限编辑回归在测试中单独覆盖。

## 未在本轮承诺的扩展

不做通用约束/表达式引擎、全尺寸设计参数恢复、装配树/历史恢复、自由曲面/放样/扫掠、跨版本迁移器、全自动语义模板推断或分布式调度。当前缓存采取保守整体身份；细粒度缓存命中优化、候选成本分项聚合和更细的原生 worker 拆分留给有性能证据后的迭代。

旧设计文档中的目标目录是职责分解，不要求每个概念都创建单独文件；上述源码路径是当前实现。


## P1A 当前实现：只读 B-rep graph

`kernel/brep_graph.py` 提供 `BrepGraph` / `graph_from_shape()`，只读取 OCP Shape，输出稳定的 solid、shell、face、loop、edge occurrence、edge、vertex、surface 和 curve 实体，以及面邻接关系。几何 edge 与 edge occurrence 分开建模；外环、内环、边方向、端点、曲面/曲线类型和参数状态都保留在来源图中。图不参与 plan 执行，也不生成 plan 节点。

图使用确定性局部 ID、规范 JSON 和 SHA256 graph hash，支持 `to_dict()` / `from_dict()` 往返校验。超过详细计算阈值的大模型仍保留全部实体和拓扑关系，但面积、包围盒、中心和曲面/曲线详细参数显式标记为 `summary_only` / `skipped_large_source`，不会静默删除 B-spline 或解析曲面记录。

P1A 的定向真实案例记录在 `docs/ir-redesign/p1a-graph-smoke-20261028.json`：`00000008` 生成完整图，252 体的 `00000055` 生成带明确摘要状态的完整拓扑索引。当前 80 项单元回归全部通过；尚未把 graph 接入识别器，也未把本次小样本结果外推为 100 例验收。


## P1B 当前实现：B-rep 关系索引

`kernel/brep_relations.py` 提供 `BrepRelationIndex`，从 `BrepGraph` 建立 face/loop/edge occurrence/edge/vertex 关系索引。拓扑关系包括 face-edge、edge-vertex、共享边 face-face 和 containment；几何关系支持 parallel、coplanar、coaxial、perpendicular、same-normal。几何判断采用惰性查询，不在大模型上预生成全量面对关系。

每条关系统一返回关系名、左右实体、`true` / `false` / `unknown` 状态、容差和 evidence。无法从当前曲面类型或参数可靠判断时保留 `unknown`，不把未知当作 false。关系索引有独立 canonical JSON 和 index hash，可恢复并检测篡改。P1B smoke 记录在 `docs/ir-redesign/p1b-relation-smoke-20261028.json`。

关系索引仍是来源诊断和识别查询层，不生成 plan，也不改变现有识别器行为。

## P2A 当前实现：精确 profile extraction 第一阶段

`recognition/profile.py` 将平面 B-rep 面的边界转换为统一的 profile evidence，并由 `emit_profile_nodes()` 降低为现有 step-plan/1 的曲线、wire、frame 和 profile 节点。提取器保留有序 edge occurrence、边方向、外环/内环关系和源边索引；生成节点使用 `wire.loop2` / `profile.face2` 的显式输入字典，符合 registry 的输入契约。

当前精确支持线、完整圆、圆弧、完整椭圆和裁剪 B-spline，包含曲线参数范围、控制点、节点重数、权重、trim 和方向信息。profile 层执行环连续性、闭合性、平面一致性、B-rep validity 和自交检查；失败通过 `profile.open_loop`、`profile.self_intersection`、`profile.non_planar`、`profile.curve_unsupported` 等 reason code 分类，不用折线采样掩盖几何能力缺口。

SCAPI 当前只提供完整椭圆表达，因此部分椭圆明确以 `profile.curve_unsupported` 和 `backend=full_ellipse_only` 失败；没有引入近似几何。多孔 profile、普通和反向 B-spline loop 已通过定向 smoke。详细记录见 `docs/ir-redesign/p2a-profile-smoke-20261028.json`。

P2A 验证：定向 `test_profile + test_kernel + test_pipeline` 共 35 项通过；完整 `tests/test_*.py` 共 90 项通过。此阶段未新增 100 例回归。
