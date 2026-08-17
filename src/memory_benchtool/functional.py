import time
from typing import Any, Callable, Dict, List, Set
import uuid

from .config import Target


Case = Callable[[Any, Callable[[str], str]], None]


def case_string_and_expiration(client, key):
    name = key("string")
    assert client.set(name, "1") is True
    assert client.incr(name) == 2
    assert client.get(name) == "2"
    assert client.pexpire(name, 1500) is True
    assert client.pttl(name) > 0
    assert client.persist(name) is True
    assert client.ttl(name) == -1


def case_expiration_eventually_removes_key(client, key):
    name = key("expiry")
    client.set(name, "value", px=150)
    deadline = time.monotonic() + 2
    while client.exists(name) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not client.exists(name)


def case_hash(client, key):
    name = key("hash")
    assert client.hset(name, mapping={"a": "1", "b": "2"}) == 2
    assert client.hget(name, "a") == "1"
    assert client.hincrby(name, "a", 4) == 5
    assert client.hgetall(name) == {"a": "5", "b": "2"}
    assert client.hdel(name, "b") == 1


def case_list(client, key):
    name = key("list")
    assert client.rpush(name, "a", "b", "c") == 3
    assert client.lrange(name, 0, -1) == ["a", "b", "c"]
    assert client.lpop(name) == "a"
    assert client.lset(name, 0, "B") is True
    assert client.lrange(name, 0, -1) == ["B", "c"]


def case_set(client, key):
    name = key("set")
    assert client.sadd(name, "a", "b", "c") == 3
    assert bool(client.sismember(name, "b"))
    assert client.smembers(name) == {"a", "b", "c"}
    assert client.srem(name, "b") == 1
    assert client.scard(name) == 2


def case_sorted_set(client, key):
    name = key("zset")
    assert client.zadd(name, {"a": 1, "b": 2, "c": 3}) == 3
    assert client.zrange(name, 0, -1, withscores=True) == [
        ("a", 1.0), ("b", 2.0), ("c", 3.0)
    ]
    assert client.zincrby(name, 3, "a") == 4.0
    assert client.zrank(name, "b") == 0


def case_multi_exec(client, key):
    first = key("txn:first")
    second = key("txn:second")
    pipeline = client.pipeline(transaction=True)
    pipeline.set(first, "one")
    pipeline.set(second, "two")
    assert pipeline.execute() == [True, True]
    assert client.mget(first, second) == ["one", "two"]


def case_lua(client, key):
    name = key("lua")
    result = client.eval(
        "redis.call('set', KEYS[1], ARGV[1]); return redis.call('get', KEYS[1])",
        1,
        name,
        "lua-value",
    )
    assert result == "lua-value"


def case_bitmap(client, key):
    name = key("bitmap")
    assert client.setbit(name, 10, 1) == 0
    assert client.getbit(name, 10) == 1
    assert client.bitcount(name) == 1


def case_hyperloglog(client, key):
    name = key("hll")
    assert client.pfadd(name, "a", "b", "c") == 1
    assert client.pfcount(name) == 3


def case_stream(client, key):
    name = key("stream")
    entry_id = client.xadd(name, {"event": "created"})
    assert client.xrange(name) == [(entry_id, {"event": "created"})]


DATA_STRUCTURE_CASES: List[tuple[str, Case]] = [
    ("hash", case_hash),
    ("list", case_list),
    ("set", case_set),
]

COMMON_CASES: List[tuple[str, Case]] = [
    ("string_and_expiration", case_string_and_expiration),
    ("expiration_eventually_removes_key", case_expiration_eventually_removes_key),
    *DATA_STRUCTURE_CASES,
    ("sorted_set", case_sorted_set),
    ("multi_exec", case_multi_exec),
    ("lua", case_lua),
]

REDIS_NATIVE_CASES: List[tuple[str, Case]] = [
    ("bitmap", case_bitmap),
    ("hyperloglog", case_hyperloglog),
    ("stream", case_stream),
]


def run_functional(target: Target) -> Dict[str, Any]:
    client = target.client()
    prefix = f"memory-benchtool:{target.name}:{uuid.uuid4().hex}"
    created_keys: Set[str] = set()

    def key(name: str) -> str:
        value = f"{prefix}:{name}"
        created_keys.add(value)
        return value

    results = []
    cases = COMMON_CASES + (REDIS_NATIVE_CASES if target.product == "redis" else [])
    started = time.monotonic()
    try:
        client.ping()
        for name, case in cases:
            case_started = time.monotonic()
            try:
                case(client, key)
                results.append({"name": name, "status": "passed",
                                "duration_seconds": round(time.monotonic() - case_started, 4)})
            except Exception as exc:  # The report must preserve failures from remote products.
                detail = str(exc) or "result did not match expected Redis semantics"
                results.append({"name": name, "status": "failed",
                                "duration_seconds": round(time.monotonic() - case_started, 4),
                                "error": f"{type(exc).__name__}: {detail}"})
    except Exception as exc:
        results.append({"name": "connect", "status": "failed",
                        "duration_seconds": round(time.monotonic() - started, 4),
                        "error": f"{type(exc).__name__}: {exc}"})
    finally:
        if created_keys:
            try:
                client.delete(*created_keys)
            except Exception:
                pass

    passed = sum(result["status"] == "passed" for result in results)
    failed = sum(result["status"] == "failed" for result in results)
    skipped = len(REDIS_NATIVE_CASES) if target.product == "tidis" else 0
    return {
        "target": target.public_dict(),
        "passed": failed == 0,
        "summary": {"passed": passed, "failed": failed, "skipped": skipped},
        "duration_seconds": round(time.monotonic() - started, 4),
        "cases": results,
    }
