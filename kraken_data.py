"""
KrakenDataAdapter — Fetch OHLC and ticker data via Kraken CLI

Usage:
    adapter = KrakenDataAdapter()
    df = adapter.get_ohlc("BTC/USDT", interval="4h")
    ticker = adapter.get_ticker("BTC/USDT")
"""
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from config import PAIR_MAP, RESPONSE_KEY_TO_PAIR, INTERVAL_MAP
from kraken_cli import run_kraken

logger = logging.getLogger(__name__)


class KrakenDataAdapter:
    """Wraps Kraken CLI for market data retrieval."""

    def __init__(self, timeout: int = 30):
        self.timeout = timeout

    def _run_kraken(self, args: list[str]) -> dict:
        """Execute a kraken CLI command and return parsed JSON."""
        return run_kraken(args, timeout=self.timeout)

    def _resolve_pair(self, pair: str) -> tuple[str, str]:
        """Resolve standard pair name to CLI pair and response key.

        Returns:
            (cli_pair, response_key)
        """
        info = PAIR_MAP.get(pair)
        if not info:
            raise ValueError(f"Unknown pair: {pair}. Available: {list(PAIR_MAP.keys())}")
        return info["cli"], info["response_key"]

    def get_ohlc(self, pair: str, interval: str = "4h") -> pd.DataFrame:
        """Fetch OHLC candles for a pair.

        Args:
            pair: Standard pair name, e.g. "BTC/USDT"
            interval: "1h", "4h", or "1d"

        Returns:
            DataFrame with columns: [timestamp, open, high, low, close, volume]
        """
        cli_pair, response_key = self._resolve_pair(pair)
        interval_minutes = INTERVAL_MAP.get(interval)
        if interval_minutes is None:
            raise ValueError(f"Unknown interval: {interval}. Available: {list(INTERVAL_MAP.keys())}")

        data = self._run_kraken(["ohlc", cli_pair, "--interval", str(interval_minutes)])
        if not data:
            logger.warning(f"No OHLC data for {pair}")
            return pd.DataFrame()

        # Kraken returns {response_key: [[ts, o, h, l, c, vwap, vol, count], ...], "last": ...}
        candles = data.get(response_key, [])
        if not candles:
            # Try to find the data under any key (fallback)
            for key, value in data.items():
                if key != "last" and isinstance(value, list) and len(value) > 0:
                    candles = value
                    break

        if not candles:
            logger.warning(f"No candles found for {pair} ({response_key})")
            return pd.DataFrame()

        df = pd.DataFrame(candles, columns=[
            "timestamp", "open", "high", "low", "close", "vwap", "volume", "count"
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = df[col].astype(float)

        df = df[["timestamp", "open", "high", "low", "close", "volume"]].copy()
        df = df.sort_values("timestamp").reset_index(drop=True)
        return df

    def get_ticker(self, pair: str) -> dict:
        """Fetch current ticker for a pair.

        Returns:
            dict with keys: bid, ask, last, volume_24h
        """
        cli_pair, response_key = self._resolve_pair(pair)
        data = self._run_kraken(["ticker", cli_pair])
        if not data:
            return {}

        ticker_data = data.get(response_key, {})
        if not ticker_data:
            for key, value in data.items():
                if isinstance(value, dict) and "c" in value:
                    ticker_data = value
                    break

        if not ticker_data:
            return {}

        return {
            "bid": float(ticker_data["b"][0]),
            "ask": float(ticker_data["a"][0]),
            "last": float(ticker_data["c"][0]),
            "volume_24h": float(ticker_data["v"][1]),
            "pair": pair,
        }

    def get_multi_ticker(self, pairs: list[str] | None = None) -> dict[str, dict]:
        """Fetch tickers for multiple pairs.

        Args:
            pairs: List of standard pair names. Defaults to all active pairs.

        Returns:
            dict mapping pair name to ticker dict
        """
        if pairs is None:
            pairs = list(PAIR_MAP.keys())

        results = {}
        for pair in pairs:
            try:
                ticker = self.get_ticker(pair)
                if ticker:
                    results[pair] = ticker
            except Exception as e:
                logger.error(f"Ticker fetch failed for {pair}: {e}")
        return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    adapter = KrakenDataAdapter()

    print("=== OHLC Test (BTC/USDT 4H) ===")
    df = adapter.get_ohlc("BTC/USDT", "4h")
    if not df.empty:
        print(f"Rows: {len(df)}")
        print(df.tail(3).to_string(index=False))
    else:
        print("No data")

    print("\n=== Ticker Test ===")
    ticker = adapter.get_ticker("BTC/USDT")
    print(ticker)
