# Modelo Fama-French 6 + Momentum Domenec

Este repositorio implementa un sistema de inversión "Quantamental" que combina el rigor académico del **Modelo de 6 Factores de Fama-French** con el timing técnico del **Momentum Domenec** y optimización de cartera avanzada mediante **Black-Litterman**.

## 🚀 Características Principales

*   **Screener Fundamental:** Identifica activos subvaluados y de alta calidad utilizando los factores de Fama-French (Value, Profitability, Investment, Size, Market, Momentum).
*   **Timing Técnico:** Aplica el indicador de "Túnel Domenec" y fuerza de tendencia para filtrar activos en corrección.
*   **Optimización de Cartera:** Utiliza el modelo Black-Litterman para asignar pesos óptimos, balanceando la visión de mercado con el equilibrio histórico.
*   **Backtesting Engine:** Módulo para simular el rendimiento histórico de la estrategia.
*   **Integración Local (Argentina):** Capacidad para verificar liquidez en mercados locales (BYMA/Rofex).

## 📁 Estructura del Proyecto

Siguiendo el **Artículo 7** de la constitución, el proyecto se organiza de la siguiente manera:

```text
Modelo Fama-French 6/
├── config/                  # Archivos de configuración y listas de tickers
│   ├── ticker.txt           # Lista de activos globales
│   └── ticker_arg.txt       # Lista de activos argentinos
├── data/                    # Almacenamiento de datos (ignorado en git excepto estructura)
│   ├── raw/                 # Datos crudos (no procesados)
│   ├── interim/             # Datos en proceso de transformación
│   └── processed/           # Rankings y resultados finales (Excel)
├── docs/                    # Documentación científica y manuales
│   ├── metodologia.md       # Fundamento científico del modelo
│   ├── manual_usuario.txt   # Guía de uso
│   └── ...
├── src/                     # Código fuente
│   ├── data/                # Scripts de ETL y validación
│   ├── models/              # Modelos cuantitativos (Screener, Backtest, Optimizador)
│   └── utils/               # Utilidades (Inspección, Sincronización GitHub)
├── tests/                   # Pruebas unitarias e integración
├── logs/                    # Registros de ejecución
└── requirements.txt         # Versiones exactas de librerías
```

## 📋 Requisitos

## ⚙️ Configuración

1.  **Lista de Activos:** Edita el archivo `config/ticker.txt` e incluye los símbolos (Yahoo Finance) que deseas analizar, uno por línea o separados por comas.
    *   Ejemplo: `AAPL, MSFT, GOOGL, YPF, GGAL`

## 🖥️ Uso

El sistema es modular y permite ejecutar pasos individuales o el flujo completo.

### 1. Screening Fundamental
Genera un ranking de los mejores activos basándose en su "Esperanza Matemática" (Z-Scores de factores).
```bash
python src/models/screener_fundamental.py
```
*   **Salida:** `data/processed/Ranking_Global_Top.xlsx`

### 2. Optimización de Cartera
Calcula la asignación de capital ideal para una lista de activos seleccionados.
```bash
python src/models/optimizador_cartera.py
```

### 3. Backtesting
Simula la estrategia en el pasado para validar su robustez.
```bash
python src/models/backtest_quantamental.py
```

### 4. Análisis Técnico Rápido
Genera un reporte de indicadores técnicos (Semáforos, ADX, Túneles) sin el análisis fundamental pesado.
```bash
python "src/models/script deteccion momentum domenec.py"
```

### 5. Sincronización GitHub
Actualiza el repositorio remoto con los cambios locales.
```bash
python src/utils/git_sync.py
```

### Ejecución Completa
Para correr el pipeline entero (Descarga -> Análisis -> Ranking -> Optimización):
```bash
python main.py
```

> **Nota Pendiente:** El archivo `main.py` para la ejecución automatizada del pipeline completo está programado para ser desarrollado una vez finalizado el periodo de pruebas actual.


## 📊 Interpretación de Resultados

Los reportes generan **Z-Scores** (Desviaciones Estándar) para comparar activos de diferentes sectores:

*   **Z > 0**: El activo es superior al promedio de su sector.
*   **Z < 0**: El activo es inferior al promedio.
*   **Final Score**: Suma ponderada de Value (0.4), Profitability (0.3) e Investment (-0.3).

## ⚠️ Disclaimer

**Este software es para fines educativos y de investigación exclusivamente.**
No constituye una recomendación de inversión, compra o venta de activos financieros. Los rendimientos pasados no garantizan rendimientos futuros. El autor no se hace responsable por pérdidas financieras derivadas del uso de este código.

---
*Desarrollado con asistencia de Antigravity AI*
