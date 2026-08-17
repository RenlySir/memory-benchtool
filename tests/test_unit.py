import json
from pathlib import Path
import tempfile
import unittest

from memory_benchtool.benchmark import _counts, percentile
from memory_benchtool.config import Target, load_targets
from memory_benchtool.functional import case_set
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


class FunctionalTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
