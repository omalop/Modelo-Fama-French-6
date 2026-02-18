import logging
import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# Asegurar path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings, logging_config
from src.backtesting.motor_backtest import MotorBacktest

# Setup Logging
logging_config.setup_logging()
logger = logging.getLogger(__name__)

def modo_backtest():
    """
    Ejecuta simulación histórica.
    """
    logger.info("--- INICIANDO MODO BACKTEST ---")
    
    # 1. Cargar Tickers (Ranking Global)
    try:
        df_ranking = pd.read_excel(settings.PATH_RANKING_GLOBAL)
        tickers = df_ranking['Ticker'].tolist()
    except Exception as e:
        logger.warning(f"No se pudo cargar ranking: {e}. Usando tickers por defecto.")
        tickers = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA'] # Fallback
        
    # 2. Configurar Fechas
    fecha_fin = datetime.now()
    fecha_inicio = fecha_fin - timedelta(days=365*2) # 2 años
    
    # 3. Inicializar Motor
    motor = MotorBacktest(capital_inicial=10000)
    
    # 4. Ejecutar
    resultado = motor.ejecutar(tickers, fecha_inicio, fecha_fin)
    
    # 5. Mostrar Reporte
    print("\nResultados del Backtest:")
    print(f"Capital Final: ${resultado['capital_final']:.2f}")
    print(f"Sharpe Ratio: {resultado['sharpe_ratio']:.2f}")
    print(f"Max Drawdown: {resultado['max_drawdown']:.2%}")
    print(f"Trades: {resultado['metricas']['total_trades']}")
    print(f"Win Rate: {resultado['metricas']['win_rate']:.2%}")

if __name__ == "__main__":
    # Argumentos simples
    if len(sys.argv) > 1 and sys.argv[1] == 'backtest':
        modo_backtest()
    else:
        print("Uso: python main.py [backtest]")
        modo_backtest() # Default por ahora
