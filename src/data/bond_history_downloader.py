import requests
import re
import json
import pandas as pd
import logging
from typing import Optional, Dict

logger = logging.getLogger(__name__)

ID_BONOS = {
    'AL30': 71,
    'S31L6': 962,
    'S31G6': 812,
    'TZXD6': 344,
    'TZXD7': 346,
    'TZX28': 343,
    'AE38': 120,
    'NDT25': 106,
    'YFC2O': 428
}

class ScreenermaticHistoryDownloader:
    BASE_URL = "https://www.screenermatic.com"
    LOGIN_URL = f"{BASE_URL}/login.php"
    SIMULATOR_URL = f"{BASE_URL}/simulador_bonos.php"

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password
        self.session = requests.Session()
        self.logged_in = False

    def login(self) -> bool:
        payload = {
            'email': self.email,
            'pass': self.password
        }
        try:
            r = self.session.post(self.LOGIN_URL, data=payload, timeout=15)
            if self.session.cookies.get('PHPSESSID'):
                self.logged_in = True
                logger.info("✅ Login exitoso en Screenermatic")
                return True
            else:
                logger.error("❌ Fallo el login en Screenermatic (no se encontro la cookie PHPSESSID)")
                return False
        except Exception as e:
            logger.error(f"Error al loguear en Screenermatic: {e}")
            return False

    def get_bond_history(self, ticker: str) -> Optional[pd.Series]:
        if not self.logged_in:
            if not self.login():
                return None
        
        bond_id = ID_BONOS.get(ticker)
        if not bond_id:
            logger.warning(f"ID no encontrado para ticker {ticker}")
            return None

        url = f"{self.SIMULATOR_URL}?id={bond_id}"
        try:
            r = self.session.get(url, timeout=15)
            html = r.text
            
            # Buscar Chart.js blocks
            scripts = re.findall(r'<script.*?>\s*(.*?new Chart.*?)\s*</script>', html, re.DOTALL)
            
            found_labels = []
            for i, s in enumerate(scripts):
                label_match = re.search(r'label:\s*[\"\'](.*?)[\"\']', s)
                if label_match:
                    ln = label_match.group(1)
                    found_labels.append(ln)
                    
                    if 'precio' in ln.lower():
                        # Extraer arrays
                        labels_match = re.search(r'labels:\s*\[(.*?)\],', s, re.DOTALL)
                        data_match = re.search(r'data:\s*\[(.*?)\],', s, re.DOTALL)
                        
                        if not labels_match or not data_match:
                             labels_match = re.search(r'labels:\s*\[(.*?)\]', s, re.DOTALL)
                             data_match = re.search(r'data:\s*\[(.*?)\]', s, re.DOTALL)

                        if labels_match and data_match:
                            labels_raw = "[" + labels_match.group(1).replace("'", '"') + "]"
                            data_raw = "[" + data_match.group(1).replace("'", '"') + "]"
                            
                            labels_raw = re.sub(r',\s*\]', ']', labels_raw)
                            data_raw = re.sub(r',\s*\]', ']', data_raw)
                            
                            try:
                                labels = json.loads(labels_raw)
                                prices = json.loads(data_raw)
                                
                                if len(labels) != len(prices):
                                     continue

                                s_data = pd.Series(prices, index=pd.to_datetime(labels, dayfirst=True))
                                s_data = s_data.sort_index()
                                return s_data[~s_data.index.duplicated(keep='last')]
                            except:
                                continue
                                
            logger.warning(f"No se encontro grafico de 'Precio' para de {ticker}. Labels vistos: {found_labels}")
            return None
        except Exception as e:
            logger.error(f"Error descargando historia de {ticker}: {e}")
            return None

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import os
    email = os.getenv("SCREENERMATIC_EMAIL", "tu_correo@ejemplo.com")
    pwd = os.getenv("SCREENERMATIC_PASSWORD", "tu_contraseña")
    downloader = ScreenermaticHistoryDownloader(email, pwd)
    for t in ['AL30', 'S31L6']:
        hist = downloader.get_bond_history(t)
        if hist is not None:
            print(f"✅ {t}: {len(hist)} puntos. Ultimo: {hist.iloc[-1]} ({hist.index[-1].date()})")
        else:
            print(f"❌ Fallo {t}")
