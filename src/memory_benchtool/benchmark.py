from concurrent.futures import ThreadPoolExecutor
import math
import statistics
import time
from typing import Any, Dict, List
import uuid

from .config import Target


SUPPORTED_DATA_STRUCTURES = ("list", "hash", "set")
DATA_STRUCTURE_COMMANDS = {
    "list": {"write": "RPUSH", "read": "LINDEX"},
    "hash": {"write": "HSET", "read": "HGET"},
    "set": {"write": "SADD", "read": "SISMEMBER"},
}


def percentile(values: List[float], percent: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * percent) - 1)
    return ordered[index]


def _counts(operations: int, clients: int) -> List[int]:
    base, remainder = divmod(operations, clients)
    return [base + (1 if index < remainder else 0) for index in range(clients)]


def _write(connection, data_structure: str, key: str, value: str) -> None:
    if data_structure == "string":
        result = connection.set(key, value)
        expected = True
    elif data_structure == "list":
        result = connection.rpush(key, value)
        expected = 1
    elif data_structure == "hash":
        result = connection.hset(key, "field", value)
        expected = 1
    elif data_structure == "set":
        result = connection.sadd(key, value)
        expected = 1
    else:
        raise ValueError(f"unsupported data structure: {data_structure}")
    if result != expected:
        raise AssertionError(
            f"{data_structure} write returned {result!r}, expected {expected!r}"
        )


def _read(connection, data_structure: str, key: str, value: str) -> None:
    if data_structure == "string":
        result = connection.get(key)
        matched = result == value
    elif data_structure == "list":
        result = connection.lindex(key, 0)
        matched = result == value
    elif data_structure == "hash":
        result = connection.hget(key, "field")
        matched = result == value
    elif data_structure == "set":
        result = connection.sismember(key, value)
        matched = bool(result)
    else:
        raise ValueError(f"unsupported data structure: {data_structure}")
    if not matched:
        raise AssertionError(
            f"{data_structure} read validation failed for {key}: {result!r}"
        )


def _worker(target: Target, data_structure: str, operation: str, worker_id: int,
            count: int, prefix: str, value: str) -> List[float]:
    connection = target.client()
    latencies = []
    for index in range(count):
        key = f"{prefix}:{worker_id}:{index}"
        started = time.perf_counter_ns()
        if operation == "write":
            _write(connection, data_structure, key, value)
        else:
            _read(connection, data_structure, key, value)
        latencies.append((time.perf_counter_ns() - started) / 1_000_000)
    return latencies


def _phase(target: Target, data_structure: str, operation: str, operations: int,
           clients: int, prefix: str, value: str) -> Dict[str, Any]:
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=clients) as executor:
        futures = [
            executor.submit(
                _worker,
                target,
                data_structure,
                operation,
                worker_id,
                count,
                prefix,
                value,
            )
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
        result["set"] = _phase(
            target, "string", "write", operations, clients, prefix, value
        )
        result["get"] = _phase(
            target, "string", "read", operations, clients, prefix, value
        )
    finally:
        result["keys_deleted"] = _cleanup(target, operations, clients, prefix)
    return result


def _run_data_structure_benchmark(target: Target, data_structure: str,
                                  operations: int, clients: int,
                                  value_size: int) -> Dict[str, Any]:
    commands = DATA_STRUCTURE_COMMANDS[data_structure]
    prefix = (
        f"memory-benchtool-structures:{target.name}:{data_structure}:"
        f"{uuid.uuid4().hex}"
    )
    value = "x" * value_size
    result = {
        "write_command": commands["write"],
        "read_command": commands["read"],
    }
    try:
        result["write"] = _phase(
            target, data_structure, "write", operations, clients, prefix, value
        )
        result["read"] = _phase(
            target, data_structure, "read", operations, clients, prefix, value
        )
    finally:
        result["keys_deleted"] = _cleanup(target, operations, clients, prefix)
    return result


def run_data_structure_benchmarks(target: Target, operations: int, clients: int,
                                  value_size: int,
                                  data_structures: List[str]) -> Dict[str, Any]:
    unsupported = sorted(set(data_structures) - set(SUPPORTED_DATA_STRUCTURES))
    if unsupported:
        raise ValueError(
            f"unsupported data structures: {', '.join(unsupported)}"
        )

    connection = target.client()
    connection.ping()
    result = {
        "target": target.public_dict(),
        "clients": clients,
        "value_size_bytes": value_size,
        "operations_per_phase": operations,
        "structures": {},
    }
    for data_structure in data_structures:
        try:
            result["structures"][data_structure] = _run_data_structure_benchmark(
                target, data_structure, operations, clients, value_size
            )
        except Exception as exc:
            commands = DATA_STRUCTURE_COMMANDS[data_structure]
            result["structures"][data_structure] = {
                "write_command": commands["write"],
                "read_command": commands["read"],
                "error": f"{type(exc).__name__}: {exc}",
            }
    return result
