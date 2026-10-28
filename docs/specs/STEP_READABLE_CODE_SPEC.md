# STEP2Code 可读代码规范 2

规范标识：`step-readable-2`。实现：step2code 1.0、`scapi-plan-1`。唯一输入契约为 [STEP Plan 1](../ir-redesign/PLAN_SPEC.md)，不支持旧 CAD IR 或 STEP recipe 格式。

## 1. 程序结构

生成文件依次包含说明、`math` / `simplecadapi as scad` 导入、公开常量、`BODIES`、局部轮廓函数、独立实体函数、`build()` 与固定的有限几何 helper。

- `make_profile_001(*, meridian=False)` 显式创建曲线、wire 和带孔 face。坐标保持局部二维；旋转轮廓明确映射为 (u,0,v)。
- `make_body_001()` 按稳定拓扑顺序显式调用拉伸、旋转、解析原语、布尔和变换。每个中间变量可追踪至节点 ID。
- `build()` 读取顶部常量，调用各独立实体工厂，保存世界坐标实体集合并返回真实模型。
- `_as_model()` 只以恒等 placement 包装多体，不再次定位。

不得嵌入 plan/STEP/BREP/base64、项目运行时、通用操作解释器、eval/exec 或动态 import。生成器自身可以用 AST 检查 Python 语法；不得重新解析旧 emitter 的源码做 AST 重命名来拼装新代码。

## 2. 参数与模板

当前自动识别产物每个源实体有四个独立参数：整体尺度、平移、x/y 轴。参数 ID 对应唯一顶部常量，默认值相同的不同实体不共享可编辑状态。固定的内部几何不冒充恢复的设计参数。

顶部常量在调用工厂时读取，不在函数默认值中提前捕获。尺度必须正且有限；frame 必须有限、单位、正交。非法 frame 直接报错，不静默归一化。

完全相同的固定轮廓定义允许复用同一函数；每次调用创建新 face/solid。模板指纹保留轮廓拓扑、值和显式参数绑定，不把等值的独立参数合并。当前不承诺自动提炼同构实体模板，也不因代码复用声称恢复阵列或设计历史。

## 3. 精确几何 lowering

圆弧按中心/半径/起角/有符号扫角构造三点弧，明确保留大弧方向。整圆拆成两段精确半圆弧。原生轮廓的 outer/holes 语义明确；SCAPI 后端根据其内孔反向约定在发射时定向，不改变 plan 的几何。

B 样条在编译时精确裁剪与反向后输出控制点、节点、重数、权重和周期性；不采样拟合。需要 SCAPI 运行时裁剪/拓扑检查的可编辑轮廓在 capability 阶段拒绝。

`solid.box` 的 width/depth/height 分别沿 XYZ；SCAPI 2.2 API 的 width/height/depth 分别沿 XYZ，因此后端必须显式交换 height/depth 参数名。

刚性 frame 用轴角旋转和平移 lowering，保留解析曲面。禁止使用当前环境会把解析面转为一般曲面的 `transform_rsolid()` 来实现刚性定位。不对世界位置单独配准或缩放。

n-ary union 发射为一次显式 SCAPI union，避免强制包装不连通的二元中间 Solid。后端无法表达的有效 Region3 中间态必须明确拒绝，不能偷嵌 OCP/BREP。

## 4. symbols 与证据

`model.symbols.json` 是派生产物：绑定 plan hash、规范 LF 源码文本的 SHA256、参数常量、函数、源行与节点映射、输出根及结构指标。它不参与执行；删除后代码和计划仍可运行。symbols.code_sha256 标识编译器返回的 UTF-8/LF 源码文本；checks/report 的 code_sha256/artifact_hashes 标识实际落盘字节。Windows 的 CRLF 转换可能使两者不同，验证 symbols 时先规范化 CRLF 为 LF。

来源、识别证据及几何检查分别在 provenance/checks/source/preparation。生成器不得从 evidence 中补读执行必需的 frame、profile 或尺寸。

结构指标统计轮廓、实体和 build 重建函数；固定辅助函数的行数不计入 max_function_lines。指标供审阅，不替代几何验收；本规范不承诺总行数下降，也不把默认几何通过当作任意参数编辑通过。

## 5. 验收

计划须通过严格 JSON/类型/全图校验和独立内核轮廓检查。实际生成代码须运行，逐源体覆盖核查，并完成 STEP 导出回读及 CD/IoU/V 验收。保留源物理尺寸与世界位置。

任何可靠必需指标失败优先产生 rejected；缺少可靠测量保持 incomplete。整体尺度/定位的有限编辑测试单独记录。`history_recovered=false`，装配约束不在本版能力范围内。
