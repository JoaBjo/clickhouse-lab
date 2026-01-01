-- Local trades table (on each shard, replicated within shard)
CREATE TABLE trades_local ON CLUSTER 'cluster_2s_2r'
(
    exchange_time DateTime64(3),
    symbol LowCardinality(String),
    price Decimal64(8),
    volume Decimal64(8),
    trade_id UInt64,
    side Enum8('buy' = 1, 'sell' = 2)
)
ENGINE = ReplicatedMergeTree
PARTITION BY toYYYYMM(exchange_time)
ORDER BY (symbol, exchange_time);

-- Distributed table for trades (shards by symbol hash)
CREATE TABLE trades ON CLUSTER 'cluster_2s_2r'
AS trades_local
ENGINE = Distributed('cluster_2s_2r', default, trades_local, cityHash64(symbol));

-- Local OHLCV table (on each shard)
CREATE TABLE ohlcv_1m_local ON CLUSTER 'cluster_2s_2r'
(
    symbol LowCardinality(String),
    bucket DateTime,
    open AggregateFunction(argMin, Decimal64(8), DateTime64(3)),
    high AggregateFunction(max, Decimal64(8)),
    low AggregateFunction(min, Decimal64(8)),
    close AggregateFunction(argMax, Decimal64(8), DateTime64(3)),
    volume AggregateFunction(sum, Decimal64(8)),
    trade_count AggregateFunction(count, UInt64)
)
ENGINE = ReplicatedAggregatingMergeTree
PARTITION BY toYYYYMM(bucket)
ORDER BY (symbol, bucket);

-- Distributed OHLCV table
CREATE TABLE ohlcv_1m ON CLUSTER 'cluster_2s_2r'
AS ohlcv_1m_local
ENGINE = Distributed('cluster_2s_2r', default, ohlcv_1m_local, cityHash64(symbol));

-- MV: trades_local -> ohlcv_1m_local (runs on each shard)
CREATE MATERIALIZED VIEW ohlcv_1m_mv ON CLUSTER 'cluster_2s_2r'
TO ohlcv_1m_local
AS SELECT
    symbol,
    toStartOfMinute(exchange_time) AS bucket,
    argMinState(price, exchange_time) AS open,
    maxState(price) AS high,
    minState(price) AS low,
    argMaxState(price, exchange_time) AS close,
    sumState(volume) AS volume,
    countState(trade_id) AS trade_count
FROM trades_local
GROUP BY symbol, bucket;

-- Kafka source (on ch-1a only - single consumer)
CREATE TABLE trades_kafka ON CLUSTER 'cluster_2s_2r'
(
    exchange_time DateTime64(3),
    symbol String,
    price Decimal64(8),
    volume Decimal64(8),
    trade_id UInt64,
    side String
)
ENGINE = Kafka
SETTINGS
    kafka_broker_list = 'kafka:9092',
    kafka_topic_list = 'trades',
    kafka_group_name = 'clickhouse',
    kafka_format = 'JSONEachRow';

-- MV: Kafka -> Distributed trades table (auto-routes to correct shard)
CREATE MATERIALIZED VIEW trades_kafka_mv ON CLUSTER 'cluster_2s_2r'
TO trades
AS SELECT
    exchange_time, symbol, price, volume, trade_id,
    CAST(side AS Enum8('buy' = 1, 'sell' = 2)) AS side
FROM trades_kafka;

-- Helper view for querying OHLCV
CREATE VIEW ohlcv_view ON CLUSTER 'cluster_2s_2r' AS
SELECT
    symbol, bucket,
    argMinMerge(open) AS open,
    maxMerge(high) AS high,
    minMerge(low) AS low,
    argMaxMerge(close) AS close,
    sumMerge(volume) AS volume,
    countMerge(trade_count) AS trades
FROM ohlcv_1m
GROUP BY symbol, bucket;
