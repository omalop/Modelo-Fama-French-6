# Metodología Científica - Modelo Fama-French 6 + Momentum Domenec

## 1. Introducción y Objetivo
Este proyecto tiene como objetivo la implementación de un sistema de inversión robusto basado en fundamentos académicos de finanzas cuantitativas y análisis técnico avanzado. El sistema busca maximizar la **Esperanza Matemática** de los retornos mediante la identificación de activos con factores de riesgo premiados históricamente y el filtrado por tendencia y momentum.

Está dirigido a analistas cuantitativos e inversores institucionales que buscan un enfoque sistemático para la selección de activos y optimización de carteras.

## 2. Fundamentos Teóricos

### A. Modelo de 6 Factores de Fama-French
El núcleo fundamental del modelo se basa en la extensión del modelo de tres factores original de Fama y French.
- **Mercado (Mkt-RF):** El exceso de retorno del mercado sobre la tasa libre de riesgo.
- **Tamaño (SMB - Small Minus Big):** Históricamente, las empresas pequeñas tienden a superar a las grandes.
- **Valor (HML - High Minus Low):** Empresas con alta relación valor contable/precio suelen tener mayores retornos esperados.
- **Rentabilidad (RMW - Robust Minus Weak):** Empresas con utilidades operativas robustas superan a las de utilidades débiles.
- **Inversión (CMA - Conservative Minus Aggressive):** Empresas que invierten de forma conservadora suelen superar a las que invierten agresivamente.
- **Momentum (MOM):** La tendencia de los activos que han rendido bien recientemente a seguir rindiendo bien en el corto plazo.

**Referencia:** Fama, E. F., & French, K. R. (2015). "A five-factor asset pricing model". Journal of Financial Economics.

### B. Momentum Domenec (Túneles y Fuerza)
Complementamos el análisis fundamental con un filtro de ejecución basado en:
- **Túneles Domenec:** Basado en medias móviles dinámicas y desviaciones estándar para identificar zonas de sobrecompra/sobreventa y quiebres de tendencia.
- **Indicador de Fuerza:** Cuantificación de la volatilidad relativa para evitar falsos quiebres.

### C. Optimización Black-Litterman
A diferencia de la Optimización de Media-Varianza tradicional (Markowitz), el modelo Black-Litterman utiliza:
1. **Equilibrio de Mercado:** Un punto de partida basado en la capitalización de mercado.
2. **Opiniones del Modelo (Views):** Los resultados del Screener se incorporan como "visiones" cuantitativas para ajustar los pesos del equilibrio hacia los activos con mayor score de factores.

## 3. Descripción de Procesos

| Proceso | Objetivo Científico | Salida Principal |
| :--- | :--- | :--- |
| **Screener Fundamental** | Normalizar (Z-Score) los indicadores financieros por sector para identificar activos infravalorados y de alta calidad. | `Ranking_Global_Top.xlsx` |
| **Detección de Momentum** | Asegurar que el activo seleccionado esté en una fase de tendencia alcista confirmada, evitando "atrapadas" en caídas libres. | Reporte de Semáforos |
| **Backtest Engine** | Validar la robustez de la estrategia mediante simulación histórica, considerando costos de transacción y deslizamiento (slippage). | Curva de Equity / Métricas de Riesgo |
| **Optimizador de Cartera** | Generar una asignación de capital que minimice el error de seguimiento respecto al mercado mientras maximiza la exposición a los factores ganadores. | Pesos de Cartera (%) |

## 4. Limitaciones y Supuestos
- **Supuesto de Estacionariedad:** El modelo asume que las primas de riesgo de los factores se mantendrán en el futuro.
- **Liquidez:** El modelo asume que se puede operar al precio de cierre, lo cual puede variar en mercados de baja liquidez como el local (Argentina).
- **Datos Point-in-Time:** El backtest intenta evitar el sesgo de supervivencia y de mirada al futuro (look-ahead bias) utilizando datos históricos según estaban disponibles en el momento.

---
*Documento generado bajo el Artículo 8 de la Constitución Antigravity.*
