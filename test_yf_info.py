import yfinance as yf
t = yf.Ticker("AAPL")
try:
    print("Price from info:", t.info.get('currentPrice'))
except Exception as e:
    print("Error:", e)
