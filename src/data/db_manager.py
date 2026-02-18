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

    def _get_outdated_tickers(self, tickers):
        """Identifica qué tickers necesitan actualización (faltantes o >24h)."""
        if not tickers: return []
        
        # 1. Obtener tickers que ya existen y su timestamp
        tickers_str = "'" + "','".join(tickers) + "'"
        try:
            query = f"SELECT ticker, last_updated FROM tickers_metadata WHERE ticker IN ({tickers_str})"
            existing = self.conn.execute(query).df()
        except:
            return tickers # Si falla la query, asumir todos outdated
        
        existing_dict = existing.set_index('ticker')['last_updated'].to_dict()
        
        outdated = []
        now = datetime.now()
        
        for t in tickers:
            if t not in existing_dict:
                outdated.append(t)
            else:
                last_upd = existing_dict[t]
                # Verificar TTL 24h
                if (now - last_upd) > timedelta(hours=24):
                    outdated.append(t)
                    
        return outdated

    def _delete_data_for_tickers(self, tickers):
        """Borra datos de precios y fundamentales para una lista de tickers."""
        if not tickers: return
        
        logger.info(f"Limpiando datos previos para {len(tickers)} tickers...")
        tickers_str = "'" + "','".join(tickers) + "'"
        
        self.conn.execute(f"DELETE FROM prices WHERE ticker IN ({tickers_str})")
        self.conn.execute(f"DELETE FROM financials WHERE ticker IN ({tickers_str})")
        # No borramos metadata aun, se reemplazará al insertar
        
    def update_history(self, tickers: list, source: str = 'yfinance'):
        """
        Actualiza la base de datos solo para los tickers que lo necesiten.
        Args:
            tickers: Lista de tickers
            source: 'yfinance' o 'sec'
        """
        # Deduplicar
        tickers = sorted(list(set(tickers)))
        
        # 1. Verificar qué tickers necesitan update
        tickers_to_update = self._get_outdated_tickers(tickers)
        
        if not tickers_to_update:
            logger.info(f"Todos los {len(tickers)} tickers están actualizados (Cache < 24h).")
            return

        logger.info(f"Actualizando {len(tickers_to_update)} tickers (Fuente: {source.upper()})...")
        
        # 2. Limpiar datos viejos de esos tickers
        self._delete_data_for_tickers(tickers_to_update)
        
        try:
            # 3. Descargar Precios (Batch para los tickers a actualizar)
            # Usamos tickers_to_update para la descarga
            logger.info("Descargando precios (Batch)...")
            # threads=False para evitar conflictos con curl_cffi/requests session en algunos entornos (ej. tests)
            df_prices = yf.download(tickers_to_update, period="5y", group_by='ticker', auto_adjust=True, threads=False, progress=False)
            
            # Procesar y guardar precios
            batch_data = []
            
            # Caso 1: Un solo ticker (yf devuelve DataFrame simple, no MultiIndex)
            if len(tickers_to_update) == 1:
                ticker = tickers_to_update[0]
                df = df_prices
                if not df.empty:
                    df = df.reset_index()
                    for _, row in df.iterrows():
                        batch_data.append((
                            ticker, row['Date'], 
                            row['Open'], row['High'], row['Low'], row['Close'], row['Volume']
                        ))
            
            # Caso 2: Múltiples tickers (MultiIndex)
            else:
                for ticker in tickers_to_update:
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
                self.conn.executemany(
                    "INSERT OR IGNORE INTO prices VALUES (?, ?, ?, ?, ?, ?, ?)",
                    batch_data
                )
                logger.info(f"Insertados {len(batch_data)} registros de precios.")

            # 4. Descarga de Fundamentales y Metadatos
            logger.info(f"Descargando fundamentales (Fuente: {source.upper()})...")
            financials_data = []
            metadata_records = []
            
            # Usamos tickers_to_update para iterar
            if source == 'sec':
                # --- LOGICA SEC ---
                try:
                    downloader = SECDownloader(user_agent=SEC_USER_AGENT)
                    total = len(tickers_to_update)
                    for i, ticker in enumerate(tickers_to_update):
                        if i % 5 == 0: print(f"SEC Download {i}/{total} ({ticker})...", end='\r')
                        
                        # A. Metadatos básicos (desde yfinance para complementar)
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
                total = len(tickers_to_update)
                for i, ticker in enumerate(tickers_to_update):
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
            
            # Insertar Metadatos (con timestamp actual)
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

            logger.info("Actualización parcial completada con éxito.")
            
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
