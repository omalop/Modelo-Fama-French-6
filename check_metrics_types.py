import duckdb
conn = duckdb.connect('data/market_data.duckdb')
res = conn.execute("SELECT DISTINCT metric, type FROM financials").fetchall()
for r in res:
    print(r)
conn.close()
