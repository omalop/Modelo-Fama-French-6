import yfinance as yf
import pandas as pd
import numpy as np
import logging
import argparse
import warnings
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
warnings.filterwarnings("ignore")

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
# 2. CALCULADORA FAMA-FRENCH + MOMENTUM
# ==============================================================================

class FamaFrenchCalculator:
    """
    Calculadora Multifactorial:
    - Value (HML)
    - Profitability (RMW) con Gatekeeper
    - Investment (CMA) con Cap de Destrucción
    - Momentum (WML) Multifractal (Domenec)
    """
    
    def __init__(self, tickers, mode='global'):
        self.tickers = tickers
        self.mode = mode
        self.data_store = []
        
        # Configuración de Temporalidades para Momentum
        # Intervalo yfinance, Periodo a descargar
        self.timeframes = {
            '3mo': {'interval': '3mo', 'period': 'max'}, # Trimestral (Trend Macro) - '3mo' support in yf vary, 1mo and resample is safer but lets try 3mo or 1mo
            # yfinance NO soporta '3mo' nativo confiable en histórico largo a veces. 
            # Mejor usaremos 1mo y haremos la lógica de "Largo Plazo" con 1mo.
            # User pidió: "descargar ticker con temporalidad de 3 meses".
            # Si yf falla, usaremos 1mo.
            '1mo': {'interval': '1mo', 'period': '5y'},  # Mensual (Trend Principal)
            '1wk': {'interval': '1wk', 'period': '2y'},  # Semanal (Trend Secundaria)
            '1d':  {'interval': '1d',  'period': '1y'}   # Diario (Ejecución)
        }
    
    def fetch_data(self):
        """Descarga fundamentales y técnicos multicapa."""
        logger.info(f"Iniciando análisis ({self.mode.upper()}) para {len(self.tickers)} activos...")
        
        total = len(self.tickers)
        for i, ticker in enumerate(self.tickers):
            print(f"[{i+1}/{total}] Procesando {ticker}...", end='\r')
            try:
                t = yf.Ticker(ticker)
                
                # --- A. FUNDAMENTALES (Fama-French Classic Fix) ---
                try:
                    info = t.info
                    bs = t.balance_sheet
                    fin = t.financials
                    
                    if bs.empty or fin.empty:
                        logger.warning(f"{ticker}: Sin datos fundamentales. Omitiendo.")
                        continue

                    # Filtro Diviza (Global Mode)
                    if self.mode == 'global':
                        fc = info.get('financialCurrency')
                        c = info.get('currency')
                        if fc and c and fc != c:
                            # Permitir si es consistente USD/USD, sino warning
                            continue
                            
                    # 1. VALUE (Book-to-Market)
                    # Intentar varias claves para Equity
                    equity_keys = ['Total Stockholder Equity', 'Total Equity Gross Minority Interest', 'Stockholders Equity']
                    book_value = None
                    for k in equity_keys:
                        if k in bs.index:
                            book_value = bs.loc[k].iloc[0]
                            break
                    
                    if book_value is None: continue
                    
                    mkt_cap = info.get('marketCap')
                    if not mkt_cap:
                        price = info.get('currentPrice') or info.get('regularMarketPreviousClose')
                        shares = info.get('sharesOutstanding')
                        if price and shares: mkt_cap = price * shares
                    
                    if not mkt_cap: continue
                    
                    bm_ratio = book_value / mkt_cap
                    
                    # 2. PROFITABILITY (Operating Profitability)
                    # Op Income / Book Equity
                    op_income = 0
                    if 'Operating Income' in fin.index:
                        op_income = fin.loc['Operating Income'].iloc[0]
                    elif 'Ebit' in fin.index: # Approx
                        op_income = fin.loc['Ebit'].iloc[0]
                        
                    profitability = op_income / book_value if book_value != 0 else np.nan
                    
                    # 3. INVESTMENT (Asset Growth)
                    # (At - At-1) / At-1
                    asset_growth = np.nan
                    if 'Total Assets' in bs.index:
                        assets = bs.loc['Total Assets']
                        if len(assets) >= 2:
                            at = assets.iloc[0]
                            at1 = assets.iloc[1]
                            asset_growth = (at - at1) / at1

                except Exception as eval_e:
                    logger.debug(f"{ticker}: Error en fundamentales: {eval_e}")
                    continue

                # --- B. MOMENTUM MULTIFRACTAL (Domenec) ---
                # Descarga independiente por TF
                mom_scores = []
                dispersions = []
                
                # Vamos a usar 3 temporalidades robustas: 1mo (Mensual/Trimestral proxy), 1wk, 1d
                # yfinance '3mo' interval is valid? Documentation says: 1m,2m,5m,15m,30m,60m,90m,1h,1d,5d,1wk,1mo,3mo
                # Probemos 3mo. Si falla, fallback.
                
                tfs_to_process = ['3mo', '1mo', '1wk', '1d']
                
                tf_status = {}
                
                for tf_name in tfs_to_process:
                    try:
                        cfg = self.timeframes.get(tf_name, {'interval': '1d', 'period': '1y'})
                        # Descarga
                        hist = t.history(period=cfg['period'], interval=cfg['interval'], auto_adjust=True)
                        
                        if hist.empty:
                            tf_status[tf_name] = {'status': 0, 'disp': 0}
                            continue
                            
                        # Calcular Status Domenec
                        status_val = get_domenec_status(hist)
                        disp_val = calculate_dispersion_sma34(hist)
                        
                        tf_status[tf_name] = {'status': status_val, 'disp': disp_val}
                        
                    except Exception as e_tf:
                        logger.debug(f"{ticker}: Error TF {tf_name}: {e_tf}")
                        tf_status[tf_name] = {'status': 0, 'disp': 0}

                # Guardar Datos
                self.data_store.append({
                    'Ticker': ticker,
                    'Sector': info.get('sector', 'Unknown'),
                    'MarketCap': mkt_cap,
                    
                    # FF Factors
                    'Book_to_Market': bm_ratio,
                    'Profitability': profitability,
                    'Asset_Growth': asset_growth,
                    
                    # Momentum Raw Data
                    'Mom_Status_3M': tf_status.get('3mo', {}).get('status', 0),
                    'Mom_Status_1M': tf_status.get('1mo', {}).get('status', 0),
                    'Mom_Status_1W': tf_status.get('1wk', {}).get('status', 0),
                    'Mom_Status_1D': tf_status.get('1d', {}).get('status', 0),
                    'Dispersion_1D': tf_status.get('1d', {}).get('disp', 0.0)
                })
                
            except Exception as e:
                logger.error(f"Error fatal procesando {ticker}: {e}")

    def calculate_scores(self):
        """Calcula Scores Finales usando lógica Fama-French Corregida + Domenec Refinado."""
        df = pd.DataFrame(self.data_store)
        if df.empty: return df
        
        # Limpieza básica
        cols_ff = ['Book_to_Market', 'Profitability', 'Asset_Growth']
        df[cols_ff] = df[cols_ff].apply(pd.to_numeric, errors='coerce')
        df.dropna(subset=cols_ff, inplace=True)
        
        # --- 1. NORMALIZACIÓN ROBUSTA (Mediana/IQR) ---
        # Evita que outliers (ej. PCAR3 -50% growth) distorsionen el score
        def robust_zscore(x):
            median = x.median()
            iqr = x.quantile(0.75) - x.quantile(0.25)
            if iqr == 0: iqr = x.abs().mean() # Fallback
            if iqr == 0: return np.zeros_like(x)
            return (x - median) / iqr

        # Agrupar por Sector (o Global si hay pocos datos)
        # Usaremos Global si el dataset es pequeño (<10 por sector), sino Sectorial.
        # Para robustez en screener general, Sectorial suele ser mejor.
        
        for col in cols_ff:
            z_col_name = f'Z_{col}'
            # Transformación robusta por sector
            df[z_col_name] = df.groupby('Sector')[col].transform(robust_zscore)
            # Rellenar NaNs con 0 (mediana)
            df[z_col_name] = df[z_col_name].fillna(0)
            
        # Renombrar para claridad interna
        df['Z_Value'] = df['Z_Book_to_Market']
        df['Z_Prof'] = df['Z_Profitability']
        df['Z_Inv'] = df['Z_Asset_Growth']
        
        # --- 2. LOGICA FAMA-FRENCH CORREGIDA (Forensic Fix) ---
        
        # A. CMA CAP (Tope a la destrucción de activos)
        # Invertimos Z_Inv porque "Menor crecimiento = Mejor" (Conservador)
        # PERO, si Z_Inv es muy negativo (< -1.0), no damos más puntos extra.
        # score_inv = -1 * max(Z_Inv, -1.0)
        # Así, si Z_Inv es -3.0 (destrucción masiva), tomamos -1.0. Score = -(-1.0) = +1.0 (Bueno, pero no infinito)
        # Si Z_Inv es 2.0 (Crecimiento agresivo), tomamos 2.0. Score = -2.0 (Malo).
        df['Z_Inv_Capped'] = df['Z_Inv'].clip(lower=-1.0)
        
        # B. RMW GATEKEEPER (Penalización por pérdidas)
        # Si Profitability < 0, penalizamos fuertemente.
        # Creamos una mascara
        mask_loss = df['Profitability'] < 0
        
        # --- 3. SCORING MOMENTUM MULTIFRACTAL (Domenec) ---
        # Sumamos los status (0-5) de los 4 TFs.
        # Max score ideal: 5+5+5+5 = 20. Min: 0.
        # Queremos alineación.
        
        df['Raw_Mom_Score'] = (
            (df['Mom_Status_3M'] * 1.5) +  # Mayor peso a macro
            (df['Mom_Status_1M'] * 1.5) +
            (df['Mom_Status_1W'] * 1.0) +
            (df['Mom_Status_1D'] * 1.0)
        )
        
        # Penalización por Agotamiento en Diario (Dispersión Extrema)
        # Calculamos percentil de dispersión
        df['Pct_Dispersion'] = df['Dispersion_1D'].rank(pct=True)
        # Si dispersión > 95% y Status es alcista (>=3), penalizar (posible techo)
        mask_exhaustion = (df['Pct_Dispersion'] > 0.95) & (df['Mom_Status_1D'] >= 3)
        df.loc[mask_exhaustion, 'Raw_Mom_Score'] -= 5.0 # Penalización fuerte
        
        # Normalizar Momentum Score a escala Z (aprox)
        df['Z_Mom'] = robust_zscore(df['Raw_Mom_Score'])

        # --- CORRECCIÓN ESTADÍSTICA (WINSORIZATION & GATEKEEPER) ---
        # 1. Gatekeeper Absoluto para Rentabilidad (Corrección ALAB)
        # Aplicar a TODAS las columnas de Z-Score de rentabilidad para consistencia
        cols_prof = ['Z_Profitability', 'Z_Prof']
        for c in cols_prof:
            if c in df.columns:
                 df.loc[mask_loss, c] = -3.0

        # 2. Winsorization (Recorte de Outliers) - Corrección MA
        # Aplicar a TODAS las columnas Z (largas y cortas) para que el Excel sea consistente
        all_z_cols = [
            'Z_Value', 'Z_Prof', 'Z_Inv', 'Z_Mom',
            'Z_Book_to_Market', 'Z_Profitability', 'Z_Asset_Growth'
        ]
        
        for c in all_z_cols:
            if c in df.columns:
                df[c] = df[c].clip(lower=-3.0, upper=3.0)

        # 3. Recalcular Z_Inv_Capped (sobre el Z_Inv ya recortado)
        # El tope inferior de -1.0 para Investment sigue aplicando (Prudencia).
        df['Z_Inv_Capped'] = df['Z_Inv'].clip(lower=-1.0)
        
        # --- 4. SCORE FINAL ---
        # Pesos: Value 0.25, Quality 0.35, Investment 0.20, Momentum 0.20
        # Damos más peso a Quality para evitar trampas.
        
        w_val = 0.25
        w_prof = 0.35
        w_inv = 0.20
        w_mom = 0.20
        
        # Score Base
        df['Final_Score'] = (
            (w_val * df['Z_Value']) +
            (w_prof * df['Z_Prof']) +     # RMW (Profitability)
            (w_inv * (-1 * df['Z_Inv_Capped'])) + # CMA (Investment invertido y topeado)
            (w_mom * df['Z_Mom'])
        )
        
        # Aplicar Penalización Gatekeeper (RMW) Extra
        # Mantenemos la patada extra (-3.0) para asegurar que salgan del radar.
        df.loc[mask_loss, 'Final_Score'] -= 3.0
        
        return df

def run_screener(filename, mode, output_name):
    print(f"\n>>> PROCESANDO LISTA: {mode.upper()} ({filename})")
    try:
        with open(filename, 'r') as f:
            content = f.read()
            tickers = [t.strip().upper() for t in content.replace('\n', ',').split(',') if t.strip()]
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
    print(f"\nRanking {mode} guardado en: {output_name}")
    
    # Top 5 Preview
    cols_show = ['Ticker', 'Sector', 'Final_Score', 'Profitability', 'Asset_Growth', 'Raw_Mom_Score']
    print(f"\nTOP 5 {mode.upper()}")
    if all(c in df_results.columns for c in cols_show):
        print(df_results[cols_show].head().to_string())
    else:
        print(df_results.head().to_string())

def main():
    # 1. Ranking Global
    run_screener('config/ticker.txt', 'global', 'data/processed/Ranking_Global_Top.xlsx')
    
    # 2. Ranking Argentina
    run_screener('config/ticker_arg.txt', 'argentina', 'data/processed/Ranking_Argentina_Top.xlsx')
    
    print("\n" + "="*50)
    print("PROCESO FINALIZADO. REVISE LOS ARCHIVOS OUTPUT.")

if __name__ == "__main__":
    main()
