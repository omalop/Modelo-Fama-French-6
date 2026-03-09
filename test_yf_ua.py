import yfinance as yf
import requests

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
})

tickers = ["AAPL", "MSFT"]
print(f"Probando descarga con User-Agent generico...")
try:
    data = yf.download(tickers, period="1mo", session=session)
    print("Vacio?:", data.empty)
    if not data.empty:
        print(data.head())
except Exception as e:
    print("Error:", e)
