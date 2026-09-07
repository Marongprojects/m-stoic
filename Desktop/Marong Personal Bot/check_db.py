import sqlite3

db = sqlite3.connect('profiles.db')
cursor = db.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
print('Tables:', tables)

print("\nUser profiles:")
profiles = db.execute("SELECT user_id, profile_name, strategy, target_pct FROM user_profiles").fetchall()
for p in profiles:
    print(f"  {p}")

print("\nAudit log count:", db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0])
print("Trade queue count:", db.execute("SELECT COUNT(*) FROM trade_queue").fetchone()[0])

db.close()
