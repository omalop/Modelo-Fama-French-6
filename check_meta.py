import duckdb
conn = duckdb.connect('data/market_data.duckdb')
res = conn.execute("SELECT ticker, sector, shares, last_updated_financials FROM tickers_metadata LIMIT 10").fetchall()
for r in res:
    print(r)
conn.close()
