import asyncio
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from collectors.binance_ws import collector
from collectors.derivatives import derivatives_engine
from strategies.evaluator import strategy_evaluator

async def main():
    print("=== Testing Strategy Evaluator Once ===")
    records = await strategy_evaluator.evaluate_once()
    print(f"Generated {len(records)} candidate records:")
    for r in records:
        print(f"[{r.symbol}] {r.strategy_id} -> {r.decision} (Reason: {r.decision_reason}) [Score: {r.signal_score:.2f}, Net Edge: {r.expected_net_edge_bps:.1f} bps, Regime: {r.regime}]")

if __name__ == "__main__":
    asyncio.run(main())
