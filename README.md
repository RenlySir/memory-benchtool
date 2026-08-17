# memory-benchtool

`memory-benchtool` 是面向 Redis 协议服务的独立测试工具，目前用于对 Redis 与 Tidis 执行：

- 基础命令兼容性测试
- Redis 专属能力测试
- SET/GET 吞吐与延迟采样
- 多目标结果汇总
- JSON 原始结果与 Markdown 报告生成

工具不会执行 `FLUSHDB`。每轮测试使用随机 key 前缀，并精确删除本轮创建的 key。仍应只在测试实例或隔离数据库上运行。

## 1. 环境要求

- Python 3.9 或更高版本
- 可访问待测试服务的 Redis 协议端口
- Redis 或 Tidis 测试实例

当前依赖 `redis-py >= 4.5, < 7`。

## 2. 安装

### 从 GitHub 安装

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools
.venv/bin/pip install git+https://github.com/RenlySir/memory-benchtool.git
.venv/bin/memory-benchtool --help
```

### 从源码安装

```bash
git clone https://github.com/RenlySir/memory-benchtool.git
cd memory-benchtool
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools
.venv/bin/pip install -e .
.venv/bin/memory-benchtool --help
```

CentOS 8 自带的 Python 3.6 版本过低，可以先安装 Python 3.9：

```bash
dnf install -y python39 python39-pip
python3.9 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools
```

## 3. 配置测试目标

复制示例配置：

```bash
cp examples/targets.example.json targets.local.json
```

配置格式：

```json
{
  "targets": [
    {
      "name": "redis-local",
      "product": "redis",
      "host": "127.0.0.1",
      "port": 6379,
      "db": 0,
      "tls": false,
      "password_env": "REDIS_PASSWORD"
    },
    {
      "name": "tidis-local",
      "product": "tidis",
      "host": "127.0.0.1",
      "port": 6666,
      "db": 0,
      "tls": false,
      "password_env": "TIDIS_PASSWORD"
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 说明 |
|---|---|---|
| `name` | 是 | 目标的唯一名称，写入报告和测试 key 前缀 |
| `product` | 是 | `redis` 或 `tidis`，决定是否执行 Redis 专属用例 |
| `host` | 是 | 服务地址 |
| `port` | 是 | Redis 协议端口，范围 1-65535 |
| `db` | 否 | Redis DB 编号，默认 0 |
| `tls` | 否 | 是否使用 TLS，默认 `false` |
| `password_env` | 否 | 保存密码的环境变量名称，不是密码本身 |

有认证时通过环境变量传入密码：

```bash
export REDIS_PASSWORD='replace-with-password'
export TIDIS_PASSWORD='replace-with-password'
```

密码不会写入 JSON 结果。不要直接把密码放入目标配置并提交到 Git。

## 4. 功能兼容测试

运行：

```bash
.venv/bin/memory-benchtool functional \
  --config targets.local.json \
  --output-dir results/functional
```

共同兼容用例：

| 用例 | 主要命令 |
|---|---|
| String 与 TTL | SET、GET、INCR、PEXPIRE、PTTL、PERSIST、TTL |
| 过期删除 | SET PX、EXISTS |
| Hash | HSET、HGET、HINCRBY、HGETALL、HDEL |
| List | RPUSH、LRANGE、LPOP、LSET |
| Set | SADD、SISMEMBER、SMEMBERS、SREM、SCARD |
| Sorted Set | ZADD、ZRANGE、ZINCRBY、ZRANK |
| 事务 | MULTI/EXEC、MGET |
| Lua | EVAL、脚本内 SET/GET |

当 `product` 为 `redis` 时，额外执行：

- Bitmap：SETBIT、GETBIT、BITCOUNT
- HyperLogLog：PFADD、PFCOUNT
- Stream：XADD、XRANGE

当 `product` 为 `tidis` 时，这 3 个能力记为跳过，不会误报为失败。

输出文件为 `functional.json`。每个 case 包含状态、耗时和失败错误。只要任一目标存在失败，命令退出码为 `1`。

## 5. 轻量性能测试

运行：

```bash
.venv/bin/memory-benchtool benchmark \
  --config targets.local.json \
  --operations 10000 \
  --clients 16 \
  --value-size 128 \
  --output-dir results/benchmark
```

参数说明：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--operations` | 5000 | 每个目标在每个阶段执行的操作总数 |
| `--clients` | 16 | 并发客户端线程数 |
| `--value-size` | 128 | SET value 字节数，内容为 ASCII `x` |

执行过程：

1. 对目标执行 PING。
2. 使用指定并发度执行 SET，每个请求单独往返。
3. 对同一批 key 执行 GET。
4. 计算吞吐、mean、p50、p95、p99 和 max 延迟。
5. 分批精确删除本轮创建的 key。

输出文件为 `benchmark.json`。

这不是极限容量测试：结果包含 Python、线程调度、客户端和网络开销。比较两个产品时，应保持机器规格、客户端位置、并发数、value 大小和持久化配置一致。

## 6. 一次运行全部测试并生成报告

```bash
.venv/bin/memory-benchtool all \
  --config targets.local.json \
  --operations 10000 \
  --clients 16 \
  --value-size 128 \
  --output-dir results/20260817
```

生成：

```text
results/20260817/
├── functional.json
├── benchmark.json
└── report.md
```

`report.md` 汇总每个目标的功能通过数、失败数、跳过数，以及 SET/GET 吞吐和 p50/p95/p99 延迟。

退出码：

| 退出码 | 含义 |
|---:|---|
| 0 | 所有功能和性能测试成功 |
| 1 | 存在功能失败或性能测试错误 |
| 2 | 配置文件、参数或文件访问错误 |

## 7. 两台服务器分别本地测试

当数据库端口只监听 `127.0.0.1` 时，应在两台服务器分别部署工具。本项目最初验证的拓扑是：

| 主机 | 产品 | 本地端点 |
|---|---|---|
| 10.2.106.5 | Tidis | 127.0.0.1:6666 |
| 10.2.106.124 | Redis | 127.0.0.1:6379 |

### Tidis 主机配置

`targets.local.json`：

```json
{
  "targets": [
    {"name": "tidis", "product": "tidis", "host": "127.0.0.1", "port": 6666}
  ]
}
```

运行：

```bash
.venv/bin/memory-benchtool all \
  --config targets.local.json \
  --operations 10000 --clients 16 --value-size 128 \
  --output-dir results/tidis
```

### Redis 主机配置

`targets.local.json`：

```json
{
  "targets": [
    {"name": "redis", "product": "redis", "host": "127.0.0.1", "port": 6379}
  ]
}
```

运行：

```bash
.venv/bin/memory-benchtool all \
  --config targets.local.json \
  --operations 10000 --clients 16 --value-size 128 \
  --output-dir results/redis
```

分别运行可以避免开放数据库端口，但两份 Markdown 报告不会自动合并。需要自动生成一份对比报告时，可以：

- 在一台能够访问两个端点的独立负载机上配置两个 target；或
- 通过 SSH 本地端口转发把两个服务映射到负载机，再运行一次双 target 测试。

SSH 转发示例：

```bash
ssh -N -L 16666:127.0.0.1:6666 root@10.2.106.5
ssh -N -L 16379:127.0.0.1:6379 root@10.2.106.124
```

负载机配置中使用 `127.0.0.1:16666` 和 `127.0.0.1:16379`。

## 8. 安全与数据清理

- 不调用 FLUSHDB、FLUSHALL 或 KEYS。
- 功能测试 key 前缀为 `memory-benchtool:<target>:<uuid>:`。
- 性能测试 key 前缀为 `memory-benchtool-benchmark:<target>:<uuid>:`。
- 清理时根据已生成的 key 精确执行 DEL，不依赖 SCAN MATCH 兼容性。
- 进程被强制终止时可能来不及清理。此时可根据上述前缀人工检查后删除。
- 不要对生产库直接进行高并发测试。

## 9. 常见问题

### Connection refused

检查目标服务、监听地址和端口：

```bash
ss -lntp | grep -E ':6379|:6666'
systemctl status redis-lab.service
systemctl status tidis-lab.service
```

### Authentication required

确认配置中的 `password_env` 名称正确，并且启动工具的同一 shell 已设置该环境变量。

### TLS 证书验证失败

当前版本使用 redis-py 默认 TLS 验证行为，尚未提供自定义 CA 文件参数。应将 CA 安装到系统信任库；不要在正式环境关闭证书验证。

### Tidis 跳过 3 个用例

这是预期行为。Bitmap、HyperLogLog 和 Stream 属于本工具中的 Redis 专属能力，不计入 Tidis 共同兼容用例失败数。

### 性能结果波动较大

先进行预热并重复运行多轮，同时检查 CPU、磁盘、网络和后台任务。正式评估建议使用专用负载机以及 `memtier_benchmark` 等成熟压测工具复核。

## 10. 开发与验证

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip setuptools
.venv/bin/pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m compileall -q src tests
```

项目采用 `src/` 布局，核心模块：

```text
src/memory_benchtool/
├── config.py       # 目标配置、连接创建和敏感信息隔离
├── functional.py   # 功能兼容用例
├── benchmark.py    # 并发 SET/GET 采样
├── report.py       # Markdown 报告生成
└── cli.py          # 命令行入口和结果文件管理
```

## 11. 已知边界

- 当前不是 Redis 完整命令一致性套件。
- 未测试 Pub/Sub、ACL、Cluster、Sentinel、主从切换或网络分区。
- 未测试 TiKV 多副本故障、Region 调度和 Tidis 多计算节点。
- 性能采样不包含 pipeline，也不模拟复杂业务 key 分布。
- 受控重启与故障注入测试需要在部署层单独执行。
