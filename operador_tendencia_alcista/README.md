# Sistema de Trading Algorítmico - Operador de Tendencia Alcista

## Descripción
Este sistema implementa un operador algorítmico basado en la metodología de análisis técnico de "BLOGdeDOC", enfocada en la Ley de Oferta y Demanda, desequilibrio de liquidez y estructura de mercado fractal.

**Principios Clave:**
- **Inoperatividad Estratégica**: El sistema permanece en STANDBY el 84% del tiempo, operando solo impulsos alineados.
- **Cotas Históricas**: Muros de contención validados por recurrencia temporal.
- **Túnel Domènec**: Indicador propietario para identificar zonas de corrección y tendencias.
- **Gestión de Riesgo**: Stop Loss estructural y Take Profit por cotas.

## Estructura del Proyecto

```
operador_tendencia_alcista/
├── config/             # Configuraciones y logging
├── data/               # Datos jerárquicos (Raw, Interim, Processed)
├── src/
│   ├── data/           # Repositorios e Ingesta
│   ├── indicadores/    # Túnel Domènec, Cotas, Gann
│   ├── estructura/     # Análisis de Tendencia y Fractalidad
│   ├── senales/        # Lógica de Entrada (Triggers)
│   ├── gestion/        # Lógica de Salida y Position Sizing
│   └── visualizacion/  # Gráficos de Cotas
├── tests/              # Tests Unitarios e Integración
└── docs/               # Documentación Metodológica
```

## Instalación

1. Crear entorno virtual:
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   venv\Scripts\activate     # Windows
   ```

2. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

## Uso

(Pendiente de implementación)
