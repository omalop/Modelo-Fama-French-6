
import duckdb
conn = duckdb.connect('data/market_data.duckdb')
res = conn.execute("SELECT ticker, last_updated_prices FROM tickers_metadata WHERE ticker LIKE '%.BA%'").df()
print(f"Total tickers .BA encontrados: {len(res)}")
print(res.head())
conn.close()
