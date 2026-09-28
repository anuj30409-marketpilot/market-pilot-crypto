"""Market Pilot Crypto Research Desk - Primary Process Runner.

Runs both the async WebSocket collector and FastAPI server inside a single
event loop for maximum memory efficiency on 1 GB RAM cloud VMs.
"""
import asyncio
import uvicorn
from collectors.binance_ws import collector
from collectors.derivatives import derivatives_engine
from paper.paper_engine import paper_engine
from api.server import app
from config.settings import settings
import logging

logger = logging.getLogger("crypto_main")

async def run_services():
    # 1. Start WebSocket collector background task
    collector_task = asyncio.create_task(collector.run())

    # 2. Start Open Interest background polling task
    oi_task = asyncio.create_task(derivatives_engine.run_oi_poller())

    # 3. Paper engine: update unrealised PnL + SL/TP on every mark price tick
    paper_mark_task = asyncio.create_task(paper_engine.run_mark_price_updater())

    # 4. Paper engine: charge real funding rates every 8 hours
    paper_funding_task = asyncio.create_task(paper_engine.run_funding_charger())

    # 5. Configure Uvicorn server
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
        server_task,
        oi_task,
        paper_mark_task,
        paper_funding_task,
    )

if __name__ == "__main__":
    try:
        asyncio.run(run_services())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Market Pilot Crypto service stopped.")
