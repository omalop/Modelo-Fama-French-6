import sys
import os
# Agregar el directorio de modelos al path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'models')))

from optimizador_cartera import GestorBlackLitterman
import pandas as pd

def run_demo():
    print("--- DEMOSTRACIÓN AUTOMÁTICA: OPTIMIZADOR BLACK-LITTERMAN ---")
    
    # 1. Selección de Activos (Simulando Salida del Screener Fundamental)
    tickers = ['AAPL', 'MSFT', 'GOOGL', 'NVDA', 'TSLA']
    print(f"Activos Seleccionados: {tickers}")
    
    # 2. Definición de Vistas Fundamentales (Simulando Z-Scores del Excel)
    # Escenario: 
    # - NVDA: Fundamental Excelente (Z=2.5) -> Debería tener peso alto
    # - MSFT: Fundamental Sólido (Z=1.0) -> Peso medio-alto
    # - AAPL: Neutral (Z=0.0) -> Peso neutral (market cap)
    # - GOOGL: Débil (Z=-1.0) -> Peso bajo
    # - TSLA: Muy Débil (Z=-2.0) -> Peso mínimo/cero
    fundamental_scores = {
        'NVDA': 2.5,
        'MSFT': 1.0,
        'AAPL': 0.0,
        'GOOGL': -1.0,
        'TSLA': -2.0
    }
    print("\n--- VISTAS FUNDAMENTALES (Z-SCORES) ---")
    for t, s in fundamental_scores.items():
        print(f"{t}: {s}")
        
    # 3. Inicialización del Gestor
    print("\nInicializando Gestor Black-Litterman...")
    optimizer = GestorBlackLitterman(tickers)
    
    # 4. Descarga de Datos de Mercado (Precios + Market Caps)
    # Usamos un periodo corto para la demo sea rápida
    optimizer.fetch_market_data(period='1y')
    
    # 5. Ejecutar optimización
    print("\nOptimizando pesos combinando Equilibrio de Mercado + Vistas + Confianza Técnica...")
    try:
        weights_bl = optimizer.optimize(fundamental_scores)
        
        print("\n" + "="*40)
        print("RESULTADOS DE ASIGNACIÓN ÓPTIMA (BL)")
        print("="*40)
        print(weights_bl.sort_values(ascending=False).to_string())
        print("\nInterpretación:")
        print("- NVDA debería tener el peso más alto (Fundamental fuerte).")
        print("- TSLA debería tener peso cercano a cero (Fundamental débil).")
        print("- Los pesos suman 100%.")
    except Exception as e:
        print(f"\nError en la optimización: {e}")

if __name__ == "__main__":
    run_demo()
