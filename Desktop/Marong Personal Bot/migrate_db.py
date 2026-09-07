import sqlite3
from pathlib import Path

db_path = Path("profiles.db")
db = sqlite3.connect(db_path)

# Drop old tables
db.execute("DROP TABLE IF EXISTS user_profiles")
db.execute("DROP TABLE IF EXISTS audit_log")
db.execute("DROP TABLE IF EXISTS trade_queue")
db.commit()

# Create new tables with proper schema
db.execute("""
CREATE TABLE user_profiles (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_name TEXT UNIQUE NOT NULL,
    strategy TEXT NOT NULL,
    target_pct INTEGER NOT NULL,
    risk_mode TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
""")

db.execute("""
CREATE TABLE audit_log (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action TEXT NOT NULL,
    symbol TEXT,
    decision TEXT,
    conviction_score INTEGER,
    regime TEXT,
    recovery_state TEXT,
    block_reason TEXT,
    FOREIGN KEY(user_id) REFERENCES user_profiles(user_id)
)
""")

db.execute("""
CREATE TABLE trade_queue (
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    strategy TEXT NOT NULL,
    confidence INTEGER NOT NULL,
    enqueued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    executed_at TIMESTAMP,
    status TEXT DEFAULT 'PENDING',
    execution_reason TEXT,
    FOREIGN KEY(user_id) REFERENCES user_profiles(user_id)
)
""")

# Re-seed default profiles
db.executemany(
    "INSERT INTO user_profiles (profile_name, strategy, target_pct, risk_mode) VALUES (?, ?, ?, ?)",
    [
        ("Conservative", "Trend following", 5, "Conservative"),
        ("Balanced", "Breakout", 10, "Balanced"),
        ("Growth", "Momentum", 15, "Growth"),
    ],
)
db.commit()

# Verify
profiles = db.execute("SELECT user_id, profile_name, strategy, target_pct FROM user_profiles").fetchall()
print("✓ Migration complete")
print("Profiles:")
for p in profiles:
    print(f"  user_id={p[0]}: {p[1]} | {p[2]} | {p[3]}%")

db.close()
