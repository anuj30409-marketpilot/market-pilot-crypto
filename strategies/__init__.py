"""Quantitative Alpha Strategies package for Crypto Research Desk."""
from strategies.funding_reversion import evaluate_funding_reversion
from strategies.orderbook_momentum import evaluate_orderbook_momentum
from strategies.liquidation_fader import evaluate_liquidation_fader
from strategies.evaluator import strategy_evaluator

__all__ = [
    "evaluate_funding_reversion",
    "evaluate_orderbook_momentum",
    "evaluate_liquidation_fader",
    "strategy_evaluator",
]
