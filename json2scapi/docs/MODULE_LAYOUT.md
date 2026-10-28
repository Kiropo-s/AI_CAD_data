# 模块结构

2026-10-25 将根目录的 19 个实现模块按职责归入 Python 包。生成规则仍为 readable v3 / ir-geometry-v1；本次不修改 IR 语义、生成几何、评估规则或已有批次状态。

## 依赖边界

- adapters：源格式解析，调用共享 IR 几何函数；未来 STEP→IR 前端可放在此层，当前没有实现。
- ir：来源无关的结构校验、端点准备、解析曲线和 region 关系。不导入 adapter、emitter 或评估层。
- codegen：消费准备后的 IR，输出 standalone SCAPI 源码。api 提供公开函数与 CLI，readable 组织代码，sketch_builder 提供点/边构建及字面量格式化。readable 不再反向导入 api。
- evaluation：配准、几何指标、验收阈值与决策，保持 similarity-shape-v4 协议。
- runtime：资源路径、输出布局和隔离 worker；worker 调用 evaluation 完成 STEP 回读验收。
- pipeline：组合 adapter、校验、codegen、worker、独立参考与评估，不承载新的几何规则。
- legacy：保留旧单例、旧批量及最小比较实现，生产流水线不导入此包。

包的 __init__.py 只标识职责，不自动导入 CAD 内核。纯 IR 转换与代码生成不要求在导入包时加载 OCP。

## 资源与子进程

runtime/paths.py 统一定位工作区、包目录、Schema、out 和官方参考适配入口。位置由文件绝对路径推导，不依赖运行命令的当前目录。

子进程继续通过根目录兼容入口启动：corpus 使用 build_corpus.py，worker 使用 local_worker.py。worker 保留指定 staging 目录作为 cwd，避免第三方执行产物散落到源码目录。隔离 case 的输入 JSON 在启动前解析为绝对路径，避免切换 cwd 后相对输入失效。测试覆盖从其他工作目录运行旧命令；新模块通过 python -m json2scapi.<package>.<module> 启动。

## 迁移对照

| 原实现 | 当前实现（相对 json2scapi） |
|---|---|
| deepcad_to_ir.py | adapters/deepcad.py |
| validate_ir.py | ir/validation.py |
| ir_preparation.py | ir/preparation.py |
| geometry2d.py | ir/geometry2d.py |
| profile_regions.py | ir/regions.py |
| ir2code.py | codegen/api.py |
| readable_codegen.py | codegen/readable.py |
| evaluation_policy.py | evaluation/policy.py |
| geometry_metrics.py | evaluation/metrics.py |
| similarity_registration.py | evaluation/registration.py |
| similarity_evaluation.py | evaluation/similarity.py |
| build_corpus.py | pipeline/corpus.py |
| run_shape_regression.py | pipeline/regression.py |
| local_worker.py | runtime/worker.py |
| output_layout.py | runtime/output_layout.py |
| batch_verify.py | legacy/batch_verify.py |
| compare_minimal.py | legacy/compare_minimal.py |
| deepcad_render.py | legacy/deepcad_render.py |
| verify_deepcad.py | legacy/verify_deepcad.py |

原 ir2code.py 中的 SketchBuilder、数值格式化等函数另外提取到 codegen/sketch_builder.py；codegen/api.py 保留这些原有符号供入口兼容。

## 兼容范围

根目录保留 10 个薄入口：deepcad_to_ir.py、validate_ir.py、ir2code.py、build_corpus.py、run_shape_regression.py、local_worker.py，以及四个历史工具。原命令参数和默认输出位置保持。

这 10 个入口被导入时转发到同一个实现模块，避免复制函数或出现两套模块状态。其余旧平铺库文件已移走；外部脚本需要按上表更新导入，例如 from geometry_metrics import read_step 改为 from json2scapi.evaluation.metrics import read_step。

新增源码和测试使用完整包路径导入，不依赖根目录兼容入口或将 json2scapi 目录加入 sys.path。普通包调用应从 F:\AI_CAD 运行；当前未提供 pip 安装配置。历史 tools/archive 脚本已更新导入，但仍有写回历史报告的副作用，不用于本次迁移验证。

## 本次验证

- 完整测试：83 项通过（原有 79 项及新增 4 项），耗时 54.764 秒；[运行日志](reports/evidence/module-layout-20261025/unittest.log)。
- 3 个 examples IR 的生成源码与 symbols 元数据逐项对比，均与迁移前完全一致。
- 新包入口使用相对输入路径和 --isolate 运行 00000007，经过嵌套 worker、候选 STEP 回读和独立参考验收，结果为 train_ready / accepted；[逐例报告](reports/evidence/module-layout-20261025/00000007.report.json)。
- 兼容入口与实现共享模块对象，旧脚本可从其他工作目录生成代码；新包入口生成相同代码及伴随产物。
- 纯 adapter / IR / codegen 导入不加载 OCP 或 simplecadapi。
- 几何算法及指标核心迁移只改变导入路径；阈值、评估版本和生成规则版本不变。本次未重跑固定 23 例、前 50 例或全量批次。

2026-10-25 清理后，仅归档上述测试日志和逐例验收报告；迁移基线快照、临时脚本及该临时验证的模型产物已删除。归档报告中的 paths 保留执行当时的位置，不表示临时模型仍然存在。当前正式回归证据仍位于 out/corpus。
