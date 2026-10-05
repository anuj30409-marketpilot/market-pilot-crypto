"""Market Pilot Crypto Research Desk - Primary Process Runner.

Runs both the async WebSocket collector and FastAPI server inside a single
event loop for maximum memory efficiency on 1 GB RAM cloud VMs.
"""
import asyncio
import uvicorn
from collectors.binance_ws import collector
from collectors.derivatives import derivatives_engine
from collectors.kline_rest_fallback import kline_rest_fallback
from paper.paper_engine import paper_engine
from strategies.evaluator import strategy_evaluator
from strategies.superhuman_evaluator import superhuman_evaluator
from api.server import app
from config.settings import settings
import logging

logger = logging.getLogger("crypto_main")

async def run_services():
    # 1. Start WebSocket collector background task
    collector_task = asyncio.create_task(collector.run())

    # 1b. Start REST kline fallback poller (2026-09-30: futures WS kline channel went silent)
    kline_rest_task = asyncio.create_task(kline_rest_fallback.run())

    # 2. Start Open Interest background polling task
    oi_task = asyncio.create_task(derivatives_engine.run_oi_poller())

    # 3. Paper engine: update unrealised PnL + SL/TP on every mark price tick
    paper_mark_task = asyncio.create_task(paper_engine.run_mark_price_updater())

    # 4. Paper engine: charge real funding rates every 8 hours
    paper_funding_task = asyncio.create_task(paper_engine.run_funding_charger())

    # 4b. Start Delta India Parity Feed Poller
    from execution.parity import parity_engine
    parity_task = asyncio.create_task(parity_engine.run_delta_poller(interval_seconds=3.0))

    # 5. Continuous Alpha Strategy Evaluator (evaluates S1, S2, S3 every 60s)
    evaluator_task = asyncio.create_task(strategy_evaluator.run_loop())

    # 6. Continuous Superhuman AI Strategy Evaluator (SH1, SH2, SH3 every 60s)
    superhuman_task = asyncio.create_task(superhuman_evaluator.run_loop())

    # 7. Configure Uvicorn server
    config = uvicorn.Config(
        app=app,
        host=settings.API_HOST,
        port=settings.API_PORT,
        log_level="info",
        access_log=False  # Disable per-request access logs to preserve disk IOPS
    )
    server = uvicorn.Server(config)

    logger.info(f"Starting Market Pilot Crypto API on {settings.API_HOST}:{settings.API_PORT}")
    server_task = asyncio.create_task(server.serve())

    # Wait for all tasks
    await asyncio.gather(
        collector_task,
        kline_rest_task,
        server_task,
        oi_task,
        parity_task,
        paper_mark_task,
        paper_funding_task,
        evaluator_task,
        superhuman_task,
    )

if __name__ == "__main__":
    try:
        asyncio.run(run_services())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Market Pilot Crypto service stopped.")
