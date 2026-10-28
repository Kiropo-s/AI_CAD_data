# 不兼容重构实施计划与验收

> 此文保留重构前的架构/实施目标。当前 1.0 的实际模块、能力和验证边界见 [IMPLEMENTED.md](IMPLEMENTED.md)，本轮结果见 [RESULTS.md](RESULTS.md)。

状态：实施前设计，不表示以下工作已经完成。目标是一次可完成的架构替换，不维护两条生产路线、旧 emitter 旁路或永久转换适配器。

## 1. 范围和删除决策

| 当前结构/依赖 | 处理决定 | 替代 |
| --- | --- | --- |
| 纯拉伸 CAD IR 0.1 分支 | 删除新运行中的支持 | 所有几何统一 step-plan/1 |
| step-reconstruction-plan-v1 加旋转/CSG 的兼容格式 | 删除加载与发射支持 | 严格新 schema/type registry |
| sequence/results 与隐式 body 累积 | 删除 | 显式输出根和 typed DAG |
| feature.csg.params.recipe 嵌套图 | 删除 | 统一 nodes 中的基本体、特征和布尔节点 |
| 子图中的 feature_id/profile_id 隐藏引用 | 删除 | inputs 显式有类型引用 |
| 为导入旧数据而设计的迁移器/自动格式探测 | 不实现 | 历史数据从原 STEP 重新转换 |
| StepEmitter / CompositeEmitter / StructuredEmitter 调用链 | 删除旧运行时路径 | 单一 SCAPI compiler |
| 生成 Python → AST parse/rename → 拼装 Python | 删除 | 自有 source builder/AST，一次生成 |
| DeepCAD prepare_ir 对 STEP 几何的前置要求 | 删除 | STEP 专用准备、内核与二维检查 |
| emitter 读取 evidence 决定操作/坐标 | 删除 | plan 是执行语义唯一来源 |
| 递归重命名所有字符串与 dict key | 删除 | 按类型签名重写节点引用 |
| model.ir.json + model.prepared.ir.json 两套生产计划 | 删除 | model.plan.json + preparation.json；work 快照仅诊断 |
| 数值相等即共享参数、泛 BODIES 状态作为图语义 | 禁止 | 显式参数独立性、实例输出与纯 build |
| 旧输出字段/参数名/函数名/Python 字节一致性 | 不兼容 | 几何、编辑、来源和可读性行为回归 |
| worker_error / unsupported_geometry 混合分类 | 删除 | stage + outcome + reason_code + details |
| runtime import json2scapi | 删除 | 迁移经过验证的必要算法和数学，内部模块化 |
| 独立 direct 识别器继续作为第二条生产主线 | 不纳入新主线 | 值得保留的候选/搜索策略移到统一 Strategy；旧目录只读研究，不由新 CLI import |
| simplecadapi 的公开 API、既有 CAD 环境 | 保留 | 不更换 CAD 内核/目标库来混入额外变量 |
| 原始 STEP、旧运行报告/生成代码/验证记录 | 原样保留 | 不要求新运行时读取，不重标版本或自动升级 |
| json2scapi 的独立 DeepCAD 工具 | 不改动 | 新 STEP 工具与其解耦 |

保留 `python -m step2code --input ... --out ...` 的基本易用入口是产品选择，不表示要保留所有旧 flag、错误码、schema 或测试内部 API。新策略/预算配置重新定义并完整记录；可以保留同名且语义完全相同的常用参数，语义改变必须显式说明。

## 2. 交付 A：冻结基线与最小复现

工作：

- 使用本目录 manifest 固定 100 个输入、报告和 28 个成功产物身份；旧文件只读。
- 建立按 source SHA256/body identity 的数据清单，不靠文件名排序结果覆盖已有报告。
- 把 62 的孔环、52 的 14 个实体分段签名做成最小诊断夹具；保存其失败证据，不提前决定根因。
- 将 46/48 的现有超时作为“不完整且阶段不明”的历史事实；新测试用受控 timeout/native exception 检查事件和 checkpoint。

退出条件：能重复生成这 100 例审计；输入身份 100/100 匹配；28 个几何成功和 2 个拒绝原因可检索；历史代码身份未记录的限制明确保留。只读审计脚本不进入运行时。

## 3. 交付 B：新 plan 内核与独立准备

工作：

- model/codec/schema/validation/graph 一起落地；plan 包不 import OCP、codegen 或旧库。
- 完成 PLAN_SPEC.md 中有限操作和所有引用/量纲/输出规则。
- 实现 geometry preparation 独立 API；权威圆弧、周期 B 样条、闭环和孔方向使用统一定义。
- 未识别几何通过 Candidate 类型表示，不在 plan 里塞 unsupported 占位节点。

退出条件：格式错误、空输出、坏版本、环、缺失引用、类型/量纲错、未使用参数、孤立节点、非有限数、错误 frame 全部在进入 kernel 前拒绝。测试 JSON key 重排不改变结果；evidence 不在场时仍可加载。

不要先做旧 IR→新 IR 转换器作为生产入口。为迁移识别器短期存在的本地 builder 辅助可以使用，但必须在交付 E 前移除旧 payload 契约，不能成为新架构永久适配层。

## 4. 交付 C：原生 executor、签名和验收

工作：

- 按统一节点图执行，支持多实体/空中间 Region，但最终输出严格单 solid。
- 源、native、generated、wrapper、readback、occupied-space 分别签名和校验。
- 将现有可靠度量/共享数值调理的必要逻辑迁移，保留错误/不确定语义，不降低 IoU/V/CD 门槛。
- checks 的每项证据绑定真实对象/文件/plan/config hash，报告分轴 eligibility。

退出条件：原生 plan 不依赖识别缓存；translation/scale/漏实体/重叠 body/装配重复变换/无效布尔测量的对照能准确分类。52 能定位第一个不一致边界或明确仍无法可靠测量，不默认判为已修复。

## 5. 交付 D：独立 SCAPI compiler

工作：

- 单次 source emission，typed lowering 与 backend capability 先行。
- 输出清晰的参数、profile、operand factory、feature、build 结构。
- n-ary union 等 lowering 有明确测试，不因 emitter 对单 Solid 的假设阻止合法中间区域。
- symbols/source map 由生成器派生；参数修改的拓扑/尺寸/位置检查走独立验证器。

退出条件：仅拷贝生成 .py 到隔离目录，用现有 envs/simplecad 的 `python -I` 执行 build 两次通过；不需要原 STEP、plan、symbols 或项目代码。新编译器自身在没有 json2scapi 包的项目安装环境下可运行。

不追求旧代码字节相同。控制可读性应检查几何节点对应明确操作、函数长度/依赖、重复子图与绑定；不能硬性要求“每个模型少于 N 行”以迫使隐藏解释器出现。

## 6. 交付 E：迁移有效识别策略并删除旧路径

工作：

- prism、full revolution、bounded CSG、partition、residual recognition 直接产出新 typed body plan。
- 复用几何数学，替换输出构造与来源引用；不同时改变候选排序、搜索目标与阈值。
- 采用真实 source/topology scope 的 provenance，派生边面使用独立 derivation 身份。
- 移除旧 codegen.py/composite_codegen.py/csg_codegen.py 的生产路径、旧 plan 辅助与全部 json2scapi runtime import。
- direct 中有价值的新候选可稍后以 Strategy 接入，不能在结构迁移阶段暗中扩大搜索预算再把通过率提升归于 IR。

退出条件：28 个成功源文件全部通过默认几何及回读；混合文件 29/50/73、多实体 17/74/75/76 和分块 08/77/79 全部覆盖；源码/导入路径审计确认旧 schema/emitter/prepare 没有旁路。

对少量新几何策略改善单独生成实验记录，不能把它们混入结构回归的唯一结论。

## 7. 交付 F：逐体检查点与批处理身份

工作：

- 每体候选原子提交，case-state 显示 pending/complete/failed/unknown；未全部覆盖不写可接受的完整模型。
- 父进程硬超时和取消，flush native operation start/end，精确阶段 reason；恢复按 hash/config/revision 判断。
- manifest 在开始时固定代码/依赖/输入/策略/评估器；报告包含 selected 与 completed 数，不在 100/1000 时声称全量结束。
- events 与正式 report 分离；编辑测试不修改旧 baseline report。

退出条件：受控第 11 个实体超时后，前 10 个实体候选仍可验证和复用；改变参数/算法/内核/评估器会使对应缓存层失效；杀死 worker 后新父进程能区分未完成与未找到方案，不自动重标 accepted。

## 8. 回归矩阵

| 集合 | 重点 |
| --- | --- |
| 原有 44 项测试中的行为夹具 | 转写到新 API，保留几何/参数/隔离/错误边界；删除旧结构断言 |
| 新 schema/type/graph tests | 新格式的正反例、统一依赖/输出、序列化往返与确定性 |
| 06 | 延长截锥 + 带孔多边形求交；原 plan 不依赖证据；坐标只应用一次 |
| 08/77/79 | 分块组合、派生来源、n-ary union、预算和整源覆盖 |
| 17/74/75/76 | 多实体独立输出、模板与参数独立性 |
| 29/50/73 | 同一计划的拉伸/旋转/CSG 混合 |
| 62 | 有向大弧与复杂孔环，准备冲突定位到具体曲线 |
| 52 | 14 体→包装→导出回读→度量视图的逐边界诊断 |
| 25/37/44/82/85 | 无效源几何；不能静默删除/修复后混入通过 |
| 46/48 与模拟 timeout | 最后一次 native 调用可追踪、硬超时与部分提交 |
| 55 | 252 体超限是 limit，不是表示不支持；规模策略独立配置 |
| 36/43/81/86/87/92 | 游离面/边、纯表面域，不自动丢弃以提升成功率 |
| 11/12 等能力门槛 | 不支持曲面和面数门槛分开，成本和原因可统计 |
| 99 | 部分 10 体被保留，源覆盖未完成仍不计整例成功 |

100 例结构回归用相同输入、world、8192 点、相同几何阈值与 120 秒 case 上限。新预算模型与旧配置如无法一一对应，报告具体差异，不伪称相同搜索能力 A/B。

28 个历史成功是几何行为保留目标，不是必须凑出的数字。如果新可靠检查证明旧接受是假阳性，应保留新失败并提供独立证据和基线变更说明，不能删除检查或放宽阈值来保住计数。

28 个成功样本没有本批参数编辑证据；新体系增加有限编辑测试并单独报告覆盖，例如每个操作族至少一例、每种公开参数类型至少一例、同构多体互不影响。不能把旧 not_run 当作测试通过，也不以全部尺寸都能独立编辑作为一期承诺。

## 9. 完成门槛与升级规则

必须同时达到：

1. 生产 runtime 只有 step-plan/1，无旧格式自动回退，无 json2scapi emitter/preparation 依赖。
2. 全部合法节点/参数/输出引用由一个类型系统检查，错误在明确阶段拒绝。
3. 原生 replay 与 generated code 从同一 plan 出发，移走所有外部诊断文件仍可独立执行。
4. 同源 28 个历史几何成功不回退；任何基线状态变化附原因、测量协议和文件身份。
5. 62/52 有可重复诊断与明确当前结论，不能在根因未明时“为了新架构”把原检查删除。
6. 逐体保留、硬超时、resume 与新 run identity 可验证；全部旧数据只读。
7. 文档、CLI/help、操作注册表、schema 和示例与真实实现一致，测试不再依赖旧字段名。

结构重构的 release gate 不承诺 63 个未识别样本的新通过率。若新实现意外提升或下降，先判断是协议差异、修复或策略变化，再给出完整可审计结果。

## 10. 后续能力路线（结构完成后）

按观测到的损失分组推进：

- 21 个 CSG 搜索未完成：候选工具延长/定向毛坯、残差定位、区域分解、预算分配与缓存。
- 35 个复合门槛：先把曲面类型与面数分开；再选择有限解析化、样条截面/扫掠、复杂度路由的投入点。
- 多体规模/部分完成：并行或细粒度 worker 依据性能证据再做，不预先扩展成调度平台。
- 参数可编辑性：从 uniform scale 提升到有来源、明确约束的局部尺寸，不从默认值相等推断设计意图。

扩展评估按 35 个文档 token 组拆分，避免同源相邻模型同时进入开发和独立留出集合；之后再选新的文档组做泛化评估，不以固定前缀反复调参后的 28/100 外推全量表现。
