from datetime import datetime, timezone
from typing import Any, Dict, List


def _metric(result: Dict[str, Any], operation: str, field: str) -> str:
    try:
        if field == "throughput":
            return f'{result[operation]["throughput_ops_per_second"]:,.1f}'
        return f'{result[operation]["latency_ms"][field]:.3f}'
    except (KeyError, TypeError):
        return "N/A"


def render_report(functional: List[Dict[str, Any]],
                  benchmarks: List[Dict[str, Any]], parameters: Dict[str, int]) -> str:
    benchmark_by_name = {item["target"]["name"]: item for item in benchmarks}
    lines = [
        "# Redis 与 Tidis 测试报告",
        "",
        f'生成时间：{datetime.now(timezone.utc).isoformat(timespec="seconds")}',
        "",
        "## 功能测试",
        "",
        "| 目标 | 产品 | 端点 | 通过 | 失败 | 跳过 | 结果 |",
        "|---|---|---|---:|---:|---:|---|",
    ]
    for result in functional:
        target = result["target"]
        summary = result["summary"]
        status = "PASS" if result["passed"] else "FAIL"
        lines.append(
            f'| {target["name"]} | {target["product"]} | {target["endpoint"]} | '
            f'{summary["passed"]} | {summary["failed"]} | {summary["skipped"]} | {status} |'
        )

    lines.extend([
        "",
        "## 性能采样",
        "",
        f'参数：每个阶段 {parameters["operations"]:,} 次操作，'
        f'{parameters["clients"]} 个客户端，value {parameters["value_size"]} 字节。',
        "",
        "| 目标 | SET ops/s | SET p50 ms | SET p95 ms | SET p99 ms | GET ops/s | GET p50 ms | GET p95 ms | GET p99 ms |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    for functional_result in functional:
        target = functional_result["target"]
        benchmark = benchmark_by_name.get(target["name"], {})
        lines.append(
            f'| {target["name"]} | {_metric(benchmark, "set", "throughput")} | '
            f'{_metric(benchmark, "set", "p50")} | {_metric(benchmark, "set", "p95")} | '
            f'{_metric(benchmark, "set", "p99")} | {_metric(benchmark, "get", "throughput")} | '
            f'{_metric(benchmark, "get", "p50")} | {_metric(benchmark, "get", "p95")} | '
            f'{_metric(benchmark, "get", "p99")} |'
        )

    lines.extend([
        "",
        "## 说明",
        "",
        "- Tidis 只执行双方共同支持的基础命令；Bitmap、HyperLogLog 和 Stream 在 Tidis 上记为跳过。",
        "- 测试键使用随机前缀并在测试后精确删除，不调用 FLUSHDB。仍应只对测试环境运行。",
        "- 性能结果包含 Python 客户端、线程调度和网络开销，只适合初步趋势比较。",
        "- 不同持久化、一致性、拓扑或硬件配置下的结果不能直接横向比较。",
        "",
    ])
    return "\n".join(lines)
