import duckdb
conn = duckdb.connect('data/market_data.duckdb')
res = conn.execute("SELECT DISTINCT metric FROM financials LIMIT 50").fetchall()
for r in res:
    print(r)
conn.close()
