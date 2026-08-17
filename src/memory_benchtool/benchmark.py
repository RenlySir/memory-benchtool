from concurrent.futures import ThreadPoolExecutor
import math
import statistics
import time
from typing import Any, Dict, List
import uuid

from .config import Target


def percentile(values: List[float], percent: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * percent) - 1)
    return ordered[index]


def _counts(operations: int, clients: int) -> List[int]:
    base, remainder = divmod(operations, clients)
    return [base + (1 if index < remainder else 0) for index in range(clients)]


def _worker(target: Target, operation: str, worker_id: int, count: int,
            prefix: str, value: str) -> List[float]:
    connection = target.client()
    latencies = []
    for index in range(count):
        key = f"{prefix}:{worker_id}:{index}"
        started = time.perf_counter_ns()
        if operation == "set":
            connection.set(key, value)
        else:
            connection.get(key)
        latencies.append((time.perf_counter_ns() - started) / 1_000_000)
    return latencies


def _phase(target: Target, operation: str, operations: int, clients: int,
           prefix: str, value: str) -> Dict[str, Any]:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [
            executor.submit(_worker, target, operation, worker_id, count, prefix, value)
            for worker_id, count in enumerate(_counts(operations, clients))
        ]
    latencies = [latency for future in futures for latency in future.result()]
    duration = time.perf_counter() - started
    return {
        "operations": len(latencies),
        "duration_seconds": round(duration, 4),
        "throughput_ops_per_second": round(len(latencies) / duration, 1),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3),
            "p50": round(percentile(latencies, 0.50), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "p99": round(percentile(latencies, 0.99), 3),
            "max": round(max(latencies), 3),
        },
    }


def _cleanup(target: Target, operations: int, clients: int, prefix: str) -> int:
    connection = target.client()
    deleted = 0
    batch = []
    for worker_id, count in enumerate(_counts(operations, clients)):
        for index in range(count):
            batch.append(f"{prefix}:{worker_id}:{index}")
            if len(batch) == 1000:
                deleted += connection.delete(*batch)
                batch.clear()
    if batch:
        deleted += connection.delete(*batch)
    return deleted


def run_benchmark(target: Target, operations: int, clients: int,
                  value_size: int) -> Dict[str, Any]:
    connection = target.client()
    connection.ping()
    prefix = f"memory-benchtool-benchmark:{target.name}:{uuid.uuid4().hex}"
    value = "x" * value_size
    result = {
        "target": target.public_dict(),
        "clients": clients,
        "value_size_bytes": value_size,
    }
    try:
        result["set"] = _phase(target, "set", operations, clients, prefix, value)
        result["get"] = _phase(target, "get", operations, clients, prefix, value)
    finally:
        result["keys_deleted"] = _cleanup(target, operations, clients, prefix)
    return result
