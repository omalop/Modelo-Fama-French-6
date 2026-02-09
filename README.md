# Modelo Fama-French 6 + Momentum Domenec

Este repositorio implementa un sistema de inversión "Quantamental" que combina el rigor académico del **Modelo de 6 Factores de Fama-French** con el timing técnico del **Momentum Domenec** y optimización de cartera avanzada mediante **Black-Litterman**.

## 🚀 Características Principales

*   **Screener Fundamental:** Identifica activos subvaluados y de alta calidad utilizando los factores de Fama-French (Value, Profitability, Investment, Size, Market, Momentum).
*   **Timing Técnico:** Aplica el indicador de "Túnel Domenec" y fuerza de tendencia para filtrar activos en corrección.
*   **Optimización de Cartera:** Utiliza el modelo Black-Litterman para asignar pesos óptimos, balanceando la visión de mercado con el equilibrio histórico.
*   **Backtesting Engine:** Módulo para simular el rendimiento histórico de la estrategia.
*   **Integración Local (Argentina):** Capacidad para verificar liquidez en mercados locales (BYMA/Rofex).

## 📋 Requisitos

*   Python 3.9+
*   Librerías principales: `pandas`, `numpy`, `yfinance`, `scipy`, `statsmodels`, `openpyxl`, `matplotlib`.

```bash
pip install pandas numpy yfinance scipy statsmodels openpyxl matplotlib requests
```

## ⚙️ Configuración

1.  **Lista de Activos:** Edita el archivo `ticker.txt` e incluye los símbolos (Yahoo Finance) que deseas analizar, uno por línea o separados por comas.
    *   Ejemplo: `AAPL, MSFT, GOOGL, YPF, GGAL`

## 🖥️ Uso

El sistema es modular y permite ejecutar pasos individuales o el flujo completo.

### 1. Screening Fundamental
Genera un ranking de los mejores activos basándose en su "Esperanza Matemática" (Z-Scores de factores).
```bash
python screener_fundamental.py
```
*   **Salida:** `Ranking_Fundamental_Top50.xlsx`

### 2. Optimización de Cartera
Calcula la asignación de capital ideal para una lista de activos seleccionados.
```bash
python optimizador_cartera.py
```

### 3. Backtesting
Simula la estrategia en el pasado para validar su robustez.
```bash
python backtest_quantamental.py
```

### 4. Análisis Técnico Rápido
Genera un reporte de indicadores técnicos (Semáforos, ADX, Túneles) sin el análisis fundamental pesado.
```bash
python "script deteccion momentum domenec.py"
```

### Ejecución Completa
Para correr el pipeline entero (Descarga -> Análisis -> Ranking -> Optimización):
```bash
python main.py
```

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
