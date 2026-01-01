# ClickHouse Lab

Example ClickHouse configurations for learning cluster topologies, replication, and sharding.

## Setups

| Setup | Description | Containers |
|-------|-------------|------------|
| [sharded/](sharded/) | 2 shards × 2 replicas with Kafka ingestion | 6 |

## Quick Start

```bash
# Pick a setup
cd sharded/

# Start it
docker compose up -d

# Initialize schema
docker exec -i ch-1a clickhouse-client < sql/init.sql

# Send test data
cd ../producer
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python producer.py --rate 50 --count 500
```

## Producer

The `producer/` directory contains a shared test data generator that works with all setups. It simulates financial tick data (trades) and publishes to Kafka.

```bash
cd producer
python producer.py --help
```
