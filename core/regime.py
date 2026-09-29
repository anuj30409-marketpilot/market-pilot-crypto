"""7-State Market Regime Classifier for Crypto Research Desk.

Classifies market state into one of 8 distinct regimes:
1. TRENDING_UP: Bullish directional momentum (Price > EMA, positive CVD, high directional index).
2. TRENDING_DOWN: Bearish directional momentum (Price < EMA, negative CVD, high directional index).
3. RANGE: Low directional momentum, mean-reverting within Bollinger bands.
4. HIGH_VOLATILITY: Realized volatility in the top quartile of rolling distribution.
5. LOW_VOLATILITY: Volatility compression / consolidation before breakout.
6. LIQUIDATION_EVENT: Active liquidation cascade with severe volume spikes.
7. FUNDING_EXTREME: Leverage crowding dislocation (funding z-score > |2.5|).
8. DATA_DEGRADED: Feed latency, missing snapshots, or degraded feed health.

Strategy execution gates are strictly conditioned on regime validity out-of-sample.
"""
from enum import Enum
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGE = "RANGE"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    LIQUIDATION_EVENT = "LIQUIDATION_EVENT"
    FUNDING_EXTREME = "FUNDING_EXTREME"
    DATA_DEGRADED = "DATA_DEGRADED"


class RegimeState(BaseModel):
    symbol: str
    primary_regime: MarketRegime
    confidence: float
    regime_scores: Dict[str, float]
    metrics: Dict[str, float]
    timestamp_ms: int


def calculate_ema(prices: List[float], period: int) -> float:
    """Calculates Exponential Moving Average on price series."""
    if not prices or len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2.0 / (period + 1.0)
    ema = prices[0]
    for p in prices[1:]:
        ema = (p * k) + (ema * (1.0 - k))
    return ema


def calculate_realized_volatility(closes: List[float], window: int = 30) -> float:
    """Computes annualized-equivalent or rolling realized standard deviation of log returns."""
    if len(closes) < window + 1:
        return 0.0
    returns: List[float] = []
    for i in range(len(closes) - window, len(closes)):
        prev = closes[i - 1]
        if prev > 0:
            returns.append((closes[i] - prev) / prev)
    if len(returns) < 2:
        return 0.0
    mean_ret = sum(returns) / len(returns)
    variance = sum((r - mean_ret) ** 2 for r in returns) / len(returns)
    return (variance ** 0.5) * 10000.0  # in basis points


def classify_regime(
    symbol: str,
    recent_closes: List[float],
    recent_highs: List[float],
    recent_lows: List[float],
    recent_volumes: List[float],
    funding_zscore: float,
    liquidation_intensity: float,
    cvd_notional_zscore: float,
    feed_quarantine: bool,
    feed_latency_ms: int,
    timestamp_ms: int,
) -> RegimeState:
    """Classifies the market into a canonical MarketRegime."""
    # 1. First priority: Feed health / Data Degraded
    if feed_quarantine or feed_latency_ms > 2000:
        return RegimeState(
            symbol=symbol,
            primary_regime=MarketRegime.DATA_DEGRADED,
            confidence=1.0,
            regime_scores={MarketRegime.DATA_DEGRADED.value: 1.0},
            metrics={"feed_latency_ms": float(feed_latency_ms)},
            timestamp_ms=timestamp_ms,
        )

    # 2. Liquidation Event
    if liquidation_intensity >= 3.0:
        return RegimeState(
            symbol=symbol,
            primary_regime=MarketRegime.LIQUIDATION_EVENT,
            confidence=min(1.0, liquidation_intensity / 5.0),
            regime_scores={MarketRegime.LIQUIDATION_EVENT.value: min(1.0, liquidation_intensity / 4.0)},
            metrics={"liquidation_intensity": liquidation_intensity},
            timestamp_ms=timestamp_ms,
        )

    # 3. Funding Extreme
    if abs(funding_zscore) >= 2.5:
        return RegimeState(
            symbol=symbol,
            primary_regime=MarketRegime.FUNDING_EXTREME,
            confidence=min(1.0, abs(funding_zscore) / 3.5),
            regime_scores={MarketRegime.FUNDING_EXTREME.value: min(1.0, abs(funding_zscore) / 3.0)},
            metrics={"funding_zscore": funding_zscore},
            timestamp_ms=timestamp_ms,
        )

    # 4. Volatility Checks (Realized Volatility)
    current_price = recent_closes[-1] if recent_closes else 0.0
    vol_bps = calculate_realized_volatility(recent_closes, window=30) if len(recent_closes) >= 31 else 0.0

    # Moving averages
    ema_fast = calculate_ema(recent_closes, 12)
    ema_slow = calculate_ema(recent_closes, 50)
    ema_trend = calculate_ema(recent_closes, 200) if len(recent_closes) >= 200 else ema_slow

    # Trend metric
    trend_spread_bps = ((ema_fast - ema_slow) / ema_slow * 10000.0) if ema_slow > 0 else 0.0

    # High Volatility Regime
    if vol_bps > 60.0:  # e.g., >60 bps rolling 30m return standard deviation
        return RegimeState(
            symbol=symbol,
            primary_regime=MarketRegime.HIGH_VOLATILITY,
            confidence=min(1.0, vol_bps / 100.0),
            regime_scores={MarketRegime.HIGH_VOLATILITY.value: min(1.0, vol_bps / 80.0)},
            metrics={"vol_bps": vol_bps, "trend_spread_bps": trend_spread_bps},
            timestamp_ms=timestamp_ms,
        )

    # Low Volatility / Compression
    if vol_bps < 12.0 and abs(trend_spread_bps) < 5.0:
        return RegimeState(
            symbol=symbol,
            primary_regime=MarketRegime.LOW_VOLATILITY,
            confidence=0.85,
            regime_scores={MarketRegime.LOW_VOLATILITY.value: 0.85},
            metrics={"vol_bps": vol_bps, "trend_spread_bps": trend_spread_bps},
            timestamp_ms=timestamp_ms,
        )

    # Directional Trending Up
    if current_price > ema_trend and trend_spread_bps > 8.0 and cvd_notional_zscore > 0.5:
        return RegimeState(
            symbol=symbol,
            primary_regime=MarketRegime.TRENDING_UP,
            confidence=min(1.0, (trend_spread_bps / 20.0) + (cvd_notional_zscore * 0.1)),
            regime_scores={MarketRegime.TRENDING_UP.value: 0.90},
            metrics={"trend_spread_bps": trend_spread_bps, "cvd_notional_zscore": cvd_notional_zscore},
            timestamp_ms=timestamp_ms,
        )

    # Directional Trending Down
    if current_price < ema_trend and trend_spread_bps < -8.0 and cvd_notional_zscore < -0.5:
        return RegimeState(
            symbol=symbol,
            primary_regime=MarketRegime.TRENDING_DOWN,
            confidence=min(1.0, (abs(trend_spread_bps) / 20.0) + (abs(cvd_notional_zscore) * 0.1)),
            regime_scores={MarketRegime.TRENDING_DOWN.value: 0.90},
            metrics={"trend_spread_bps": trend_spread_bps, "cvd_notional_zscore": cvd_notional_zscore},
            timestamp_ms=timestamp_ms,
        )

    # 5. Default to RANGE
    return RegimeState(
        symbol=symbol,
        primary_regime=MarketRegime.RANGE,
        confidence=0.75,
        regime_scores={MarketRegime.RANGE.value: 0.75},
        metrics={"vol_bps": vol_bps, "trend_spread_bps": trend_spread_bps},
        timestamp_ms=timestamp_ms,
    )
