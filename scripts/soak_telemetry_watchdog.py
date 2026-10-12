#!/usr/bin/env python3
"""Market Pilot Crypto: 24–48h Live Soak & Forensic Telemetry Watchdog.

Performs read-only forensic audits of post-deployment trade execution against
the live Oracle VM 2 database (or local mirror):
1. Verifies Maker execution fee reduction (2.0 bps Maker vs 4.0 bps Taker).
2. Audits exit reasons (verifying STAGNATION_EXIT is 0 and observing STI).
3. Tracks isolated desk metrics for Strong Quant ($10k) and Overmind AI ($10k).
4. Analyzes S2 Orderbook Scalper barrier reachability.

Usage:
    python scripts/soak_telemetry_watchdog.py [--remote] [--db-path PATH] [--since-ms EPOCH_MS] [--since-hours H]
"""
import os
import sys
import json
import sqlite3
import argparse
import datetime
import subprocess
from pathlib import Path
from typing import Dict, List, Any, Optional

# Set safe stdout encoding
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Default to Oracle VM 2 deployment timestamp (Mon 2026-10-12 13:50:00 UTC)
DEFAULT_DEPLOY_TS_MS = 1791813000000
DEFAULT_VM2_IP = "129.154.244.236"
DEFAULT_REMOTE_DB = "/home/ubuntu/market-pilot-crypto/data/crypto_research.db"


def ms_to_utc_str(ms: Optional[int]) -> str:
    if not ms:
        return "—"
    try:
        dt = datetime.datetime.fromtimestamp(ms / 1000.0, tz=datetime.timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ms)


def load_trades_from_db(db_path: str, since_ms: int) -> List[Dict[str, Any]]:
    """Reads closed paper positions opened at or after since_ms."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    query = """
        SELECT * FROM crypto_paper_positions
        WHERE opened_at_ms >= ?
        ORDER BY opened_at_ms DESC
    """
    rows = c.execute(query, (since_ms,)).fetchall()
    trades = [dict(r) for r in rows]
    conn.close()
    return trades


def load_trades_remote(ip: str, remote_db: str, since_ms: int) -> List[Dict[str, Any]]:
    """Executes a read-only query over SSH to Oracle VM 2 and parses JSON output."""
    ssh_key = Path.home() / ".ssh" / "market-pilot-oracle"
    if not ssh_key.exists():
        raise FileNotFoundError(f"SSH private key not found at {ssh_key}")

    python_snippet = f"""
import sqlite3, json
conn = sqlite3.connect('{remote_db}')
conn.row_factory = sqlite3.Row
c = conn.cursor()
rows = c.execute('SELECT * FROM crypto_paper_positions WHERE opened_at_ms >= ? ORDER BY opened_at_ms DESC', ({since_ms},)).fetchall()
trades = [dict(r) for r in rows]
conn.close()
print(json.dumps(trades))
"""
    cmd = [
        "ssh",
        "-i", str(ssh_key),
        "-o", "StrictHostKeyChecking=no",
        f"ubuntu@{ip}",
        "python3 -"
    ]
    res = subprocess.run(cmd, input=python_snippet, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    trades = json.loads(res.stdout.strip())
    return trades


def analyze_soak_telemetry(trades: List[Dict[str, Any]], since_ms: int) -> Dict[str, Any]:
    """Computes comprehensive institutional telemetry on post-deploy trades."""
    closed_trades = [t for t in trades if t.get("status") == "CLOSED"]
    open_trades = [t for t in trades if t.get("status") == "OPEN"]

    desks = {"QUANT": [], "OVERMIND": []}
    for t in closed_trades:
        d = (t.get("desk") or "QUANT").upper()
        if d in ("OVERMIND", "SUPERHUMAN", "OVERMIND_AI"):
            desks["OVERMIND"].append(t)
        else:
            desks["QUANT"].append(t)

    # Fee Friction Analysis
    total_entry_fees = 0.0
    total_exit_fees = 0.0
    total_notional = 0.0
    maker_entries = 0
    taker_entries = 0
    entry_bps_list = []

    for t in closed_trades:
        n = float(t.get("notional_usdt") or 0.0)
        e_fee = float(t.get("entry_fee_usdt") or 0.0)
        if n > 0:
            total_notional += n
            total_entry_fees += e_fee
            bps = (e_fee / n) * 10_000.0
            entry_bps_list.append(bps)
            if bps <= 2.5:
                maker_entries += 1
            else:
                taker_entries += 1

    avg_entry_bps = sum(entry_bps_list) / len(entry_bps_list) if entry_bps_list else 0.0
    # Estimated savings: baseline was 4.0 bps taker for all
    estimated_taker_fee_total = (total_notional * 4.0 / 10_000.0)
    actual_fee_savings_usd = max(0.0, estimated_taker_fee_total - total_entry_fees)

    # Exit Reasons Breakdown
    exit_counts = {}
    exit_pnl = {}
    for t in closed_trades:
        reason = t.get("exit_reason") or "UNKNOWN"
        pnl = float(t.get("realised_pnl") or 0.0)
        exit_counts[reason] = exit_counts.get(reason, 0) + 1
        exit_pnl[reason] = exit_pnl.get(reason, 0.0) + pnl

    # S2 Scalper specific metrics
    s2_trades = [t for t in closed_trades if "ORDERBOOK" in (t.get("strategy_id") or "")]
    s2_hold_times_sec = []
    s2_exit_reasons = {}
    for t in s2_trades:
        o = t.get("opened_at_ms") or 0
        c = t.get("closed_at_ms") or 0
        if c > o:
            s2_hold_times_sec.append((c - o) / 1000.0)
        r = t.get("exit_reason") or "UNKNOWN"
        s2_exit_reasons[r] = s2_exit_reasons.get(r, 0) + 1

    desk_stats = {}
    for desk_name, d_trades in desks.items():
        wins = [t for t in d_trades if (t.get("realised_pnl") or 0.0) >= 0.005]
        losses = [t for t in d_trades if (t.get("realised_pnl") or 0.0) <= -0.005]
        bes = [t for t in d_trades if abs(t.get("realised_pnl") or 0.0) < 0.005]
        
        realised_pnl = sum(t.get("realised_pnl") or 0.0 for t in d_trades)
        fees_paid = sum(t.get("total_fees_usdt") or 0.0 for t in d_trades)
        gross_pnl = realised_pnl + fees_paid

        win_rate = (len(wins) / len(d_trades) * 100.0) if d_trades else 0.0
        gross_wins = sum(t.get("realised_pnl") or 0.0 for t in wins)
        gross_loss_abs = abs(sum(t.get("realised_pnl") or 0.0 for t in losses))
        pf = (gross_wins / gross_loss_abs) if gross_loss_abs > 0 else (1.0 if gross_wins > 0 else 0.0)

        desk_stats[desk_name] = {
            "total_trades": len(d_trades),
            "wins": len(wins),
            "losses": len(losses),
            "bes": len(bes),
            "win_rate_pct": win_rate,
            "realised_pnl": realised_pnl,
            "fees_paid": fees_paid,
            "gross_pnl": gross_pnl,
            "profit_factor": pf,
        }

    return {
        "since_ms": since_ms,
        "since_utc": ms_to_utc_str(since_ms),
        "total_opened": len(trades),
        "open_count": len(open_trades),
        "closed_count": len(closed_trades),
        "desk_stats": desk_stats,
        "fee_analytics": {
            "avg_entry_bps": avg_entry_bps,
            "maker_entries": maker_entries,
            "taker_entries": taker_entries,
            "maker_ratio_pct": (maker_entries / len(closed_trades) * 100.0) if closed_trades else 0.0,
            "fee_savings_usd": actual_fee_savings_usd,
            "total_fees_paid": sum(t.get("total_fees_usdt") or 0.0 for t in closed_trades),
        },
        "exit_distribution": {
            "counts": exit_counts,
            "pnl": exit_pnl,
            "stagnation_exits": exit_counts.get("STAGNATION_EXIT", 0),
            "sti_exits": exit_counts.get("STRUCTURAL_THESIS_INVALIDATION", 0),
        },
        "s2_scalper": {
            "trade_count": len(s2_trades),
            "avg_hold_sec": sum(s2_hold_times_sec) / len(s2_hold_times_sec) if s2_hold_times_sec else 0.0,
            "exit_reasons": s2_exit_reasons,
        },
    }


def print_report(res: Dict[str, Any]):
    print("=" * 72)
    print("MARKET PILOT CRYPTO: 24-48h LIVE SOAK FORENSIC TELEMETRY REPORT")
    print("=" * 72)
    print(f"Deployment Baseline Time: {res['since_utc']} (Epoch: {res['since_ms']})")
    print(f"Total Trades Logged:     {res['total_opened']} (Closed: {res['closed_count']}, Active Open: {res['open_count']})")
    print("-" * 72)

    # Desk Isolation Performance
    print("1. DUAL-DESK PERFORMANCE BREAKDOWN:")
    for d_name, s in res["desk_stats"].items():
        print(f"  [{d_name} DESK ($10,000 Capital)]")
        print(f"    Trades: {s['total_trades']} | Wins: {s['wins']} | Losses: {s['losses']} | BE: {s['bes']}")
        wr_flag = "[GOOD]" if s["win_rate_pct"] >= 45.0 else ("[WATCH]" if s["win_rate_pct"] >= 35.0 else "[LOW]")
        print(f"    Win Rate:      {wr_flag} {s['win_rate_pct']:.1f}%")
        pnl_flag = "[POS]" if s["realised_pnl"] >= 0 else "[NEG]"
        print(f"    Gross P&L:     ${s['gross_pnl']:+.2f}")
        print(f"    Fees Paid:     ${s['fees_paid']:.2f}")
        print(f"    Net Realised:  {pnl_flag} ${s['realised_pnl']:+.2f}")
        print(f"    Profit Factor: {s['profit_factor']:.2f}x")
        print()

    # Fee Friction & Maker Execution
    fa = res["fee_analytics"]
    print("2. MICROSTRUCTURE & FEE FRICTION VERIFICATION:")
    maker_flag = "[EXCELLENT]" if fa["maker_ratio_pct"] >= 50.0 else "[MONITORING]"
    print(f"  Maker Fill Ratio:     {maker_flag} {fa['maker_ratio_pct']:.1f}% ({fa['maker_entries']} Maker / {fa['taker_entries']} Taker)")
    print(f"  Avg Entry Friction:   {fa['avg_entry_bps']:.2f} bps (Target: ~2.0 bps Maker vs 4.0 bps Taker)")
    print(f"  Maker Fee Savings:    ${fa['fee_savings_usd']:.2f} preserved from taker friction")
    print(f"  Total Fees Incurred:  ${fa['total_fees_paid']:.2f}")
    print()

    # Exit Reason Distribution & Invariants
    ed = res["exit_distribution"]
    print("3. STRATEGY EXIT DISTRIBUTION & INVARIANT AUDIT:")
    stag_alert = "[VIOLATION] Old code running!" if ed["stagnation_exits"] > 0 else "[CLEAN] Zero Stagnation Exits"
    print(f"  STAGNATION_EXIT Check:    {stag_alert}")
    print(f"  STI (Structural Thesis):  {ed['sti_exits']} trades invalidated on regime/flow inversion")
    print("  Exit Reasons Detail:")
    if ed["counts"]:
        for reason, count in sorted(ed["counts"].items(), key=lambda x: x[1], reverse=True):
            pnl = ed["pnl"].get(reason, 0.0)
            pnl_str = f"${pnl:+.2f}"
            print(f"    * {reason:<30} Count: {count:<4} Net P&L: {pnl_str}")
    else:
        print("    * No closed trades yet in post-deploy window.")
    print()

    # S2 Scalper Analytics
    s2 = res["s2_scalper"]
    print("4. ORDERBOOK SCALPER (STRAT_ORDERBOOK_MOMENTUM_V1) VITALS:")
    print(f"  Trades Evaluated: {s2['trade_count']}")
    if s2["trade_count"] > 0:
        print(f"  Avg Duration:     {s2['avg_hold_sec']:.1f}s ({s2['avg_hold_sec']/60.0:.1f}m / 5.0m max)")
        print(f"  Exit Breakdown:   {dict(s2['exit_reasons'])}")
    else:
        print("  No S2 trades recorded yet in post-deploy window.")
    print("=" * 72)


def main():
    parser = argparse.ArgumentParser(description="Live Soak & Telemetry Watchdog for Market Pilot Crypto")
    parser.add_argument("--remote", action="store_true", help="Query Oracle VM 2 directly via SSH")
    parser.add_argument("--ip", default=DEFAULT_VM2_IP, help="VM 2 IP address")
    parser.add_argument("--db-path", default=None, help="Local path to crypto_research.db")
    parser.add_argument("--since-ms", type=int, default=DEFAULT_DEPLOY_TS_MS, help="Baseline deploy epoch ms")
    parser.add_argument("--since-hours", type=float, default=None, help="Look back N hours instead of default deploy")
    parser.add_argument("--json", action="store_true", help="Output raw JSON instead of report")
    args = parser.parse_args()

    since_ms = args.since_ms
    if args.since_hours is not None:
        since_ms = int((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=args.since_hours)).timestamp() * 1000)

    trades = []
    if args.remote:
        trades = load_trades_remote(args.ip, DEFAULT_REMOTE_DB, since_ms)
    else:
        db_path = args.db_path
        if not db_path:
            candidates = [
                Path("data/crypto_research.db"),
                Path("data/crypto_research_vm2.db"),
                Path(DEFAULT_REMOTE_DB),
            ]
            for c in candidates:
                if c.exists():
                    db_path = str(c)
                    break
        if not db_path or not os.path.exists(db_path):
            # Fall back to remote query automatically if local DB is absent
            print(f"Local database not found. Defaulting to remote Oracle VM 2 query ({args.ip})...", file=sys.stderr)
            trades = load_trades_remote(args.ip, DEFAULT_REMOTE_DB, since_ms)
        else:
            trades = load_trades_from_db(db_path, since_ms)

    res = analyze_soak_telemetry(trades, since_ms)

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print_report(res)


if __name__ == "__main__":
    main()
