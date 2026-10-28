# IR→Code v3 实施与回归结果

日期：2026-10-25。范围为生成器几何契约，生产评估仍为 similarity-shape-v4 / readable-only-v2；未修改指标、配准、门槛、状态决策或本地 worker。IR 保留未冻结的 0.1，生成规则为 readable_version=3 / ir-geometry-v1。

## 实现

- 新增来源无关的 prepare_ir：结构/引用检查、原始圆弧辅助字段严格校验、有界相邻端点规范化、派生圆弧计算、闭合/退化/自交及 region 校验。validate_ir 和所有生成入口共用；直接 StructuredEmitter 也不能绕过。
- 输入对象保留；按实体 ID/字段字典序选端点代表，只合并 loop 相邻字段关系。共享曲线引用统一，传递合并超容差拒绝。生成点标识不再按九位小数合并，数值使用 float 往返表达。
- 圆弧由 start/mid/end 计算；center/radius 超过 length 容差或 ccw 冲突即拒绝。缺失辅助字段可派生，但不把不存在的原始 radius 字段作为代码参数绑定。
- 线、圆、弧使用解析浮点几何判断边界距离、交点、包含和面积，替换固定 128 点圆及固定步长弧多边形。近退化和容差内接触保守拒绝。
- adapter 反向曲线时同步 ccw；删除无记录的端点吸附；非有限参数及不一致的双侧 taper 明确拒绝。未新增 taper2、目标 body 或原子工具分组。
- CLI 输出 .prepared.ir.json；语料 case 同时保存 fidelity.ir.json 和 prepared.ir.json。symbols.json 保存 preparation、全部调整、原始字段映射和每条曲线的 original_params。既有 API 签名保持。

## 测试

完整测试 **79 项通过，无跳过**。原有 61 项继续通过，新增 18 项覆盖缝隙、超限/共享顶点、来源独立性、圆弧冲突/走向/大弧/近共线、薄间隙孔、相切相交/岛中岛、自交、双侧 taper、确定性及真实内核构建。原来的辅助半径冲突回退测试按严格契约改为拒绝。

- gap=5e-7 mm 和 5e-4 mm，在声明 1e-3 mm 容差下均有效构建；与原样例体积在测试精度内一致。
- 外圆 R=100 mm、内孔 r=1 mm、圆心距离 98.99 mm，真实间隙 0.01 mm：校验及构建通过，体积符合解析值。
- 中点 x 从 25 改为 25.0005 且保留冲突辅助字段时拒绝；移除辅助字段后有效构建，体积确实改变。
- 修改安全提取的圆/弧半径和拉伸距离后仍可有效构建，布尔调用顺序回归通过。

测试日志：[UNIT_TESTS.txt](../../out/corpus/ir-contract-v3-local-20261025/UNIT_TESTS.txt)。

## 批次结果

| 批次 | 总数 | train_ready | provisional | rejected |
|---|---:|---:|---:|---:|
| v4 固定回归：core 14 + edge 5 + targeted 4 | 23 | 18 | 1 | 4 |
| 0000 按文件名排序前 50 例 | 50 | 39 | 2 | 9 |

固定 23 例对比既有同协议 v4：此前 15 个 train_ready 全部保留（15/15），00001590、00007944、00002722 从 provisional 转为 train_ready。不能仅由此归因到某个修复；这些样本原先是测量不确定，本次派生几何/数值表达变化后得到可用证据。

前 50 例对比历史 structured-v2-first50：历史 32 个 train_ready 全部保留。历史采用不同的评估和双轨流程，32→39 **不能解释为本轮修复独立带来的成功率提升**。

- [固定 23 例 manifest](../../out/corpus/ir-contract-v3-local-20261025/manifest.json)
- [前 50 例 manifest](../../out/corpus/ir-contract-v3-first50-20261025/manifest.json)
- 两批均保留实际代码、候选/参考 STEP、prepared IR、symbols、逐例报告和 train_ready.jsonl；未运行全量 0000。

## 原尺寸、坐标及确定性审计

前 50 例有 41 对新旧候选 STEP 可读取；实体数全部一致，体积和面积相对误差均小于 1e-6，世界 bbox 最大坐标差小于 0.001 mm。实际最大值：体积 6.254939513188174e-7、面积 1.1681721845134146e-7、bbox 0.0000027459480236302625 mm。

这只是生成器原空间不变量诊断，**不是新增生产验收阈值，也不能替代完整几何等价证明**。详见 [RAW_INVARIANT_COMPARISON.json](../../out/corpus/ir-contract-v3-first50-20261025/RAW_INVARIANT_COMPARISON.json)。

固定回归 21 份、前 50 例 42 份已生成代码及 prepared IR，均用最终生成器逐例复现。收尾仅同步代码说明中的 v3 版本标识及 additive original_params 元数据，核对可执行 AST 不变，并同步训练 JSONL；没有更改已验收几何。详见各批次 GENERATION_AUDIT.json。

## 剩余问题与严格拒绝证据

- 固定回归：00000062、00007271 仍在布尔构建时产生无效拓扑；00000073 缺少先前 body；00000076 无可用拉伸。00000137 在 STEP 往返形状比较阶段证据不确定，继续 provisional。
- 前 50 例额外保留 00000352 的 Common/Cut 一致性测量错误，不降级为通过。其余 adapter/不完整 IR 原因保存在逐例报告。
- 00000093 从原来的后续比较失败提前到圆弧一致性拒绝。源 JG5 声明圆心 [0,0] mm，三点计算圆心为 [-0.000580177559873718, 0.000907318685179348] mm，偏差 0.0010769555225062 mm，大于 0.001 mm 声明容差。偏差接近门槛，可能与源端点舍入有关；按已选严格策略仍拒绝，不伪称其一定是损坏数据。历史该例也未通过。证据：[ARC_CONFLICT_00000093.json](../../out/corpus/ir-contract-v3-first50-20261025/ARC_CONFLICT_00000093.json)。

本轮没有声称恢复建模历史或支持任意 STEP，也未扩大曲线/特征类型范围。下一阶段可在此后端上开展 STEP→IR；目前需要继续单独定位的生成问题是剩余布尔拓扑失败。
