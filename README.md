# M-STOIC

M-STOIC is a Streamlit trading decision desk for paper-mode strategy testing, dynamic risk controls, market filters, and audit analytics.

## Safety

The app enforces PAPER mode by default with `MARONG_FORCE_PAPER=true`. Streamlit Community Cloud does not provide a native MetaTrader 5 terminal, so hosted deployments remain paper-only.

## Run locally

```powershell
.\.venv\Scripts\Activate.ps1
streamlit run app.py
```

Install dependencies with:

```powershell
pip install -r requirements.txt
```

The M-STOIC logo is stored at `assets/m-stoic-logo.png`.

See [DEPLOYMENT.md](DEPLOYMENT.md) for GitHub, Streamlit Cloud, and controlled demo-terminal guidance.
