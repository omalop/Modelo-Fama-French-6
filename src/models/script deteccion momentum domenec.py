import yfinance as yf
import pandas as pd
import numpy as np
import logging
import warnings
from datetime import datetime

# Configuración
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
warnings.filterwarnings("ignore")

# ==============================================================================
# 1. DEFINICIÓN DE FUNCIONES TÉCNICAS (Traducción de Pine Script)
# ==============================================================================

def calculate_rma(series, length):
    """Calcula la Wilder's Moving Average (RMA) simular a ta.rma de Pine."""
    return series.ewm(alpha=1/length, min_periods=length, adjust=False).mean()

def calculate_adx(high, low, close, period=14):
    """Calcula el ADX usando la metodología de Wilder."""
    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional Movement
    up = high - high.shift(1)
    down = low.shift(1) - low

    pos_dm = np.where((up > down) & (up > 0), up, 0.0)
    neg_dm = np.where((down > up) & (down > 0), down, 0.0)

    pos_dm = pd.Series(pos_dm, index=high.index)
    neg_dm = pd.Series(neg_dm, index=high.index)

    # Smoothing (RMA)
    tr_smooth = calculate_rma(tr, period)
    pos_dm_smooth = calculate_rma(pos_dm, period)
    neg_dm_smooth = calculate_rma(neg_dm, period)

    # DI+ and DI-
    # Evitar división por cero
    tr_smooth = tr_smooth.replace(0, np.nan)
    pos_di = 100 * (pos_dm_smooth / tr_smooth)
    neg_di = 100 * (neg_dm_smooth / tr_smooth)

    # DX and ADX
    dx = 100 * (abs(pos_di - neg_di) / (pos_di + neg_di))
    adx = calculate_rma(dx, period)

    return adx

def calculate_wpr(high, low, close, period=14):
    """Calcula Williams %R."""
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    wpr = -100 * ((highest_high - close) / (highest_high - lowest_low))
    return wpr

def apply_indicators(df):
    """
    Aplica todos los indicadores solicitados (4IN1 y Control Total) al DataFrame.
    """
    if df.empty: return df

    # --- 1. GENIAL LINE (SMA 34) ---
    df['Genial_Line'] = df['Close'].rolling(window=34).mean()

    # --- 2. ZONA DE CORRECCION (EMA 8 vs Wilder 8) ---
    df['EMA_8'] = df['Close'].ewm(span=8, adjust=False).mean()
    df['Wilder_8'] = calculate_rma(df['Close'], 8)
    # Condición: True si EMA8 > Wilder8 (Zona Alcista/Verde), False si Roja
    df['Zona_Correccion_Alcista'] = df['EMA_8'] > df['Wilder_8']

    # --- 3. TUNEL DOMENEC (EMAs) ---
    emas = [123, 188, 416, 618, 882, 1223]
    for p in emas:
        df[f'EMA_{p}'] = df['Close'].ewm(span=p, adjust=False).mean()

    # --- 4. CONTROL TOTAL ---
    # Parámetros inputs
    pDir = 40  # Williams %R
    pForce = 7 # ADX

    df['WPR'] = calculate_wpr(df['High'], df['Low'], df['Close'], pDir)
    df['ADX'] = calculate_adx(df['High'], df['Low'], df['Close'], pForce)

    # Lógica de Estados (Traducción del bloque if/else de colores)
    # Necesitamos valores previos
    df['WPR_Prev'] = df['WPR'].shift(1)
    df['ADX_Prev'] = df['ADX'].shift(1)

    df['WPR_Up'] = df['WPR'] > df['WPR_Prev']
    df['WPR_Down'] = df['WPR'] < df['WPR_Prev']
    df['Sig_Up'] = df['ADX'] >= df['ADX_Prev']
    df['Sig_Down'] = df['ADX'] < df['ADX_Prev']

    upper_band = -25

    # Definimos una función para aplicar fila por fila o vectorizada
    # 0: Neutral, 1: Correction Red, 2: Yellow, 3: Navy, 4: DarkGreen, 5: Green
    conditions = [
        (df['WPR'] > -50) & (df['WPR_Down']) & (df['Sig_Up']),   # Rojo: Corrección c/ fuerza
        (df['WPR'] > -50) & (df['WPR_Down']) & (df['Sig_Down']), # Amarillo: Sin fuerza
        (df['WPR'] > -50) & (df['WPR_Up']) & (df['Sig_Down']),   # Navy: Retroceso leve
        (df['WPR'] > -50) & (df['WPR_Up']) & (df['Sig_Up']) & (df['WPR'] > upper_band), # DarkGreen: Impulso Fuerte
        (df['WPR'] > -50) & (df['WPR_Up']) & (df['Sig_Up']) & (df['WPR'] <= upper_band) # Green: Impulso Medio
    ]
    choices = ['Correccion Fuerte (Rojo)', 'Sin Fuerza (Amarillo)', 'Pullback (Azul)', 'Impulso Fuerte (Verde Osc)', 'Impulso Medio (Verde)']

    # Nota: El script original de pine script estaba cortado para la parte bajista (WPR < -50).
    # Asignaremos "Zona Bajista" por defecto si no cumple las de arriba.
    df['Status_Control'] = np.select(conditions, choices, default='Zona Bajista / Neutral')

    # --- 5. DISPERSIÓN ---
    # Diferencia porcentual entre Precio y SMA 34
    df['Dispersion_SMA34'] = ((df['Close'] - df['Genial_Line']) / df['Genial_Line']) * 100

    return df

# ==============================================================================
# 2. LÓGICA DE DESCARGA Y PROCESAMIENTO
# ==============================================================================

def get_data_for_timeframe(tickers_list, interval, period, ccl_ref_tickers):
    """
    Descarga, ajusta por CCL (si aplica) y devuelve un diccionario de DataFrames por ticker.
    """
    print(f"--- Procesando intervalo: {interval} ---")

    # Descargar todo junto
    tickers_to_download = list(set(tickers_list + ccl_ref_tickers))
    # yfinance maneja mal la descarga masiva intradía a veces, pero intentaremos bulk
    try:
        data = yf.download(tickers_to_download, period=period, interval=interval, group_by='ticker', auto_adjust=True, progress=True)
    except Exception as e:
        print(f"Error descargando {interval}: {e}")
        return {}

    processed_data = {}

    # Calcular CCL si es necesario y posible
    ccl_factor = None
    t_local = ccl_ref_tickers[0] # GGAL.BA
    t_adr = ccl_ref_tickers[1]   # GGAL

    has_ccl = False
    if t_local in data.columns.levels[0] and t_adr in data.columns.levels[0]:
        try:
            # Extracción segura
            local_c = data[t_local]['Close']
            adr_c = data[t_adr]['Close']

            # Limpieza básica
            df_ccl = pd.concat([local_c, adr_c], axis=1).dropna()
            if not df_ccl.empty:
                # Fórmula CCL
                ccl_series = (df_ccl.iloc[:, 0] * 10) / df_ccl.iloc[:, 1]
                has_ccl = True
        except Exception as e:
            logging.warning(f"No se pudo calcular CCL para {interval}: {e}")

    # Procesar cada ticker individualmente
    for ticker in tickers_list:
        try:
            if ticker not in data.columns.levels[0]:
                continue

            df_ticker = data[ticker].copy()
            if df_ticker.empty: continue

            # Limpiar NAs
            df_ticker = df_ticker.dropna(subset=['Close'])

            # Dolarizar si es .BA y tenemos CCL
            if ticker.endswith('.BA') and has_ccl:
                # Alinear indices
                common_idx = df_ticker.index.intersection(ccl_series.index)
                df_ticker = df_ticker.loc[common_idx]
                ccl_aligned = ccl_series.loc[common_idx]

                # Ajustar precio
                df_ticker['Close'] = df_ticker['Close'] / ccl_aligned
                df_ticker['Open'] = df_ticker['Open'] / ccl_aligned
                df_ticker['High'] = df_ticker['High'] / ccl_aligned
                df_ticker['Low'] = df_ticker['Low'] / ccl_aligned

            # Aplicar Indicadores
            df_ticker = apply_indicators(df_ticker)

            # Guardar solo si tiene datos suficientes para los cálculos
            if not df_ticker.empty:
                processed_data[ticker] = df_ticker

        except Exception as e:
            logging.error(f"Error procesando {ticker} en {interval}: {e}")

    return processed_data

# ==============================================================================
# 3. EJECUCIÓN PRINCIPAL
# ==============================================================================

if __name__ == "__main__":
    print("--- SCREENER MULTI-TIMEFRAME CON INDICADORES ---")
    # Leer tickers desde ticker.txt
    try:
        with open('ticker.txt', 'r') as f:
            content = f.read()
            # Asumiendo que pueden estar separados por comas o saltos de línea
            tickers = [t.strip().upper() for t in content.replace('\n', ',').split(',') if t.strip()]
        print(f"Cargados {len(tickers)} tickers desde ticker.txt")
    except FileNotFoundError:
        print("Error: No se encontró el archivo ticker.txt")
        tickers = []
    except Exception as e:
        print(f"Error leyendo ticker.txt: {e}")
        tickers = []

    if not tickers:
        tickers_input = input("No se pudieron cargar tickers. Ingrese los tickers manualmente (ej: GGAL, YPF): ")
        tickers = [t.strip().upper() for t in tickers_input.split(',') if t.strip()]

    # Referencias para CCL
    ccl_ref = ['GGAL.BA', 'GGAL']

    # Definición de marcos temporales a analizar
    # (Nombre, Intervalo yfinance, Periodo máximo para ese intervalo)
    timeframes_config = [
        ('Semanal', '1wk', '5y'),
        ('Diario', '1d', '2y'),
        ('4 Horas', '1h', '730d'), # Usamos 1h para simular 4h o pedimos 1h directo. yf a veces falla con '4h'.
                                   # Nota: Si pides '4h' directo a yfinance, el max period es ~2 años.
        ('1 Hora', '1h', '730d')
    ]

    # Ajuste: El usuario pidió explícitamente 4H. yfinance soporta '60m' y '1h'.
    # '4h' no siempre es estable en yfinance gratuito para todos los mercados,
    # pero intentaremos usar '1h' y hacer resample si fuera necesario,
    # o pedir '1h' para ambos casos como datos separados.
    # Para simplificar y robustez, descargaremos:
    # 1. Semanal
    # 2. Diario
    # 3. 1 Hora
    # Y para el de 4 horas, haremos un resample del de 1 hora si es posible, o pediremos '1h' y lo trataremos como 'intradia'.
    # Voy a configurar la petición estricta que pidió el usuario:

    tf_params = {
        'Semanal': {'interval': '1wk', 'period': '5y'},
        'Diario':  {'interval': '1d',  'period': '2y'},
        '4 Horas': {'interval': '1h',  'period': '730d'}, # Truco: Bajamos 1H y haremos resample x4
        '1 Hora':  {'interval': '1h',  'period': '730d'}
    }

    results_summary = {}

    for tf_name, params in tf_params.items():
        print(f"\nGenerando datos para: {tf_name}...")

        # Obtener datos crudos procesados
        dict_dfs = get_data_for_timeframe(tickers, params['interval'], params['period'], ccl_ref)

        screener_rows = []

        for ticker, df in dict_dfs.items():
            if df.empty: continue

            # Si es 4 Horas, necesitamos hacer resample del dataframe de 1 Hora
            if tf_name == '4 Horas':
                # Resamplear lógica OHLC
                logic = {'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'}
                try:
                    df = df.resample('4h').apply(logic).dropna()
                    # Re-aplicar indicadores sobre la data resampleada
                    df = apply_indicators(df)
                except Exception as e:
                    continue

            # Tomar la última vela cerrada (iloc[-1])
            last_row = df.iloc[-1]

            # Construir fila para el resumen
            screener_rows.append({
                'Ticker': ticker,
                'Precio': last_row['Close'],
                'Genial_Line (34)': last_row['Genial_Line'],
                'Zona_Correccion': 'Alcista' if last_row['Zona_Correccion_Alcista'] else 'Bajista',
                'Status_Control': last_row['Status_Control'],
                'Dispersion_SMA34': last_row['Dispersion_SMA34']
            })

        if not screener_rows:
            print(f"No se generaron datos para {tf_name}.")
            continue

        # Crear DataFrame del Screener para este Timeframe
        df_screener = pd.DataFrame(screener_rows)

        # --- CÁLCULO DE PERCENTILES DE DISPERSIÓN ---
        # Rankear la columna Dispersion_SMA34 de 0 a 100
        if len(df_screener) > 1:
            df_screener['Rango_Percentil'] = df_screener['Dispersion_SMA34'].rank(pct=True) * 100
        else:
            df_screener['Rango_Percentil'] = 100.0 # Si es solo uno

        # Formatear para visualización
        df_screener = df_screener.sort_values(by='Dispersion_SMA34', ascending=False).reset_index(drop=True)

        # Guardar en diccionario global
        results_summary[tf_name] = df_screener

        print(f"--- RESULTADOS TOP 5 PARA {tf_name.upper()} ---")
        print(df_screener[['Ticker', 'Precio', 'Status_Control', 'Dispersion_SMA34', 'Rango_Percentil']].head())

    # ==============================================================================
    # 4. EXPORTACIÓN (OPCIONAL) O VISUALIZACIÓN FINAL
    # ==============================================================================
    print("\n" + "="*50)
    print("RESUMEN FINAL COMPLETO")
    print("="*50)

    for tf, df in results_summary.items():
        print(f"\n>>> TABLA: {tf}")
        # Mostrar tabla bonita
        print(df[['Ticker', 'Precio', 'Zona_Correccion', 'Status_Control', 'Dispersion_SMA34', 'Rango_Percentil']].to_string(index=False))

    # Guardar a Excel
    try:
        output_file = 'Screener_Output.xlsx'
        with pd.ExcelWriter(output_file) as writer:
            for tf, df in results_summary.items():
                df.to_excel(writer, sheet_name=tf, index=False)
        print(f"\nGuardado exitosamente en {output_file}")
    except Exception as e:
        print(f"\nError al guardar Excel: {e}")