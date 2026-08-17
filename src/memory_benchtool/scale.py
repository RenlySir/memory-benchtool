from concurrent.futures import ThreadPoolExecutor
import math
import re
import shutil
import statistics
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from .benchmark import SUPPORTED_DATA_STRUCTURES, percentile
from .config import Target


DATASET_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class CapacityLimitError(RuntimeError):
    pass


def validate_dataset_parameters(data_structure: str, rows: int,
                                rows_per_key: int, value_size: int,
                                dataset_id: str) -> None:
    if data_structure not in SUPPORTED_DATA_STRUCTURES:
        raise ValueError(f"unsupported data structure: {data_structure}")
    if rows < 1 or rows_per_key < 1 or value_size < 8:
        raise ValueError("rows and rows-per-key must be positive; value-size must be at least 8")
    if not DATASET_ID_PATTERN.fullmatch(dataset_id):
        raise ValueError(
            "dataset-id may contain only letters, numbers, dot, underscore, and hyphen"
        )


def dataset_key(target: Target, dataset_id: str, data_structure: str,
                shard: int) -> str:
    return (
        f"memory-benchtool-dataset:{target.name}:{dataset_id}:"
        f"{data_structure}:{shard}"
    )


def dataset_meta_key(target: Target, dataset_id: str,
                     data_structure: str) -> str:
    return (
        f"memory-benchtool-dataset:{target.name}:{dataset_id}:"
        f"{data_structure}:meta"
    )


def _set_member(row_id: int, value_size: int) -> str:
    identity = f"{row_id:020d}"
    if value_size <= len(identity):
        return identity[-value_size:]
    return identity + ("x" * (value_size - len(identity)))


def _cardinality(client, data_structure: str, key: str) -> int:
    if data_structure == "list":
        return int(client.llen(key))
    if data_structure == "hash":
        return int(client.hlen(key))
    return int(client.scard(key))


def _seed_shard(target: Target, dataset_id: str, data_structure: str,
                shard: int, start_row: int, row_count: int,
                value_size: int) -> Tuple[str, int]:
    client = target.client()
    key = dataset_key(target, dataset_id, data_structure, shard)
    if client.exists(key):
        actual = _cardinality(client, data_structure, key)
        if actual != row_count:
            raise AssertionError(
                f"existing {data_structure} shard {shard} has {actual} rows, "
                f"expected {row_count}"
            )
        return "existing", row_count

    value = "x" * value_size
    if data_structure == "list":
        inserted = client.rpush(key, *([value] * row_count))
    elif data_structure == "hash":
        mapping = {str(offset): value for offset in range(row_count)}
        inserted = client.hset(key, mapping=mapping)
    else:
        members = [
            _set_member(start_row + offset, value_size)
            for offset in range(row_count)
        ]
        inserted = client.sadd(key, *members)

    if int(inserted) != row_count:
        raise AssertionError(
            f"{data_structure} shard {shard} inserted {inserted} rows, "
            f"expected {row_count}"
        )
    actual = _cardinality(client, data_structure, key)
    if actual != row_count:
        raise AssertionError(
            f"{data_structure} shard {shard} cardinality is {actual}, "
            f"expected {row_count}"
        )
    return "created", row_count


def capacity_snapshot(path: str = "/") -> Dict[str, Optional[float]]:
    disk = shutil.disk_usage(path)
    memory_available_gib = None
    try:
        with open("/proc/meminfo", encoding="ascii") as meminfo:
            for line in meminfo:
                if line.startswith("MemAvailable:"):
                    memory_available_gib = int(line.split()[1]) / (1024 ** 2)
                    break
    except OSError:
        pass
    return {
        "disk_free_gib": round(disk.free / (1024 ** 3), 3),
        "memory_available_gib": (
            round(memory_available_gib, 3)
            if memory_available_gib is not None else None
        ),
    }


def _check_capacity(min_free_disk_gib: float,
                    min_available_memory_gib: float) -> Dict[str, Optional[float]]:
    snapshot = capacity_snapshot()
    if snapshot["disk_free_gib"] < min_free_disk_gib:
        raise CapacityLimitError(
            f'disk free {snapshot["disk_free_gib"]} GiB is below '
            f"the {min_free_disk_gib} GiB safety threshold"
        )
    memory_available = snapshot["memory_available_gib"]
    if (memory_available is not None
            and memory_available < min_available_memory_gib):
        raise CapacityLimitError(
            f"available memory {memory_available} GiB is below "
            f"the {min_available_memory_gib} GiB safety threshold"
        )
    return snapshot


def seed_dataset(target: Target, data_structure: str, rows: int,
                 rows_per_key: int, value_size: int, dataset_id: str,
                 clients: int, min_free_disk_gib: float,
                 min_available_memory_gib: float) -> Dict[str, Any]:
    validate_dataset_parameters(
        data_structure, rows, rows_per_key, value_size, dataset_id
    )
    if clients < 1:
        raise ValueError("clients must be positive")
    if min_free_disk_gib < 0 or min_available_memory_gib < 0:
        raise ValueError("capacity safety thresholds cannot be negative")
    target.client().ping()
    total_shards = math.ceil(rows / rows_per_key)
    result: Dict[str, Any] = {
        "target": target.public_dict(),
        "dataset_id": dataset_id,
        "data_structure": data_structure,
        "requested_rows": rows,
        "rows_per_key": rows_per_key,
        "value_size_bytes": value_size,
        "clients": clients,
        "total_shards": total_shards,
        "created_shards": 0,
        "existing_shards": 0,
        "created_rows": 0,
        "existing_rows": 0,
        "rows_present": 0,
        "status": "running",
        "capacity_before": capacity_snapshot(),
    }
    started = time.perf_counter()
    batch_size = max(clients * 8, 1)

    try:
        _check_capacity(min_free_disk_gib, min_available_memory_gib)
        for batch_start in range(0, total_shards, batch_size):
            _check_capacity(min_free_disk_gib, min_available_memory_gib)
            batch_end = min(total_shards, batch_start + batch_size)
            with ThreadPoolExecutor(max_workers=clients) as executor:
                futures = []
                for shard in range(batch_start, batch_end):
                    start_row = shard * rows_per_key
                    row_count = min(rows_per_key, rows - start_row)
                    futures.append(executor.submit(
                        _seed_shard,
                        target,
                        dataset_id,
                        data_structure,
                        shard,
                        start_row,
                        row_count,
                        value_size,
                    ))
                for future in futures:
                    disposition, row_count = future.result()
                    result[f"{disposition}_shards"] += 1
                    result[f"{disposition}_rows"] += row_count
                    result["rows_present"] += row_count
        result["status"] = "completed"
    except CapacityLimitError as exc:
        result["status"] = "capacity_limited"
        result["error"] = str(exc)
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["duration_seconds"] = round(time.perf_counter() - started, 4)
        result["capacity_after"] = capacity_snapshot()
        if result["duration_seconds"] > 0:
            result["created_rows_per_second"] = round(
                result["created_rows"]
                / result["duration_seconds"],
                1,
            )
        meta_value = rows if result["status"] == "completed" else result["rows_present"]
        target.client().set(
            dataset_meta_key(target, dataset_id, data_structure),
            str(meta_value),
        )
    return result


def _verify_dataset_samples(client, target: Target, dataset_id: str,
                            data_structure: str, rows: int,
                            rows_per_key: int) -> List[Dict[str, int]]:
    total_shards = math.ceil(rows / rows_per_key)
    shard_ids = sorted({0, total_shards // 2, total_shards - 1})
    samples = []
    for shard in shard_ids:
        start_row = shard * rows_per_key
        expected = min(rows_per_key, rows - start_row)
        key = dataset_key(target, dataset_id, data_structure, shard)
        actual = _cardinality(client, data_structure, key)
        if actual != expected:
            raise AssertionError(
                f"dataset sample shard {shard} has {actual} rows, expected {expected}"
            )
        samples.append({"shard": shard, "expected_rows": expected,
                        "actual_rows": actual})
    return samples


def _read_row(client, target: Target, dataset_id: str,
              data_structure: str, row_id: int, rows_per_key: int,
              value_size: int) -> None:
    shard, offset = divmod(row_id, rows_per_key)
    key = dataset_key(target, dataset_id, data_structure, shard)
    if data_structure == "list":
        actual = client.lindex(key, offset)
        expected = "x" * value_size
        matched = actual == expected
    elif data_structure == "hash":
        actual = client.hget(key, str(offset))
        expected = "x" * value_size
        matched = actual == expected
    else:
        expected = _set_member(row_id, value_size)
        actual = client.sismember(key, expected)
        matched = bool(actual)
    if not matched:
        raise AssertionError(
            f"{data_structure} row {row_id} validation failed: {actual!r}"
        )


def _write_row(client, data_structure: str, key: str, worker_id: int,
               operation_index: int, value_size: int) -> None:
    value = "w" * value_size
    if data_structure == "list":
        result = client.rpush(key, value)
        expected = operation_index + 1
    elif data_structure == "hash":
        result = client.hset(key, str(operation_index), value)
        expected = 1
    else:
        member_id = worker_id * 1_000_000_000 + operation_index
        result = client.sadd(key, _set_member(member_id, value_size))
        expected = 1
    if int(result) != expected:
        raise AssertionError(
            f"{data_structure} workload write returned {result!r}, "
            f"expected {expected!r}"
        )


def _worker_phase(target: Target, dataset_id: str, data_structure: str,
                  rows: int, rows_per_key: int, value_size: int,
                  mode: str, read_ratio: int, worker_id: int,
                  run_id: str, start_event: threading.Event,
                  stop_event: threading.Event,
                  deadline: List[float]) -> Dict[str, List[float]]:
    client = target.client()
    read_latencies: List[float] = []
    write_latencies: List[float] = []
    write_key = (
        f"memory-benchtool-workload:{target.name}:{dataset_id}:"
        f"{data_structure}:{run_id}:{worker_id}"
    )
    operation_index = 0
    start_event.wait()
    try:
        while not stop_event.is_set() and time.perf_counter() < deadline[0]:
            if mode == "read":
                operation = "read"
            elif mode == "write":
                operation = "write"
            else:
                operation = (
                    "read"
                    if (operation_index + worker_id) % 100 < read_ratio
                    else "write"
                )

            started = time.perf_counter_ns()
            if operation == "read":
                row_id = (
                    worker_id * 104729 + operation_index * 8191
                ) % rows
                _read_row(
                    client,
                    target,
                    dataset_id,
                    data_structure,
                    row_id,
                    rows_per_key,
                    value_size,
                )
                read_latencies.append(
                    (time.perf_counter_ns() - started) / 1_000_000
                )
            else:
                _write_row(
                    client,
                    data_structure,
                    write_key,
                    worker_id,
                    len(write_latencies),
                    value_size,
                )
                write_latencies.append(
                    (time.perf_counter_ns() - started) / 1_000_000
                )
            operation_index += 1
    except Exception:
        stop_event.set()
        raise
    return {"read": read_latencies, "write": write_latencies}


def _latency_metrics(latencies: List[float], duration: float) -> Optional[Dict[str, Any]]:
    if not latencies:
        return None
    return {
        "operations": len(latencies),
        "throughput_ops_per_second": round(len(latencies) / duration, 1),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3),
            "p50": round(percentile(latencies, 0.50), 3),
            "p95": round(percentile(latencies, 0.95), 3),
            "p99": round(percentile(latencies, 0.99), 3),
            "max": round(max(latencies), 3),
        },
    }


def _cleanup_workload_keys(target: Target, dataset_id: str,
                           data_structure: str, run_id: str,
                           clients: int) -> int:
    keys = [
        f"memory-benchtool-workload:{target.name}:{dataset_id}:"
        f"{data_structure}:{run_id}:{worker_id}"
        for worker_id in range(clients)
    ]
    client = target.client()
    deleted = 0
    for start in range(0, len(keys), 100):
        deleted += int(client.delete(*keys[start:start + 100]))
    return deleted


def _timed_phase(target: Target, dataset_id: str, data_structure: str,
                 rows: int, rows_per_key: int, value_size: int,
                 mode: str, read_ratio: int, clients: int,
                 duration_seconds: float, run_id: str) -> Dict[str, Any]:
    start_event = threading.Event()
    stop_event = threading.Event()
    deadline = [0.0]
    started = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=clients) as executor:
            futures = [
                executor.submit(
                    _worker_phase,
                    target,
                    dataset_id,
                    data_structure,
                    rows,
                    rows_per_key,
                    value_size,
                    mode,
                    read_ratio,
                    worker_id,
                    run_id,
                    start_event,
                    stop_event,
                    deadline,
                )
                for worker_id in range(clients)
            ]
            started = time.perf_counter()
            deadline[0] = started + duration_seconds
            start_event.set()
            worker_results = [future.result() for future in futures]
        elapsed = time.perf_counter() - started
        read_latencies = [
            latency for item in worker_results for latency in item["read"]
        ]
        write_latencies = [
            latency for item in worker_results for latency in item["write"]
        ]
        all_latencies = read_latencies + write_latencies
        return {
            "duration_seconds": round(elapsed, 4),
            "operations": len(all_latencies),
            "throughput_ops_per_second": round(len(all_latencies) / elapsed, 1),
            "read": _latency_metrics(read_latencies, elapsed),
            "write": _latency_metrics(write_latencies, elapsed),
            "overall_latency_ms": (
                _latency_metrics(all_latencies, elapsed)["latency_ms"]
                if all_latencies else None
            ),
        }
    finally:
        start_event.set()


def run_scale_workload(target: Target, data_structure: str, rows: int,
                       rows_per_key: int, value_size: int, dataset_id: str,
                       mode: str, clients: int, duration_seconds: float,
                       warmup_seconds: float, read_ratio: int) -> Dict[str, Any]:
    validate_dataset_parameters(
        data_structure, rows, rows_per_key, value_size, dataset_id
    )
    if mode not in {"read", "write", "mixed"}:
        raise ValueError("mode must be read, write, or mixed")
    if not 0 <= read_ratio <= 100:
        raise ValueError("read-ratio must be between 0 and 100")
    if clients < 1 or duration_seconds <= 0 or warmup_seconds < 0:
        raise ValueError(
            "clients and duration-seconds must be positive; warmup-seconds cannot be negative"
        )

    client = target.client()
    client.ping()
    meta = client.get(dataset_meta_key(target, dataset_id, data_structure))
    present_rows = int(meta) if meta is not None else 0
    result: Dict[str, Any] = {
        "target": target.public_dict(),
        "dataset_id": dataset_id,
        "data_structure": data_structure,
        "dataset_rows": rows,
        "rows_per_key": rows_per_key,
        "value_size_bytes": value_size,
        "mode": mode,
        "read_ratio_percent": read_ratio if mode == "mixed" else (100 if mode == "read" else 0),
        "clients": clients,
        "requested_duration_seconds": duration_seconds,
        "warmup_seconds": warmup_seconds,
        "status": "running",
    }
    if present_rows < rows:
        result["status"] = "dataset_incomplete"
        result["error"] = (
            f"dataset contains {present_rows} rows, but {rows} rows are required"
        )
        return result

    try:
        result["dataset_samples"] = _verify_dataset_samples(
            client, target, dataset_id, data_structure, rows, rows_per_key
        )
    except Exception as exc:
        result["status"] = "dataset_invalid"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result

    run_id = uuid.uuid4().hex
    warmup_id = f"{run_id}-warmup"
    try:
        if warmup_seconds > 0:
            _timed_phase(
                target,
                dataset_id,
                data_structure,
                rows,
                rows_per_key,
                value_size,
                mode,
                read_ratio,
                clients,
                warmup_seconds,
                warmup_id,
            )
        result["measurement"] = _timed_phase(
            target,
            dataset_id,
            data_structure,
            rows,
            rows_per_key,
            value_size,
            mode,
            read_ratio,
            clients,
            duration_seconds,
            run_id,
        )
        result["status"] = "completed"
    except Exception as exc:
        result["status"] = "failed"
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["warmup_keys_deleted"] = _cleanup_workload_keys(
            target, dataset_id, data_structure, warmup_id, clients
        )
        result["measurement_keys_deleted"] = _cleanup_workload_keys(
            target, dataset_id, data_structure, run_id, clients
        )
    return result


def cleanup_dataset(target: Target, data_structure: str, rows: int,
                    rows_per_key: int, value_size: int,
                    dataset_id: str) -> Dict[str, Any]:
    validate_dataset_parameters(
        data_structure, rows, rows_per_key, value_size, dataset_id
    )
    total_shards = math.ceil(rows / rows_per_key)
    client = target.client()
    client.ping()
    started = time.perf_counter()
    deleted = 0
    for batch_start in range(0, total_shards, 20):
        keys = [
            dataset_key(target, dataset_id, data_structure, shard)
            for shard in range(batch_start, min(total_shards, batch_start + 20))
        ]
        deleted += int(client.delete(*keys))
    deleted_meta = int(client.delete(
        dataset_meta_key(target, dataset_id, data_structure)
    ))
    return {
        "target": target.public_dict(),
        "dataset_id": dataset_id,
        "data_structure": data_structure,
        "requested_rows": rows,
        "rows_per_key": rows_per_key,
        "deleted_shard_keys": deleted,
        "deleted_meta_keys": deleted_meta,
        "duration_seconds": round(time.perf_counter() - started, 4),
        "status": "completed",
    }
