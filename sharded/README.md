# Sharded Setup (2 shards × 2 replicas)

ClickHouse cluster with Kafka ingestion, sharding by symbol, and replication for high availability.

## Architecture

```
┌────────────────────────────────────────────────────────────────────┐
│                        Docker Network (ch-net)                      │
│                                                                     │
│  ┌─────────────────┐              ┌─────────────────┐              │
│  │  Kafka (KRaft)  │              │     Keeper      │              │
│  │  :9092 / :9093  │              │      :9181      │              │
│  └────────┬────────┘              └────────┬────────┘              │
│           │                                │                        │
│           ▼                                ▼                        │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │                    cluster_2s_2r                            │   │
│  │                                                             │   │
│  │   Shard 1                    │         Shard 2              │   │
│  │   (BTC/USD, ETH/USD)         │         (DOGE/USD, SOL/USD)  │   │
│  │                              │                              │   │
│  │   ch-1a ◄──────► ch-1b      │         ch-2a ◄──────► ch-2b │   │
│  │   :8123    sync   :8124      │         :8125    sync  :8126 │   │
│  │                              │                              │   │
│  └─────────────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘

6 containers:
- Kafka (KRaft mode - no Zookeeper)
- ClickHouse Keeper (replication coordination)
- ch-1a, ch-1b (Shard 1 replicas)
- ch-2a, ch-2b (Shard 2 replicas)
```

## Data Flow

```
Kafka (trades topic)
        │
        ▼
trades_kafka (Kafka engine)
        │
        ▼ [trades_kafka_mv]
trades (Distributed) ─── routes by cityHash64(symbol)
        │
        ├──────────────────────────┐
        ▼                          ▼
trades_local (Shard 1)    trades_local (Shard 2)
        │                          │
        ▼ [ohlcv_1m_mv]           ▼ [ohlcv_1m_mv]
ohlcv_1m_local            ohlcv_1m_local
        │                          │
        └──────────┬───────────────┘
                   ▼
            ohlcv_1m (Distributed)
                   │
                   ▼
            ohlcv_view (readable output)
```

## Tables

| Table | Engine | Purpose |
|-------|--------|---------|
| `trades` | Distributed | Query/insert interface - routes to shards |
| `trades_local` | ReplicatedMergeTree | Actual tick data storage per shard |
| `trades_kafka` | Kafka | Consumes from Kafka topic |
| `trades_kafka_mv` | MaterializedView | Kafka → trades |
| `ohlcv_1m` | Distributed | Query interface for OHLCV bars |
| `ohlcv_1m_local` | ReplicatedAggregatingMergeTree | OHLCV storage per shard |
| `ohlcv_1m_mv` | MaterializedView | trades_local → ohlcv_1m_local |
| `ohlcv_view` | View | Merges aggregate states for readable output |

## Quick Start

```bash
# Start the cluster
docker compose up -d

# Wait for containers to be ready
sleep 10

# Initialize schema
docker exec -i ch-1a clickhouse-client < sql/init.sql

# Start producing test data
cd ../producer
source venv/bin/activate
python producer.py --rate 50 --count 1000
```

## Querying

```bash
# Connect to any node - distributed tables handle routing
docker exec -it ch-1a clickhouse-client

# Count all trades (queries all shards automatically)
SELECT count() FROM trades;

# Trades per symbol
SELECT symbol, count() FROM trades GROUP BY symbol;

# OHLCV bars (human-readable)
SELECT * FROM ohlcv_view ORDER BY bucket DESC LIMIT 10;

# Check shard distribution
SELECT
    hostName() AS node,
    count() AS trades
FROM clusterAllReplicas('cluster_2s_2r', default, trades_local)
GROUP BY node;
```

## Connection Details

| Service | HTTP Port | Native Port | Description |
|---------|-----------|-------------|-------------|
| ch-1a | 8123 | 9000 | Shard 1, Replica A |
| ch-1b | 8124 | 9001 | Shard 1, Replica B |
| ch-2a | 8125 | 9002 | Shard 2, Replica A |
| ch-2b | 8126 | 9003 | Shard 2, Replica B |
| Kafka | - | 9093 | External producer access |
| Keeper | - | 9181 | ClickHouse coordination |

## Sharding

Data is distributed across shards using `cityHash64(symbol)`:

```sql
ENGINE = Distributed('cluster_2s_2r', 'default', 'trades_local', cityHash64(symbol))
```

- All trades for a symbol go to the same shard
- New symbols are pseudo-randomly assigned
- Over many symbols, load balances naturally

**You don't need to know which shard has data** - query the Distributed table (`trades`, `ohlcv_1m`) and ClickHouse handles routing.

## Replication

Each shard has 2 replicas with automatic sync:
- Write to any replica → automatically synced to the other
- Read from any replica → same data
- If one replica fails → other continues serving

## Files

```
sharded/
├── docker-compose.yml      # 6-container stack
├── config/
│   ├── keeper.xml          # Keeper config
│   ├── ch-1a.xml           # Shard 1, Replica A
│   ├── ch-1b.xml           # Shard 1, Replica B
│   ├── ch-2a.xml           # Shard 2, Replica A
│   ├── ch-2b.xml           # Shard 2, Replica B
│   └── users.xml           # User permissions
└── sql/
    └── init.sql            # Schema definitions
```

## Useful Commands

```bash
# Check cluster status
docker exec ch-1a clickhouse-client -q "SELECT * FROM system.clusters WHERE cluster='cluster_2s_2r'"

# Check replication status
docker exec ch-1a clickhouse-client -q "SELECT database, table, is_leader, total_replicas, active_replicas FROM system.replicas"

# Check Kafka consumer lag
docker exec ch-1a clickhouse-client -q "SELECT * FROM system.kafka_consumers FORMAT Vertical"

# Produce more test data
python producer.py --rate 100 --count 10000

# Stop everything
docker compose down

# Stop and remove all data
docker compose down -v
```

## Experiments to Try

1. **Failover test**: Stop ch-1a, query from ch-1b
2. **Shard locality**: Query `trades_local` on different nodes
3. **Add more symbols**: See how they distribute across shards
4. **Scale ingestion**: Increase producer rate, watch throughput
5. **Add time-based rollups**: Create 5m, 1h OHLCV tables
