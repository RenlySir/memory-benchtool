# memory-benchtool 规模与兼容性测试手册

## 1. 目的和范围

本手册用于在相互独立的测试服务器上，对 Redis 和 Tidis 执行可重复的造数、读、写、读写混合及命令兼容性测试。

本轮约定如下：

- 用户需求中的 `hist` 按前文约定解释为 `List`。
- 数据结构为 List、Hash、Set。
- 数据规模为 100 万、500 万、1000 万行，不包含 1 亿行。
- 并发客户端数为 128、256、512。
- 负载模式为只读、只写、50% 读 + 50% 写。
- 每个性能场景预热 2 秒、测量 10 秒、重复 3 轮，汇总时取中位数。
- value/member 大小固定为 128 字节；每个容器 key 放 1000 行。

总场景数为：2 个产品 × 3 种结构 × 3 个数据量 × 3 个并发 × 3 种模式 = 162 个场景。每个场景 3 轮，共 486 次正式测量。

## 2. 与 redis-benchmark 的关系

工具参考 `redis-benchmark` 的并发客户端、预热、定时压测、吞吐和延迟百分位方法，但针对本项目增加以下能力：

- 先建立指定行数的 List、Hash、Set 基础数据集，再在相同数据规模下压测。
- 每次读操作校验返回值，避免把错误响应计入成功吞吐。
- 写测试使用独立临时容器，测试结束后精确删除，不改变基础数据规模。
- 混合测试按 50:50 确定性分配读写请求，并分别统计读、写指标。
- 输出 mean、p50、p95、p99、max 延迟和 ops/s。
- 不使用 pipeline，每次操作都是一次独立 Redis 协议往返。
- 造数支持续跑，并在每批数据前检查磁盘和可用内存安全阈值。

因此结果用于 Redis 与 Tidis 在当前部署形态下的相对比较，不应直接等同于 C 语言实现的 `redis-benchmark` 极限值。

## 3. 数据模型

默认 `rows-per-key=1000`，数据 key 采用以下格式：

```text
memory-benchtool-dataset:<target>:<dataset-id>:<structure>:<shard>
```

| 结构 | 一行的定义 | 造数命令 | 读命令 | 写压测命令 |
|---|---|---|---|---|
| List | 一个 list element | RPUSH | LINDEX | RPUSH 到临时 list |
| Hash | 一个 field/value | HSET | HGET | HSET 到临时 hash |
| Set | 一个唯一 member | SADD | SISMEMBER | SADD 到临时 set |

1000 万行对应 1 万个容器 key。Hash 的 field 在容器内唯一；Set member 在整个数据集内唯一；List 以 element 数量计行。造数完成后校验每个分片的 cardinality，运行负载前再抽查首、中、末三个分片。

100 万到 500 万、再到 1000 万使用同一 `dataset-id` 增量扩容。已存在且 cardinality 正确的分片会跳过，因此中断后可以重复执行同一造数命令续跑。

## 4. 测试环境

| 主机 | 产品 | 本地端点 | 工具目录 |
|---|---|---|---|
| 10.2.106.5 | Tidis | 127.0.0.1:6666 | /opt/memory-benchtool |
| 10.2.106.124 | Redis | 127.0.0.1:6379 | /opt/memory-benchtool |

两台服务器都在本机回环地址发压，避免开放数据库端口。此方式测量的是“客户端与数据库同机”的整体结果；正式容量选型还应使用独立负载机复测，以隔离客户端 CPU 竞争。

测试前检查：

```bash
uname -a
lscpu
free -h
df -h /
ss -lntp | grep -E ':6379|:6666'
```

测试期间不要运行备份、批量导入、系统更新等额外任务。Redis 和 Tidis 的持久化、副本、内存上限等配置必须记录到报告，不能只比较 ops/s。

## 5. 安装和配置

安装 wheel：

```bash
cd /opt/memory-benchtool
python3.9 -m venv .venv
.venv/bin/pip install --force-reinstall dist/memory_benchtool-0.3.0-py3-none-any.whl
.venv/bin/memory-benchtool --help
```

Tidis 主机的 `targets.local.json`：

```json
{
  "targets": [
    {"name": "tidis", "product": "tidis", "host": "127.0.0.1", "port": 6666}
  ]
}
```

Redis 主机的 `targets.local.json`：

```json
{
  "targets": [
    {"name": "redis", "product": "redis", "host": "127.0.0.1", "port": 6379}
  ]
}
```

## 6. 单步操作

以下示例在对应服务器的 `/opt/memory-benchtool` 下执行。

### 6.1 造数

以 List 100 万行为例：

```bash
.venv/bin/memory-benchtool seed \
  --config targets.local.json \
  --output-dir results/scale/list/1000000/seed \
  --structure list --rows 1000000 \
  --rows-per-key 1000 --value-size 128 \
  --dataset-id scale-20260817 --clients 32 \
  --min-free-disk-gib 8 --min-available-memory-gib 4
```

将 `--rows` 依次改为 `5000000` 和 `10000000` 即可增量扩容。Hash 和 Set 分别将 `--structure` 改为 `hash`、`set`。成功时 `seed.json` 中 `status` 为 `completed`，`rows_present` 等于请求行数。

### 6.2 只读测试

```bash
.venv/bin/memory-benchtool workload \
  --config targets.local.json \
  --output-dir results/scale/list/1000000/128/read/round-1 \
  --structure list --rows 1000000 \
  --rows-per-key 1000 --value-size 128 \
  --dataset-id scale-20260817 \
  --mode read --clients 128 \
  --warmup-seconds 2 --duration-seconds 10
```

### 6.3 只写测试

```bash
.venv/bin/memory-benchtool workload \
  --config targets.local.json \
  --output-dir results/scale/list/1000000/128/write/round-1 \
  --structure list --rows 1000000 \
  --rows-per-key 1000 --value-size 128 \
  --dataset-id scale-20260817 \
  --mode write --clients 128 \
  --warmup-seconds 2 --duration-seconds 10
```

写入发生在每个 worker 独立的临时容器中，完成后按精确 key 删除。基础数据集保持 100 万行，因此不同容量下的写入结果反映已有数据量对后台存储的影响，而不是持续扩充基础数据集。

### 6.4 读写混合测试

```bash
.venv/bin/memory-benchtool workload \
  --config targets.local.json \
  --output-dir results/scale/list/1000000/128/mixed/round-1 \
  --structure list --rows 1000000 \
  --rows-per-key 1000 --value-size 128 \
  --dataset-id scale-20260817 \
  --mode mixed --read-ratio 50 --clients 128 \
  --warmup-seconds 2 --duration-seconds 10
```

### 6.5 清理基础数据

```bash
.venv/bin/memory-benchtool cleanup-dataset \
  --config targets.local.json \
  --output-dir results/scale/list/cleanup \
  --structure list --rows 10000000 \
  --rows-per-key 1000 --value-size 128 \
  --dataset-id scale-20260817
```

工具不执行 `FLUSHDB`、`FLUSHALL` 或 `KEYS`。清理只删除能根据参数精确推导出的数据集 key 和元数据 key。

## 7. 一键执行完整矩阵

在 Tidis 和 Redis 主机上同时执行以下命令，可以并行完成两端测试：

```bash
cd /opt/memory-benchtool
TOOL_BIN=.venv/bin/memory-benchtool \
PYTHON_BIN=.venv/bin/python \
bash scripts/run-scale-matrix.sh \
  targets.local.json results/scale-20260817 scale-20260817
```

脚本默认执行 100 万、500 万、1000 万，128/256/512 并发，只读/只写/混合，3 轮。每种结构完成 1000 万档后自动清理，再进行下一种结构，降低磁盘和内存峰值。

常用覆盖参数：

```bash
ROUNDS=1 DURATION_SECONDS=5 KEEP_DATASETS=1 \
TOOL_BIN=.venv/bin/memory-benchtool PYTHON_BIN=.venv/bin/python \
bash scripts/run-scale-matrix.sh targets.local.json results/smoke smoke-20260817
```

- `KEEP_DATASETS=1`：完成后保留基础数据，默认自动清理。
- `ROUNDS`：每个场景轮数，正式测试使用 3。
- `DURATION_SECONDS`、`WARMUP_SECONDS`：正式测试分别使用 10 和 2。
- `MIN_FREE_DISK_GIB`、`MIN_AVAILABLE_MEMORY_GIB`：造数安全阈值。
- `ROW_TIERS`、`CLIENT_TIERS`、`STRUCTURES`、`MODES`：空格分隔的测试子集。

脚本每次都会重验或续建基础数据；只跳过状态为 `completed` 的负载结果。失败、不完整或不存在的场景会重新执行，因此完整清理后也能补跑缺失场景。

## 8. 结果汇总

将两台服务器的结果目录下载到同一机器后执行：

```bash
python3 scripts/summarize-scale-results.py \
  --input-dir collected/tidis \
  --input-dir collected/redis \
  --output-dir reports/scale-summary
```

输出：

- `scale-summary.json`：结构化汇总与异常明细。
- `scale-summary.csv`：便于电子表格分析的完整指标。
- `scale-summary.md`：各场景中位数表格。

每个 `workload.json` 保留 target、数据规模、结构、并发、模式、实际时长、操作数、吞吐，以及 overall/read/write 的 mean、p50、p95、p99、max 延迟。

## 9. 新增兼容性测试案例

先运行：

```bash
.venv/bin/memory-benchtool functional \
  --config targets.local.json \
  --output-dir results/compatibility
```

除原有 8 个共同案例外，本版新增以下 10 个案例。每个案例使用唯一 key，执行后自动清理。

| 编号 | 案例 | 操作步骤 | 预期结果 |
|---:|---|---|---|
| 1 | String 批量与条件写 | MSET 两个 key；MGET；对已有和新 key 分别 SETNX；STRLEN | MGET 值一致；已有 key SETNX=0，新 key=1；长度正确 |
| 2 | 数值增减 | SET 10；INCRBY 5；DECR；DECRBY 4 | 依次返回 15、14、10 |
| 3 | 类型与存在性 | 分别创建 List、Hash、Set；执行 TYPE 和 EXISTS | 类型分别为 list/hash/set，EXISTS 均为 1 |
| 4 | Hash 扩展 | HSET；HSETNX 已有/新 field；HMGET；HLEN；HEXISTS；HSTRLEN；HKEYS/HVALS | 新增数、字段值、长度和集合内容符合 Redis 语义 |
| 5 | List 队列与裁剪 | LPUSH；RPUSH；LLEN；LINDEX -1；LTRIM；RPOP；LRANGE | 长度、负索引、裁剪和弹出顺序正确 |
| 6 | List 插入与删除 | RPUSH a,b,a,c；LINSERT BEFORE；LREM 2 a；LRANGE | 删除两个 a，最终为 x,b,c |
| 7 | Set 扩展读取 | SADD；SMISMEMBER；SRANDMEMBER；SCARD | 成员布尔结果、随机成员范围、基数正确 |
| 8 | Set 弹出与删除 | SADD；SPOP；SISMEMBER；SCARD；SREM | 弹出成员消失，基数逐次减少 |
| 9 | Sorted Set 扩展 | ZADD；ZCARD；ZSCORE；ZCOUNT；ZRANGEBYSCORE；ZREM | 分值、范围顺序、计数和删除结果正确 |
| 10 | 容器过期 | 创建 Hash/List/Set；分别 EXPIRE、TTL、PERSIST、TTL | TTL 在 1-5 秒；PERSIST 后 TTL=-1 |

原有共同案例包括 String 与 TTL、实际过期删除、Hash、List、Set、Sorted Set、MULTI/EXEC、Lua。Redis 额外执行 Bitmap、HyperLogLog、Stream；Tidis 对这三项记为跳过。

## 10. 判读与报告要求

性能报告至少包含：

1. 主机 CPU、内存、磁盘和操作系统信息。
2. Redis/Tidis 版本、进程启动参数、持久化与副本配置。
3. 数据模型、value 大小、预热/测量时长、轮数和客户端位置。
4. 每个场景的 ops/s、p50、p95、p99，并明确取三轮中位数。
5. 同结构、同数据量、同并发、同模式下 Redis/Tidis 的吞吐比和延迟差异。
6. 失败、超时、容量保护中止和未完成场景，不能从汇总中静默删除。
7. 兼容性通过、失败、跳过数和每个失败的原始错误。

重点观察：并发从 128 增加到 512 后吞吐是否继续增长，p99 是否非线性恶化；数据量从 100 万增加到 1000 万后读延迟和写放大是否明显；混合负载是否出现读或写一侧的尾延迟突增。

## 11. 安全与故障恢复

- 仅在隔离测试实例运行，禁止在生产库直接执行。
- 默认保留至少 8 GiB 磁盘和 4 GiB 可用内存；低于阈值时造数返回 `capacity_limited`。
- 强制终止时临时写 key 可能来不及清理，可重跑对应场景后正常清理，或按结果中的 run id 精确定位。
- 不要通过模糊匹配批量删除未知 key。
- 若 512 并发出现连接或文件句柄错误，先记录失败，再检查 `ulimit -n`、服务端连接上限和系统日志；不要直接排除该结果。
- 两端应尽量同步开始，避免不同时间段的后台负载造成偏差。
