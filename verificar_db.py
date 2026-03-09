import duckdb
conn = duckdb.connect('data/market_data.duckdb')
print("Prices count:", conn.execute("SELECT count(*) FROM prices").fetchone()[0])
print("Financials count:", conn.execute("SELECT count(*) FROM financials").fetchone()[0])
print("Tickers count:", conn.execute("SELECT count(*) FROM tickers_metadata").fetchone()[0])
conn.close()
