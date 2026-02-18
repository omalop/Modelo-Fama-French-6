import unittest
import sys
import os

# Agregar ruta raíz al path para importar módulos
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config.settings import SEC_USER_AGENT
from src.data.sec_downloader import SECDownloader

class TestSECConnection(unittest.TestCase):
    
    def setUp(self):
        self.downloader = SECDownloader(user_agent=SEC_USER_AGENT)
        
    def test_cik_mapping(self):
        """Prueba que el mapeo CIK se descargue correctamente."""
        self.assertTrue(len(self.downloader.cik_mapping) > 0, "El mapeo CIK está vacío.")
        self.assertIn('AAPL', self.downloader.cik_mapping, "AAPL no encontrado en mapeo CIK.")
        print(f"\n[OK] Mapeo CIK cargado: {len(self.downloader.cik_mapping)} tickers.")

    def test_fetch_and_parse_aapl(self):
        """Prueba descarga y parseo de hechos para AAPL."""
        ticker = 'AAPL'
        facts = self.downloader.get_company_facts(ticker)
        self.assertIsNotNone(facts, "No se obtuvieron facts para AAPL.")
        
        parsed_data = self.downloader.parse_facts(facts, ticker)
        self.assertTrue(len(parsed_data) > 0, "No se extrajeron métricas de AAPL.")
        
        # Verificar que existan las métricas clave
        metrics_found = set(item[2] for item in parsed_data) # index 2 is metric name
        expected_metrics = {'Total Stockholder Equity', 'Total Assets', 'Operating Income'}
        
        missing = expected_metrics - metrics_found
        self.assertTrue(len(missing) == 0, f"Faltan métricas clave: {missing}")
        
        print(f"\n[OK] Datos AAPL parseados: {len(parsed_data)} registros.")
        print(f"Métricas encontradas: {metrics_found}")

if __name__ == '__main__':
    unittest.main()
