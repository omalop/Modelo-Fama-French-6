from tvdatafeed import TvDatafeed, Interval
import pandas as pd
import logging

# Configurar logging para ver errores de tvdatafeed
logging.basicConfig(level=logging.ERROR)

def test_tv_data():
    tv = TvDatafeed()
    
    tickers = ['S31L6', 'S31G6', 'TZXD6', 'TZXD7', 'TZX28', 'YFC2O']
    exchange = 'BCBA'
    
    results = {}
    for ticker in tickers:
        try:
            print(f"Buscando {ticker} en {exchange}...")
            data = tv.get_hist(symbol=ticker, exchange=exchange, interval=Interval.in_daily, n_bars=250)
            if data is not None and not data.empty:
                print(f"✅ {ticker}: {len(data)} barras encontradas.")
                results[ticker] = len(data)
            else:
                print(f"❌ {ticker}: No se encontraron datos.")
        except Exception as e:
            print(f"❌ {ticker}: Error - {e}")
            
    return results

if __name__ == "__main__":
    test_tv_data()
