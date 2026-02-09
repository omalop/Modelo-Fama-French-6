
import yfinance as yf
import pandas as pd
import numpy as np
import logging
import argparse
from datetime import datetime

# Configuración de Logging Científico (Artículo 5)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/screener_fundamental.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class FamaFrenchCalculator:
    """
    Calculadora de Factores Fama-French (Fuente: Fama & French, 2015).
    
    Factores a calcular:
    1. Value (HML): Book-to-Market Ratio = Total Stockholder Equity / Market Cap
    2. Profitability (RMW): Operating Profitability = (Revenue - COGS - SGA) / Book Equity
    3. Investment (CMA): Asset Growth = (Total Assets_t - Total Assets_t-1) / Total Assets_t-1
    """
    

    def __init__(self, tickers, mode='global'):
        self.tickers = tickers
        self.mode = mode  # 'global' (EEUU strict) o 'argentina' (Local check)
        self.data_store = []
    
    def fetch_data(self):
        """Descarga datos fundamentales 'crudos' para respetar Artículo 2."""
        logger.info(f"Iniciando descarga fundamental ({self.mode.upper()}) para {len(self.tickers)} activos...")
        
        for ticker in self.tickers:
            try:
                t = yf.Ticker(ticker)
                info = t.info
                # Usar Balance y Financials anuales para consistencia
                bs = t.balance_sheet
                fin = t.financials
                
                if bs.empty or fin.empty:
                    logger.warning(f"{ticker}: Datos financieros insuficientes ({self.mode}). Omitiendo.")
                    continue
                
                # Datos Sectoriales
                sector = info.get('sector', 'Unknown')
                mkt_cap = info.get('marketCap', np.nan)
                price = info.get('currentPrice', np.nan)
                
                # --- FILTROS DE CONSISTENCIA DE MONEDA ---
                fin_curr = info.get('financialCurrency')
                stock_curr = info.get('currency')

                if self.mode == 'global':
                    # MODO GLOBAL: Estricto. Solo USD/USD (o coincidencia exacta).
                    # Descarta ADRs con divisa cruzada (JPY vs USD).
                    if fin_curr and stock_curr and fin_curr != stock_curr:
                        logger.warning(f"{ticker}: [GLOBAL] Descarte por Divisa Cruzada ({stock_curr} vs {fin_curr}).")
                        continue

                elif self.mode == 'argentina':
                    # MODO ARGENTINA:
                    # 1. Si es .BA: Esperamos ARS en ambos lados.
                    # 2. Si es Ticker puro (YPF, GGAL ADR): Esperamos USD/USD.
                    # PERO cuidado con empresas mixtas.
                    # Para simplificar: Aceptamos todo, asumiendo que el usuario
                    # nos dio una lista curada de activos comparables ("Peras con Peras").
                    # Opcional: Podríamos forzar que si es .BA tenga ARS.
                    pass

                # --- Factor VALUE (Book-to-Market) ---
                # Book Value (Total Stockholder Equity) más reciente
                try:
                    book_value = bs.loc['Total Stockholder Equity'].iloc[0]
                except KeyError:
                    try:
                        book_value = bs.loc['Total Equity Gross Minority Interest'].iloc[0]
                    except:
                        logger.warning(f"{ticker}: No se encontró 'Total Stockholder Equity'.")
                        continue
                
                # Market Cap
                if pd.isna(mkt_cap):
                    shares = info.get('sharesOutstanding', 0)
                    if shares and price:
                        mkt_cap = shares * price
                    else:
                        continue
                        
                bm_ratio = book_value / mkt_cap
                
                # --- Factor PROFITABILITY ---
                try:
                    op_income = fin.loc['Operating Income'].iloc[0]
                    profitability = op_income / book_value if book_value > 0 else np.nan
                except KeyError:
                    profitability = np.nan
                
                # --- Factor INVESTMENT ---
                try:
                    assets_t = bs.loc['Total Assets'].iloc[0]
                    assets_t1 = bs.loc['Total Assets'].iloc[1]
                    asset_growth = (assets_t - assets_t1) / assets_t1
                except:
                    asset_growth = np.nan
                    
                self.data_store.append({
                    'Ticker': ticker,
                    'Sector': sector,
                    'MarketCap': mkt_cap,
                    'Book_to_Market': bm_ratio,
                    'Profitability': profitability,
                    'Asset_Growth': asset_growth
                })
                
                logger.info(f"{ticker}: Datos procesados correctamente.")
                
            except Exception as e:
                logger.error(f"Error procesando {ticker}: {e}")

    def calculate_scores(self):
        """Calcula Z-Scores relativos por sector."""
        df = pd.DataFrame(self.data_store)
        if df.empty: return df
        
        cols = ['Book_to_Market', 'Profitability', 'Asset_Growth']
        df[cols] = df[cols].apply(pd.to_numeric, errors='coerce')
        df.dropna(subset=cols, inplace=True)
        
        def safe_zscore(x):
            if len(x) < 3: return (x - x.mean()) / (x.std() + 1e-6)
            return (x - x.mean()) / x.std()
            
        # Agrupar por Sector para Z-Score
        # Nota: En modo 'argentina', si hay pocos activos, tal vez convenga Z-Score global del grupo
        # Pero mantendremos sectorial para consistencia.
        df['Z_Value'] = df.groupby('Sector')['Book_to_Market'].transform(safe_zscore)
        df['Z_Prof'] = df.groupby('Sector')['Profitability'].transform(safe_zscore)
        df['Z_Inv'] = df.groupby('Sector')['Asset_Growth'].transform(safe_zscore)
        
        # Si Z-Score da NaN (ej: solo 1 activo en el sector), rellenar con 0 (Promedio)
        df.fillna({'Z_Value':0, 'Z_Prof':0, 'Z_Inv':0}, inplace=True)
        
        w_val, w_prof, w_inv = 0.4, 0.3, 0.3
        df['Final_Score'] = (w_val * df['Z_Value']) + (w_prof * df['Z_Prof']) - (w_inv * df['Z_Inv'])
        
        return df

def run_screener(filename, mode, output_name):
    print(f"\n>>> PROCESANDO LISTA: {mode.upper()} ({filename})")
    try:
        with open(filename, 'r') as f:
            content = f.read()
            tickers = [t.strip().upper() for t in content.replace('\\n', ',').split(',') if t.strip()]
    except FileNotFoundError:
        print(f"Error: {filename} no encontrado.")
        return

    screener = FamaFrenchCalculator(tickers, mode=mode)
    screener.fetch_data()
    df_results = screener.calculate_scores()
    
    if df_results.empty:
        print(f"No se obtuvieron resultados para {mode}.")
        return

    df_results = df_results.sort_values(by='Final_Score', ascending=False)
    
    # Guardar Excel
    df_results.to_excel(output_name, index=False)
    print(f"Ranking {mode} guardado en: {output_name}")
    
    # Top 5 Preview
    print(f"\nTOP 5 {mode.upper()}")
    print(df_results[['Ticker', 'Sector', 'Final_Score', 'Z_Value']].head().to_string())

def main():
    # 1. Ranking Global (EEUU + Internacionales en USD)
    run_screener('ticker.txt', 'global', 'Ranking_Global_Top.xlsx')
    
    # 2. Ranking Argentina (Local .BA + ADRs mixtos)
    run_screener('ticker_arg.txt', 'argentina', 'Ranking_Argentina_Top.xlsx')
    
    print("\n" + "="*50)
    print("PROCESO FINALIZADO. REVISE LOS ARCHIVOS APP_GLOBAL Y APP_ARGENTINA.")

if __name__ == "__main__":
    main()

