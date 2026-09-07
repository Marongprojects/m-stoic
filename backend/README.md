# M-STOIC Market Pulse API

Run locally with `pip install -r requirements.txt` and `uvicorn market_pulse:app --reload --port 8000`.

Set `TWELVE_DATA_API_KEY` for live prices. Without a provider key, `/pulse` returns a paper-safe fallback. The mobile client connects with `--dart-define=M_STOIC_API_URL=https://your-api.example.com`.
