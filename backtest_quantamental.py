
import yfinance as yf
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
import matplotlib.pyplot as plt

# Importar módulos propios
try:
    from screener_fundamental import FamaFrenchCalculator
    from optimizador_cartera import GestorBlackLitterman
except ImportError:
    pass # Se manejará dinámicamente si es necesario

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/backtest.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TimeTravelSim:
    """
    Simulador de 'Viaje en el Tiempo' para estrategias Quantamental.
    
    Principios:
    1. Point-in-Time: Solo usa datos disponibles en la fecha de simulación.
    2. Walk-Forward: Rebalanceo periódico (Trimestral).
    """
    
    def __init__(self, tickers, start_date='2020-01-01', end_date='2024-01-01', initial_capital=10000.0):
        self.tickers = tickers
        self.start_date = pd.to_datetime(start_date)
        self.end_date = pd.to_datetime(end_date)
        self.capital = initial_capital
        self.portfolio_value = []
        self.rebalance_dates = pd.date_range(start=self.start_date, end=self.end_date, freq='QE') # Trimestral
        
        # Cache de datos básicos para no descargar mil veces
        self.price_cache = {}
        
    def preload_prices(self):
        """Descarga historial de precios completo una sola vez."""
        logger.info("Precargando historial de precios...")
        self.price_cache = yf.download(self.tickers, start=self.start_date, end=self.end_date, auto_adjust=True, progress=False)['Close']

    def get_valid_financials(self, ticker, simulation_date):
        """
        Filtra estados financieros para evitar Look-Ahead Bias.
        Retorna los datos más recientes reportados ANTES de simulation_date.
        """
        t = yf.Ticker(ticker)
        # Nota: yfinance cachea, pero la llamada es lenta. En backtest real se usaría base de datos local.
        fin = t.financials
        bs = t.balance_sheet
        
        if fin.empty or bs.empty: return None, None
        
        # Las columnas de yfinance son fechas. Filtramos.
        valid_cols = [date for date in fin.columns if pd.to_datetime(date) < simulation_date]
        
        if not valid_cols:
            return None, None # No habia datos reportados en esa fecha (o yfinance no tiene historia tan vieja)
            
        # Tomar la columna más reciente válida
        latest_date = max(valid_cols)
        return fin[latest_date], bs[latest_date]

    def run_simulation(self):
        """Ejecuta el loop de simulación."""
        logger.info(f"Iniciando Backtest desde {self.start_date.date()} hasta {self.end_date.date()}")
        
        self.preload_prices()
        current_capital = self.capital
        current_positions = {} # {ticker: shares}
        
        equity_curve = []
        
        for sim_date in self.rebalance_dates:
            logger.info(f"--- Rebalanceo: {sim_date.date()} ---")
            
            # 1. Screening Fundamental (Time Travel)
            selected_tickers = []
            valid_fundamental_scores = {} # Para BL
            
            # Optimizacion: Para demo no corremos los 400 tickers, seleccionamos aleatoriamente 20 para testear lógica
            # En prod: Correr sobre self.tickers completo
            subset_tickers = self.tickers[:30] # Limitado por velocidad de API yfinance en este script demo
            
            for t in subset_tickers:
                # Verificar precio disponible
                if t not in self.price_cache.columns or pd.isna(self.price_cache.loc[sim_date:][t].head(1).values[0]):
                    continue
                    
                fin_series, bs_series = self.get_valid_financials(t, sim_date)
                
                if fin_series is None: continue
                
                # Calcular Factores básicos "al vuelo"
                try:
                    bk_val = bs_series.get('Total Stockholder Equity', np.nan)
                    # Market Cap a la fecha de simulacion
                    price_at_sim = self.price_cache.loc[:sim_date][t].iloc[-1]
                    shares = 100000000 # Dummy si no tenemos shares historicos. Limitacion yfinance.
                    mkt_cap = price_at_sim * shares 
                    
                    bm = bk_val / mkt_cap if mkt_cap > 0 else 0
                    
                    # Criterio simple para backtest: BM > 0.5 (Value)
                    if bm > 0.2: # Filtro laxo
                        selected_tickers.append(t)
                        # Score normalizado (mock)
                        valid_fundamental_scores[t] = 1.0 # Asumimos buen score
                        
                except Exception as e:
                    continue
            
            if not selected_tickers:
                logger.warning("No se encontraron activos válidos en esta fecha. Manteniendo Cash.")
                equity_curve.append({'Date': sim_date, 'Equity': current_capital})
                continue
                
            # Seleccionar Top N (ej: 5)
            top_n = selected_tickers[:5]
            
            # 2. Optimización (Black-Litterman simplificado para Backtest)
            # En backtest real, instanciaríamos GestorBlackLitterman. Aquí simplificamos pesos iguales 
            # para validar la mecánica del loop primero.
            target_weight = 1.0 / len(top_n)
            
            # Rebalancear
            # Vender todo lo anterior (simplificación)
            # Comprar nuevos
            
            portfolio_val_at_date = 0
            new_positions = {}
            cash_left = current_capital
            
            for t in top_n:
                price = self.price_cache.loc[:sim_date][t].iloc[-1]
                alloc = current_capital * target_weight
                shares = alloc / price
                new_positions[t] = shares
                cash_left -= alloc
                
            current_positions = new_positions
            
            # Avanzar al siguiente periodo para calcular Valor
            # (Aquí simplificamos: El valor se marca en la sig fecha de rebalanceo)
            
            equity_curve.append({'Date': sim_date, 'Equity': current_capital})
            
        # Generar Reporte
        results = pd.DataFrame(equity_curve).set_index('Date')
        
        # Calcular Retorno Total
        start_val = results.iloc[0]['Equity']
        end_val = results.iloc[-1]['Equity']
        cagr = (end_val / start_val) ** (1 / (len(results)/4)) - 1 # 4 trimestres/año
        
        logger.info(f"Backtest Finalizado. CAGR: {cagr:.2%}")
        return results

if __name__ == "__main__":
    print("--- BACKTEST QUANTAMENTAL (SIMULACIÓN HISTÓRICA) ---")
    
    # Cargar tickers
    try:
        with open('ticker.txt', 'r') as f:
            content = f.read()
            tickers = [t.strip().upper() for t in content.replace('\n', ',').split(',') if t.strip()]
    except:
        tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA'] # Fallback
        
    # Pedir fechas
    print("\nAVISO: Debido a limitaciones de yfinance, se recomienda simular solo 2020-2024.")
    start = input("Fecha Inicio (YYYY-MM-DD) [Default 2021-01-01]: ") or "2021-01-01"
    end = input("Fecha Fin (YYYY-MM-DD) [Default 2024-01-01]: ") or "2024-01-01"
    
    sim = TimeTravelSim(tickers, start_date=start, end_date=end)
    results = sim.run_simulation()
    
    print("\n--- RESULTADOS PRELIMINARES ---")
    print(results)
    
    try:
        results['Equity'].plot(title="Curva de Equidad - Simulación")
        plt.show()
    except:
        pass
