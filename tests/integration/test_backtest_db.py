import sys
import os
import logging

# Add src and src/models to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src', 'models')))

from backtest_quantamental import TimeTravelSim

# Configurar logger para ver output
logging.basicConfig(level=logging.INFO)

def test_backtest_integration():
    print("--- INICIANDO TEST BACKTEST DB ---")
    
    tickers = ['AAPL', 'MSFT']
    # Fechas cortas para rápido test
    start = '2023-01-01'
    end = '2023-06-01'
    
    try:
        sim = TimeTravelSim(tickers, start_date=start, end_date=end, initial_capital=100000)
        results = sim.run_simulation()
        
        print("\n[RESULTADOS]")
        print(results.head())
        
        if not results.empty:
            print("\n[EXITO] Backtest generó resultados usando DB.")
        else:
            print("\n[FALLO] Backtest no generó resultados.")
            
    except Exception as e:
        print(f"\n[ERROR] El backtest falló: {e}")
        raise

if __name__ == "__main__":
    test_backtest_integration()
