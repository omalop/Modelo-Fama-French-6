import os
from pathlib import Path

# Rutas Base
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"

# Crear directorios si no existen
LOG_DIR.mkdir(exist_ok=True)

# Parámetros Operativos (Constitución Antigravity)
FACTOR_INOPERATIVIDAD = 0.84  # 84% Tiempo en Stand-by
RIESGO_MAX_TRADE = 0.01       # 1% del capital por operación

# Configuración de Datos
TIMEFRAMES = {
    'trimestral': '3mo',
    'mensual': '1mo',
    'semanal': '1wk',
    'diario': '1d',
    'intradia': '1h'
}

# Configuración de Cotas
VALIDACIONES_MINIMAS_TRIMESTRAL = 2
VALIDACIONES_MINIMAS_SEMANAL = 3
VALIDACIONES_MINIMAS_DIARIO = 20

# Rutas de Archivos
PATH_RANKING_GLOBAL = DATA_DIR / "processed" / "Ranking_Global_Top.xlsx"
PATH_TICKER_ARG = BASE_DIR.parent / "config" / "ticker_arg.txt" # Referencia externa si necesaria
