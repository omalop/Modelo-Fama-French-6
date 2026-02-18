import duckdb
import pandas as pd
import yfinance as yf
import logging
from datetime import datetime, timedelta
import os
import shutil
import requests

# Configuración de User-Agent para evitar bloqueos (SEC/Yahoo)
USER_AGENT_HEADERS = {
    'User-Agent': 'Omar Lopez (omlop90@gmail.com)',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive'
}
import sys

# Importar configuración y módulos
try:
    from config.settings import SEC_USER_AGENT
    from src.data.sec_downloader import SECDownloader
except ImportError:
    # Fallback si se ejecuta como script suelto (no recomendado, pero robustness)
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))
    from config.settings import SEC_USER_AGENT
    from src.data.sec_downloader import SECDownloader

# Configuración de Logging
logger = logging.getLogger(__name__)

class DBManager:
    """
    Gestor de Base de Datos DuckDB para caché diario de datos financieros.
    
    Estrategia de Caché:
    - TTL (Time To Live): 24 horas.
    - Si la base de datos es más antigua que 24 horas, se trunca y se recarga todo.
    - Esto garantiza que los ajustes por splits/dividendos de yfinance se reflejen.
    """
    
    def __init__(self, db_path='data/market_data.duckdb'):
        self.db_path = db_path
        self._ensure_data_dir()
        
        # Configurar Session para yfinance
        self.session = requests.Session()
        self.session.headers.update(USER_AGENT_HEADERS)
        
        self.conn = duckdb.connect(self.db_path)
        self.initialize_schema()

    def _ensure_data_dir(self):
        """Asegura que el directorio data exista."""
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)

    def initialize_schema(self):
        """Crea las tablas necesarias si no existen."""
        # Tabla de Metadatos (Control de Caché)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata (
                key VARCHAR PRIMARY KEY,
                value VARCHAR,
                last_updated TIMESTAMP
            )
        """)
        
        # Tabla de Precios (OHLCV)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                ticker VARCHAR,
                date DATE,
                open DOUBLE,
                high DOUBLE,
                low DOUBLE,
                close DOUBLE,
                volume BIGINT,
                PRIMARY KEY (ticker, date)
            )
        """)
        
        # Tabla de Fundamentales (Balance Sheet, Financials)
        # Almacenamos en formato largo para flexibilidad
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS financials (
                ticker VARCHAR,
                report_date DATE,
                metric VARCHAR,
                value DOUBLE,
                type VARCHAR, -- 'BS' (Balance Sheet) o 'IS' (Income Statement)
                PRIMARY KEY (ticker, report_date, metric)
            )
        """)

        # Tabla de Metadatos de Tickers (Sector, Shares, Currency)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tickers_metadata (
                ticker VARCHAR PRIMARY KEY,
                sector VARCHAR,
                shares DOUBLE,
                currency VARCHAR,
                last_updated TIMESTAMP
            )
        """)

    def _is_cache_valid(self):
        """Verifica si el caché tiene menos de 24 horas."""
        try:
            result = self.conn.execute("SELECT last_updated FROM metadata WHERE key='market_data'").fetchone()
            if not result:
                return False
            
            last_updated = result[0]
            # DuckDB devuelve datetime, procedemos a comparar
            if (datetime.now() - last_updated) < timedelta(hours=24):
                return True
            return False
        except Exception as e:
            logger.warning(f"Error verificando caché: {e}")
            return False

    def _clear_cache(self):
        """Borra todos los datos para una recarga limpia."""
        logger.info("Invalidando caché (murió el TTL de 24h)...")
        # Borramos datos volátiles, pero podríamos conservar metadatos estáticos si quisiéramos.
        # Por simplicidad, refrescamos todo.
        self.conn.execute("DELETE FROM prices")
        self.conn.execute("DELETE FROM financials")
        self.conn.execute("DELETE FROM tickers_metadata")
        self.conn.execute("DELETE FROM metadata WHERE key='market_data'")

    def update_history(self, tickers: list, source: str = 'yfinance'):
        """
        Actualiza la base de datos.
        Args:
            tickers: Lista de tickers
            source: 'yfinance' o 'sec'
        """
        if self._is_cache_valid():
            logger.info("Caché válido (<24h). Usando datos locales.")
            return

        # Si llegamos acá, hay que actualizar
        self._clear_cache()
        
        # Deduplicar lista de tickers para evitar Primary Key Constraint Errors
        tickers = sorted(list(set(tickers)))
        
        logger.info(f"Actualizando caché para {len(tickers)} tickers únicos...")
        
        # 1. Descarga Masiva de Precios (Mucho más rápido con yfinance.download group)
        # Limitamos historial a lo necesario para Fama-French Screener (ej. 2-5 años)
        # Para backtest, quizás necesitemos más, pero el screener es el uso principal diario.
        # User pidió "descargar datos nuevos".
        # En estrategia "Borrar y Cargar", descargamos "max" o "5y" según necesidad.
        # Usaremos "5y" como default robusto para análisis de mediano plazo.
        
        try:
            # Descarga optimizada en batch
            logger.info("Descargando precios (Batch)...")
            # threads=False para evitar conflictos con curl_cffi/requests session en algunos entornos (ej. tests)
            df_prices = yf.download(tickers, period="5y", group_by='ticker', auto_adjust=True, threads=False, progress=False)
            
            # Procesar y guardar precios
            # El formato de yf.download con group_by='ticker' es MultiIndex (Ticker, OHLC) si hay >1 ticker
            # Ojo: si es 1 solo ticker, el formato es diferente.
            
            batch_data = []
            
            if len(tickers) == 1:
                ticker = tickers[0]
                df = df_prices
                # Reset index para tener Date como columna
                df = df.reset_index()
                for _, row in df.iterrows():
                    batch_data.append((
                        ticker, row['Date'], 
                        row['Open'], row['High'], row['Low'], row['Close'], row['Volume']
                    ))
            else:
                for ticker in tickers:
                    # Acceder al nivel superior del MultiIndex
                    if ticker not in df_prices.columns.get_level_values(0):
                        continue
                        
                    df = df_prices[ticker].copy()
                    df = df.dropna(how='all')
                    df = df.reset_index()
                    
                    for _, row in df.iterrows():
                        # Validar tipos básicos
                        if pd.isna(row['Close']): continue
                        
                        batch_data.append((
                            ticker, row['Date'], 
                            row['Open'], row['High'], row['Low'], row['Close'], row['Volume']
                        ))
            
            # Insertar Precios en Batch
            if batch_data:
                # Usamos INSERT OR IGNORE para que si un ticker viene duplicado o con fechas solapadas, no explote.
                self.conn.executemany(
                    "INSERT OR IGNORE INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
                    batch_data
                )
                logger.info(f"Insertados {len(batch_data)} registros de precios.")

            # 2. Descarga de Fundamentales y Metadatos
            logger.info(f"Descargando fundamentales (Fuente: {source.upper()})...")
            financials_data = []
            metadata_records = []
            
            if source == 'sec':
                # --- LOGICA SEC ---
                try:
                    downloader = SECDownloader(user_agent=SEC_USER_AGENT)
                    total = len(tickers)
                    for i, ticker in enumerate(tickers):
                        if i % 5 == 0: print(f"SEC Download {i}/{total} ({ticker})...", end='\r')
                        
                        # A. Metadatos básicos (desde yfinance para complementar)
                        # SEC no da sector/shares facilmente en companyfacts, usamos yf.Ticker.info rapido
                        # OJO: Si yf falla, poner default.
                        # OJO: Si yf falla, poner default.
                        try:
                            t_yf = yf.Ticker(ticker)
                            info = t_yf.info
                            sector = info.get('sector', 'Unknown')
                            shares = info.get('sharesOutstanding', 0)
                            currency = info.get('currency', 'USD')
                        except:
                            sector, shares, currency = 'Unknown', 0, 'USD'
                        
                        metadata_records.append((ticker, sector, shares, currency, datetime.now()))

                        # B. Fundamentales (SEC)
                        facts = downloader.get_company_facts(ticker)
                        if facts:
                            parsed = downloader.parse_facts(facts, ticker)
                            financials_data.extend(parsed)
                        else:
                            logger.warning(f"No facts for {ticker} from SEC")

                except Exception as e_sec:
                    logger.error(f"Error en bloque SEC: {e_sec}")

            else:
                # --- LOGICA YFINANCE (Legado) ---
                total = len(tickers)
                for i, ticker in enumerate(tickers):
                    if i % 10 == 0: print(f"YF Download {i}/{total} ({ticker})...", end='\r')
                    try:
                        t = yf.Ticker(ticker)
                        
                        # A. Metadatos (Info)
                        try:
                            info = t.info
                            sector = info.get('sector', 'Unknown')
                            shares = info.get('sharesOutstanding', 0)
                            currency = info.get('currency', 'USD')
                            metadata_records.append((ticker, sector, shares, currency, datetime.now()))
                        except Exception as e_meta:
                            logger.warning(f"{ticker}: Error en metadatos: {e_meta}")
                            metadata_records.append((ticker, 'Unknown', 0, 'USD', datetime.now()))

                        # B. Balance Sheet
                        bs = t.balance_sheet
                        if not bs.empty:
                            for date_col in bs.columns:
                                report_date = pd.to_datetime(date_col)
                                for metric, value in bs[date_col].items():
                                    if pd.notna(value):
                                        financials_data.append((ticker, report_date, metric, float(value), 'BS'))
                        
                        # C. Financials (Income Statement)
                        fin = t.financials
                        if not fin.empty:
                            for date_col in fin.columns:
                                report_date = pd.to_datetime(date_col)
                                for metric, value in fin[date_col].items():
                                    if pd.notna(value):
                                        financials_data.append((ticker, report_date, metric, float(value), 'IS'))
                                        
                    except Exception as e:
                        logger.debug(f"Error procesando {ticker}: {e}")
                        continue
            
            # Insertar Metadatos
            if metadata_records:
                self.conn.executemany(
                    "INSERT OR REPLACE INTO tickers_metadata VALUES (?, ?, ?, ?, ?)",
                    metadata_records
                )
                logger.info(f"Actualizados metadatos para {len(metadata_records)} tickers.")

            # Insertar Fundamentales
            if financials_data:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO financials VALUES (?, ?, ?, ?, ?)",
                    financials_data
                )
                logger.info(f"Insertados {len(financials_data)} registros fundamentales.")

            # Marcar como actualizado
            self.conn.execute(
                "INSERT INTO metadata (key, value, last_updated) VALUES ('market_data', 'full_load', current_timestamp)"
            )
            logger.info("Actualización de caché completada con éxito.")
            
        except Exception as e:
            logger.error(f"Error crítico actualizando DB: {e}")
            raise

    def get_price_history(self, tickers: list):
        """Retorna DataFrame de precios formato long."""
        tickers_str = "'" + "','".join(tickers) + "'"
        query = f"SELECT * FROM prices WHERE ticker IN ({tickers_str}) ORDER BY date"
        return self.conn.execute(query).df()
    
    def get_financials(self, tickers: list):
        """Retorna DataFrame de fundamentales para una lista de tickers."""
        tickers_str = "'" + "','".join(tickers) + "'"
        query = f"SELECT * FROM financials WHERE ticker IN ({tickers_str})"
        return self.conn.execute(query).df()
        
    def get_tickers_metadata(self, tickers: list):
        """Retorna DataFrame de metadatos estáticos."""
        tickers_str = "'" + "','".join(tickers) + "'"
        query = f"SELECT * FROM tickers_metadata WHERE ticker IN ({tickers_str})"
        return self.conn.execute(query).df()

    def close(self):
        self.conn.close()
