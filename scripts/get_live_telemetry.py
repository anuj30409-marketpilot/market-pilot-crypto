import urllib.request
import json
import datetime
import sqlite3

def run():
    # 1. Query Paper Positions & Summary via HTTP
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8800/paper/positions?desk=ALL&version=ALL", timeout=5)
        data = json.loads(req.read().decode("utf-8"))
        summary = data.get("summary", {})
        positions = data.get("positions", [])
        
        print("=" * 70)
        print("LIVE CRYPTO DESK PERFORMANCE SUMMARY (ORACLE VM 2)")
        print("=" * 70)
        print(f"Total Trades: {summary.get('total_trades')} | Open Positions: {summary.get('open_positions')}")
        print(f"Win Rate: {summary.get('win_rate_pct', 0):.1f}% | Profit Factor: {summary.get('profit_factor', 0):.2f}x | Payoff Ratio: {summary.get('payoff_ratio', 0):.2f}:1")
        print(f"Avg Win: ${summary.get('avg_win_usdt', 0):.2f} | Avg Loss: ${summary.get('avg_loss_usdt', 0):.2f}")
        print(f"Realised PnL: ${summary.get('total_realised_pnl', 0):.2f} | Unrealised PnL: ${summary.get('total_unrealised_pnl', 0):.2f}")
        print(f"Total Fees Paid: ${summary.get('total_fees_paid_usdt', 0):.2f} | Net PnL: ${summary.get('net_pnl', 0):.2f}")
        
        q = summary.get("quant", {})
        sh = summary.get("superhuman", {})
        print(f"\n  [Quant Desk]      Trades: {q.get('total_trades', 0):3d} | Win Rate: {q.get('win_rate_pct', 0):.1f}% | PF: {q.get('profit_factor', 0):.2f}x | Net PnL: ${q.get('net_pnl', 0):.2f}")
        print(f"  [Superhuman Desk] Trades: {sh.get('total_trades', 0):3d} | Win Rate: {sh.get('win_rate_pct', 0):.1f}% | PF: {sh.get('profit_factor', 0):.2f}x | Net PnL: ${sh.get('net_pnl', 0):.2f}")

        print("\n" + "-" * 70)
        print("CURRENT OPEN POSITIONS")
        print("-" * 70)
        open_pos = [p for p in positions if p.get("status") == "OPEN"]
        if not open_pos:
            print("  No open positions currently active.")
        for p in open_pos:
            sym = p.get("symbol")
            dirn = p.get("direction")
            notional = p.get("notional_usdt", 0)
            entry = p.get("entry_price", 0)
            mark = p.get("mark_price", 0)
            unreal = p.get("unrealised_pnl", 0)
            roe = p.get("roe_pct", 0)
            strat = p.get("strategy_id", "").replace("STRAT_", "").replace("_V1", "")
            sl = p.get("stop_loss_price")
            tp = p.get("take_profit_price")
            op_dt = datetime.datetime.fromtimestamp(p.get("opened_at_ms", 0)/1000, tz=datetime.timezone.utc).strftime("%H:%M:%S UTC")
            print(f"  {sym} {dirn} (${notional:.0f}) | Strat: {strat} | Entry: {entry:.2f} | Mark: {mark:.2f} | PnL: ${unreal:+.2f} ({roe:+.2f}%) | SL: {sl} | TP: {tp} | Opened: {op_dt}")

    except Exception as e:
        print(f"Error querying summary: {e}")

    # 2. Check Recent Trades Since Last Calibrated Deployment (~14:51 UTC)
    try:
        conn = sqlite3.connect("/home/ubuntu/market-pilot-crypto/data/crypto_research.db")
        c = conn.cursor()
        last_deploy_ms = 1791471100000 # ~14:51:40 UTC
        recent = c.execute("""
            SELECT symbol, direction, desk, strategy_id, entry_price, exit_price, realised_pnl, exit_reason, opened_at_ms, closed_at_ms
            FROM crypto_paper_positions
            WHERE opened_at_ms >= ?
            ORDER BY opened_at_ms DESC
        """, (last_deploy_ms,)).fetchall()
        
        print("\n" + "-" * 70)
        print(f"TRADES OPENED SINCE LATEST CALIBRATED DEPLOYMENT ({len(recent)} total)")
        print("-" * 70)
        if not recent:
            print("  No new trades opened since deployment. System evaluating feeds for high-conviction setups.")
        for r in recent:
            sym, dirn, desk, strat, ep, xp, pnl, rsn, op, cl = r
            strat_short = strat.replace("STRAT_", "").replace("_V1", "")
            dur = (cl - op)/1000/60.0 if cl else 0
            op_dt = datetime.datetime.fromtimestamp(op/1000, tz=datetime.timezone.utc).strftime("%H:%M:%S UTC")
            print(f"  {op_dt} | {sym:8s} {dirn:5s} | {desk:10s} | {strat_short:22s} | Exit: {rsn or 'STILL OPEN':15s} | Dur: {dur:4.1f}m | PnL: ${pnl or 0:+.2f}")

    except Exception as e:
        print(f"Error querying DB: {e}")

    # 3. Market Derivatives Telemetry
    try:
        c = conn.cursor()
        print("\n" + "-" * 70)
        print("LIVE MARKET REGIME & ORDERBOOK TELEMETRY")
        print("-" * 70)
        for sym in ["BTCUSDT", "ETHUSDT"]:
            row = c.execute("""
                SELECT mark_price, index_price, basis_bps, open_interest, funding_rate, funding_zscore_7d, cvd_1m, taker_buy_ratio_1m, state_time_ms
                FROM crypto_derivatives_state
                WHERE symbol = ?
                ORDER BY state_time_ms DESC LIMIT 1
            """, (sym,)).fetchone()
            if row:
                mp, ip, basis, oi, fr, fz, cvd1m, tbr, st_ms = row
                up_dt = datetime.datetime.fromtimestamp(st_ms/1000, tz=datetime.timezone.utc).strftime("%H:%M:%S UTC")
                print(f"  [{sym}] Mark: ${mp:,.2f} | Basis: {basis:+.1f}bps | OI: ${oi:,.0f} | Funding: {fr*100:+.4f}% (z={fz:+.1f}) | CVD 1m: ${cvd1m:,.0f} | Taker Buy: {tbr*100:.1f}% | Time: {up_dt}")
    except Exception as e:
        print(f"Error querying market telemetry: {e}")

if __name__ == "__main__":
    run()
