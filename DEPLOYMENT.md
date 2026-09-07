# M-STOIC Deployment

## Safe default

The hosted app must run in PAPER mode. Streamlit Cloud does not host a native MetaTrader 5 terminal, so live broker execution is unavailable there even if credentials exist.

Use Streamlit secrets only for a separate local/demo terminal deployment:

```toml
MT5_ACCOUNT_ID = "demo-login"
MT5_ACCOUNT_PASSWORD = "demo-password"
MT5_SERVER = "HFMarketsSA-Demo"
```

Never commit `secrets.toml`, passwords, tokens, or `profiles.db`.

## GitHub packaging

1. Create the dedicated GitHub repository `m-stoic` for this app.
2. Add this folder as its repository root.
3. Confirm `.gitignore` excludes local databases, backups, virtual environments, and secrets.
4. Push only reviewed source, documentation, tests, and `requirements.txt`.

## Streamlit Cloud

1. Create a new app from the dedicated repository.
2. Set the main file to `app.py`.
3. Do not add MT5 credentials to Streamlit Cloud secrets.
4. Confirm the UI shows `PAPER | Not connected` and that auto-trading authorization is disabled.

## Local demo integration

Run the app on the Windows machine hosting the MT5 demo terminal. Use `HFMarketsSA-Demo` and demo-only credentials in environment variables or local Streamlit secrets. Keep the live toggle off until the demo account and broker preflight checks have been independently tested.