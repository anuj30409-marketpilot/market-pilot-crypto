"""Advanced Replay Simulator: Comparing Baseline vs Step 1 vs Step 2 vs Step 3 Optimal.

Tests:
1. Baseline (Old Unfixed Logic)
2. Step 1 (Core Fixes: SH1 Direction, 60m Swing, Calibrated S2)
3. Step 2 (Step 1 + Tight Breakeven Stop @ 45 bps - testing if it suffocates crypto trends)
4. Step 3 Optimal (Step 1 + S1 Funding Reversion Unlocked + ETH Short Suppression - Letting trends run)
"""
import sqlite3
import pandas as pd
import numpy as np


def run_full_simulation(mode: str = "STEP3_OPTIMAL"):
    conn = sqlite3.connect("data/crypto_research_vm2.db")
    df_candles = pd.read_sql_query(
        "SELECT symbol, open_time_ms, open, high, low, close, volume FROM crypto_candles_1m ORDER BY open_time_ms ASC", conn
    )
    df_deriv = pd.read_sql_query(
        "SELECT symbol, state_time_ms, mark_price, basis_bps, cvd_notional_usd_zscore, funding_rate, funding_zscore_7d, regime FROM crypto_derivatives_state ORDER BY state_time_ms ASC", conn
    )
    conn.close()

    if mode == "BASELINE":
        donchian_period = 20
        ema_fast_p = 20
        ema_slow_p = 50
        sh1_use_cvd_direction = False
        swing_require_cvd = False
        use_breakeven_stop = False
        enable_s1_funding = False
        suppress_eth_shorts = False
    elif mode == "STEP1_CORE":
        donchian_period = 60
        ema_fast_p = 30
        ema_slow_p = 90
        sh1_use_cvd_direction = True
        swing_require_cvd = True
        use_breakeven_stop = False
        enable_s1_funding = False
        suppress_eth_shorts = False
    elif mode == "STEP2_TIGHT_BE":
        donchian_period = 60
        ema_fast_p = 30
        ema_slow_p = 90
        sh1_use_cvd_direction = True
        swing_require_cvd = True
        use_breakeven_stop = True
        enable_s1_funding = True
        suppress_eth_shorts = True
    else:  # STEP3_OPTIMAL
        donchian_period = 60
        ema_fast_p = 30
        ema_slow_p = 90
        sh1_use_cvd_direction = True
        swing_require_cvd = True
        use_breakeven_stop = False       # Do NOT suffocate crypto trends
        enable_s1_funding = True         # Unlock 81% win rate funding carry
        suppress_eth_shorts = True       # Suppress ETH counter-trend shorts

    risk_budget_usd = 25.0
    roundtrip_fee_bps = 8.0

    symbols = ["BTCUSDT", "ETHUSDT"]
    results = []

    for sym in symbols:
        sym_candles = df_candles[df_candles["symbol"] == sym].sort_values("open_time_ms").reset_index(drop=True)
        sym_deriv = df_deriv[df_deriv["symbol"] == sym].sort_values("state_time_ms").reset_index(drop=True)

        merged = pd.merge_asof(
            sym_candles,
            sym_deriv[["state_time_ms", "basis_bps", "cvd_notional_usd_zscore", "funding_rate", "funding_zscore_7d", "regime"]],
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
        funding_zs = merged["funding_zscore_7d"].fillna(0.0).tolist()
        basis_bps_list = merged["basis_bps"].fillna(0.0).tolist()
        regimes = merged["regime"].fillna("RANGE").tolist()
        times = merged["open_time_ms"].tolist()

        n_bars = len(closes)
        open_positions = []
        min_bars = max(ema_slow_p, donchian_period) + 15

        for i in range(min_bars, n_bars):
            curr_time = times[i]
            curr_close = closes[i]
            curr_high = highs[i]
            curr_low = lows[i]
            curr_cvd = cvd_zs[i]
            curr_f_z = funding_zs[i]
            curr_basis = basis_bps_list[i]
            curr_regime = regimes[i]

            # 1. Update Open Positions
            active = []
            for pos in open_positions:
                entry_p = pos["entry_price"]
                direction = pos["direction"]
                sl_p = pos["sl_price"]
                tp_p = pos["tp_price"]
                bars_held = i - pos["entry_bar"]
                max_bars = pos["max_hold_bars"]

                # Breakeven Stop Ratchet
                if use_breakeven_stop and not pos.get("breakeven_triggered", False):
                    if direction == "LONG":
                        floating_gain_bps = ((curr_high - entry_p) / entry_p) * 10000.0
                        if floating_gain_bps >= 45.0:
                            pos["sl_price"] = entry_p * (1.0 + 0.0010)
                            pos["breakeven_triggered"] = True
                            sl_p = pos["sl_price"]
                    else:  # SHORT
                        floating_gain_bps = ((entry_p - curr_low) / entry_p) * 10000.0
                        if floating_gain_bps >= 45.0:
                            pos["sl_price"] = entry_p * (1.0 - 0.0010)
                            pos["breakeven_triggered"] = True
                            sl_p = pos["sl_price"]

                closed = False
                exit_price = curr_close
                exit_reason = None

                if direction == "LONG":
                    if curr_low <= sl_p:
                        closed = True
                        exit_price = sl_p
                        exit_reason = "BREAKEVEN_STOP" if pos.get("breakeven_triggered", False) else "STOP_LOSS"
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
                        exit_reason = "BREAKEVEN_STOP" if pos.get("breakeven_triggered", False) else "STOP_LOSS"
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

            # Concurrency limit (max 2 per symbol)
            if len(open_positions) >= 2:
                continue

            sub_closes = closes[:i+1]
            sub_highs = highs[:i+1]
            sub_lows = lows[:i+1]
            sub_vols = volumes[:i+1]

            # Strategy 1: SH1 Superhuman Macro Impulse
            can_fire_sh1 = (abs(curr_cvd) >= 1.60)
            if can_fire_sh1 and len([p for p in open_positions if p["strat"] == "SH1"]) == 0:
                if sh1_use_cvd_direction:
                    sh1_dir = "LONG" if curr_cvd >= 0 else "SHORT"
                else:
                    sh1_dir = "LONG"

                skip = False
                if suppress_eth_shorts and sym == "ETHUSDT" and sh1_dir == "SHORT" and curr_f_z >= 0:
                    skip = True

                if not skip:
                    sl_pct = 0.80
                    tp_pct = 1.60
                    notional = risk_budget_usd / ((sl_pct / 100.0) + 0.0013)
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
                        "max_hold_bars": 120,
                        "mode": mode
                    })

            # Strategy 2: S4 Swing Momentum Breakout
            dc_h = max(sub_highs[-donchian_period - 1:-1])
            dc_l = min(sub_lows[-donchian_period - 1:-1])

            k_f = 2.0 / (ema_fast_p + 1)
            ema_f = sub_closes[0]
            for p in sub_closes[1:]:
                ema_f = p * k_f + ema_f * (1.0 - k_f)

            k_s = 2.0 / (ema_slow_p + 1)
            ema_s = sub_closes[0]
            for p in sub_closes[1:]:
                ema_s = p * k_s + ema_s * (1.0 - k_s)

            vol_sma = np.mean(sub_vols[-donchian_period - 1:-1])
            curr_vol = max(sub_vols[-1], sub_vols[-2])
            vol_ok = (curr_vol >= 1.25 * vol_sma) if vol_sma > 0 else False

            bull_break = (curr_close > dc_h) and (ema_f > ema_s) and vol_ok
            bear_break = (curr_close < dc_l) and (ema_f < ema_s) and vol_ok

            if swing_require_cvd:
                bull_break = bull_break and (curr_cvd >= 0.30)
                bear_break = bear_break and (curr_cvd <= -0.30)

            if (bull_break or bear_break) and len([p for p in open_positions if p["strat"] == "SWING"]) == 0:
                swing_dir = "LONG" if bull_break else "SHORT"
                skip = False
                if suppress_eth_shorts and sym == "ETHUSDT" and swing_dir == "SHORT" and curr_f_z >= 0:
                    skip = True

                if not skip:
                    sl_pct = 1.00
                    tp_pct = 2.00
                    notional = risk_budget_usd / ((sl_pct / 100.0) + 0.0013)
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
                        "max_hold_bars": 240,
                        "mode": mode
                    })

            # Strategy 3: S1 Funding & Basis Mean-Reversion (Unlocked)
            if enable_s1_funding and len([p for p in open_positions if p["strat"] == "S1_FUNDING"]) == 0:
                is_long_fund = (curr_f_z <= -1.8) and (curr_basis <= -1.5)
                is_short_fund = (curr_f_z >= 1.8) and (curr_basis >= 1.5)

                if is_long_fund or is_short_fund:
                    f_dir = "LONG" if is_long_fund else "SHORT"
                    sl_pct = 0.80
                    tp_pct = 1.50
                    notional = risk_budget_usd / ((sl_pct / 100.0) + 0.0013)
                    entry_p = curr_close
                    sl_p = entry_p * (1.0 - sl_pct / 100.0) if f_dir == "LONG" else entry_p * (1.0 + sl_pct / 100.0)
                    tp_p = entry_p * (1.0 + tp_pct / 100.0) if f_dir == "LONG" else entry_p * (1.0 - tp_pct / 100.0)

                    open_positions.append({
                        "symbol": sym,
                        "strat": "S1_FUNDING",
                        "direction": f_dir,
                        "entry_price": entry_p,
                        "entry_time": curr_time,
                        "entry_bar": i,
                        "notional": notional,
                        "sl_price": sl_p,
                        "tp_price": tp_p,
                        "max_hold_bars": 480,
                        "mode": mode
                    })

    return pd.DataFrame(results)


def summarize(df, name):
    if len(df) == 0:
        return {"Version": name, "Trades": 0}
    wins = df[df["net_pnl"] > 0]
    losses = df[df["net_pnl"] <= 0]
    wr = (len(wins) / len(df)) * 100.0
    gross = df["gross_pnl"].sum()
    fees = df["fees"].sum()
    net = df["net_pnl"].sum()
    sl_hits = len(df[df["exit_reason"] == "STOP_LOSS"])
    be_hits = len(df[df["exit_reason"] == "BREAKEVEN_STOP"])
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
        "Hard Stop-Outs": sl_hits,
        "Take Profit Hits": tp_hits,
        "Time Stops": time_stops
    }


if __name__ == "__main__":
    print("=" * 95)
    print("SIMULATION COMPARISON ACROSS SESSIONS: BASELINE vs STEP 1 vs STEP 3 OPTIMAL")
    print("=" * 95)

    df_base = run_full_simulation("BASELINE")
    df_step1 = run_full_simulation("STEP1_CORE")
    df_opt = run_full_simulation("STEP3_OPTIMAL")

    s_base = summarize(df_base, "1. Baseline (Unfixed)")
    s_s1 = summarize(df_step1, "2. Step 1 (Core Fixes)")
    s_opt = summarize(df_opt, "3. Step 3 (Optimal Alpha)")

    df_comp = pd.DataFrame([s_base, s_s1, s_opt])
    print("\n[1] THREE-TIER ENGINE EVOLUTION:")
    print("-" * 95)
    print(df_comp.to_string(index=False))

    print("\n[2] OPTIMAL ENGINE STRATEGY ATTRIBUTION:")
    print("-" * 95)
    for strat in ["SH1_MACRO", "SWING", "S1_FUNDING"]:
        sub = df_opt[df_opt["strat"] == strat] if len(df_opt) > 0 else pd.DataFrame()
        if len(sub) > 0:
            print(f" Strategy: {strat:<12} | Trades: {len(sub):>2} | WinRate: {round((sub['net_pnl']>0).mean()*100, 1):>5}% | Gross: ${round(sub['gross_pnl'].sum(), 2):>7.2f} | Net: ${round(sub['net_pnl'].sum(), 2):>7.2f}")

    print("\n[3] SESSION-BY-SESSION BREAKDOWN (OPTIMAL ENGINE):")
    print("-" * 95)
    df_opt["session_day"] = pd.to_datetime(df_opt["entry_time"], unit="ms").dt.strftime("%Y-%m-%d")
    agg_opt = df_opt.groupby("session_day").agg(
        trades=("net_pnl", "count"),
        win_rate=("net_pnl", lambda x: f"{round((x > 0).mean()*100, 1)}%"),
        gross_pnl=("gross_pnl", lambda x: round(x.sum(), 2)),
        fees=("fees", lambda x: round(x.sum(), 2)),
        net_pnl=("net_pnl", lambda x: round(x.sum(), 2)),
        hard_sl=("exit_reason", lambda x: (x == "STOP_LOSS").sum()),
        tp_hits=("exit_reason", lambda x: (x == "TAKE_PROFIT").sum())
    )
    print(agg_opt.to_string())
