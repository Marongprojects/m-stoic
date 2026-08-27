# Marong Stoic Bot go-live checklist

The current Streamlit app is a paper-trading prototype. Do not connect live broker credentials, accept real deposits, or enable automated payouts until every applicable item below is complete and independently reviewed.

## 1. Product and trading controls

- [ ] Define the supported instruments, sessions, spreads, slippage limits, and trading hours.
- [ ] Validate strategy lock-in, repeat priority, opportune switching, daily target, and three-stop-loss rules with deterministic tests.
- [ ] Verify every live order has a hard stop-loss and bounded position size.
- [ ] Add an operator kill switch that can stop all order creation immediately.
- [ ] Run backtests, walk-forward tests, paper trading, and failure-injection tests.
- [ ] Document that no strategy can guarantee profits or a win rate.

## 2. Broker and IB integration

- [ ] Obtain written authorization and current API documentation from HFM.
- [ ] Confirm whether the proposed IB attribution and commission flow are supported by HFM.
- [ ] Use a backend service for broker calls; never expose broker secrets in Streamlit or browser code.
- [ ] Store credentials in a managed secret store and rotate them regularly.
- [ ] Implement account linking with explicit user consent and an idempotency key.
- [ ] Verify account identity, link status, order status, fills, fees, and reconciliation.
- [ ] Add sandbox/demo-account tests before any production account access.
- [ ] Display `Verified via broker API` only after a signed, successful broker response.

## 3. Voucher, identity, and access

- [ ] Move voucher validation to a server-side API backed by a database.
- [ ] Store only salted hashes of voucher codes and normalized phone identifiers where possible.
- [ ] Add rate limiting, replay protection, redemption idempotency, and fraud monitoring.
- [ ] Add authenticated user accounts and 2FA before redemption or account linking.
- [ ] Use a verified phone provider for ownership checks; do not treat formatting as verification.
- [ ] Enforce seven-day expiry server-side and revoke expired entitlements.
- [ ] Record consent, terms acceptance, plan, timestamp, and eligibility decision.

## 4. Money, splits, and compliance

- [ ] Obtain legal advice on FSCA requirements, financial-product licensing, marketing, and advice restrictions.
- [ ] Confirm the legality and disclosure requirements for IB attribution, commissions, vouchers, and profit-sharing.
- [ ] Define whether funds are user-owned, company-owned, or held by a regulated provider.
- [ ] Never silently deduct money. Show the amount, recipient, reason, consent, and transaction status.
- [ ] Use a regulated payment provider for any payout or fee collection.
- [ ] Implement KYC/AML, sanctions screening, refunds, disputes, tax records, and support procedures.
- [ ] Publish risk disclosures, terms, privacy notice, complaints process, and contact details.

## 5. Data, security, and auditability

- [ ] Replace `st.session_state` as the system of record with PostgreSQL or an equivalent managed database.
- [ ] Use append-only audit events for redemptions, account links, orders, fills, P/L, allocations, and kill switches.
- [ ] Add server-side authorization checks for every sensitive action.
- [ ] Encrypt data in transit and at rest; do not log secrets, voucher codes, or full phone numbers.
- [ ] Encrypt backups and store them in a separate access-controlled location.
- [ ] Verify backup checksums and test restoration on a schedule.
- [ ] Add retention and deletion policies aligned with privacy obligations.
- [ ] Run dependency scanning, SAST/DAST, penetration testing, and secret scanning.

## 6. Reliability and operations

- [ ] Move execution and scheduled jobs to a backend worker architecture.
- [ ] Add health checks, structured logs, metrics, alerts, and incident response runbooks.
- [ ] Make order submission, reconciliation, and payout operations idempotent.
- [ ] Define recovery point and recovery time objectives.
- [ ] Test broker outages, stale prices, duplicate webhooks, partial fills, clock drift, and network loss.
- [ ] Use staging and production environments with separate credentials and databases.
- [ ] Set resource limits, connection pools, rate limits, and deployment rollback procedures.

## 7. UX and accessibility

- [ ] Test desktop, tablet, and mobile layouts on Android, iOS, Windows, and macOS.
- [ ] Test keyboard navigation, screen readers, contrast, focus states, and reduced motion.
- [ ] Translate all user-facing strings consistently for English, isiZulu, Sesotho, and Afrikaans.
- [ ] Show live data timestamps, source, stale-data warnings, and paper/live mode prominently.
- [ ] Make qualification, continuation, expiry, P/L, split, and pause states unambiguous.
- [ ] Require confirmation before irreversible account, payout, or plan actions.

## Release gates

- [ ] Security review approved.
- [ ] Broker integration approved by HFM.
- [ ] Compliance and legal review approved.
- [ ] Reconciliation trial completed with no unexplained differences.
- [ ] Disaster recovery restore test passed.
- [ ] Support and incident procedures rehearsed.
- [ ] Independent sign-off obtained before enabling live execution.

## Current prototype boundary

The current app is suitable for UI review and paper-mode demonstrations. Its HFM, IB, voucher, payout, database, authentication, and cloud-sync behavior must be treated as simulated or incomplete until the controls above are implemented and verified.
