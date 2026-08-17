#!/usr/bin/env python3
import argparse
import csv
import json
from pathlib import Path
import statistics
from typing import Any, Dict, Iterable, List, Tuple


def load_results(input_dirs: Iterable[Path]) -> Tuple[List[Dict[str, Any]], List[str]]:
    completed = []
    errors = []
    for input_dir in input_dirs:
        for path in sorted(input_dir.rglob("workload.json")):
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
                for record in records:
                    if record.get("status") == "completed":
                        completed.append(record)
                    else:
                        errors.append(f"{path}: {record.get('status')}: {record.get('error', '')}")
            except (OSError, ValueError, TypeError) as exc:
                errors.append(f"{path}: {type(exc).__name__}: {exc}")
    return completed, errors


def summarize(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[Any, ...], List[Dict[str, Any]]] = {}
    for record in records:
        target = record["target"]
        key = (
            target["name"], target["product"], target["endpoint"],
            record["data_structure"], record["dataset_rows"],
            record["clients"], record["mode"],
        )
        groups.setdefault(key, []).append(record)

    rows = []
    for key, samples in sorted(groups.items(), key=lambda item: (
        item[0][3], item[0][4], item[0][5], item[0][6], item[0][1]
    )):
        measurements = [sample["measurement"] for sample in samples]
        rows.append({
            "target": key[0],
            "product": key[1],
            "endpoint": key[2],
            "structure": key[3],
            "rows": key[4],
            "clients": key[5],
            "mode": key[6],
            "rounds": len(samples),
            "throughput_ops_per_second_median": round(statistics.median(
                item["throughput_ops_per_second"] for item in measurements
            ), 1),
            "throughput_ops_per_second_min": round(min(
                item["throughput_ops_per_second"] for item in measurements
            ), 1),
            "throughput_ops_per_second_max": round(max(
                item["throughput_ops_per_second"] for item in measurements
            ), 1),
            "latency_p50_ms_median": round(statistics.median(
                item["overall_latency_ms"]["p50"] for item in measurements
            ), 3),
            "latency_p95_ms_median": round(statistics.median(
                item["overall_latency_ms"]["p95"] for item in measurements
            ), 3),
            "latency_p99_ms_median": round(statistics.median(
                item["overall_latency_ms"]["p99"] for item in measurements
            ), 3),
            "error_count_median": round(statistics.median(
                item.get("error_count", 0) for item in measurements
            ), 1),
            "error_rate_percent_median": round(statistics.median(
                item.get("error_rate_percent", 0) for item in measurements
            ), 4),
        })
    return rows


def render_markdown(rows: List[Dict[str, Any]], errors: List[str]) -> str:
    lines = [
        "# Redis 与 Tidis 规模性能测试汇总",
        "",
        "结果按目标、结构、数据量、并发和模式分组；吞吐与延迟均取各轮中位数。",
        "",
        "| 目标 | 结构 | 数据量 | 并发 | 模式 | 轮数 | 吞吐 ops/s | p50 ms | p95 ms | p99 ms | 错误率 |",
        "|---|---|---:|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['target']} | {row['structure']} | {row['rows']} | "
            f"{row['clients']} | {row['mode']} | {row['rounds']} | "
            f"{row['throughput_ops_per_second_median']} | "
            f"{row['latency_p50_ms_median']} | {row['latency_p95_ms_median']} | "
            f"{row['latency_p99_ms_median']} | {row['error_rate_percent_median']}% |"
        )
    lines.extend(["", "## 异常记录", ""])
    if errors:
        lines.extend(f"- {error}" for error in errors)
    else:
        lines.append("无。")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", action="append", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    records, errors = load_results(args.input_dir)
    rows = summarize(records)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "scale-summary.json").write_text(
        json.dumps({"rows": rows, "errors": errors}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (args.output_dir / "scale-summary.csv").open("w", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=list(rows[0]) if rows else ["status"])
        writer.writeheader()
        if rows:
            writer.writerows(rows)
    (args.output_dir / "scale-summary.md").write_text(
        render_markdown(rows, errors), encoding="utf-8"
    )
    print(f"Summarized {len(records)} runs into {len(rows)} result rows")
    return 0 if records and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
