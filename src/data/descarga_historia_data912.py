import requests
import json
import pandas as pd
import os
import logging
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# Configuración
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
DATA_DIR = os.path.join(ROOT_DIR, 'data', 'processed')
CACHE_FILE = os.path.join(DATA_DIR, 'historia_renta_fija.json')

class HistorialData912:
    BASE_URL = "https://data912.com/historical"

    def __init__(self, cache_file=CACHE_FILE):
        self.cache_file = cache_file
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)
        self.history = self._cargar_cache()

    def _cargar_cache(self):
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def _guardar_cache(self):
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.history, f, indent=4, ensure_ascii=False)

    def obtener_historia(self, ticker: str, force: bool = False):
        # Evitar llamados repetidos si ya tenemos data reciente (TTL 1 dia)
        # Pero para historia larga (6m), si ya existe no hace falta re-descargar todo el tiempo
        if ticker in self.history and not force:
            return self.history[ticker]

        logger.info(f"Descargando historia de {ticker} desde Data912...")
        
        # Intentar en /bonds/
        url = f"{self.BASE_URL}/bonds/{ticker}"
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                data = r.json()
                if len(data) > 1: # Si tiene mas de un punto (no solo el actual)
                    self.history[ticker] = data
                    self._guardar_cache()
                    return data
            
            # Si fallo o tiene 1 punto, quizas sea /stocks/ (algunos instrumentos locales raros)
            if r.status_code != 200 or len(r.json()) <= 1:
                url_s = f"{self.BASE_URL}/stocks/{ticker}"
                r_s = requests.get(url_s, timeout=10)
                if r_s.status_code == 200:
                    data = r_s.json()
                    self.history[ticker] = data
                    self._guardar_cache()
                    return data

        except Exception as e:
            logger.error(f"Error descargando {ticker}: {e}")
        
        return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    api = HistorialData912()
    # Tickers de RF segun dashboard anterior
    rf_tickers = ['AL30', 'AE38', 'AL35', 'GD35', 'GD30', 'S31L6', 'S31G6', 'TZXD6', 'TZX28', 'NDT25', 'YFC2O']
    for t in rf_tickers:
        h = api.obtener_historia(t)
        if h:
            print(f"✅ {t}: {len(h)} puntos.")
        else:
            print(f"❌ {t}: No disponible.")
