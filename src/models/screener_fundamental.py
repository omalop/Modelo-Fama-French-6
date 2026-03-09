import yfinance as yf
import pandas as pd
import numpy as np
import logging
import argparse
import warnings
from datetime import datetime, timedelta

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
warnings.filterwarnings("ignore")

# Import DB Manager relative to this file
import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
try:
    from src.data.db_manager import DBManager
except ImportError:
    logger.error("No se pudo importar src.data.db_manager. Verifique PYTHONPATH.")
    sys.exit(1)

# ==============================================================================
# 1. INDICADORES TÉCNICOS (Metodología Domenec)
# ==============================================================================

def calculate_rma(series, length):
    """Calcula la Wilder's Moving Average (RMA)."""
    return series.ewm(alpha=1/length, min_periods=length, adjust=False).mean()

def calculate_adx(high, low, close, period=14):
    """Calcula el ADX usando metodología Wilder."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    up = high - high.shift(1)
    down = low.shift(1) - low

    pos_dm = np.where((up > down) & (up > 0), up, 0.0)
    neg_dm = np.where((down > up) & (down > 0), down, 0.0)

    pos_dm = pd.Series(pos_dm, index=high.index)
    neg_dm = pd.Series(neg_dm, index=high.index)

    tr_smooth = calculate_rma(tr, period)
    pos_dm_smooth = calculate_rma(pos_dm, period)
    neg_dm_smooth = calculate_rma(neg_dm, period)

    # Evitar división por cero
    tr_smooth = tr_smooth.replace(0, np.nan)
    pos_di = 100 * (pos_dm_smooth / tr_smooth)
    neg_di = 100 * (neg_dm_smooth / tr_smooth)
    
    # Manejo de NaNs en la suma
    sum_di = pos_di + neg_di
    sum_di = sum_di.replace(0, np.nan)

    dx = 100 * (abs(pos_di - neg_di) / sum_di)
    adx = calculate_rma(dx, period)
    return adx

def calculate_wpr(high, low, close, period=14):
    """Calcula Williams %R."""
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    denom = highest_high - lowest_low
    denom = denom.replace(0, np.nan) # Evitar div 0
    wpr = -100 * ((highest_high - close) / denom)
    return wpr

def get_domenec_status(df):
    """
    Determina el estado de 'Control Total' para la última vela.
    Retorna un puntaje numérico:
    5: Impulso Fuerte (Verde Oscuro)
    4: Impulso Medio (Verde)
    3: Pullback (Azul)
    2: Sin Fuerza (Amarillo)
    1: Correccion Fuerte (Rojo)
    0: Bajista / Neutral
    """
    if df.empty or len(df) < 50: return 0
    
    # Parametros
    pDir = 40
    pForce = 7
    upper_band = -25
    
    # Calcular indicadores solo lo necesario (vectorizado es rapido)
    df['WPR'] = calculate_wpr(df['High'], df['Low'], df['Close'], pDir)
    df['ADX'] = calculate_adx(df['High'], df['Low'], df['Close'], pForce)
    
    # Valores actuales y previos
    wpr = df['WPR'].iloc[-1]
    wpr_prev = df['WPR'].iloc[-2]
    adx = df['ADX'].iloc[-1]
    adx_prev = df['ADX'].iloc[-2]
    
    wpr_up = wpr > wpr_prev
    wpr_down = wpr < wpr_prev
    sig_up = adx >= adx_prev
    sig_down = adx < adx_prev
    
    # Lógica de Estados
    if (wpr > -50) and wpr_up and sig_up and (wpr > upper_band):
        return 5 # Impulso Fuerte
    elif (wpr > -50) and wpr_up and sig_up and (wpr <= upper_band):
        return 4 # Impulso Medio
    elif (wpr > -50) and wpr_up and sig_down:
        return 3 # Pullback
    elif (wpr > -50) and wpr_down and sig_down:
        return 2 # Sin Fuerza
    elif (wpr > -50) and wpr_down and sig_up:
        return 1 # Correccion Fuerte
    else:
        return 0 # Bajista

def calculate_dispersion_sma34(df):
    """Calcula dispersión % respecto a SMA 34."""
    if df.empty or len(df) < 35: return 0.0
    sma34 = df['Close'].rolling(window=34).mean().iloc[-1]
    price = df['Close'].iloc[-1]
    if pd.isna(sma34) or sma34 == 0: return 0.0
    return ((price - sma34) / sma34) * 100


# ==============================================================================
# 2. CCL ALINEADO (Mejora Robustez — Artículo 2/3)
# ==============================================================================

def obtener_serie_ccl() -> pd.Series:
    """
    Calcula la serie histórica del Dólar CCL usando GGAL.BA / GGAL.

    Fundamento:
        El Contado con Liqui (CCL) es el tipo de cambio implícito que surge
        de operar simultáneamente un activo local y su ADR/cedear:
            CCL = GGAL.BA (en ARS) / GGAL (en USD) * 10
        El factor 10 refleja que 1 ADR de GGAL = 10 acciones locales.

    Alineación por días operativos concurrentes:
        En lugar de usar forward-fill simple, se toman solo los días en que
        AMBOS mercados operaron, eliminando gaps causados por feriados asimétricos
        entre Buenos Aires y Nueva York.

    Referencia:
        Brigo, D., Mercurio, F. (2006). "Interest Rate Models — Theory and Practice".
        Springer, Cap. 1 (Principio de no arbitraje en tipo de cambio implícito).

    Returns:
        pd.Series con el CCL diario alineado, índice de tipo DatetimeIndex (tz-naive).
    """
    try:
        datos = yf.download(["GGAL.BA", "GGAL"], period="5y", progress=False)
        if datos.empty:
            logger.warning("No se pudo descargar datos para CCL.")
            return pd.Series(dtype=float)

        # Extraer series de cierre
        if isinstance(datos.columns, pd.MultiIndex):
            # Identificar nivel Ticker
            ticker_level = next(
                (i for i, name in enumerate(datos.columns.names) if name == 'Ticker'), 1
            )
            price_level = 1 - ticker_level
            # Extraer por ticker usando xs en el nivel de Price='Close'
            try:
                if ticker_level == 1:
                    ggal_ba = datos.xs('GGAL.BA', level='Ticker', axis=1)['Close']
                    ggal_adr = datos.xs('GGAL', level='Ticker', axis=1)['Close']
                else:
                    ggal_ba = datos['GGAL.BA']['Close']
                    ggal_adr = datos['GGAL']['Close']
            except Exception:
                ggal_ba = datos.loc[:, (slice(None), 'GGAL.BA')].iloc[:, 0]
                ggal_adr = datos.loc[:, (slice(None), 'GGAL')].iloc[:, 0]
        else:
            logger.warning("Formato inesperado en descarga CCL. Sin CCL disponible.")
            return pd.Series(dtype=float)

        # Normalizar índice
        ggal_ba.index = pd.to_datetime(ggal_ba.index).normalize().tz_localize(None)
        ggal_adr.index = pd.to_datetime(ggal_adr.index).normalize().tz_localize(None)

        # Alineación por días concurrentes (inner join)
        df_ccl = pd.concat([ggal_ba.rename("ba"), ggal_adr.rename("adr")], axis=1).dropna()

        if df_ccl.empty:
            logger.warning("Sin días concurrentes GGAL.BA/GGAL para CCL.")
            return pd.Series(dtype=float)

        ccl = (df_ccl["ba"] / df_ccl["adr"]) * 10
        logger.info(f"CCL calculado: {len(ccl)} observaciones. Último: {ccl.iloc[-1]:.2f}")
        return ccl

    except Exception as e:
        logger.error(f"Error calculando CCL: {e}")
        return pd.Series(dtype=float)


def obtener_retornos_benchmark(modo: str, hist: pd.DataFrame) -> pd.Series:
    """
    Descarga y alinea los retornos del benchmark correspondiente al modo del screener.

    Fundamento del Beta:
        Sharpe, W. F. (1964). "Capital Asset Prices: A Theory of Market Equilibrium
        under Conditions of Risk". Journal of Finance, 19(3), 425-442.
        β = Cov(R_activo, R_benchmark) / Var(R_benchmark)

    Supuestos:
        - Período: mínimo 252 días hábiles (1 año)
        - Los retornos del benchmark se calculan al cierre ajustado

    Args:
        modo: 'global_sec' o 'global_intl' → usa SPY.
              'argentina' → usa Merval en CCL (^MERV ajustado por CCL).
        hist: DataFrame de precios del activo (para alinear fechas).

    Returns:
        pd.Series con retornos porcentuales del benchmark alineados al índice de hist.
    """
    try:
        if modo in ('global_sec', 'global_intl'):
            bm_ticker = "SPY"
            bm_data = yf.download(bm_ticker, period="5y", progress=False)
            if isinstance(bm_data.columns, pd.MultiIndex):
                bm_close = bm_data.xs('Close', level='Price', axis=1).squeeze()
            else:
                bm_close = bm_data['Close']
        else:
            # Argentina: Merval ajustado por CCL
            merval_raw = yf.download("^MERV", period="5y", progress=False)
            if isinstance(merval_raw.columns, pd.MultiIndex):
                merval_close = merval_raw.xs('Close', level='Price', axis=1).squeeze()
            else:
                merval_close = merval_raw['Close']
            
            ccl = obtener_serie_ccl()
            if ccl.empty:
                return pd.Series(dtype=float)
            
            merval_close.index = pd.to_datetime(merval_close.index).normalize().tz_localize(None)
            ccl.index = pd.to_datetime(ccl.index).normalize().tz_localize(None)
            
            df_bm = pd.concat([merval_close.rename("merv"), ccl.rename("ccl")], axis=1).dropna()
            bm_close = df_bm["merv"] / df_bm["ccl"]

        bm_close.index = pd.to_datetime(bm_close.index).normalize().tz_localize(None)
        bm_ret = bm_close.pct_change().dropna()
        return bm_ret

    except Exception as e:
        logger.error(f"Error obteniendo benchmark para modo '{modo}': {e}")
        return pd.Series(dtype=float)


def calcular_beta(ret_activo: pd.Series, ret_benchmark: pd.Series, min_obs: int = 60) -> float:
    """
    Calcula el Beta de un activo respecto a su benchmark.

    Fundamento:
        Fama, E. F., & French, K. R. (1992). "The Cross-Section of Expected Stock Returns".
        Journal of Finance, 47(2), 427-465. — Factor MKT (Riesgo de Mercado).
        β = Cov(R_i, R_m) / Var(R_m)

    Supuestos:
        - Las series deben estar alineadas por fecha.
        - Se requieren al menos `min_obs` observaciones para un Beta estadísticamente válido.

    Args:
        ret_activo: Retornos diarios del activo.
        ret_benchmark: Retornos diarios del benchmark.
        min_obs: Mínimo de observaciones para calcular.

    Returns:
        Beta como float, o np.nan si no hay suficientes datos.
    """
    # Alinear por índice común
    datos = pd.concat([ret_activo.rename("r_i"), ret_benchmark.rename("r_m")], axis=1).dropna()
    
    if len(datos) < min_obs:
        return np.nan
    
    # Usar últimos 252 días (1 año bursátil)
    datos = datos.tail(252)
    
    cov_matrix = np.cov(datos["r_i"], datos["r_m"])
    var_bm = cov_matrix[1, 1]
    
    if var_bm == 0:
        return np.nan
    
    beta = cov_matrix[0, 1] / var_bm
    return beta


# ==============================================================================
# 3. CALCULADORA FAMA-FRENCH 6 FACTORES + MOMENTUM
# ==============================================================================

class FamaFrenchCalculator:
    """
    Calculadora basada en el Modelo Fama-French 6 Factores:

    Pilar FF — 5 Factores (60% del Score Total):
        - Value         (HML): Book-to-Market Ratio.
        - Profitability (RMW): Operating Profitability.
        - Investment    (CMA): Crecimiento de Activos (Conservatism).
        - Size          (SMB): Small Minus Big (Market Cap inverso).
        - Market Risk  (Beta): Covarianza del activo con el benchmark.

    Referencia:
        Fama, E. F., & French, K. R. (2015). "A five-factor asset pricing model".
        Journal of Financial Economics, 116(1), 1-22.
        Fama, E. F., & French, K. R. (2018). "Choosing factors".
        Journal of Financial Economics, 128(2), 234-252.

    Pilar Momentum — 1 Factor (40% del Score Total):
        - Momentum Multifractal Domenec (4 temporalidades con pesos diferenciales)
        El modelo compone 5 FF + Momentum = 6 factores en total (de ahí el nombre).

    Referencia Momentum:
        Jegadeesh, N., & Titman, S. (1993). "Returns to Buying Winners and Selling Losers".
        Journal of Finance, 48(1), 65-91.
    """
    
    def __init__(self, tickers, modo='global', fuente='yfinance'):
        self.tickers = tickers
        self.modo = modo
        self.fuente = fuente
        self.data_store = []
        self._ret_benchmark = None  # Cache del benchmark
    
    def _obtener_ret_benchmark_cacheado(self, hist: pd.DataFrame) -> pd.Series:
        """Descarga y cachea los retornos del benchmark solo 1 vez por instancia."""
        if self._ret_benchmark is None:
            logger.info(f"Descargando benchmark para modo '{self.modo}'...")
            self._ret_benchmark = obtener_retornos_benchmark(self.modo, hist)
            if self._ret_benchmark.empty:
                logger.warning("Benchmark sin datos. Beta se calculará como NaN.")
        return self._ret_benchmark

    def fetch_data(self):
        """Descarga fundamentales y técnicos usando DBManager (Caché Diario)."""
        logger.info(f"Iniciando análisis ({self.modo.upper()}) para {len(self.tickers)} activos...")
        
        # 1. Inicializar y Actualizar DB
        try:
            db = DBManager()
            db.update_history(self.tickers, source=self.fuente)
        except Exception as e:
            logger.error(f"Error crítico en DBManager: {e}")
            return

        # 2. Recuperar Datos Bulk (Eficiencia)
        logger.info("Recuperando datos desde DB local...")
        try:
            df_prices_all = db.get_price_history(self.tickers)
            if not df_prices_all.empty:
                df_prices_all['date'] = pd.to_datetime(df_prices_all['date'])
            
            df_financials_all = db.get_financials(self.tickers)
            if not df_financials_all.empty:
                df_financials_all['report_date'] = pd.to_datetime(df_financials_all['report_date'])
            
            df_meta_all = db.get_tickers_metadata(self.tickers)
            db.close()
            
        except Exception as e:
            logger.error(f"Error recuperando datos de DB: {e}")
            return

        # 3. Precalcular CCL si es Argentina
        ccl_serie = pd.Series(dtype=float)
        if self.modo == 'argentina':
            ccl_serie = obtener_serie_ccl()

        # 4. Procesamiento Ticker por Ticker
        total = len(self.tickers)
        for i, ticker in enumerate(self.tickers):
            print(f"[{i+1}/{total}] Procesando {ticker}...", end='\r')
            try:
                # --- A. METADATOS ---
                meta_rows = df_meta_all[df_meta_all['ticker'] == ticker]
                if meta_rows.empty:
                    continue
                meta = meta_rows.iloc[0]
                
                sector = meta['sector']
                currency = meta['currency']
                shares = meta['shares']
                
                # --- B. PRECIOS ---
                hist = df_prices_all[df_prices_all['ticker'] == ticker].copy()
                if hist.empty: continue
                
                hist.rename(columns={
                    'date': 'Date', 'open': 'Open', 'high': 'High', 
                    'low': 'Low', 'close': 'Close', 'volume': 'Volume'
                }, inplace=True)
                hist.set_index('Date', inplace=True)
                hist.sort_index(inplace=True)
                
                price = hist['Close'].iloc[-1]
                
                # Ajuste por CCL para acciones locales (Market Cap en USD)
                if ticker.endswith('.BA') and not ccl_serie.empty:
                    idx_comun = hist.index.intersection(ccl_serie.index)
                    if len(idx_comun) > 0:
                        ultimo_ccl = ccl_serie.loc[idx_comun].iloc[-1]
                        mkt_cap = (price / ultimo_ccl) * shares if shares > 0 else 0
                    else:
                        mkt_cap = 0
                else:
                    mkt_cap = price * shares if shares > 0 else 0
                
                if mkt_cap == 0: continue

                # --- C. FUNDAMENTALES ---
                fin_rows = df_financials_all[df_financials_all['ticker'] == ticker]
                if fin_rows.empty: continue
                
                bs_data = fin_rows[fin_rows['type'] == 'BS']
                bs = bs_data.pivot(index='metric', columns='report_date', values='value')
                
                is_data = fin_rows[fin_rows['type'] == 'IS']
                fin = is_data.pivot(index='metric', columns='report_date', values='value')
                
                bs = bs[sorted(bs.columns, reverse=True)]
                fin = fin[sorted(fin.columns, reverse=True)]

                # 1. VALUE (Book-to-Market)
                equity_keys = ['Total Stockholder Equity', 'Total Equity Gross Minority Interest', 'Stockholders Equity']
                book_value = None
                for k in equity_keys:
                    if k in bs.index:
                        book_value = bs.loc[k].iloc[0]
                        break
                
                if book_value is None: continue
                bm_ratio = book_value / mkt_cap

                # 2. PROFITABILITY (Operating Profitability / RMW)
                op_income = 0
                if 'Operating Income' in fin.index:
                    op_income = fin.loc['Operating Income'].iloc[0]
                elif 'Ebit' in fin.index:
                    op_income = fin.loc['Ebit'].iloc[0]
                    
                profitability = op_income / book_value if book_value != 0 else np.nan
                
                # 3. INVESTMENT (Asset Growth / CMA)
                asset_growth = np.nan
                if 'Total Assets' in bs.index:
                    assets = bs.loc['Total Assets']
                    if len(assets) >= 2:
                        at = assets.iloc[0]
                        at1 = assets.iloc[1]
                        asset_growth = (at - at1) / at1

                # 4. SIZE (SMB — Small Minus Big)
                # Racional: log(MktCap) captura la distribución right-skewed.
                # El signo se invierte en el z-score: menor cap → mayor score.
                log_mkt_cap = np.log(mkt_cap) if mkt_cap > 0 else np.nan

                # 5. MARKET RISK (Beta respecto al benchmark)
                ret_bm = self._obtener_ret_benchmark_cacheado(hist)
                if not ret_bm.empty:
                    hist_idx = hist.index.normalize().tz_localize(None) if hist.index.tz is not None else hist.index.normalize()
                    ret_activo = hist['Close'].pct_change().dropna()
                    ret_activo.index = hist_idx[-len(ret_activo):]
                    beta = calcular_beta(ret_activo, ret_bm)
                else:
                    beta = np.nan

                # --- D. MOMENTUM MULTIFRACTAL (Domenec) ---
                tf_status = {}
                
                # 1. Diario (1d)
                try:
                    status_1d = get_domenec_status(hist.copy())
                    disp_1d = calculate_dispersion_sma34(hist.copy())
                    tf_status['1d'] = {'status': status_1d, 'disp': disp_1d}
                except:
                    tf_status['1d'] = {'status': 0, 'disp': 0}

                # 2. Semanal (1wk) - Resample
                try:
                    hist_wk = hist.resample('W').agg({
                        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                    }).dropna()
                    status_1wk = get_domenec_status(hist_wk.copy())
                    tf_status['1wk'] = {'status': status_1wk}
                except:
                    tf_status['1wk'] = {'status': 0}

                # 3. Mensual (1mo) - Resample
                try:
                    hist_mo = hist.resample('ME').agg({
                        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                    }).dropna()
                    status_1mo = get_domenec_status(hist_mo.copy())
                    tf_status['1mo'] = {'status': status_1mo}
                except:
                    tf_status['1mo'] = {'status': 0}

                # 4. Trimestral (3mo) - Resample (Proxy Macro)
                try:
                    hist_3mo = hist.resample('QE').agg({
                        'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                    }).dropna()
                    status_3mo = get_domenec_status(hist_3mo.copy())
                    tf_status['3mo'] = {'status': status_3mo}
                except:
                    tf_status['3mo'] = {'status': 0}

                # Guardar Datos
                self.data_store.append({
                    'Ticker': ticker,
                    'Sector': sector,
                    'MarketCap': mkt_cap,
                    
                    # FF Factors (crudos)
                    'Book_to_Market': bm_ratio,
                    'Profitability': profitability,
                    'Asset_Growth': asset_growth,
                    'Log_MktCap': log_mkt_cap,     # Para SMB
                    'Beta': beta,                    # Para Market Risk
                    
                    # Momentum Raw Data
                    'Mom_Status_3M': tf_status.get('3mo', {}).get('status', 0),
                    'Mom_Status_1M': tf_status.get('1mo', {}).get('status', 0),
                    'Mom_Status_1W': tf_status.get('1wk', {}).get('status', 0),
                    'Mom_Status_1D': tf_status.get('1d', {}).get('status', 0),
                    'Dispersion_1D': tf_status.get('1d', {}).get('disp', 0.0)
                })
                
            except Exception as e:
                logger.error(f"Error procesando {ticker}: {e}")

    def calculate_scores(self):
        """
        Calcula Scores Finales usando el Modelo Fama-French de 5 Factores + Momentum Domenec.

        Estructura de Ponderación:
            60% → Factores Fama-French (FF5):
                Value      (HML):  15% del total
                Profitability(RMW):15% del total
                Investment (CMA):  10% del total
                Size       (SMB):  10% del total
                Market Risk(Beta):  10% del total
            40% → Momentum Domenec (4 temporalidades):
                3M: 16% del total (mayor peso — tendencia macro)
                1M: 14% del total (tendencia principal)
                1W:  6% del total (penalizado — ruido)
                1D:  4% del total (penalizado — ruido)

        El modelo se denomina 'FF6' porque combina los 5 factores canónicos de
        Fama-French + el factor Momentum como sexto componente.

        Referencia de Ponderación:
            Fama, E. F., & French, K. R. (2015). Op. cit.
            Harvey, C. R., Liu, Y., & Zhu, H. (2016). "... and the Cross-Section of
            Expected Returns". Review of Financial Studies, 29(1), 5-68.
        """
        df = pd.DataFrame(self.data_store)
        if df.empty: return df
        
        # --- 1. NORMALIZACIÓN ROBUSTA (Mediana/IQR) ---
        def robust_zscore(x):
            """
            Z-Score robusto usando mediana y rango intercuartílico.
            Referencia: Rousseeuw & Croux (1993) — Alternatives to the Median 
            Absolute Deviation. JASA, 88(424), 1273-1283.
            """
            median = x.median()
            iqr = x.quantile(0.75) - x.quantile(0.25)
            if iqr == 0: iqr = x.abs().mean()
            if iqr == 0: return pd.Series(np.zeros(len(x)), index=x.index)
            return (x - median) / iqr

        # Columnas FF a normalizar
        cols_ff = ['Book_to_Market', 'Profitability', 'Asset_Growth', 'Log_MktCap', 'Beta']
        
        # Asegurar numérico y limpiar (dropna completo solo si > 3 de 5 son NaN)
        for col in cols_ff:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        
        # Solo eliminar filas donde los 3 factores base (Value, Prof, Investment) son todos NaN
        df.dropna(subset=['Book_to_Market', 'Profitability', 'Asset_Growth'], inplace=True)
        
        # --- 2. Z-SCORES POR SECTOR ---
        for col in cols_ff:
            z_col = f'Z_{col}'
            df[z_col] = df.groupby('Sector')[col].transform(robust_zscore)
            df[z_col] = df[z_col].fillna(0)
        
        # Alias claros para cada factor
        df['Z_Value'] = df['Z_Book_to_Market']
        df['Z_Prof']  = df['Z_Profitability']
        df['Z_Inv']   = df['Z_Asset_Growth']
        df['Z_Size']  = df['Z_Log_MktCap']    # Se invertirá (pequeñas > grandes)
        df['Z_Risk']  = df['Z_Beta']           # Se invertirá (baja beta > alta beta en long-only)

        # --- 3. AJUSTES FAMA-FRENCH ---
        
        # A. CMA CAP (tope de destrucción de activos)
        df['Z_Inv_Capped'] = df['Z_Inv'].clip(lower=-1.0)

        # B. SMB: invertir Z_Size (empresas pequeñas → log_cap bajo → zscore negativo → bueno)
        df['Z_Size_FF'] = -1.0 * df['Z_Size']

        # C. MARKET RISK: Se usa Low-Beta Anomaly:
        # "Betting Against Beta" — Frazzini & Pedersen (2014)
        # Activos de baja beta tienden a superar ajustados por riesgo en mercados eficientes.
        # Una Beta < 1 en mercado local es neutralmente positivo.
        # Estrategia: premiar Beta < 1, penalizar Beta > 2.
        # Ajuste: penalizamos la beta alta (invertimos el zscore)
        df['Z_Risk_FF'] = -1.0 * df['Z_Risk']
        
        # D. RMW GATEKEEPER (pérdidas absolutas)
        mask_loss = df['Profitability'] < 0
        
        # --- 4. MOMENTUM MULTIFRACTAL (Domenec) ---
        # Pesos de las temporalidades (suman al 100% del canal de Momentum=40%):
        #   3M → 40% del canal Momentum (tendencia macro/estructural)
        #   1M → 35% del canal Momentum (tendencia principal)
        #   1W → 15% del canal Momentum (tendencia corta, penalizada)
        #   1D →  10% del canal Momentum (ruido, penalizado)
        #
        # Referencia: Asness, C., Moskowitz, T., Pedersen, L. (2013).
        # "Value and Momentum Everywhere". Journal of Finance, 68(3), 929-985.
        
        df['Raw_Mom_Score'] = (
            (df['Mom_Status_3M'] * 2.0) +   # 40% ponderación macro
            (df['Mom_Status_1M'] * 1.75) +  # 35% ponderación principal
            (df['Mom_Status_1W'] * 0.75) +  # 15% ponderación corta
            (df['Mom_Status_1D'] * 0.50)    # 10% ponderación intratrend
        )
        
        # Penalización por Agotamiento en Diario (Dispersión Extrema sobre SMA34)
        df['Pct_Dispersion'] = df['Dispersion_1D'].rank(pct=True)
        mask_agotamiento = (df['Pct_Dispersion'] > 0.95) & (df['Mom_Status_1D'] >= 3)
        df.loc[mask_agotamiento, 'Raw_Mom_Score'] -= 5.0
        
        # Normalizar Momentum
        df['Z_Mom'] = robust_zscore(df['Raw_Mom_Score'])

        # --- 5. CORRECCIONES ESTADÍSTICAS ---
        
        # 5A. Gatekeeper: empresas con pérdidas → Z_Prof forzado a -3
        cols_prof = ['Z_Profitability', 'Z_Prof']
        for c in cols_prof:
            if c in df.columns:
                df.loc[mask_loss, c] = -3.0

        # 5B. Winsorization [−3, 3] para todos los z-scores (evitar influencia de outliers)
        all_z_cols = ['Z_Value', 'Z_Prof', 'Z_Inv', 'Z_Mom', 'Z_Size_FF', 'Z_Risk_FF',
                      'Z_Book_to_Market', 'Z_Profitability', 'Z_Asset_Growth']
        for c in all_z_cols:
            if c in df.columns:
                df[c] = df[c].clip(lower=-3.0, upper=3.0)

        # 5C. Recalcular Z_Inv_Capped sobre Z_Inv winsorizado
        df['Z_Inv_Capped'] = df['Z_Inv'].clip(lower=-1.0)

        # --- 6. SCORE FINAL ---
        # 60% FF (distribuido entre 5 factores):
        #   Value:      15% del total
        #   Prof:       15% del total
        #   Investment: 10% del total
        #   Size:       10% del total
        #   Risk:       10% del total
        # 40% Momentum (ya normalizado)
        #
        # Nota sobre ponderaciones:
        # Fama & French (2015) documentan que Value y Profitability tienen primas
        # históricas más consistentes que Size e Investment.
        
        w_val  = 0.15  # HML
        w_prof = 0.15  # RMW  
        w_inv  = 0.10  # CMA (conservatism)
        w_size = 0.10  # SMB (size)
        w_risk = 0.10  # Beta (market risk, baja beta premium — Frazzini 2014)
        w_mom  = 0.40  # Momentum multifractal Domenec
        
        # Verificar suma de pesos
        total_pesos = w_val + w_prof + w_inv + w_size + w_risk + w_mom
        assert abs(total_pesos - 1.0) < 1e-9, f"Pesos no suman 1: {total_pesos}"
        
        df['FF_Score'] = (
            (w_val  * df['Z_Value']) +
            (w_prof * df['Z_Prof']) +
            (w_inv  * (-1 * df['Z_Inv_Capped'])) +
            (w_size * df['Z_Size_FF'].fillna(0)) +
            (w_risk * df['Z_Risk_FF'].fillna(0))
        )
        
        df['Final_Score'] = df['FF_Score'] + (w_mom * df['Z_Mom'])
        
        # Penalización Gatekeeper extra para empresas con pérdidas
        df.loc[mask_loss, 'Final_Score'] -= 3.0
        
        logger.info(
            f"Scores calculados: {len(df)} activos | "
            f"FF_Score min={df['FF_Score'].min():.2f} max={df['FF_Score'].max():.2f}"
        )
        return df


# ==============================================================================
# 4. FUNCIÓN DE EJECUCIÓN DEL SCREENER
# ==============================================================================

def run_screener(filename, modo, output_name, fuente='yfinance'):
    """
    Ejecuta el screener para una lista de tickers.

    Args:
        filename:    Ruta al archivo de texto con los tickers (uno por línea o separados por coma).
        modo:        'global_sec', 'global_intl' o 'argentina'.
        output_name: Ruta del archivo Excel de salida.
        fuente:      Fuente de datos ('yfinance' o 'sec').
    """
    print(f"\n>>> PROCESANDO LISTA: {modo.upper()} ({filename})")
    try:
        with open(filename, 'r') as f:
            content = f.read()
            tickers = [t.strip().upper() for t in content.replace('\n', ',').split(',') if t.strip()]
    except FileNotFoundError:
        print(f"Error: {filename} no encontrado.")
        return

    calculadora = FamaFrenchCalculator(tickers, modo=modo, fuente=fuente)
    calculadora.fetch_data()
    df_results = calculadora.calculate_scores()
    
    if df_results.empty:
        print(f"No se obtuvieron resultados para {modo}.")
        return

    df_results = df_results.sort_values(by='Final_Score', ascending=False)
    
    # Guardar Excel
    df_results.to_excel(output_name, index=False)
    print(f"\nRanking {modo} guardado en: {output_name}")
    
    # Top 5 Preview
    cols_show = [
        'Ticker', 'Sector', 'Final_Score', 'FF_Score',
        'Profitability', 'Asset_Growth', 'Beta', 'Raw_Mom_Score'
    ]
    print(f"\nTOP 5 {modo.upper()}")
    cols_disponibles = [c for c in cols_show if c in df_results.columns]
    print(df_results[cols_disponibles].head().to_string())


def main():
    parser = argparse.ArgumentParser(description='Screener Fundamental Fama-French 6')
    parser.add_argument('--modo', choices=['all', 'sec', 'global', 'arg'], default='all')
    args = parser.parse_args()
    
    if args.modo in ('all', 'sec'):
        # 1. Ranking SEC (EE.UU. — Fuente Oficial)
        run_screener('config/ticker_sec.txt', 'global_sec', 'data/processed/Ranking_Global_SEC_Top.xlsx', fuente='sec')

    if args.modo in ('all', 'global'):
        # 2. Ranking Global (Resto del Mundo — Fuente YFinance)
        run_screener('config/ticker_global.txt', 'global_intl', 'data/processed/Ranking_Global_Intl_Top.xlsx', fuente='yfinance')
    
    if args.modo in ('all', 'arg'):
        # 3. Ranking Argentina (Local — Fuente YFinance + CCL)
        run_screener('config/ticker_arg.txt', 'argentina', 'data/processed/Ranking_Argentina_Top.xlsx', fuente='yfinance')
    
    print("\n" + "="*50)
    print("PROCESO FINALIZADO. REVISE LOS ARCHIVOS OUTPUT.")


if __name__ == "__main__":
    main()
