
import yfinance as yf
import requests

session = requests.Session()
session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'})

print("Probando descarga con sesion...")
try:
    data = yf.download("GGAL.BA", period="1mo", session=session)
    if not data.empty:
        print("Exito: Se descargaron datos.")
        print(data.tail())
    else:
        print("Fallo: Dataframe vacio.")
except Exception as e:
    print(f"Error: {e}")
