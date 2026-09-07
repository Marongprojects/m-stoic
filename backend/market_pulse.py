import json
import os
from urllib.parse import urlencode
from urllib.request import urlopen

from fastapi import FastAPI

app = FastAPI(title="M-STOIC Market Pulse", version="1.0.0")


def fetch_twelve_data() -> dict:
    api_key = os.getenv("TWELVE_DATA_API_KEY")
    symbol = os.getenv("MARKET_SYMBOL", "XAU/USD")
    if not api_key:
        return {"symbol": "XAUUSD", "price": 2338.60, "change": 0.84, "status": "Paper fallback"}

    query = urlencode({"symbol": symbol, "apikey": api_key, "format": "JSON"})
    with urlopen(f"https://api.twelvedata.com/price?{query}", timeout=5) as response:
        payload = json.load(response)
    if "price" not in payload:
        raise RuntimeError(payload.get("message", "Market provider returned no price"))
    return {"symbol": symbol.replace("/", ""), "price": float(payload["price"]), "change": 0.0, "status": "Live"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "paper_mode": os.getenv("MARONG_FORCE_PAPER", "true")}


@app.get("/pulse")
def pulse() -> dict:
    try:
        return fetch_twelve_data()
    except Exception as error:
        return {"symbol": "XAUUSD", "price": 2338.60, "change": 0.0, "status": f"Fallback: {error.__class__.__name__}"}
