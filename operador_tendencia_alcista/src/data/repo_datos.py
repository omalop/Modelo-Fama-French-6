import yfinance as yf
import pandas as pd
import logging
import sys
import os

# Ajuste de path para importar módulos internos
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from config.settings import TIMEFRAMES
from src.data.validadores import ValidadorDataFrame

logger = logging.getLogger(__name__)

class RepositorioDatos:
    def __init__(self):
        # Parametros de descarga optimizados para cada timeframe
        self.params_descarga = {
            'trimestral': {'interval': '3mo', 'period': 'max'}, # yfinance puede fallar con 3mo, fallback en logica
            'mensual': {'interval': '1mo', 'period': 'max'},
            'semanal': {'interval': '1wk', 'period': 'max'},
            'diario': {'interval': '1d', 'period': '10y'},
            'intradia': {'interval': '1h', 'period': '730d'} # Max 730d para horario
        }

    def obtener_datos(self, ticker: str, timeframe: str) -> pd.DataFrame:
        """
        Descarga datos OHLCV para un ticker y timeframe específicos.
        Aplica validaciones de integridad.
        """
        if timeframe not in self.params_descarga:
            raise ValueError(f"Timeframe no soportado: {timeframe}. Use {list(self.params_descarga.keys())}")

        params = self.params_descarga[timeframe]
        interval = params['interval']
        period = params['period']

        logger.info(f"Descargando {ticker} [{timeframe}] Interval: {interval} Period: {period}")

        try:
            # Descarga directa de yfinance
            df = yf.download(ticker, period=period, interval=interval, progress=False, auto_adjust=True)
            
            if df.empty:
                logger.warning(f"{ticker}: Datos vacíos para {timeframe}")
                return pd.DataFrame()

            # yfinance devuelve MultiIndex en columnas si se descarga 1 solo ticker a veces, o flat.
            # Asegurar formato plano: Open, High, Low, Close, Volume
            if isinstance(df.columns, pd.MultiIndex):
                # Si es multiindex (Price, Ticker), droppeamos nivel Ticker
                df.columns = df.columns.get_level_values(0)

            # Validar integridad
            df = ValidadorDataFrame.validar(df)
            
            # Resample manual si 3mo falla (yfinance a veces no trae 3mo valido)
            # Logica de fallback: Si pedimos 'trimestral' y el index no parece trimestral (diff < 60 dias promedio)
            if timeframe == 'trimestral':
                # Chequeo heurístico
                if len(df) > 1:
                    avg_diff = df.index.to_series().diff().mean().days
                    if avg_diff < 80: # Menos de ~3 meses
                        logger.warning(f"yfinance devolvió datos con frecuencia {avg_diff}d para '3mo'. Re-muestrando desde Mensual.")
                        # Descargar mensual y re-muestrear
                        df_mo = self.obtener_datos(ticker, 'mensual')
                        if not df_mo.empty:
                            df = df_mo.resample('QE').agg({
                                'Open': 'first', 'High': 'max', 'Low': 'min', 'Close': 'last', 'Volume': 'sum'
                            }).dropna()

            logger.info(f"{ticker} [{timeframe}]: {len(df)} registros validados.")
            return df

        except Exception as e:
            logger.error(f"Error descargando {ticker} [{timeframe}]: {e}")
            return pd.DataFrame()

    def obtener_todo_multitemporal(self, ticker: str) -> dict:
        """
        Retorna diccionario con todos los timeframes para el ticker.
        """
        datos = {}
        for tf in self.params_descarga:
            datos[tf] = self.obtener_datos(ticker, tf)
        return datos
