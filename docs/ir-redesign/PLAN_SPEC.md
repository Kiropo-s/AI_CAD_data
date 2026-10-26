# STEP Plan 1：新的执行语义契约（设计稿）

本契约是全新的格式，不是旧 CAD IR 或 STEP plan 的增量版本。尚未实现的 schema/loader/executor 不应被宣称为现有能力。实施时以本规范形成类型定义与独立 JSON Schema；二者必须有一致性测试。

## 1. 目标与范围

计划应自包含地描述可执行重建几何：移走原 STEP、report、provenance、symbols 和识别器内存后，仍能加载、验证、原生重放并生成独立 SCAPI。只记录最终选定方案，不把完整候选搜索树或原生 Shape 序列化进去。

第一版覆盖现有已验证几何能力：2D 直线/圆/有向圆弧/有界 B 样条，环和含孔 profile；拉伸、完整旋转、box/cylinder/cone/sphere/torus；布尔、刚性变换和正的均匀缩放。分块与残差是识别策略，最终表达为这些操作的组合，派生历史留在 provenance。

不要求第一版就实现一般放样、扫掠、圆角、草图约束、装配约束、任意拓扑选择或设计历史恢复。未来新增 op 必须同时定义类型、校验、执行、后端能力和来源规则，不允许塞入 raw BREP/通用 Python 表达式逃生。

## 2. 顶层结构

```json
{
  "format": "step-plan",
  "version": 1,
  "units": {"length": "mm", "angle": "rad"},
  "parameters": {},
  "nodes": {},
  "outputs": [{"id": "body_01", "node": "final_shape", "expected_kind": "solid"}]
}
```

规定：

- format 固定；version 是整数 1，不能是字符串、bool 或任意未知值。旧格式在入口立即 `schema.unsupported_format`，不自动识别/迁移。
- 主干顶层仅上述字段。扩展信息放独立 sidecar，避免给执行器塞入可改变执行行为的宽泛 extensions。
- plan 中统一 mm/rad；源单位和单位转换证据在 source.json。后端转成度只是 lowering，不改变 plan 的角度定义。
- 不再有 ir_version、kind=part、sequence、results、recipe、operation=new、unsupported 列表和 output_body_indices。输出显式列出，不依靠任何隐式 body 容器状态。
- 节点 ID、输出 ID、参数 ID 各有独立名字空间；全局规则为 ASCII `[A-Za-z][A-Za-z0-9_]*`，每个表内唯一。JSON 重复 key、NaN、Infinity 一律拒绝。
- JSON 字典顺序没有语义。拓扑调度采用稳定排序；有序 inputs 的数组次序有语义，不能为了哈希排序而改动布尔/轮廓顺序。
- 未知字段默认拒绝；不是忽略后继续执行。每份 plan 使用 canonical JSON 的 SHA256 标识，规范化仅处理 JSON 表达，不舍入 float 或重排有序几何。

## 3. 单一节点图与引用

每个节点结构：`{op, inputs, args}`。op 是版本内注册的精确名称；inputs 为命名角色到节点 ID 数组的映射，所有节点依赖只能通过它表达。args 为已知参数名到 Value 的映射，不能隐藏 profile_id、feature_id、frame 字典或子图。

Value 只有二选一：`{"literal": value}` 或 `{"param": "parameter_id"}`。args 内不存在节点引用，param 也不是节点引用。数组和矩阵 literal 作为一个整体值，禁止混合任意嵌套求值。literal 的单位与形状由 op 的签名决定。

公共接口 `dependencies(node)`、`reachable(outputs)`、`topological_order()` 与 `rewrite_ids(mapping)` 是唯一引用遍历入口。不能再递归替换任意字符串，以免改动注释、source ID、标签和数值来源。

两个图域不嵌套：plan.nodes 只有一张图；候选搜索图和来源推导图在各自日志中，不是第二个可执行子图。

## 4. 类型与操作注册表

类型：Curve2、Loop2、Profile2、Frame3、Region3。Region3 是有效的占据空间形状，可为多实体或空集；最终 `expected_kind=solid` 的输出要求恰好一个有效闭合、正体积 solid。第一版不增加空集字面量，布尔计算可产生空中间区域；后端无法表达的路径在 lowering 能力检查时拒绝。

| op | inputs | args / 语义 |
| --- | --- | --- |
| curve.line2 | 无 | start、end：两个不同 2D 点，方向从 start 到 end |
| curve.circle2 | 无 | center、radius>0、winding=ccw/cw；一整圈，不用两点是否相等推断周期 |
| curve.arc2 | 无 | center、radius>0、start_angle、sweep_angle；0<abs(sweep)<2π，带符号 sweep 明确大弧和方向 |
| curve.bspline2 | 无 | degree、poles、knots、multiplicities、weights、periodic、trim、reversed；必须保留裁剪和方向，无拟合回退 |
| wire.loop2 | curves=[Curve2...] | 无；至少一条，有序、闭合，允许整圆独占一个环 |
| profile.face2 | outer=[Loop2]、holes=[Loop2...] | 无；一个外环，显式孔列表；不靠面索引推断内外关系 |
| frame.rigid3 | 无 | origin 3D、x_axis、y_axis；有限单位正交向量，z=cross(x,y)，右手系 |
| solid.extrude | profile=[Profile2] | distance>0；局部 XY profile 沿 +Z 拉伸，从 z=0 开始 |
| solid.revolve | profile=[Profile2] | angle=2π；profile 的 (u,v) 映射局部 (x,0,z)，绕局部 +Z 旋转 |
| solid.box | 无 | width、depth、height>0；底面以 XY 原点为中心，底面 z=0 |
| solid.cylinder | 无 | radius、height>0；底面中心原点，轴 +Z |
| solid.cone | 无 | radius_bottom/top>=0（不能都为 0）、height>0；底面中心原点，轴 +Z |
| solid.sphere | 无 | radius>0；球心原点 |
| solid.torus | 无 | major_radius>minor_radius>0；中心原点、轴 +Z、完整圆环 |
| boolean.union | operands=[Region3...] | 至少两个；允许分离中间区域，最终输出仍检查 solid 契约 |
| boolean.cut | target=[Region3]、tool=[Region3] | 有序二元 target-tool，不隐式指定“此前 body” |
| boolean.intersect | operands=[Region3, Region3] | 第一版二元；记录明确顺序，不能交换后跳过回归 |
| shape.transform | shape=[Region3]、frame=[Frame3] | 对局部形状施加 frame 的刚性变换 |
| shape.uniform_scale | shape=[Region3] | factor>0、center 3D；p'=center+factor*(p-center)，包含内部全部相对位置 |

签名应由一份注册表驱动类型校验、遍历与后端支持查询，JSON Schema 保持独立可测试。不是让 op 注册表内执行任意运行时指令；SCAPI 源码仍展开明确 API 调用。

新能力不靠预先开放未知 op。以后支持部分旋转时修改 revolve 的能力协议、约束和测试；不因为 schema 能存 angle 就把当前完整旋转识别说成任意角度识别。

## 5. 几何权威值、坐标和变换

- Curve2/Profile2 全部在局部二维坐标。禁止同一个节点同时携带 local 和 world 两套可冲突数据。
- 原生圆弧抽取先依据源 curve adaptor 的区间与 orientation 得到 center/radius/start/signed sweep，保留原始区间和端中点作为独立证据。start/mid/end/center/radius/ccw 不再同时作为执行权威值。
- B 样条的节点、控制点、权重和修剪域必须自洽；第一版不进行近似拟合，也不把精确回放宣称为恢复样条设计参数。
- 变换均显式作为节点。feature 自身不再携带 placement，又从 evidence 读取一份 frame，再在 Assembly 外层重复施加一次。
- 默认世界姿态由最外层 frame 节点表达；整体尺寸调整由 uniform_scale 节点表达。内部工具体可有自己的 transform，所有变换严格按图依赖组合。
- 尺度数值调理属于内核/验收配置，对 reference 与 candidate 共享；不能写成改变真实尺寸的隐式 plan 变换。
- 禁止镜像/负尺度、剪切或通用仿射矩阵作为未声明能力。方向参数每次 build 都须校验，不能只验证默认值。

## 6. 参数契约

parameters 的记录字段：`type`、`unit`、`value`、`editability`。type 限定为 number、point2、point3、direction3 等注册类型；unit 为 mm/rad/1；editability 为 independent 或 fixed。节点签名进一步施加正值等约束。

每个 `{"param": id}` 都必须解析到已声明、被使用且维度匹配的参数。不允许未使用参数、引用不存在参数或用 mm 参数当角度。元数据里的 measured/derived/inferred、设计参数恢复状态和名称建议在 provenance 中；它们不改变执行值。

第一版不实现 eval、任意 Python 表达式、自动约束求解或通用符号参数引擎。派生尺寸暂以固定值和来源公式证据表示，不能公开成独立设计尺寸。将来增加表达式时单独设计受限运算和量纲规则。

形状/参数独立性不是靠默认值判定：两个 body 的相等尺寸不能自动绑定为同一参数。模板复用需要按节点签名和显式参数绑定归一化；共享固定几何可以，意外共享可编辑状态不可以。

现阶段 CSG 支持整体 scale 和刚性定位，工具体原始尺寸可固定。重构不要求把每个控制点、每条边坐标全部公开成参数。当前参数编辑没测的 100 例不能在新报告中自动获得编辑通过资格。

## 7. 输出和多实体

outputs 按数组次序提供稳定 body_id，每个记录为 `{id,node,expected_kind}`，node 必须为 Region3。输出集合是模型的实体清单，不默认对所有根求并集，也不恢复装配树。

源对应关系在 provenance/output_map 中：一个 body_id 对应本次导入快照的源 solid ID，分块内部节点不冒充源实体。所有源实体覆盖才有完整 case plan；多个 root 可以共享不可变几何定义，但返回的可编辑 body 必须正确独立实例化。

输出 shape 的全局位置已在图里。_as_model/Assembly 包装只能使用恒等变换。用于占据空间 IoU 的 union 是验收视图，不能改变实体清单和导出语义。输出根若得到多个 solid，不能悄悄拆成多个原始 body 计成功。

## 8. 独立几何准备

准备分为纯结构校验、几何准备和原生可执行验证。所有操作族使用同一套 STEP 曲线/环/面规范，不按“这是旧拉伸”绕回 DeepCAD preparation。

准备只在显式 profile 下做方向、闭合与几何一致性检查；不能默认调点、舍入、拟合或删除短边。允许的端点焊接必须受源容差和最大位移上限控制，逐项记录原值、新值、误差及授权协议，必须重新做原生和最终几何检查。不能为跨过 62 的错误直接放大模型容差。

纯 prepare(plan, policy) 返回新的不可变 plan 与 preparation.json；政策带入但不伪装成几何值。最终产物只有一个权威 model.plan.json。可选 work/candidate 快照用于诊断，不增加 raw/prepared 两套生产加载语义。

单位规范、Curve2 参数、拓扑连接与 kernel wire/face 检查若冲突：reason_code=preparation.topology_conflict，附节点和源引用。独立二维检查也要提供具体边对与交点证据，不把一条字符串当作可靠判定。

## 9. 完整校验顺序

1. 严格 JSON：重复键、非有限数、大小/深度限制；format/version/未知字段。
2. 表结构：ID 唯一、op 与字段签名、required/forbidden、literal 维度与范围。
3. 所有 inputs 引用、输入角色的基数和类型；参数引用及量纲；frame 正交性。
4. 全图无环；从 outputs 可达，拒绝孤立节点和未使用参数；有序布尔语义明确。
5. profile 准备：曲线有界、loop 连续、面和孔关系、B 样条域；与原生检查交叉验证。
6. 原生执行逐节点检查；最终 roots 为有效单 solid、正体积。
7. 后端 capability/lowering 检查；不能在 emitter 内才临时发现不支持某个几何类型。
8. provenance/source coverage 是独立验收层；不能参与步骤 1–7 的执行语义。

例：unknown version=999、outputs=[]、missing root、循环、错误坐标、隐藏节点引用、未知字段、错误量纲和边方向都必须有精确 JSON pointer 与 reason_code。不得“为了兼容”回退到另一种格式。

## 10. 规范化、执行和编译边界

语义规范化不重写几何。节点去重仅对确定相同且参数绑定相同的不可变子图；不同 body 实例不可因为默认相同就共享可编辑参数。

关联并集合并、常量折叠、模板提取是显式优化 pass，输出变换映射和前后 plan hash。浮点/内核布尔不保证不同分组逐位相同，必须在对应夹具和实际输出上验证；不能把数学等价当作免验收证明。

原生 executor 统一遍历 plan 的同一依赖表，识别器缓存只是可丢弃加速。SCAPI compiler 使用自己的后端 lowering 和 source map，生成显式函数/API 调用，不把 plan 嵌入一个解释器。若 simplecadapi 无法表示某个多实体中间态，lowering 尝试已验证的多元 union 等规则，否则明确 backend.unsupported_intermediate；不能注入 OCP 或 BREP 绕过限制。

## 11. 来源、证据与几何身份

source.json 记录原 STEP SHA256、导入协议、单位、内核版本、源拓扑索引快照 hash。solid/face/edge ID 仅在该导入快照内有意义，不能声称跨 OCC 版本稳定。

provenance.json 以 plan hash + node_id/parameter_id/body_id 为键，记录 measured/derived/inferred、原始面边、派生分区/残差链、容差和几何重放检查。派生面边有独立命名空间和 parent derivation ID，不再使用多个 bool 标记在相同字典里反复改名。

evidence/checks.json 按阶段记录实际测量，采用 plan/code/export hash 绑定；任何值变化使相关证据失效。plan 本身不是“已经与源几何相等”的证明，能加载/能执行与通过验收分开。

## 12. 格式生命周期

生产 loader 只支持上述一种格式。首个版本未实现前允许修改设计；开始产生正式输出后冻结语义，未知版本 fail fast。未来确实需要新版本时新建 loader 或重建数据，不预先构建兼容框架。

旧数据读取仅允许审计脚本留在 docs/tools，不得被主运行时 import。历史基线靠输入 SHA256 和几何结果比较，不靠让旧 payload 继续走新 emitter。
