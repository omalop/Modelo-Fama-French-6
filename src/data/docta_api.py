import requests
import os
import json
import logging
from typing import Dict, Optional, List
import pandas as pd
from datetime import datetime

logger = logging.getLogger(__name__)

class DoctaCapitalAPI:
    """
    Cliente oficial para interactuar con la API REST de Docta Capital.
    Proporciona rendimientos en tiempo real de renta fija emergente apoyando
    la metodología de cálculo dinámico de Yield Gap.
    
    Ref: https://docs.doctacapital.com.ar/
    """
    BASE_URL = "https://api.doctacapital.com.ar/api/v1"
    
    def __init__(self, client_id: str, client_secret: str):
        """Inicializa el cliente y obtiene el token OAuth2 inicial."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = None
        self._authenticate()

    def _authenticate(self) -> None:
        """
        Intercambia el Client ID y Client Secret por un JWT Token.
        Se asume un endpoint estándar OAuth2 según documentaciones Pyme.
        """
        # URL correcta según documentación oficial: SIN barra al final
        url = f"{self.BASE_URL}/auth/token"
        try:
            # Body según documentación oficial de Docta Capital
            payload = {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret
            }
            response = requests.post(url, json=payload, timeout=10)
            
            # Fallback en caso de que use Forms
            if response.status_code != 200:
                response = requests.post(url, data=payload, timeout=10)
                
            response.raise_for_status()
            data = response.json()
            self.token = data.get('access_token', data.get('token'))
            logger.info("✅ Autenticación exitosa con API Docta Capital")
        except requests.exceptions.RequestException as e:
            logger.error(f"Fallo al autenticar contra Docta Capital: {e}")
            raise ConnectionError(f"Error de conexión OAuth2: {e}")

    def _get_headers(self) -> Dict[str, str]:
        if not self.token:
            self._authenticate()
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def get_instruments(self) -> pd.DataFrame:
        """Descarga el catálogo maestro de bonos disponibles."""
        url = f"{self.BASE_URL}/bonds/instruments"
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=15)
            response.raise_for_status()
            data = response.json()
            # Asumimos que la lista viene en 'data' o directamente como lista
            items = data.get('data', data) if isinstance(data, dict) else data
            return pd.DataFrame(items)
        except Exception as e:
            logger.error(f"Error parseando listado de instrumentos: {e}")
            return pd.DataFrame()

    def get_bond_yield(self, ticker: str) -> Optional[float]:
        """
        Obtiene la TIR (Yield to Maturity) intradiaria para un ticker específico.
        Según documentación oficial: GET /api/v1/bonds/yields/{symbol}/intraday
        Estructura de respuesta: {"ticker": "AL30", "data": [{"tir": 0.09887, ...}], "metadata": {...}}
        """
        url = f"{self.BASE_URL}/bonds/yields/{ticker}/intraday"
        try:
            response = requests.get(url, headers=self._get_headers(), timeout=10)
            response.raise_for_status()
            data = response.json()

            # Estructura oficial: {"ticker": ..., "data": [{"tir": float, ...}], "metadata": ...}
            if isinstance(data, dict):
                registros = data.get('data', [])
                if isinstance(registros, list) and len(registros) > 0:
                    tir = registros[0].get('tir')
                    if tir is not None:
                        return float(tir)
                # Fallback: campo tir directo en el dict
                if 'tir' in data:
                    return float(data['tir'])

            # Caso lista directa (respuesta antigua)
            if isinstance(data, list) and len(data) > 0:
                tir = data[0].get('tir') if isinstance(data[0], dict) else None
                if tir is not None:
                    return float(tir)

            logger.warning(f"Estructura no reconocida al pedir TIR de {ticker}: {str(data)[:100]}")
            return None

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 401:
                logger.info("Token expirado, re-autenticando...")
                self._authenticate()
                return self.get_bond_yield(ticker)  # Reintentar
            elif e.response.status_code == 404:
                # No hay datos intraday hoy para este ticker; normal fuera de horario
                logger.debug(f"{ticker}: sin datos intraday disponibles (404). Bono no liquida hoy.")
                return None
            logger.error(f"Error HTTP consultando Docta API: {e}")
            return None
        except Exception as e:
            logger.error(f"Error obteniendo yield de {ticker}: {e}")
            return None

if __name__ == "__main__":
    # Test unitario básico interactivo
    logging.basicConfig(level=logging.INFO)
    print("Probando cliente Docta API...")
    try:
        # Se asume que el usuario definió las variables o se pasan de .env
        CLIENT_ID = os.getenv("DOCTA_CLIENT_ID", "docta-api-cf68347b-omlop")
        CLIENT_SECRET = os.getenv("DOCTA_CLIENT_SECRET", "_ciyJML_JOgBD89Ft39PL6Az-ps9BJAAapzkQJ-u-LM")
        client = DoctaCapitalAPI(CLIENT_ID, CLIENT_SECRET)
        
        # Test 1: Fetch lista
        df_bonos = client.get_instruments()
        print(f"Obtenidos {len(df_bonos)} bonos del catálogo.")
        if not df_bonos.empty:
            print(df_bonos.head())
            
    except Exception as e:
         print(f"Prueba falló (probablemente entorno sin internet o API Key de test rechazada): {e}")
