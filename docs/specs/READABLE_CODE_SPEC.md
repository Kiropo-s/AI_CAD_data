# 独立 SCAPI 可读代码 v3

状态：已实现。对应 `ir2code.py --style readable`，元数据 `readable_version=3`。
这是代码生成契约，不是新的 CAD IR 版本；生产仅启用 `readable`，`faithful` 已关闭。

## 1. 程序层次

脚本只导入 `simplecadapi`，可直接导入并调用 `build()`，不依赖本项目运行时、IR 文件或附加建模库。生成顺序固定：

1. 按源特征分组的尺寸常量，长度 mm、taper 度；`BODIES = None`。
2. `make_profile_01(...)` 等草图函数：创建原平面的局部草图，返回有序 face 元组。
3. `apply_feature_01(bodies, ...)` 等特征函数：调用草图函数、拉伸各 region、逐步更新 body 集合。
4. `build()`：显式传入顶部常量、按顺序调用特征函数、写入 `BODIES`、调用 `_as_model()`。
5. 使用到的局部辅助函数：`_add_closed_polyline()` 和 `_as_model()`。

顶部参数由 `build()` 在调用时读取，再通过关键字形参传递给特征及草图函数。不得使用在函数定义时绑定的尺寸默认值。重复调用草图函数创建独立草图，不缓存可变几何。

## 2. 顺序、命名及参数

- 源特征按 IR_SPEC 第 5.1 节的连续来源规则分组；无来源时一个拉伸步骤对应一个函数。同组多 profile 保留所有 IR 步骤及其顺序，不合并布尔。多 region 在单个 IR 步骤内构建工具集合后执行该步布尔。
- profile 按拉伸首次引用顺序编号；函数定义仅生成一次。圆、弧按首次引用顺序分别编号。主要名称不使用随机源 ID，也不猜测零件用途。
- 常量示例：`FEATURE_01_DISTANCE_MM`、`FEATURE_01_REVERSE_DISTANCE_MM`、`FEATURE_01_CIRCLE_01_RADIUS_MM`；局部参数使用 `distance`、`reverse_distance`、`circle_01_radius`。源分组内同字段不同值不得共享参数，增加稳定的步骤序号区分。
- 跨 profile 的曲线尺寸只有在来源格式、源草图、源曲线及尺寸类型/值一致时共享；来源缺失时仅允许复用同一 IR 曲线的参数。同数值的独立圆和正反方向距离分别保留参数。
- 圆半径直接参数化。圆弧先按 IR 声明容差严格检查辅助字段，再从三点统一计算派生几何。只有原 IR 声明了 radius、准备后的派生几何自洽（数值表达检查 `math.isclose(rel_tol=1e-9, abs_tol=1e-9)`）、且端点不被多个弧共同控制时提取半径参数；不会把不存在的原始 radius 字段作为参数绑定。使用 `原坐标 + 方向分量 * (新半径 - 原半径)`，默认参数不重新计算原点坐标；相邻直线使用同一共享点。其余弧保持显式点。

## 3. 草图与运行时

- 混合线弧 loop 保持显式 SCAPI 点、边调用。禁止增加矩形、圆环、阵列、镜像或约束识别。
- 至少三条直线、准备后端点精确相接、loop 内顶点身份唯一时，可输出有序顶点列表，通过 `_add_closed_polyline()` 展开。其余情况保持逐边表达。该辅助函数只创建点和线，不解释通用几何数据。
- 坐标使用能往返恢复 Python float 的数值表达，不舍入到九位、不将微小非零值归零。准备层统一相邻端点，SCAPI 点身份在 loop 内精确复用；不同 loop 不按坐标接近合并。保留显式 `regions`，emitter 不重新推断孔归属。
- 单步多 region 的拉伸保留 region 顺序；对称/双向拉伸继续先构造两个方向，再按原方式合并工具体。非 `new` 且 body 集合为空时抛出错误，禁止回退。
- `_as_model()` 检查空结果，单实体直接返回，多实体通过 SCAPI 装配输出；最终原始 body 集合保存在 `BODIES`，供现有 worker 使用。

## 4. 元数据与语料

`symbols.json` 包含：

- `style`、`readable_version`；函数符号与全部尺寸常量的映射。
- `preparation`：`ir-geometry-v1`、长度容差、端点及派生圆弧调整。每条调整保留原始 IR 字段路径和原值。
- `feature_groups`：各特征函数包含的有序 IR 步骤列表。
- `profiles`：profile 函数的 IR/source、loop/edge 与源曲线关联、点标识、准备后曲线 `params` 和原始 `original_params`。
- 常量的值、单位、局部形参，以及全部 `bindings`（IR ID、字段路径、source）。
- `structure`：总行数、函数数、主函数行数、最长函数行数、特征/profile 数及是否使用折线辅助函数。结构指标用于审阅，不授予几何通过状态。

单文件生成保存旁置 `.symbols.json` 和 `.prepared.ir.json`；语料 case 保存原始 `fidelity.ir.json` 和新增 `prepared.ir.json`。语料目录和代码文件名保持兼容，报告记录版本与结构指标，训练 JSONL 标记可读代码版本。v1 的历史产物保留在原批次，不覆盖。

只有 readable 有效构建且通过独立参考验收才能 `train_ready`。不再执行 faithful 或双轨比较。参考不可用和度量失败保持 `provisional`；代码错误或明确几何不匹配为 `rejected`。旧双轨产物保持原样，禁止复用为新单路径报告。

## 5. 验收与限制

固定回归保留 core、edge、mixed 样本；历史 emitter 仅在 tests/_legacy_emitter.py 中作为测试 oracle，不属于生产路径。生成器回归检查原尺寸、坐标和布尔执行；训练验收严格沿用 EVALUATION_SPEC.md 的 v4 策略，不恢复旧世界坐标或实体数量硬门槛。

测试还覆盖缺失/非连续来源、独立等值尺寸、共享源参数、多 profile、多 region/孔、多实体、对称/双向拉伸、布尔执行顺序、确定性、Python 语法及圆/弧/距离编辑。至少人工审阅 10 例函数层次与命名。

本阶段优化代码层次，不保证总行数或训练 token 数下降，也不承诺所有尺寸修改都得到有效几何。大量 profile 的源特征仍可能产生较长函数；进一步抽象须另行评估，不改变建模历史来追求短代码。


## 当前形状验收（2026-10-25）

代码生成规则升级为 v3，原始尺寸与坐标、body 数据流保持；仅执行有记录的有界端点规范化。生产验收已转为 `similarity-shape-v4`：仅在测量副本上比较整体等比缩放、平移和正规旋转后的实体空间并集。几何硬指标为CD、IoU及归一化体积误差；body数量差异、源bbox冲突只作信息诊断，IoU测量失败属于必需证据不足，保持provisional。新本地worker检查实际 `build()` 返回对象，`BODIES`保留供追溯和旧入口使用。完整定义及局部验证范围见 [EVALUATION_SPEC.md](EVALUATION_SPEC.md)。
