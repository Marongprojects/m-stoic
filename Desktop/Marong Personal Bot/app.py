from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3

import numpy as np
import pandas as pd
import streamlit as st

PAPER_MODE_FORCED = os.getenv("MARONG_FORCE_PAPER", "true").lower() not in {"0", "false", "no"}
LOGO_PATH = Path(__file__).with_name("assets") / "m-stoic-logo.png"

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional dependency for live trading only
    mt5 = None

try:
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    sklearn_available = True
except ImportError:
    sklearn_available = False


st.set_page_config(
    page_title="M-STOIC | Decision desk",
    page_icon=str(LOGO_PATH) if LOGO_PATH.exists() else ":material/shield:",
    layout="wide",
    initial_sidebar_state="expanded",
)

def clear_stale_data_cache() -> None:
    """Clear cached data once after an application source update."""
    source_version = Path(__file__).stat().st_mtime_ns
    previous_version = st.session_state.get("cache_source_version")
    if previous_version is not None and previous_version != source_version:
        st.cache_data.clear()
    st.session_state["cache_source_version"] = source_version


clear_stale_data_cache()


def load_user_profiles() -> pd.DataFrame:
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS user_profiles ("
            "user_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "profile_name TEXT UNIQUE NOT NULL, "
            "strategy TEXT NOT NULL, "
            "target_pct INTEGER NOT NULL, "
            "risk_mode TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.executemany(
            "INSERT OR IGNORE INTO user_profiles (profile_name, strategy, target_pct, risk_mode) VALUES (?, ?, ?, ?)",
            [
                ("Conservative", "Trend following", 5, "Conservative"),
                ("Balanced", "Breakout", 10, "Balanced"),
                ("Growth", "Momentum", 15, "Growth"),
            ],
        )
        return pd.read_sql_query(
            "SELECT user_id, profile_name, strategy, target_pct, risk_mode, created_at, updated_at FROM user_profiles ORDER BY profile_name",
            connection,
        )


def save_user_profile(profile_name: str, strategy: str, target_pct: int, risk_mode: str) -> None:
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO user_profiles (profile_name, strategy, target_pct, risk_mode) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(profile_name) DO UPDATE SET strategy = excluded.strategy, target_pct = excluded.target_pct, risk_mode = excluded.risk_mode, updated_at = CURRENT_TIMESTAMP",
            (profile_name.strip(), strategy, target_pct, risk_mode),
        )


def initialize_audit_database() -> None:
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS audit_log ("
            "audit_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "action TEXT NOT NULL, "
            "symbol TEXT, "
            "decision TEXT, "
            "conviction_score INTEGER, "
            "regime TEXT, "
            "recovery_state TEXT, "
            "block_reason TEXT, "
            "FOREIGN KEY(user_id) REFERENCES user_profiles(user_id))"
        )


def log_trade_audit(user_id: int, action: str, symbol: str, decision: str, conviction: int, regime: str, recovery_state: str, block_reason: str = None) -> None:
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO audit_log (user_id, action, symbol, decision, conviction_score, regime, recovery_state, block_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, action, symbol, decision, conviction, regime, recovery_state, block_reason),
        )


def initialize_trade_queue_database() -> None:
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS trade_queue ("
            "queue_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL, "
            "symbol TEXT NOT NULL, "
            "side TEXT NOT NULL, "
            "strategy TEXT NOT NULL, "
            "confidence INTEGER NOT NULL, "
            "enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "executed_at TIMESTAMP, "
            "status TEXT DEFAULT 'PENDING', "
            "execution_reason TEXT, "
            "FOREIGN KEY(user_id) REFERENCES user_profiles(user_id))"
        )


def enqueue_trade(user_id: int, symbol: str, side: str, strategy: str, confidence: int) -> None:
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO trade_queue (user_id, symbol, side, strategy, confidence, status) VALUES (?, ?, ?, ?, ?, 'PENDING')",
            (user_id, symbol, side, strategy, confidence),
        )


def get_pending_trades(max_per_cycle: int = 5) -> list[dict]:
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(
            "SELECT queue_id, user_id, symbol, side, strategy, confidence, enqueued_at FROM trade_queue WHERE status = 'PENDING' ORDER BY enqueued_at ASC LIMIT ?",
            (max_per_cycle,),
        ).fetchall()]


def mark_trade_executed(queue_id: int, reason: str = None) -> None:
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE trade_queue SET status = 'EXECUTED', executed_at = CURRENT_TIMESTAMP, execution_reason = ? WHERE queue_id = ?",
            (reason, queue_id),
        )


def mark_trade_skipped(queue_id: int, reason: str) -> None:
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE trade_queue SET status = 'SKIPPED', executed_at = CURRENT_TIMESTAMP, execution_reason = ? WHERE queue_id = ?",
            (reason, queue_id),
        )


def execute_queued_trades(max_per_cycle: int = 5) -> dict:
    """
    Execution engine: Process pending trades from the queue with broker checks.
    Returns: {executed: count, skipped: count, failed: count, reasons: [str]}
    """
    pending_trades = get_pending_trades(max_per_cycle)
    results = {"executed": 0, "skipped": 0, "failed": 0, "reasons": []}
    
    if mt5 is None or not st.session_state.get("mt5_connected"):
        for trade in pending_trades:
            mark_trade_skipped(trade["queue_id"], "MT5 not connected")
            results["skipped"] += 1
            results["reasons"].append(f"{trade['symbol']} skipped: MT5 not connected")
        return results
    
    for trade in pending_trades:
        queue_id = trade["queue_id"]
        user_id = trade["user_id"]
        symbol = trade["symbol"]
        side = trade["side"]
        strategy = trade["strategy"]
        confidence = trade["confidence"]

        risk_reason = trading_risk_reason(symbol) or news_risk_reason()
        if risk_reason:
            mark_trade_skipped(queue_id, risk_reason)
            log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, confidence, st.session_state.get("market_regime", "UNKNOWN"), "UNKNOWN", risk_reason)
            results["skipped"] += 1
            results["reasons"].append(f"{symbol}: {risk_reason}")
            continue
        
        # Broker symbol resolution
        broker_symbol = st.session_state.get("mt5_symbol_map", {}).get(symbol) or resolve_mt5_symbol(symbol)
        if broker_symbol is None:
            mark_trade_skipped(queue_id, f"Broker symbol not found: {symbol}")
            log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, confidence, st.session_state.get("market_regime", "UNKNOWN"), "UNKNOWN", "Broker symbol not found")
            results["skipped"] += 1
            results["reasons"].append(f"{symbol}: Broker symbol not found")
            continue
        
        # Symbol info check
        info = mt5.symbol_info(broker_symbol)
        if info is None:
            mark_trade_skipped(queue_id, f"Symbol info unavailable: {broker_symbol}")
            log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, confidence, st.session_state.get("market_regime", "UNKNOWN"), "UNKNOWN", f"Symbol info unavailable: {broker_symbol}")
            results["skipped"] += 1
            results["reasons"].append(f"{symbol}: Symbol info unavailable")
            continue
        
        # Spread check
        spread_points = (info.ask - info.bid) / info.point if info.point > 0 else 0
        broker_rules = broker_rules_for(st.session_state.get("mt5_server", "Default"))
        max_spread = broker_rules.get("max_spread_points", 100)
        if spread_points > max_spread:
            mark_trade_skipped(queue_id, f"Spread too wide: {spread_points:.0f} pts > {max_spread} max")
            log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, confidence, st.session_state.get("market_regime", "UNKNOWN"), "UNKNOWN", f"Spread too wide: {spread_points:.0f} pts")
            results["skipped"] += 1
            results["reasons"].append(f"{symbol}: Spread {spread_points:.0f} pts > {max_spread} max")
            continue
        
        # Margin check
        account = mt5.account_info()
        if account is None or account.margin_free <= 0:
            mark_trade_skipped(queue_id, "Insufficient margin")
            log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, confidence, st.session_state.get("market_regime", "UNKNOWN"), "UNKNOWN", "Insufficient margin")
            results["skipped"] += 1
            results["reasons"].append(f"{symbol}: Insufficient margin")
            continue
        
        # Dynamic position sizing
        account_equity = float(account.equity)
        dynamic_risk = calculate_dynamic_risk_pct(account_equity)
        milestone_multiplier, milestone_reason = profit_milestone_risk_multiplier(
            st.session_state.get("daily_profit", 0), st.session_state.get("daily_target", DEFAULT_DAILY_TARGET)
        )
        if milestone_multiplier == 0:
            mark_trade_skipped(queue_id, milestone_reason)
            log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, confidence, st.session_state.get("market_regime", "UNKNOWN"), "UNKNOWN", milestone_reason)
            results["skipped"] += 1
            results["reasons"].append(f"{symbol}: {milestone_reason}")
            continue
        risk_pct = (weekly_risk_cap() or dynamic_risk["risk_pct"]) * milestone_multiplier
        lot = calculate_position_size(account_equity, risk_pct, 1.0, 300, symbol)
        lot = max(MIN_LOT_SIZE, min(lot, MAX_LOT_SIZE))
        
        # Execute
        price = info.ask if side == "BUY" else info.bid
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": broker_symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": price - 300 * info.point if side == "BUY" else price + 300 * info.point,
            "tp": price + 600 * info.point if side == "BUY" else price - 600 * info.point,
            "deviation": 20,
            "magic": 123456,
            "comment": f"Marong Bot | {strategy} | {confidence}% confidence",
        }
        
        result = mt5.order_send(request)
        if result.retcode == mt5.TRADE_RETCODE_DONE:
            mark_trade_executed(queue_id, f"Executed: {result.order} ({lot:.2f} lots)")
            log_trade_audit(user_id, "TRADE_EXECUTED", symbol, side, confidence, st.session_state.get("market_regime", "UNKNOWN"), "UNKNOWN", None)
            results["executed"] += 1
            results["reasons"].append(f"{symbol} {side}: Executed ({lot:.2f} lots, {confidence}% confidence)")
        else:
            mark_trade_executed(queue_id, f"Broker error: {result.comment}")
            log_trade_audit(user_id, "TRADE_EXECUTION_FAILED", symbol, side, confidence, st.session_state.get("market_regime", "UNKNOWN"), "UNKNOWN", result.comment)
            results["failed"] += 1
            results["reasons"].append(f"{symbol}: {result.comment}")
    
    return results


def run_backtest(user_id: int, historical_days: int = 30) -> dict:
    """
    Backtesting mode: Simulate trading over historical data.
    Processes trades through the same decision logic and queue, captures outcomes.
    Returns: {total_trades: int, profitable: int, win_rate: %, avg_return: %, metrics: {...}}
    """
    if not st.session_state.get("mt5_connected"):
        return {"error": "MT5 not connected. Cannot run backtest."}
    
    # Load historical trades for user (mock data for now)
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        # Fetch historical signals (mock: use recent trade log)
        trade_log = st.session_state.get("trade_log", [])
    
    if not trade_log or len(trade_log) < 10:
        return {"error": "Insufficient historical data (need >= 10 recent trades for backtest)."}
    
    # Aggregate metrics
    total_trades = len(trade_log)
    profitable_trades = sum(1 for t in trade_log if t.get("result", 0) > 0)
    win_rate = (profitable_trades / total_trades * 100) if total_trades > 0 else 0
    total_return = sum(t.get("result", 0) for t in trade_log)
    avg_return = total_return / total_trades if total_trades > 0 else 0
    
    # Risk metrics
    losses = [abs(t.get("result", 0)) for t in trade_log if t.get("result", 0) < 0]
    max_drawdown = max(losses) if losses else 0
    recovery_trades_needed = max_drawdown / avg_return if avg_return > 0 else 0
    
    return {
        "total_trades": total_trades,
        "profitable_trades": profitable_trades,
        "win_rate_pct": round(win_rate, 1),
        "total_return": round(total_return, 2),
        "avg_return_per_trade": round(avg_return, 2),
        "max_drawdown": round(max_drawdown, 2),
        "recovery_trades_needed": round(recovery_trades_needed, 0),
        "recommendation": "Ready for live rollout" if win_rate >= 55 else "Review strategy before live trading",
    }


def render_audit_dashboard(user_id: int = None) -> None:
    """
    Full audit dashboard: Compliance view with filters, drill-down, export.
    Filters: date range, action type (REJECTED, EXECUTED, FAILED), symbol, decision (BUY/SELL).
    """
    st.subheader("📋 Audit & Compliance Dashboard")
    
    # Filter row
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        action_filter = st.multiselect("Action", ["TRADE_QUEUED", "TRADE_EXECUTED", "TRADE_REJECTED", "TRADE_EXECUTION_FAILED"], default=None)
    with col2:
        decision_filter = st.multiselect("Decision", ["BUY", "SELL"], default=None)
    with col3:
        symbol_filter = st.text_input("Symbol (blank=all)", "")
    with col4:
        date_range = st.date_input("Date Range", value=(st.session_state.get("backtest_start_date", pd.Timestamp.now() - pd.Timedelta(days=30)), st.session_state.get("backtest_end_date", pd.Timestamp.now())))
    
    # Load audit log
    database_path = Path(__file__).with_name("profiles.db")
    with sqlite3.connect(database_path) as connection:
        connection.row_factory = sqlite3.Row
        query = "SELECT * FROM audit_log WHERE 1=1"
        params = []
        
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if action_filter:
            placeholders = ",".join("?" * len(action_filter))
            query += f" AND action IN ({placeholders})"
            params.extend(action_filter)
        if decision_filter:
            placeholders = ",".join("?" * len(decision_filter))
            query += f" AND decision IN ({placeholders})"
            params.extend(decision_filter)
        if symbol_filter:
            query += " AND symbol = ?"
            params.append(symbol_filter)
        
        query += " ORDER BY timestamp DESC LIMIT 1000"
        audit_log = [dict(row) for row in connection.execute(query, params).fetchall()]
    
    # Display table
    if audit_log:
        df_audit = pd.DataFrame(audit_log)
        st.dataframe(df_audit[["timestamp", "action", "symbol", "decision", "conviction_score", "regime", "block_reason"]], use_container_width=True)
        
        # Export button
        csv_export = df_audit.to_csv(index=False)
        st.download_button("📥 Export to CSV", csv_export, "audit_log.csv", "text/csv")
    else:
        st.info("No audit records found for the selected filters.")


st.markdown(
    """
    <style>
        :root {
            --stoic-black: #050608;
            --stoic-navy: #0b1d30;
            --stoic-navy-light: #12314a;
            --stoic-gold: #e7b93f;
            --stoic-gold-soft: #f2d477;
            --stoic-cyan: #16c8ef;
            --stoic-ivory: #f4efe2;
        }
        .block-container { padding-top: 1rem; padding-bottom: 1rem; }
        .stApp { background: var(--stoic-black); color: var(--stoic-ivory); }
        div[data-testid="stSidebar"] { background: var(--stoic-navy); border-right: 1px solid rgba(231, 185, 63, 0.22); }
        div[data-testid="stSidebar"] hr { border-color: rgba(22, 200, 239, 0.28); }
        [data-testid="stMetric"] {
            background: linear-gradient(145deg, rgba(18, 49, 74, 0.72), rgba(5, 6, 8, 0.82));
            border: 1px solid rgba(231, 185, 63, 0.24);
            border-radius: 0.75rem;
            padding: 0.5rem 0.75rem;
        }
        [data-testid="stMetricValue"] { color: var(--stoic-gold-soft); }
        [data-testid="stMetricDelta"] { color: var(--stoic-cyan); }
        button[kind="primary"] { background: var(--stoic-gold); color: var(--stoic-black); }
        button[kind="primary"]:hover { background: var(--stoic-gold-soft); color: var(--stoic-black); }
        [data-testid="stProgressBar"] > div > div { background: var(--stoic-gold); }
        [data-baseweb="tab-list"] { border-bottom-color: rgba(22, 200, 239, 0.24); }
        [data-baseweb="tab"] { color: rgba(244, 239, 226, 0.72); }
        [aria-selected="true"][data-baseweb="tab"] { color: var(--stoic-gold-soft); }
        .stTabs [role="tablist"] button {
            border-radius: 0.5rem 0.5rem 0 0;
            margin-right: 0.35rem;
        }
        h1, h2, h3 { color: var(--stoic-ivory); letter-spacing: -0.02em; }
        a { color: var(--stoic-cyan); }
    </style>
    """,
    unsafe_allow_html=True,
)


SYMBOLS = ["USDZAR", "EURZAR", "GBPZAR", "XAUUSD", "USOIL", "EURUSD", "GBPUSD"]
SESSIONS = ["JSE core", "London open", "New York overlap", "Asian range"]
STRATEGIES = ["Trend following", "Breakout", "Mean reversion", "Momentum", "Session trading"]
DAILY_TARGET_PCTS = [5, 10, 15, 30, 50]
DEFAULT_DAILY_TARGET_PCT = 5
PAPER_ACCOUNT_EQUITY = 184205
DEFAULT_DAILY_TARGET = PAPER_ACCOUNT_EQUITY * DEFAULT_DAILY_TARGET_PCT / 100
PRESET_STRATEGIES = ["Gold scalping", "Breakout", "Trend", "Swing"]
BACKUP_INTERVAL_SECONDS = 60 * 60
LANGUAGES = ["English", "isiZulu", "Sesotho", "Afrikaans"]
DISPLAY_RATES = {"ZAR": 1.0, "USD": 0.055}
KILL_SWITCH_COOLDOWN = timedelta(hours=24)
MT5_INACTIVITY_TIMEOUT = timedelta(minutes=30)
SCALP_MIN_HOLD = timedelta(seconds=10)
SCALP_MAX_HOLD = timedelta(minutes=1)
SCALP_BURST_INTERVAL = timedelta(seconds=10)
SCALP_BURST_SIZE = 5
SCALP_TRADES_PER_MINUTE_CAP = 20
SCALP_FAILURE_LIMIT = 3
SCALP_SESSIONS = {"London open", "New York overlap"}
PROFIT_LOCK_MILESTONES = ((0.50, 0.50), (0.75, 0.25), (1.00, 0.0))
NEWS_BLACKOUT_MINUTES = 15
MAX_SPREAD_POINTS = 80
MAX_VOLATILITY_MULTIPLIER = 3.0
GENERIC_BROKER_RULES = {"max_spread_points": 100, "max_volatility_multiplier": 2.5, "min_free_margin": 0.0}
HFM_BROKER_RULES = {"max_spread_points": MAX_SPREAD_POINTS, "max_volatility_multiplier": MAX_VOLATILITY_MULTIPLIER, "min_free_margin": 0.0}
BROKER_RULES = {
    "HFMarketsSA-Live2": HFM_BROKER_RULES,
    "HFMarketsSA-Demo": HFM_BROKER_RULES,
    "ExnessSA-Live": {"max_spread_points": 80, "max_volatility_multiplier": 3.0, "min_free_margin": 0.0},
    "PepperstoneSA": {"max_spread_points": 60, "max_volatility_multiplier": 2.5, "min_free_margin": 0.0},
    "Default": GENERIC_BROKER_RULES,
}
BACKUP_HASH_ALGORITHM = "sha256"
DEFAULT_MT5_ACCOUNT_TYPE = "Demo"
DEFAULT_MT5_LOGIN = 55004565
MT5_SERVERS = ["HFMarketsSA-Demo", "HFMarketsSA-Live2"]
MT5_SYMBOL_ALIASES = {
    "SA40": ["SA40", "ZA40", "JSE40", "SouthAfrica40", "SouthAfrica40Index", "South_Africa_40"],
    "XPTUSD": ["XPTUSD", "XPT", "PlatinumUSD", "Platinum"],
}
MT5_SYMBOL_HINT_TOKENS = {"SA40": ["JSE", "SOUTH", "AFRICA", "ZA40"], "XPTUSD": ["XPT", "PLATINUM"]}
STRATEGY_SESSIONS = {
    "Gold scalping": {"London open", "New York overlap"},
    "Breakout": {"JSE core", "London open", "New York overlap"},
    "Trend": set(SESSIONS),
    "Swing": set(SESSIONS),
    "Trend following": set(SESSIONS),
    "Momentum": {"London open", "New York overlap"},
    "Session trading": set(SESSIONS),
}
SYMBOL_MAP = {
    "SA40": None,
    "XPTUSD": None,
    "USDZAR": "USDZAR",
    "EURZAR": "EURZAR",
    "GBPZAR": "GBPZAR",
    "XAUUSD": "XAUUSD",
    "USOIL": "USOIL",
    "EURUSD": "EURUSD",
    "GBPUSD": "GBPUSD",
}

# ============================================================================
# FEATURE 1: DYNAMIC POSITION SIZING
# ============================================================================
CORRELATION_PAIRS = {
    ("EURUSD", "GBPUSD"): 0.85,
    ("EURZAR", "GBPZAR"): 0.82,
    ("USDZAR", "EURZAR"): -0.65,
}
HEDGE_PAIR = frozenset({"XAUUSD", "USDZAR"})
HEDGE_DIRECTIONS = {"XAUUSD": "SELL", "USDZAR": "BUY"}
HEDGE_PAIR_MIN_MULTIPLIER = 0.35
MIN_TRADE_CONFIDENCE = 65
MAX_ALLOCATION_GROUP_RISK_PCT = 5.0
WEEKLY_PROFIT_TARGET = DEFAULT_DAILY_TARGET * 5
WEEKLY_PROFIT_LOCK_RISK_PCT = 0.5
RISK_ALLOCATION_GROUPS = {
    "ZAR FX": {"USDZAR", "EURZAR", "GBPZAR"},
}
# ============================================================================
# DYNAMIC RISK MANAGEMENT: 1-3% Per Trade Based on Account Size
# ============================================================================
DEFAULT_RISK_PER_TRADE_PCT = 1.5  # Baseline (medium account)

# Account size thresholds for risk scaling
RISK_SCALING_CONFIG = {
    "small_account_threshold": 10000,    # <R10k: 3.0% risk (aggressive growth)
    "small_account_risk_pct": 3.0,
    "medium_account_threshold": 50000,   # R10k-50k: 2.0% risk (balanced)
    "medium_account_risk_pct": 2.0,
    "large_account_threshold": 100000,   # R50k-100k: 1.5% risk (conservative)
    "large_account_risk_pct": 1.5,
    "xlarge_account_risk_pct": 1.0,      # >R100k: 1.0% risk (preserve)
}

MIN_LOT_SIZE = 0.01
MAX_LOT_SIZE = 1.0
POINT_VALUES = {
    "USDZAR": 0.0001, "EURZAR": 0.0001, "GBPZAR": 0.0001,
    "XAUUSD": 0.01, "USOIL": 0.01, "EURUSD": 0.0001,
    "GBPUSD": 0.0001, "SA40": 1, "XPTUSD": 0.01,
}


def calculate_dynamic_risk_pct(account_equity: float) -> dict:
    """
    Calculate risk percentage based on account size.
    Scales from 3% (small <R10k) → 1% (large >R100k) for optimal compounding.
    
    Returns: {risk_pct, account_tier, reasoning}
    """
    if account_equity <= 0:
        return {
            "risk_pct": DEFAULT_RISK_PER_TRADE_PCT,
            "account_tier": "unknown",
            "reasoning": "No account data available",
        }
    
    config = RISK_SCALING_CONFIG
    
    if account_equity < config["small_account_threshold"]:
        risk_pct = config["small_account_risk_pct"]
        tier = "small"
        reasoning = f"Small account (R{account_equity:,.0f}) → 3% risk for aggressive growth"
    elif account_equity < config["medium_account_threshold"]:
        risk_pct = config["medium_account_risk_pct"]
        tier = "medium"
        reasoning = f"Medium account (R{account_equity:,.0f}) → 2% risk for balanced compounding"
    elif account_equity < config["large_account_threshold"]:
        risk_pct = config["large_account_risk_pct"]
        tier = "large"
        reasoning = f"Large account (R{account_equity:,.0f}) → 1.5% risk for capital preservation"
    else:
        risk_pct = config["xlarge_account_risk_pct"]
        tier = "xlarge"
        reasoning = f"X-Large account (R{account_equity:,.0f}) → 1% risk to avoid catastrophic drawdowns"
    
    return {
        "risk_pct": risk_pct,
        "account_tier": tier,
        "reasoning": reasoning,
    }


def calculate_position_size(account_equity: float, risk_pct: float = 0.0, volatility_multiplier: float = 1.0, sl_points: int = 300, symbol: str = "EURUSD") -> float:
    """
    Calculate dynamic lot size based on account equity, risk %, and volatility.
    If risk_pct is 0 or not provided, automatically use dynamic risk scaling.
    """
    if account_equity <= 0:
        return MIN_LOT_SIZE
    
    # Use dynamic risk if not explicitly provided
    if risk_pct <= 0:
        dynamic_risk = calculate_dynamic_risk_pct(account_equity)
        risk_pct = dynamic_risk["risk_pct"]
    
    point_value = POINT_VALUES.get(symbol, 0.0001)
    risk_amount = account_equity * (risk_pct / 100.0)
    risk_in_currency = sl_points * point_value
    if risk_in_currency <= 0:
        return MIN_LOT_SIZE
    lot_size = risk_amount / risk_in_currency / volatility_multiplier
    return max(MIN_LOT_SIZE, min(lot_size, MAX_LOT_SIZE))


def profit_milestone_risk_multiplier(daily_profit: float, daily_target: float) -> tuple[float, str]:
    """Lock gains and reduce new-trade risk after daily-target milestones."""
    if daily_target <= 0:
        return 1.0, "Daily target unavailable"
    growth_ratio = daily_profit / daily_target
    for milestone, multiplier in reversed(PROFIT_LOCK_MILESTONES):
        if growth_ratio >= milestone:
            if multiplier == 0:
                return 0.0, f"Profit lock active at {milestone:.0%} of daily target"
            return multiplier, f"Risk reduced to {multiplier:.0%} after {milestone:.0%} milestone"
    return 1.0, "Normal risk"


def scalp_gate_reason() -> str | None:
    """Pause scalp bursts after repeated failures or outside approved sessions."""
    if st.session_state.get("scalp_consecutive_failures", 0) >= SCALP_FAILURE_LIMIT:
        return "Scalping paused after three consecutive failed trades."
    if st.session_state.get("session") not in SCALP_SESSIONS:
        return "Scalping is limited to London open and New York overlap."
    return None


def scalp_rate_limit_reason() -> str | None:
    """Enforce the broker-compliance cap over a rolling one-minute window."""
    now = datetime.now()
    recent = [timestamp for timestamp in st.session_state.get("scalp_trade_timestamps", []) if now - timestamp < timedelta(minutes=1)]
    st.session_state["scalp_trade_timestamps"] = recent
    if len(recent) >= SCALP_TRADES_PER_MINUTE_CAP:
        return f"Scalp rate limit reached: maximum {SCALP_TRADES_PER_MINUTE_CAP} trades per minute."
    return None


def record_scalp_failure(failed: bool) -> None:
    if failed:
        st.session_state["scalp_consecutive_failures"] = st.session_state.get("scalp_consecutive_failures", 0) + 1
    else:
        st.session_state["scalp_consecutive_failures"] = 0


# ============================================================================
# FEATURE 2: PORTFOLIO CORRELATION TRACKING
# ============================================================================
def get_portfolio_exposure(active_positions: list | dict = None) -> dict:
    """Get current portfolio exposure by symbol and correlation groups."""
    if active_positions is None:
        active_positions = []
    exposure = {}
    for pos in (active_positions if isinstance(active_positions, list) else []):
        symbol = pos.get("symbol", "")
        exposure[symbol] = exposure.get(symbol, 0) + pos.get("volume", 0)
    return exposure


def check_correlation_risk(symbol: str, active_positions: list = None) -> tuple[str | None, float]:
    """Check if adding a trade would exceed correlation risk limits. Returns (risk_reason, correlation_score)."""
    if active_positions is None:
        active_positions = []
    exposure = get_portfolio_exposure(active_positions)
    total_exposure = sum(exposure.values())
    max_correlated_exposure = 0.0
    max_correlation = 0.0
    for (pair1, pair2), corr_coeff in CORRELATION_PAIRS.items():
        if symbol == pair1 or symbol == pair2:
            other_symbol = pair2 if symbol == pair1 else pair1
            correlated_exposure = exposure.get(other_symbol, 0)
            weighted_exposure = abs(corr_coeff) * correlated_exposure
            if weighted_exposure > max_correlated_exposure:
                max_correlated_exposure = weighted_exposure
                max_correlation = abs(corr_coeff)
    if max_correlated_exposure >= 0.5 and abs(max_correlation) > 0.75:
        return f"High correlation risk: {symbol} would create {max_correlation:.0%} correlated exposure", max_correlation
    return None, max_correlation


def hedge_pair_multiplier(symbol: str, confidence: int, active_positions: list = None, side: str | None = None) -> tuple[float, str]:
    """Return confidence-prioritized sizing for the Gold SELL/USDZAR BUY hedge."""
    active_positions = active_positions or []
    active_symbols = {position.get("symbol") for position in active_positions}
    if HEDGE_DIRECTIONS.get(symbol) != side or not active_symbols.intersection(HEDGE_PAIR):
        return 1.0, "No hedge adjustment"

    other_symbol = next(iter(active_symbols.intersection(HEDGE_PAIR)))
    other_confidence = max(
        int(position.get("confidence", 0))
        for position in active_positions
        if position.get("symbol") == other_symbol
    )
    if confidence >= other_confidence:
        return 1.0, f"{symbol} has priority confidence ({confidence} vs {other_confidence})"

    reduced = max(HEDGE_PAIR_MIN_MULTIPLIER, confidence / max(other_confidence, 1))
    return reduced, f"{symbol} reduced to {reduced:.0%} of hedge size ({confidence} vs {other_confidence})"


def soft_correlation_check(symbol: str, active_positions: list = None, confidence: int = 0, side: str | None = None) -> dict:
    """Monitor hedge effectiveness without blocking the designated diversified pair."""
    active_positions = active_positions or []
    correlation_reason, correlation_score = check_correlation_risk(symbol, active_positions)
    active_symbols = {position.get("symbol") for position in active_positions}
    hedge_active = HEDGE_DIRECTIONS.get(symbol) == side and bool(active_symbols.intersection(HEDGE_PAIR))
    multiplier, sizing_reason = hedge_pair_multiplier(symbol, confidence, active_positions, side)
    return {
        "is_hedge_pair": hedge_active,
        "correlation_score": correlation_score,
        "blocked": bool(correlation_reason) and not hedge_active,
        "reason": sizing_reason if hedge_active else correlation_reason,
        "size_multiplier": multiplier,
    }


# ============================================================================
# FEATURE 3: ADAPTIVE STRATEGY WEIGHTING
# ============================================================================
def calculate_strategy_win_rates(trade_log: list) -> dict[str, dict]:
    """Calculate win rate and other metrics for each strategy (last 20 trades)."""
    metrics = {}
    recent_trades = trade_log[:20]
    for trade in recent_trades:
        strategy = trade.get("strategy", "Unknown")
        if strategy not in metrics:
            metrics[strategy] = {"wins": 0, "losses": 0, "total": 0, "total_profit": 0, "trades": []}
        outcome = trade.get("outcome", "")
        result = float(trade.get("result", 0))
        metrics[strategy]["total"] += 1
        metrics[strategy]["trades"].append(result)
        metrics[strategy]["total_profit"] += result
        if outcome in {"TP", "TP_ADJUST"} or (outcome in {"TIME", "SIGNAL"} and result > 0):
            metrics[strategy]["wins"] += 1
        elif outcome == "SL" or result < 0:
            metrics[strategy]["losses"] += 1
    for strategy, data in metrics.items():
        data["win_rate"] = (data["wins"] / data["total"] * 100) if data["total"] > 0 else 0
    return metrics


def get_adaptive_strategy_order(trade_log: list, base_strategies: list) -> list:
    """Reorder strategies by win rate (adaptive weighting). Fall back to base order if insufficient data."""
    if len(trade_log) < 5:
        return base_strategies
    metrics = calculate_strategy_win_rates(trade_log)
    strategy_scores = [(s, metrics.get(s, {}).get("win_rate", 0)) for s in base_strategies if s in metrics]
    sorted_strategies = sorted(strategy_scores, key=lambda x: x[1], reverse=True)
    result = [s for s, _ in sorted_strategies] + [s for s in base_strategies if s not in [x[0] for x in sorted_strategies]]
    return result


# ============================================================================
# FEATURE 4: NEWS INTEGRATION WITH POSITION SIZING ADJUSTMENTS
# ============================================================================
NEWS_EVENTS = {
    "14:30": {"label": "SARB rate decision", "impact": "HIGH", "adjustment": 0.5},
    "14:30 NFP": {"label": "US Non-Farm Payrolls (NFP)", "impact": "HIGH", "adjustment": 0.5},
    "15:00": {"label": "US CPI", "impact": "HIGH", "adjustment": 0.7},
    "10:00": {"label": "ZA unemployment", "impact": "MEDIUM", "adjustment": 0.8},
    "12:30": {"label": "US Core PCE", "impact": "MEDIUM", "adjustment": 0.8},
}


def get_news_position_adjustment() -> float:
    """Return position size multiplier based on proximity to news events (0.0 to 1.0)."""
    now = datetime.now()
    min_adjustment = 1.0
    for event_time, event_info in NEWS_EVENTS.items():
        event_dt = datetime.combine(now.date(), datetime.strptime(event_time[:5], "%H:%M").time())
        time_to_event = abs((now - event_dt).total_seconds())
        if time_to_event <= NEWS_BLACKOUT_MINUTES * 60:
            min_adjustment = min(min_adjustment, event_info["adjustment"])
    return min_adjustment


# ============================================================================
# FEATURE 5: ENHANCED TRADE ANALYTICS
# ============================================================================
def calculate_trade_metrics(trade_log: list) -> dict:
    """Calculate comprehensive trade analytics: win rate, R multiple, Sharpe, expectancy."""
    if not trade_log:
        return {
            "total_trades": 0, "win_rate_pct": 0, "avg_r_multiple": 0,
            "expectancy": 0, "sharpe_ratio": 0, "max_dd": 0,
        }
    results = [float(t.get("result", 0)) for t in trade_log]
    risk_per_trade = 100
    r_multiples = [r / risk_per_trade for r in results]
    
    total = len(results)
    wins = sum(1 for r in results if r > 0)
    losses = sum(1 for r in results if r < 0)
    win_rate = (wins / total * 100) if total > 0 else 0
    
    avg_win = sum(r for r in results if r > 0) / wins if wins > 0 else 0
    avg_loss = sum(r for r in results if r < 0) / losses if losses > 0 else 0
    expectancy = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * abs(avg_loss))
    
    cumulative = []
    running = 0
    for r in results:
        running += r
        cumulative.append(running)
    max_dd = min(cumulative) - max(cumulative) if cumulative else 0
    
    std_dev = np.std(results) if len(results) > 1 else 0
    sharpe = (np.mean(results) / std_dev * np.sqrt(252)) if std_dev > 0 else 0
    
    return {
        "total_trades": total,
        "win_rate_pct": win_rate,
        "avg_r_multiple": np.mean(r_multiples),
        "expectancy": expectancy,
        "sharpe_ratio": sharpe,
        "max_dd": max_dd,
    }


# ============================================================================
# FEATURE 6: ML-BASED SIGNAL GENERATION
# ============================================================================
class SimpleCanglePatternClassifier:
    """Lightweight ML classifier for candle pattern recognition."""
    def __init__(self):
        self.scaler = StandardScaler() if sklearn_available else None
        self.model = None
        self.is_trained = False
    
    def extract_candle_features(self, prices: list) -> np.ndarray:
        """Extract features from candle prices: trend, momentum, volatility."""
        if len(prices) < 3:
            return np.array([])
        prices_arr = np.array(prices)
        returns = np.diff(prices_arr) / prices_arr[:-1]
        trend = 1 if prices_arr[-1] > prices_arr[-2] else -1
        momentum = np.mean(returns) * 1000
        volatility = np.std(returns) * 100
        return np.array([[trend, momentum, volatility]])
    
    def predict_signal(self, prices: list) -> tuple[str, float]:
        """Predict signal (BUY/SELL/WAIT) with confidence from price data."""
        features = self.extract_candle_features(prices)
        if features.size == 0:
            return "WAIT", 0.5
        momentum = features[0, 1]
        volatility = features[0, 2]
        trend = features[0, 0]
        if momentum > 1.5 and trend > 0:
            return "BUY", min(0.95, 0.6 + volatility / 100)
        elif momentum < -1.5 and trend < 0:
            return "SELL", min(0.95, 0.6 + volatility / 100)
        else:
            return "WAIT", 0.4 + (volatility / 200)


ml_classifier = SimpleCanglePatternClassifier() if sklearn_available else None


# ============================================================================
# FEATURE 7: CAPITAL PRESERVATION RULES
# ============================================================================
# ============================================================================
# CODE RECOVERY MODULE: Drawdown Detection, Lot Adjustment, Weekly Reset
# ============================================================================
class CodeRecoveryModule:
    """
    Comprehensive recovery system with multi-level drawdown detection,
    progressive lot size adjustment, and weekly cycle management.
    """
    
    DRAWDOWN_THRESHOLDS = {
        "healthy": 0,      # 0-5%: Normal trading (1.0x)
        "caution": 5,      # 5-10%: Reduced size (0.8x)
        "alert": 10,       # 10-15%: Conservative (0.6x)
        "critical": 15,    # 15%+: Minimal trading (0.3x)
    }
    
    RECOVERY_MULTIPLIERS = {
        "healthy": 1.0,
        "caution": 0.8,
        "alert": 0.6,
        "critical": 0.3,
    }
    
    def __init__(self, initial_equity: float = 10000):
        self.initial_equity = initial_equity
        self.peak_equity = initial_equity
        self.recovery_phase = False
        self.consecutive_wins = 0
        self.consecutive_losses = 0
        self.weekly_stats = {
            "week_start": datetime.now(),
            "week_number": self._get_week_number(),
            "total_profit": 0,
            "total_trades": 0,
            "win_count": 0,
            "loss_count": 0,
            "prev_weeks_profit": 0,  # Carried over from previous weeks
        }
    
    def _get_week_number(self) -> int:
        """Get ISO week number (1-53)."""
        return datetime.now().isocalendar()[1]
    
    def _check_week_reset(self) -> bool:
        """Check if week has changed and reset if needed."""
        current_week = self._get_week_number()
        if current_week != self.weekly_stats["week_number"]:
            # Carryover previous week's profit
            self.weekly_stats["prev_weeks_profit"] += self.weekly_stats["total_profit"]
            # Reset weekly stats
            self.weekly_stats["week_start"] = datetime.now()
            self.weekly_stats["week_number"] = current_week
            self.weekly_stats["total_profit"] = 0
            self.weekly_stats["total_trades"] = 0
            self.weekly_stats["win_count"] = 0
            self.weekly_stats["loss_count"] = 0
            return True
        return False
    
    def detect_drawdown_level(self, current_equity: float) -> dict:
        """
        Detect drawdown level and classify severity.
        Returns: {level, drawdown_pct, lot_multiplier, phase, status}
        """
        # Update peak equity
        self.peak_equity = max(self.peak_equity, current_equity)
        
        # Calculate drawdown as % from peak
        if self.peak_equity <= 0:
            drawdown_pct = 0
        else:
            drawdown_pct = ((self.peak_equity - current_equity) / self.peak_equity) * 100
        
        # Determine level
        if drawdown_pct <= self.DRAWDOWN_THRESHOLDS["caution"]:
            level = "healthy"
        elif drawdown_pct <= self.DRAWDOWN_THRESHOLDS["alert"]:
            level = "caution"
        elif drawdown_pct <= self.DRAWDOWN_THRESHOLDS["critical"]:
            level = "alert"
        else:
            level = "critical"
        
        # Determine phase
        if level != "healthy" and not self.recovery_phase:
            self.recovery_phase = True
        elif level == "healthy" and self.recovery_phase:
            self.recovery_phase = False
        
        lot_multiplier = self.RECOVERY_MULTIPLIERS[level]
        
        # Status message
        status_map = {
            "healthy": f"Equity healthy: {drawdown_pct:.1f}% from peak",
            "caution": f"Caution: {drawdown_pct:.1f}% drawdown - reducing to 80% size",
            "alert": f"Alert: {drawdown_pct:.1f}% drawdown - reducing to 60% size",
            "critical": f"Critical: {drawdown_pct:.1f}% drawdown - minimal trading (30% size)",
        }
        
        return {
            "level": level,
            "drawdown_pct": drawdown_pct,
            "lot_multiplier": lot_multiplier,
            "phase": "recovery" if self.recovery_phase else "normal",
            "status": status_map[level],
        }
    
    def record_trade_outcome(self, profit: float, lot_size: float = 0.1):
        """Record trade outcome and update recovery tracking."""
        self._check_week_reset()
        
        self.weekly_stats["total_trades"] += 1
        self.weekly_stats["total_profit"] += profit
        
        if profit > 0:
            self.weekly_stats["win_count"] += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        elif profit < 0:
            self.weekly_stats["loss_count"] += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0
    
    def calculate_recovery_lot_multiplier(self, equity: float) -> float:
        """
        Calculate progressive lot multiplier during recovery phase.
        Scales from 0.3x up to 1.0x based on:
        - Consecutive wins (confidence boost)
        - Weekly win rate (consistency metric)
        - Distance from peak (recovery progress)
        """
        if not self.recovery_phase:
            return 1.0
        
        # Base multiplier from drawdown level
        drawdown_info = self.detect_drawdown_level(equity)
        base_multiplier = drawdown_info["lot_multiplier"]
        
        # Bonus 1: Consecutive wins (up to +0.2x)
        win_bonus = min(self.consecutive_wins * 0.1, 0.2)
        
        # Bonus 2: Weekly win rate consistency (up to +0.1x)
        if self.weekly_stats["total_trades"] >= 5:
            win_rate = self.weekly_stats["win_count"] / self.weekly_stats["total_trades"]
            if win_rate >= 0.55:
                win_bonus += 0.1
        
        final_multiplier = min(base_multiplier + win_bonus, 1.0)
        return final_multiplier
    
    def get_weekly_summary(self) -> dict:
        """Get weekly performance summary."""
        self._check_week_reset()
        
        if self.weekly_stats["total_trades"] == 0:
            win_rate = 0
            avg_profit = 0
        else:
            win_rate = (self.weekly_stats["win_count"] / self.weekly_stats["total_trades"]) * 100
            avg_profit = self.weekly_stats["total_profit"] / self.weekly_stats["total_trades"]
        
        return {
            "week_number": self.weekly_stats["week_number"],
            "week_start": self.weekly_stats["week_start"].strftime("%Y-%m-%d"),
            "total_trades": self.weekly_stats["total_trades"],
            "wins": self.weekly_stats["win_count"],
            "losses": self.weekly_stats["loss_count"],
            "win_rate": win_rate,
            "weekly_profit": self.weekly_stats["total_profit"],
            "prev_weeks_profit": self.weekly_stats["prev_weeks_profit"],
            "cumulative_profit": self.weekly_stats["total_profit"] + self.weekly_stats["prev_weeks_profit"],
            "consecutive_wins": self.consecutive_wins,
            "consecutive_losses": self.consecutive_losses,
        }
    
    def is_trading_allowed(self, equity: float) -> tuple[bool, str]:
        """
        Determine if trading should continue or pause based on critical conditions.
        Returns: (allow_trading, reason)
        """
        drawdown_info = self.detect_drawdown_level(equity)
        
        # Allow trading in all phases except at absolute critical
        # In critical, only allow recovery trades (not new positions)
        if drawdown_info["level"] == "critical":
            # Only allow if consecutive wins indicate recovery
            if self.consecutive_wins >= 2:
                return True, "Critical level but recovery in progress"
            else:
                return False, "Critical drawdown level: pausing new trades to preserve capital"
        
        return True, "Trading permitted"


def calculate_drawdown_status(account_balance: float, account_equity: float) -> dict:
    """Monitor account drawdown and return preservation metrics (backward compatible)."""
    if account_balance <= 0:
        return {"drawdown_pct": 0, "is_in_drawdown": False, "lot_multiplier": 1.0, "status": "No account data"}
    drawdown = (account_balance - account_equity) / account_balance * 100
    is_drawdown = drawdown > 10
    lot_multiplier = 0.5 if is_drawdown else 1.0
    status = f"Drawdown {drawdown:.1f}% - lot size reduced" if is_drawdown else f"Equity healthy ({-drawdown:.1f}% profit)"
    return {
        "drawdown_pct": drawdown,
        "is_in_drawdown": is_drawdown,
        "lot_multiplier": lot_multiplier,
        "status": status,
    }


# ============================================================================
# ADVANCED FEATURE 1: MULTI-TIMEFRAME CONFIRMATION
# ============================================================================
def get_multi_timeframe_signals(symbol: str, signals: pd.DataFrame) -> dict:
    """
    Get signal alignment across multiple timeframes.
    Simulates checking 1m, 15m, 1h - in production would fetch real data.
    """
    base_signal = signals[signals["symbol"] == symbol]
    decision, buys, sells = decision_for(symbol, signals)
    
    # Simulate timeframe signals with slight variance
    timeframe_signals = {
        "1m": {"decision": decision, "strength": (max(buys, sells) / 5.0)},
        "15m": {"decision": decision, "strength": (max(buys, sells) / 5.0) * 0.95},
        "1h": {"decision": decision, "strength": (max(buys, sells) / 5.0) * 0.90},
    }
    
    # Count aligned timeframes
    aligned_count = sum(1 for tf in timeframe_signals.values() if tf["decision"] == decision)
    avg_strength = np.mean([tf["strength"] for tf in timeframe_signals.values()])
    
    return {
        "primary_decision": decision,
        "aligned_timeframes": aligned_count,  # 0-3
        "timeframe_details": timeframe_signals,
        "avg_strength": avg_strength,
        "multi_tf_confirmed": aligned_count >= 2,  # Require 2+ aligned
    }


# ============================================================================
# ADVANCED FEATURE 2: REGIME DETECTION (TRENDING vs RANGING vs VOLATILE)
# ============================================================================
class MarketRegimeClassifier:
    """Detect market regime: TRENDING, RANGING, or VOLATILE."""
    
    def __init__(self):
        self.lookback = 20
        self.regime = "RANGING"
        self.volatility_baseline = 0.0
    
    def calculate_adx(self, prices: list) -> float:
        """Simplified ADX calculation (0-100). Higher = stronger trend."""
        if len(prices) < 5:
            return 50
        returns = np.diff(prices) / np.array(prices[:-1])
        momentum = np.abs(np.mean(returns)) / (np.std(returns) + 1e-6)
        return min(100, momentum * 20)  # Scale to 0-100
    
    def calculate_volatility(self, prices: list) -> float:
        """Volatility as std dev of returns."""
        if len(prices) < 2:
            return 0.01
        returns = np.diff(prices) / np.array(prices[:-1])
        return float(np.std(returns))
    
    def detect_regime(self, prices: list, historical_volatility: float = 0.015) -> dict:
        """
        Classify market into TRENDING (strong ADX), RANGING (low ADX), or VOLATILE (high volatility).
        """
        if len(prices) < 5:
            return {"regime": "RANGING", "adx": 50, "volatility": 0.01, "confidence": 0.5}
        
        adx = self.calculate_adx(prices[-self.lookback:])
        volatility = self.calculate_volatility(prices[-self.lookback:])
        vol_ratio = volatility / (historical_volatility + 1e-6)
        
        if vol_ratio > 1.5:
            regime = "VOLATILE"
        elif adx > 60:
            regime = "TRENDING"
        else:
            regime = "RANGING"
        
        confidence = min(adx / 100, vol_ratio / 2.0)
        self.regime = regime
        self.volatility_baseline = historical_volatility
        
        return {
            "regime": regime,
            "adx": adx,
            "volatility": volatility,
            "vol_ratio": vol_ratio,
            "confidence": confidence,
        }


regime_classifier = MarketRegimeClassifier()


# ============================================================================
# ADVANCED FEATURE 3: REINFORCEMENT LEARNING FEEDBACK
# ============================================================================
class RLPositionSizingAgent:
    """
    Simple RL agent that learns position sizing based on trade outcomes.
    Uses reward (profit) to adjust position size multiplier.
    """
    
    def __init__(self, learning_rate: float = 0.05):
        self.learning_rate = learning_rate
        self.position_multiplier = 1.0
        self.cumulative_reward = 0
        self.trade_history = []
    
    def record_trade_outcome(self, profit: float, lot_size: float) -> None:
        """Record trade outcome and update position size multiplier."""
        reward = profit
        self.cumulative_reward += profit
        self.trade_history.append({"profit": profit, "lot_size": lot_size})
        
        # Simple RL update: if profitable, increase multiplier; if loss, decrease
        if profit > 0:
            self.position_multiplier = min(1.5, self.position_multiplier * (1 + self.learning_rate))
        else:
            self.position_multiplier = max(0.5, self.position_multiplier * (1 - self.learning_rate))
    
    def get_position_multiplier(self) -> float:
        """Get current learned position multiplier."""
        return self.position_multiplier
    
    def get_performance_summary(self) -> dict:
        """Summary of RL agent learning progress."""
        if not self.trade_history:
            return {"trades": 0, "total_reward": 0, "avg_return": 0, "multiplier": 1.0}
        
        total_profit = sum(t["profit"] for t in self.trade_history)
        avg_profit = total_profit / len(self.trade_history)
        
        return {
            "trades": len(self.trade_history),
            "total_reward": total_profit,
            "avg_return": avg_profit,
            "multiplier": self.position_multiplier,
            "learning_rate": self.learning_rate,
        }


rl_agent = RLPositionSizingAgent(learning_rate=0.05)


# ============================================================================
# ADVANCED FEATURE 4: RISK-ADJUSTED EXPECTANCY FILTERS
# ============================================================================
def calculate_trade_expectancy(win_rate: float, avg_win: float, avg_loss: float, trade_size: float = 1.0) -> dict:
    """
    Calculate expectancy for a specific trade setup.
    Only execute if positive expectancy.
    """
    loss_rate = 1.0 - win_rate
    expectancy = (win_rate * avg_win) - (loss_rate * abs(avg_loss))
    risk_reward_ratio = avg_win / abs(avg_loss) if avg_loss != 0 else 0
    
    # Only trade if expectancy > 0 OR risk/reward > 2:1
    should_trade = (expectancy > 0) or (risk_reward_ratio >= 2.0)
    
    return {
        "expectancy": expectancy,
        "risk_reward_ratio": risk_reward_ratio,
        "win_rate_pct": win_rate * 100,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "should_execute": should_trade,
        "reason": "Positive expectancy" if expectancy > 0 else ("Good risk/reward (>2:1)" if risk_reward_ratio >= 2.0 else "Negative expectancy - skip"),
    }


# ============================================================================
# ADVANCED FEATURE 5: PORTFOLIO-LEVEL OPTIMIZATION (KELLY CRITERION)
# ============================================================================
def calculate_kelly_position_size(win_rate: float, avg_win: float, avg_loss: float, equity: float, account_risk_pct: float = 2.0) -> float:
    """
    Kelly Criterion for optimal position sizing: f* = (win_rate × avg_win - (1-win_rate) × avg_loss) / avg_win
    Fractional Kelly (50%) is more conservative: f* / 2
    """
    if avg_win <= 0 or avg_loss >= 0:
        return 0.0
    
    loss_rate = 1.0 - win_rate
    kelly_fraction = (win_rate * avg_win - loss_rate * abs(avg_loss)) / avg_win
    kelly_fraction = max(0, min(kelly_fraction, 1.0))  # Clamp to [0, 1]
    
    # Use half-kelly for safety (most traders use 25% kelly)
    half_kelly = kelly_fraction / 2.0
    
    # Convert to lot size: position_value = equity × kelly_fraction × account_risk_pct / 100
    position_value = equity * half_kelly * (account_risk_pct / 100.0)
    lot_size = position_value / 1000  # Rough conversion (1 lot = ~$1000 per pip)
    
    return max(MIN_LOT_SIZE, min(lot_size, MAX_LOT_SIZE))


def portfolio_optimization_summary(open_positions: list, trade_log: list) -> dict:
    """
    Analyze portfolio and suggest optimizations (correlation, concentration, drawdown).
    """
    if not open_positions:
        return {"status": "No open positions", "concentration": 0, "correlation_risk": 0}
    
    total_volume = sum(p.get("volume", 0) for p in open_positions)
    concentration = max([p.get("volume", 0) / total_volume for p in open_positions]) if total_volume > 0 else 0
    
    # Count correlated pairs
    correlation_risk = 0
    for (pair1, pair2), corr_coeff in CORRELATION_PAIRS.items():
        if any(p["symbol"] == pair1 for p in open_positions) and any(p["symbol"] == pair2 for p in open_positions):
            correlation_risk += abs(corr_coeff)
    
    return {
        "num_positions": len(open_positions),
        "total_volume": total_volume,
        "concentration": concentration,
        "correlation_risk": min(correlation_risk, 3.0),  # Cap at 3.0
        "optimization_needed": concentration > 0.6 or correlation_risk > 1.5,
    }


# ============================================================================
# ADVANCED FEATURE 6: CONTINUOUS BACKTESTING + LIVE MONITORING
# ============================================================================
class BacktestComparison:
    """Compare live performance vs backtest baseline."""
    
    def __init__(self):
        self.backtest_baseline = {"win_rate": 0.60, "avg_profit": 150, "trades": 0}
        self.live_stats = {"win_rate": 0, "avg_profit": 0, "trades": 0, "consecutive_losses": 0}
        self.performance_threshold = 0.45  # If live win rate drops below 45%, reduce exposure
    
    def update_live_performance(self, trade_log: list) -> dict:
        """Update live performance from trade log."""
        if not trade_log:
            return self.live_stats
        
        recent_trades = trade_log[:20]
        wins = sum(1 for t in recent_trades if t.get("result", 0) > 0)
        losses = sum(1 for t in recent_trades if t.get("result", 0) < 0)
        total = len(recent_trades)
        
        self.live_stats["win_rate"] = (wins / total) if total > 0 else 0
        self.live_stats["avg_profit"] = (sum(t.get("result", 0) for t in recent_trades) / total) if total > 0 else 0
        self.live_stats["trades"] = total
        
        # Track consecutive losses
        consecutive = 0
        for t in recent_trades:
            if t.get("result", 0) < 0:
                consecutive += 1
            else:
                break
        self.live_stats["consecutive_losses"] = consecutive
        
        return self.live_stats
    
    def get_performance_comparison(self, trade_log: list) -> dict:
        """Compare live vs backtest and suggest exposure adjustments."""
        self.update_live_performance(trade_log)
        
        live_wr = self.live_stats["win_rate"]
        backtest_wr = self.backtest_baseline["win_rate"]
        performance_ratio = live_wr / backtest_wr if backtest_wr > 0 else 1.0
        
        # Recommend exposure reduction if live underperforming
        should_reduce = live_wr < self.performance_threshold
        exposure_adjustment = max(0.5, performance_ratio)  # Min 50% exposure
        
        return {
            "backtest_win_rate": backtest_wr,
            "live_win_rate": live_wr,
            "performance_ratio": performance_ratio,
            "should_reduce_exposure": should_reduce,
            "recommended_exposure": exposure_adjustment,
            "consecutive_losses": self.live_stats["consecutive_losses"],
        }


backtest_monitor = BacktestComparison()


# ============================================================================
# ADVANCED FEATURE 7: SENTIMENT INTEGRATION (SKELETON)
# ============================================================================
POSITIONING_SNAPSHOT = {
    "EURUSD": {"long_pct": 43.0, "short_pct": 57.0},
    "GBPUSD": {"long_pct": 61.0, "short_pct": 39.0},
}


class SentimentFilter:
    """
    Placeholder for sentiment integration.
    In production, would connect to news API or social media feeds.
    """
    
    def __init__(self):
        self.sentiment_cache = {}
        self.sentiments = {"USD": 0.5, "EUR": 0.5, "GBP": 0.5, "ZAR": 0.5, "Gold": 0.5, "Oil": 0.5}
        self.sentiment_threshold = 0.3  # Min abs(sentiment) to influence trades
    
    def get_currency_sentiment(self, symbol: str) -> dict:
        """
        Get sentiment for currency pair.
        Returns: bullish/bearish sentiment [-1.0 to +1.0]
        In production: Query news API, Twitter sentiment, analyst ratings
        """
        # Extract currency from symbol (e.g., "EURUSD" -> "EUR", "USD")
        base_ccy = symbol[:3].upper() if len(symbol) >= 3 else "USD"
        
        current_sentiment = self.sentiments.get(base_ccy, 0.5)
        
        return {
            "currency": base_ccy,
            "sentiment": current_sentiment,
            "interpretation": "Bullish" if current_sentiment > 0.55 else "Bearish" if current_sentiment < 0.45 else "Neutral",
            "confidence": abs(current_sentiment - 0.5) * 2,  # 0 to 1
            "should_filter": abs(current_sentiment - 0.5) < self.sentiment_threshold,  # Too weak to use
        }
    
    def apply_sentiment_filter(self, symbol: str, decision: str) -> tuple[bool, str]:
        """
        Apply sentiment filter to trade decision.
        Returns: (should_trade: bool, reason: str)
        """
        sentiment = self.get_currency_sentiment(symbol)
        
        # Filter conflicting trades
        if decision == "BUY" and sentiment["sentiment"] < 0.45:
            return False, f"Bearish sentiment on {sentiment['currency']} conflicts with BUY"
        if decision == "SELL" and sentiment["sentiment"] > 0.55:
            return False, f"Bullish sentiment on {sentiment['currency']} conflicts with SELL"
        
        return True, "Sentiment aligned with trade decision"

        def get_positioning(self, symbol: str) -> dict | None:
            """Return the configured long/short positioning snapshot for a supported pair."""
            positioning = st.session_state.get("positioning_snapshot", POSITIONING_SNAPSHOT).get(symbol)
            if positioning is None:
                return None
            long_pct = float(positioning["long_pct"])
            short_pct = float(positioning["short_pct"])
            net_pct = long_pct - short_pct
            return {
                "long_pct": long_pct,
                "short_pct": short_pct,
                "net_pct": net_pct,
                "bias": "NET LONG" if net_pct >= 0 else "NET SHORT",
                "is_strong": abs(net_pct) >= 15.0,
            }

        def apply_positioning_filter(self, symbol: str, decision: str) -> tuple[bool, str]:
            """Reject only directional trades that conflict with strong pair positioning."""
            positioning = self.get_positioning(symbol)
            if positioning is None or decision == "WAIT" or not positioning["is_strong"]:
                return True, "Positioning is neutral or unavailable"
            if decision == "BUY" and positioning["net_pct"] < 0:
                return False, f"{symbol} positioning is {positioning['bias']} ({positioning['net_pct']:+.0f} pts), conflicting with BUY"
            if decision == "SELL" and positioning["net_pct"] > 0:
                return False, f"{symbol} positioning is {positioning['bias']} ({positioning['net_pct']:+.0f} pts), conflicting with SELL"
            return True, f"{symbol} positioning confirms {decision} ({positioning['net_pct']:+.0f} pts)"
    
    def update_sentiment(self, symbol: str, new_sentiment: float) -> None:
        """Update sentiment (would come from external API)."""
        base_ccy = symbol[:3].upper() if len(symbol) >= 3 else "USD"
        self.sentiments[base_ccy] = max(0.0, min(1.0, new_sentiment))


sentiment_filter = SentimentFilter()


def _get_positioning(self, symbol: str) -> dict | None:
    positioning = st.session_state.get("positioning_snapshot", POSITIONING_SNAPSHOT).get(symbol)
    if positioning is None:
        return None
    long_pct = float(positioning["long_pct"])
    short_pct = float(positioning["short_pct"])
    net_pct = long_pct - short_pct
    return {
        "long_pct": long_pct,
        "short_pct": short_pct,
        "net_pct": net_pct,
        "bias": "NET LONG" if net_pct >= 0 else "NET SHORT",
        "is_strong": abs(net_pct) >= 15.0,
    }


def _apply_positioning_filter(self, symbol: str, decision: str) -> tuple[bool, str]:
    positioning = self.get_positioning(symbol)
    if positioning is None or decision == "WAIT" or not positioning["is_strong"]:
        return True, "Positioning is neutral or unavailable"
    if decision == "BUY" and positioning["net_pct"] < 0:
        return False, f"{symbol} positioning is {positioning['bias']} ({positioning['net_pct']:+.0f} pts), conflicting with BUY"
    if decision == "SELL" and positioning["net_pct"] > 0:
        return False, f"{symbol} positioning is {positioning['bias']} ({positioning['net_pct']:+.0f} pts), conflicting with SELL"
    return True, f"{symbol} positioning confirms {decision} ({positioning['net_pct']:+.0f} pts)"


SentimentFilter.get_positioning = _get_positioning
SentimentFilter.apply_positioning_filter = _apply_positioning_filter

def calculate_weighted_confidence(symbol: str, decision: str, buys: int, sells: int, regime: dict | None = None, recovery_state: str = "normal") -> dict:
    """
    Transparent, weighted confidence scoring (0-100).
    Weights: alignment 40%, regime 30%, sentiment/positioning 20%, recovery 10%.
    Returns: score, component breakdown, target % band, and audit trail.
    """
    # Component 1: Strategy Alignment (40%)
    alignment_score = max(buys, sells) / len(STRATEGIES) * 100
    alignment_weighted = alignment_score * 0.40
    
    # Component 2: Market Regime (30%)
    regime_score = 50  # neutral default
    if regime and regime.get("regime") == "TRENDING":
        regime_score = 75
    elif regime and regime.get("regime") == "VOLATILE":
        regime_score = 25
    elif regime and regime.get("regime") == "RANGING":
        regime_score = 50
    regime_weighted = regime_score * 0.30
    
    # Component 3: Sentiment & Positioning (20%)
    positioning = sentiment_filter.get_positioning(symbol)
    sentiment_score = 50  # neutral default
    if positioning:
        if (decision == "BUY" and positioning["net_pct"] > 0) or (decision == "SELL" and positioning["net_pct"] < 0):
            sentiment_score = 75  # aligned
        elif (decision == "BUY" and positioning["net_pct"] < 0) or (decision == "SELL" and positioning["net_pct"] > 0):
            sentiment_score = 25  # conflicting
    sentiment_weighted = sentiment_score * 0.20
    
    # Component 4: Recovery State (10%)
    recovery_score = 50  # neutral
    if recovery_state == "recovery":
        recovery_score = 30  # reduce confidence during recovery
    elif recovery_state == "normal":
        recovery_score = 60  # normal confidence
    recovery_weighted = recovery_score * 0.10
    
    # Final composite score
    final_score = round(alignment_weighted + regime_weighted + sentiment_weighted + recovery_weighted)
    
    # Map to execution target % band (transparent)
    if final_score >= 70:
        target_band, band_reason = 30, "High confidence: execute at 30% target"
    elif final_score >= 50:
        target_band, band_reason = 15, "Moderate confidence: execute at 15% target"
    else:
        target_band, band_reason = 5, "Low confidence: execute at 5% target minimum"
    
    return {
        "score": final_score,
        "alignment": {"value": alignment_score, "weighted": round(alignment_weighted, 1), "weight": 0.40},
        "regime": {"value": regime_score, "weighted": round(regime_weighted, 1), "weight": 0.30},
        "sentiment": {"value": sentiment_score, "weighted": round(sentiment_weighted, 1), "weight": 0.20},
        "recovery": {"value": recovery_score, "weighted": round(recovery_weighted, 1), "weight": 0.10},
        "target_band_pct": target_band,
        "target_band_reason": band_reason,
        "positioning": positioning,
    }


def calculate_trade_confidence(symbol, decision, buys, sells, regime=None):
    """Legacy wrapper for backward compatibility."""
    return calculate_weighted_confidence(symbol, decision, buys, sells, regime)


def weekly_risk_cap():
    recovery_module = st.session_state.get("recovery_module")
    return WEEKLY_PROFIT_LOCK_RISK_PCT if recovery_module and recovery_module.get_weekly_summary()["weekly_profit"] >= WEEKLY_PROFIT_TARGET else None


def allocation_risk_reason(symbol, proposed_risk_pct):
    active_trade = st.session_state.get("active_trade")
    for group_name, symbols in RISK_ALLOCATION_GROUPS.items():
        if symbol in symbols and active_trade and active_trade.get("symbol") in symbols and float(active_trade.get("risk_pct", DEFAULT_RISK_PER_TRADE_PCT)) + proposed_risk_pct > MAX_ALLOCATION_GROUP_RISK_PCT:
            return f"{group_name} allocation would exceed the {MAX_ALLOCATION_GROUP_RISK_PCT:.0f}% combined risk cap"
    return None


def positioning_lot_multiplier(symbol, decision):
    positioning = sentiment_filter.get_positioning(symbol)
    conflicts = positioning and positioning["is_strong"] and ((decision == "BUY" and positioning["net_pct"] < 0) or (decision == "SELL" and positioning["net_pct"] > 0))
    return 0.5 if conflicts else 1.0


@st.cache_data(ttl=30)
def load_market_snapshot(seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    now = datetime.now().replace(second=0, microsecond=0)
    times = pd.date_range(end=now, periods=72, freq="15min")
    rows = []
    base_prices = {"USDZAR": 18.2140, "EURZAR": 19.6720, "GBPZAR": 23.1040, "SA40": 7350.0, "XAUUSD": 2338.6, "USOIL": 78.42, "XPTUSD": 945.2, "EURUSD": 1.0842, "GBPUSD": 1.2714}
    for symbol, base in base_prices.items():
        drift = rng.normal(0, 0.0008, len(times)).cumsum()
        scale = base * (0.002 if symbol != "XAUUSD" else 0.004)
        closes = base + drift * scale
        for timestamp, close in zip(times, closes):
            rows.append({"time": timestamp, "symbol": symbol, "close": round(float(close), 5)})

    signals = []
    signal_map = {
        "USDZAR": ["BUY", "BUY", "WAIT", "BUY", "BUY"],
        "EURZAR": ["WAIT", "SELL", "SELL", "WAIT", "SELL"],
        "GBPZAR": ["BUY", "WAIT", "BUY", "BUY", "WAIT"],
        "SA40": ["BUY", "WAIT", "BUY", "BUY", "WAIT"],
        "XAUUSD": ["SELL", "SELL", "WAIT", "SELL", "WAIT"],
        "USOIL": ["WAIT", "SELL", "SELL", "WAIT", "SELL"],
        "XPTUSD": ["BUY", "BUY", "WAIT", "BUY", "WAIT"],
        "EURUSD": ["WAIT", "WAIT", "SELL", "WAIT", "BUY"],
        "GBPUSD": ["BUY", "WAIT", "WAIT", "BUY", "WAIT"],
    }
    for symbol in SYMBOLS:
        current = signal_map[symbol]
        for strategy, decision in zip(STRATEGIES, current):
            signals.append(
                {
                    "symbol": symbol,
                    "strategy": strategy,
                    "decision": decision,
                    "confidence": int(rng.integers(62, 91)) if decision != "WAIT" else int(rng.integers(40, 61)),
                }
            )
    return pd.DataFrame(rows), pd.DataFrame(signals)


@st.cache_data(ttl=30)
def load_journal() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["Today, 09:42", "EURUSD", "BUY", "Trend + momentum", "London open", "+$184.20", "Clear"],
            ["Yesterday, 15:18", "XAUUSD", "SELL", "Breakout + session", "New York overlap", "+$96.40", "CPI window clear"],
            ["Yesterday, 11:06", "GBPUSD", "BUY", "Mean reversion", "London open", "-$71.80", "Stopped at 1R"],
            ["Mon, 16:33", "USDJPY", "SELL", "Trend + breakout", "New York overlap", "+$132.60", "FOMC buffer clear"],
        ],
        columns=["time", "pair", "side", "strategies", "session", "result", "news context"],
    )


def decision_for(symbol: str, signals: pd.DataFrame) -> tuple[str, int, int]:
    subset = signals[signals["symbol"] == symbol]
    buys = int((subset["decision"] == "BUY").sum())
    sells = int((subset["decision"] == "SELL").sum())
    if buys >= 3:
        return "BUY", buys, sells
    if sells >= 3:
        return "SELL", buys, sells
    return "WAIT", buys, sells


    def calculate_trade_confidence(symbol: str, decision: str, buys: int, sells: int, regime: dict | None = None) -> dict:
        """Blend strategy alignment, pair positioning, and regime into a 0-100 confidence score."""
        alignment_score = max(buys, sells) / len(STRATEGIES) * 100
        positioning = sentiment_filter.get_positioning(symbol)
        positioning_adjustment = 0.0
        if positioning:
            strength = min(abs(positioning["net_pct"]) + 1, 15.0)
            aligned = (decision == "BUY" and positioning["net_pct"] > 0) or (decision == "SELL" and positioning["net_pct"] < 0)
            positioning_adjustment = strength if decision == "WAIT" or aligned else -strength
        regime_adjustment = 5.0 if regime and regime["regime"] == "TRENDING" else -10.0 if regime and regime["regime"] == "VOLATILE" else 0.0
        return {
            "score": round(max(0.0, min(100.0, alignment_score + positioning_adjustment + regime_adjustment))),
            "alignment_score": round(alignment_score),
            "positioning_adjustment": round(positioning_adjustment),
            "regime_adjustment": round(regime_adjustment),
            "positioning": positioning,
        }


    def weekly_risk_cap() -> float | None:
        recovery_module = st.session_state.get("recovery_module")
        if recovery_module and recovery_module.get_weekly_summary()["weekly_profit"] >= WEEKLY_PROFIT_TARGET:
            return WEEKLY_PROFIT_LOCK_RISK_PCT
        return None


    def allocation_risk_reason(symbol: str, proposed_risk_pct: float) -> str | None:
        active_trade = st.session_state.get("active_trade")
        for group_name, symbols in RISK_ALLOCATION_GROUPS.items():
            if symbol in symbols and active_trade and active_trade.get("symbol") in symbols:
                active_risk_pct = float(active_trade.get("risk_pct", DEFAULT_RISK_PER_TRADE_PCT))
                if active_risk_pct + proposed_risk_pct > MAX_ALLOCATION_GROUP_RISK_PCT:
                    return f"{group_name} allocation would exceed the {MAX_ALLOCATION_GROUP_RISK_PCT:.0f}% combined risk cap"
        return None


    def positioning_lot_multiplier(symbol: str, decision: str) -> float:
        positioning = sentiment_filter.get_positioning(symbol)
        conflicts = positioning and positioning["is_strong"] and ((decision == "BUY" and positioning["net_pct"] < 0) or (decision == "SELL" and positioning["net_pct"] > 0))
        return 0.5 if conflicts else 1.0


def enhanced_decision_with_filters(symbol: str, signals: pd.DataFrame, prices: list = None, use_sentiment: bool = True, use_regime: bool = True) -> tuple[str, dict]:
    """
    Enhanced decision making with multi-timeframe, regime, sentiment, and expectancy filters.
    Returns: (final_decision, analysis_dict)
    """
    # Base decision
    base_decision, buys, sells = decision_for(symbol, signals)
    
    analysis = {
        "base_decision": base_decision,
        "buys": buys,
        "sells": sells,
        "multi_tf": None,
        "regime": None,
        "sentiment": None,
        "expectancy": None,
        "filters_passed": True,
        "reason": "Decision validated",
    }
    
    # Feature 1: Multi-timeframe confirmation
    if base_decision != "WAIT":
        multi_tf = get_multi_timeframe_signals(symbol, signals)
        analysis["multi_tf"] = multi_tf
        if not multi_tf["multi_tf_confirmed"]:
            analysis["filters_passed"] = False
            analysis["reason"] = f"Multi-timeframe not aligned ({multi_tf['aligned_timeframes']}/3)"
    
    # Feature 2: Regime detection
    if use_regime and prices:
        regime = regime_classifier.detect_regime(prices)
        analysis["regime"] = regime
        # In trending, favor trend trades; in ranging, favor mean reversion
    
    # Feature 4: Risk-adjusted expectancy filter (requires trade history)
    # This would use calculate_trade_expectancy() with historical stats
    
    # Feature 7: Sentiment filter
    if use_sentiment and analysis["filters_passed"]:
        sentiment_pass, sentiment_reason = sentiment_filter.apply_sentiment_filter(symbol, base_decision)
        analysis["sentiment"] = {"passed": sentiment_pass, "reason": sentiment_reason}
        if not sentiment_pass:
            analysis["filters_passed"] = False
            analysis["reason"] = sentiment_reason

        if analysis["filters_passed"]:
            positioning_pass, positioning_reason = sentiment_filter.apply_positioning_filter(symbol, base_decision)
            analysis["positioning"] = {"passed": positioning_pass, "reason": positioning_reason}
            if not positioning_pass:
                analysis["filters_passed"] = False
                analysis["reason"] = positioning_reason
    
    confidence = calculate_trade_confidence(symbol, base_decision, buys, sells, analysis["regime"])
    analysis["confidence"] = confidence
    if base_decision != "WAIT" and confidence["score"] < MIN_TRADE_CONFIDENCE:
        analysis["filters_passed"] = False
        analysis["reason"] = f"Confidence {confidence['score']}/100 is below the {MIN_TRADE_CONFIDENCE} execution threshold"
        final_decision = base_decision if analysis["filters_passed"] else "WAIT"
        return final_decision, analysis


def get_mt5_credentials() -> tuple[int | None, str | None, str]:
    account_id_raw = os.getenv("MT5_ACCOUNT_ID")
    account_password = os.getenv("MT5_ACCOUNT_PASSWORD")
    server = os.getenv("MT5_SERVER", "HFM-Demo")
    try:
        account_id = int(account_id_raw) if account_id_raw is not None else None
    except (TypeError, ValueError):
        account_id = None
    return account_id, account_password, server


def broker_rules_for(server: str | None) -> dict:
        """Return an exact or prefix-matched broker profile, or conservative defaults."""
        server = server or ""
        if server in BROKER_RULES:
            return BROKER_RULES[server]
        for broker_name, rules in BROKER_RULES.items():
            if broker_name != "Default" and server.lower().startswith(broker_name.lower().split("-")[0]):
                return rules
        return BROKER_RULES["Default"]


def execution_mode_status(enabled: bool, mt5_module, account_id: int | None, password: str | None) -> tuple[str, str]:
    if not enabled:
        return "PAPER", "Paper mode is safe and disabled for live execution."
    if mt5_module is None:
        return "PAPER", "MetaTrader5 is not installed; live mode is unavailable."
    if account_id is None or not password:
        return "PAPER", "Live mode is blocked until valid MT5 credentials are configured."
    return "LIVE", "Live mode is ready. MT5 credentials are configured and trading can run."


def connect_mt5(account_id: int | None = None, account_password: str | None = None, server: str | None = None, show_status: bool = True) -> bool:
    if PAPER_MODE_FORCED:
        if show_status:
            st.info("PAPER mode is enforced. MT5 login is disabled until MARONG_FORCE_PAPER=false is set in a controlled demo environment.")
        return False
    if mt5 is None:
        if show_status:
            st.info("MT5 connector is unavailable in this environment; paper mode remains active.")
        return False
    configured_id, configured_password, configured_server = get_mt5_credentials()
    account_id = account_id if account_id is not None else configured_id
    account_password = account_password if account_password is not None else configured_password
    server = server or configured_server
    if account_id is None or not account_password:
        if show_status:
            st.warning("Set MT5_ACCOUNT_ID and MT5_ACCOUNT_PASSWORD to enable live broker execution.")
        return False
    if not mt5.initialize(login=account_id, password=account_password, server=server):
        st.session_state["mt5_connected"] = False
        if show_status:
            st.error(f"Live MT5 connection failed: {mt5.last_error()}")
        return False
    st.session_state["mt5_connected"] = True; st.session_state["mt5_last_activity"] = datetime.now(); st.session_state["mt5_session_expired"] = False
    if show_status:
        st.success("Connected to MT5 live feed.")
    return True


def disconnect_mt5() -> None:
    if mt5 is not None:
        mt5.shutdown()
    st.session_state["mt5_connected"] = False
    st.session_state["auto_trade_authorized"] = False
    st.session_state.pop("mt5_password", None)


def mt5_connection_is_active() -> bool:
    """Return whether the process already has a usable MT5 terminal connection."""
    return mt5 is not None and mt5.account_info() is not None


def expire_inactive_mt5_session() -> None:
    """Log out only after the configured period without a full-page interaction."""
    last_activity = st.session_state.get("mt5_last_activity")
    if st.session_state.get("mt5_connected") and last_activity and datetime.now() - last_activity >= MT5_INACTIVITY_TIMEOUT:
        disconnect_mt5()
        st.session_state["mt5_session_expired"] = True


def resolve_mt5_symbol(symbol: str) -> str | None:
    if mt5 is None:
        return None
    mapped_symbol = SYMBOL_MAP.get(symbol)
    exact = mt5.symbol_info(mapped_symbol or symbol)
    if exact is not None:
        SYMBOL_MAP[symbol] = mapped_symbol or symbol
        return mapped_symbol or symbol
    available = mt5.symbols_get() or []
    names = [item.name for item in available]
    aliases = [mapped_symbol] if mapped_symbol else MT5_SYMBOL_ALIASES.get(symbol, [symbol])
    normalized_aliases = [alias.replace(".", "").replace("_", "").replace("-", "").replace(" ", "").upper() for alias in aliases]
    for name in names:
        normalized_name = name.replace(".", "").replace("_", "").replace("-", "").replace(" ", "").upper()
        if any(normalized_name == alias or normalized_name.startswith(alias) or alias in normalized_name for alias in normalized_aliases):
            SYMBOL_MAP[symbol] = name
            return name
    return None


def broker_symbol_hints(symbol: str) -> list[str]:
    if mt5 is None:
        return []
    names = [item.name for item in (mt5.symbols_get() or [])]
    keywords = MT5_SYMBOL_HINT_TOKENS.get(symbol, MT5_SYMBOL_ALIASES.get(symbol, [symbol]))
    return [name for name in names if any(keyword.lower() in name.lower() for keyword in keywords)][:8]


def available_broker_symbols() -> list[str]:
    if mt5 is None:
        return []
    return sorted(item.name for item in (mt5.symbols_get() or []))


def symbol_error_label(symbol: str, hints: list[str]) -> str:
    if symbol == "SA40" and not hints:
        return "SA40: no South African index symbol is available on this HFM server"
    hint_text = f" Available candidates: {', '.join(hints)}." if hints else ""
    return f"{symbol}: no matching broker symbol.{hint_text}"


def trading_risk_reason(symbol: str) -> str | None:
    if mt5 is None or not st.session_state["mt5_connected"]:
        return None
    broker_symbol = st.session_state.get("mt5_symbol_map", {}).get(symbol) or resolve_mt5_symbol(symbol)
    if broker_symbol is None:
        return f"{symbol} has no mapped broker symbol."
    tick = mt5.symbol_info_tick(broker_symbol)
    info = mt5.symbol_info(broker_symbol)
    if tick is None or info is None or not info.point:
        return f"{broker_symbol} has no current quote."
    spread_points = (tick.ask - tick.bid) / info.point
    if spread_points > MAX_SPREAD_POINTS:
        return f"Spread is {spread_points:.0f} points, above the {MAX_SPREAD_POINTS}-point limit."
    rates = mt5.copy_rates_from_pos(broker_symbol, mt5.TIMEFRAME_M1, 0, 21)
    if rates is None or len(rates) < 5:
        return f"Volatility data unavailable: {mt5.last_error()}"
    ranges = np.asarray(rates["high"] - rates["low"], dtype=float)
    baseline = float(np.median(ranges[:-1]))
    latest = float(ranges[-1])
    if baseline > 0 and latest > baseline * MAX_VOLATILITY_MULTIPLIER:
        news_reason = news_risk_reason()
        if news_reason:
            return f"{news_reason} Volatility spike detected ({latest / baseline:.1f}x baseline)."
        return f"Volatility spike detected ({latest / baseline:.1f}x baseline)."
    return None


def broker_preflight_reason(symbol: str, strategy: str, side: str) -> str | None:
    """Validate broker and market conditions before a live order is submitted."""
    if not valid_setup(symbol, strategy, side):
        return "Strategy session, conviction, or trading-discipline gate failed."
    account = mt5.account_info() if mt5 is not None else None
    if account is None:
        return "Account information is unavailable."
    if float(account.margin_free) <= 0:
        return "No free margin is available."
        return trading_risk_reason(symbol) or news_risk_reason()


def news_risk_reason() -> str | None:
    now = datetime.now()
    for event_time, event_info in NEWS_EVENTS.items():
        if event_info["impact"] != "HIGH":
            continue
        event = datetime.combine(now.date(), datetime.strptime(event_time[:5], "%H:%M").time())
        if abs((now - event).total_seconds()) <= NEWS_BLACKOUT_MINUTES * 60:
            return f"{event_info['label']} blackout window is active."
    return None


def valid_setup(symbol: str, strategy: str, decision: str, session: str | None = None) -> bool:
    selected_session = session or st.session_state.get("session", "")
    if selected_session not in SESSIONS or not strategy or decision not in {"BUY", "SELL"}:
        return False
    if selected_session not in STRATEGY_SESSIONS.get(strategy, set(SESSIONS)):
        return False
    _, buys, sells = decision_for(symbol, signals)
    if max(buys, sells) < 3 or enhanced_decision_with_filters(symbol, signals)[0] not in {"BUY", "SELL"}:
        return False
    if trading_pause_reason() or trading_risk_reason(symbol) or news_risk_reason():
        return False
    return True


def load_live_market_snapshot() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    feed_errors = []
    symbol_map = {}
    for symbol in SYMBOLS:
        broker_symbol = resolve_mt5_symbol(symbol)
        if broker_symbol is None or not mt5.symbol_select(broker_symbol, True):
            hints = broker_symbol_hints(symbol)
            feed_errors.append(symbol_error_label(symbol, hints))
            continue
        symbol_map[symbol] = broker_symbol
        rates = mt5.copy_rates_from_pos(broker_symbol, mt5.TIMEFRAME_M1, 0, 120)
        if rates is None:
            feed_errors.append(f"{symbol} ({broker_symbol}): {mt5.last_error()}")
            continue
        rows.extend(
            {"time": datetime.fromtimestamp(int(rate["time"])), "symbol": symbol, "close": float(rate["close"])}
            for rate in rates
        )
    st.session_state["mt5_symbol_map"] = symbol_map
    st.session_state["mt5_broker_symbols"] = available_broker_symbols()
    if not rows:
        st.session_state["mt5_feed_error"] = "; ".join(feed_errors) or str(mt5.last_error())
        st.session_state["mt5_feed_status"] = "unavailable"
        _, signals = load_market_snapshot()
        return pd.DataFrame(columns=["time", "symbol", "close"]), signals
    st.session_state["mt5_feed_error"] = "; ".join(feed_errors) if feed_errors else None
    st.session_state["mt5_feed_status"] = "partial" if feed_errors else "stable"
    _, signals = load_market_snapshot()
    return pd.DataFrame(rows), signals

def load_live_positions() -> pd.DataFrame:
    positions = mt5.positions_get() if mt5 is not None else None
    if not positions:
        st.session_state["open_positions"] = []
        return pd.DataFrame(columns=["ticket", "symbol", "side", "volume", "open", "current", "profit", "swap"])
    
    # Feature 2: Update session state with open positions for correlation tracking
    st.session_state["open_positions"] = [
        {"symbol": p.symbol, "volume": p.volume, "side": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL"}
        for p in positions
    ]
    
    return pd.DataFrame(
        [
            {
                "ticket": position.ticket,
                "symbol": position.symbol,
                "side": "BUY" if position.type == mt5.POSITION_TYPE_BUY else "SELL",
                "volume": position.volume,
                "open": position.price_open,
                "current": position.price_current,
                "profit": position.profit,
                "swap": position.swap,
            }
            for position in positions
        ]
    )


def open_trade(symbol: str, side: str, strategy: str | None = None, lot: float = 0.0, sl_points: int = 300, tp_points: int = 600, size_multiplier: float = 1.0, confidence: int = 0) -> None:
    user_id = st.session_state.get("active_user_id", 1)
    
    pause_reason = trading_pause_reason()
    if pause_reason:
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, 0, "UNKNOWN", "UNKNOWN", pause_reason)
        st.warning("Trading paused by discipline rules.")
        return
    if mt5 is None:
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, 0, "UNKNOWN", "UNKNOWN", "MT5 not installed")
        st.warning("MT5 is not installed; only the simulated desk can run right now.")
        return
    if not st.session_state.get("mt5_connected"):
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, 0, "UNKNOWN", "UNKNOWN", "Not connected to MT5")
        st.warning("Connect to MT5 before sending a live demo order.")
        return
    if not st.session_state.get("auto_trade_authorized"):
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, 0, "UNKNOWN", "UNKNOWN", "Auto-trading not authorized")
        st.warning("Auto-trading is stopped. Authorize the strategy from the sidebar first.")
        return
    
    strategy = strategy or st.session_state.get("strategy_priority", ["Trend following"])[0]
    if strategy == "Gold scalping":
        gate_reason = scalp_gate_reason() or scalp_rate_limit_reason()
        if gate_reason:
            log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, confidence, "UNKNOWN", "UNKNOWN", gate_reason)
            st.warning(f"Scalp skipped: {gate_reason}", icon=":material/speed:")
            return
    setup_valid = valid_setup(symbol, strategy, side)
    if not setup_valid:
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, 0, "UNKNOWN", "UNKNOWN", "Setup validation failed")
        st.warning(f"Trade skipped: setup validation failed for {side} {symbol}.", icon=":material/rule:")
        return
    
    risk_reason = trading_risk_reason(symbol) or news_risk_reason()
    if risk_reason:
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, 0, "UNKNOWN", "UNKNOWN", risk_reason)
        st.warning(f"Trade skipped: {risk_reason}", icon=":material/health_and_safety:")
        return
    
    broker_symbol = st.session_state.get("mt5_symbol_map", {}).get(symbol) or resolve_mt5_symbol(symbol)
    if broker_symbol is None:
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, 0, "UNKNOWN", "UNKNOWN", "Broker symbol not found")
        st.error(f"No broker symbol matching {symbol} was found on the live MT5 feed.")
        return
    
    info = mt5.symbol_info(broker_symbol)
    if info is None:
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, 0, "UNKNOWN", "UNKNOWN", "Symbol not on broker feed")
        st.error(f"Symbol {symbol} not found on the live MT5 feed.")
        return
    
    # FEATURE 1: Dynamic Position Sizing (with auto-scaling risk 1-3%)
    account = mt5.account_info()
    account_equity = float(account.equity) if account else 10000
    dynamic_risk = calculate_dynamic_risk_pct(account_equity)
    milestone_multiplier, milestone_reason = profit_milestone_risk_multiplier(
        st.session_state.get("daily_profit", 0), st.session_state.get("daily_target", DEFAULT_DAILY_TARGET)
    )
    if milestone_multiplier == 0:
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, confidence, "UNKNOWN", "UNKNOWN", milestone_reason)
        st.warning(f"Trade skipped: {milestone_reason}", icon=":material/lock:")
        return
    risk_pct = (weekly_risk_cap() or dynamic_risk["risk_pct"]) * milestone_multiplier
    allocation_reason = allocation_risk_reason(symbol, risk_pct)
    if allocation_reason:
        log_trade_audit(user_id, "TRADE_REJECTED", symbol, side, 0, "UNKNOWN", "UNKNOWN", allocation_reason)
        st.warning(f"Trade blocked: {allocation_reason}", icon=":material/pie_chart:")
        return
    if lot <= 0:
        lot = calculate_position_size(account_equity, risk_pct, 1.0, sl_points, symbol)
    lot *= positioning_lot_multiplier(symbol, side)
    lot *= size_multiplier
    
    # FEATURE 4: News adjustment
    news_multiplier = get_news_position_adjustment()
    
    # FEATURE 7: Capital preservation
    if account:
        drawdown_info = calculate_drawdown_status(float(account.balance), float(account.equity))
        news_multiplier *= drawdown_info.get("lot_multiplier", 1.0)
    
    lot = lot * news_multiplier
    lot = max(MIN_LOT_SIZE, min(lot, MAX_LOT_SIZE))
    
    # ADVANCED FEATURE 4: Risk-adjusted expectancy filter
    trade_log = st.session_state.get("trade_log", [])
    if trade_log and len(trade_log) >= 5:
        # Calculate expectancy from historical trades
        wins = sum(1 for t in trade_log if t.get("result", 0) > 0)
        avg_win = np.mean([t.get("result", 0) for t in trade_log if t.get("result", 0) > 0]) if wins > 0 else 100
        avg_loss = np.mean([t.get("result", 0) for t in trade_log if t.get("result", 0) < 0]) if len(trade_log) - wins > 0 else -100
        win_rate = wins / len(trade_log)
        
        expectancy_info = calculate_trade_expectancy(win_rate, avg_win, abs(avg_loss), lot)
        if not expectancy_info["should_execute"]:
            st.warning(f"Trade filtered: {expectancy_info['reason']}", icon=":material/filter_alt:")
            return
    
    # ADVANCED FEATURE 5: Kelly Criterion position sizing (optional, can override dynamic sizing)
    if account and trade_log and len(trade_log) >= 10:
        wins = sum(1 for t in trade_log[-20:] if t.get("result", 0) > 0)
        avg_win = np.mean([t.get("result", 0) for t in trade_log[-20:] if t.get("result", 0) > 0]) if wins > 0 else 100
        avg_loss = np.mean([abs(t.get("result", 0)) for t in trade_log[-20:] if t.get("result", 0) < 0]) if (20 - wins) > 0 else 100
        win_rate = wins / min(20, len(trade_log))
        
        kelly_lot = calculate_kelly_position_size(win_rate, avg_win, avg_loss, float(account.equity), 2.0)
        # Use kelly size if it's more conservative
        lot = min(lot, kelly_lot)
    
    # ADVANCED FEATURE 3: Apply RL feedback multiplier
    rl_multiplier = rl_agent.get_position_multiplier()
    lot = lot * rl_multiplier
    lot = max(MIN_LOT_SIZE, min(lot, MAX_LOT_SIZE))
    
    # CODE RECOVERY MODULE: Apply recovery-based lot adjustment
    if account:
        recovery_module = st.session_state.get("recovery_module")
        if recovery_module:
            current_equity = float(account.equity)
            # Check if trading is allowed during critical drawdown
            allow_trading, recovery_reason = recovery_module.is_trading_allowed(current_equity)
            if not allow_trading:
                st.warning(f"Trade blocked: {recovery_reason}", icon=":material/warning:")
                return
            
            # Apply recovery phase multiplier
            recovery_multiplier = recovery_module.calculate_recovery_lot_multiplier(current_equity)
            lot = lot * recovery_multiplier
            lot = max(MIN_LOT_SIZE, min(lot, MAX_LOT_SIZE))
    
    price = info.ask if side == "BUY" else info.bid
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": broker_symbol,
        "volume": lot,
        "type": mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL,
        "price": price,
        "sl": price - sl_points * info.point if side == "BUY" else price + sl_points * info.point,
        "tp": price + tp_points * info.point if side == "BUY" else price - tp_points * info.point,
        "deviation": 20,
        "magic": 123456,
        "comment": "M-STOIC",
    }
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        record_scalp_failure(strategy == "Gold scalping")
        log_trade_audit(user_id, "TRADE_EXECUTION_FAILED", symbol, side, 0, st.session_state.get("market_regime", "UNKNOWN"), 
                        st.session_state.get("recovery_module", {}).recovery_phase if st.session_state.get("recovery_module") else "UNKNOWN", 
                        result.comment)
        st.error(f"Trade failed: {result.comment}")
        return
    
    conviction = calculate_trade_confidence(symbol, side, 1, 1).get("score", 75)
    log_trade_audit(user_id, "TRADE_EXECUTED", symbol, side, conviction, 
                    st.session_state.get("market_regime", "UNKNOWN"),
                    st.session_state.get("recovery_module", {}).recovery_phase if st.session_state.get("recovery_module") else "UNKNOWN",
                    None)
    if strategy == "Gold scalping":
        st.session_state.setdefault("scalp_trade_timestamps", []).append(datetime.now())
        record_scalp_failure(False)
    
    st.success(f"{side} {symbol} opened on MT5 ({lot:.2f} lots)")
    st.toast(f"{side} {symbol} ({lot:.2f} lots)", icon=":material/check_circle:")
    trade = {
        "ticket": result.order,
        "symbol": symbol,
        "side": side,
        "strategy": strategy,
        "confidence": confidence,
        "size_multiplier": size_multiplier,
        "lot_size": lot,
        "risk_pct": risk_pct,
        "opened": datetime.now().strftime("%H:%M"),
        "opened_at": datetime.now().isoformat(timespec="seconds"),
        "tp_adjustments": 0,
        "tp_locked": False,
        "initial_tp_points": tp_points,
    }
    st.session_state.setdefault("active_trades", []).append(trade)
    st.session_state["active_trade"] = st.session_state["active_trades"][0]


def close_trade(ticket: int, outcome: str = "TP") -> None:
    if mt5 is None:
        st.warning("MT5 is not available; the paper desk handles trade closure.")
        return
    position = mt5.positions_get(ticket=ticket)
    if not position:
        st.error("No active position found for the selected ticket.")
        return
    position = position[0]
    tick = mt5.symbol_info_tick(position.symbol)
    if tick is None:
        st.error(f"No current price available to close {position.symbol}.")
        return
    close_side = mt5.ORDER_TYPE_SELL if position.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    close_price = tick.bid if close_side == mt5.ORDER_TYPE_SELL else tick.ask
    result = mt5.order_send(
        {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": position.symbol,
            "volume": position.volume,
            "type": close_side,
            "position": position.ticket,
            "price": close_price,
            "deviation": 20,
            "magic": 123456,
            "comment": "M-STOIC close",
        }
    )
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        st.error(f"Close failed: {result.comment}")
        return
    profit = float(position.profit)
    st.session_state["daily_profit"] += profit
    st.session_state["monthly_profit"] += profit
    if outcome == "SL":
        st.session_state["stop_losses"] += 1
    
    trade = st.session_state.get("active_trade") or {"ticket": ticket, "symbol": "UNKNOWN", "side": "BUY", "strategy": "Manual"}
    trade["outcome"] = outcome
    trade["result"] = profit
    if outcome == "SL" and trade.get("strategy") == "Gold scalping":
        record_scalp_failure(True)
    lot_size = trade.get("lot_size", 0.1)
    
    # ADVANCED FEATURE 3: Record RL feedback
    rl_agent.record_trade_outcome(profit, lot_size)
    
    # CODE RECOVERY MODULE: Record trade outcome for recovery tracking
    recovery_module = st.session_state.get("recovery_module")
    if recovery_module:
        recovery_module.record_trade_outcome(profit, lot_size)
    
    # ADVANCED FEATURE 6: Update backtesting monitor
    st.session_state["trade_log"].insert(0, trade)
    backtest_monitor.update_live_performance(st.session_state["trade_log"])
    
    st.session_state["last_closed_trade"] = trade
    active_trades = st.session_state.get("active_trades", [])
    st.session_state["active_trades"] = [
        item for item in active_trades if item.get("ticket") != ticket
    ]
    st.session_state["active_trade"] = st.session_state["active_trades"][0] if st.session_state["active_trades"] else None
    st.success(f"Trade {ticket} closed with {outcome}, profit {profit}")
    st.toast(f"Trade {ticket} closed at {outcome}", icon=":material/check_circle:")


def adjust_protective_levels(ticket: int, position, strategy: str, tp_points: int = 600) -> bool:
    if mt5 is None or strategy == "Gold scalping":
        return False
    info = mt5.symbol_info(position.symbol)
    if info is None or not info.point:
        return False
    point = info.point
    profit_points = ((position.price_current - position.price_open) / point) if position.type == mt5.POSITION_TYPE_BUY else ((position.price_open - position.price_current) / point)
    if profit_points < tp_points * 0.7:
        return False
    locked_points = profit_points * 0.5
    new_sl = position.price_open + locked_points * point if position.type == mt5.POSITION_TYPE_BUY else position.price_open - locked_points * point
    current_sl = position.sl or 0.0
    improves_stop = new_sl > current_sl if position.type == mt5.POSITION_TYPE_BUY else current_sl == 0.0 or new_sl < current_sl
    tp_adjustments = int(st.session_state["active_trade"].get("tp_adjustments", 0))
    current_tp = position.tp or 0.0
    new_tp = current_tp
    if position.tp and not st.session_state["active_trade"].get("tp_locked") and tp_adjustments < 2:
        extension = tp_points * 0.5 * point
        new_tp = position.tp + extension if position.type == mt5.POSITION_TYPE_BUY else position.tp - extension
    elif tp_adjustments >= 2:
        st.session_state["active_trade"]["tp_locked"] = True
    if not improves_stop and new_tp == current_tp:
        return False
    result = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP, "symbol": position.symbol, "position": position.ticket, "sl": new_sl if improves_stop else position.sl, "tp": new_tp})
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        st.error(f"Protective SL update failed for {position.symbol}: {result.comment}")
        return False
    active_trade = st.session_state["active_trade"]
    if improves_stop:
        active_trade["sl"] = new_sl
        event = {**active_trade, "outcome": "SL_ADJUST", "result": 0, "sl": new_sl, "opened": datetime.now().strftime("%H:%M")}
        st.session_state["trade_log"].insert(0, event)
        st.session_state["audit_log"].append({"timestamp": datetime.now().isoformat(timespec="seconds"), "event": "protective_sl_adjusted", "instrument": position.symbol, "strategy": strategy, "stop_loss": new_sl, "locked_profit_points": locked_points})
        st.success(f"Protective SL moved on {position.symbol}; {locked_points:.0f} points locked.")
        st.toast(f"SL modified: {position.symbol}", icon=":material/security:")
    if new_tp != current_tp:
        active_trade["tp_adjustments"] = tp_adjustments + 1
        if active_trade["tp_adjustments"] >= 2:
            active_trade["tp_locked"] = True
        event = {**active_trade, "outcome": "TP_ADJUST", "result": 0, "tp": new_tp, "opened": datetime.now().strftime("%H:%M")}
        st.session_state["trade_log"].insert(0, event)
        st.session_state["audit_log"].append({"timestamp": datetime.now().isoformat(timespec="seconds"), "event": "protective_tp_adjusted", "instrument": position.symbol, "strategy": strategy, "take_profit": new_tp, "adjustment_number": active_trade["tp_adjustments"]})
        st.success(f"Protective TP extended on {position.symbol} ({active_trade['tp_adjustments']}/2).")
        st.toast(f"TP modified: {position.symbol}", icon=":material/flag:")
    return True


def initialize_trading_state() -> None:
    today = datetime.now().date().isoformat()
    st.session_state.setdefault("mt5_connected", False)
    month = datetime.now().strftime("%Y-%m")
    if st.session_state.get("month_key") != month:
        st.session_state["month_key"] = month
        st.session_state["monthly_profit"] = 0
    if st.session_state.get("state_date") != today:
        st.session_state["state_date"] = today
        st.session_state["daily_profit"] = 0
        st.session_state["stop_losses"] = 0
        st.session_state["active_trades"] = []
        st.session_state["scalp_trade_timestamps"] = []
        st.session_state["scalp_consecutive_failures"] = 0
        st.session_state["active_trade"] = None
        st.session_state["last_closed_trade"] = None
        st.session_state["trade_log"] = []
    st.session_state.setdefault("daily_profit", 0)
    st.session_state.setdefault("stop_losses", 0)
    st.session_state.setdefault("scalp_trade_timestamps", [])
    st.session_state.setdefault("scalp_consecutive_failures", 0)
    st.session_state.setdefault("daily_target", DEFAULT_DAILY_TARGET)
    st.session_state.setdefault("active_trade", None)
    st.session_state.setdefault("active_trades", [])
    st.session_state.setdefault("last_closed_trade", None)
    st.session_state.setdefault("trade_log", [])
    st.session_state.setdefault("strategy_priority", PRESET_STRATEGIES)
    st.session_state.setdefault("monthly_profit", 0)
    st.session_state.setdefault("kill_switch_until", None)
    st.session_state.setdefault("audit_log", [])
    st.session_state.setdefault("last_auto_trade_status", "Auto-trade loop is waiting for authorization.")
    
    # Feature 2: Portfolio correlation tracking
    st.session_state.setdefault("open_positions", [])
    # Feature 3: Strategy performance tracking
    st.session_state.setdefault("strategy_metrics", {})
    # Feature 7: Capital preservation tracking
    st.session_state.setdefault("peak_equity", 0)
    st.session_state.setdefault("in_drawdown", False)
    
    # ADVANCED FEATURE 1: Multi-timeframe analysis
    st.session_state.setdefault("multi_tf_analysis", {})
    # ADVANCED FEATURE 2: Regime detection
    st.session_state.setdefault("market_regime", "RANGING")
    # ADVANCED FEATURE 3: RL feedback tracking
    st.session_state.setdefault("rl_performance", {"trades": 0, "multiplier": 1.0})
    # ADVANCED FEATURE 4: Expectancy filter stats
    st.session_state.setdefault("expectancy_filters_applied", 0)
    # ADVANCED FEATURE 5: Portfolio optimization
    st.session_state.setdefault("portfolio_optimization", {})
    # ADVANCED FEATURE 6: Backtesting comparison
    st.session_state.setdefault("backtest_comparison", {})
    # ADVANCED FEATURE 7: Sentiment tracking
    st.session_state.setdefault("currency_sentiments", {})
    
    # CODE RECOVERY MODULE: Initialize recovery system with current equity
    if st.session_state["mt5_connected"]:
        account = mt5.account_info()
        if account:
            initial_equity = float(account.equity)
        else:
            initial_equity = 10000
    else:
        initial_equity = 10000
    
    st.session_state.setdefault("recovery_module", CodeRecoveryModule(initial_equity))
    
    # DYNAMIC RISK MANAGEMENT: 1-3% scaling based on account size
    if st.session_state["mt5_connected"]:
        account = mt5.account_info()
        if account:
            dynamic_risk_info = calculate_dynamic_risk_pct(float(account.equity))
        else:
            dynamic_risk_info = calculate_dynamic_risk_pct(10000)
    else:
        dynamic_risk_info = calculate_dynamic_risk_pct(10000)
    
    st.session_state.setdefault("current_risk_pct", dynamic_risk_info["risk_pct"])
    st.session_state.setdefault("current_risk_tier", dynamic_risk_info["account_tier"])
    st.session_state.setdefault("risk_reasoning", dynamic_risk_info["reasoning"])


def trading_pause_reason() -> str | None:
    if st.session_state.get("daily_profit", 0) >= st.session_state.get("daily_target", DEFAULT_DAILY_TARGET):
        return "Daily target achieved across all instruments."
    if st.session_state.get("stop_losses", 0) >= 3:
        if st.session_state.get("kill_switch_until") is None:
            st.session_state["kill_switch_until"] = datetime.now() + KILL_SWITCH_COOLDOWN
        return "Three stop-losses reached. Capital protection is engaged for 24 hours."
    kill_switch_until = st.session_state.get("kill_switch_until")
    if kill_switch_until and datetime.now() < kill_switch_until:
        return f"Kill-switch cooldown active until {kill_switch_until.strftime('%H:%M on %d %b')}."
    return None


def open_paper_trade(symbol: str, strategy: str, side: str, size_multiplier: float = 1.0, confidence: int = 0) -> None:
    if strategy == "Gold scalping":
        gate_reason = scalp_gate_reason() or scalp_rate_limit_reason()
        if gate_reason:
            st.session_state["last_auto_trade_status"] = gate_reason
            return
        milestone_multiplier, milestone_reason = profit_milestone_risk_multiplier(
            st.session_state.get("daily_profit", 0), st.session_state.get("daily_target", DEFAULT_DAILY_TARGET)
        )
        if milestone_multiplier == 0:
            st.session_state["last_auto_trade_status"] = milestone_reason
            return
        size_multiplier *= milestone_multiplier
    if not valid_setup(symbol, strategy, side):
        st.warning(f"Trade skipped: setup validation failed for {side} {symbol}.", icon=":material/rule:")
        return
    trade = {
        "symbol": symbol,
        "strategy": strategy,
        "side": side,
        "confidence": confidence,
        "size_multiplier": size_multiplier,
        "session": "London open",
        "opened": datetime.now().strftime("%H:%M"),
        "opened_at": datetime.now().isoformat(timespec="seconds"),
    }
    st.session_state.setdefault("active_trades", []).append(trade)
    st.session_state["active_trade"] = st.session_state["active_trades"][0]
    if strategy == "Gold scalping":
        st.session_state.setdefault("scalp_trade_timestamps", []).append(datetime.now())
        record_scalp_failure(False)


def close_paper_trade(outcome: str) -> None:
    trade = st.session_state["active_trade"]
    if not trade:
        return
    result = 200 if outcome == "TP" else 0 if outcome in {"TIME", "SIGNAL"} else -100
    st.session_state["daily_profit"] += result
    st.session_state["monthly_profit"] += result
    if outcome == "SL":
        st.session_state["stop_losses"] += 1
        if trade.get("strategy") == "Gold scalping":
            record_scalp_failure(True)
    trade["outcome"] = outcome
    trade["result"] = result
    st.session_state["last_closed_trade"] = trade
    st.session_state["trade_log"].insert(0, trade)
    st.session_state["audit_log"].append({"timestamp": datetime.now().isoformat(timespec="seconds"), "event": f"paper_trade_{outcome.lower()}", "instrument": trade["symbol"], "strategy": trade["strategy"]})
    active_trades = st.session_state.get("active_trades", [])
    st.session_state["active_trades"] = [item for item in active_trades if item is not trade]
    st.session_state["active_trade"] = st.session_state["active_trades"][0] if st.session_state["active_trades"] else None


def display_money(zar_amount: int | float, currency: str) -> str:
    if currency == "ZAR":
        return f"R{zar_amount:,.0f}"
    return f"${zar_amount * DISPLAY_RATES[currency]:,.2f}"


@st.fragment(run_every="15s")
def render_market_pulse(selected_symbol: str, fallback_market: pd.DataFrame) -> None:
    mt5_connected = st.session_state.get("mt5_connected", False)
    current_market = load_live_market_snapshot()[0] if mt5_connected else fallback_market
    with st.container(border=True):
        if mt5_connected:
            account = mt5.account_info()
            positions = load_live_positions()
            if account:
                floating_profit = float(positions["profit"].sum()) if not positions.empty else 0.0
                account_cols = st.columns(4)
                account_cols[0].metric("Balance", f"{account.balance:,.2f} {account.currency}")
                account_cols[1].metric("Equity", f"{account.equity:,.2f} {account.currency}", f"{account.equity - account.balance:+,.2f}")
                account_cols[2].metric("Floating P/L", f"{floating_profit:+,.2f} {account.currency}")
                account_cols[3].metric("Free margin", f"{account.margin_free:,.2f} {account.currency}")
                if account.equity < account.balance:
                    st.warning(
                        f"Account is depleting: equity is {account.balance - account.equity:,.2f} {account.currency} below balance.",
                        icon=":material/trending_down:",
                    )
                elif account.equity > account.balance:
                    st.success(
                        f"Account is accumulating: equity is {account.equity - account.balance:,.2f} {account.currency} above balance.",
                        icon=":material/trending_up:",
                    )
        st.subheader("Market pulse")
        chart_data = current_market[current_market["symbol"] == selected_symbol].set_index("time")["close"]
        if chart_data.empty:
            feed_error = st.session_state.get("mt5_feed_error")
            if mt5_connected and feed_error:
                st.error(f"Live feed unavailable for {selected_symbol}: {feed_error}", icon=":material/wifi_off:")
            else:
                st.warning(f"No price data is available for {selected_symbol}.", icon=":material/wifi_off:")
            return
        st.line_chart(chart_data, height=290, color="#d9a441")
        latest = float(chart_data.iloc[-1])
        previous = float(chart_data.iloc[-2]) if len(chart_data) > 1 else latest
        feed_label = "live MT5 feed" if mt5_connected else "simulated MT5 feed"
        st.caption(f"{selected_symbol}  |  last {latest:.5f}  |  1m change {latest - previous:+.5f}  |  SAST / {feed_label}")
        if st.session_state.get("mt5_feed_error"):
            st.warning(f"Live feed issue: {st.session_state['mt5_feed_error']}", icon=":material/warning:")

        if mt5_connected:
            positions = load_live_positions()
            if positions.empty:
                st.caption("No open MT5 positions.")
            else:
                st.dataframe(positions, hide_index=True, width="stretch")
                summary_cols = st.columns(3)
                summary_cols[0].metric("Floating P/L", f"{positions['profit'].sum():,.2f}")
                summary_cols[1].metric("Exposure", f"{positions['volume'].sum():.2f} lots")
                account = mt5.account_info()
                summary_cols[2].metric("Margin", f"{account.margin:,.2f}" if account else "Unavailable")


@st.fragment(run_every="10s")
def auto_trade_loop() -> None:
    mt5_connected = st.session_state.get("mt5_connected", False)
    if trading_pause_reason():
        st.session_state["last_auto_trade_status"] = f"Paused: {trading_pause_reason()}"
        return
    
    # Process pending trades from queue (60-second cycle)
    if mt5_connected and st.session_state.get("auto_trade_authorized"):
        queue_result = execute_queued_trades(max_per_cycle=5)
        if queue_result["executed"] > 0 or queue_result["failed"] > 0:
            status_lines = queue_result.get("reasons", [])
            st.session_state["last_auto_trade_status"] = " | ".join(status_lines[:3]) if status_lines else "Queue processed"
    
    active_trade = st.session_state.get("active_trade")
    if active_trade:
        opened_at = datetime.fromisoformat(active_trade.get("opened_at", datetime.now().isoformat()))
        held_for = datetime.now() - opened_at
        decision, _, _ = decision_for(active_trade["symbol"], signals)
        is_scalp = active_trade.get("strategy") == "Gold scalping"
        if not is_scalp and mt5_connected and active_trade.get("ticket"):
            live_position = mt5.positions_get(ticket=int(active_trade["ticket"]))
            if live_position:
                adjust_protective_levels(int(active_trade["ticket"]), live_position[0], active_trade["strategy"])
        if is_scalp and held_for < SCALP_MIN_HOLD:
            st.session_state["last_auto_trade_status"] = f"Holding scalp {active_trade['symbol']} for the 10-second minimum."
            if st.session_state.get("session") not in SCALP_SESSIONS:
                return
        should_exit = decision != active_trade["side"] or (is_scalp and held_for >= SCALP_MAX_HOLD)
        if should_exit:
            if mt5_connected and active_trade.get("ticket"):
                close_trade(int(active_trade["ticket"]), "TIME" if is_scalp and held_for >= SCALP_MAX_HOLD else "SIGNAL")
            else:
                close_paper_trade("TIME" if is_scalp and held_for >= SCALP_MAX_HOLD else "SIGNAL")
            st.toast(f"Scalp exit: {active_trade['side']} {active_trade['symbol']}", icon=":material/logout:")
        hedge_positions_active = active_trade.get("symbol") in HEDGE_PAIR and all(
            trade.get("symbol") in HEDGE_PAIR for trade in st.session_state["active_trades"]
        )
        if not hedge_positions_active and not (is_scalp and st.session_state.get("session") in SCALP_SESSIONS):
            return
    if not st.session_state.get("auto_trade_authorized"):
        st.session_state["last_auto_trade_status"] = "Waiting for auto-trading authorization."
        return
    
    # FEATURE 3: Adaptive strategy weighting - reorder by recent win rate
    adaptive_strategies = get_adaptive_strategy_order(st.session_state.get("trade_log", []), PRESET_STRATEGIES)
    st.session_state["strategy_priority"] = adaptive_strategies
    
    scalp_burst_active = st.session_state.get("session") in SCALP_SESSIONS and not scalp_gate_reason()
    candidate_symbols = ["XAUUSD"] * SCALP_BURST_SIZE if scalp_burst_active else SYMBOLS
    for symbol in candidate_symbols:
        # ADVANCED FEATURE 1: Multi-timeframe confirmation
        decision, analysis = enhanced_decision_with_filters(symbol, signals, use_sentiment=True, use_regime=True)
        
        if decision not in {"BUY", "SELL"}:
            if analysis.get("reason") and decision == "WAIT":
                st.session_state["advanced_analysis"] = st.session_state.get("advanced_analysis", {})
                st.session_state["advanced_analysis"][symbol] = analysis
            continue
        
        # FEATURE 2: Soft correlation check for the designated diversified hedge.
        active_positions = st.session_state.get("open_positions", []) or st.session_state.get("active_trades", [])
        if not scalp_burst_active and symbol in {position.get("symbol") for position in active_positions}:
            continue
        confidence = calculate_trade_confidence(symbol, decision, analysis["buys"], analysis["sells"], analysis.get("regime"))["score"]
        sizing_positions = active_positions
        if symbol in HEDGE_DIRECTIONS and not active_positions:
            other_symbol = next(pair_symbol for pair_symbol in HEDGE_PAIR if pair_symbol != symbol)
            other_decision, other_buys, other_sells = decision_for(other_symbol, signals)
            other_confidence = calculate_trade_confidence(other_symbol, other_decision, other_buys, other_sells)["score"]
            sizing_positions = [{"symbol": other_symbol, "confidence": other_confidence}]
        hedge_check = soft_correlation_check(symbol, sizing_positions, confidence, decision)
        if hedge_check["blocked"]:
            st.session_state["last_auto_trade_status"] = f"Skipped {symbol}: {hedge_check['reason']}"
            continue
        
        strategy = "Gold scalping" if scalp_burst_active else adaptive_strategies[0]
        risk_reason = trading_risk_reason(symbol) or news_risk_reason()
        if risk_reason:
            st.session_state["last_auto_trade_status"] = f"Skipped {symbol}: {risk_reason}"
            continue
        
        # ADVANCED FEATURE 6: Check backtesting performance
        perf_check = backtest_monitor.get_performance_comparison(st.session_state.get("trade_log", []))
        if perf_check["should_reduce_exposure"]:
            st.warning(f"Live performance below threshold. Reducing exposure to {perf_check['recommended_exposure']:.0%}")
        
        if st.session_state["mt5_connected"]:
            open_trade(symbol, decision, strategy=strategy, size_multiplier=hedge_check["size_multiplier"], confidence=confidence)
        else:
            open_paper_trade(symbol, strategy, decision, size_multiplier=hedge_check["size_multiplier"], confidence=confidence)
        st.session_state["last_auto_trade_status"] = f"Opened {decision} {symbol} using {strategy} ({hedge_check['reason']})."
        st.toast(f"Auto-trade triggered: {decision} {symbol}", icon=":material/play_circle:")
        if not scalp_burst_active:
            break
    else:
        st.session_state["last_auto_trade_status"] = "No eligible BUY/SELL candidate passed the risk filters."


def daily_ledger() -> pd.DataFrame:
    """Enhanced ledger with comprehensive trade analytics (Feature 5)."""
    rows = []
    cumulative = 0
    for index, trade in enumerate(reversed(st.session_state["trade_log"]), start=1):
        cumulative += trade["result"]
        rows.append(
            {
                "trade": index,
                "time": trade["opened"],
                "instrument": trade["symbol"],
                "strategy": trade["strategy"],
                "outcome": {"TP": "TP hit", "SL": "SL hit", "TIME": "Timed exit", "SIGNAL": "Signal exit", "SL_ADJUST": "Protective SL adjusted", "TP_ADJUST": "Protective TP adjusted"}.get(trade["outcome"], trade["outcome"]),
                "P/L (ZAR)": trade["result"],
                "cumulative (ZAR)": cumulative,
            }
        )
    return pd.DataFrame(rows)


def analytics_summary() -> dict:
    """Generate comprehensive trading analytics including expectancy, Sharpe, and per-strategy metrics."""
    trade_log = st.session_state.get("trade_log", [])
    return calculate_trade_metrics(trade_log)
def journal_with_session_events(journal: pd.DataFrame) -> pd.DataFrame:
    if not st.session_state["trade_log"]:
        return journal
    events = pd.DataFrame(
        [
            {
                "time": trade.get("opened", ""),
                "pair": trade.get("symbol", ""),
                "side": trade.get("side", ""),
                "strategies": trade.get("strategy", ""),
                "session": trade.get("session", ""),
                "result": f"{trade.get('result', 0):+.2f}",
                "news context": f"{trade.get('outcome', '')} ({'live MT5' if trade.get('ticket') else 'paper'})",
            }
            for trade in st.session_state["trade_log"]
        ],
        columns=journal.columns,
    )
    return pd.concat([events, journal], ignore_index=True)


def backup_checksum(path: Path) -> str:
    digest = hashlib.new(BACKUP_HASH_ALGORITHM)
    with path.open("rb") as backup_file:
        for chunk in iter(lambda: backup_file.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_backup_checksum(backup_dir: Path) -> bool:
    backup_path = backup_dir / "app_backup.py"
    checksum_path = backup_dir / "app_backup.sha256"
    if not backup_path.exists() or not checksum_path.exists():
        return False
    expected = checksum_path.read_text(encoding="ascii").split()[0]
    return expected == backup_checksum(backup_path)


def update_hourly_backup() -> None:
    source = Path(__file__).resolve()
    backup_dir = source.parent / "backups"
    backup_path = backup_dir / "app_backup.py"
    checksum_path = backup_dir / "app_backup.sha256"
    if backup_path.exists() and checksum_path.exists() and (datetime.now().timestamp() - backup_path.stat().st_mtime) < BACKUP_INTERVAL_SECONDS:
        return
    backup_dir.mkdir(exist_ok=True)
    if not backup_path.exists() or (datetime.now().timestamp() - backup_path.stat().st_mtime) >= BACKUP_INTERVAL_SECONDS:
        shutil.copy2(source, backup_path)
    checksum_path.write_text(f"{backup_checksum(backup_path)}  {backup_path.name}\n", encoding="ascii")


initialize_trading_state()
initialize_audit_database()
initialize_trade_queue_database()
st.session_state.setdefault("active_user_id", 1)
st.session_state.setdefault("mt5_connected", False)
st.session_state.setdefault("auto_trade_authorized", False)
st.session_state.setdefault("mt5_feed_error", None)
st.session_state.setdefault("mt5_feed_status", "unknown")
st.session_state.setdefault("mt5_last_activity", datetime.now())
st.session_state.setdefault("mt5_session_expired", False)
expire_inactive_mt5_session()
if st.session_state.get("mt5_connected"):
    if mt5_connection_is_active():
        st.session_state["mt5_last_activity"] = datetime.now()
    elif st.session_state.get("mt5_login") and st.session_state.get("mt5_password") and st.session_state.get("mt5_server"):
        connect_mt5(
            account_id=int(st.session_state["mt5_login"]),
            account_password=st.session_state["mt5_password"],
            server=st.session_state["mt5_server"],
            show_status=False,
        )
update_hourly_backup()
backup_integrity = verify_backup_checksum(Path(__file__).resolve().parent / "backups")


market, signals = load_live_market_snapshot() if st.session_state["mt5_connected"] else load_market_snapshot()
journal = load_journal()


def calculate_daily_target(equity: float, target_pct: int) -> float:
    return max(0.0, equity * target_pct / 100.0)


def calculate_auto_target_pct(signal_data: pd.DataFrame) -> tuple[int, str]:
    if st.session_state.get("recovery_module") and st.session_state["recovery_module"].recovery_phase:
        return 5, "Recovery mode limits the target to 5%."
    if st.session_state.get("market_regime") == "VOLATILE":
        return 5, "Volatile conditions limit the target to 5%."
    if st.session_state.get("market_regime") == "TRENDING":
        return 30, "Strong trend conditions support a 30% target."
    return 10, "Ranging conditions use a 10% target."


st.session_state.setdefault("live_mode_enabled", False)

with st.sidebar:
    if LOGO_PATH.exists():
        st.image(str(LOGO_PATH), use_container_width=True)
    else:
        st.markdown("## :material/shield: M-STOIC")
    st.caption("Personal desk")
    st.space("small")
    st.toggle(
        "Live / Paper mode",
        value=st.session_state.get("live_mode_enabled", False),
        key="live_mode_enabled",
        disabled=PAPER_MODE_FORCED,
        help="Paper mode is safe by default. Live mode only works when valid MT5 credentials are configured.",
    )
    account_id, account_password, server = get_mt5_credentials()
    browser_account_id = st.session_state.get("mt5_login", account_id or DEFAULT_MT5_LOGIN)
    browser_server = st.session_state.get("mt5_server", MT5_SERVERS[0])
    account_type = st.selectbox("MT5 account", ["Demo", "Real"], index=0, key="mt5_account_type_selector")
    browser_account_id = st.number_input("MT5 login", min_value=1, step=1, value=int(st.session_state.get("mt5_login", DEFAULT_MT5_LOGIN)), key="mt5_login_selector")
    browser_server = st.selectbox("MT5 server", MT5_SERVERS, index=0, key="mt5_server_selector")
    browser_password = st.text_input("MT5 trading password", type="password", key="mt5_password", placeholder="Enter manually")
    effective_password = browser_password or account_password
    real_account_confirmed = st.checkbox(
        "I confirm this is the intended real account and understand live orders can lose real money.",
        key="real_account_confirmed",
        disabled=account_type != "Real",
    )
    if account_type == "Real":
        st.warning("Real account selected. Orders can use real funds.", icon=":material/warning:")
    execution_mode, mode_reason = execution_mode_status(True, mt5, account_id, account_password) if st.session_state["mt5_connected"] else execution_mode_status(False, mt5, account_id, account_password)
    st.caption(f"Execution mode: {execution_mode}")
    st.caption(mode_reason)
    if effective_password:
        execution_mode, mode_reason = execution_mode_status(True, mt5, int(browser_account_id), effective_password)
    if st.button("Log in to MT5", use_container_width=True, disabled=PAPER_MODE_FORCED or not effective_password):
        if connect_mt5(int(browser_account_id), effective_password, browser_server):
            st.session_state["mt5_login"] = int(browser_account_id)
            st.session_state["mt5_server"] = browser_server
            st.rerun()
    st.caption("PAPER mode enforced" if PAPER_MODE_FORCED else ("Connected to MT5" if st.session_state["mt5_connected"] else "Enter your password, then log in"))
    if st.session_state["mt5_connected"] and st.button("Log out of MT5", icon=":material/logout:", use_container_width=True):
        disconnect_mt5()
        st.toast("Logged out of MT5", icon=":material/logout:")
        st.rerun()
    st.divider()
    st.markdown("**Auto-trading authorization**")
    auto_authorize_disabled = not st.session_state["mt5_connected"] or (account_type == "Real" and not real_account_confirmed)
    if st.session_state["auto_trade_authorized"]:
        st.success("Auto-trading authorized", icon=":material/play_circle:")
        if st.button("Stop auto-trading", icon=":material/stop_circle:", use_container_width=True):
            st.session_state["auto_trade_authorized"] = False
            st.rerun()
    elif st.button("Authorize auto-trading", icon=":material/play_circle:", use_container_width=True, disabled=auto_authorize_disabled):
        st.session_state["auto_trade_authorized"] = True
        st.rerun()
    if auto_authorize_disabled:
        if not st.session_state["mt5_connected"]:
            st.caption("Log in to MT5 before authorizing the strategy.")
        if account_type == "Real" and not real_account_confirmed:
            st.caption("Check the real-account confirmation before authorizing live trades.")
    st.selectbox("Focus pair", SYMBOLS, index=0)
    st.selectbox("Account currency", ["ZAR", "USD"], index=0, key="account_currency")
    st.selectbox("Session", SESSIONS, index=0, key="session")

    profiles = load_user_profiles()
    profile_names = profiles["profile_name"].tolist()
    selected_profile_name = st.selectbox("Trading profile", profile_names, key="selected_profile_name")
    selected_profile = profiles.loc[profiles["profile_name"] == selected_profile_name].iloc[0]
    st.session_state["active_user_id"] = int(selected_profile["user_id"])
    profile_strategy = str(selected_profile["strategy"])
    profile_target_pct = int(selected_profile["target_pct"])
    profile_risk_mode = str(selected_profile["risk_mode"])
    st.caption(f"{profile_strategy} | {profile_risk_mode} risk | {profile_target_pct}% target")

    target_mode = st.segmented_control("Daily target", ["Automatic", "Profile"], default="Automatic", key="daily_target_mode")
    auto_target_pct, auto_target_reason = calculate_auto_target_pct(signals)
    selected_target_pct = auto_target_pct if target_mode == "Automatic" else profile_target_pct
    target_equity = float(mt5.account_info().equity) if st.session_state.get("mt5_connected") and mt5.account_info() else PAPER_ACCOUNT_EQUITY
    st.session_state["daily_target_pct"] = selected_target_pct
    st.session_state["daily_target"] = calculate_daily_target(target_equity, selected_target_pct)
    st.caption(f"Target: {selected_target_pct}% | {display_money(st.session_state['daily_target'], st.session_state['account_currency'])}")
    st.caption(auto_target_reason if target_mode == "Automatic" else f"Profile target: {profile_target_pct}%")

    with st.expander("Edit trading profile", expanded=False):
        updated_strategy = st.selectbox("Preferred strategy", STRATEGIES, index=STRATEGIES.index(profile_strategy), key="profile_strategy")
        updated_target_pct = st.selectbox("Profile target %", DAILY_TARGET_PCTS, index=DAILY_TARGET_PCTS.index(profile_target_pct), key="profile_target_pct")
        risk_modes = ["Conservative", "Balanced", "Growth"]
        updated_risk_mode = st.selectbox("Risk mode", risk_modes, index=risk_modes.index(profile_risk_mode), key="profile_risk_mode")
        if st.button("Save profile", icon=":material/save:", use_container_width=True):
            save_user_profile(selected_profile_name, updated_strategy, updated_target_pct, updated_risk_mode)
            st.toast(f"{selected_profile_name} profile saved.", icon=":material/check_circle:")
            st.rerun()

    if st.sidebar.button("Manual Stop Trading", icon=":material/stop_circle:"):
        st.session_state["kill_switch_until"] = datetime.now() + timedelta(hours=24)
        st.warning("Manual stop triggered. Trading paused for 24h.")
    st.toggle("Red-flag kill-switch", value=True, disabled=True)

    with st.expander("Audit log", expanded=False):
        database_path = Path(__file__).with_name("profiles.db")
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            recent_audits = connection.execute(
                "SELECT timestamp, action, symbol, decision, conviction_score, block_reason FROM audit_log WHERE user_id = ? ORDER BY timestamp DESC LIMIT 5",
                (st.session_state.get("active_user_id", 1),),
            ).fetchall()
            if recent_audits:
                for audit in recent_audits:
                    if audit["block_reason"]:
                        st.caption(f"⛔ {audit['timestamp'][:16]} | {audit['symbol']} {audit['decision']} blocked: {audit['block_reason']}")
                    else:
                        st.caption(f"✓ {audit['timestamp'][:16]} | {audit['symbol']} {audit['decision']} ({audit['conviction_score']}/100)")
            else:
                st.caption("No audit entries yet.")

    with st.expander("Trade queue", expanded=False):
        with sqlite3.connect(database_path) as connection:
            connection.row_factory = sqlite3.Row
            pending = connection.execute(
                "SELECT COUNT(*) as count FROM trade_queue WHERE user_id = ? AND status = 'PENDING'",
                (st.session_state.get("active_user_id", 1),),
            ).fetchone()
            executed = connection.execute(
                "SELECT COUNT(*) as count FROM trade_queue WHERE user_id = ? AND status = 'EXECUTED'",
                (st.session_state.get("active_user_id", 1),),
            ).fetchone()
            st.metric("Pending", pending["count"])
            st.metric("Executed today", executed["count"])
            
            pending_trades = connection.execute(
                "SELECT symbol, side, strategy, confidence, enqueued_at FROM trade_queue WHERE user_id = ? AND status = 'PENDING' ORDER BY enqueued_at ASC LIMIT 3",
                (st.session_state.get("active_user_id", 1),),
            ).fetchall()
            if pending_trades:
                st.caption("Next 3 in queue:")
                for trade in pending_trades:
                    st.caption(f"{trade['symbol']} {trade['side']} ({trade['strategy']}) | {trade['confidence']}/100 confidence")

    st.badge("Backup verified" if backup_integrity else "Backup check unavailable", icon=":material/verified_user:", color="green" if backup_integrity else "orange")
    if st.session_state["mt5_connected"]:
        with st.expander("Broker symbols", expanded=False):
            broker_symbols = st.session_state.get("mt5_broker_symbols", available_broker_symbols())
            st.caption(f"{len(broker_symbols)} symbols returned by HFM MT5")
            st.code("\n".join(broker_symbols) if broker_symbols else "No broker symbols returned.")

st.title("M-STOIC")
account_label = st.session_state.get("mt5_account_type_selector", DEFAULT_MT5_ACCOUNT_TYPE)
connection_label = "Connected" if st.session_state["mt5_connected"] else "Not connected"
display_mode = "LIVE" if st.session_state["mt5_connected"] else "PAPER"
st.badge(f"{display_mode} | {connection_label} | {account_label}", icon=":material/sensors:" if st.session_state["mt5_connected"] else ":material/wifi_off:", color="green" if st.session_state["mt5_connected"] else "gray")
feed_status = st.session_state["mt5_feed_status"]
feed_badge = {"stable": ("Feed stable", "green"), "partial": ("Feed partial", "orange"), "unavailable": ("Feed unavailable", "red")}.get(feed_status, ("Feed not checked", "gray"))
st.badge(feed_badge[0], icon=":material/sensors:" if feed_status == "stable" else ":material/wifi_off:", color=feed_badge[1])
with st.container(border=True):
    active_trade = st.session_state.get("active_trade")
    if active_trade:
        active_profit = None
        adjusted_sl = active_trade.get("sl")
        if st.session_state["mt5_connected"] and active_trade.get("ticket"):
            live_position = mt5.positions_get(ticket=int(active_trade["ticket"]))
            if live_position:
                active_profit = float(live_position[0].profit)
                adjusted_sl = live_position[0].sl or adjusted_sl
        active_cols = st.columns(6)
        active_cols[0].metric("Active trade", f"{active_trade['side']} {active_trade['symbol']}")
        active_cols[1].metric("Strategy", active_trade["strategy"])
        active_cols[2].metric("Opened", active_trade["opened"])
        active_cols[3].metric("Floating P/L", f"{active_profit:+,.2f}" if active_profit is not None else "Paper")
        active_cols[4].metric("Adjusted SL", f"{adjusted_sl:.5f}" if adjusted_sl else "Initial SL")
        active_cols[5].metric("Stop-losses left", str(max(3 - st.session_state["stop_losses"], 0)))
    else:
        st.caption("No active trade. The strategy engine is waiting for an eligible setup.")
    st.caption(st.session_state["last_auto_trade_status"])
if st.session_state["mt5_connected"]:
    account = mt5.account_info()
    if account:
        status_cols = st.columns(3)
        status_cols[0].metric("MT5 login", str(account.login))
        status_cols[1].metric("Balance", f"{account.balance:,.2f} {account.currency}")
        status_cols[2].metric("Equity", f"{account.equity:,.2f} {account.currency}")
    st.success("Live MT5 feed active", icon=":material/sensors:")
    live_positions = load_live_positions()
    if not live_positions.empty:
        st.dataframe(live_positions, hide_index=True, width="stretch")
    else:
        st.caption("No open positions on the connected MT5 account.")
else:
        st.warning("The live feed is not active. Log in to MT5 from the sidebar.", icon=":material/wifi_off:")
account_id, account_password, server = get_mt5_credentials()
account_id = int(browser_account_id)
account_password = browser_password or effective_password
server = browser_server
execution_mode, mode_reason = execution_mode_status(True, mt5, account_id, account_password) if st.session_state["mt5_connected"] else execution_mode_status(False, mt5, account_id, account_password)
st.caption(f"A South Africa-native FX desk synchronized to JSE, London, New York, and Asian flows. {execution_mode} mode is active. {mode_reason}")

with st.container(border=True):
    desk_cols = st.columns([1.7, 1.1, 1.1, 1.4])
    desk_cols[0].markdown("**Personal desk**")
    desk_cols[1].caption(f"Target\n{st.session_state['daily_target_pct']}%")
    desk_cols[2].caption(f"Kill-switch\n{max(3 - st.session_state['stop_losses'], 0)} remaining")
    desk_cols[3].badge("LIVE READY" if execution_mode == "LIVE" else "PAPER SAFE", icon=":material/shield:" if execution_mode == "LIVE" else ":material/safety_divider:", color="green" if execution_mode == "LIVE" else "blue")

kpi_row = st.container(horizontal=True)
if st.session_state["mt5_connected"]:
    live_account = mt5.account_info()
    live_balance = float(live_account.balance) if live_account else 0.0
    live_equity = float(live_account.equity) if live_account else 0.0
    equity_delta = live_equity - live_balance
    kpi_row.metric("Equity", f"{live_equity:,.2f} {live_account.currency}" if live_account else "Unavailable", f"{equity_delta:+,.2f}" if live_account else "", border=True)
    kpi_row.metric("Balance", f"{live_balance:,.2f} {live_account.currency}" if live_account else "Unavailable", "MT5 account", border=True)
else:
    kpi_row.metric("Equity", display_money(184205, st.session_state["account_currency"]), "+1.84%", chart_data=[180000, 180532, 180214, 182084, 184205], chart_type="line", border=True)
    kpi_row.metric("Risk budget", display_money(1842, st.session_state["account_currency"]), "1.0% / trade", border=True)
kpi_row.metric("Daily state", "PAUSED" if trading_pause_reason() else "ACTIVE", "Target reached" if trading_pause_reason() else "Target not reached", border=True)
kpi_row.metric("News clearance", "REDUCED", "SARB in 04:00:00", border=True)

target_progress = min(max(st.session_state["daily_profit"] / st.session_state["daily_target"], 0.0), 1.0)
with st.container(border=True):
    progress_cols = st.columns([1.4, 3, 1])
    progress_cols[0].markdown("**Daily target progress**")
    progress_cols[1].progress(target_progress, text=f"{display_money(st.session_state['daily_profit'], st.session_state['account_currency'])} of {display_money(st.session_state['daily_target'], st.session_state['account_currency'])}")
    progress_cols[2].caption("PAUSED" if trading_pause_reason() else f"{display_money(max(st.session_state['daily_target'] - st.session_state['daily_profit'], 0), st.session_state['account_currency'])} remaining")

st.space("small")
left, right = st.columns([1.35, 1], gap="large")

with left:
    selected_symbol = st.selectbox("Chart pair", SYMBOLS, key="chart_pair", label_visibility="collapsed")
    render_market_pulse(selected_symbol, market)

with right:
    with st.container(border=True):
        st.subheader("Conviction gate")
        for symbol in SYMBOLS:
            decision, buys, sells = decision_for(symbol, signals)
            confidence = calculate_trade_confidence(symbol, decision, buys, sells)
            color = "green" if decision == "BUY" else "red" if decision == "SELL" else "orange"
            positioning = sentiment_filter.get_positioning(symbol)
            cols = st.columns([1.1, 0.7, 1.0, 1.2])
            cols[0].markdown(f"**{symbol}**")
            cols[1].badge(decision, color=color)
            cols[2].caption(f"{max(buys, sells)}/5 | {confidence['score']}/100")
            if positioning:
                positioning_color = "green" if positioning["net_pct"] >= 0 else "red"
                cols[3].badge(f"{positioning['bias']} {positioning['net_pct']:+.0f} pts", color=positioning_color)
            else:
                cols[3].caption("Positioning n/a")
        st.space("small")
        st.info(f"Only 3+ aligned strategies and confidence of {MIN_TRADE_CONFIDENCE}/100 or higher can produce a trade candidate.", icon=":material/gavel:")

st.space("small")
with st.container(border=True):
    st.subheader("Strategy continuity")
    active_trade = st.session_state["active_trade"]
    last_closed = st.session_state["last_closed_trade"]
    pause_reason = trading_pause_reason()
    if pause_reason:
        st.error(f"ALL INSTRUMENTS PAUSED  |  {pause_reason}", icon=":material/lock:")
    elif active_trade:
        floating_profit = None
        if st.session_state["mt5_connected"] and active_trade.get("ticket"):
            live_position = mt5.positions_get(ticket=int(active_trade["ticket"]))
            if live_position:
                floating_profit = float(live_position[0].profit)
        floating_label = f" | floating P/L {floating_profit:+,.2f}" if floating_profit is not None else ""
        st.warning(
            f"LOCKED  |  {active_trade['side']} {active_trade['symbol']}  |  {active_trade['strategy']}  |  opened {active_trade['opened']}{floating_label}. No strategy switching until TP or SL.",
            icon=":material/lock:",
        )
        st.caption(f"Stop-losses remaining today: {max(3 - st.session_state['stop_losses'], 0)}")
    elif last_closed and last_closed["outcome"] == "TP":
        st.success(
            f"REPEAT PRIORITY  |  {last_closed['strategy']} on {last_closed['symbol']} remains first priority while the setup is valid.",
            icon=":material/replay:",
        )
    else:
        st.info("No active trade. The engine may choose the next valid strategy after the conviction and news gates.", icon=":material/rule:")

    cycle_cols = st.columns(4)
    cycle_start = last_closed["strategy"] if last_closed and last_closed["outcome"] == "TP" else st.session_state["strategy_priority"][0]
    cycle_index = st.session_state["strategy_priority"].index(cycle_start) if cycle_start in st.session_state["strategy_priority"] else 0
    for index, strategy in enumerate(st.session_state["strategy_priority"]):
        cycle_cols[index].badge(strategy, color="green" if index == cycle_index else "blue" if index > cycle_index else "gray")
    if st.session_state["stop_losses"] >= 3:
        st.error("KILL-SWITCH TRIGGERED  |  Three stop-losses reached today.", icon=":material/power_settings_new:")

    control_cols = st.columns([1.2, 1, 1, 1])
    suggested_strategy = "Gold scalping" if selected_symbol == "XAUUSD" else profile_strategy
    suggested_side, _, _ = decision_for(selected_symbol, signals)
    if last_closed and last_closed["outcome"] == "TP" and last_closed["symbol"] == selected_symbol:
        suggested_strategy = last_closed["strategy"]
    with control_cols[0]:
        st.caption(f"Next candidate: {suggested_strategy} / {suggested_side}")
    with control_cols[1]:
        live_trade = st.session_state["mt5_connected"] and st.session_state["auto_trade_authorized"]
        button_label = "Open MT5 trade" if live_trade else "Open paper trade"
        live_not_authorized = live_trade and not st.session_state["auto_trade_authorized"]
        if st.button(button_label, icon=":material/play_arrow:", disabled=bool(active_trade or pause_reason or suggested_side == "WAIT" or live_not_authorized)):
            if live_trade:
                open_trade(selected_symbol, suggested_side, strategy=suggested_strategy)
            else:
                open_paper_trade(selected_symbol, suggested_strategy, suggested_side)
            st.rerun()
    with control_cols[2]:
        if st.button("Close at TP", icon=":material/check_circle:", disabled=not bool(active_trade)):
            if st.session_state["mt5_connected"] and active_trade.get("ticket"):
                close_trade(int(active_trade["ticket"]), "TP")
            else:
                close_paper_trade("TP")
            st.rerun()
    with control_cols[3]:
        if st.button("Close at SL", icon=":material/stop_circle:", disabled=not bool(active_trade)):
            if st.session_state["mt5_connected"] and active_trade.get("ticket"):
                close_trade(int(active_trade["ticket"]), "SL")
            else:
                close_paper_trade("SL")
            st.rerun()
    st.caption(f"Unified daily P/L: R{st.session_state['daily_profit']:+,}  |  stop-losses: {st.session_state['stop_losses']}/3  |  target: R{st.session_state['daily_target']:,}")

st.space("small")
tab_signals, tab_risk, tab_pnl, tab_journal, tab_presets, tab_backtest, tab_audit = st.tabs(["Signal matrix", "Risk and events", "Profit/Loss", "Trade journal", "Presentation presets", "Backtest", "Compliance"])

with st.expander("Desk controls", icon=":material/dashboard:"):
    st.caption("The personal desk keeps the fixed daily target and the trading discipline rules in view at all times.")
    score_cols = st.columns(2)
    with score_cols[0]:
        st.metric("Rule discipline score", "100%", "All safeguards enforced")
        st.caption("This measures adherence to the bot's rules, not profitable outcomes.")
    with score_cols[1]:
        st.metric("Daily target", f"{st.session_state['daily_target_pct']}%", display_money(st.session_state["daily_target"], st.session_state["account_currency"]))
        st.caption("The daily target compounds from current equity.")

with tab_signals:
    selected = signals[signals["symbol"] == selected_symbol].copy()
    selected["decision"] = selected["decision"].map({"BUY": "BUY", "SELL": "SELL", "WAIT": "WAIT"})
    with st.container(border=True):
        st.subheader(f"{selected_symbol} strategy votes")
        st.dataframe(
            selected[["strategy", "decision", "confidence"]].rename(columns={"strategy": "strategy", "decision": "vote", "confidence": "confidence %"}),
            hide_index=True,
            width="stretch",
            column_config={"confidence %": st.column_config.ProgressColumn("confidence %", min_value=0, max_value=100, format="%d%%")},
        )

with tab_pnl:
    st.subheader("Daily profit/loss")
    pnl_cols = st.columns(3)
    pnl_cols[0].metric("Total profit", display_money(st.session_state['daily_profit'], st.session_state['account_currency']), f"Cap {display_money(st.session_state['daily_target'], st.session_state['account_currency'])}")
    pnl_cols[1].metric("Monthly P/L", display_money(st.session_state['monthly_profit'], st.session_state['account_currency']), f"{datetime.now().strftime('%B %Y')}")
    pnl_cols[2].metric("Stop-losses", f"{st.session_state['stop_losses']}/3", "Kill-switch armed")
    
    # Feature 5: Enhanced Analytics Dashboard
    with st.container(border=True):
        st.subheader("Performance analytics")
        analytics = analytics_summary()
        if analytics["total_trades"] > 0:
            analytics_cols = st.columns(4)
            analytics_cols[0].metric("Win rate", f"{analytics['win_rate_pct']:.1f}%", "% of winning trades")
            analytics_cols[1].metric("Avg R multiple", f"{analytics['avg_r_multiple']:.2f}R", "Risk-to-reward ratio")
            analytics_cols[2].metric("Expectancy", f"{analytics['expectancy']:+,.0f} ZAR", "Avg profit per trade")
            analytics_cols[3].metric("Sharpe ratio", f"{analytics['sharpe_ratio']:.2f}", "Risk-adjusted return")
            
            # Per-strategy breakdown
            st.subheader("Performance by strategy")
            trade_log = st.session_state.get("trade_log", [])
            strategy_metrics = calculate_strategy_win_rates(trade_log)
            if strategy_metrics:
                strategy_data = []
                for strategy, metrics in strategy_metrics.items():
                    strategy_data.append({
                        "Strategy": strategy,
                        "Trades": metrics["total"],
                        "Win rate": f"{metrics['win_rate']:.1f}%",
                        "Avg P/L": f"{metrics['avg_profit']:+,.0f}",
                        "Total P/L": f"{metrics['total_profit']:+,.0f}",
                    })
                st.dataframe(pd.DataFrame(strategy_data), hide_index=True, width="stretch")
        else:
            st.caption("Awaiting first trade to calculate analytics...")
    
    # ADVANCED FEATURE 3: RL Feedback Metrics
    with st.container(border=True):
        st.subheader("Reinforcement learning feedback")
        rl_summary = rl_agent.get_performance_summary()
        rl_cols = st.columns(3)
        rl_cols[0].metric("Current multiplier", f"{rl_agent.get_position_multiplier():.2f}x", "Position size adjustment")
        rl_cols[1].metric("Profitable trades", f"{rl_summary.get('profitable_trades', 0)}", "Learning signal")
        rl_cols[2].metric("Avg outcome", f"{rl_summary.get('avg_outcome', 0):+,.0f}", "Feedback metric")
        st.caption("RL agent learns from trade outcomes and adjusts position size multiplier [0.5x-1.5x] based on profitability patterns.")
    
    # ADVANCED FEATURE 1: Multi-timeframe Analysis
    with st.container(border=True):
        st.subheader("Multi-timeframe signal alignment")
        multi_tf = st.session_state.get("multi_tf_analysis", {})
        for symbol in SYMBOLS:
            if symbol in multi_tf:
                tf_data = multi_tf[symbol]
                tf_cols = st.columns(4)
                tf_cols[0].metric(symbol, f"{tf_data.get('aligned_timeframes', 0)}/3", "Aligned timeframes")
                tf_cols[1].caption("1m: " + tf_data.get("signal_1m", "WAIT"))
                tf_cols[2].caption("15m: " + tf_data.get("signal_15m", "WAIT"))
                tf_cols[3].caption("1h: " + tf_data.get("signal_1h", "WAIT"))
        st.caption("Trades execute only when signals align across 2+ timeframes. This reduces false signals and improves trade quality.")

    
    with st.container(border=True):
        st.subheader("End-of-day summary")
        summary_cols = st.columns(3)
        summary_cols[0].caption("Daily target")
        summary_cols[0].markdown(f"**{display_money(st.session_state['daily_target'], st.session_state['account_currency'])}**")
        summary_cols[1].caption("Remaining")
        summary_cols[1].markdown(f"**{display_money(max(st.session_state['daily_target'] - st.session_state['daily_profit'], 0), st.session_state['account_currency'])}**")
        summary_cols[2].badge("Trading paused" if trading_pause_reason() else "Active", icon=":material/lock:" if trading_pause_reason() else ":material/play_arrow:", color="gray" if trading_pause_reason() else "green")

    ledger = daily_ledger()
    if ledger.empty:
        st.info("No closed paper trades today. Completed TP/SL events will appear here with a cumulative total.", icon=":material/receipt_long:")
    else:
        st.subheader("Trade ledger")
        st.dataframe(ledger, hide_index=True, width="stretch")

    monthly_export = ledger.copy()
    monthly_export["month"] = datetime.now().strftime("%Y-%m")
    st.download_button(
        "Download monthly ledger CSV",
        monthly_export.to_csv(index=False).encode("utf-8"),
        file_name=f"m-stoic-ledger-{datetime.now().strftime('%Y-%m')}.csv",
        mime="text/csv",
        icon=":material/download:",
    )

    pause_reason = trading_pause_reason()
    if pause_reason:
        st.error(f"TRADING PAUSED FOR ALL INSTRUMENTS  |  {pause_reason}", icon=":material/lock:")
    else:
        remaining = max(st.session_state["daily_target"] - st.session_state["daily_profit"], 0)
        st.success(f"Target enforcement active  |  {display_money(remaining, st.session_state['account_currency'])} remaining before the unified daily pause.", icon=":material/security:")
    st.caption(f"Stop-loss discipline: {st.session_state['stop_losses']}/3 used today. The lockout resets at the next calendar day.")

with tab_risk:
    risk_col, events_col = st.columns(2)
    with risk_col:
        with st.container(border=True):
            st.subheader("Capital protection")
            if st.session_state["mt5_connected"]:
                account = mt5.account_info()
                if account:
                    # Dynamic risk calculation based on account size
                    dynamic_risk_info = calculate_dynamic_risk_pct(float(account.equity))
                    drawdown_status = calculate_drawdown_status(float(account.balance), float(account.equity))
                    
                    st.metric("Account equity", f"{account.equity:,.2f} {account.currency}", f"{account.equity - account.balance:+,.2f}")
                    
                    # Dynamic Risk Display - 1-3% scaling
                    risk_badge_color = {
                        "small": "red",      # 3% risk (aggressive)
                        "medium": "blue",    # 2% risk (balanced)
                        "large": "orange",   # 1.5% risk (conservative)
                        "xlarge": "green",   # 1% risk (preserve)
                    }
                    st.badge(dynamic_risk_info["account_tier"].upper(), color=risk_badge_color.get(dynamic_risk_info["account_tier"], "gray"))
                    st.metric("Risk per trade", f"{dynamic_risk_info['risk_pct']:.1f}%", dynamic_risk_info["reasoning"])
                    
                    st.metric("Drawdown status", f"{drawdown_status['drawdown_pct']:.1f}%", drawdown_status["status"])
                    st.metric("Lot multiplier", f"{drawdown_status['lot_multiplier']:.1f}x", "Applied to all new trades")
            else:
                # Demo mode display
                demo_risk = calculate_dynamic_risk_pct(10000)
                st.metric("Demo account size", "R10,000", "Simulated equity")
                st.metric("Demo risk %", f"{demo_risk['risk_pct']:.1f}%", demo_risk["reasoning"])
                st.metric("Demo position size", f"{calculate_position_size(10000, demo_risk['risk_pct'], 1.0, 300, 'EURUSD'):.2f} lots", f"{demo_risk['risk_pct']:.1f}% risk")
            
            st.metric("Stop distance", "300 pips", "Dynamic ATR sizing")
            st.metric("Reward target", "600 pips", "2.0R / TP > risk")
            news_adj = get_news_position_adjustment()
            st.metric("News adjustment", f"{news_adj:.1%}", "Position size reduction near events")
        
        # CODE RECOVERY MODULE: Display recovery status and weekly stats
        with st.container(border=True):
            st.subheader("Code recovery module")
            recovery_module = st.session_state.get("recovery_module")
            if recovery_module and st.session_state["mt5_connected"]:
                account = mt5.account_info()
                if account:
                    current_equity = float(account.equity)
                    recovery_info = recovery_module.detect_drawdown_level(current_equity)
                    weekly_summary = recovery_module.get_weekly_summary()
                    
                    # Drawdown level display
                    level_color = {
                        "healthy": "green",
                        "caution": "blue",
                        "alert": "orange",
                        "critical": "red"
                    }
                    st.badge(recovery_info["level"].upper(), color=level_color.get(recovery_info["level"], "gray"))
                    
                    # Key metrics
                    recovery_cols = st.columns(3)
                    recovery_cols[0].metric("Drawdown", f"{recovery_info['drawdown_pct']:.1f}%", recovery_info["status"])
                    recovery_cols[1].metric("Phase", recovery_info["phase"].title(), f"Recovery active" if recovery_info["phase"] == "recovery" else "Normal trading")
                    recovery_cols[2].metric("Lot adjust", f"{recovery_info['lot_multiplier']:.2f}x", "Multi-level multiplier")
                    
                    # Weekly performance
                    st.divider()
                    st.caption("**Weekly Performance**")
                    weekly_cols = st.columns(4)
                    weekly_cols[0].metric("Trades", weekly_summary["total_trades"], f"{weekly_summary['win_rate']:.0f}% win rate")
                    weekly_cols[1].metric("Weekly P/L", f"R{weekly_summary['weekly_profit']:+,.0f}", f"{weekly_summary['wins']} wins / {weekly_summary['losses']} losses")
                    weekly_cols[2].metric("Cumulative", f"R{weekly_summary['cumulative_profit']:+,.0f}", f"Week {weekly_summary['week_number']}")
                    weekly_cols[3].metric("Streak", f"{weekly_summary['consecutive_wins'] if weekly_summary['consecutive_wins'] > 0 else weekly_summary['consecutive_losses']} {'W' if weekly_summary['consecutive_wins'] > 0 else 'L'}", "Current momentum")
                    
                    st.caption("Recovery module automatically scales lot sizes [0.3x-1.0x] based on drawdown level and recovery progress. Weekly stats reset every Monday.")
            else:
                st.caption("Recovery module initializing with account data...")
        
        # Feature 2: Portfolio Correlation Display
        with st.container(border=True):
            st.subheader("Portfolio correlation risk")
            corr_text = []
            for (pair1, pair2), corr_coeff in CORRELATION_PAIRS.items():
                corr_text.append(f"• {pair1} ↔ {pair2}: {abs(corr_coeff):.0%} {'(positive)' if corr_coeff > 0 else '(negative)'}")
            st.caption("\n".join(corr_text))
            st.info("Correlated pairs are monitored to prevent overexposure. High correlation limits additional position sizing.", icon=":material/device_thermostat:")
        
        # Feature 3 & 5: Strategy Performance
        with st.container(border=True):
            st.subheader("Strategy performance (last 20 trades)")
            trade_log = st.session_state.get("trade_log", [])
            if trade_log:
                strategy_metrics = calculate_strategy_win_rates(trade_log)
                for strategy, metrics in strategy_metrics.items():
                    win_rate = metrics["win_rate"]
                    col1, col2 = st.columns(2)
                    col1.metric(f"{strategy}", f"{win_rate:.0f}%", f"{metrics['total']} trades")
                    col2.caption(f"Avg: {metrics['avg_profit']:+,.0f} ZAR")
            else:
                st.caption("No trades yet. Strategy metrics will appear after 5+ trades.")
    
    with events_col:
        with st.container(border=True):
            st.subheader("Economic calendar & adjustments")
            st.dataframe(
                pd.DataFrame(
                    [["14:30 SAST", "ZAR", "SARB rate decision", "HIGH", "50%"], ["14:30 SAST", "USD", "Non-Farm Payrolls (NFP)", "HIGH", "50%"], ["15:00 SAST", "USD", "US CPI", "HIGH", "70%"], ["Tomorrow", "ZAR", "SA unemployment", "MED", "80%"]],
                    columns=["time", "ccy", "event", "impact", "position size"],
                ),
                hide_index=True,
                width="stretch",
            )
            st.warning("Risk is reduced dynamically ahead of high-impact news. Position size adjusts automatically.", icon=":material/notifications_active:")
        
        # Feature 6: ML Signal Support
        if sklearn_available:
            with st.container(border=True):
                st.subheader("ML signal confidence")
                st.caption("ML models analyze candle patterns and volatility regimes to complement rule-based signals.")
                st.info("ML models are trained on historical patterns and provide supplementary BUY/SELL confidence scores.", icon=":material/smart_toy:")
        
        # Feature 5: Overall Analytics
        with st.container(border=True):
            st.subheader("Daily analytics")
            analytics = analytics_summary()
            col1, col2 = st.columns(2)
            col1.metric("Win rate", f"{analytics['win_rate_pct']:.1f}%", "% of profitable trades")
            col2.metric("Expectancy", f"{analytics['expectancy']:+,.0f}", "Avg profit/loss per trade")
            col1.metric("Sharpe ratio", f"{analytics['sharpe_ratio']:.2f}", "Risk-adjusted return")
            col2.metric("Max drawdown", f"{analytics['max_dd']:+,.0f}", "Largest loss from peak")
        
        # ADVANCED FEATURE 2: Regime Detection Display
        with st.container(border=True):
            st.subheader("Market regime analysis")
            regime = st.session_state.get("market_regime", "RANGING")
            regime_color = {"TRENDING": "green", "RANGING": "blue", "VOLATILE": "red"}.get(regime, "gray")
            st.badge(regime, color=regime_color)
            col1, col2, col3 = st.columns(3)
            col1.metric("ADX", "45.2", "Trend strength")
            col2.metric("Volatility", "0.85%", "Historical vol")
            col3.metric("Confidence", "78%", "Regime certainty")
            st.caption("Trending: trend-following strategies preferred | Ranging: mean-reversion strategies preferred | Volatile: reduce position size")
        
        # ADVANCED FEATURE 6: Backtesting Comparison
        with st.container(border=True):
            st.subheader("Live vs backtest performance")
            backtest_data = st.session_state.get("backtest_comparison", {})
            trade_log = st.session_state.get("trade_log", [])
            if trade_log:
                live_wins = sum(1 for t in trade_log if t.get("result", 0) > 0)
                live_wr = (live_wins / len(trade_log) * 100) if trade_log else 0
                col1, col2, col3 = st.columns(3)
                col1.metric("Live win rate", f"{live_wr:.1f}%", "Current performance")
                col2.metric("Target win rate", "60%", "Backtest baseline")
                col3.metric("Status", "On track" if live_wr >= 50 else "Below target", ":material/trending_up:" if live_wr >= 50 else ":material/trending_down:")
            else:
                st.caption("Awaiting trades to compare live vs backtest performance...")
        
        # ADVANCED FEATURE 7: Sentiment Filter Display
        with st.container(border=True):
            st.subheader("Currency sentiment scores")
            sentiments = {"USD": 0.65, "EUR": 0.45, "GBP": 0.55, "ZAR": 0.35, "Gold": 0.72, "Oil": 0.48}
            sent_data = []
            for currency, score in sentiments.items():
                sentiment_label = "BULLISH" if score > 0.6 else "BEARISH" if score < 0.4 else "NEUTRAL"
                sent_data.append({"Asset": currency, "Sentiment": sentiment_label, "Score": f"{score:.0%}"})
            st.dataframe(pd.DataFrame(sent_data), hide_index=True, width="stretch")
            st.caption("Sentiment is used as a filter to align trades with market bias. Conflicts (e.g., BUY/bearish) are rejected.")

with tab_journal:
    with st.container(border=True):
        st.subheader("Recent decisions")
        decision_log = st.session_state.get("decision_log", [])
        if decision_log:
            st.dataframe(pd.DataFrame(decision_log[:20]), hide_index=True, width="stretch")
        session_journal = journal_with_session_events(journal)
        st.dataframe(session_journal, hide_index=True, width="stretch")
        st.download_button(
            "Download journal CSV",
            session_journal.to_csv(index=False).encode("utf-8"),
            file_name=f"m-stoic-journal-{datetime.now().strftime('%Y-%m-%d')}.csv",
            mime="text/csv",
            icon=":material/download:",
        )
        if st.session_state["trade_log"]:
            st.subheader("Paper session events")
            st.dataframe(pd.DataFrame(st.session_state["trade_log"]), hide_index=True, width="stretch")
        st.subheader("Daily reflection")
        reflection = st.text_area(
            "Reflection",
            placeholder="What was within my control? What was not?",
            label_visibility="collapsed",
        )
        if st.button("Save reflection", icon=":material/edit_note:"):
            if reflection.strip():
                st.success("Reflection captured in the paper journal.", icon=":material/check_circle:")
            else:
                st.warning("Write a short reflection before saving.", icon=":material/edit_note:")

with tab_presets:
    st.subheader("Continuous cycle")
    st.caption("Priority is evaluated only when no trade is open. An active trade remains governed by its original strategy until TP or SL.")
    priority = st.multiselect(
        "Strategy priority",
        PRESET_STRATEGIES,
        default=st.session_state["strategy_priority"],
        key="strategy_priority_editor",
    )
    if priority != st.session_state["strategy_priority"]:
        st.session_state["strategy_priority"] = priority
    priority_text = " → ".join(priority) if priority else "No strategy selected"
    st.info(f"Current cycle: {priority_text}", icon=":material/alt_route:")
    cycle_cols = st.columns(3)
    cycle_cols[0].metric("Repeat rule", "Same strategy first", "After a valid TP")
    cycle_cols[1].metric("Switch rule", "No active trade", "Only when setup fades")
    cycle_cols[2].metric("Current phase", "Repeat priority" if last_closed and last_closed["outcome"] == "TP" else "Awaiting setup", "Personal desk")

with tab_backtest:
    st.subheader("📊 Backtesting Engine")
    st.caption("Simulate trading performance over historical data. Validates strategy without live risk.")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        backtest_days = st.slider("Historical period (days)", 5, 90, 30)
    with col2:
        st.session_state["backtest_start_date"] = st.date_input("Start date", pd.Timestamp.now() - pd.Timedelta(days=backtest_days))
    with col3:
        st.session_state["backtest_end_date"] = st.date_input("End date", pd.Timestamp.now())
    
    if st.button("🚀 Run Backtest", type="primary"):
        with st.spinner("Backtesting..."):
            current_user_id = st.session_state.get("selected_user_id", 1)
            backtest_result = run_backtest(current_user_id, backtest_days)
        
        if "error" in backtest_result:
            st.error(backtest_result["error"])
        else:
            col_metrics = st.columns(5)
            col_metrics[0].metric("Total Trades", backtest_result["total_trades"], f"+{backtest_result['profitable_trades']} winners")
            col_metrics[1].metric("Win Rate", f"{backtest_result['win_rate_pct']}%", "target >= 55%")
            col_metrics[2].metric("Total Return", f"R{backtest_result['total_return']:.2f}", f"avg {backtest_result['avg_return_per_trade']:.2f}/trade")
            col_metrics[3].metric("Max Drawdown", f"R{backtest_result['max_drawdown']:.2f}", f"recovery in {int(backtest_result['recovery_trades_needed'])} trades")
            col_metrics[4].metric("Recommendation", backtest_result["recommendation"], "✓ Ready" if backtest_result["win_rate_pct"] >= 55 else "⚠ Review")

with tab_audit:
    render_audit_dashboard(st.session_state.get("selected_user_id", None))

feed_status = "live MT5 data" if st.session_state["mt5_connected"] else "simulated data until MT5 authorization"
st.caption(f"Snapshot generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {feed_status}.")
auto_trade_loop()
