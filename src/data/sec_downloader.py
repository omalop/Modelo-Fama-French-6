import requests
import pandas as pd
import time
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class SECDownloader:
    """
    Descargador de datos fundamentales desde SEC EDGAR.
    Respeta límites de tasa (10 req/seg) y User-Agent obligatorio.
    """
    
    BASE_URL = "https://data.sec.gov"
    
    def __init__(self, user_agent):
        self.headers = {
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate"
        }
        self.cik_mapping = {}
        self._load_cik_mapping()

    def _load_cik_mapping(self):
        """Descarga el mapeo Ticker -> CIK oficial de la SEC."""
        try:
            url = "https://www.sec.gov/files/company_tickers.json"
            # Nota: www.sec.gov a veces requiere headers también
            resp = requests.get(url, headers=self.headers)
            if resp.status_code == 200:
                data = resp.json()
                # El formato es indexado por numeros: 0: {cik_str, ticker, title}
                for entry in data.values():
                    self.cik_mapping[entry['ticker']] = str(entry['cik_str']).zfill(10)
                logger.info(f"Mapeo CIK cargado: {len(self.cik_mapping)} tickers.")
            else:
                logger.error(f"Error descargando company_tickers.json: {resp.status_code}")
        except Exception as e:
            logger.error(f"Excepción cargando CIK mapping: {e}")

    def get_cik(self, ticker):
        """Retorna el CIK (10 dígitos) para un ticker."""
        return self.cik_mapping.get(ticker.upper())

    def get_company_facts(self, ticker):
        """
        Descarga todos los hechos (facts) reportados en XBRL para un ticker.
        Retorna el JSON completo procesado.
        """
        cik = self.get_cik(ticker)
        if not cik:
            logger.warning(f"CIK no encontrado para {ticker}")
            return None
        
        url = f"{self.BASE_URL}/api/xbrl/companyfacts/CIK{cik}.json"
        
        try:
            # Rate limit manual simple (0.15s para estar seguros < 10req/s)
            time.sleep(0.15) 
            
            resp = requests.get(url, headers=self.headers)
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 404:
                logger.warning(f"Datos no encontrados en SEC para {ticker} (404)")
                return None
            else:
                logger.error(f"Error SEC API {resp.status_code} para {ticker}")
                return None
        except Exception as e:
            logger.error(f"Excepción descargando facts para {ticker}: {e}")
            return None

    def parse_facts(self, json_data, ticker):
        """
        Extrae métricas Fama-French del JSON crudo de la SEC.
        Retorna lista de tuplas: (ticker, report_date, metric, value, type)
        """
        if not json_data or 'facts' not in json_data:
            return []

        us_gaap = json_data['facts'].get('us-gaap', {})
        results = []

        # Mapa de Conceptos GAAP -> Métricas Internas
        # Prioridad: Concepto Principal > Concepto Alternativo
        concept_map = {
            'StockholdersEquity': ('Total Stockholder Equity', 'BS'),
            'Equity': ('Total Stockholder Equity', 'BS'), # Fallback
            
            'Assets': ('Total Assets', 'BS'),
            
            'OperatingIncomeLoss': ('Operating Income', 'IS'),
            'OperatingIncome': ('Operating Income', 'IS') # A veces difiere nombre
        }

        for concept, (metric_name, doc_type) in concept_map.items():
            if concept in us_gaap:
                units = us_gaap[concept].get('units', {})
                # Generalmente 'USD'
                for unit_name, records in units.items():
                    # Filtrar reporte anual (10-K) o trimestral (10-Q)
                    # Para Fama-French clásico se usa anual, pero screener usa TTM/reciente.
                    # Tomamos todos los reportes 'filed' (presentados).
                    for r in records:
                        if 'val' not in r or 'end' not in r: continue
                        
                        # Validar fecha
                        try:
                            val = float(r['val'])
                            end_date = r['end'] # string YYYY-MM-DD
                            
                            # Filtros de calidad de dato:
                            # Preferir 10-K/10-Q sobre otros forms si hay duplicados
                            form = r.get('form', '')
                            
                            results.append({
                                'ticker': ticker,
                                'report_date': end_date,
                                'metric': metric_name,
                                'value': val,
                                'type': doc_type,
                                'form': form,
                                'filed': r.get('filed', '')
                            })
                        except:
                            continue

        # Post-procesamiento: Deduplicación
        # Si hay múltiples valores para misma fecha y métrica, priorizar 10-K > 10-Q > Otros
        df = pd.DataFrame(results)
        if df.empty: return []

        # Convertir fecha
        df['report_date'] = pd.to_datetime(df['report_date'])
        
        # Ordenar prioridad de Form
        form_priority = {'10-K': 1, '10-Q': 2, '20-F': 1, '40-F': 1}
        df['priority'] = df['form'].map(form_priority).fillna(99)
        
        # Ordenar por fecha (desc), prioridad (asc), filed (desc)
        df.sort_values(by=['report_date', 'metric', 'priority', 'filed'], ascending=[False, True, True, False], inplace=True)
        
        # Deduplicar (quedarse con el primero según orden)
        df_clean = df.drop_duplicates(subset=['report_date', 'metric'], keep='first')
        
        # Convertir a lista de tuplas para DB
        final_data = []
        for _, row in df_clean.iterrows():
            final_data.append((
                row['ticker'],
                row['report_date'], # Timestamp
                row['metric'],
                row['value'],
                row['type']
            ))
            
        return final_data
