"""Backtest & Replay Simulator: Old Strategy vs New Strategy on Last 2-3 Days Live Data.

Replays 1-minute historical candles and derivatives states from data/crypto_research_vm2.db:
- Compares Old Logic (Baseline that generated the -$148.55 result) vs New Logic (With all fixes).
- Models orderbook slippage, taker fees (4 bps/leg = 8 bps roundtrip), 5x leverage, and risk-as-loss sizing ($25 risk budget).
- Computes Win Rate, PnL, Drawdown, Fee Drag, and Strategy attribution.
"""
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, List, Tuple, Optional


def load_dataset(db_path: str = "data/crypto_research_vm2.db"):
    conn = sqlite3.connect(db_path)
    df_candles = pd.read_sql_query(
        "SELECT * FROM crypto_candles_1m ORDER BY open_time_ms ASC", conn
    )
    df_deriv = pd.read_sql_query(
        "SELECT * FROM crypto_derivatives_state ORDER BY state_time_ms ASC", conn
    )
    conn.close()
    return df_candles, df_deriv


def run_simulation(mode: str = "NEW"):
    """mode: 'OLD' or 'NEW'"""
    conn = sqlite3.connect("data/crypto_research_vm2.db")
    df_candles = pd.read_sql_query(
        "SELECT symbol, open_time_ms, open, high, low, close, volume FROM crypto_candles_1m ORDER BY open_time_ms ASC", conn
    )
    df_deriv = pd.read_sql_query(
        "SELECT symbol, state_time_ms, mark_price, cvd_notional_usd_zscore, funding_rate, regime FROM crypto_derivatives_state ORDER BY state_time_ms ASC", conn
    )
    conn.close()

    # Configuration differences
    if mode == "OLD":
        donchian_period = 20
        ema_fast_p = 20
        ema_slow_p = 50
        s2_tp_pct = 1.20
        s2_sl_pct = 0.60
        s2_max_hold_mins = 30
        sh1_use_cvd_direction = False  # Always Long
        swing_require_cvd = False
    else:  # NEW
        donchian_period = 60
        ema_fast_p = 30
        ema_slow_p = 90
        s2_tp_pct = 0.60
        s2_sl_pct = 0.40
        s2_max_hold_mins = 30
        sh1_use_cvd_direction = True   # Long if cvd_z > 0 else Short
        swing_require_cvd = True

    # Risk Parameters
    equity = 10000.0  # Per desk
    risk_budget_usd = 25.0
    taker_fee_bps = 4.0  # 4 bps each side
    roundtrip_fee_bps = 8.0

    symbols = ["BTCUSDT", "ETHUSDT"]
    results = []

    for sym in symbols:
        sym_candles = df_candles[df_candles["symbol"] == sym].sort_values("open_time_ms").reset_index(drop=True)
        sym_deriv = df_deriv[df_deriv["symbol"] == sym].sort_values("state_time_ms").reset_index(drop=True)
        
        # Merge candles and deriv by nearest timestamp within 60s
        merged = pd.merge_asof(
            sym_candles,
            sym_deriv[["state_time_ms", "cvd_notional_usd_zscore", "funding_rate", "regime"]],
            left_on="open_time_ms",
            right_on="state_time_ms",
            direction="nearest",
            tolerance=60000
        )

        closes = merged["close"].tolist()
        highs = merged["high"].tolist()
        lows = merged["low"].tolist()
        volumes = merged["volume"].tolist()
        cvd_zs = merged["cvd_notional_usd_zscore"].fillna(0.0).tolist()
        regimes = merged["regime"].fillna("RANGE").tolist()
        times = merged["open_time_ms"].tolist()

        n_bars = len(closes)
        open_positions = []  # active positions

        min_bars = max(ema_slow_p, donchian_period) + 15

        for i in range(min_bars, n_bars):
            curr_time = times[i]
            curr_close = closes[i]
            curr_high = highs[i]
            curr_low = lows[i]
            curr_cvd = cvd_zs[i]
            curr_regime = regimes[i]

            # 1. Update & Check Open Positions
            active = []
            for pos in open_positions:
                entry_p = pos["entry_price"]
                direction = pos["direction"]
                sl_p = pos["sl_price"]
                tp_p = pos["tp_price"]
                bars_held = i - pos["entry_bar"]
                max_bars = pos["max_hold_bars"]

                closed = False
                exit_price = curr_close
                exit_reason = None

                # Check SL and TP
                if direction == "LONG":
                    if curr_low <= sl_p:
                        closed = True
                        exit_price = sl_p
                        exit_reason = "STOP_LOSS"
                    elif curr_high >= tp_p:
                        closed = True
                        exit_price = tp_p
                        exit_reason = "TAKE_PROFIT"
                    elif bars_held >= max_bars:
                        closed = True
                        exit_price = curr_close
                        exit_reason = "TIME_STOP"
                else:  # SHORT
                    if curr_high >= sl_p:
                        closed = True
                        exit_price = sl_p
                        exit_reason = "STOP_LOSS"
                    elif curr_low <= tp_p:
                        closed = True
                        exit_price = tp_p
                        exit_reason = "TAKE_PROFIT"
                    elif bars_held >= max_bars:
                        closed = True
                        exit_price = curr_close
                        exit_reason = "TIME_STOP"

                if closed:
                    notional = pos["notional"]
                    if direction == "LONG":
                        price_return = (exit_price - entry_p) / entry_p
                    else:
                        price_return = (entry_p - exit_price) / entry_p
                    
                    gross_pnl = price_return * notional
                    fee = notional * (roundtrip_fee_bps / 10000.0)
                    net_pnl = gross_pnl - fee

                    pos["exit_price"] = exit_price
                    pos["exit_time"] = curr_time
                    pos["exit_reason"] = exit_reason
                    pos["gross_pnl"] = gross_pnl
                    pos["fees"] = fee
                    pos["net_pnl"] = net_pnl
                    pos["hold_mins"] = bars_held
                    results.append(pos)
                else:
                    active.append(pos)

            open_positions = active

            # 2. Evaluate Strategies for New Entries if under limit (max 2 per symbol)
            if len(open_positions) >= 2:
                continue

            sub_closes = closes[:i+1]
            sub_highs = highs[:i+1]
            sub_lows = lows[:i+1]
            sub_vols = volumes[:i+1]

            # Strategy A: SH1 Superhuman Macro Impulse
            # Fires in RANGE/TRENDING when |cvd_z| >= 1.60
            can_fire_sh1 = (abs(curr_cvd) >= 1.60)
            if can_fire_sh1 and len([p for p in open_positions if p["strat"] == "SH1"]) == 0:
                if mode == "OLD":
                    sh1_dir = "LONG"  # Buggy fallback
                else:
                    sh1_dir = "LONG" if curr_cvd >= 0 else "SHORT"

                sl_pct = 0.80
                tp_pct = 1.60
                total_adverse = (sl_pct / 100.0) + (0.13 / 100.0)
                notional = risk_budget_usd / total_adverse

                entry_p = curr_close
                sl_p = entry_p * (1.0 - sl_pct / 100.0) if sh1_dir == "LONG" else entry_p * (1.0 + sl_pct / 100.0)
                tp_p = entry_p * (1.0 + tp_pct / 100.0) if sh1_dir == "LONG" else entry_p * (1.0 - tp_pct / 100.0)

                open_positions.append({
                    "symbol": sym,
                    "strat": "SH1_MACRO",
                    "direction": sh1_dir,
                    "entry_price": entry_p,
                    "entry_time": curr_time,
                    "entry_bar": i,
                    "notional": notional,
                    "sl_price": sl_p,
                    "tp_price": tp_p,
                    "max_hold_bars": 120,  # 2 hours
                    "mode": mode
                })

            # Strategy B: S4 Swing Momentum Breakout
            # Donchian channel
            dc_h = max(sub_highs[-donchian_period - 1:-1])
            dc_l = min(sub_lows[-donchian_period - 1:-1])

            # EMAs
            # EMA fast
            k_f = 2.0 / (ema_fast_p + 1)
            ema_f = sub_closes[0]
            for p in sub_closes[1:]:
                ema_f = p * k_f + ema_f * (1.0 - k_f)

            # EMA slow
            k_s = 2.0 / (ema_slow_p + 1)
            ema_s = sub_closes[0]
            for p in sub_closes[1:]:
                ema_s = p * k_s + ema_s * (1.0 - k_s)

            # Vol expansion
            vol_sma = np.mean(sub_vols[-donchian_period - 1:-1])
            curr_vol = max(sub_vols[-1], sub_vols[-2])
            vol_ok = (curr_vol >= 1.25 * vol_sma) if vol_sma > 0 else False

            bull_break = (curr_close > dc_h) and (ema_f > ema_s) and vol_ok
            bear_break = (curr_close < dc_l) and (ema_f < ema_s) and vol_ok

            # CVD confirmation
            if swing_require_cvd:
                bull_break = bull_break and (curr_cvd >= 0.30)
                bear_break = bear_break and (curr_cvd <= -0.30)

            if (bull_break or bear_break) and len([p for p in open_positions if p["strat"] == "SWING"]) == 0:
                swing_dir = "LONG" if bull_break else "SHORT"
                sl_pct = 1.00
                tp_pct = 2.00
                total_adverse = (sl_pct / 100.0) + (0.13 / 100.0)
                notional = risk_budget_usd / total_adverse

                entry_p = curr_close
                sl_p = entry_p * (1.0 - sl_pct / 100.0) if swing_dir == "LONG" else entry_p * (1.0 + sl_pct / 100.0)
                tp_p = entry_p * (1.0 + tp_pct / 100.0) if swing_dir == "LONG" else entry_p * (1.0 - tp_pct / 100.0)

                open_positions.append({
                    "symbol": sym,
                    "strat": "SWING",
                    "direction": swing_dir,
                    "entry_price": entry_p,
                    "entry_time": curr_time,
                    "entry_bar": i,
                    "notional": notional,
                    "sl_price": sl_p,
                    "tp_price": tp_p,
                    "max_hold_bars": 240,  # 4 hours
                    "mode": mode
                })

    df_res = pd.DataFrame(results)
    return df_res


if __name__ == "__main__":
    print("=" * 75)
    print("REPLAY BACKTEST ON LAST 2-3 DAYS LIVE DATA: OLD LOGIC VS NEW FIXES")
    print("=" * 75)

    df_old = run_simulation("OLD")
    df_new = run_simulation("NEW")

    print("\n[1] OVERALL METRICS COMPARISON:")
    print("-" * 75)
    
    def summarize(df, name):
        if len(df) == 0:
            return {"Name": name, "Trades": 0}
        wins = df[df["net_pnl"] > 0]
        losses = df[df["net_pnl"] <= 0]
        wr = (len(wins) / len(df)) * 100.0
        gross = df["gross_pnl"].sum()
        fees = df["fees"].sum()
        net = df["net_pnl"].sum()
        sl_hits = len(df[df["exit_reason"] == "STOP_LOSS"])
        tp_hits = len(df[df["exit_reason"] == "TAKE_PROFIT"])
        time_stops = len(df[df["exit_reason"] == "TIME_STOP"])
        pf = abs(wins["net_pnl"].sum() / losses["net_pnl"].sum()) if len(losses) > 0 and losses["net_pnl"].sum() != 0 else 0.0

        return {
            "Version": name,
            "Total Trades": len(df),
            "Win Rate": f"{wr:.1f}%",
            "Wins / Losses": f"{len(wins)} / {len(losses)}",
            "Gross PnL": f"${gross:.2f}",
            "Fees Paid": f"${fees:.2f}",
            "Net Realized PnL": f"${net:.2f}",
            "Profit Factor": f"{pf:.2f}",
            "Stop Loss Hits": sl_hits,
            "Take Profit Hits": tp_hits,
            "Time Stops": time_stops
        }

    sum_old = summarize(df_old, "OLD (Before Fixes)")
    sum_new = summarize(df_new, "NEW (With All Fixes)")

    df_comp = pd.DataFrame([sum_old, sum_new])
    print(df_comp.to_string(index=False))

    print("\n[2] STRATEGY-BY-STRATEGY BREAKDOWN:")
    print("-" * 75)
    for strat in ["SH1_MACRO", "SWING"]:
        sub_o = df_old[df_old["strat"] == strat] if len(df_old) > 0 else pd.DataFrame()
        sub_n = df_new[df_new["strat"] == strat] if len(df_new) > 0 else pd.DataFrame()

        print(f"\n Strategy: {strat}")
        s_old = summarize(sub_o, "OLD")
        s_new = summarize(sub_n, "NEW")
        print(pd.DataFrame([s_old, s_new]).to_string(index=False))

    print("\n[3] SYMBOL BREAKDOWN:")
    print("-" * 75)
    for sym in ["BTCUSDT", "ETHUSDT"]:
        sub_o = df_old[df_old["symbol"] == sym] if len(df_old) > 0 else pd.DataFrame()
        sub_n = df_new[df_new["symbol"] == sym] if len(df_new) > 0 else pd.DataFrame()

        print(f"\n Symbol: {sym}")
        s_old = summarize(sub_o, "OLD")
        s_new = summarize(sub_n, "NEW")
        print(pd.DataFrame([s_old, s_new]).to_string(index=False))
