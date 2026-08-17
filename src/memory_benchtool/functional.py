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


def case_string_batch_and_conditions(client, key):
    first = key("string-batch:first")
    second = key("string-batch:second")
    assert client.mset({first: "alpha", second: "beta"}) is True
    assert client.mget(first, second) == ["alpha", "beta"]
    assert not client.setnx(first, "replacement")
    assert client.setnx(key("string-batch:new"), "created")
    assert client.strlen(first) == 5


def case_numeric_operations(client, key):
    name = key("numeric")
    assert client.set(name, "10") is True
    assert client.incrby(name, 5) == 15
    assert client.decr(name) == 14
    assert client.decrby(name, 4) == 10


def case_key_types_and_exists(client, key):
    list_key = key("types:list")
    hash_key = key("types:hash")
    set_key = key("types:set")
    client.rpush(list_key, "value")
    client.hset(hash_key, "field", "value")
    client.sadd(set_key, "member")
    assert client.type(list_key) == "list"
    assert client.type(hash_key) == "hash"
    assert client.type(set_key) == "set"
    assert all(client.exists(name) == 1 for name in (list_key, hash_key, set_key))


def case_hash_extended(client, key):
    name = key("hash-extended")
    assert client.hset(name, mapping={"a": "one", "b": "two"}) == 2
    assert client.hsetnx(name, "a", "replaced") == 0
    assert client.hsetnx(name, "c", "three") == 1
    assert client.hmget(name, "a", "c") == ["one", "three"]
    assert client.hlen(name) == 3
    assert bool(client.hexists(name, "b"))
    assert client.hstrlen(name, "c") == 5
    assert set(client.hkeys(name)) == {"a", "b", "c"}
    assert set(client.hvals(name)) == {"one", "two", "three"}


def case_list_queue_and_trim(client, key):
    name = key("list-queue")
    assert client.lpush(name, "b", "a") == 2
    assert client.rpush(name, "c", "d") == 4
    assert client.llen(name) == 4
    assert client.lindex(name, -1) == "d"
    assert client.ltrim(name, 1, 2) is True
    assert client.rpop(name) == "c"
    assert client.lrange(name, 0, -1) == ["b"]


def case_list_insert_and_remove(client, key):
    name = key("list-insert-remove")
    assert client.rpush(name, "a", "b", "a", "c") == 4
    assert client.linsert(name, "BEFORE", "b", "x") == 5
    assert client.lrem(name, 2, "a") == 2
    assert client.lrange(name, 0, -1) == ["x", "b", "c"]


def case_set_extended(client, key):
    name = key("set-extended")
    assert client.sadd(name, "a", "b", "c") == 3
    assert [bool(value) for value in client.smismember(name, ["a", "missing"])] == [
        True, False
    ]
    assert client.srandmember(name) in {"a", "b", "c"}
    assert client.scard(name) == 3


def case_set_pop_and_remove(client, key):
    name = key("set-pop-remove")
    assert client.sadd(name, "a", "b", "c") == 3
    removed = client.spop(name)
    assert removed in {"a", "b", "c"}
    assert not client.sismember(name, removed)
    assert client.scard(name) == 2
    remaining = next(iter(client.smembers(name)))
    assert client.srem(name, remaining) == 1
    assert client.scard(name) == 1


def case_sorted_set_extended(client, key):
    name = key("zset-extended")
    assert client.zadd(name, {"low": 1, "middle": 2, "high": 3}) == 3
    assert client.zcard(name) == 3
    assert client.zscore(name, "middle") == 2.0
    assert client.zcount(name, 1, 2) == 2
    assert client.zrangebyscore(name, 2, 3) == ["middle", "high"]
    assert client.zrem(name, "middle") == 1


def case_container_expiration(client, key):
    names = {
        "hash": key("container-expiration:hash"),
        "list": key("container-expiration:list"),
        "set": key("container-expiration:set"),
    }
    client.hset(names["hash"], "field", "value")
    client.rpush(names["list"], "value")
    client.sadd(names["set"], "value")
    for name in names.values():
        assert client.expire(name, 5)
        assert 0 < client.ttl(name) <= 5
        assert client.persist(name)
        assert client.ttl(name) == -1


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
    ("string_batch_and_conditions", case_string_batch_and_conditions),
    ("numeric_operations", case_numeric_operations),
    ("key_types_and_exists", case_key_types_and_exists),
    ("hash_extended", case_hash_extended),
    ("list_queue_and_trim", case_list_queue_and_trim),
    ("list_insert_and_remove", case_list_insert_and_remove),
    ("set_extended", case_set_extended),
    ("set_pop_and_remove", case_set_pop_and_remove),
    ("sorted_set_extended", case_sorted_set_extended),
    ("container_expiration", case_container_expiration),
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
