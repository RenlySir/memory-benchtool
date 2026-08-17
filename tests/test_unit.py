import json
from pathlib import Path
import tempfile
import unittest

from memory_benchtool.benchmark import (
    _counts,
    percentile,
    run_data_structure_benchmarks,
)
from memory_benchtool.cli import build_parser
from memory_benchtool.config import Target, load_targets
from memory_benchtool.functional import (
    DATA_STRUCTURE_CASES,
    case_hash,
    case_list,
    case_set,
)
from memory_benchtool.report import render_report


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


class FunctionalTests(unittest.TestCase):
    def test_data_structure_group_contains_list_hash_set(self):
        self.assertEqual(
            [name for name, _ in DATA_STRUCTURE_CASES], ["hash", "list", "set"]
        )

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
