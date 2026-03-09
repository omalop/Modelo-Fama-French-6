import duckdb
conn = duckdb.connect('data/market_data.duckdb')
res = conn.execute("SELECT DISTINCT ticker FROM financials LIMIT 10").fetchall()
for r in res:
    print(r)
conn.close()
