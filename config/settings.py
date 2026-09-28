"""Configuration settings for Market Pilot Crypto Research Desk."""
from pathlib import Path
from pydantic import BaseModel
import os

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
PARQUET_DIR = DATA_DIR / "parquet"
DB_PATH = DATA_DIR / "crypto_research.db"

# Ensure data directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
PARQUET_DIR.mkdir(parents=True, exist_ok=True)

class Settings(BaseModel):
    # API Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8800
    
    # Binance Feeds
    BINANCE_SPOT_WS: str = "wss://stream.binance.com:9443"
    BINANCE_FUTURES_WS: str = "wss://fstream.binance.com"
    BINANCE_SPOT_REST: str = "https://api.binance.com"
    BINANCE_FUTURES_REST: str = "https://fapi.binance.com"
    
    # Universe V1 (Spot + Perpetuals)
    SYMBOLS_SPOT: list[str] = ["btcusdt", "ethusdt"]
    SYMBOLS_FUTURES: list[str] = ["btcusdt", "ethusdt"]
    
    # Timeouts & Reconnection
    WS_HEARTBEAT_SECONDS: int = 15
    WS_PROACTIVE_ROTATION_HOURS: int = 23  # Rotate before Binance 24h force drop
    MAX_DEPTH_LEVELS: int = 20             # Keep memory bounded
    
    # Storage settings
    DB_FILE: Path = DB_PATH
    PARQUET_BASE: Path = PARQUET_DIR

settings = Settings()
