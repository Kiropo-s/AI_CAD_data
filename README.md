# json2scapi

当前主线：**DeepCAD JSON → IR → readable SCAPI code → STEP → 独立参考验收**。长期目标为 STEP→IR→code，当前尚未开发 STEP→IR。其他 JSON adapter 和 CadQuery/build123d emitter 也属于后续扩展。

生成规则为 readable v3 / ir-geometry-v1；IR 0.1 仍为未冻结草案。生产语料沿用 readable-only-v2 / similarity-shape-v4，CD 协议为 similarity-surface-v3。

## 目录

~~~text
json2scapi/
├─ README.md                   # 当前入口、命令和模块导航
├─ *.py                        # 旧命令兼容入口；业务实现位于下列包
├─ adapters/                   # DeepCAD JSON → IR
├─ ir/                         # 结构校验、几何准备、轮廓及解析几何
├─ codegen/                    # 可读代码生成及 SCAPI 草图构建
├─ evaluation/                 # 形状配准、度量及验收决策
├─ pipeline/                   # 语料流水线及固定回归
├─ runtime/                    # 本地 worker、资源路径及输出布局
├─ legacy/                     # 历史单例及批量验证实现
├─ docs/
│  ├─ README.md                # 规范与报告索引
│  ├─ specs/                   # IR、代码生成、评估规范
│  ├─ reports/                 # 已完成实验及回归报告
│  └─ archive/                 # 整理前的文档快照
├─ schema/                     # IR JSON Schema
├─ examples/                   # 小型、可追踪的 IR 示例
├─ tests/                      # 契约、几何、代码生成及评估回归
├─ tools/archive/              # 历史专项诊断脚本，非生产入口
└─ out/                        # 全部生成物，详见 out/README.md
   ├─ cases/                   # 单例产物，首次运行时创建
   ├─ corpus/                  # 训练语料、固定回归及逐例证据
   ├─ batches/                 # 历史批量验证产物
   └─ tmp/                     # 可丢弃的临时输出和调试日志
~~~

## 规则与结果

- [IR_SPEC.md](docs/specs/IR_SPEC.md)：数据结构、单位/坐标、几何准备、region 和布尔语义。
- [READABLE_CODE_SPEC.md](docs/specs/READABLE_CODE_SPEC.md)：命名、参数提取、函数层次和来源映射。
- [EVALUATION_SPEC.md](docs/specs/EVALUATION_SPEC.md)：当前形状验收、STEP 往返和状态决策。
- [IR→Code v3 实施与回归](docs/reports/IR2CODE_V3_RESULTS.md)：79 项测试通过；固定 23 例为 18 train_ready / 1 provisional / 4 rejected；前 50 例为 39 / 2 / 9。未运行本轮全量 0000。
- [模块整理与验证](docs/MODULE_LAYOUT.md)：19 个模块按职责分包；完整 83 项测试通过，旧命令保留兼容。
- [全部文档](docs/README.md)：历史实验结果单独列出，不能混用为当前评估通过率。

生成器保留 IR 的原始尺寸、坐标和布尔顺序，只做有记录的有界端点规范化。评估允许一个整体正等比缩放、平移和正规旋转，比较实体空间并集；不允许镜像、分轴缩放或逐实体独立对齐。train_ready 表示当前协议下容差内形状一致，不证明恢复原始建模历史。

## 快速开始

以下命令均在 F:\AI_CAD 下执行。使用已配置的 envs/simplecad 环境。

### 单例：JSON → IR → code

~~~powershell
& .\envs\simplecad\Scripts\python.exe .\json2scapi\deepcad_to_ir.py --input .\datasets\DeepCAD\cad_json\0000\00000007.json
& .\envs\simplecad\Scripts\python.exe .\json2scapi\ir2code.py --input .\json2scapi\out\cases\00000007\model.ir.json
~~~

默认产物：

~~~text
out/cases/00000007/
├─ model.ir.json
├─ model.readable.ftc.py
├─ model.readable.symbols.json
└─ model.readable.prepared.ir.json
~~~

生成代码只依赖 simplecadapi，可导入并调用 build()。原始 IR 保留，准备后的 IR 和全部调整另存。

### 训练语料：构建 STEP 并独立验收

~~~powershell
& .\envs\simplecad\Scripts\python.exe .\json2scapi\build_corpus.py --cad-dir .\datasets\DeepCAD\smoke\core --name my-core-run --isolate
~~~

每个批次必须使用新的名称，已有目录不会覆盖。每例保存原始 IR、prepared IR、代码、symbols、候选与参考 STEP 及 report。根目录保存 manifest.json、errors.csv、progress.jsonl 和 train_ready.jsonl；只有通过独立验收的代码进入训练 JSONL。

参考不支持、参考出错或必需测量无法可靠取得时保持 provisional；明确的候选构建错误或几何不匹配为 rejected。--log-only 会丢弃逐例产物，仅保留日志及训练索引，不能作为包含代码的训练导出。

### 固定回归与单元测试

~~~powershell
& .\envs\simplecad\Scripts\python.exe .\json2scapi\run_shape_regression.py --out .\json2scapi\out\corpus\local-check-001
& .\envs\simplecad\Scripts\python.exe -m unittest discover -s .\json2scapi\tests -v
~~~

固定回归包括 smoke/core 14 例、smoke/edge 5 例和 4 个定点样本，不自动运行前 50 或全量数据。

## 模块导航

| 职责 | 实现模块 |
|---|---|
| JSON 适配 | adapters/deepcad.py |
| IR 结构和几何契约 | ir/validation.py、preparation.py、geometry2d.py、regions.py |
| 可读代码生成 | codegen/api.py、readable.py、sketch_builder.py |
| 生产语料与固定回归 | pipeline/corpus.py、regression.py |
| 执行与 STEP 往返 | runtime/worker.py |
| 形状评估与决策 | evaluation/similarity.py、registration.py、policy.py |
| 基础几何度量 | evaluation/metrics.py；旧 compare_shapes/compare_step_files 不是训练决策入口 |
| 资源及输出位置 | runtime/paths.py、output_layout.py |
| 旧单例/批量工具 | legacy/ 下的四个模块 |

包结构、依赖边界和旧模块迁移对照见 [模块说明](docs/MODULE_LAYOUT.md)。根目录原有 CLI 文件只转发调用，保留旧命令及这些入口模块的导入兼容；其余库模块请改用完整包路径。旧工具部分仍依赖 research 中的历史 worker，不用于当前训练入库。

新增调用建议使用包路径，在 F:\AI_CAD 下执行：

~~~powershell
& .\envs\simplecad\Scripts\python.exe -m json2scapi.adapters.deepcad --input .\datasets\DeepCAD\cad_json\0000\00000007.json
& .\envs\simplecad\Scripts\python.exe -m json2scapi.codegen.api --input .\json2scapi\out\cases\00000007\model.ir.json
& .\envs\simplecad\Scripts\python.exe -m json2scapi.pipeline.corpus --cad-dir .\datasets\DeepCAD\smoke\core --name my-package-core-run --isolate
~~~

Python 调用示例：

~~~python
from json2scapi.adapters.deepcad import convert
from json2scapi.ir.validation import validate_ir
from json2scapi.codegen.api import generate_readable_with_metadata
~~~

## 存放约定

- 源码、测试、小型示例和规范可纳入 Git；out 下仅目录说明和忽略规则纳入 Git。
- 生成代码、STEP、日志及实验数据统一放 out；文档中的汇总报告放 docs/reports，原始证据继续保存在原批次。
- 规范变更更新 docs/specs；实验结果新增报告，不覆盖历史报告的结论或评估版本。
- 专项历史脚本放 tools/archive，不放临时目录，也不替代正式测试及生产入口。
