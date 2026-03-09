import yfinance as yf
tickers = ["AAPL", "GGAL", "ALUA.BA"]
print(f"Probando descarga para {tickers}...")
try:
    data = yf.download(tickers, period="1mo", group_by='ticker')
    print("Columnas:", data.columns)
    print("Vacio?:", data.empty)
    if not data.empty:
        print("Muestra:", data.head())
except Exception as e:
    print("Error:", e)
