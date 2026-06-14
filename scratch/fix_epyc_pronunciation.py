import sqlite3
import time

conn = sqlite3.connect("ruri_memory.db")
cursor = conn.cursor()
cursor.execute("DELETE FROM pronunciations WHERE word = 'EPYC'")
cursor.execute("INSERT OR REPLACE INTO pronunciations (word, pronunciation, created_at) VALUES ('EPYC', 'エピック', ?)", (time.time(),))
conn.commit()

# Print current database entries for verification
cursor.execute("SELECT word, pronunciation FROM pronunciations")
rows = cursor.fetchall()
print("Current pronunciations in DB:")
for r in rows:
    print(f"  {r[0]} -> {r[1]}")

conn.close()
