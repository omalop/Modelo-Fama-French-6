"""
SISTEMA DE 5 PASOS - HIGH YIELD SPREAD
Metodología avanzada del Usuario (Dic 2024)

OBJETIVO: Detectar crisis en desarrollo para SALIR/MANTENERSE FUERA del mercado

Este módulo puede reemplazar la función analyze_high_yield() en crisis_dashboard_pro.py
"""

import pandas as pd
import numpy as np


def analyze_high_yield_5_pasos(series, backtester=None):
    """
    Análisis de High Yield Spread con metodología de 5 pasos.
    
    OBJETIVO: Sistema de SALIDA - Detectar crisis para mantenerse FUERA
    
    FORMACIÓN DE CRISIS (Pasos 1-4):
    ═══════════════════════════════════════════════════════════
    1️⃣ Spread ≥ 7.30%
    2️⃣ Dispersión máxima > +15% en últimas 30 ruedas
    3️⃣ Spread cruza WWMA-8 DE ARRIBA HACIA ABAJO
       → Señal: "CRISIS EN DESARROLLO, MANTENERSE FUERA"
    4️⃣ Dos mínimos decrecientes consecutivos después del cruce
       → Señal: "ADVERTENCIA: Crisis desarrollándose"
    
    CRISIS CONFIRMADA (Paso 5):
    ═══════════════════════════════════════════════════════════
    5️⃣ Todos los anteriores + Dispersión vs MA-34 ≤ 0%
       → Señal: "CRISIS CONFIRMADA - ¡NO INVERTIR!"
    
    Ejemplo: COVID Marzo 2020
    ─────────────────────────────────────────────────────────
    1. Spread llegó a 10%                    ✓
    2. Dispersión llegó a +97%               ✓
    3. Cruce WWMA-8: 26 Marzo 2020           ✓ → CRISIS EN DESARROLLO
    4. Mínimos decrecientes post-cruce       ✓ → ADVERTENCIA
    5. Cruce MA-34: 13 Abril 2020            ✓ → ¡NO INVERTIR!
    """
    
    if series.empty:
        return None
    
    # =========================================================================
    # CÁLCULO DE INDICADORES TÉCNICOS
    # =========================================================================
    
    current = series.iloc[-1]
    last_date = series.index[-1]
    
    # MA-34 días (Simple Moving Average)
    ma_34 = series.rolling(window=34).mean()
    current_ma_34 = ma_34.iloc[-1] if len(ma_34) >= 34 else current
    
    # WWMA-8 (Welles Wilder Moving Average)
    # WWMA es un EMA con alpha = 1/period
    wwma_8 = series.ewm(alpha=1/8, adjust=False).mean()
    current_wwma_8 = wwma_8.iloc[-1] if len(wwma_8) >= 8 else current
    
    # Dispersión vs MA-34
    dispersion_ma34 = ((series - ma_34) / ma_34) * 100
    current_dispersion = dispersion_ma34.iloc[-1] if not dispersion_ma34.empty else 0
    
    # =========================================================================
    # DETECCIÓN DE CRISIS - METODOLOGÍA DE 5 PASOS
    # =========================================================================
    
    def detect_crisis_5_steps(series, ma_34, wwma_8, dispersion_ma34):
        """
        Detecta fechas donde se confirma crisis según los 5 pasos.
        
        Returns: Lista de índices donde se confirmó crisis
        """
        crisis_indices = []
        
        # Necesitamos suficientes datos para evaluar
        start_idx = max(34, 8, 30)
        
        for i in range(start_idx, len(series)):
            # ─────────────────────────────────────────────────────────────
            # PASO 1: Spread ≥ 7.30%
            # ─────────────────────────────────────────────────────────────
            if series.iloc[i] < 7.30:
                continue
            
            # ─────────────────────────────────────────────────────────────
            # PASO 2: Dispersión máxima > +15% en últimas 30 ruedas
            # ─────────────────────────────────────────────────────────────
            if i < 30:
                continue
                
            disp_window_30 = dispersion_ma34.iloc[i-30:i+1]
            max_disp_30 = disp_window_30.max()
            
            if pd.isna(max_disp_30) or max_disp_30 <= 15:
                continue
            
            # ─────────────────────────────────────────────────────────────
            # PASO 3: Spread cruza WWMA-8 DE ARRIBA HACIA ABAJO
            # ─────────────────────────────────────────────────────────────
            # Buscar cruce más reciente en últimas 30 ruedas
            cruce_wwma_idx = None
            
            for j in range(max(start_idx, i-30), i):
                # Cruce bajista: estaba ARRIBA, ahora está ABAJO
                if (series.iloc[j] >= wwma_8.iloc[j] and 
                    series.iloc[j+1] < wwma_8.iloc[j+1]):
                    cruce_wwma_idx = j + 1
                    break  # Tomamos el primer cruce
            
            if cruce_wwma_idx is None:
                continue
            
            # ─────────────────────────────────────────────────────────────
            # PASO 4: Dos mínimos decrecientes consecutivos post-cruce
            # ─────────────────────────────────────────────────────────────
            # Buscar mínimos locales después del cruce (hasta 20 días después)
            minimos = []
            
            for k in range(cruce_wwma_idx + 1, min(i + 1, cruce_wwma_idx + 21)):
                # Mínimo local: menor que vecinos
                if 1 <= k < len(series) - 1:
                    if (series.iloc[k] < series.iloc[k-1] and 
                        series.iloc[k] < series.iloc[k+1]):
                        minimos.append((k, series.iloc[k]))
            
            # Verificar: al menos 2 mínimos y son decrecientes
            if len(minimos) < 2:
                continue
            
            # Verificar que los mínimos son decrecientes (cada uno menor que el anterior)
            minimos_decrecientes = True
            for j in range(len(minimos) - 1):
                if minimos[j][1] <= minimos[j+1][1]:  # No es decreciente
                    minimos_decrecientes = False
                    break
            
            if not minimos_decrecientes:
                continue
            
            # ─────────────────────────────────────────────────────────────
            # PASO 5: Dispersión vs MA-34 ≤ 0% (CRISIS CONFIRMADA)
            # ─────────────────────────────────────────────────────────────
            current_disp_i = dispersion_ma34.iloc[i]
            
            if pd.isna(current_disp_i) or current_disp_i > 0:
                continue
            
            # ✅ TODOS LOS 5 PASOS CUMPLIDOS
            crisis_indices.append(i)
        
        return crisis_indices
    
    # =========================================================================
    # EVALUAR ESTADO ACTUAL (PROGRESIVO - SECUENCIAL)
    # =========================================================================
    
    # Inicializar estados
    paso1 = False
    paso2 = False
    paso3 = False
    paso4 = False
    paso5 = False
    
    paso1_detail = ""
    paso2_detail = "⏸️  No evaluado (requiere Paso 1)"
    paso3_detail = "⏸️  No evaluado (requiere Paso 1+2)"
    paso4_detail = "⏸️  No evaluado (requiere Paso 1+2+3)"
    paso5_detail = "⏸️  No evaluado (requiere Paso 1+2+3+4)"
    
    cruce_fecha = None
    
    # ─────────────────────────────────────────────────────────────────────
    # PASO 1: Spread ≥ 7.30%
    # ─────────────────────────────────────────────────────────────────────
    paso1 = current >= 7.30
    paso1_detail = f"Spread: {current:.2f}% {'≥' if paso1 else '<'} 7.30%"
    
    if not paso1:
        # DETENER: Si Paso 1 falla, no evaluar el resto
        pasos_cumplidos = 0
    else:
        # ─────────────────────────────────────────────────────────────────
        # PASO 2: Dispersión máxima > +15% en últimas 30 ruedas
        # ─────────────────────────────────────────────────────────────────
        if len(series) >= 30:
            disp_last_30 = dispersion_ma34.tail(30)
            max_disp = disp_last_30.max()
            paso2 = max_disp > 15
            paso2_detail = f"Dispersión máx 30 ruedas: {max_disp:+.1f}% {'>' if paso2 else '≤'} +15%"
        else:
            paso2 = False
            paso2_detail = "⚠️  Insuficientes datos (< 30 ruedas)"
        
        if not paso2:
            # DETENER: Si Paso 2 falla, no evaluar 3, 4, 5
            pasos_cumplidos = 1
        else:
            # ─────────────────────────────────────────────────────────────
            # PASO 3: Spread cruza WWMA-8 DE ARRIBA HACIA ABAJO
            # ─────────────────────────────────────────────────────────────
            if len(series) >= 10:
                # Buscar cruce bajista en últimas 30 ruedas
                for j in range(max(0, len(series)-30), len(series)-1):
                    # Cruce DE ARRIBA HACIA ABAJO
                    if (series.iloc[j] >= wwma_8.iloc[j] and 
                        series.iloc[j+1] < wwma_8.iloc[j+1]):
                        paso3 = True
                        cruce_fecha = series.index[j+1]
                        paso3_detail = f"Cruce WWMA-8 (bajista) detectado: {cruce_fecha.strftime('%Y-%m-%d')}"
                        break
                
                if not paso3:
                    paso3_detail = "No se detectó cruce WWMA-8 reciente (últimas 30 ruedas)"
            else:
                paso3 = False
                paso3_detail = "⚠️  Insuficientes datos para evaluar"
            
            if not paso3:
                # DETENER: Si Paso 3 falla, no evaluar 4, 5
                pasos_cumplidos = 2
            else:
                # ─────────────────────────────────────────────────────────
                # PASO 4: Dos mínimos decrecientes consecutivos post-cruce
                # ─────────────────────────────────────────────────────────
                if cruce_fecha is not None:
                    # Buscar mínimos post-cruce
                    cruce_idx = series.index.get_loc(cruce_fecha)
                    minimos = []
                    
                    for k in range(cruce_idx + 1, min(len(series), cruce_idx + 21)):
                        if 1 <= k < len(series) - 1:
                            if (series.iloc[k] < series.iloc[k-1] and 
                                series.iloc[k] < series.iloc[k+1]):
                                minimos.append(series.iloc[k])
                    
                    if len(minimos) >= 2:
                        decrecientes = all(minimos[j] > minimos[j+1] for j in range(len(minimos)-1))
                        paso4 = decrecientes
                        paso4_detail = f"{len(minimos)} mínimos detectados, {'decrecientes ✓' if decrecientes else 'NO decrecientes ✗'}"
                    else:
                        paso4 = False
                        paso4_detail = f"Solo {len(minimos)} mínimo(s) detectado(s), se necesitan 2+"
                else:
                    paso4 = False
                    paso4_detail = "⚠️  Requiere Paso 3 cumplido"
                
                if not paso4:
                    # DETENER: Si Paso 4 falla, no evaluar 5
                    pasos_cumplidos = 3
                else:
                    # ─────────────────────────────────────────────────────
                    # PASO 5: Dispersión vs MA-34 ≤ 0%
                    # ─────────────────────────────────────────────────────
                    paso5 = current_dispersion <= 0
                    paso5_detail = f"Dispersión actual: {current_dispersion:+.1f}% {'≤' if paso5 else '>'} 0%"
                    
                    pasos_cumplidos = 5 if paso5 else 4
    
    # =========================================================================
    # CLASIFICACIÓN DE NIVEL (BASADA EN PASOS PROGRESIVOS)
    # =========================================================================
    
    if pasos_cumplidos == 5:
        level = 2
        status = '🔴 CRISIS CONFIRMADA (5/5 pasos)'
        recommendation = '¡ESTAMOS EN CRISIS CONFIRMADA! Todos los criterios cumplidos. QUEDARSE EN CASH o TREASURIES hasta señales claras de recuperación.'
    elif pasos_cumplidos == 4:
        level = 2
        status = '🔴 CRISIS EN DESARROLLO (4/5 pasos)'
        recommendation = 'CRISIS EN DESARROLLO. Mínimos decrecientes confirmados. SALIR inmediatamente de posiciones de riesgo.'
    elif pasos_cumplidos == 3:
        level = 1
        status = '🟡 CRISIS POR DESARROLLARSE (3/5 pasos)'
        recommendation = 'CRISIS POR DESARROLLARSE. Cruce bajista detectado. MANTENERSE FUERA del mercado de HY. Monitorear diariamente.'
    elif pasos_cumplidos == 2:
        level = 1
        status = '🟡 PÁNICO DETECTADO (2/5 pasos)'
        recommendation = 'Pánico confirmado en últimas 30 ruedas pero sin cruce técnico. PREPARAR salida, reducir exposición.'
    elif pasos_cumplidos == 1:
        level = 1
        status = '🟡 ALERTA TEMPRANA (1/5 pasos)'
        recommendation = 'Spread superó umbral crítico. VIGILAR de cerca evolución. Considerar reducir exposición a HY.'
    else:
        level = 0
        status = '🟢 NORMAL'
        recommendation = 'Sin señales de crisis. Condiciones normales de mercado.'
    
    # =========================================================================
    # BACKTESTING CON METODOLOGÍA DE 5 PASOS
    # =========================================================================
    
    if backtester:
        # Detectar todas las crisis históricas
        crisis_indices = detect_crisis_5_steps(series, ma_34, wwma_8, dispersion_ma34)
        
        print(f"\n🔍 DEBUG: Detectadas {len(crisis_indices)} crisis históricas con metodología 5 pasos")
        for idx in crisis_indices:
            fecha = series.index[idx]
            valor = series.iloc[idx]
            print(f"   - {fecha.strftime('%Y-%m-%d')}: Spread = {valor:.2f}%")
        
        # Crear serie binaria para backtesting
        crisis_series = pd.Series(0.0, index=series.index)
        for idx in crisis_indices:
            crisis_series.iloc[idx] = 10.0  # Marcar crisis confirmada
        
        # Ejecutar backtesting
        backtester.backtest_threshold(
            crisis_series,
            threshold=5,
            comparison='>',
            lead_days=378,  # 18 meses bursátiles
            name='High Yield - Metodología 5 Pasos (Usuario)'
        )
    
    # =========================================================================
    # OUTPUT DETALLADO
    # =========================================================================
    
    output = {
        'name': 'High Yield Spread (Metodología 5 Pasos)',
        'current': current,
        'last_date': last_date.strftime('%Y-%m-%d'),
        'ma_34': current_ma_34,
        'wwma_8': current_wwma_8,
        'dispersion': current_dispersion,
        'steps_completed': pasos_cumplidos,
        'level': level,
        'status': status,
        'recommendation': recommendation
    }
    
    # Construir status detallado
    status_detail = f"\n   📅 Último cierre: {last_date.strftime('%Y-%m-%d')}"
    status_detail += f"\n   📊 Spread: {current:.2f}%"
    status_detail += f"\n   📊 MA-34: {current_ma_34:.2f}%"
    status_detail += f"\n   📊 WWMA-8: {current_wwma_8:.2f}%"
    status_detail += f"\n   📊 Dispersión MA-34: {current_dispersion:+.1f}%"
    
    status_detail += "\n\n   ═══════════════════════════════════════════════════════"
    status_detail += "\n   ║  VALIDACIÓN PROGRESIVA DE PASOS                      ║"
    status_detail += "\n   ═══════════════════════════════════════════════════════"
    
    # PASO 1 (siempre se muestra)
    status_detail += f"\n   {'✓' if paso1 else '✗'} PASO 1: {paso1_detail}"
    
    if paso1:
        # PASO 2 (solo si Paso 1 ✓)
        status_detail += f"\n        {'✓' if paso2 else '✗'} PASO 2: {paso2_detail}"
        
        if paso2:
            # PASO 3 (solo si Paso 1+2 ✓)
            status_detail += f"\n             {'✓' if paso3 else '✗'} PASO 3: {paso3_detail}"
            
            if paso3:
                # PASO 4 (solo si Paso 1+2+3 ✓)
                status_detail += f"\n                  {'✓' if paso4 else '✗'} PASO 4: {paso4_detail}"
                
                if paso4:
                    # PASO 5 (solo si Paso 1+2+3+4 ✓)
                    status_detail += f"\n                       {'✓' if paso5 else '✗'} PASO 5: {paso5_detail}"
                else:
                    # Mostrar que Paso 5 no se evaluó
                    status_detail += f"\n                       ⏸️  PASO 5: {paso5_detail}"
            else:
                # Mostrar que Pasos 4-5 no se evaluaron
                status_detail += f"\n                  ⏸️  PASO 4: {paso4_detail}"
                status_detail += f"\n                       ⏸️  PASO 5: {paso5_detail}"
        else:
            # Mostrar que Pasos 3-5 no se evaluaron
            status_detail += f"\n             ⏸️  PASO 3: {paso3_detail}"
            status_detail += f"\n                  ⏸️  PASO 4: {paso4_detail}"
            status_detail += f"\n                       ⏸️  PASO 5: {paso5_detail}"
    else:
        # Mostrar que Pasos 2-5 no se evaluaron
        status_detail += f"\n        ⏸️  PASO 2: {paso2_detail}"
        status_detail += f"\n             ⏸️  PASO 3: {paso3_detail}"
        status_detail += f"\n                  ⏸️  PASO 4: {paso4_detail}"
        status_detail += f"\n                       ⏸️  PASO 5: {paso5_detail}"
    
    status_detail += "\n   ═══════════════════════════════════════════════════════"
    status_detail += f"\n   🎯 PASOS CUMPLIDOS (PROGRESIVOS): {pasos_cumplidos}/5"
    status_detail += "\n   ═══════════════════════════════════════════════════════"
    
    # Interpretación del nivel
    if pasos_cumplidos == 5:
        status_detail += "\n\n   🔴 INTERPRETACIÓN: ESTAMOS EN CRISIS CONFIRMADA"
        status_detail += "\n   🚫 Acción: ¡NO INVERTIR! Quedarse en CASH/Treasuries"
    elif pasos_cumplidos == 4:
        status_detail += "\n\n   🔴 INTERPRETACIÓN: CRISIS EN DESARROLLO"
        status_detail += "\n   🚫 Acción: SALIR de posiciones de riesgo AHORA"
    elif pasos_cumplidos == 3:
        status_detail += "\n\n   🟡 INTERPRETACIÓN: CRISIS POR DESARROLLARSE"
        status_detail += "\n   ⚠️  Acción: MANTENERSE FUERA, monitorear diariamente"
    elif pasos_cumplidos == 2:
        status_detail += "\n\n   🟡 INTERPRETACIÓN: PÁNICO DETECTADO (sin confirmación técnica)"
        status_detail += "\n   ⚠️  Acción: PREPARAR salida, reducir exposición"
    elif pasos_cumplidos == 1:
        status_detail += "\n\n   🟡 INTERPRETACIÓN: ALERTA TEMPRANA"
        status_detail += "\n   👁️  Acción: VIGILAR evolución, considerar reducir HY"
    else:
        status_detail += "\n\n   ✅ INTERPRETACIÓN: CONDICIONES NORMALES"
        status_detail += "\n   👍 Acción: Operar normalmente"
    
    if pasos_cumplidos >= 3:
        status_detail += f"\n   📈 Monitorear: https://www.tradingview.com/chart/?symbol=ICE:BAMLH0A0HYM2"
    
    output['status'] = status + status_detail
    
    return output


# =============================================================================
# CÓDIGO DE EJECUCIÓN PRINCIPAL
# =============================================================================

if __name__ == '__main__':
    import os
    from fredapi import Fred
    
    print("="*80)
    print("  SISTEMA DE 5 PASOS - HIGH YIELD SPREAD")
    print("  Metodología Avanzada de Detección de Crisis")
    print("="*80)
    print()
    
    # =========================================================================
    # CARGAR API KEY
    # =========================================================================
    
    api_key = None
    
    # Intentar desde archivo .env
    try:
        with open('FRED_API_KEY.env', 'r') as f:
            for line in f:
                if line.startswith('FRED_API_KEY'):
                    api_key = line.split('=')[1].strip()
                    print("✅ API Key cargada desde FRED_API_KEY.env")
                    break
    except FileNotFoundError:
        pass
    
    # Intentar desde variable de entorno
    if not api_key:
        api_key = os.getenv('FRED_API_KEY')
        if api_key:
            print("✅ API Key cargada desde variable de entorno")
    
    if not api_key:
        print("❌ ERROR: No se encontró API key")
        print("   Crear archivo 'FRED_API_KEY.env' con:")
        print("   FRED_API_KEY=tu_api_key_aqui")
        exit(1)
    
    # =========================================================================
    # DESCARGAR DATOS
    # =========================================================================
    
    print("\n📥 Descargando datos de FRED (BAMLH0A0HYM2)...")
    
    try:
        fred = Fred(api_key=api_key)
        series = fred.get_series('BAMLH0A0HYM2')
        print(f"✅ Descargados {len(series)} registros históricos")
        print(f"   Período: {series.index[0].strftime('%Y-%m-%d')} a {series.index[-1].strftime('%Y-%m-%d')}")
    except Exception as e:
        print(f"❌ ERROR al descargar datos: {e}")
        exit(1)
    
    # =========================================================================
    # EJECUTAR ANÁLISIS
    # =========================================================================
    
    print("\n🔍 Ejecutando análisis de 5 pasos...")
    print()
    
    result = analyze_high_yield_5_pasos(series, backtester=None)
    
    if result is None:
        print("❌ ERROR: No se pudo analizar la serie")
        exit(1)
    
    # =========================================================================
    # MOSTRAR RESULTADOS
    # =========================================================================
    
    print("="*80)
    print(f"  {result['name']}")
    print("="*80)
    print()
    print(result['status'])
    print()
    print("-"*80)
    print(f"Recomendación:")
    print(f"  {result['recommendation']}")
    print("-"*80)
    print()
    
    # =========================================================================
    # RESUMEN DE PASOS
    # =========================================================================
    
    print("="*80)
    print("  RESUMEN DE VALIDACIÓN")
    print("="*80)
    print()
    print(f"  Pasos cumplidos: {result['steps_completed']}/5")
    print(f"  Nivel de alerta: {result['level']}")
    print()
    
    if result['steps_completed'] >= 3:
        print("  ⚠️  ADVERTENCIA: Crisis en desarrollo o confirmada")
        print("  🚫 NO invertir en High Yield en este momento")
    elif result['steps_completed'] >= 1:
        print("  ⚠️  ALERTA: Vigilar de cerca la evolución")
        print("  ⚡ Preparar estrategia defensiva")
    else:
        print("  ✅ Sin señales de crisis")
        print("  👍 Condiciones normales de mercado")
    
    print()
    print("="*80)

