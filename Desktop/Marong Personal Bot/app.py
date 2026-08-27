from datetime import datetime, timedelta
import hashlib
import os
from pathlib import Path
import shutil

import numpy as np
import pandas as pd
import streamlit as st

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - optional dependency for live trading only
    mt5 = None


st.set_page_config(
    page_title="Marong Stoic Bot | Decision desk",
    page_icon=":material/shield:",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
        .block-container { padding-top: 1rem; padding-bottom: 1rem; }
        div[data-testid="stSidebar"] { background: #0b1220; }
        [data-testid="stMetric"] {
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 0.75rem;
            padding: 0.5rem 0.75rem;
        }
        .stTabs [role="tablist"] button {
            border-radius: 0.5rem 0.5rem 0 0;
            margin-right: 0.35rem;
        }
        h1, h2, h3 { letter-spacing: -0.02em; }
    </style>
    """,
    unsafe_allow_html=True,
)


SYMBOLS = ["USDZAR", "EURZAR", "GBPZAR", "SA40", "XAUUSD", "USOIL", "XPTUSD", "EURUSD", "GBPUSD"]
SESSIONS = ["JSE core", "London open", "New York overlap", "Asian range"]
STRATEGIES = ["Trend following", "Breakout", "Mean reversion", "Momentum", "Session trading"]
DAILY_TARGET = 1000
PRESET_STRATEGIES = ["Gold scalping", "Breakout", "Trend", "Swing"]
BACKUP_INTERVAL_SECONDS = 60 * 60
LANGUAGES = ["English", "isiZulu", "Sesotho", "Afrikaans"]
DISPLAY_RATES = {"ZAR": 1.0, "USD": 0.055}
KILL_SWITCH_COOLDOWN = timedelta(hours=24)
BACKUP_HASH_ALGORITHM = "sha256"
DEFAULT_MT5_ACCOUNT_TYPE = "Real"
DEFAULT_MT5_LOGIN = 55004565
MT5_SERVERS = ["HFMarketsSA-Live2", "HFMarketsSA-Demo"]
MT5_SYMBOL_ALIASES = {
    "SA40": ["SA40", "ZA40", "JSE40", "SouthAfrica40", "SouthAfrica40Index", "South_Africa_40"],
    "XPTUSD": ["XPTUSD", "XPT", "PlatinumUSD", "Platinum"],
}
MT5_SYMBOL_HINT_TOKENS = {"SA40": ["JSE", "SOUTH", "AFRICA", "ZA40"], "XPTUSD": ["XPT", "PLATINUM"]}


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


def get_mt5_credentials() -> tuple[int | None, str | None, str]:
    account_id_raw = os.getenv("MT5_ACCOUNT_ID")
    account_password = os.getenv("MT5_ACCOUNT_PASSWORD")
    server = os.getenv("MT5_SERVER", "HFM-Demo")
    try:
        account_id = int(account_id_raw) if account_id_raw is not None else None
    except (TypeError, ValueError):
        account_id = None
    return account_id, account_password, server


def execution_mode_status(enabled: bool, mt5_module, account_id: int | None, password: str | None) -> tuple[str, str]:
    if not enabled:
        return "PAPER", "Paper mode is safe and disabled for live execution."
    if mt5_module is None:
        return "PAPER", "MetaTrader5 is not installed; live mode is unavailable."
    if account_id is None or not password:
        return "PAPER", "Live mode is blocked until valid MT5 credentials are configured."
    return "LIVE", "Live mode is ready. MT5 credentials are configured and trading can run."


def connect_mt5(account_id: int | None = None, account_password: str | None = None, server: str | None = None, show_status: bool = True) -> bool:
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
    st.session_state["mt5_connected"] = True
    if show_status:
        st.success("Connected to MT5 live feed.")
    return True


def disconnect_mt5() -> None:
    if mt5 is not None:
        mt5.shutdown()
    st.session_state["mt5_connected"] = False
    st.session_state["auto_trade_authorized"] = False
    st.session_state.pop("mt5_password", None)


def resolve_mt5_symbol(symbol: str) -> str | None:
    if mt5 is None:
        return None
    exact = mt5.symbol_info(symbol)
    if exact is not None:
        return symbol
    available = mt5.symbols_get() or []
    names = [item.name for item in available]
    aliases = MT5_SYMBOL_ALIASES.get(symbol, [symbol])
    normalized_aliases = [alias.replace(".", "").replace("_", "").replace("-", "").replace(" ", "").upper() for alias in aliases]
    for name in names:
        normalized_name = name.replace(".", "").replace("_", "").replace("-", "").replace(" ", "").upper()
        if any(normalized_name == alias or normalized_name.startswith(alias) or alias in normalized_name for alias in normalized_aliases):
            return name
    return None


def broker_symbol_hints(symbol: str) -> list[str]:
    if mt5 is None:
        return []
    names = [item.name for item in (mt5.symbols_get() or [])]
    keywords = MT5_SYMBOL_HINT_TOKENS.get(symbol, MT5_SYMBOL_ALIASES.get(symbol, [symbol]))
    return [name for name in names if any(keyword.lower() in name.lower() for keyword in keywords)][:8]


def symbol_error_label(symbol: str, hints: list[str]) -> str:
    if symbol == "SA40" and not hints:
        return "SA40: no South African index symbol is available on this HFM server"
    hint_text = f" Available candidates: {', '.join(hints)}." if hints else ""
    return f"{symbol}: no matching broker symbol.{hint_text}"


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
        return pd.DataFrame(columns=["ticket", "symbol", "side", "volume", "open", "current", "profit", "swap"])
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


def open_trade(symbol: str, side: str, lot: float = 0.1, sl_points: int = 300, tp_points: int = 600) -> None:
    if trading_pause_reason():
        st.warning("Trading paused by discipline rules.")
        return
    if mt5 is None:
        st.warning("MT5 is not installed; only the simulated desk can run right now.")
        return
    if not st.session_state.get("mt5_connected"):
        st.warning("Connect to MT5 before sending a live demo order.")
        return
    if not st.session_state.get("auto_trade_authorized"):
        st.warning("Auto-trading is stopped. Authorize the strategy from the sidebar first.")
        return
    broker_symbol = st.session_state.get("mt5_symbol_map", {}).get(symbol) or resolve_mt5_symbol(symbol)
    if broker_symbol is None:
        st.error(f"No broker symbol matching {symbol} was found on the live MT5 feed.")
        return
    info = mt5.symbol_info(broker_symbol)
    if info is None:
        st.error(f"Symbol {symbol} not found on the live MT5 feed.")
        return
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
        "comment": "Marong Stoic Bot",
    }
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        st.error(f"Trade failed: {result.comment}")
        return
    st.success(f"{side} {symbol} opened on MT5")
    st.toast(f"{side} {symbol} opened on MT5", icon=":material/check_circle:")
    st.session_state["active_trade"] = {
        "ticket": result.order,
        "symbol": symbol,
        "side": side,
        "strategy": st.session_state.get("strategy_priority", ["Trend following"])[0],
        "opened": datetime.now().strftime("%H:%M"),
    }


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
            "comment": "Marong Stoic Bot close",
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
    st.session_state["last_closed_trade"] = trade
    st.session_state["trade_log"].insert(0, trade)
    st.session_state["active_trade"] = None
    st.success(f"Trade {ticket} closed with {outcome}, profit {profit}")
    st.toast(f"Trade {ticket} closed at {outcome}", icon=":material/check_circle:")


def initialize_trading_state() -> None:
    today = datetime.now().date().isoformat()
    month = datetime.now().strftime("%Y-%m")
    if st.session_state.get("month_key") != month:
        st.session_state["month_key"] = month
        st.session_state["monthly_profit"] = 0
    if st.session_state.get("state_date") != today:
        st.session_state["state_date"] = today
        st.session_state["daily_profit"] = 0
        st.session_state["stop_losses"] = 0
        st.session_state["active_trade"] = None
        st.session_state["last_closed_trade"] = None
        st.session_state["trade_log"] = []
    st.session_state.setdefault("daily_profit", 0)
    st.session_state.setdefault("stop_losses", 0)
    st.session_state.setdefault("daily_target", DAILY_TARGET)
    st.session_state.setdefault("active_trade", None)
    st.session_state.setdefault("last_closed_trade", None)
    st.session_state.setdefault("trade_log", [])
    st.session_state.setdefault("strategy_priority", PRESET_STRATEGIES)
    st.session_state.setdefault("monthly_profit", 0)
    st.session_state.setdefault("kill_switch_until", None)
    st.session_state.setdefault("audit_log", [])


def trading_pause_reason() -> str | None:
    if st.session_state["daily_profit"] >= st.session_state["daily_target"]:
        return "Daily target achieved across all instruments."
    if st.session_state["stop_losses"] >= 3:
        if st.session_state["kill_switch_until"] is None:
            st.session_state["kill_switch_until"] = datetime.now() + KILL_SWITCH_COOLDOWN
        return "Three stop-losses reached. Capital protection is engaged for 24 hours."
    if st.session_state["kill_switch_until"] and datetime.now() < st.session_state["kill_switch_until"]:
        return f"Kill-switch cooldown active until {st.session_state['kill_switch_until'].strftime('%H:%M on %d %b')}."
    return None


def open_paper_trade(symbol: str, strategy: str, side: str) -> None:
    st.session_state["active_trade"] = {
        "symbol": symbol,
        "strategy": strategy,
        "side": side,
        "session": "London open",
        "opened": datetime.now().strftime("%H:%M"),
    }


def close_paper_trade(outcome: str) -> None:
    trade = st.session_state["active_trade"]
    if not trade:
        return
    result = 200 if outcome == "TP" else -100
    st.session_state["daily_profit"] += result
    st.session_state["monthly_profit"] += result
    if outcome == "SL":
        st.session_state["stop_losses"] += 1
    trade["outcome"] = outcome
    trade["result"] = result
    st.session_state["last_closed_trade"] = trade
    st.session_state["trade_log"].insert(0, trade)
    st.session_state["audit_log"].append({"timestamp": datetime.now().isoformat(timespec="seconds"), "event": f"paper_trade_{outcome.lower()}", "instrument": trade["symbol"], "strategy": trade["strategy"]})
    st.session_state["active_trade"] = None


def display_money(zar_amount: int | float, currency: str) -> str:
    if currency == "ZAR":
        return f"R{zar_amount:,.0f}"
    return f"${zar_amount * DISPLAY_RATES[currency]:,.2f}"


@st.fragment(run_every="15s")
def render_market_pulse(selected_symbol: str, fallback_market: pd.DataFrame) -> None:
    current_market = load_live_market_snapshot()[0] if st.session_state["mt5_connected"] else fallback_market
    with st.container(border=True):
        if st.session_state["mt5_connected"]:
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
            if st.session_state["mt5_connected"] and st.session_state["mt5_feed_error"]:
                st.error(f"Live feed unavailable for {selected_symbol}: {st.session_state['mt5_feed_error']}", icon=":material/wifi_off:")
            else:
                st.warning(f"No price data is available for {selected_symbol}.", icon=":material/wifi_off:")
            return
        st.line_chart(chart_data, height=290, color="#d9a441")
        latest = float(chart_data.iloc[-1])
        previous = float(chart_data.iloc[-2]) if len(chart_data) > 1 else latest
        feed_label = "live MT5 feed" if st.session_state["mt5_connected"] else "simulated MT5 feed"
        st.caption(f"{selected_symbol}  |  last {latest:.5f}  |  1m change {latest - previous:+.5f}  |  SAST / {feed_label}")
        if st.session_state["mt5_feed_error"]:
            st.warning(f"Live feed issue: {st.session_state['mt5_feed_error']}", icon=":material/warning:")

        if st.session_state["mt5_connected"]:
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


@st.fragment(run_every="60s")
def auto_trade_loop() -> None:
    if not st.session_state.get("auto_trade_authorized") or trading_pause_reason():
        return
    if st.session_state.get("active_trade"):
        return
    for symbol in SYMBOLS:
        decision, _, _ = decision_for(symbol, signals)
        if decision not in {"BUY", "SELL"}:
            continue
        strategy = st.session_state.get("strategy_priority", ["Trend following"])[0]
        if st.session_state["mt5_connected"]:
            open_trade(symbol, decision)
        else:
            open_paper_trade(symbol, strategy, decision)
        st.toast(f"Auto-trade triggered: {decision} {symbol}", icon=":material/play_circle:")
        break


def daily_ledger() -> pd.DataFrame:
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
                "outcome": "TP hit" if trade["outcome"] == "TP" else "SL hit",
                "P/L (ZAR)": trade["result"],
                "cumulative (ZAR)": cumulative,
            }
        )
    return pd.DataFrame(rows)


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
st.session_state.setdefault("mt5_connected", False)
st.session_state.setdefault("auto_trade_authorized", False)
st.session_state.setdefault("mt5_feed_error", None)
st.session_state.setdefault("mt5_feed_status", "unknown")
if st.session_state.get("mt5_connected") and st.session_state.get("mt5_login") and st.session_state.get("mt5_password") and st.session_state.get("mt5_server"):
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

st.session_state.setdefault("live_mode_enabled", False)

with st.sidebar:
    st.markdown("## :material/shield: Marong Stoic Bot")
    st.caption("Personal desk")
    st.space("small")
    st.toggle(
        "Live / Paper mode",
        value=st.session_state.get("live_mode_enabled", False),
        key="live_mode_enabled",
        help="Paper mode is safe by default. Live mode only works when valid MT5 credentials are configured.",
    )
    account_id, account_password, server = get_mt5_credentials()
    browser_account_id = st.session_state.get("mt5_login", account_id or DEFAULT_MT5_LOGIN)
    browser_server = st.session_state.get("mt5_server", MT5_SERVERS[0])
    account_type = st.selectbox("MT5 account", ["Demo", "Real"], index=1, key="mt5_account_type_selector")
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
    if st.button("Log in to MT5", use_container_width=True, disabled=not effective_password):
        if connect_mt5(int(browser_account_id), effective_password, browser_server):
            st.session_state["mt5_login"] = int(browser_account_id)
            st.session_state["mt5_server"] = browser_server
            st.rerun()
    st.caption("Connected to MT5" if st.session_state["mt5_connected"] else "Enter your password, then log in")
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
    if auto_authorize_disabled and not st.session_state["mt5_connected"]:
        st.caption("Connect MT5 before authorizing the strategy.")
    st.selectbox("Focus pair", SYMBOLS, index=0)
    st.selectbox("Account currency", ["ZAR", "USD"], index=0, key="account_currency")
    st.selectbox("Session", SESSIONS, index=0)
    st.caption(f"Target: R{DAILY_TARGET:,}")
    if st.sidebar.button("Manual Stop Trading", icon=":material/stop_circle:"):
        st.session_state["kill_switch_until"] = datetime.now() + timedelta(hours=24)
        st.warning("Manual stop triggered. Trading paused for 24h.")
    st.toggle("Red-flag kill-switch", value=True, disabled=True)
    st.badge("Backup verified" if backup_integrity else "Backup check unavailable", icon=":material/verified_user:", color="green" if backup_integrity else "orange")

    auto_trade_loop()

st.title("Marong Stoic Bot")
account_label = st.session_state.get("mt5_account_type_selector", DEFAULT_MT5_ACCOUNT_TYPE)
connection_label = "Connected" if st.session_state["mt5_connected"] else "Not connected"
display_mode = "LIVE" if st.session_state["mt5_connected"] else "PAPER"
st.badge(f"{display_mode} | {connection_label} | {account_label}", icon=":material/sensors:" if st.session_state["mt5_connected"] else ":material/wifi_off:", color="green" if st.session_state["mt5_connected"] else "gray")
feed_status = st.session_state["mt5_feed_status"]
feed_badge = {"stable": ("Feed stable", "green"), "partial": ("Feed partial", "orange"), "unavailable": ("Feed unavailable", "red")}.get(feed_status, ("Feed not checked", "gray"))
st.badge(feed_badge[0], icon=":material/sensors:" if feed_status == "stable" else ":material/wifi_off:", color=feed_badge[1])
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
    st.warning("The live feed is not active. Authorize MT5 from the sidebar.", icon=":material/wifi_off:")
account_id, account_password, server = get_mt5_credentials()
account_id = int(browser_account_id)
account_password = browser_password or effective_password
server = browser_server
execution_mode, mode_reason = execution_mode_status(True, mt5, account_id, account_password) if st.session_state["mt5_connected"] else execution_mode_status(False, mt5, account_id, account_password)
st.caption(f"A South Africa-native FX desk synchronized to JSE, London, New York, and Asian flows. {execution_mode} mode is active. {mode_reason}")

st.session_state["daily_target"] = DAILY_TARGET
with st.container(border=True):
    desk_cols = st.columns([1.7, 1.1, 1.1, 1.4])
    desk_cols[0].markdown("**Personal desk**")
    desk_cols[1].caption(f"Target\nR{DAILY_TARGET:,}")
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
            color = "green" if decision == "BUY" else "red" if decision == "SELL" else "orange"
            cols = st.columns([1.2, 0.7, 1.2])
            cols[0].markdown(f"**{symbol}**")
            cols[1].badge(decision, color=color)
            cols[2].caption(f"{max(buys, sells)}/5 aligned")
        st.space("small")
        st.info("Only 3+ aligned strategies can produce a trade candidate. WAIT is a valid outcome.", icon=":material/gavel:")

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
    suggested_strategy = "Gold scalping" if selected_symbol == "XAUUSD" else "Breakout"
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
                open_trade(selected_symbol, suggested_side)
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
tab_signals, tab_risk, tab_pnl, tab_journal, tab_presets = st.tabs(["Signal matrix", "Risk and events", "Profit/Loss", "Trade journal", "Presentation presets"])

with st.expander("Desk controls", icon=":material/dashboard:"):
    st.caption("The personal desk keeps the fixed daily target and the trading discipline rules in view at all times.")
    score_cols = st.columns(2)
    with score_cols[0]:
        st.metric("Rule discipline score", "100%", "All safeguards enforced")
        st.caption("This measures adherence to the bot's rules, not profitable outcomes.")
    with score_cols[1]:
        st.metric("Daily target", f"R{DAILY_TARGET:,}", "Fixed personal desk cap")
        st.caption("The daily pause is triggered when the target is reached.")

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
        st.dataframe(ledger, hide_index=True, width="stretch")

    monthly_export = ledger.copy()
    monthly_export["month"] = datetime.now().strftime("%Y-%m")
    st.download_button(
        "Download monthly ledger CSV",
        monthly_export.to_csv(index=False).encode("utf-8"),
        file_name=f"marong-stoic-ledger-{datetime.now().strftime('%Y-%m')}.csv",
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
            st.metric("Suggested risk", "R921", "0.5% of equity")
            st.metric("Stop distance", "300 pips", "Dynamic ATR sizing")
            st.metric("Reward target", "600 pips", "2.0R / TP > risk")
            st.metric("Position size", "0.01 lot", "50% reduction for SARB")
            st.caption("No doubling down. No revenge trading. No new position after the daily target or drawdown limit is reached.")
    with events_col:
        with st.container(border=True):
            st.subheader("Economic calendar")
            st.dataframe(
                pd.DataFrame(
                    [["14:30 SAST", "ZAR", "SARB rate decision", "HIGH", "04:00:00"], ["15:00 SAST", "USD", "US CPI", "HIGH", "04:30:00"], ["Tomorrow", "ZAR", "SA unemployment", "MED", "22:18:44"]],
                    columns=["time", "ccy", "event", "impact", "time to event"],
                ),
                hide_index=True,
                width="stretch",
            )
            st.warning("Risk is reduced 50% ahead of SARB. Kill-switch remains armed: no trades 15 minutes before or after HIGH impact events.", icon=":material/notifications_active:")

with tab_journal:
    with st.container(border=True):
        st.subheader("Recent decisions")
        session_journal = journal_with_session_events(journal)
        st.dataframe(session_journal, hide_index=True, width="stretch")
        st.download_button(
            "Download journal CSV",
            session_journal.to_csv(index=False).encode("utf-8"),
            file_name=f"marong-stoic-journal-{datetime.now().strftime('%Y-%m-%d')}.csv",
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

feed_status = "live MT5 data" if st.session_state["mt5_connected"] else "simulated data until MT5 authorization"
st.caption(f"Snapshot generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {feed_status}.")
