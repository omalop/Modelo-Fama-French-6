import yfinance as yf
import pandas as pd
import logging
import sys
import os
import duckdb
from datetime import datetime

# Ajuste de path para importar módulos internos
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from config.settings import TIMEFRAMES
from src.data.validadores import ValidadorDataFrame

logger = logging.getLogger(__name__)

# Definir ruta de proyecto raíz y base de datos
ROOT_PROYECTO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
DB_PATH = os.path.join(ROOT_PROYECTO, 'data', 'market_data.duckdb')

class RepositorioDatos:
    def __init__(self):
        # Parametros de descarga optimizados para cada timeframe
        self.params_descarga = {
            'trimestral': {'interval': '3mo', 'period': 'max'},
            'mensual': {'interval': '1mo', 'period': 'max'},
            'semanal': {'interval': '1wk', 'period': 'max'},
            'diario': {'interval': '1d', 'period': '10y'},
            'intradia': {'interval': '1h', 'period': '730d'} 
        }

    def obtener_datos(self, ticker: str, timeframe: str) -> pd.DataFrame:
        """
        Obtiene datos OHLCV. Primero intenta desde DuckDB (solo para 'diario' o 'semanal' si disponible),
        luego yfinance como fallback.
        """
        if timeframe not in self.params_descarga:
            raise ValueError(f"Timeframe no soportado: {timeframe}. Use {list(self.params_descarga.keys())}")

        # 1. Intentar conexión directa a DuckDB (Evita problemas de import de DBManager)
        # Solo tiene sentido para datos diarios que son los que guardamos en la tabla 'prices'
        if timeframe == 'diario' and os.path.exists(DB_PATH):
            try:
                # Conexión temporal en modo lectura
                with duckdb.connect(DB_PATH, read_only=True) as conn:
                    # Verificar si existe la tabla
                    tablas = conn.execute("SHOW TABLES").df()
                    if not tablas.empty and 'prices' in tablas['name'].values:
                        query = f"SELECT * FROM prices WHERE ticker = '{ticker}' ORDER BY date"
                        df_local = conn.execute(query).df()
                        
                        if not df_local.empty:
                            logger.info(f"Cargados {len(df_local)} registros de DuckDB para {ticker}")
                            # Preparar formato para el operador
                            # La tabla tiene columnas en minúscula: date, open, high, low, close, volume
                            df_local['date'] = pd.to_datetime(df_local['date'])
                            df_local.set_index('date', inplace=True)
                            
                            # Renombrar a Capitalize para compatibilidad
                            mapa_cols = {
                                'open': 'Open', 'high': 'High', 'low': 'Low', 
                                'close': 'Close', 'volume': 'Volume'
                            }
                            df_local.rename(columns=mapa_cols, inplace=True)
                            
                            # Validar
                            df_local = ValidadorDataFrame.validar(df_local)
                            
                            # Asegurar timezone naive
                            if df_local.index.tz is not None:
                                df_local.index = df_local.index.tz_localize(None)
                                
                            return df_local
            except Exception as e:
                logger.debug(f"No se pudo leer de DuckDB para {ticker}: {e}")

        # 2. Fallback a yfinance
        params = self.params_descarga[timeframe]
        interval = params['interval']
        period = params['period']

        logger.info(f"Descargando {ticker} [{timeframe}] desde yfinance...")

        try:
            df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
            
            if df.empty:
                logger.warning(f"{ticker}: Datos vacíos de yfinance para {timeframe}")
                return pd.DataFrame()

            # Asegurar formato plano
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            # --- CORRECCIÓN CRÍTICA: ZONAS HORARIAS ---
            # Para evitar: TypeError: Cannot compare tz-naive and tz-aware datetime-like objects
            if df.index.tz is not None:
                df.index = df.index.tz_localize(None)

            # Validar integridad
            df = ValidadorDataFrame.validar(df)
            
            # Resample manual si 3mo falla
            if timeframe == 'trimestral':
                if len(df) > 1:
                    avg_diff = df.index.to_series().diff().mean().days
                    if avg_diff < 80:
                        logger.warning(f"Re-muestrando trimestral para {ticker} desde datos mensuales.")
                        df_mo = self.obtener_datos(ticker, 'mensual')
                        if not df_mo.empty:
                            df = df_mo.resample('QE').agg({
                                'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                            }).dropna()

            logger.info(f"{ticker} [{timeframe}]: {len(df)} registros validados.")
            return df

        except Exception as e:
            logger.error(f"Error obteniendo {ticker} [{timeframe}]: {e}")
            return pd.DataFrame()

    def obtener_todo_multitemporal(self, ticker: str) -> dict:
        """
        Retorna diccionario con todos los timeframes para el ticker.
        """
        datos = {}
        for tf in self.params_descarga:
            datos[tf] = self.obtener_datos(ticker, tf)
        return datos
