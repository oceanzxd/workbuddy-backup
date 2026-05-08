"""
Capital Flow Analysis Module
Fetches and analyzes fund flow data from East Money API for A-stock market.
Supports concurrent batch fetching, sector aggregation, and capital attraction scoring.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class Market(Enum):
    SHANGHAI = "1"
    SHENZHEN = "2"


# East Money API base URL for daily capital flow data
EM_FLOW_API = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"


@dataclass
class FlowRecord:
    """Parsed single-day capital flow record from East Money API."""
    date: str
    net_inflow: float          # 主力净流入 (f52)
    main_net: float           # 主力净额 (f53)
    large_net: float          # 大单净额 (f54)
    super_large_net: float    # 超大单净额 (f55)
    small_net: float          # 小单净额 (f56)
    medium_net: float         # 中单净额 (f57)
    main_net_ratio: float     # 主力净流入比率 (f58)
    large_net_in: float       # 大单买入 (f59)
    large_net_out: float      # 大单卖出 (f60)
    total_flow: float         # 总成交额 (f61)
    capital_score: float = 0.0  # calculated: [-3, +3]


@dataclass
class StockFlowResult:
    """Capital flow data for a single stock."""
    code: str
    name: str
    market: str
    records: list[FlowRecord] = field(default_factory=list)
    aggregate_net_inflow: float = 0.0
    aggregate_main_net: float = 0.0
    avg_capital_score: float = 0.0


class CapitalFlowAnalyzer:
    """
    Fetches and analyzes capital (fund) flow data from East Money API.

    secid format: {market_id}.{stock_code}
      - Shanghai: 1.600000
      - Shenzhen: 2.000001

    API fields returned:
      f1: 最新价  f2: 涨跌幅  f3: 涨跌额  f4: 换手率
      f51: 日期  f52: 主力净流入  f53: 主力净额
      f54: 大单净额  f55: 超大单净额  f56: 小单净额  f57: 中单净额
      f58: 主力净流入比率  f59: 大单买入  f60: 大单卖出  f61: 总成交额
    """

    def __init__(self):
        self._cache: dict[str, list[dict]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def fetch_stock_flow(
        self,
        stock_code: str,
        market: Market = Market.SHANGHAI,
        days: int = 10,
    ) -> Optional[StockFlowResult]:
        """Fetch capital flow data for a single stock."""
        results = await self.fetch_batch([stock_code], market, days)
        return results[0] if results else None

    async def fetch_batch(
        self,
        stock_codes: list[str],
        market: Market = Market.SHANGHAI,
        days: int = 10,
        concurrency: int = 10,
    ) -> list[StockFlowResult]:
        """Fetch capital flow data for multiple stocks concurrently."""
        sem = asyncio.Semaphore(concurrency)
        tasks = [
            self._fetch_single(stock_code, market, days, sem)
            for stock_code in stock_codes
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        results: list[StockFlowResult] = []
        for task in done:
            try:
                res = task.result()
                if res is not None:
                    results.append(res)
            except Exception as exc:
                logger.error("Batch fetch failed for a task: %s", exc)
        return results

    async def _fetch_single(
        self,
        stock_code: str,
        market: Market,
        days: int,
        semaphore: asyncio.Semaphore,
    ) -> Optional[StockFlowResult]:
        """Internal: fetch + parse one stock behind a semaphore."""
        async with semaphore:
            secid = f"{market.value}.{stock_code}"
            try:
                raw = await self._request(secid, days)
                records = self._parse_records(raw)
                result = StockFlowResult(
                    code=stock_code,
                    name=raw.get("name", ""),
                    market=market.value,
                    records=records,
                )
                # Aggregate summary
                if records:
                    result.aggregate_net_inflow = sum(r.net_inflow for r in records)
                    result.aggregate_main_net = sum(r.main_net for r in records)
                    result.avg_capital_score = sum(r.capital_score for r in records) / len(
                        records
                    )
                return result
            except Exception as exc:
                logger.error("Failed to fetch flow for %s: %s", stock_code, exc)
                return None

    async def _request(self, secid: str, days: int) -> dict:
        """Execute a single request to East Money API."""
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "1",  # daily
            "lmt": str(days),
        }
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        ) as session:
            async with session.get(EM_FLOW_API, params=params) as resp:
                resp.raise_for_status()
                text = await resp.text()
                data = json.loads(text)

        if not data.get("data") or not data["data"].get("klines"):
            raise ValueError(f"No flow data returned for secid={secid}")

        return data["data"]

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_records(data: dict) -> list[FlowRecord]:
        """Parse East Money kline string lines into FlowRecord objects."""
        records: list[FlowRecord] = []
        # Each kline: date,close,chgpct,chgp,volume,avgprice,main_net,
        #             main_pct,large_buy,large_sell,small_net,medium_net,
        #             large_net,super_large_net,turnover,total_flow
        for line in data["klines"]:
            parts = line.split(",")
            if len(parts) < 15:
                logger.warning("Skipping malformed kline: %s", line)
                continue
            try:
                rec = FlowRecord(
                    date=parts[0],
                    net_inflow=float(parts[6]),   # f53 主力净流入 / main_net
                    main_net=float(parts[6]),
                    large_net=float(parts[13]),   # f54
                    super_large_net=float(parts[14]),  # f55
                    small_net=float(parts[11]),   # f56
                    medium_net=float(parts[12]),  # f57
                    main_net_ratio=float(parts[7]),  # f58
                    large_net_in=float(parts[8]), # f59
                    large_net_out=float(parts[9]),  # f60
                    total_flow=float(parts[16]) if len(parts) > 16 else 0.0,  # f61
                )
                rec.capital_score = CapitalFlowAnalyzer.score_capital_attraction(
                    rec.net_inflow, rec.total_flow
                )
                records.append(rec)
            except (ValueError, IndexError) as exc:
                logger.debug("Skipping line parse error: %s (%s)", line, exc)
        return records

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def score_capital_attraction(net_inflow: float, total_flow: float) -> float:
        """
        Score capital attraction on a scale of [-3, +3].

        Score is based on net inflow as a percentage of total turnover:
          ratio = net_inflow / total_flow
          score  = clipped(ratio * 100, -3, +3)

        A higher positive score means stronger net inflow (bullish signal).
        A higher negative score means stronger net outflow (bearish signal).
        """
        if total_flow <= 0:
            return 0.0
        # Convert to percentage and scale down to [-3, +3] range
        ratio = net_inflow / total_flow
        score = ratio * 100  # e.g. 5% -> +5, clip to +3
        return max(-3.0, min(3.0, score))

    # ------------------------------------------------------------------
    # Sector aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def aggregate_sector_flows(
        results: list[StockFlowResult],
    ) -> dict[str, dict]:
        """
        Aggregate capital flows by market (Shanghai / Shenzhen) or by
        grouping key if sector labels are attached.

        Returns:
            dict keyed by market code -> summary stats
        """
        buckets: dict[str, list[float]] = {}
        for r in results:
            key = r.market
            if key not in buckets:
                buckets[key] = []
            buckets[key].append(r.aggregate_net_inflow)

        aggregated: dict[str, dict] = {}
        for market, inflows in buckets.items():
            aggregated[market] = {
                "total_net_inflow": sum(inflows),
                "avg_net_inflow": sum(inflows) / len(inflows),
                "count": len(inflows),
                "positive_flow_count": sum(1 for v in inflows if v > 0),
                "negative_flow_count": sum(1 for v in inflows if v < 0),
            }
        return aggregated

    # ------------------------------------------------------------------
    # Convenience: parse from raw JSON (useful for logging / replay tests)
    # ------------------------------------------------------------------

    @staticmethod
    def parse_from_raw(raw_json: str) -> list[FlowRecord]:
        """Parse FlowRecords from a saved raw API response JSON string."""
        data = json.loads(raw_json)
        em_data = data.get("data", {}) or {}
        return CapitalFlowAnalyzer._parse_records(em_data)


# ------------------------------------------------------------------
# Standalone CLI helper
# ------------------------------------------------------------------

async def _cli_main():
    """Quick demo: fetch flow for a few Shanghai stocks."""
    analyzer = CapitalFlowAnalyzer()
    codes = ["600000", "600036", "601318", "600519", "000001"]
    results = await analyzer.fetch_batch(codes, Market.SHANGHAI, days=5)
    for r in results:
        print(f"{r.code} ({r.name}): net_inflow={r.aggregate_net_inflow:.2f}, "
              f"avg_score={r.avg_capital_score:.2f}")
    agg = analyzer.aggregate_sector_flows(results)
    for market, info in agg.items():
        print(f"Market {market}: {info}")


if __name__ == "__main__":
    asyncio.run(_cli_main())
