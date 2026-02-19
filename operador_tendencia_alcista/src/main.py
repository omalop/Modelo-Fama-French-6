import logging
import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# Asegurar path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config import settings, logging_config
from src.backtesting.motor_backtest import MotorBacktest
from src.visualizacion.grafico_cotas import VisualizadorCotas
from src.estructura.cotas_historicas import DetectorCotas
from src.data.repo_datos import RepositorioDatos

# Setup Logging
logging_config.setup_logging()
logger = logging.getLogger(__name__)

def modo_backtest():
    """
    Ejecuta simulación histórica con selección interactiva de activos.
    """
    logger.info("--- INICIANDO MODO BACKTEST ---")
    
    print("\n" + "="*40)
    print("      OPERADOR TENDENCIA ALCISTA")
    print("="*40)
    print("1. Analizar Ranking Global (Ranking_Global_Top.xlsx) - Backtest Masivo")
    print("2. Analizar Ticker/s particular/es (Incluye VISUALIZACION de Cotas)")
    
    opcion = input("\nSeleccione una opción (1 o 2): ").strip()
    
    tickers = []
    
    if opcion == '1':
        # 1. Cargar Tickers (Ranking Global)
        try:
            df_ranking = pd.read_excel(settings.PATH_RANKING_GLOBAL)
            tickers = df_ranking['Ticker'].tolist()
            logger.info(f"Cargados {len(tickers)} tickers del Ranking Global.")
        except Exception as e:
            logger.warning(f"No se pudo cargar ranking: {e}. Usando tickers por defecto.")
            tickers = ['AAPL', 'MSFT', 'GOOGL'] # Fallback
    elif opcion == '2':
        # 2. Ticker particular
        entrada = input("Ingrese el/los ticker/s separados por coma (ej: AAPL, ALUA.BA): ").strip()
        if entrada:
            tickers = [t.strip().upper() for t in entrada.split(',')]
            logger.info(f"Analizando tickers especificados: {tickers}")
        else:
            print("No se ingresaron tickers. Usando fallback.")
            tickers = ['AAPL']
    else:
        print("Opción no válida. Usando Ranking Global por defecto.")
        try:
            df_ranking = pd.read_excel(settings.PATH_RANKING_GLOBAL)
            tickers = df_ranking['Ticker'].tolist()
        except:
            tickers = ['AAPL']
            
    if not tickers:
        logger.error("No hay tickers para analizar. Abortando.")
        return

    # Configurar Fechas
    fecha_fin = datetime.now()
    fecha_inicio = fecha_fin - timedelta(days=365*2) # 2 años
    
    # Inicializar Motor
    motor = MotorBacktest(capital_inicial=10000)
    
    # Ejecutar
    resultado = motor.ejecutar(tickers, fecha_inicio, fecha_fin)
    
    # Mostrar Reporte
    print("\n" + "="*40)
    print("        RESULTADOS DEL BACKTEST")
    print("="*40)
    print(f"Capital Final: ${resultado['capital_final']:.2f}")
    print(f"Sharpe Ratio:  {resultado['sharpe_ratio']:.2f}")
    print(f"Max Drawdown:  {resultado['max_drawdown']:.2%}")
    print(f"Win Rate:      {resultado['metricas']['win_rate']:.2%}")
    print(f"Total Trades:  {resultado['metricas']['total_trades']}")
    print("="*40 + "\n")

    # --- NUEVA SECCIÓN: VISUALIZACIÓN ---
    if len(tickers) == 1 or opcion == '2':
        ver = input("¿Desea visualizar el gráfico de análisis técnico para el ticker analizado? (s/n): ").strip().lower()
        if ver == 's':
            ticker_v = tickers[-1] # El último o el único
            print(f"Generando gráfico para {ticker_v}...")
            repo = RepositorioDatos()
            datos_mt = repo.obtener_todo_multitemporal(ticker_v)
            detector = DetectorCotas()
            cotas = detector.detectar(datos_mt)
            df_diario = datos_mt.get('diario')
            if df_diario is not None and not df_diario.empty:
                VisualizadorCotas.plot_cotas(df_diario, cotas, ticker_v)
            else:
                print("No hay datos disponibles para graficar.")

if __name__ == "__main__":
    # Iniciar directamente en modo interactivo
    modo_backtest()
