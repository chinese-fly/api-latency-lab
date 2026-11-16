# api-latency-lab

[English](README.md) | 简体中文

一个带 N+1 查询问题的列表接口、消除该问题的重写方案，以及一个把两者都测出来的压测脚本 —— 让结论是数字，而不是感觉。

```bash
python bench/bench.py --threads 8 --requests 40
python -m unittest      # 7 tests
```

零依赖。Python 3.10+。

---

## 实测

```
queries per request : slow=51  fast=1
load: 8 threads x 40 requests

endpoint         reqs      qps     p50     p95     p99     max
--------------------------------------------------------------
/orders/slow      320    381.3    21.4    34.0    39.0    44.3
/orders/fast      320    814.5     9.6    17.6    20.8    47.4

p95 speedup: 1.9x
```

**请带着怀疑看待这个 1.9 倍 —— 它其实低估了真正的问题。** SQLite 是进程内运行的，一次查询只花几微秒。51 次查询在生产环境里致命的原因不是 CPU，而是每一次都是一个网络往返：按 0.5ms 的局域网延迟算，51 次查询在任何计算开始之前就凭空多出 25ms 的纯等待；如果数据库在远端、延迟 1ms，那就是 51ms。同样的重写放到网络化的 Postgres 上，p95 会变化一个数量级，而不是两倍。

这个仓库诚实地能主张的是：**51 次往返变成了 1 次**。这个数字不依赖于数据库是否在本地。

---

## 问题代码

```python
rows = conn.execute("SELECT id, customer_id, total_cents, status FROM orders "
                    "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

for r in rows:                                    # 50 rows
    cust = conn.execute(                          # 50 more queries
        "SELECT name, tier FROM customers WHERE id = ?", (r[1],)
    ).fetchone()
```

它是对的。测试能过。开发环境里十行数据时毫无问题。它就这么上线了，一直撑到并发大概 150 用户，此时 p99 飙到四秒，曲线看起来像一道悬崖，且找不到明显原因。

线索（如果你知道该往哪看）是：延迟随**分页大小**增长，而不是随**数据总量**增长。50 行的一页慢，10 行的一页快 —— 这没法用任何索引调优来解释。

## 修复

```sql
SELECT o.id, c.name, c.tier, o.total_cents, o.status
FROM orders o JOIN customers c ON c.id = o.customer_id
ORDER BY o.id DESC LIMIT ?
```

一次查询，一次往返。join 走的是有索引的主键，所以查询计划器在内存里做 50 次索引查找，而不是在网络上跑 50 个来回。

### 缓存，以及为什么范围划在这里

`TierCache` 缓存客户等级，TTL 30 秒。等级极少变化、但每一行都要读，所以它是唯一值得缓存的字段。

它刻意**不**缓存订单行。订单一直在变，而过期的订单列表等于一张附带退款的客服工单。这个区分比缓存本身更重要：「加个缓存」这个建议不加区分地套用，就是你用一个慢系统换一个错系统的方式。

`test_parity.py` 把这一点钉死了 —— `fast_list_orders` 返回的 payload 必须 `assertEqual` 于 `slow_list_orders` 的返回，同时查询计数测试断言 51 对 1。如果有人把 join「优化」成会丢行的写法，测试会在部署之前失败。

---

## 目录结构

```
app/
  schema.py      DDL + seed (2k customers, 20k orders)
  repository.py  slow_list_orders / fast_list_orders / TierCache
  server.py      threaded stdlib HTTP layer, per-thread connections
bench/
  bench.py       concurrent load, p50/p95/p99, query counting
tests/
  test_parity.py payload equality, query counts, cache behaviour
```

服务端用的是标准库 `ThreadingHTTPServer`，不是 FastAPI。这是刻意的：本仓库的主角是查询模式，上框架会引入三个依赖，同时把要测量的东西藏起来。

查询计数用的是 SQLite 的 `set_trace_callback`。51 对 1 这个数字是这么「测」出来的，不是「断言」出来的 —— `bench.py` 每次运行都会把它打印在顶部。

---

## 怎么应用到真实代码库

真正有效的操作顺序：

1. **先数每个请求的查询次数。** Django Debug Toolbar、SQLAlchemy 的事件钩子，或者 `pg_stat_statements`。没有计数你就是在猜，而「猜」正是人们去「优化」一个本来就正常的接口的原因。
2. **找随分页大小增长的延迟。** 那是 N+1 的特征。随总行数增长的延迟是缺索引，是另一种修法。
3. **在重写之前写等价性测试。** 不是之后。这是让「查询数降到 1/51」这件事可以安全合并的唯一保障。
4. **只缓存读多写少的东西。** 对这个接口来说是等级，不是订单。

## 已知限制

- **是 SQLite，不是 Postgres。** 因为上面说的原因，1.9 倍低估了生产影响。这个仓库能可靠测量的是查询次数；墙钟时间的收益只对进程内数据库成立。
- **没有连接池。** 每线程一个连接，懒创建。并发再高就需要真正的连接池，数字也会变。
- **单机。** 没有模拟网络延迟。加人为延迟会让数字更有代表性，但也更接近编造，所以选择不加，而是写清楚原因。
