# 实验结果归档

本目录保存**进入版本控制的最小实验记录**。逐例的 `model.step`、`events.jsonl`、`worker.log` 等大体积产物仍留在本地 `out/<run>/`，由 `.gitignore` 排除；这里只收录索引、批次摘要和代表性样本，保证仓库克隆后仍能核对结论。

## 1. 内容

```text
docs/results/
├── README.md                          本说明
├── PRUNE_LOG.md                       2026-10-28 清理记录（删除了哪些运行、保留了什么）
├── index.json                         当前本地运行的机器可读索引（自动生成）
├── runs.md                            同一索引的 Markdown 表格（自动生成）
├── index-before-prune-20261028.json   清理前的完整运行清单快照
├── runs-before-prune-20261028.md      清理前的 Markdown 快照
├── abc100-p05-diagnostics-20261027-r4/ 100 例权威运行：summary / cases.csv / manifest
├── abc-first20-p2a-20261028/          P2A profile 重构后的 20 例 smoke
├── abc100-plan1-verified-20261026/    1.0 首次完整 100 例的摘要
├── abc100-recognition-extension-20261027-r1/  B 样条/椭圆扩展轮次的摘要
├── legacy-logs/                       0.x 验证文档引用的 4 份测试日志
└── samples/                           四个已通过案例的完整计划、生成代码与验收报告
    ├── 00000006-bounded-csg/          verified_bounded_csg
    ├── 00000007-cap-prism/            analytic_cap_prism
    ├── 00000008-partition-csg/        verified_partition_csg
    └── 00000009-revolution/           verified_meridian_revolution
```

每个样本目录包含：`report.json`、`checks.json`、`model.plan.json`、`model.scapi.py`、`model.symbols.json`、`provenance.json`、`preparation.json`、`recognition-diagnostics.json`、`source-topology.json`。不含源 STEP 与导出 STEP（二进制，且源数据不随仓库分发）。

`index.json` / `runs.md` 由脚本生成，不要手工编辑：

```powershell
python tools/archive_runs.py           # 重新生成
python tools/archive_runs.py --check   # 索引过期时返回非零
```

## 2. 权威结果：100 例（2026-10-27）

运行目录（本地）：`out/abc100-p05-diagnostics-20261027-r4`
实现指纹：`3bdc6fd5ed474a4198cfc30d79870023b36926f6c9ed14128edeecd4972c60ca`
逐例审计：[`docs/ir-redesign/regression-100-p05-diagnostics-20261027-final.json`](../ir-redesign/regression-100-p05-diagnostics-20261027-final.json)

| outcome | 数量 |
| --- | ---: |
| accepted | 30 |
| incomplete | 63 |
| invalid_input | 5 |
| error | 2 |
| **合计** | **100** |

通过 30 例共覆盖 **172 个源实体**，按实体计的识别方法分布：

| 方法 | 实体数 |
| --- | ---: |
| `analytic_cap_prism` | 160 |
| `verified_bounded_csg` | 6 |
| `verified_partition_csg` | 3 |
| `verified_meridian_revolution` | 3 |

失败原因分布：

| reason_code | 数量 |
| --- | ---: |
| `capability.surface_type` | 31 |
| `search.no_verified_candidate` | 19 |
| `import.invalid_topology` | 5 |
| `domain.loose_faces` | 4 |
| `limit.source_faces` | 3 |
| `worker.timeout` | 2 |
| `recognition.replay_mismatch` | 2 |
| `domain.loose_edges` | 1 |
| `domain.no_solids` | 1 |
| `measurement.unreliable` | 1 |
| `limit.source_bodies` | 1 |

已知独立问题案例：

- `00000000`、`00000056`：`recognition.replay_mismatch`（原生重放与源几何不一致）。
- `00000025`、`00000037`、`00000044`、`00000082`、`00000085`：源 STEP 拓扑无效，属输入域问题。
- `00000052`：`measurement.unreliable`（占据空间测量未通过可靠性边界）。

## 3. 最新实验：P2A 20 例 smoke（2026-10-28）

运行目录（本地）：`out/abc-first20-p2a-20261028`
实现指纹：`135d063d2a567cf272fd0b92f6f9f6dd51954144c2be0f048b36e57a93d455b5`
摘要：[`abc-first20-p2a-20261028/summary.json`](abc-first20-p2a-20261028/summary.json)

| outcome | 数量 |
| --- | ---: |
| accepted | 8 |
| incomplete | 11 |
| error | 1 |

该结果与历史基线的前 20 例逐例一致，说明把 `recognition/profile.py` 接入 `recognition/prism.py` 没有引入回退。**但它只覆盖 20 例**；当前工作树（含 P2A）尚未重跑完整 100 例，所以第 2 节的 100 例结论不能直接视为对当前 HEAD 的认证。

## 4. 证据链与自校验

每个已通过案例都能独立核对：

1. `checks.json` 固定 `plan_hash`、`code_sha256`、`export_sha256`。
2. `report.json` 记录源/原生/生成/STEP 回读的逐体签名、CD/IoU/体积检查与 eligibility 分项。
3. `model.plan.json` 加上 `model.scapi.py` 即可脱离源 STEP 重放：`--action validate|replay|compile`。
4. `docs/ir-redesign/regression-100-p05-diagnostics-20261027-final.json` 记录每例的 `source_sha256`、`report_sha256`、`plan_hash`。

样本 `00000007-cap-prism` 的对照值：

```text
report.json  sha256 = 27d6de116f077d735fdff144a296fd49573109004a2eab05912e6e11866e8a3f
plan_hash          = 137fa9cefb54d5a535a08fd59942f2b9f8c8028d062faa1357cb9c0c797366cc
```

## 5. 复现

在仓库**根目录**（包含 `step2code/` 的目录）执行：

```powershell
./envs/simplecad/Scripts/python.exe -m step2code --input datasets/abc/step --limit 100 `
    --out step2code/out/abc100-rerun --timeout 120
```

`datasets/`、`envs/`、`out/` 均不随仓库分发，需要自备 ABC 数据集与 Python 环境（见 `requirements.txt`）。结果目录必须为空；中断后用同一命令加 `--resume` 恢复。

单元测试：

```powershell
python -m unittest discover -s step2code/tests -p 'test_*.py' -v
```

归档时（2026-10-28）验证：90 项测试通过。

## 6. 未纳入版本控制的内容

| 路径 | 体积 | 原因 |
| --- | ---: | --- |
| `out/` | 约 58 MB / 2 个运行目录 | 逐例 plan/code/STEP/events；只保留权威基线与最新实验 |
| `datasets/` | 约 19 GB | ABC 原始 STEP，第三方数据 |
| `envs/` | — | 本地虚拟环境 |
| `model.step`、`*.stl` | — | 二进制导出物 |

`index.json` 登记当前保留的本地运行目录及其结果分布。2026-10-28 已删除 28 个被取代的历史运行（约 312 MB），删除清单、原因和替代证据见 [`PRUNE_LOG.md`](PRUNE_LOG.md)；清理前的完整清单保留为 [`index-before-prune-20261028.json`](index-before-prune-20261028.json)。

## 7. 数据来源与许可

- 实验输入来自 ABC 数据集（`datasets/abc`）。该数据**不包含**在本仓库中，使用需遵循其原始许可。
- 本仓库暂未添加开源许可证；在公开上传前请由作者确定许可条款。
- 文档中的 `F:\AI_CAD` 等路径是历史运行记录，克隆后按第 5 节替换为本地路径。
