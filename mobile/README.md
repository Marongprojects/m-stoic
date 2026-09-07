# M-STOIC Mobile

Cross-platform Flutter client for iOS and Android.

## Screens

- Welcome: paper-desk entry and safety disclosure
- Dashboard: equity, target, win rate, drawdown, performance chart, safeguards
- Trading: instrument selection, performance chart, conviction, spread, risk sizing, paper controls
- Safety: execution mode and protection settings

## Run

Install Flutter, then from this directory:

```bash
flutter pub get
flutter analyze
flutter test
flutter run
```

The mobile client is PAPER-only at the UI layer. Live broker credentials must remain behind a secure backend/API; do not place MT5 passwords in the app bundle.

Charts currently use `fl_chart` as the shared native chart surface. A future backend can stream TradingView/market data into the typed screen models without changing navigation or the safety UI.
