import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from memory_benchtool.benchmark import (
    _counts,
    percentile,
    run_data_structure_benchmarks,
)
from memory_benchtool.cli import build_parser
from memory_benchtool.config import Target, load_targets
from memory_benchtool.functional import (
    COMMON_CASES,
    DATA_STRUCTURE_CASES,
    case_hash,
    case_list,
    case_set,
)
from memory_benchtool.report import render_report
from memory_benchtool.scale import (
    cleanup_dataset,
    run_scale_workload,
    seed_dataset,
    validate_dataset_parameters,
)


class ScaleClient:
    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()

    def ping(self):
        return True

    def exists(self, key):
        with self.lock:
            return int(key in self.data)

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
        return True

    def get(self, key):
        with self.lock:
            return self.data.get(key)

    def delete(self, *keys):
        with self.lock:
            deleted = sum(key in self.data for key in keys)
            for key in keys:
                self.data.pop(key, None)
        return deleted

    def rpush(self, key, *values):
        with self.lock:
            current = self.data.setdefault(key, [])
            current.extend(values)
            return len(current)

    def llen(self, key):
        with self.lock:
            return len(self.data.get(key, []))

    def lindex(self, key, index):
        with self.lock:
            return self.data[key][index]

    def hset(self, key, field=None, value=None, mapping=None):
        with self.lock:
            current = self.data.setdefault(key, {})
            values = mapping if mapping is not None else {field: value}
            created = sum(name not in current for name in values)
            current.update(values)
            return created

    def hlen(self, key):
        with self.lock:
            return len(self.data.get(key, {}))

    def hget(self, key, field):
        with self.lock:
            return self.data[key].get(field)

    def sadd(self, key, *members):
        with self.lock:
            current = self.data.setdefault(key, set())
            before = len(current)
            current.update(members)
            return len(current) - before

    def scard(self, key):
        with self.lock:
            return len(self.data.get(key, set()))

    def sismember(self, key, member):
        with self.lock:
            return int(member in self.data[key])


class ScaleTarget:
    name = "fake"

    def __init__(self):
        self.client_instance = ScaleClient()

    def client(self):
        return self.client_instance

    def public_dict(self):
        return {"name": self.name, "product": "redis", "endpoint": "fake:6379"}


class ConfigTests(unittest.TestCase):
    def test_load_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            path.write_text(json.dumps({"targets": [
                {"name": "redis", "product": "redis", "host": "localhost", "port": 6379}
            ]}), encoding="utf-8")
            targets = load_targets(path)
        self.assertEqual(targets, [Target("redis", "redis", "localhost", 6379)])

    def test_duplicate_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.json"
            target = {"name": "same", "product": "redis", "host": "localhost", "port": 6379}
            path.write_text(json.dumps({"targets": [target, target]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unique"):
                load_targets(path)


class BenchmarkTests(unittest.TestCase):
    def test_counts_preserve_operation_total(self):
        counts = _counts(10, 3)
        self.assertEqual(counts, [4, 3, 3])
        self.assertEqual(sum(counts), 10)

    def test_percentile_uses_nearest_rank(self):
        self.assertEqual(percentile([1, 2, 3, 4], 0.50), 2)
        self.assertEqual(percentile([1, 2, 3, 4], 0.99), 4)

    def test_list_hash_set_benchmarks_validate_reads_and_cleanup(self):
        class Client:
            def __init__(self):
                self.data = {}

            def ping(self):
                return True

            def rpush(self, key, value):
                self.data[key] = [value]
                return 1

            def lindex(self, key, index):
                return self.data[key][index]

            def hset(self, key, field, value):
                self.data[key] = {field: value}
                return 1

            def hget(self, key, field):
                return self.data[key][field]

            def sadd(self, key, value):
                self.data[key] = {value}
                return 1

            def sismember(self, key, value):
                return int(value in self.data[key])

            def delete(self, *keys):
                deleted = sum(key in self.data for key in keys)
                for key in keys:
                    self.data.pop(key, None)
                return deleted

        class FakeTarget:
            name = "fake"
            client_instance = Client()

            def client(self):
                return self.client_instance

            def public_dict(self):
                return {"name": self.name, "product": "redis", "endpoint": "fake:6379"}

        result = run_data_structure_benchmarks(
            FakeTarget(), 3, 1, 8, ["list", "hash", "set"]
        )

        self.assertEqual(list(result["structures"]), ["list", "hash", "set"])
        for name, commands in {
            "list": ("RPUSH", "LINDEX"),
            "hash": ("HSET", "HGET"),
            "set": ("SADD", "SISMEMBER"),
        }.items():
            metrics = result["structures"][name]
            self.assertEqual(
                (metrics["write_command"], metrics["read_command"]), commands
            )
            self.assertEqual(metrics["write"]["operations"], 3)
            self.assertEqual(metrics["read"]["operations"], 3)
            self.assertEqual(metrics["keys_deleted"], 3)
        self.assertEqual(FakeTarget.client_instance.data, {})

    def test_data_structure_read_mismatch_is_reported(self):
        class Client:
            def ping(self):
                return True

            def rpush(self, key, value):
                return 1

            def lindex(self, key, index):
                return "wrong-value"

            def delete(self, *keys):
                return len(keys)

        class FakeTarget:
            name = "fake"

            def client(self):
                return Client()

            def public_dict(self):
                return {"name": self.name, "product": "redis", "endpoint": "fake:6379"}

        result = run_data_structure_benchmarks(
            FakeTarget(), 1, 1, 8, ["list"]
        )
        self.assertIn("read validation failed", result["structures"]["list"]["error"])


class CliTests(unittest.TestCase):
    def test_structures_default_to_list_hash_set(self):
        args = build_parser().parse_args([
            "structures", "--config", "targets.json"
        ])
        self.assertEqual(args.structures, ["list", "hash", "set"])

    def test_structures_can_be_selected(self):
        args = build_parser().parse_args([
            "structures", "--config", "targets.json", "--structures", "hash,set"
        ])
        self.assertEqual(args.structures, ["hash", "set"])

    def test_scale_workload_defaults(self):
        args = build_parser().parse_args([
            "workload", "--config", "targets.json", "--structure", "hash",
            "--rows", "5000000", "--dataset-id", "scale-v1", "--mode", "mixed",
            "--clients", "256",
        ])
        self.assertEqual(args.rows_per_key, 1000)
        self.assertEqual(args.value_size, 128)
        self.assertEqual(args.read_ratio, 50)
        self.assertEqual(args.duration_seconds, 10.0)


class FunctionalTests(unittest.TestCase):
    def test_data_structure_group_contains_list_hash_set(self):
        self.assertEqual(
            [name for name, _ in DATA_STRUCTURE_CASES], ["hash", "list", "set"]
        )

    def test_ten_extended_compatibility_cases_are_registered(self):
        extended = {
            "string_batch_and_conditions", "numeric_operations",
            "key_types_and_exists", "hash_extended", "list_queue_and_trim",
            "list_insert_and_remove", "set_extended", "set_pop_and_remove",
            "sorted_set_extended", "container_expiration",
        }
        self.assertEqual(extended, {name for name, _ in COMMON_CASES} & extended)
        self.assertEqual(len(COMMON_CASES), 18)

    def test_hash_case(self):
        class Client:
            def hset(self, *args, **kwargs):
                return 2

            def hget(self, *args):
                return "1"

            def hincrby(self, *args):
                return 5

            def hgetall(self, *args):
                return {"a": "5", "b": "2"}

            def hdel(self, *args):
                return 1

        case_hash(Client(), lambda name: name)

    def test_list_case(self):
        class Client:
            ranges = iter([["a", "b", "c"], ["B", "c"]])

            def rpush(self, *args):
                return 3

            def lrange(self, *args):
                return next(self.ranges)

            def lpop(self, *args):
                return "a"

            def lset(self, *args):
                return True

        case_list(Client(), lambda name: name)

    def test_set_membership_accepts_integer_response(self):
        class Client:
            def sadd(self, *args):
                return 3

            def sismember(self, *args):
                return 1

            def smembers(self, *args):
                return {"a", "b", "c"}

            def srem(self, *args):
                return 1

            def scard(self, *args):
                return 2

        case_set(Client(), lambda name: name)


class ScaleTests(unittest.TestCase):
    def test_dataset_parameter_validation(self):
        validate_dataset_parameters("set", 10_000_000, 1000, 128, "scale-v1")
        with self.assertRaisesRegex(ValueError, "dataset-id"):
            validate_dataset_parameters("set", 1, 1, 8, "invalid id")

    @patch("memory_benchtool.scale.capacity_snapshot")
    def test_seed_resume_workload_and_cleanup_for_all_structures(self, snapshot):
        snapshot.return_value = {
            "disk_free_gib": 100.0, "memory_available_gib": 100.0
        }
        for structure in ("list", "hash", "set"):
            target = ScaleTarget()
            seed = seed_dataset(
                target, structure, 5, 2, 20, "scale-v1", 2, 1, 1
            )
            self.assertEqual(seed["status"], "completed")
            self.assertEqual(seed["created_rows"], 5)
            resumed = seed_dataset(
                target, structure, 5, 2, 20, "scale-v1", 2, 1, 1
            )
            self.assertEqual(resumed["existing_rows"], 5)

            result = run_scale_workload(
                target, structure, 5, 2, 20, "scale-v1", "mixed", 2,
                0.01, 0, 50,
            )
            self.assertEqual(result["status"], "completed")
            self.assertGreater(result["measurement"]["operations"], 0)
            self.assertIsNotNone(result["measurement"]["read"])
            self.assertIsNotNone(result["measurement"]["write"])

            cleanup = cleanup_dataset(
                target, structure, 5, 2, 20, "scale-v1"
            )
            self.assertEqual(cleanup["deleted_shard_keys"], 3)
            self.assertEqual(target.client_instance.data, {})


class ReportTests(unittest.TestCase):
    def test_report_contains_target_and_metrics(self):
        functional = [{
            "target": {"name": "redis", "product": "redis", "endpoint": "localhost:6379"},
            "passed": True,
            "summary": {"passed": 11, "failed": 0, "skipped": 0},
        }]
        benchmark = [{
            "target": functional[0]["target"],
            "set": {"throughput_ops_per_second": 100.0,
                    "latency_ms": {"p50": 1.0, "p95": 2.0, "p99": 3.0}},
            "get": {"throughput_ops_per_second": 200.0,
                    "latency_ms": {"p50": 0.5, "p95": 1.0, "p99": 2.0}},
        }]
        report = render_report(functional, benchmark,
                               {"operations": 100, "clients": 2, "value_size": 16})
        self.assertIn("redis", report)
        self.assertIn("100.0", report)

    def test_report_contains_data_structure_metrics(self):
        target = {"name": "redis", "product": "redis", "endpoint": "localhost:6379"}
        phase = {
            "throughput_ops_per_second": 100.0,
            "latency_ms": {"p50": 1.0, "p95": 2.0, "p99": 3.0},
        }
        structures = [{
            "target": target,
            "structures": {
                "list": {
                    "write_command": "RPUSH",
                    "read_command": "LINDEX",
                    "write": phase,
                    "read": phase,
                    "keys_deleted": 10,
                }
            },
        }]
        report = render_report(
            [],
            [],
            {"operations": 10, "clients": 1, "value_size": 8},
            structures,
        )
        self.assertIn("List、Hash、Set 性能采样", report)
        self.assertIn("RPUSH", report)
        self.assertIn("LINDEX", report)


if __name__ == "__main__":
    unittest.main()
