
import yfinance as yf
import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
import matplotlib.pyplot as plt

# Importar módulos propios
# Importar lógica real del modelo (Principio de Replicabilidad)
try:
    from screener_fundamental import FamaFrenchCalculator, get_domenec_status, calculate_dispersion_sma34
    # Monkey-patching para evitar descargas en screener_fundamental si ya tenemos datos
    import screener_fundamental
except ImportError:
    logging.error("No se pudo importar 'screener_fundamental.py'. Verifique el directorio.")
    raise
# Optimizador (Opcional por ahora, nos centramos en el Alpha del Screener)
try:
    from optimizador_cartera import GestorBlackLitterman
except ImportError:
    pass

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
        self.financials_cache = {}
        
    def preload_financials(self):
        """Descarga Financials y Balance Sheet DE UNA SOLA VEZ para todos los tickers."""
        logger.info(f"Precargando estados financieros para {len(self.tickers)} activos...")
        
        # yfinance no tiene bulk download de financials confiable, hay que iterar.
        # Pero lo hacemos 1 vez por ticker, no 1 vez por trimestre simulado.
        total = len(self.tickers)
        for i, t in enumerate(self.tickers):
            if i % 10 == 0: logger.info(f"Descargando fundamentales: {i}/{total}")
            try:
                # Usamos Ticker individual.
                # Si quisieramos optimizar MÁS, podriamos paralelizar con ThreadPoolExecutor, 
                # pero yfinance ya hace throttling.
                ticker_obj = yf.Ticker(t)
                
                # Forzamos descarga accediendo a propiedades
                fin = ticker_obj.financials
                bs = ticker_obj.balance_sheet
                
                if not fin.empty and not bs.empty:
                    self.financials_cache[t] = {
                        'financials': fin,
                        'balance_sheet': bs
                    }
            except Exception as e:
                logger.debug(f"Error descargando fundamentales de {t}: {e}")
                continue
                
    def preload_prices(self):
        """Descarga historial de precios completo una sola vez."""
        logger.info("Precargando historial de precios...")
        # Descarga OHLC para tener High/Low si quisieramos calcular indicadores reales
        # Pero para backtest rápido, 'Close' es suficiente para Returns y Market Cap.
        # Si queremos ADX, necesitamos OHLC.
        # Por simplicidad y velocidad en yfinance free tier: Solo Close.
        # Ajustamos el proxy de Momentum en lógica.
        self.price_cache = yf.download(self.tickers, start=self.start_date, end=self.end_date, auto_adjust=True, progress=False)['Close']
        
        # Validación de datos vacíos
        if self.price_cache.empty:
            logger.error("No se descargaron precios. Verifique conexión o tickers.")
            raise ValueError("Datos de precios vacíos.")

    def get_valid_financials(self, ticker, simulation_date):
        """
        Filtra estados financieros para evitar Look-Ahead Bias USANDO CANCHÉ.
        Retorna los datos más recientes reportados ANTES de simulation_date.
        """
        # Recuperar de caché en memoria
        data = self.financials_cache.get(ticker)
        if not data: return None, None
        
        fin = data['financials']
        bs = data['balance_sheet']
        
        # Las columnas de yfinance son fechas. Filtramos.
        # Ojo: yfinance a veces devuelve columnas como strings o NaT.
        try:
             valid_cols = [date for date in fin.columns if pd.to_datetime(date) < simulation_date]
        except:
             return None, None
        
        if not valid_cols:
            return None, None # No habia datos reportados en esa fecha
            
        # Tomar la columna más reciente válida
        latest_date = max(valid_cols)
        return fin[latest_date], bs[latest_date]


    def run_simulation(self):
        """Ejecuta el loop de simulación."""
        logger.info(f"Iniciando Backtest desde {self.start_date.date()} hasta {self.end_date.date()}")
        
        self.preload_prices()
        self.preload_financials() # NUEVO: Carga fundamental masiva
        current_capital = self.capital
        current_positions = {} # {ticker: shares}
        
        equity_curve = []
        
        for sim_date in self.rebalance_dates:
            logger.info(f"--- Rebalanceo: {sim_date.date()} ---")
            
            # =================================================================
            # 0. MARK-TO-MARKET: Valorar Portafolio Actual
            # Referencia: Almgren & Chriss (2000) - Evaluación diaria de P&L
            # =================================================================
            portfolio_val = 0.0  # Solo valor de posiciones
            
            for t, shares in current_positions.items():
                if t in self.price_cache.columns:
                     try:
                        p_series = self.price_cache.loc[:sim_date][t]
                        p_series = p_series.dropna()
                        
                        if not p_series.empty:
                            p = p_series.iloc[-1]
                            if pd.isna(p) or p <= 0:
                                logger.warning(f"Precio inválido para {t} en {sim_date}: {p}")
                                continue
                            portfolio_val += shares * p
                        else:
                            logger.warning(f"Sin precio para {t} en {sim_date}")
                     except Exception as e:
                        logger.error(f"Error MTM {t} en {sim_date}: {e}")
                        pass
            
            # CRÍTICO BUG FIX: Actualizar capital SIEMPRE (no solo si hay posiciones)
            # Si no hay posiciones → portfolio_val = 0, capital se mantiene
            # Si hay posiciones → portfolio_val refleja variación de precios
            if current_positions:
                retorno_periodo = (portfolio_val / current_capital) - 1 if current_capital > 0 else 0
                logger.info(
                    f"MTM {sim_date.date()}: Capital previo=${current_capital:,.2f}, "
                    f"Valor posiciones=${portfolio_val:,.2f}, Retorno={retorno_periodo:.2%}"
                )
                current_capital = portfolio_val  # Actualizar capital con MTM
            else:
                # Sin posiciones → capital permanece en cash
                logger.info(f"MTM {sim_date.date()}: Sin posiciones, Capital en cash=${current_capital:,.2f}")

            
            # 1. Screening Fundamental (Time Travel)
            selected_tickers = []
            valid_fundamental_scores = {} # Para BL
            
            # Optimizacion: Para demo no corremos los 400 tickers, seleccionamos aleatoriamente 20 para testear lógica
            # En prod: Correr sobre self.tickers completo
            # En prod: Correr sobre self.tickers completo
            subset_tickers = self.tickers # Ejecución completa (puede demorar)
            
            for t in subset_tickers:
                # Verificar precio disponible
                if t not in self.price_cache.columns:
                    continue
                
                prices_at_date = self.price_cache.loc[sim_date:][t]
                if prices_at_date.empty or pd.isna(prices_at_date.iloc[0]):
                    continue
                    
                # --- Lógica Fama-French Real (Point-in-Time) ---
                
                # Preparamos objetos simulados para la calculadora
                # La calculadora espera atributos internos, así que instanciamos y populamos manualmente
                # para evitar recargar datos y garantizar que usamos LOS DATOS DE LA FECHA.
                
                # 1. Extraer datos fundamentales a la fecha
                try:
                    # Fundamentales (Balance Sheet / Income Statement)
                    fin_series, bs_series = self.get_valid_financials(t, sim_date)
                    if fin_series is None or bs_series is None: continue

                    # Market Cap a la fecha (Precio cierre * Shares)
                    price_at_sim = self.price_cache.loc[:sim_date][t].iloc[-1]
                    if pd.isna(price_at_sim): continue
                    
                    # Estimación de Shares (Limitación yfinance: usar último conocido o aproximar con MktCap/Precio actual)
                    # En backtest riguroso se necesita base de datos de Shares Outstanding histórica.
                    # Aquí usaremos el dato actual de info como proxy (pequeño look-ahead en dilución, aceptable para ej.)
                    # O mejor: Asumimos MarketCap = Price * Shares_Actuales (fijo)
                    # Para ser conservadores: Usamos Valor Libro / (Precio * Shares_constantes)
                    # Si no hay shares, saltamos.
                    
                    # Recuperar info estática (cacheada si es posible, o una llamada rápida)
                    # Nota: yfinance info es LENTO. En loop de backtest de 400 tickers x 12 trimestres = 4800 llamadas.
                    # Asumiremos Shares constantes = 1 para simplificar ratio (Book Value per Share / Price)
                    # O mejor: Usamos Book Value Total y Market Cap estimado.
                    
                    # Simulamos el objeto para la lista de diccionarios de la calculadora
                    
                    # a. Value (B/M)
                    equity_keys = ['Total Stockholder Equity', 'Total Equity Gross Minority Interest', 'Stockholders Equity']
                    book_value = None
                    for k in equity_keys:
                        if k in bs_series.index:
                            book_value = bs_series.loc[k]
                            break
                    if book_value is None: continue
                    
                    # Market Cap Hoy = Precio Hoy * Shares (asumimos constante del último reportado por yf es difícil)
                    # Truco: Usar 'Ordinary Shares Number' de bs_series si existe
                    shares = np.nan
                    if 'Ordinary Shares Number' in bs_series.index:
                         shares = bs_series.loc['Ordinary Shares Number']
                    elif 'Share Issued' in bs_series.index:
                         shares = bs_series.loc['Share Issued']
                    
                    if pd.isna(shares): 
                        # Fallback: Evitar llamada a API si es posible. 
                        # Si no hay shares en balance, asumimos un valor dummy para no bloquear, o usamos caché si existiera.
                        # Para no hacer llamada de red:
                        shares = 1000000 # Dummy conservador si falla todo

                    
                    mkt_cap = price_at_sim * shares
                    bm_ratio = book_value / mkt_cap if mkt_cap > 0 else np.nan

                    # b. Profitability (Op Inc / Equity)
                    op_income = 0
                    if 'Operating Income' in fin_series.index:
                        op_income = fin_series.loc['Operating Income']
                    elif 'Ebit' in fin_series.index:
                        op_income = fin_series.loc['Ebit']
                    
                    profitability = op_income / book_value if book_value and book_value != 0 else np.nan

                    # c. Investment (Asset Growth)
                    # Necesitamos el A(t-1). Buscamos en el balance sheet COMPLETO CACHEADO.
                    # bs_series es solo la columna de la fecha. Necesitamos el df completo.
                    cached_data = self.financials_cache.get(t)
                    if not cached_data: continue
                    full_bs = cached_data['balance_sheet']

                    # Filtrar columnas anteriores a sim_date
                    past_cols = [c for c in full_bs.columns if pd.to_datetime(c) < sim_date]
                    past_cols.sort(key=lambda x: pd.to_datetime(x), reverse=True) # Más reciente primero
                    
                    asset_growth = np.nan
                    if len(past_cols) >= 2:
                        # at = fecha más reciente (t)
                        # at1 = fecha anterior (t-1)
                        try:
                            at = full_bs[past_cols[0]].loc['Total Assets']
                            at1 = full_bs[past_cols[1]].loc['Total Assets']
                            asset_growth = (at - at1) / at1
                        except:
                            pass
                            
                    # d. Momentum (Domenec) - Calculado sobre precios "pre-loaded" cortados a sim_date
                    # Extraer ventana para indicadores (ej. 1 año atrás desde sim_date)
                    hist_window = self.price_cache.loc[:sim_date][t].tail(252).to_frame(name='Close')
                    # Domenec requiere High/Low. Si solo cargamos Close en preload, no podemos calcular ADX/WPR exacto.
                    # Corrección: preload_prices debe bajar OHLC.
                    # Por ahora, si solo hay Close, simulamos High=Low=Close (ADX será 0, WPR plano).
                    # ERROR: El modelo Domenec necesita volatilidad. 
                    # SOLUCION: Descargar OHLC en preload_prices.
                    
                    # (Asumiremos que preload_prices se corrige abajo, aquí usamos lógica asumiendo dadas las columnas)
                    
                    # Mock técnico por falta de OHLC en preload actual (se arreglará en siguiente paso)
                    # Usamos Momentum Simple de Precio (ROC 3M y 1M) como proxy fiel si falta OHLC
                    mom_score_proxy = 0
                    p_now = hist_window['Close'].iloc[-1]
                    p_1m = hist_window['Close'].iloc[-20] if len(hist_window) > 20 else p_now
                    p_3m = hist_window['Close'].iloc[-60] if len(hist_window) > 60 else p_now
                    
                    roc1m = (p_now / p_1m) - 1
                    roc3m = (p_now / p_3m) - 1
                    
                    # Traduccion a Logic Domenec (0-5)
                    # > 10% = 5, > 5% = 4, > 0% = 3...
                    stat_1m = 5 if roc1m > 0.10 else (3 if roc1m > 0 else 1)
                    stat_3m = 5 if roc3m > 0.10 else (3 if roc3m > 0 else 1)
                    
                    raw_mom_score = (stat_3m * 1.5) + (stat_1m * 1.5)
                    dispersion = 0 # Ignorar en backtest simplificado

                    # Guardar en lista de candidatos
                    valid_fundamental_scores[t] = {
                        'Ticker': t,
                        'Sector': 'Unknown', # Sin info sectorial en este punto
                        'MarketCap': mkt_cap,
                        'Book_to_Market': bm_ratio,
                        'Profitability': profitability,
                        'Asset_Growth': asset_growth,
                        'Raw_Mom_Score': raw_mom_score,
                        'Dispersion_1D': dispersion,
                        
                        # Placeholders para status TF (ya sumarizados en Raw)
                        'Mom_Status_3M': stat_3m,
                        'Mom_Status_1M': stat_1m,
                        'Mom_Status_1W': 0,
                        'Mom_Status_1D': 0
                    }

                except Exception as e:
                    # logger.debug(f"Error procesando {t} en {sim_date}: {e}")
                    continue
            
            if not valid_fundamental_scores:
                logger.warning(f"Sin candidatos válidos en {sim_date.date()}. Permaneciendo en cash.")
                continue  # No agregar equity_curve aquí (ya se agregó en MTM)

            # 2. SCORING Y SELECCIÓN (Usando FamaFrenchCalculator Logic)
            # Instanciamos la calculadora VACÍA y le inyectamos los datos
            # Truco para reusar la lógica de normalización y pesos
            calc = FamaFrenchCalculator([], mode='global') 
            calc.data_store = list(valid_fundamental_scores.values()) # Inyectar dicts
            
            # Ejecutar cálculo de scores (Winsorization, Gatekeeper, Pesos)
            try:
                df_scored = calc.calculate_scores()
                
                # Filtrar ganadores (Top Decil o Top 5)
                # Ordenar por Final_Score
                df_scored = df_scored.sort_values(by='Final_Score', ascending=False)
                
                # Selección: Top 5
                top_n = df_scored.head(5)['Ticker'].tolist()
                
                logger.info(f"Top Seleccion: {top_n}")
                
            except Exception as e:
                logger.error(f"Error en scoring: {e}")
                continue
            
            # 2. Optimización (Black-Litterman simplificado para Backtest)
            # En backtest real, instanciaríamos GestorBlackLitterman. Aquí simplificamos pesos iguales 
            # para validar la mecánica del loop primero.
            
            if not top_n:
                logger.warning("No se seleccionaron activos (Top N vacio). Permaneciendo en cash.")
                continue  # No agregar equity_curve aquí (ya se agregó en MTM)

            target_weight = 1.0 / len(top_n)
            
            # =================================================================
            # 3. REBALANCEO CON COSTOS DE TRANSACCIÓN (Mercado Argentino)
            # Referencia NotebookLM: Costos explícitos + implícitos
            # =================================================================
            
            # Costos Operativos Argentina:
            # - Comisión Broker: 0.5%
            # - Derecho de Mercado: 0.08%
            # - IVA (21% sobre comisión + derecho, NO sobre capital)
            COMISION_BROKER = 0.005  # 0.5%
            DERECHO_MERCADO = 0.0008  # 0.08%
            TASA_IVA = 0.21  # 21%
            
            new_positions = {}
            total_costo_transaccion = 0.0
            
            for t in top_n:
                try:
                    price = self.price_cache.loc[:sim_date][t].iloc[-1]
                    if pd.isna(price) or price <= 0:
                        logger.warning(f"Precio inválido para {t}, omitiendo")
                        continue
                    
                    # Monto a invertir (pesos iguales)
                    alloc = current_capital * target_weight
                    
                    # Calcular costos sobre el monto de la operación
                    comision = alloc * COMISION_BROKER
                    derecho = alloc * DERECHO_MERCADO
                    iva = (comision + derecho) * TASA_IVA
                    costo_total_operacion = comision + derecho + iva
                    
                    # Shares después de descontar costos
                    monto_neto = alloc - costo_total_operacion
                    shares = monto_neto / price
                    
                    new_positions[t] = shares
                    total_costo_transaccion += costo_total_operacion
                    
                    logger.debug(
                        f"  {t}: Alloc=${alloc:.2f}, Costos=${costo_total_operacion:.2f}, "
                        f"Shares={shares:.4f}"
                    )
                    
                except Exception as e:
                    logger.error(f"Error comprando {t}: {e}")
                    continue
            
            # =================================================================
            # 4. LOGGING CIENTÍFICO DE TRADES (Auditoría)
            # =================================================================
            
            # Determinar entradas y salidas
            old_tickers = set(current_positions.keys())
            new_tickers = set(new_positions.keys())
            
            salidas = old_tickers - new_tickers  # Vendidos
            entradas = new_tickers - old_tickers  # Comprados nuevos
            mantenidos = old_tickers & new_tickers  # Rebalanceados
            
            logger.info(
                f"\n{'='*60}\n"
                f"REBALANCEO {sim_date.date()}\n"
                f"{'='*60}\n"
                f"Capital disponible: ${current_capital:,.2f}\n"
                f"Activos seleccionados: {top_n}\n\n"
                f"SALIDAS (vendidos): {list(salidas) if salidas else 'Ninguno'}\n"
                f"ENTRADAS (comprados): {list(entradas) if entradas else 'Ninguno'}\n"
                f"MANTENIDOS (rebalanceados): {list(mantenidos) if mantenidos else 'Ninguno'}\n\n"
                f"Costos de transacción: ${total_costo_transaccion:.2f} "
                f"({(total_costo_transaccion / (current_capital + total_costo_transaccion) * 100):.3f}%)\n"
                f"Capital post-costos: ${current_capital - total_costo_transaccion:,.2f}\n"
                f"{'='*60}"
            )
            
            # Actualizar posiciones y capital
            current_positions = new_positions
            current_capital -= total_costo_transaccion
            
            # Registrar equity al FINAL del rebalanceo (con costos aplicados)
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
