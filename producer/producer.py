#!/usr/bin/env python3
"""
Financial Tick Data Producer for ClickHouse Lab

Generates realistic-looking trade data and publishes to Kafka.
Simulates multiple trading pairs with random walk price movements.

Usage:
    python producer.py                    # Run with defaults
    python producer.py --rate 100         # 100 trades per second
    python producer.py --symbols BTC,ETH  # Custom symbols
"""

import json
import time
import random
import argparse
from datetime import datetime
from typing import Generator
from kafka import KafkaProducer
from kafka.errors import KafkaError

# Default configuration
DEFAULT_KAFKA_BROKER = "localhost:9093"
DEFAULT_TOPIC = "trades"
DEFAULT_SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD"]
DEFAULT_RATE = 10  # trades per second


class PriceSimulator:
    """Simulates random walk price movements for a symbol."""

    def __init__(self, symbol: str, initial_price: float, volatility: float = 0.0002):
        self.symbol = symbol
        self.price = initial_price
        self.volatility = volatility
        self.trade_id = random.randint(1000000, 9999999)

    def next_trade(self) -> dict:
        """Generate next trade with random price movement."""
        # Random walk: price changes by small percentage
        change = random.gauss(0, self.volatility)
        self.price *= (1 + change)
        self.price = max(self.price, 0.0001)  # Prevent negative prices

        # Random volume (log-normal distribution for realistic volumes)
        volume = random.lognormvariate(0, 1) * 0.1

        # Random side
        side = random.choice(["buy", "sell"])

        self.trade_id += 1

        return {
            "exchange_time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            "symbol": self.symbol,
            "price": round(self.price, 8),
            "volume": round(volume, 8),
            "trade_id": self.trade_id,
            "side": side
        }


def create_simulators(symbols: list[str]) -> dict[str, PriceSimulator]:
    """Create price simulators with realistic starting prices."""
    initial_prices = {
        "BTC/USD": 45000.0,
        "ETH/USD": 2500.0,
        "SOL/USD": 100.0,
        "DOGE/USD": 0.08,
        "XRP/USD": 0.55,
        "ADA/USD": 0.45,
    }

    simulators = {}
    for symbol in symbols:
        price = initial_prices.get(symbol, random.uniform(10, 1000))
        simulators[symbol] = PriceSimulator(symbol, price)

    return simulators


def generate_trades(simulators: dict[str, PriceSimulator]) -> Generator[dict, None, None]:
    """Infinite generator of trades across all symbols."""
    symbols = list(simulators.keys())
    while True:
        # Weight selection towards more liquid symbols
        symbol = random.choice(symbols)
        yield simulators[symbol].next_trade()


def main():
    parser = argparse.ArgumentParser(description="Financial tick data producer")
    parser.add_argument("--broker", default=DEFAULT_KAFKA_BROKER, help="Kafka broker address")
    parser.add_argument("--topic", default=DEFAULT_TOPIC, help="Kafka topic name")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS), help="Comma-separated symbols")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, help="Trades per second")
    parser.add_argument("--count", type=int, default=0, help="Total trades to send (0=infinite)")
    parser.add_argument("--dry-run", action="store_true", help="Print trades without sending")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    simulators = create_simulators(symbols)

    print(f"Starting producer...")
    print(f"  Broker: {args.broker}")
    print(f"  Topic: {args.topic}")
    print(f"  Symbols: {symbols}")
    print(f"  Rate: {args.rate} trades/sec")
    print()

    if args.dry_run:
        print("DRY RUN - not connecting to Kafka")
        trade_gen = generate_trades(simulators)
        for i, trade in enumerate(trade_gen):
            print(json.dumps(trade))
            time.sleep(1 / args.rate)
            if args.count and i >= args.count - 1:
                break
        return

    # Connect to Kafka
    try:
        producer = KafkaProducer(
            bootstrap_servers=args.broker,
            value_serializer=lambda v: json.dumps(v).encode("utf-8"),
            acks="all",
            retries=3,
        )
        print(f"Connected to Kafka at {args.broker}")
    except KafkaError as e:
        print(f"Failed to connect to Kafka: {e}")
        print("Make sure Kafka is running and accessible.")
        return

    # Generate and send trades
    trade_gen = generate_trades(simulators)
    interval = 1 / args.rate
    sent = 0
    start_time = time.time()

    try:
        for trade in trade_gen:
            producer.send(args.topic, value=trade)
            sent += 1

            # Progress logging every 100 trades
            if sent % 100 == 0:
                elapsed = time.time() - start_time
                actual_rate = sent / elapsed if elapsed > 0 else 0
                print(f"Sent {sent} trades ({actual_rate:.1f}/sec) - {trade['symbol']}: {trade['price']:.4f}")

            # Rate limiting
            time.sleep(interval)

            # Check count limit
            if args.count and sent >= args.count:
                break

    except KeyboardInterrupt:
        print(f"\nInterrupted. Sent {sent} trades total.")
    finally:
        producer.flush()
        producer.close()
        print("Producer closed.")


if __name__ == "__main__":
    main()
