# Notas para el Desarrollador

## Estado Actual
Se ha implementado una arquitectura de recolección de datos resiliente para mitigar los bloqueos (429 Too Many Requests) de Yahoo Finance. 

### Mejoras Implementadas:
1. **Sesiones con User-Agent Realistas**: Se utiliza la librería `requests.Session` inyectada en `yfinance` con headers que rotan entre varios User-Agents (incluyendo el del propietario).
2. **Descarga en Lotes de Tamaño 1**: Se ha reducido el `batch_size` a 1 en `DBManager` para minimizar la ráfaga de peticiones.
3. **Delays Adaptativos**: Se incorporaron pausas aleatorias de entre 2.5 y 4.5 segundos entre cada ticker y una rotación completa de sesión cada 10 tickers.
4. **Resiliencia en Benchmarks**: El script `screener_fundamental.py` ahora maneja fallos en la descarga de benchmarks sin detener el proceso completo, intentando alinear con los datos disponibles.

## Problema Pendiente: Yahoo Finance Rate Limit
A pesar de las optimizaciones, Yahoo Finance sigue aplicando límites estrictos para grandes universos de activos (como el de Argentina completo).

### Recomendaciones para el Siguiente Desarrollador:
- **Proxy Rotation**: Integrar un servicio de proxies rotativos en `_get_session` de `DBManager`.
- **Fuentes Alternativas**: Evaluar el uso de APIs pagas o fuentes como *Financial Modeling Prep* o *Alpha Vantage* para los fundamentales y precios históricos.
- **Persistent Cache**: La base de datos DuckDB ya funciona como caché, pero se podría mejorar la lógica de reintentos para que el script pueda ser ejecutado en ráfagas espaciadas (ej. mediante un cronjob cada 1 hora hasta completar el universo).
- **Paralelización con Precaución**: Actualmente se desactivaron los threads (`threads=False`) para evitar bloqueos rápidos. No reactivar sin proxies.

---
*Nota: Git inicializado localmente. Subir a GitHub una vez vinculado el remote origin.*
