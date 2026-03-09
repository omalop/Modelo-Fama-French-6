# Modelo Fama-French 6 + Optimizador Tres Pilares

Sistema de inversión **Quantamental** de clase institucional para el mercado argentino e internacional. Combina el rigor académico del **Modelo de 6 Factores de Fama-French** con análisis macroeconómico dinámico, un **Optimizador de Tres Pilares** y gestión avanzada de renta fija bajo el régimen macro de Milei.

---

## 🚀 Características Principales

- **Screener Fama-French**: Ranking global de activos por Z-Score de factores (Value, Profitability, Investment, Size, Momentum).
- **Tres Pilares de Allocation**: RV Local (Argentina) + RV Global (SEC/INTL) + RF Local (USD y ARS). Nunca menos del 15% en RV Local, 15% en RV Global, ni menos del 20% en Renta Fija.
- **Indicador Adelantado EMBI vs. Merval**: Detecta divergencias estructurales a 1 año entre el Riesgo País (EMBI+) y el mercado accionario local (proxy GGAL) para anticipar correcciones o rallies de RV.
- **Renta Fija Mixta (USD + ARS)**: Combina bonos soberanos Hard Dollar (AL30, AE38), subsoberanos (NDT25), corporativos (YFC2O) y renta en pesos (LECAPs, BONCERs CER) con selección por TIR, Modified Duration, Convexidad y Paridad.
- **Ajuste por Imagen Presidencial**: El peso relativo de la renta fija en pesos vs. dólares se calibra dinámicamente según el índice de confianza/imagen del gobierno (política económica vigente).
- **EMBI+ Histórico**: Base de datos DuckDB con 26 años de historial del Riesgo País (desde 1999). Se actualiza automáticamente con caché diario.
- **Timing Técnico Domenec**: Indicador de "Túnel Domenec" y fuerza de tendencia para filtrar activos en corrección.
- **Backtesting Engine**: Simulación histórica de la estrategia para validar robustez.

---

## 📁 Estructura del Proyecto

```text
Modelo Fama-French 6/
├── config/
│   ├── ticker.txt              # Lista de activos globales
│   └── ticker_arg.txt          # Lista de activos argentinos
├── data/
│   ├── raw/                    # Datos crudos INMUTABLES
│   ├── interim/                # Transformaciones intermedias
│   ├── processed/              # Rankings y cartera final (Excel)
│   └── docta_cache.duckdb      # Base de datos de caché (bonos + EMBI+)
├── docs/
│   ├── metodologia.md          # Fundamento científico del modelo
│   └── manual_usuario.md       # Guía de uso completa
├── src/
│   ├── data/
│   │   ├── scraping_screenermatic.py   # Scraper de bonos (TIR, MD, CVX, Paridad)
│   │   ├── historico_embi.py           # Descarga y caché del EMBI+ histórico
│   │   ├── docta_api.py                # API Docta Capital (fallback)
│   │   └── cache_docta.py              # Caché DuckDB para Docta
│   ├── models/
│   │   ├── screener_fundamental.py     # Screener Fama-French 6 factores
│   │   ├── allocation_tres_pilares.py  # Motor central de allocation
│   │   ├── optimizador_cartera.py      # Optimizador Black-Litterman
│   │   └── backtest_quantamental.py    # Motor de backtesting
│   └── utils/
│       └── git_sync.py                 # Sincronización GitHub automática
├── logs/                        # Registros de ejecución
├── requirements.txt             # Versiones exactas de librerías
└── .env                         # Credenciales (NO commitear)
```

---

## ⚙️ Configuración Inicial

### 1. Variables de Entorno (`.env`)
Crear un archivo `.env` en la raíz del proyecto con las siguientes claves:
```env
FRED_API_KEY=tu_clave_fred
SCREENERMATIC_PHPSESSID=tu_sesion_screenermatic
```
> **Nota:** El `PHPSESSID` de Screenermatic expira periódicamente. Renovarlo desde el navegador al detectar el error `PAYWALL detectado`.

### 2. Lista de Activos
Editar `config/ticker.txt` con los símbolos de Yahoo Finance deseados (uno por línea o separados por comas).

---

## 🖥️ Ejecución

### Pipeline Completo Recomendado

```bash
# 1. Screener fundamental (genera rankings)
python src/models/screener_fundamental.py

# 2. Allocation y cartera final (motor central)
python src/models/allocation_tres_pilares.py
```

> La ejecución del motor central **no requiere input interactivo**. La imagen presidencial está preconfigurada en el código (`confianza_gobierno = 56.0`). Actualizar ese valor según las encuestas vigentes.

### Módulos Individuales

| Módulo | Comando | Salida |
|---|---|---|
| Screener Global (SEC) | `python src/models/screener_fundamental.py` | `Ranking_Global_SEC_Top.xlsx` |
| Screener Argentina | `python src/models/screener_fundamental.py` | `Ranking_Argentina_Top.xlsx` |
| Allocation Tres Pilares | `python src/models/allocation_tres_pilares.py` | `Portfolio_Recommendation.csv` |
| Actualizar EMBI+ Histórico | `python src/data/historico_embi.py` | `docta_cache.duckdb` |
| Scraping Screenermatic | `python src/data/scraping_screenermatic.py` | `docta_cache.duckdb` |
| Sync GitHub | `python src/utils/git_sync.py` | — |

---

## 📊 Lógica del Optimizador Tres Pilares

El motor `allocation_tres_pilares.py` sigue la siguiente secuencia de pasos:

```
[1] Datos frescos ──► Screenermatic (bonos) + EMBI+ Histórico (Riesgo País)
[2] Rankings ────────► Ranking_Global_SEC_Top + Ranking_Global_Intl_Top + Ranking_Argentina_Top
[3] Valoración ──────► P/E_local, P/E_global, Tasa de Descuento (bonos soberanos)
[4] Señales MacRo ───► Yield Gaps + Crisis Signals (VIX, HY Spread, Curva 10Y-2Y)
[4.5] Divergencia ───► EMBI vs. GGAL (ventana 1 año estructural)
[5] Allocation ──────► Tres Pilares con pisos mínimos garantizados
[6] Renta Fija ──────► Selección por TIR/MD/CVX/Paridad + mezcla USD/ARS por imagen presidencial
[7] Exportar ────────► Portfolio_Recommendation.csv
```

### Pisos Mínimos (Anti-Concentración)
| Pilar | Mínimo | Máximo Dinámico |
|---|---|---|
| RV Local (Argentina) | **15%** | ~65% (con Divergencia Alcista) |
| RV Global (SEC+INTL) | **15%** | 40% |
| RF Local (USD + ARS) | **20%** | ~70% (en crisis sistémica) |

### Indicador Adelantado de Divergencia EMBI vs. Merval
| Señal | Condición | Efecto en Cartera |
|---|---|---|
| **Divergencia Alcista Estructural** | EMBI cae >20% anual y GGAL rezagada (<5%) | +15% en RV Local |
| **Divergencia Bajista Estructural** | GGAL sube >30% y EMBI sube >5% | -20% en RV Local |
| **Neutral** | Correlación histórica normal | Sin ajuste táctico |

---

## 📈 Interpretación de Rankings

Los reportes generan **Z-Scores** por sector para comparar activos de la misma industria:

- **FF Score > 1.0**: Activo de alta calidad cuantitativa → candidato fuerte.
- **FF Score 0.5–1.0**: Calidad moderada → evaluar junto al timing técnico.
- **FF Score < 0**: Activo por debajo del promedio de su sector.
- **⭐ en Renta Fija**: Bono que cumple los 3 criterios: TIR ≥ 7% (o ≥ 2% para ARS), MD < 5, Paridad < 100% (o < 110% para ARS).

---

## ⚠️ Disclaimer

**Este software es para fines educativos y de investigación exclusivamente.**
No constituye una recomendación de inversión, compra o venta de activos financieros. Los rendimientos pasados no garantizan rendimientos futuros. El autor no se hace responsable por pérdidas financieras derivadas del uso de este código.

---
*Desarrollado con asistencia de Antigravity AI · Versión: Mar-2026*
