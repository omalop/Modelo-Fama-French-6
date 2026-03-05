import yfinance as yf
import pandas as pd

df = yf.download(["AAPL", "BAC"], period="1mo", group_by='ticker', auto_adjust=True, progress=False)
df_long = df.stack(level=0, future_stack=True).reset_index()
print("Columns after stack (MultiIndex):", df_long.columns.tolist())

df_long.rename(columns={
    'level_1': 'ticker', 'Date': 'date', 'Open': 'open',
    'High': 'high', 'Low': 'low', 'Close': 'close', 'Volume': 'volume'
}, inplace=True)

print("Columns after rename:", df_long.columns.tolist())
