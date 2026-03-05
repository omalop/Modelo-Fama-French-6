# Metodología Científica - Modelo Fama-French 6 + Optimizador Dinámico

## 1. Introducción y Objetivo
Este proyecto tiene como objetivo la implementación de un sistema de inversión robusto basado en fundamentos académicos de finanzas cuantitativas y análisis técnico avanzado. El sistema busca maximizar la **Esperanza Matemática** de los retornos mediante un enfoque "Quantamental" que selecciona activos óptimos y, crucialmente, **determina la exposición dinámica entre renta variable y liquidez (renta fija corporativa/soberana como resguardo)** en base a modelos de ciclo y probabilidad de crisis.

Está dirigido a analistas cuantitativos e inversores que rechazan paradigmas tradicionales y estáticos (ej. 60/40), exigiendo en su lugar una gestión dinámica fundamentada en modelos probabilísticos y macroeconómicos regidos por el actual contexto argentino.

## 2. Fundamentos Teóricos

### A. Modelo de 6 Factores de Fama-French
El núcleo fundamental del modelo se basa en la extensión del modelo de tres factores original de Fama y French.
- **Mercado (Mkt-RF), Tamaño (SMB), Valor (HML), Rentabilidad (RMW), Inversión (CMA), Momentum (MOM).**

**Referencia:** Fama, E. F., & French, K. R. (2015). "A five-factor asset pricing model". Journal of Financial Economics.

### B. Optimizador Dinámico (Regime-Switching y Prima de Riesgo)
Para salir del modelo estático, la distribución de capital (Asset Allocation) se rige por un Optimizador Dinámico Cuántico que procesa la Esperanza Matemática y detecta desajustes entre Renta Fija y Renta Variable.

**1. Evaluación de Renta Fija Local y Proxys de Liquidez:**
Bajo el contexto macroeconómico de "Shock Fiscal" y "Emisión Cero" (Escuela Austríaca / Gestión Milei), la renta fija corporativa (ej. ONs de Energía e Infraestructura) asume el rol de **activo libre de riesgo local** o resguardo de capital. Históricamente, en crisis de liquidez, estas obligaciones negociables demostraron resiliencia estructural, rindiendo de manera sostenida (TIR/Exit Yield esperada ~8%). 
El modelo emplea esta renta fija no para *momentum trade*, sino estrictamente como "Buy and Hold" o estacionamiento táctico.

**2. Yield Gap Digno (Prima de Riesgo Soberana Relativa):**
El marco estático asume una tasa libre de riesgo constante. En un mercado emergente y volátil como Argentina, el ancla obligatoria es la métrica de riesgo soberano continuo. 
`Tasa de Descuento Local = Tasa Treasury Y10 (Libre de Riesgo USA) + EMBI+ Argentina (Riesgo País)`

El modelo evalúa el *mispricing* de la renta variable mediante:
`Yield Gap = (E/P de Renta Variable Local) - (Tasa de Descuento Local)`
Cuando la prima de riesgo que ofrece la renta variable es inferior al Riesgo País compensado, indica extrema sobrevaloración de la Renta Variable frente a la Renta Fija. Este spread puede consultarse en tiempo real utilizando flujos verdaderos a través del motor *Docta Capital*.

**3. Detección de Modos de Riesgo Extremo (Validación del Dashboard de Crisis):**
Se conservan y validan académicamente los siguientes predictores de *crisis_dashboard_pro.py*:
- **Inversión de Curva (10Y-2Y):** Ampliamente documentado por Estrella y Mishkin (1998) como el predictor más contundente de recesiones económicas norteamericanas con 12-18 meses de adelanto.
- **US High Yield Spread:** Según Gilchrist y Zakrajšek (2012), la expansión del exceso de prima de los bonos corporativos HY es un indicador sumamente robusto de contracciones del crédito con impacto sistémico y global, forzando un *flight to quality* o *flight to liquidity* (vaciando posiciones de emergentes).
- **VIX:** Medida proxy del temor del mercado, esencial en modelos GARCH asimétricos para calcular *Value at Risk*.

Estas tres señales combinadas modulan el "Expected Shortfall" del optimizador.

**Referencia:** 
- Estrada, J. (2000). "The Cost of Equity in Emerging Markets: A Downside Risk Approach". Emerging Markets Quarterly.
- Yardeni, E. (1997). "Fed's Stock Market Model".

### C. Optimización Black-Litterman Modificada
El optimizador matemático aplica Black-Litterman pero con un Prior dominado por el **Régimen de Mercado**.
- En Modo "Normal", el capital se aloja en Renta Variable según factores Fama-French y vistas Domènec.
- En Modo "Anómalo/Crisis", la diagonal de Incertidumbre (Omega) estalla, llevando el posterior BL al activo refugio (ONs y bonos soberanos como el GD35 para perfiles agresivos).

## 3. Descripción de Procesos Básica
| Proceso | Objetivo Científico | Salida Principal |
| :--- | :--- | :--- |
| **Screener Fama-French** | Normalizar Z-Score de balances. | `Ranking_Global_Top.xlsx` |
| **Detección Crisis / Riesgo** | Evaluar VIX, Curva Tipos y Yield Corp. | Matriz de Estado (P(Crisis)) |
| **Optimizador Cuántico** | Asset Allocation Dinámico | Ponderación Renta Variable vs Fija |
| **Generador BL** | Asignación intradiaria de los activos de RV. | Pesos de Cartera (%) |

## 4. Limitaciones y Supuestos
- **Supuesto de Muestra:** Se asume Muestra n >= 30 para todos los tests estadísticos.
- **Normalidad de Retornos:** Sometido a validación test Shapiro-Wilk en ejecución; en caso de fallo, se recurre a métricas de distribución empírica pesada (no-paramétricas).
- **Riesgo Sistémico Local:** El modelo asume continuidad parcial en el estado legal de los bonos macro (No Default a corto plazo) apoyado en las métricas de superávit de NotebookLM.

---
*Documento generado bajo los Artículos 2, 4 y 8 de la Constitución Antigravity.*
