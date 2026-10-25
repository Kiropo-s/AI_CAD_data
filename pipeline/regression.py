"""Fixed local shape regression: 19 smoke cases plus four targeted cases.

This entry point has no dataset-directory or limit flags and never runs the
first-50, mixed-86 or full dataset. Existing run directories are not overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import math
from pathlib import Path

from json2scapi.pipeline.corpus import run_corpus

from json2scapi.runtime.paths import WORKSPACE_DIR as ROOT
TARGETS = ("00000447", "00000137", "00007271", "00002722")


def fixed_sources():
    sources = {}
    groups = {}
    for group, expected in (("core", 14), ("edge", 5)):
        cases = sorted((ROOT / "datasets/DeepCAD/smoke" / group).glob("*.json"))
        if len(cases) != expected:
            raise ValueError(f"smoke/{group} must contain exactly {expected} cases")
        for case in cases:
            sources[case.stem] = case
            groups[case.stem] = group
    for stem in TARGETS:
        sources.setdefault(stem, ROOT / "datasets/DeepCAD/cad_json/0000" / f"{stem}.json")
        groups.setdefault(stem, "targeted")
    if not all(source.is_file() for source in sources.values()):
        raise FileNotFoundError("a fixed local fixture is missing")
    return list(sources.values()), groups


def write_report(out: Path, manifest: dict, groups: dict):
    history = ROOT / "json2scapi/out/corpus/readable-only-0000-log-20261024/manifest.json"
    old = {}
    if history.is_file():
        old = {item["stem"]: item for item in json.loads(history.read_text(encoding="utf-8"))["results"]}
    lines = ["# 形状一致性评估：局部回归", "",
        "本报告仅覆盖固定 smoke/core、smoke/edge 和四个定点样本；未运行前50、mixed-86或全量回归。",
        "旧分类来自历史日志，协议不同，仅用于解释状态迁移，不构成新旧正确率比较。", "",
        f"评估版本：`{manifest['evaluation_version']}`；CD：`{manifest['cd_protocol']}`。",
        "允许一个整体正等比缩放、平移和正规旋转；不允许镜像、分轴缩放或逐实体独立对齐。", "",
        "## 分类", "", "| 分组 | 数量 | train_ready | provisional | rejected |", "|---|---:|---:|---:|---:|"]
    for group in ("core", "edge", "targeted"):
        entries = [r for r in manifest["results"] if groups[r["stem"]] == group]
        counts = Counter(r["status"] for r in entries)
        lines.append(f"| {group} | {len(entries)} | {counts['train_ready']} | {counts['provisional']} | {counts['rejected']} |")
    lines += ["", "## 逐例判定", "",
        "每例原始代码、STEP、独立参考、执行结果及完整配准报告保存在对应子目录。",
        "表中变换将候选原始空间映射到参考原始空间；空值表示未取得可用配准。", "",
        "| 样本 | 分组 | 旧分类 | 新分类 / 原因 | 尺度比 | 旋转° | 平移模长mm | 配准 | 体积误差 | IoU | CD相对值 | 秒 | 告警 |",
        "|---|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---|"]
    def fmt(value):
        return "—" if value is None else f"{value:.6g}"
    for item in manifest["results"]:
        stem = item["stem"]
        metrics = item.get("metrics", {}).get("reference_vs_readable", {})
        registration = metrics.get("registration", {})
        transform = metrics.get("transform", {})
        rotation = registration.get("rotation_matrix")
        angle = None if rotation is None else math.degrees(math.acos(max(-1., min(1., (sum(rotation[i][i] for i in range(3)) - 1) / 2))))
        translation = transform.get("translation_mm")
        distance = None if translation is None else math.sqrt(sum(x*x for x in translation))
        reason = item.get("reference_evaluation")
        if item["stage"] not in ("independent_comparison", "reference"):
            reason = item["stage"]
        lines.append(f"| [{stem}]({stem}/report.json) | {groups[stem]} | {old.get(stem, {}).get('status', '—')} | "
            f"{item['status']} / {reason} | {fmt(transform.get('scale'))} | {fmt(angle)} | {fmt(distance)} | "
            f"{registration.get('status', '未执行')} | {fmt(metrics.get('volume_relative_error'))} | "
            f"{fmt(metrics.get('volume_iou'))} | {fmt(metrics.get('chamfer_relative'))} | "
            f"{fmt(item.get('elapsed_seconds'))} | {', '.join(item.get('warnings', [])) or '—'} |")
    lines += ["", "## 诊断", ""]
    for item in manifest["results"]:
        if item["status"] != "train_ready" or item.get("metric_errors") or item.get("diagnostics"):
            lines.append(f"- `{item['stem']}`：" + "; ".join(item.get("reasons", [])))
            if item.get("diagnostics"):
                lines.append("  信息诊断：" + ", ".join(item["diagnostics"]))
            errors = item.get("metric_errors", [])
            if errors:
                lines.append("  度量错误：" + json.dumps(errors, ensure_ascii=False))
    lines += ["", "完整证据见 `manifest.json`、`errors.csv`、`train_ready.jsonl` 和逐例 `report.json`。",
        "该训练导出只承诺容差内形状一致，不承诺原始尺度、世界坐标、body身份或完整建模历史一致。", ""]
    (out / "LOCAL_REGRESSION.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    sources, groups = fixed_sources()
    manifest = run_corpus(sources, args.out, isolate=True)
    write_report(args.out, manifest, groups)
    print(json.dumps({"total": manifest["total"], "counts": manifest["counts"],
                      "report": str(args.out / "LOCAL_REGRESSION.md")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
