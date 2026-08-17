import argparse
from datetime import datetime
import json
from pathlib import Path
import sys
from typing import Any

from .benchmark import (
    SUPPORTED_DATA_STRUCTURES,
    run_benchmark,
    run_data_structure_benchmarks,
)
from .config import load_targets
from .functional import run_functional
from .report import render_report


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def data_structures(value: str):
    parsed = [item.strip().lower() for item in value.split(",") if item.strip()]
    if not parsed:
        raise argparse.ArgumentTypeError("at least one data structure is required")
    unsupported = sorted(set(parsed) - set(SUPPORTED_DATA_STRUCTURES))
    if unsupported:
        supported = ",".join(SUPPORTED_DATA_STRUCTURES)
        raise argparse.ArgumentTypeError(
            f"unsupported data structures: {','.join(unsupported)}; supported: {supported}"
        )
    if len(parsed) != len(set(parsed)):
        raise argparse.ArgumentTypeError("data structures must not contain duplicates")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory-benchtool",
        description="Run functional and lightweight performance tests against Redis and Tidis.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("functional", "benchmark", "structures", "all"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", type=Path, required=True, help="target JSON configuration")
        command.add_argument("--output-dir", type=Path, help="result directory")
        if name in {"benchmark", "structures", "all"}:
            command.add_argument("--operations", type=positive, default=5000)
            command.add_argument("--clients", type=positive, default=16)
            command.add_argument("--value-size", type=positive, default=128)
        if name in {"structures", "all"}:
            command.add_argument(
                "--structures",
                type=data_structures,
                default=list(SUPPORTED_DATA_STRUCTURES),
                help="comma-separated data structures: list,hash,set",
            )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        targets = load_targets(args.config)
        output_dir = args.output_dir or Path("results") / datetime.now().strftime("%Y%m%d-%H%M%S")
        output_dir.mkdir(parents=True, exist_ok=True)

        functional_results = []
        benchmark_results = []
        structure_results = []
        if args.command in {"functional", "all"}:
            functional_results = [run_functional(target) for target in targets]
            write_json(output_dir / "functional.json", functional_results)
        if args.command in {"benchmark", "all"}:
            for target in targets:
                try:
                    benchmark_results.append(
                        run_benchmark(target, args.operations, args.clients, args.value_size)
                    )
                except Exception as exc:
                    benchmark_results.append({
                        "target": target.public_dict(),
                        "error": f"{type(exc).__name__}: {exc}",
                    })
            write_json(output_dir / "benchmark.json", benchmark_results)
        if args.command in {"structures", "all"}:
            for target in targets:
                try:
                    structure_results.append(
                        run_data_structure_benchmarks(
                            target,
                            args.operations,
                            args.clients,
                            args.value_size,
                            args.structures,
                        )
                    )
                except Exception as exc:
                    structure_results.append({
                        "target": target.public_dict(),
                        "error": f"{type(exc).__name__}: {exc}",
                    })
            write_json(output_dir / "structures.json", structure_results)
        if args.command == "all":
            parameters = {
                "operations": args.operations,
                "clients": args.clients,
                "value_size": args.value_size,
                "structures": args.structures,
            }
            report = render_report(
                functional_results,
                benchmark_results,
                parameters,
                structure_results,
            )
            (output_dir / "report.md").write_text(report, encoding="utf-8")

        print(f"Results written to {output_dir.resolve()}")
        functional_ok = all(result["passed"] for result in functional_results)
        benchmark_ok = all("error" not in result for result in benchmark_results)
        structures_ok = all(
            "error" not in result
            and all("error" not in item for item in result["structures"].values())
            for result in structure_results
        )
        return 0 if functional_ok and benchmark_ok and structures_ok else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
