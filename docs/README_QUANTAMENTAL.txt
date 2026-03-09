
# MANUAL DE USO - SISTEMA QUANTAMENTAL

Este sistema implementa una estrategia de inversión factor-based con timing técnico y dimensionamiento avanzado.

## REQUISITOS PREVIOS
1. Tener Python instalado.
2. Instalar dependencias:
   `pip install yfinance pandas numpy scipy matplotlib openpyxl`
3. Tener el archivo `ticker.txt` con la lista de activos a analizar.

## FLUJO DE TRABAJO (PASO A PASO)

### PASO 1: Ranking Fundamental (Generar Universo)
**Objetivo**: Identificar los 50 activos con mejor "Esperanza Matemática" según Fama-French.
1. Ejecutar el script:
   `python screener_fundamental.py`
2. Esperar a que finalice (puede tardar 10-20 mins dependiendo de la cantidad de activos).
3. **Resultado**: Se creará el archivo `Ranking_Fundamental_Top50.xlsx`.
4. **Acción Manual**: Abre el Excel, revisa los activos y elije tus favoritos (ej: top 10).

### PASO 2: Optimización de Cartera (Operar)
**Objetivo**: Saber cuánto comprar de cada activo seleccionado.
1. Ejecutar el script:
   `python optimizador_cartera.py`
2. El programa te pedirá:
   - "Ingrese tickers separados por coma": Copia y pega los símbolos que elegiste en el Paso 1 (ej: `AAPL, MSFT, GOOGL`).
   - "Z-Scores": (Opcional) Puedes ingresar el puntaje del Excel para darle más peso a los mejores fundamentales. Si das Enter toma 0 (neutral).
3. **Resultado**: El script calculará los pesos óptimos usando **Black-Litterman**.
   - Asignará MÁS capital a activos con:
     - Buen Fundamental (Z-Score alto).
     - Señal Técnica "Impulso" (Túnel Domènec Verte).
   - Asignará MENOS capital a activos en corrección.

### PASO 3: Simulación (Backtesting)
**Objetivo**: Probar la estrategia en el pasado.
1. Ejecutar:
   `python backtest_quantamental.py`
2. Ingresar fechas (ej: 2021-01-01 a 2024-01-01).
3. **Nota Importante**: Debido a limitaciones de datos gratuitos, simulaciones anteriores a 2020 pueden fallar o dar resultados incompletos.

## ARCHIVOS GENERADOS
- `logs/`: Carpeta con registros detallados de ejecución y errores.
- `Screener_Output.xlsx`: Salida del script técnico original (si se ejecuta individualmente).
- `Ranking_Fundamental_Top50.xlsx`: Salida del Screener Fama-French.

## DICCIONARIO DE COLUMNAS (RANKING EXCEL)

Los valores en el Excel son **Z-Scores** (Desviaciones Estándar). Indican qué tan lejos está el activo del promedio de SU PROPIO SECTOR.
Esto permite comparar peras con peras (ej: Banco vs Banco, Tech vs Tech).

1. **Z_Value (Factor HML - Value)**
   - Mide el Ratio Book-to-Market (Valor Libro / Precio).
   - **Positivo (+)**: La acción está "Barata" o Subvaluada respecto a su sector. (BUENO)
   - **Negativo (-)**: La acción está "Cara" o Sobrevaluada. (MALO para Value Investing)

2. **Z_Prof (Factor RMW - Profitability)**
   - Mide la Rentabilidad Operativa sobre el Patrimonio.
   - **Positivo (+)**: La empresa es más eficiente/rentable que sus competidores. (BUENO)
   - **Negativo (-)**: La empresa es menos rentable o tiene pérdidas operativas. (MALO)

3. **Z_Inv (Factor CMA - Investment)**
   - Mide el Crecimiento de Activos (Asset Growth).
   - **Positivo (+)**: La empresa está invirtiendo agresivamente (expandiendo activos rápido). Fama-French penaliza esto porque suele predecir menores retornos futuros. (MALO)
   - **Negativo (-)**: La empresa es "Conservadora" en su inversión. Fama-French premia esto. (BUENO)

4. **Final_Score**
   - Puntaje Unificado de Esperanza Matemática.
   - Fórmula: `0.4 * Z_Value + 0.3 * Z_Prof - 0.3 * Z_Inv`
   - Cuanto más alto, mejor combinación de Barata, Rentable y Conservadora.

---

## CÓMO INTERPRETAR LOS RESULTADOS

### 1. ¿Qué significa "Pares"?
Cuando decimos "Más rentable que sus pares", nos referimos a **Competidores del Mismo Sector**.
- El sistema agrupa acciones (ej: Tecnología) y calcula el promedio de ese grupo específico.
- **Sony** se compara con **Apple**, **Microsoft**, etc. No con un banco o una petrolera.
- Esto es vital porque cada industria tiene márgenes contables muy diferentes.

### 2. ¿Qué significa el Final_Score (ej: 2.93)?
**NO es una probabilidad de éxito (293%).**
Es un puntaje estadístico llamado **Z-Score** (Desviación Estándar). Mide "qué tan lejos del promedio" está el activo.

- **0.0**: El activo es **Promedio** (igual a sus competidores).
- **+1.0**: Bueno. Está por encima del 84% de sus competidores.
- **+2.0**: Muy Bueno. Está por encima del 97% de sus competidores.
- **+3.0**: **Excepcional**. Es una "estrella" estadística (Top 0.1%).

**Ejemplo SONY (2.93)**:
Significa que fundamentalmente es **casi 3 veces mejor (en desviaciones estándar)** que el promedio del sector Tecnológico. Es un activo de altísima calidad según los factores Fama-French.
Esta es la "Esperanza Matemática" o "Viento de Cola": estadísticamente, estás comprando lo mejor de la clase.


