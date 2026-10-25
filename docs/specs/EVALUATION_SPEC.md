# 形状一致性验收规范

生产语料入口为 `build_corpus.py`，使用 `pipeline_version=readable-only-v2`、`evaluation_version=similarity-shape-v4`、`equivalence_mode=similarity_shape`。低层 `geometry_metrics.compare_shapes/compare_step_files` 保留旧世界坐标策略，供旧调用方和历史测试使用，不是当前训练导出的验收入口。

## 一致性的含义

允许候选整体变换 `x' = sRx + t`，其中 `s>0`，`RᵀR=I` 且 `det(R)=+1`。忽略绝对尺寸、位置和朝向。禁止镜像、非均匀缩放、剪切和局部变形；所有实体共同使用一个变换，相对位置和间距必须保留。

默认比较占据空间，即全部实体的空间并集。仅在测量副本上做并集、归一化、网格化及配准。原始 STEP、代码和独立参考均不改写。body 分组与数量差异只诊断；不证明 body 身份或完整建模历史一致。转换器已知的 IR 契约缺陷不在本轮修复范围；独立验收也不意味着所有微小局部变化都能检出。

## 归一化与配准

1. 检查输入拓扑，提取实体并在独立副本上求并集。不使用 healing、自动对齐源文件或放宽布尔容差。并集失败不可回退到体积相加。
2. 使用常规 `BRepGProp.VolumeProperties_s` 计算体积 `V`、体积质心 `c`、中心惯性张量 `I`，取 `L=sqrt(6 trace(I)/V)`；每侧分别按 `x_n=(100/L)(x-c)` 归一化。单位为归一化 mm，`raw` 字段保留源世界 mm 诊断。归一化后的体积误差仍有意义，不强制匹配双方体积。本流程不使用额外高精度自适应体积或惯性积分，以避免近零差集碎片带来的不可控耗时。
3. 初值包含原始姿态、24个正规主轴排列、seed=17的128个随机旋转。为立方体等主轴退化形状补充解析平面法向生成的正规旋转初值；它们只是候选，不能跳过 B-rep 验收。
4. 每侧2048个配准点粗筛8个初值，分别做最多100次最近邻/Kabsch旋转细化。体积质心固定，不拟合额外尺度、不允许反射。保留未细化姿态，以免采样误差破坏原本精确的主轴对齐。
5. 将姿态作用于完整候选 B-rep，依次检查保留姿态，存在一个通过全部硬指标即可通过。失败时保留配准点得分最佳姿态上的指标与错误及所有尝试记录；不得用另一个未对齐姿态的有限差集覆盖最佳姿态上的度量错误。归一化体积失败不依赖旋转，允许直接判定形状不匹配。
6. 相邻主惯性矩差距除以最大主惯性矩小于 `1e-4` 视为退化；退化且没有通过姿态时保持 `registration_unresolved`。主轴不退化的有限多起点配准仍不构成全局最优数学证明，报告保留搜索证据。

配准报告含原始候选→参考的完整4×4矩阵、逆矩阵、尺度比、平移、正规旋转矩阵及行列式。原始姿态和尺度补偿本身不产生质量告警。

## 指标协议与门槛

`similarity-surface-v3` 使用每侧8192个面积加权全局点、seed=0；配准点使用不同种子。双方使用相同的归一化离散精度 `0.001×100=0.1 mm`，角度精度0.2 rad；因此原始模型上的等效离散精度随模型尺度变化。复制几何并清除网格缓存后重新离散，排序后采样。报告记录版本和实际参数。

| 几何硬指标 | 定义（归一化并配准后） | 门槛 |
|---|---|---:|
| volume_relative_error | `abs(Vc−Vr)/Vr` | ≤ 1e-3 |
| volume_iou | `V(R∩C)/(Vr+Vc−V(R∩C))` | ≥ 0.999 |
| chamfer_relative | `0.5(mean(d(R,C))+mean(d(C,R)))/100` | ≤ 0.01 |

报告同时保存 `thresholds` 与 `threshold_directions`，明确IoU为下限，其他两项为上限。Volume比较归一化体积，不比较原始绝对体积。另存平方CD（两个方向均方距离之和，归一化mm²）和平均距离（归一化mm）；它们不新增门槛。不同CD协议不能直接比较。

本轮评估版本为 `similarity-shape-v4`，CD采样协议仍为 `similarity-surface-v3`。v3的对称差门槛退出主流程，改为IoU门槛；两种门槛并非严格相同，不将历史样本重新标为新协议通过。对称差只在IoU低于门槛时按需计算，字段为空表示未计算，不填零。

### 收紧告警与信息诊断

- `warnings`：仅保留 `local_surface_deviation`，表示局部表面P95偏差超过0.01，供训练语料抽检，不单独拒绝。
- `diagnostics`：原始solid数量不同、配准后bbox/面积相对误差大于1e-3、source bbox与原始参考bbox相对误差大于1e-3，仅为信息；数值与详细证据仍保留在原有指标字段。整体尺度、平移、旋转补偿只记录参数，不告警。
- `metric_errors`：记录测量异常及 `required` 标志；不再把低IoU同时标为告警。可选的面积/局部采样/源bbox测量失败不阻断验收，必需指标或布尔可靠性检查失败保持 `provisional / measurement_error`。

局部诊断为每个并集外表面采样32点、每个连通实体采样128点，统计到对侧全局点云的距离P95并除以100。补充点不混入全局CD。小实体缺失可能满足全局阈值而只产生局部告警，这是当前协议的检出边界。

### IoU可靠性与计算开销

IoU是必需指标，Common运算失败、无效拓扑、非有限数或越界值不能用CD替代。合法空交集的IoU为0；失败不视为空。IoU越界数值保留原值，不截断。仅对 `(1, 1+1e-8]` 的微小正向越界，额外运行双向Cut；体积界限及Cut/Common一致性均通过时，记录 `volume_iou_roundoff_verified` 信息诊断后允许IoU项通过。超出此范围、核查失败或证据矛盾仍标为error。此数值误差界不改变IoU的0.999验收门槛。检查已知整体变换前后的体积缩放恒等式（相对误差不大于1e-6）。

正常范围内通过IoU的姿态不计算双向Cut。IoU失败或出现上述微小越界时才计算双向差集，检查差集的体积界限及其与Common的代数一致性；没有独立的差集验收阈值。Cut失败或与Common矛盾时，该姿态的IoU检查标为error，保留原始数值。若双方布尔运算均声称无交集，则用交叠bbox内的中心点及seed=31的128个点做实体内部分类；找到共同内部点即证明布尔测量不可信。未找到点不能证明无交集，不能据此授予通过。以上不使用高精度自适应积分，也不以点采样估计体积。

## 执行、STEP与状态

本地独立worker执行实际 `build()` 返回值，支持Solid、Part、Assembly及其结果包装，处理装配placement；不相信脚本声明的BODIES/体积。要求实际body和solid数量至少1、拓扑有效、体积有限且为正。

候选依次检查导出、回读有效性、内存模型与回读模型的形状等价。回读形状使用同一相似变换规则和三项阈值；尺度或坐标变化只记录。导出/回读无效、已确认的形状变化为候选错误；回读度量不确定为provisional。

| 证据 | 状态 |
|---|---|
| adapter、IR、emitter或构建确认失败 | rejected |
| 候选STEP导出失败、无效或确认形状变化 | rejected |
| 参考不支持、缺失、生成或回读失败 | provisional |
| 配准未解决、必需度量失败或不可信 | provisional |
| 可靠配准后硬指标失败 | rejected |
| 硬指标通过，仅软告警或可选度量错误 | train_ready |

独立确认的归一化体积失败仍可拒绝；其余指标存在必需测量错误时优先保留provisional，不将布尔异常当作形状失败。worker本机崩溃、无法判断阶段时为provisional，不能声称已确认几何错误。候选worker和参考生成分别限时120秒，隔离单case限时180秒，超时保持provisional。

指标层返回 `checks`、`metric_errors`、`warnings`、`diagnostics`、原始及对齐指标；`evaluation_policy.decide` 决定状态。生产流程不读取旧混合 `passed`。训练JSONL携带评估版本、验收口径、告警及完整报告路径；logs-only只导出索引，不是训练代码。

## 局部验证

2026-10-25已完成v4验证：61项单元测试通过；固定23例局部回归为15个train_ready、4个provisional、4个rejected。结果及逐例证据见 [LOCAL_REGRESSION.md](../../out/corpus/similarity-shape-v4-local-20261025/LOCAL_REGRESSION.md)。未运行0000全量、前50或mixed-86；历史v3报告不作为v4验收结果。

运行全部单元测试：

```powershell
.\envs\simplecad\Scripts\python.exe -m unittest discover -s json2scapi/tests -v
```

固定局部回归入口，无扩大数据范围的参数：

```powershell
.\envs\simplecad\Scripts\python.exe json2scapi/run_shape_regression.py --out json2scapi/out/corpus/similarity-shape-v4-local-20261025
```

仅运行core14、edge5，以及00000447/00000137/00007271/00002722，重复则去重。已有目录拒绝覆盖。每例保存代码、IR、候选与参考STEP、执行/配准报告；根目录保存manifest、errors.csv、训练导出、逐例迁移与耗时报告。不运行前50、mixed-86、全量或旧日志重新入库。

## Python接口

```python
from pathlib import Path
from similarity_evaluation import evaluate_step_files

report = evaluate_step_files(Path("reference.step"), Path("candidate.step"))
print(report["decision"])  # status、reason、failed_checks
print(report["transform"]) # 有可用配准时，包含整体变换和逆变换
```

调用方负责区分读取参考/候选时的异常归属；生产流水线已分别处理。低层报告不使用旧 `passed`，必需/可选错误在 `metric_errors` 中逐项标记。原始世界尺度及坐标在 `raw` 和 `normalization` 中保留。
