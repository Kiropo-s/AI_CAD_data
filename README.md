# AI_CAD

一个仓库，三个可运行的 Python 包，共享同一套 OCP / SimpleCADAPI 环境。

| 目录 | 作用 | 入口 |
|---|---|---|
| [step2code/](step2code/README.md) | STEP → `step-plan/1` → 原生重放 + 独立可读 SCAPI 代码，CD/IoU/体积验收 | `python -m step2code` |
| [step2code_direct/](step2code_direct/README.md) | STEP → 直接生成 SCAPI 代码，不经过 IR（0.7.0） | `python -m step2code_direct` |
| [json2scapi/](json2scapi/README.md) | DeepCAD JSON → 通用 IR → 可读 SCAPI；被 `step2code_direct` 运行时复用 | `python json2scapi/build_corpus.py` 等 |

## 环境

Python 3.13 + `cadquery-ocp` 7.9.3.1.1 + `simplecadapi` 2.2.6。

```powershell
py -3.13 -m venv envs\simplecad
envs\simplecad\Scripts\python.exe -m pip install -r requirements.txt
```

## 常用命令（从仓库根目录执行）

```powershell
# step2code：转换 + 测试
envs\simplecad\Scripts\python.exe -m step2code --input <STEP目录> --limit 20 --out step2code\out\first20 --timeout 120
envs\simplecad\Scripts\python.exe -m unittest discover -s step2code\tests -p "test_*.py" -v

# step2code_direct：转换 + 测试
envs\simplecad\Scripts\python.exe -m step2code_direct --input <STEP目录> --limit 20 --samples 8192 --timeout 120 --out step2code_direct\out\first20
envs\simplecad\Scripts\python.exe -m unittest discover -s step2code_direct\tests -v

# json2scapi：单例 / 语料
envs\simplecad\Scripts\python.exe json2scapi\deepcad_to_ir.py --input datasets\DeepCAD\cad_json\0000\00000007.json
envs\simplecad\Scripts\python.exe json2scapi\ir2code.py --input json2scapi\out\cases\00000007\model.ir.json
```

## 数据与产物

- `datasets/`、`envs/`、`*/out/`、`*/exports/`、`*.step` 不纳入版本控制，换设备后需自行准备数据并重新生成。
- 实验结果摘要与代表性样本见 [step2code/docs/results](step2code/docs/results/README.md)，清理记录见 [PRUNE_LOG](step2code/docs/results/PRUNE_LOG.md)。
- `release/`、`src/`、`research/`、`deepcad_official/`、`.vscode/`、`.simplecad/`、`docs/DeepCAD.pdf` 为本地资料，未纳入本仓库。

## 历史

本仓库由 `step2code`、`step2code_direct`、`json2scapi` 三个独立仓库通过 `git subtree` 合并，各自原有提交历史保留。

