# Metodología Científica — Modelo Fama-French 6 + Optimizador Tres Pilares

*Versión: Mar-2026 · Bajo los Artículos 2, 4, 7 y 8 de la Constitución Antigravity.*

---

## 1. Objetivo y Filosofía del Sistema

El sistema implementa una gestión de cartera **"Quantamental"**: cuantitativa en la selección de activos, y macroeconómicamente consciente en la asignación de capital entre los tres pilares fundamentales:

1. **Renta Variable Local** (acciones argentinas)
2. **Renta Variable Global** (acciones internacionales SEC e INTL)
3. **Renta Fija Local** (bonos en USD Hard Dollar y en ARS — LECAPs, CER)

El principio rector es que **nunca ningún pilar tiene 0% de exposición**, garantizando diversificación mínima a nivel institucional (Black-Litterman, 1992; Solnik, 1974).

---

## 2. Fundamentos Teóricos

### A. Modelo de 6 Factores de Fama-French
El núcleo de selección de activos es la extensión del modelo de 3 factores de Fama y French (2015).

**Factores modelados:**
- **Mercado (Mkt-RF):** Prima de riesgo sistémico.
- **Tamaño (SMB):** Small Minus Big — empresas chicas vs. grandes.
- **Valor (HML):** High Minus Low — empresas baratas (book/price alto) vs. caras.
- **Rentabilidad (RMW):** Robust Minus Weak — alta rentabilidad operacional.
- **Inversión (CMA):** Conservative Minus Aggressive — empresas que invierten conservadoramente.
- **Momentum (MOM):** Factor de continuación de tendencia (Jegadeesh & Titman, 1993).

**Referencia principal:** Fama, E.F. & French, K.R. (2015). *"A five-factor asset pricing model."* Journal of Financial Economics, 116(1), 1–22.

---

### B. Yield Gap y Prima de Riesgo Argentina

El modelo evalúa el atractivo relativo de la renta variable frente a la renta fija mediante el **Yield Gap**:

```
Tasa de Descuento Local  = TIR_bono_soberano_líquido  (AL30 o AE38 desde Screenermatic)
Yield Gap Local          = (E/P de RV local)  - Tasa de Descuento Local
Yield Gap Global         = (E/P de RV global) - Tasa de Descuento Local
```

- Si `Yield Gap > 0`: La renta variable paga más que los bonos → favorece equity.
- Si `Yield Gap < 0`: Los bonos son más atractivos → defense en renta fija.

**Supuesto:** La TIR del bono soberano líquido (AL30/AE38) ya contiene matemáticamente el Riesgo País (EMBI+), por lo que actúa como benchmark local de tasa libre de riesgo ajustada.

**Referencia:** Yardeni, E. (1997). *"Fed's Stock Market Model."* & Estrada, J. (2000). *"The Cost of Equity in Emerging Markets: A Downside Risk Approach."*

---

### C. Detección de Crisis Sistémica

Tres señales macroeconómicas modulan el peso en renta variable:

| Señal | Nivel 0 | Nivel 1 | Nivel 2 | Fuente |
|---|---|---|---|---|
| **Inversión Curva (10Y-2Y)** | Normal | Plana | Invertida | Estrella & Mishkin (1998) |
| **US High Yield Spread** | Normal | Elevado | Crítico | Gilchrist & Zakrajšek (2012) |
| **VIX** | < 20 | 20–30 | > 30 | Black (1976); GARCH asimétrico |

La probabilidad de crisis se estima con ponderación académica:
```python
P_crisis = Σ (señal_i / 2) × peso_i
# pesos: Curva=0.45, HY=0.35, VIX=0.20
```
Un `P_crisis` elevado penaliza ambos pilares de renta variable (castigo no lineal: `P^1.5`).

---

### D. Indicador Adelantado de Divergencia EMBI vs. Merval

**Fundamento:** El precio de los activos argentinos en USD (proxeado por GGAL ADR) debe moverse en correlación inversa fuerte con el Riesgo País (EMBI+). Cuando esta correlación se rompe durante ventanas prolongadas (1 año), configura una **divergencia estructural** que suele anticipar un movimiento de ajuste en la dirección de la señal del bono.

**Ventana de análisis: 1 año** (evita el ruido de señales de 30 días).

| Divergencia | Condición | Interpretación | Ajuste táctico |
|---|---|---|---|
| **Alcista Estructural** | EMBI cae >20% y GGAL <+5% | RV local rezagada, convergencia pendiente | +15% en RV Local |
| **Bajista Estructural** | GGAL sube >30% y EMBI sube >5% | Burbuja de RV local no respaldada por fundamentos soberanos | -20% en RV Local |
| **Neutral** | Correlación histórica | Sin señal | Sin ajuste |

**Fuente de datos:** Historial EMBI+ desde 1999 (API Ámbito) persistido en DuckDB. GGAL (Yahoo Finance, período `1y`).

---

### E. Selección de Renta Fija — Criterios Multi-Métrica

Se abandonó la selección exclusiva por TIR en favor de un enfoque de **tres métricas simultáneas** (Jorion, 2007):

| Métrica | Umbral Hard Dollar | Umbral ARS/CER | Rol |
|---|---|---|---|
| **TIR** | ≥ 7% | ≥ 2% real | Rendimiento mínimo atractivo |
| **Modified Duration (MD)** | < 5 años | < 5 años | Control de riesgo de tasa de interés |
| **Paridad** | < 100% | < 110% | Descuento respecto al valor nominal |

Solo los bonos que superan los 3 filtros simultáneamente reciben el marcador ⭐ y son elegibles para la cartera.

**Referencia:** Jorion, P. (2007). *"Value at Risk: The New Benchmark for Managing Financial Risk."* McGraw-Hill, Cap. 5.

---

### F. Mezcla USD vs. ARS — Ajuste Macro por Imagen Presidencial

La proporción entre renta fija en dólares (Hard Dollar) y en pesos (LECAPs, BONCERs CER) se determina dinámicamente por el **índice de confianza/imagen positiva del gobierno**:

```
peso_relativo_pesos  = confianza_gobierno / 100
peso_relativo_hd     = 1 - peso_relativo_pesos
```

**Fundamento económico:** En regímenes de ajuste fiscal ortodoxo (Escuela Austríaca), una alta confianza en la continuidad de políticas de ancla cambiaria y superávit fiscal reduce el riesgo de devaluación, haciendo al **Carry Trade en pesos** (LECAPs/CER) más atractivo que el Hard Dollar.

**Supuesto de validez:** El ajuste es válido mientras la política macroeconómica se mantenga sin cambio de régimen. Ante una señal de cambio (pérdida electoral, cambio de ministro clave), reconfigurar `confianza_gobierno` con un valor menor.

**Configuración actual:** `confianza_gobierno = 56.0` (imagen positiva de Mar-2026).

---

### G. Pisos Mínimos de Diversificación — Anti-Concentración

**Fundamento:** Solnik (1974) mostró matemáticamente que la diversificación internacional reduce la varianza del portafolio incluso cuando los retornos esperados de los activos globales son menores que los locales. French & Poterba (1991) documentaron el sesgo de "Home Bias" como una fuente sistemática de riesgo no compensado.

| Pilar | Piso Mínimo | Justificación |
|---|---|---|
| RV Local | 15% | Participación mínima en el ciclo económico local |
| RV Global | 15% | Diversificación de riesgo país obligatoria (Solnik, 1974) |
| RF Local | 20% | Amortiguador mínimo institucional (Markowitz, 1952) |

El excedente (45% restante sobre los pisos) se distribuye dinámicamente según el Yield Gap diferencial.

---

## 3. Fuentes de Datos

| Fuente | Datos | Frecuencia |
|---|---|---|
| **Screenermatic** (scraping) | TIR, MD, Convexidad, Paridad de bonos | Diaria (caché DuckDB) |
| **Yahoo Finance** (yfinance) | Cotizaciones, fundamentales, P/E, ADRs | Tiempo real |
| **FRED API** | Treasury 10Y, High Yield Spread, VIX | Diaria |
| **Ámbito Financiero** (API) | EMBI+ Argentina histórico desde 1999 | Diaria (caché DuckDB) |
| **Docta Capital API** | Fallback para datos de bonos | Semanal (caché DuckDB) |

---

## 4. Limitaciones y Supuestos

- **Muestra mínima:** n ≥ 30 observaciones para todos los tests estadísticos.
- **Normalidad de retornos:** Validada con Shapiro-Wilk (n<50) o Kolmogorov-Smirnov (n>50). En caso de fallo, se usa distribución empírica no paramétrica.
- **Riesgo de régimen:** El modelo asume continuidad de la política fiscal y monetaria actual. Un cambio de régimen (p. ej. regreso al cepo, default soberano, cambio de gobierno) requiere recalibración manual.
- **Screenermatic:** Los datos de bonos dependen de la validez de la sesión PHPSESSID. Si expira, el sistema sirve datos del caché DuckDB (potencialmente desactualizados).
- **GGAL como proxy del Merval:** Se usa GGAL (ADR NYSE) como proxy del mercado local en dólares por su alta liquidez y correlación con el índice general. No capta activos sin ADR.

---

## 5. Tabla de Procesos

| Proceso | Script | Objetivo | Salida |
|---|---|---|---|
| Screener Fama-French | `screener_fundamental.py` | Z-Score de 6 factores | Rankings Excel |
| Indicadores de Crisis | (interno en allocation) | P(Crisis) sistémica | Señal 0-2 |
| Divergencia EMBI/Merval | `analizar_divergencia_merval_embi()` | Señal táctica 1 año | ±impacto_rv |
| Allocation Tres Pilares | `allocation_tres_pilares.py` | Pesos óptimos | `Portfolio_Recommendation.csv` |
| Descarga EMBI+ histórico | `historico_embi.py` | Serie temporal EMBI | `docta_cache.duckdb` |
| Scraping bonos | `scraping_screenermatic.py` | Métricas RF | `docta_cache.duckdb` |

---

*Referencias completas disponibles en los docstrings de cada función del código fuente.*
