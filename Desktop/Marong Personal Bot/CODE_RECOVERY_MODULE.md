# Code Recovery Module Documentation

## Overview

The **Code Recovery Module** is a comprehensive system for managing drawdown periods, automatically adjusting position sizes during market volatility, and tracking weekly performance cycles. It provides multi-level drawdown detection, progressive lot size recovery, and weekly reset logic to help the bot survive and recover from losing periods.

---

## Core Features

### 1. Multi-Level Drawdown Detection

The module classifies drawdown severity into 4 levels based on percentage from peak equity:

| Level | Drawdown Range | Lot Multiplier | Trading Phase | Use Case |
|-------|----------------|----------------|---------------|----------|
| **Healthy** | 0-5% | 1.0x | Normal | Optimal trading conditions |
| **Caution** | 5-10% | 0.8x | Alert | Mild losses, reduce exposure |
| **Alert** | 10-15% | 0.6x | Recovery | Moderate losses, conservative sizing |
| **Critical** | 15%+ | 0.3x | Protection | Severe losses, minimal trading |

**Formula:**
```
Drawdown % = ((Peak Equity - Current Equity) / Peak Equity) × 100
```

**Example:**
- Peak Equity: R100,000
- Current Equity: R92,000
- Drawdown: (100,000 - 92,000) / 100,000 × 100 = 8%
- Level: **Caution** (5-10% range) → 0.8x lot multiplier applied

---

### 2. Progressive Lot Size Adjustment

During recovery phase, position size gradually scales from 0.3x up to 1.0x based on:

#### a) Base Multiplier (Drawdown Level)
From the 4-level detection system above.

#### b) Consecutive Wins Bonus
- Each consecutive win adds +0.1x to multiplier
- Maximum win bonus: +0.2x (2 consecutive wins)
- Resets on first loss

**Example:**
```
Base multiplier: 0.6x (Alert level)
Consecutive wins: 2 → +0.2x bonus
Final multiplier: min(0.6 + 0.2, 1.0) = 0.8x
```

#### c) Weekly Win Rate Consistency Bonus
- Calculated if 5+ trades in current week
- Win rate ≥55%: +0.1x bonus
- Win rate <55%: No bonus
- Incentivizes steady performance over lucky streaks

**Example:**
```
Weekly stats: 10 trades, 6 wins
Win rate: 60% → +0.1x bonus
Base multiplier: 0.6x
Final: min(0.6 + 0.1, 1.0) = 0.7x
```

#### Final Calculation:
```
Recovery Multiplier = min(base + win_bonus + wr_bonus, 1.0)
Applied Lot = Base Lot × Recovery Multiplier
```

---

### 3. Weekly Reset Logic

The module tracks performance on a weekly basis (ISO week 1-53), automatically resetting counters while preserving historical data.

#### Weekly Cycle:
- **Start:** Monday 00:00 (ISO week boundary)
- **Reset Trigger:** Week number changes
- **Preserved:** Previous weeks' cumulative profit

#### Weekly Statistics Tracked:
| Metric | Purpose | Reset? |
|--------|---------|--------|
| Total trades | Performance volume | Yes |
| Wins/Losses | Accuracy tracking | Yes |
| Win rate | Consistency metric | Yes |
| Weekly profit | P/L for period | Yes (carried to cumulative) |
| Consecutive wins/losses | Momentum indicator | Yes |
| Peak equity | Max ever reached | **No** (persistent) |
| Previous weeks' profit | Cumulative history | **No** (accumulates) |

#### Weekly Summary Example:
```
Week 34 (Aug 18-24):
- Trades: 12
- Wins: 8 (67% win rate)
- Weekly P/L: +R2,450
- Consecutive streak: +3 wins
- Cumulative P/L: +R5,200 (includes previous weeks)
```

---

## Integration Points

### 1. Position Sizing in `open_trade()`

**Execution Order:**
1. Calculate base lot size (Feature 1: Dynamic Sizing)
2. Apply news adjustment (Feature 4)
3. Apply capital preservation multiplier (Feature 7)
4. Apply RL feedback multiplier (Advanced Feature 3)
5. **Apply recovery module multiplier** ← NEW
6. Enforce min/max lot bounds

**Code Flow:**
```python
# Feature 1: Dynamic Position Sizing
lot = calculate_position_size(equity, risk%, volatility, sl_points, symbol)

# Features 4 & 7: News + Drawdown adjustments
news_mult = get_news_position_adjustment()
drawdown_mult = calculate_drawdown_status().lot_multiplier
lot = lot * news_mult * drawdown_mult

# Advanced Feature 3: RL Feedback
rl_mult = rl_agent.get_position_multiplier()
lot = lot * rl_mult

# CODE RECOVERY: Apply recovery-phase multiplier
if account and drawdown_level == "critical":
    if consecutive_wins < 2:
        # BLOCK TRADE - preserve capital
        return  # "Trade blocked: critical drawdown"

recovery_mult = recovery_module.calculate_recovery_lot_multiplier(equity)
lot = lot * recovery_mult

# Enforce bounds
lot = max(MIN_LOT_SIZE, min(lot, MAX_LOT_SIZE))
```

### 2. Trade Outcome Recording in `close_trade()`

**When Executed:** After trade closes (TP, SL, or manual exit)

**What's Recorded:**
```python
recovery_module.record_trade_outcome(
    profit=float,      # P/L in account currency (e.g., +450 or -120)
    lot_size=float     # Position size used (e.g., 0.15)
)
```

**Internal Updates:**
- Weekly trade count incremented
- Profit/loss tallied to weekly total
- Win count or loss count incremented
- Consecutive streak updated
- Peak equity updated if exceeded

### 3. UI Display in Risk Tab

**Location:** "Code recovery module" container in Risk & events tab

**Displayed Metrics:**
```
┌─ CODE RECOVERY MODULE ─────────────────────┐
│                                            │
│ Status Badge: [ALERT] [CAUTION] [HEALTHY] │
│                                            │
│ Drawdown      Phase         Lot Adjust     │
│ 12.3%         Recovery      0.65x          │
│ "Alert level" "Active phase" "Multiplier"  │
│                                            │
│ Weekly Performance ──────────────────────  │
│ Trades    Weekly P/L  Cumulative   Streak  │
│    8       +R1,200    +R3,450      +2W     │
│                                            │
│ Week 34 | 75% win rate | Momentum +      │
└────────────────────────────────────────────┘
```

---

## Usage Example

### Scenario: Recovery from Drawdown

**Initial State:**
- Account equity: R100,000 (peak)
- Daily target: R1,000

**Day 1 - Losing Period:**
```
Trade 1: -R500  → Equity: R99,500 (0.5% DD)   [Healthy] → 1.0x lot
Trade 2: -R300  → Equity: R99,200 (0.8% DD)   [Healthy] → 1.0x lot
Trade 3: -R800  → Equity: R98,400 (1.6% DD)   [Healthy] → 1.0x lot
Trade 4: -R600  → Equity: R97,800 (2.2% DD)   [Healthy] → 1.0x lot
Trade 5: -R400  → Equity: R97,400 (2.6% DD)   [Healthy] → 1.0x lot
     ↓
Peak: R100,000 → Current: R97,400 → Drawdown: 2.6% [Still Healthy]
Weekly: 5 trades, 0 wins, -R2,600
```

**Day 2 - Continued Losses:**
```
Trade 6: -R1,200 → Equity: R96,200 (3.8% DD)   [Healthy] → 1.0x lot
Trade 7: -R900   → Equity: R95,300 (4.7% DD)   [Caution] → 0.8x lot (entering caution)
Trade 8: -R600   → Equity: R94,700 (5.3% DD)   [Caution] → 0.8x lot
Trade 9: -R800   → Equity: R93,900 (6.1% DD)   [Caution] → 0.8x lot
Trade 10: -R500  → Equity: R93,400 (6.6% DD)   [Caution] → 0.8x lot
```

**Day 3 - Recovery Begins:**
```
Peak: R100,000 → Current: R93,400 → Drawdown: 6.6% [Caution]
Consecutive wins: 0, Weekly wins: 0

Trade 11: +R450  → Equity: R93,850 (6.1% DD)   [Caution]
           Consecutive wins: 1
           Lot for next: 0.8x × (1 × 0.1 bonus) = 0.9x

Trade 12: +R380  → Equity: R94,230 (5.8% DD)   [Caution]
           Consecutive wins: 2
           Lot for next: 0.8x × (0.2 win bonus) = 1.0x

Trade 13: +R520  → Equity: R94,750 (5.2% DD)   [Caution]
           Consecutive wins: 3
           Lot for next: 0.8x × (min 0.3, 1.0) = 0.8x (capped at 1.0x)
           Weekly win rate: 3/13 = 23% (no wr bonus yet)

Trade 14: +R310  → Equity: R95,060 (4.9% DD)   [Healthy] ✓
           First healthy level reached!
           Consecutive wins: 4
           Lot for next: 1.0x × (0.2 win bonus) = 1.0x (recovered!)
```

**Recovery Summary:**
- **Depth:** From 6.6% drawdown back to healthy
- **Duration:** 4 recovery trades
- **Lot progression:** 0.8x → 0.9x → 1.0x → 1.0x
- **Weekly stats:** 7 wins, 6 losses, -R1,220 remaining

---

## Trading Rules During Recovery

### Phase: Healthy (0-5% DD)
✅ **Allow:** All trades at 1.0x lot size
- Normal position sizing
- Full risk-per-trade
- No restrictions

### Phase: Caution (5-10% DD)
⚠️ **Reduce:** All trades at 0.8x lot size
- Monitor consecutive losses
- Still allow trading to recover
- Smaller position = faster capital preservation

### Phase: Alert (10-15% DD)
🔴 **Restrict:** All trades at 0.6x lot size
- Position size significantly reduced
- Recovery trades encouraged (win bonus active)
- Avoid large losses until recovery progress

### Phase: Critical (15%+ DD)
🚫 **Block unless:** Consecutive wins ≥2
- Only allow trades showing recovery momentum
- Otherwise, preserve remaining capital
- Automatic pause to prevent further damage

**Example Critical Block:**
```
Equity: R82,000 (Peak: R100,000)
Drawdown: 18% [Critical]
Consecutive wins: 0
↓
NEW TRADE ATTEMPT:
"Trade blocked: Critical drawdown level: pausing new trades to preserve capital"
↓
Wait for 2 winning trades → Recovery phase active → Resume trading
```

---

## Weekly Reset Behavior

### Monday 00:00 (or Week Boundary)

**Before Reset:**
```
Week 33 (Aug 11-17):
- Total trades: 45
- Wins: 28 (62% win rate)
- Weekly P/L: +R4,230
- Consecutive wins: 3
```

**After Reset (New Week 34):**
```
Weekly Counters RESET:
- Total trades: 0
- Wins/Losses: 0/0
- Weekly P/L: 0
- Consecutive wins/losses: 0

Persistent Data CARRIED OVER:
- Previous weeks' profit: +R4,230 (accumulated)
- Peak equity: R104,230 (updated with new peak)
- Cumulative P/L: R4,230 (shown in UI)

New Week 34 Starts:
- Fresh tracking for new 7-day cycle
- Bonus multipliers reset (need new consecutive wins)
- Weekly win rate requirement: 55% (fresh)
```

---

## Configuration

### Drawdown Thresholds (Adjustable)
```python
DRAWDOWN_THRESHOLDS = {
    "healthy": 0,      # 0-5%
    "caution": 5,      # 5-10%
    "alert": 10,       # 10-15%
    "critical": 15,    # 15%+
}

RECOVERY_MULTIPLIERS = {
    "healthy": 1.0,    # Full size
    "caution": 0.8,    # 20% reduction
    "alert": 0.6,      # 40% reduction
    "critical": 0.3,   # 70% reduction
}
```

### Bonus Parameters (Tunable)
```python
WIN_BONUS_PER_STREAK = 0.1  # Each consecutive win: +0.1x
MAX_WIN_BONUS = 0.2         # Cap: +0.2x
WIN_RATE_BONUS = 0.1        # Win rate ≥55%: +0.1x
WIN_RATE_THRESHOLD = 0.55   # 55% threshold
TRADES_FOR_WR_BONUS = 5     # Need 5+ trades for rate bonus
```

---

## Key Formulas

### 1. Drawdown Percentage
```
DD% = ((Peak - Current) / Peak) × 100
```

### 2. Lot Size During Recovery
```
Final Lot = Base Lot × [Base Mult + Win Bonus + WR Bonus]
Where:
  Base Mult ∈ {1.0, 0.8, 0.6, 0.3}
  Win Bonus = min(Consecutive Wins × 0.1, 0.2)
  WR Bonus = 0.1 if (Win Rate ≥ 55%) else 0.0
```

### 3. Weekly Win Rate
```
Win Rate = Wins / Total Trades × 100
```

### 4. Cumulative Profit
```
Cumulative = Weekly P/L + Previous Weeks' Profit
```

---

## Testing Checklist

### Unit Tests
- [ ] Drawdown detection at each level boundary (5%, 10%, 15%)
- [ ] Lot multiplier calculation with win streaks
- [ ] Weekly reset at ISO week boundary
- [ ] Peak equity tracking (never decreases)
- [ ] Consecutive wins/losses reset on opposite outcome
- [ ] Trading blocked in critical without wins

### Integration Tests
- [ ] Recovery module initialized in `initialize_trading_state()`
- [ ] Lot adjustment applied in `open_trade()` flow
- [ ] Outcome recorded in `close_trade()` flow
- [ ] UI displays correctly in Risk tab
- [ ] Weekly summary updates in real-time

### Manual Tests
1. **Simulate Losing Streak:**
   - Open 5 losing trades
   - Verify drawdown level progression (Healthy → Caution)
   - Verify lot multiplier reduction (1.0x → 0.8x)
   - Check weekly P/L accumulation

2. **Simulate Recovery:**
   - Open 2 consecutive winning trades
   - Verify consecutive wins bonus (+0.1x per trade)
   - Verify multiplier increases (0.8x → 0.9x → 1.0x)
   - Check streak display in UI

3. **Weekly Reset:**
   - Run trades in current week
   - Wait for Monday 00:00 (or manually trigger reset)
   - Verify counters reset to 0
   - Verify cumulative profit carries over

---

## Monitoring & Alerts

### Dashboard Indicators
- **Yellow Alert:** Entering Caution (5% DD)
- **Orange Alert:** Entering Alert (10% DD)
- **Red Alert:** Entering Critical (15% DD)
- **Green Recovery:** Consecutive wins detected during recovery phase

### Session State Tracking
```python
st.session_state["recovery_module"]
├─ .peak_equity        # All-time high
├─ .recovery_phase     # Currently recovering?
├─ .weekly_stats       # Current week data
├─ .consecutive_wins   # Current streak
└─ .consecutive_losses # Current loss streak
```

---

## Troubleshooting

### Issue: "Trading blocked: Critical drawdown"
**Cause:** Equity dropped >15% from peak without recovery trades
**Solution:** 
1. Reduce trade frequency to avoid additional losses
2. Wait for 2 profitable trades to re-enable trading
3. Check strategy signal alignment (Valid_setup gates)

### Issue: Lot multiplier not decreasing
**Cause:** Drawdown may not be calculating correctly
**Solution:**
1. Verify `account.equity` is updating from MT5
2. Check peak equity isn't reset prematurely
3. Ensure `detect_drawdown_level()` called before lot calculation

### Issue: Weekly reset not triggering
**Cause:** ISO week number may not change at expected time
**Solution:**
1. Verify system date/time is correct
2. Check `_get_week_number()` returns 1-53
3. Manual reset: Clear `st.session_state["recovery_module"]` and reinitialize

### Issue: Consecutive wins bonus not applying
**Cause:** Streak resets on first loss, or calculation order wrong
**Solution:**
1. Verify loss detected correctly (profit < 0)
2. Ensure `record_trade_outcome()` called immediately after close
3. Check bonus cap at 0.2x (min + 0.2, not higher)

---

## Performance Impact

### Computational Cost
- Drawdown detection: O(1) - single comparison
- Lot calculation: O(1) - arithmetic only
- Weekly tracking: O(1) - counter increments
- **Total overhead: Negligible** (<1ms per trade)

### Capital Impact
- Healthy phase: 0% reduction (1.0x lot)
- Caution: 20% reduction (0.8x) → Preserves capital during mild loss
- Alert: 40% reduction (0.6x) → Prevents escalation during moderate loss
- Critical: 70% reduction (0.3x) → Protects core capital during severe loss

**Example:** 10% drawdown with normal 0.1 lot → 0.08 lot = R120 saved per trade

---

## Future Enhancements

1. **Adaptive Thresholds:** Adjust DD levels based on account volatility
2. **Drawdown Recovery Predictor:** ML model to estimate recovery time
3. **Dynamic Win Rate Target:** Adjust 55% threshold based on strategy
4. **Multi-week Trends:** Track correlation between weekly P/L
5. **Automated Pause Resume:** Auto-pause at critical, auto-resume after recovery
6. **Drawdown Insurance:** Reserve portion of profits to offset future losses

---

## Summary

The **Code Recovery Module** provides:
✅ Multi-level drawdown detection (0%, 5%, 10%, 15%)
✅ Progressive lot size adjustment (0.3x-1.0x multiplier)
✅ Win streak bonus (+0.1x per consecutive win, max +0.2x)
✅ Weekly performance cycle with automatic reset
✅ Trading pause at critical levels (>15% DD)
✅ Real-time UI display with weekly summary
✅ Seamless integration into trading flow

**Result:** Bot survives prolonged losing periods through intelligent capital preservation while maintaining recovery momentum through bonus multipliers on consecutive wins.
