import sys
import os
import time

# Add src to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

from models.screener_fundamental import run_screener

def test_integration():
    print("--- INICIANDO TEST DE INTEGRACIÓN DB ---")
    
    # 1. Primera Ejecución (Debe descargar)
    start_time = time.time()
    print("\n[EJECUCION 1] Iniciando (Esperando descarga)...")
    run_screener('config/ticker_test.txt', 'global', 'data/processed/Test_Ranking_1.xlsx')
    duration_1 = time.time() - start_time
    print(f"[EJECUCION 1] Completada en {duration_1:.2f} segundos.")
    
    # 2. Segunda Ejecución (Debe usar caché)
    start_time = time.time()
    print("\n[EJECUCION 2] Iniciando (Esperando caché)...")
    run_screener('config/ticker_test.txt', 'global', 'data/processed/Test_Ranking_2.xlsx')
    duration_2 = time.time() - start_time
    print(f"[EJECUCION 2] Completada en {duration_2:.2f} segundos.")
    
    # Validación
    if duration_2 < duration_1: # El caché debe ser significativamente más rápido
        print("\n[EXITO] El caché funcionó (Ejecución 2 fue más rápida).")
    else:
        print("\n[WARNING] No se observó mejora de tiempo significativa. Verificar logs.")

if __name__ == "__main__":
    test_integration()
