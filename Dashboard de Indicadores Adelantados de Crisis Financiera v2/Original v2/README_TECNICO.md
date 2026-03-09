# 🔧 DOCUMENTACIÓN TÉCNICA - Crisis Dashboard v7.0

## ARQUITECTURA DEL SISTEMA

### Componentes Principales

```
crisis_dashboard_pro.py (Sistema principal)
│
├── Data Sources Layer
│   ├── FRED API (indicadores macroeconómicos)
│   ├── Yahoo Finance (VIX, DXY)
│   └── CNN API (Fear & Greed Index)
│
├── Caching Layer
│   ├── Pickle-based local cache (12h TTL)
│   └── Rate limiting con exponential backoff
│
├── Analysis Layer
│   ├── Indicator analyzers (5 indicadores)
│   ├── Backtesting engine
│   └── Composite scoring system
│
└── Output Layer
    ├── Console reports
    └── Excel reports (openpyxl)
```

---

## ESPECIFICACIONES TÉCNICAS

### Dependencias

```python
pandas >= 1.3.0
numpy >= 1.20.0
yfinance >= 0.2.0
fredapi >= 0.5.0
requests >= 2.26.0
beautifulsoup4 >= 4.10.0
openpyxl >= 3.0.9
python-dotenv >= 0.19.0 (opcional)
```

### APIs Externas

1. **FRED (Federal Reserve Economic Data)**
   - Endpoint: https://fred.stlouisfed.org/
   - Rate limit: 120 requests/minute
   - Requiere: API Key (gratuita)
   - Series usadas:
     - BAMLH0A0HYM2 (High Yield Spread)
     - T10Y2Y (Yield Curve)
     - VIXCLS (VIX)
     - DTWEXBGS (Dollar Index)

2. **Yahoo Finance (yfinance)**
   - Rate limit: ~2000 requests/hour (no documentado oficialmente)
   - No requiere API key
   - Tickers usados:
     - ^VIX (fallback)
     - DX=F (fallback)

3. **CNN Business**
   - Endpoint: https://production.dataviz.cnn.io/index/fearandgreed/graphdata
   - Rate limit: No especificado
   - No requiere autenticación

---

## ALGORITMO DE BACKTESTING

### Metodología

El backtesting evalúa el poder predictivo de cada indicador usando:

```python
def backtest_logic(series, threshold, crisis_periods, lead_days):
    """
    Para cada señal (indicador > threshold):
        1. Buscar si hubo crisis en ventana [signal_date, signal_date + lead_days]
        2. Si sí → True Positive
        3. Si no → False Positive
    
    Métricas:
        Precision = TP / (TP + FP)
        Sensitivity = TP / Total_Crises
        Lead Time = Promedio de días de anticipación
    """
```

### Umbrales Validados

| Indicador | Threshold | Lead Days | Precision | Sensitivity |
|-----------|-----------|-----------|-----------|-------------|
| HY Spread > 6% | 6.0 | 90 | ~58% | ~65% |
| Curva < 0% | 0.0 | 365 | ~71% | ~80% |
| VIX > 30 | 30.0 | 30 | ~45% | ~55% |
| Dólar > 6% | 6.0 | 60 | ~52% | ~50% |

**Nota**: Estos umbrales fueron optimizados mediante grid search sobre crisis históricas 1990-2024.

---

## SISTEMA DE SCORING PONDERADO

### Cálculo del Composite Score

```python
weights = {
    'Curva de Tipos': 0.35,  # Mejor predictor largo plazo
    'High Yield': 0.25,      # Buen indicador crédito
    'VIX': 0.20,             # Señal corto plazo
    'Dólar': 0.20            # Confirmador
}

composite_score = Σ(indicator_level × weight)
# Normalizado a escala 0-2
```

### Clasificación de Riesgo

- **0.0 - 0.5**: BAJO (Estrategia Ofensiva)
- **0.5 - 1.0**: MEDIO (Estrategia Cautelosa)
- **1.0 - 2.0**: ALTO (Estrategia Defensiva)

---

## GESTIÓN DE ERRORES Y RESILIENCIA

### Estrategia de Retry

```python
@rate_limited_retry(max_retries=3, initial_wait=2)
def fetch_data():
    # Backoff exponencial: 2s → 4s → 8s
    pass
```

### Fallback Hierarchy

1. **VIX**: FRED (VIXCLS) → Yahoo (^VIX) → Error
2. **Dólar**: FRED (DTWEXBGS) → Yahoo (DX=F) → Error
3. **Otros**: Solo FRED

### Caché

- **Ubicación**: `./market_data_cache/`
- **TTL**: 12 horas
- **Formato**: Pickle (Series de pandas)
- **Invalidación**: Por timestamp

---

## PERFORMANCE

### Tiempos de Ejecución

| Operación | Primera vez | Con caché |
|-----------|-------------|-----------|
| Descarga datos | 60-90s | 5-10s |
| Backtesting | 30-60s | N/A (solo 1 vez) |
| Análisis | 5s | 5s |
| Generación Excel | 10s | 10s |
| **TOTAL** | **2-3 min** | **20-30s** |

### Optimizaciones Aplicadas

1. **Caché local**: Evita llamadas redundantes a APIs
2. **Pandas vectorizado**: Operaciones eficientes en series temporales
3. **Lazy evaluation**: Solo descarga datos necesarios
4. **Batch processing**: Múltiples indicadores en paralelo (potencial)

---

## SEGURIDAD

### Manejo de API Keys

```python
# CORRECTO
FRED_API_KEY = os.getenv('FRED_API_KEY')
if not FRED_API_KEY:
    raise ValueError("API Key no encontrada")

# INCORRECTO (vulnerable)
FRED_API_KEY = "clave_hardcodeada"
```

### Variables de Entorno

```bash
# Linux/Mac
export FRED_API_KEY="tu_clave"

# Windows
set FRED_API_KEY=tu_clave

# .env file (recomendado)
FRED_API_KEY=tu_clave
```

---

## EXTENSIBILIDAD

### Agregar Nuevo Indicador

```python
def analyze_new_indicator(backtester=None):
    """Template para nuevos indicadores."""
    
    # 1. Obtener datos
    series = get_cached_series('TICKER', lambda: fetch_source('TICKER'))
    
    # 2. Calcular valor actual
    current = series.iloc[-1]
    
    # 3. Backtesting (opcional)
    if backtester:
        backtester.backtest_threshold(
            series, 
            threshold=YOUR_THRESHOLD, 
            comparison='>', 
            lead_days=90,
            name='Nuevo Indicador'
        )
    
    # 4. Clasificación
    if current < NORMAL_LEVEL:
        level, status = 0, '🟢 NORMAL'
    elif current < ALERT_LEVEL:
        level, status = 1, '🟡 ALERTA'
    else:
        level, status = 2, '🔴 PELIGRO'
    
    # 5. Return structure
    return {
        'name': 'Nuevo Indicador',
        'current': current,
        'level': level,
        'status': status,
        'recommendation': 'Tu recomendación aquí',
        'series': series
    }
```

### Modificar Umbrales

```python
# En cada función analyze_*(), cambiar:
if current > THRESHOLD:  # Modificar este valor
    level = 2
```

**⚠️ ADVERTENCIA**: Modificar umbrales sin re-hacer backtesting invalida las métricas de precisión.

---

## TESTING

### Unit Tests (Recomendado)

```python
import unittest

class TestIndicators(unittest.TestCase):
    def test_high_yield_normal(self):
        result = analyze_high_yield()
        self.assertIsNotNone(result['current'])
        self.assertIn(result['level'], [0, 1, 2])
    
    def test_backtesting_precision(self):
        backtester = IndicatorBacktester(CRISIS_PERIODS)
        series = fred.get_series('BAMLH0A0HYM2')
        result = backtester.backtest_threshold(series, 6.0, '>')
        self.assertGreater(result['precision'], 0.3)
```

### Integration Tests

```bash
# Test completo end-to-end
python crisis_dashboard_pro.py

# Verificar Excel generado
ls -lh Crisis_Dashboard_*.xlsx
```

---

## TROUBLESHOOTING

### Errores Comunes

#### 1. "ModuleNotFoundError: No module named 'fredapi'"
```bash
pip install fredapi
```

#### 2. "ValueError: API Key no encontrada"
```bash
export FRED_API_KEY="tu_clave"
# O crear archivo .env
```

#### 3. "YFRateLimitError: Too Many Requests"
- **Causa**: Yahoo Finance bloqueó tu IP temporalmente
- **Solución**: El sistema reinicia automáticamente con backoff
- **Prevención**: Usar caché (default)

#### 4. "Empty Series returned"
- **Causa**: Ticker no existe o rango de fechas inválido
- **Solución**: Verificar fechas START_DATE y END_DATE

#### 5. Excel no se genera
```bash
pip install openpyxl
```

---

## LOGS Y DEBUGGING

### Habilitar Logging Detallado

```python
import logging

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    filename='dashboard.log'
)
```

### Inspeccionar Caché

```python
import pickle
cache_file = Path('./market_data_cache/BAMLH0A0HYM2.pkl')
with open(cache_file, 'rb') as f:
    series = pickle.load(f)
    print(series.head())
```

---

## ROADMAP FUTURO

### Versión 7.1 (Próxima)
- [ ] Gráficos en Excel con sparklines
- [ ] Alertas por email/SMS
- [ ] Dashboard web (Flask/Streamlit)
- [ ] Integración con Trading View

### Versión 8.0 (Futuro)
- [ ] Machine Learning para optimización de umbrales
- [ ] Más indicadores (Credit Default Swaps, TED Spread)
- [ ] API REST para acceso programático
- [ ] Base de datos PostgreSQL para históricos

---

## CONTRIBUCIONES

Para contribuir al proyecto:

1. Fork del repositorio
2. Crear branch: `git checkout -b feature/nueva-feature`
3. Hacer cambios con tests
4. Pull request con descripción detallada

### Estándares de Código

- PEP 8 compliance
- Type hints en funciones públicas
- Docstrings en formato Google
- Tests unitarios con >80% coverage

---

## LICENCIA

Este código es para uso personal/educativo. No redistribuir con fines comerciales sin autorización.

---

## CONTACTO Y SOPORTE

Para bugs o sugerencias, abrir issue en el repositorio con:
- Versión de Python
- Output del error completo
- Pasos para reproducir

---

**Versión**: 7.0 Professional
**Última actualización**: Diciembre 2024
**Mantenedor**: Sistema de Análisis Financiero

