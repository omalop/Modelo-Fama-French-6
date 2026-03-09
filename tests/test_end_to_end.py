import unittest
import os
import sys

from unittest.mock import patch
import shutil

# Agregar ruta raíz al path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.models.screener_fundamental import run_screener
from src.data.db_manager import DBManager

class TestEndToEnd(unittest.TestCase):
    
    def setUp(self):
        # Crear archivos de tickers de prueba
        with open('config/test_sec.txt', 'w') as f:
            f.write("AAPL,MSFT")
        with open('config/test_global.txt', 'w') as f:
            f.write("7751.T") # Canon Inc (Japón)
        with open('config/test_arg.txt', 'w') as f:
            f.write("GGAL.BA")
            
        # Asegurar limpieza de DB test previa
        for db_file in ['data/test_market_data.duckdb', 'data/test_market_data_2.duckdb']:
            if os.path.exists(db_file):
                try:
                    os.remove(db_file)
                    if os.path.exists(db_file + '.wal'): os.remove(db_file + '.wal')
                except: pass

    @patch('src.models.screener_fundamental.DBManager')
    def test_run_screeners(self, MockDBClass):
        # Redirigir a DB de prueba (versión 2 para evitar locks)
        def db_side_effect():
            return DBManager(db_path='data/test_market_data_2.duckdb')
        MockDBClass.side_effect = db_side_effect

        print("\n--- TEST INTEGRACIÓN E2E ---")
        
        # 1. SEC Source
        print("Probando Source: SEC...")
        run_screener('config/test_sec.txt', 'test_sec', 'data/processed/Test_Ranking_SEC.xlsx', source='sec')
        self.assertTrue(os.path.exists('data/processed/Test_Ranking_SEC.xlsx'))
        
        # 2. Global Source (YFinance)
        print("Probando Source: Global (YF)...")
        run_screener('config/test_global.txt', 'test_global', 'data/processed/Test_Ranking_GLB.xlsx', source='yfinance')
        self.assertTrue(os.path.exists('data/processed/Test_Ranking_GLB.xlsx'))

        # 3. Arg Source (YFinance)
        print("Probando Source: Arg (YF)...")
        run_screener('config/test_arg.txt', 'test_arg', 'data/processed/Test_Ranking_ARG.xlsx', source='yfinance')
        self.assertTrue(os.path.exists('data/processed/Test_Ranking_ARG.xlsx'))

    def tearDown(self):
        # Limpiar
        try:
            os.remove('config/test_sec.txt')
            os.remove('config/test_global.txt')
            os.remove('config/test_arg.txt')
            # No borramos los excel de output para poder inspeccionarlos si falla, 
            # o los borramos si queremos limpieza total. 
            pass 
        except:
            pass

if __name__ == '__main__':
    unittest.main()
