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
    'User-Agent': 'Omar Lopez (contacto@ejemplo.com)',
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
        # Agregamos columnas de TTL diferenciado si no existen
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tickers_metadata (
                ticker VARCHAR PRIMARY KEY,
                sector VARCHAR,
                shares DOUBLE,
                currency VARCHAR,
                last_updated TIMESTAMP,
                last_updated_prices TIMESTAMP,
                last_updated_financials TIMESTAMP
            )
        """)
        
        # Migración: Verificar si las nuevas columnas existen (para DBs ya creadas)
        cols = self.conn.execute("PRAGMA table_info('tickers_metadata')").df()
        if 'last_updated_prices' not in cols['name'].values:
            logger.info("Migrando tickers_metadata: Agregando columna last_updated_prices")
            self.conn.execute("ALTER TABLE tickers_metadata ADD COLUMN last_updated_prices TIMESTAMP")
        if 'last_updated_financials' not in cols['name'].values:
            logger.info("Migrando tickers_metadata: Agregando columna last_updated_financials")
            self.conn.execute("ALTER TABLE tickers_metadata ADD COLUMN last_updated_financials TIMESTAMP")

    def _get_outdated_tickers(self, tickers, category='prices'):
        """
        Identifica qué tickers necesitan actualización según categoría.
        category: 'prices' (7 días) o 'financials' (30 días).
        """
        if not tickers: return []
        
        col = 'last_updated_prices' if category == 'prices' else 'last_updated_financials'
        # Fallback a last_updated si la columna nueva está vacía (migración)
        query = f"""
            SELECT ticker, 
                   COALESCE({col}, last_updated) as last_upd 
            FROM tickers_metadata 
            WHERE ticker IN ({"'" + "','".join(tickers) + "'"})
        """
        try:
            existing = self.conn.execute(query).df()
        except:
            return tickers
        
        existing_dict = existing.set_index('ticker')['last_upd'].to_dict()
        outdated = []
        now = datetime.now()
        ttl = timedelta(days=7) if category == 'prices' else timedelta(days=30)
        
        for t in tickers:
            if t not in existing_dict or existing_dict[t] is None:
                outdated.append(t)
            else:
                last_upd = existing_dict[t]
                if (now - last_upd) > ttl:
                    outdated.append(t)
                    
        return outdated

    def _delete_data_for_tickers(self, tickers, table='prices'):
        """Borra datos para una lista de tickers en una tabla específica."""
        if not tickers: return
        tickers_str = "'" + "','".join(tickers) + "'"
        self.conn.execute(f"DELETE FROM {table} WHERE ticker IN ({tickers_str})")
    def _fetch_yfinance_fundamentals(self, ticker):
        """Helper para descargar fundamentales desde YFinance (Fallback/Default)."""
        data = []
        try:
            t = yf.Ticker(ticker)
            # B. Balance Sheet
            bs = t.balance_sheet
            if not bs.empty:
                for date_col in bs.columns:
                    report_date = pd.to_datetime(date_col)
                    for metric, value in bs[date_col].items():
                        if pd.notna(value):
                            data.append((ticker, report_date, metric, float(value), 'BS'))
            
            # C. Financials (Income Statement)
            fin = t.financials
            if not fin.empty:
                for date_col in fin.columns:
                    report_date = pd.to_datetime(date_col)
                    for metric, value in fin[date_col].items():
                        if pd.notna(value):
                            data.append((ticker, report_date, metric, float(value), 'IS'))
        except Exception as e:
            logger.debug(f"Error procesando fundamentales YF para {ticker}: {e}")
        return data

    def update_history(self, tickers: list, source: str = 'yfinance'):
        """Actualiza la DB con optimización de vectorización y TTL diferenciado."""
        tickers = sorted(list(set(tickers)))
        
        # 1. Tickers que necesitan precios (TTL 7d)
        tickers_prices = self._get_outdated_tickers(tickers, 'prices')
        # 2. Tickers que necesitan fundamentales (TTL 30d)
        tickers_fin = self._get_outdated_tickers(tickers, 'financials')

        if not tickers_prices and not tickers_fin:
            logger.info("Datos locales actualizados (TTL Precios: 7d, Fundamentales: 30d).")
            return

        try:
            # --- SECCIÓN A: ACTUALIZAR PRECIOS (Si es necesario) ---
            if tickers_prices:
                logger.info(f"Actualizando precios para {len(tickers_prices)} tickers...")
                self._delete_data_for_tickers(tickers_prices, 'prices')
                
                batch_size = 50
                for i in range(0, len(tickers_prices), batch_size):
                    batch = tickers_prices[i:i + batch_size]
                    logger.info(f"Descargando precios batch {i//batch_size + 1}/{(len(tickers_prices)-1)//batch_size + 1}...")
                    
                    try:
                        df_batch = yf.download(batch, period="20y", group_by='ticker', 
                                             auto_adjust=True, threads=False, progress=False)
                        
                        if df_batch.empty: continue

                        # Vectorización: Transformar MultiIndex a Long format
                        # La versión actual de yfinance SIEMPRE devuelve MultiIndex con
                        # niveles ['Ticker', 'Price']. Usamos stack en el nivel 'Ticker'.
                        if isinstance(df_batch.columns, pd.MultiIndex):
                            # Identificar el nivel que contiene los tickers
                            # yfinance: nivel 0='Price', nivel 1 podría ser el ticker o viceversa
                            ticker_level = None
                            for lvl_idx, lvl_name in enumerate(df_batch.columns.names):
                                if lvl_name == 'Ticker':
                                    ticker_level = lvl_idx
                                    break
                            
                            if ticker_level is None:
                                # Fallback: asumir que el nivel con más valores únicos es Ticker
                                ticker_level = 0 if len(df_batch.columns.get_level_values(0).unique()) > len(df_batch.columns.get_level_values(1).unique()) else 1
                            
                            # Stackear el nivel opuesto al 'Ticker' para obtener columnas OHLCV
                            price_level = 1 - ticker_level  # 0 o 1
                            df_long = df_batch.stack(level=ticker_level).reset_index()
                            
                            # Renombrar columnas estándar
                            rename_map = {
                                'Date': 'date', 'Ticker': 'ticker',
                                'Open': 'open', 'High': 'high', 'Low': 'low',
                                'Close': 'close', 'Volume': 'volume'
                            }
                            df_long.rename(columns=rename_map, inplace=True)
                            # Renombrar posibles variaciones de nombre de ticker
                            if 'ticker' not in df_long.columns:
                                for alt in ['Ticker', 'level_1', 'level_0']:
                                    if alt in df_long.columns:
                                        df_long.rename(columns={alt: 'ticker'}, inplace=True)
                                        break
                        else:
                            # Caso inexistente en yfinance moderno, pero por robustez:
                            df_long = df_batch.reset_index()
                            df_long['ticker'] = batch[0]
                            df_long.rename(columns={
                                'Date': 'date', 'Open': 'open', 'High': 'high',
                                'Low': 'low', 'Close': 'close', 'Volume': 'volume'
                            }, inplace=True)

                        # Limpieza y casting
                        df_long = df_long.dropna(subset=['close'])
                        
                        # ALINEAR el orden exacto de las columnas de DuckDB (ticker, date, open, high, low, close, volume)
                        # También descartamos cualquier otra columna adicional (ej: Adj Close, Dividends, etc)
                        column_order = ['ticker', 'date', 'open', 'high', 'low', 'close', 'volume']
                        df_long = df_long[[c for c in column_order if c in df_long.columns]]

                        # Inserción Directa a DuckDB desde Pandas (Ultra Rápido)
                        if not df_long.empty:
                            self.conn.execute("INSERT OR IGNORE INTO prices SELECT * FROM df_long")
                            # Actualizar timestamp de precios
                            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            self.conn.execute(f"""
                                UPDATE tickers_metadata SET last_updated_prices = '{now_str}' 
                                WHERE ticker IN ({"'" + "','".join(batch) + "'"})
                            """)
                    except Exception as e:
                        logger.error(f"Error en batch de precios: {e}")

            # --- SECCIÓN B: ACTUALIZAR FUNDAMENTALES (Si es necesario) ---
            if tickers_fin:
                logger.info(f"Actualizando fundamentales para {len(tickers_fin)} tickers...")
                self._delete_data_for_tickers(tickers_fin, 'financials')
                
                financials_data = []
                metadata_records = []
                
                if source == 'sec':
                    downloader = SECDownloader(user_agent=SEC_USER_AGENT)
                    for i, ticker in enumerate(tickers_fin):
                        if i % 10 == 0: print(f"SEC Download {i}/{len(tickers_fin)} ({ticker})...", end='\r')
                        
                        # Metadatos (Siempre actualizar si estamos aquí)
                        try:
                            info = yf.Ticker(ticker).info
                            metadata_records.append((
                                ticker, info.get('sector', 'Unknown'), 
                                info.get('sharesOutstanding', 0), info.get('currency', 'USD'),
                                datetime.now(), None, datetime.now() # last_updated, prices, financials
                            ))
                        except:
                            metadata_records.append((ticker, 'Unknown', 0, 'USD', datetime.now(), None, datetime.now()))

                        # Fundamentales
                        facts = downloader.get_company_facts(ticker)
                        if facts:
                            parsed = downloader.parse_facts(facts, ticker)
                            if parsed: financials_data.extend(parsed)
                        else:
                            yf_fund = self._fetch_yfinance_fundamentals(ticker)
                            if yf_fund: financials_data.extend(yf_fund)
                else:
                    for i, ticker in enumerate(tickers_fin):
                        if i % 10 == 0: print(f"YF Download {i}/{len(tickers_fin)} ({ticker})...", end='\r')
                        try:
                            t = yf.Ticker(ticker)
                            info = t.info
                            metadata_records.append((
                                ticker, info.get('sector', 'Unknown'), 
                                info.get('sharesOutstanding', 0), info.get('currency', 'USD'),
                                datetime.now(), None, datetime.now()
                            ))
                            yf_fund = self._fetch_yfinance_fundamentals(ticker)
                            if yf_fund: financials_data.extend(yf_fund)
                        except:
                            metadata_records.append((ticker, 'Unknown', 0, 'USD', datetime.now(), None, datetime.now()))

                # Inserciones en Batch
                if metadata_records:
                    self.conn.executemany("INSERT OR REPLACE INTO tickers_metadata VALUES (?, ?, ?, ?, ?, ?, ?)", metadata_records)
                if financials_data:
                    self.conn.executemany("INSERT OR IGNORE INTO financials VALUES (?, ?, ?, ?, ?)", financials_data)
                
                logger.info("Actualización de fundamentales completada.")
                
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

    def clear_table(self, table):
        """Vacía una tabla específica."""
        if table in ['prices', 'financials']:
            self.conn.execute(f"DELETE FROM {table}")
            col = 'last_updated_prices' if table == 'prices' else 'last_updated_financials'
            self.conn.execute(f"UPDATE tickers_metadata SET {col} = NULL")
            logger.info(f"Tabla {table} vaciada y timestamps reseteados.")
        elif table == 'all':
            self.conn.execute("DELETE FROM prices")
            self.conn.execute("DELETE FROM financials")
            self.conn.execute("DELETE FROM tickers_metadata")
            logger.info("Base de datos vaciada por completo.")
