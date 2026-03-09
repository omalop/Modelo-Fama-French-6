"""
=============================================================================
ALLOCATION TRES PILARES — Asignación Dinámica de Cartera Integral
=============================================================================
Integra las tres fuentes de retorno de la cartera:
  1. Renta Variable LOCAL   (Top 5 Ranking_Argentina_Top)
  2. Renta Variable GLOBAL  (Top 10 Ranking_Global_SEC_Top)
  3. Renta Fija LOCAL       (Soberanos + Subsoberanos + Corporativos HD)

Metodología:
  - Yield Gap diferencial por universo (compara E/P local vs global vs TIR bonos)
  - Pesos dentro de cada pilar proporcionales al Final_Score del screener FF
  - Crisis signals del dashboard (VIX, Curva, HY) penalizan AMBOS pilares de RV
  - Constraint duro: suma total = 100% del capital

Referencias académicas:
  - Black & Litterman (1992): Global Portfolio Optimization. FAJ.
  - Brinson, Hood & Beebower (1986): Determinants of Portfolio Performance. FAJ.
  - Estrada (2000): The Cost of Equity in Emerging Markets. EFMA.
=============================================================================
"""

import os
import sys
import logging
import importlib.util
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore")

# Logging limpio
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Imports internos del proyecto
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, ROOT_DIR)

from src.data.docta_api import DoctaCapitalAPI
from src.data.cache_docta import CacheDoctaAPI
from src.data.scraping_screenermatic import obtener_bonos_frescos
from src.data.historico_embi import obtener_riesgo_pais_fresco

# ─────────────────────────────────────────────────────────────────────────────
# 1. CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

CLIENT_ID     = os.getenv("DOCTA_CLIENT_ID",     "docta-api-cf68347b-omlop")
CLIENT_SECRET = os.getenv("DOCTA_CLIENT_SECRET", "_ciyJML_JOgBD89Ft39PL6Az-ps9BJAAapzkQJ-u-LM")

RANKING_ARG    = os.path.join(ROOT_DIR, 'data/processed/Ranking_Argentina_Top.xlsx')
RANKING_SEC    = os.path.join(ROOT_DIR, 'data/processed/Ranking_Global_SEC_Top.xlsx')
RANKING_INTL   = os.path.join(ROOT_DIR, 'data/processed/Ranking_Global_Intl_Top.xlsx')
DASHBOARD_PATH = os.path.join(ROOT_DIR,
    'Dashboard de Indicadores Adelantados de Crisis Financiera v2/Original v2/crisis_dashboard_pro.py')
DASHBOARD_ENV  = os.path.join(ROOT_DIR,
    'Dashboard de Indicadores Adelantados de Crisis Financiera v2/Original v2/FRED_API_KEY.env')

# Bonos Hard Dollar para renta fija
BONOS_SOBERANOS     = {"AL30": "Soberano HD Ley Arg CP",
                        "AE38": "Soberano HD Ley Arg LP",
                        "AL35": "Soberano HD Ley Arg (35)"}
BONOS_CORPORATIVOS  = {"YFC2O": "YPF Corp HD",
                        "MRCEO": "Pampa Energía HD",
                        "YFCAO": "YPF Corp HD (LP)",
                        "BPOA7": "BOPREAL Serie 3"}
BONOS_SUBSOBERANOS  = {"PBA25": "Prov. Buenos Aires (25)",
                        "NDT25": "Neuquén (25)"}

# Bonos en Pesos (Tasa Fija / Ajuste CER) para Carry Trade
BONOS_PESOS_CER     = {"S31L6": "LECAP Jul-26 (ARS)",
                       "S31G6": "LECAP Ago-26 (ARS)",
                       "TZXD6": "BONCER Dic-26 (CER)",
                       "TZXD7": "BONCER Dic-27 (CER)",
                       "TZX28": "BONCER 2028 (CER)"}

# PHPSESSID actualizado para Screenermatic (renovar cuando expire)
# Actualizado: 2026-03-09
_PHPSESSID_ACTUAL = "89a157e4f517dfdfa789c64cc6ce858b"


# ─────────────────────────────────────────────────────────────────────────────
# 2. FUNCIONES AUXILIARES
# ─────────────────────────────────────────────────────────────────────────────

def obtener_pe_ponderado(tickers: list[str]) -> tuple[float, dict]:
    """
    Calcula el P/E ponderado por Market Cap para un conjunto de tickers.
    Retorna (pe_ponderado, dict {ticker: pe}).
    """
    ratios, caps = {}, {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info
            pe = info.get('trailingPE') or info.get('forwardPE')
            mc = info.get('marketCap', 0)
            if pe and 3 < pe < 80 and mc > 0:
                ratios[t] = pe
                caps[t] = mc
        except Exception:
            pass

    if not ratios:
        return 15.0, {}

    total_mc = sum(caps[t] for t in ratios)
    pe_pond = sum(ratios[t] * (caps[t] / total_mc) for t in ratios)
    return pe_pond, ratios


def leer_crisis_signals() -> dict:
    """
    Lee señales del dashboard de crisis pre-existente.
    Fallback a FRED directo si el dashboard no arranca.
    """
    signals = {'Curva_10Y2Y': 0, 'High_Yield': 0, 'VIX': 0}

    # Cargar .env del dashboard
    if os.path.exists(DASHBOARD_ENV):
        try:
            from dotenv import load_dotenv
            load_dotenv(DASHBOARD_ENV)
        except ImportError:
            with open(DASHBOARD_ENV) as f:
                for line in f:
                    if '=' in line and not line.startswith('#'):
                        k, v = line.strip().split('=', 1)
                        os.environ.setdefault(k.strip(), v.strip())

    try:
        spec = importlib.util.spec_from_file_location("crisis_dashboard", DASHBOARD_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        signals['Curva_10Y2Y'] = mod.analyze_yield_curve().get('level', 0)
        signals['VIX']         = mod.analyze_vix().get('level', 0)
        signals['High_Yield']  = mod.analyze_high_yield().get('level', 0)
        return signals

    except Exception as e_dashboard:
        # Fallback FRED + yfinance directo
        try:
            from fredapi import Fred
            fred = Fred()
            t10y2y = fred.get_series('T10Y2Y').dropna().iloc[-1]
            hy     = fred.get_series('BAMLH0A0HYM2').dropna().iloc[-1]
            signals['Curva_10Y2Y'] = 2 if t10y2y < 0 else (1 if t10y2y < 0.25 else 0)
            signals['High_Yield']  = 2 if hy > 7 else (1 if hy > 4 else 0)
        except Exception:
            pass
        try:
            vix_data = yf.Ticker("^VIX").history(period="5d")
            vix = vix_data['Close'].iloc[-1] if not vix_data.empty else 20
            signals['VIX'] = 2 if vix >= 30 else (1 if vix >= 20 else 0)
        except Exception:
            pass
        return signals


def obtener_tasa_descuento(df_bonos: pd.DataFrame) -> tuple[float, str]:
    """
    Obtiene la tasa de descuento local desde la curva soberana ley arg (AL30/AE38)
    utilizando la base de Screenermatic.
    Fallback: Treasury 10Y + EMBI+ estimado.
    """
    for tk in ["AL30", "AE38"]:
        fila = df_bonos[df_bonos['simbolo'] == tk]
        if not fila.empty and not pd.isna(fila.iloc[0]['tir_pct']):
            tir_decimal = fila.iloc[0]['tir_pct'] / 100.0
            return tir_decimal, f"{tk} (Screenermatic): {tir_decimal:.2%}"
            
    # Fallback sintético
    try:
        tnx = yf.Ticker("^TNX").history(period="5d")['Close'].iloc[-1] / 100
    except Exception:
        tnx = 0.045
    embi_proxy = 0.06  # ~600 bps EMBI+ Argentina (conservador)
    tasa = tnx + embi_proxy
    return tasa, f"Treasury {tnx:.2%} + EMBI+ {embi_proxy:.2%} = {tasa:.2%} (estimado)"


def estimar_prob_crisis(signals: dict) -> float:
    """
    Ponderación académica: Estrella & Mishkin (1998), Gilchrist & Zakrajšek (2012).
    """
    pesos = {'Curva_10Y2Y': 0.45, 'High_Yield': 0.35, 'VIX': 0.20}
    return sum((signals.get(k, 0) / 2) * p for k, p in pesos.items())


def analizar_divergencia_merval_embi(df_embi: pd.DataFrame) -> dict:
    """
    Analiza la divergencia a 1 año (estructural) entre el Riesgo País (EMBI) y el mercado local (GGAL proxy).
    Evita el ruido de corto plazo (30 días) para identificar divergencias macroeconómicas.
    Retorna un dict con la divergencia detectada e impacto sugerido en allocation.
    """
    res = {'tipo': 'Neutral', 'impacto_rv': 0.0, 'mensaje': 'Sin divergencia estructural relevante'}
    try:
        # Extraer variación a 1 año GGAL (Proxy Merval USD)
        ggal_data = yf.Ticker("GGAL").history(period="1y")
        if ggal_data.empty:
            return res
            
        ggal_hoy = ggal_data['Close'].iloc[-1]
        ggal_inicio = ggal_data['Close'].iloc[0] # ~ 1 año atrás
        var_rv = (ggal_hoy / ggal_inicio) - 1.0
        
        # Extraer variación anual EMBI (mismos días bursátiles)
        dias_habiles = len(ggal_data)
        df_embi_reciente = df_embi.tail(dias_habiles).reset_index(drop=True)
        embi_hoy = df_embi_reciente['embi_puntos'].iloc[-1]
        embi_inicio = df_embi_reciente['embi_puntos'].iloc[0]
        var_embi = (embi_hoy / embi_inicio) - 1.0
        
        # Análisis de divergencia macro/estructural
        # Equities caen o se estancan (< 5%) y Riesgo País colapsa (<-20%) = Divergencia Alcista (compra fuerte RV)
        if var_rv < 0.05 and var_embi < -0.20:
            res['tipo'] = 'Divergencia Alcista Estructural'
            res['impacto_rv'] = 0.15 # Bump de capital hacia Equity
            res['mensaje'] = f"Riesgo País colapsó {abs(var_embi):.1%} anual y GGAL sigue rezagada ({var_rv:+.1%}). 🚀 Fuerte upside de RV pendiente."
            
        # Equities volaron (> 30%) pero el Riesgo País está subiendo (> 5%) = Divergencia Bajista (burbuja RV / riesgo Kuka)
        elif var_rv > 0.30 and var_embi > 0.05:
            res['tipo'] = 'Divergencia Bajista Estructural'
            res['impacto_rv'] = -0.20 # Recorte severo en Equity para fugar a RF
            res['mensaje'] = f"GGAL voló {var_rv:+.1%} anual, pero el EMBI sube ({var_embi:+.1%}). 📉 Alerta de corrección / Burbuja RV."
            
        else:
            res['mensaje'] = f"Correlación macro consistente. GGAL 1A: {var_rv:+.1%} | EMBI 1A: {var_embi:+.1%}"
            
    except Exception as e:
        logger.warning(f"Error analizando divergencia: {e}")
        
    return res


# ─────────────────────────────────────────────────────────────────────────────
# 3. MOTOR DE ALLOCATION TRES PILARES
# ─────────────────────────────────────────────────────────────────────────────

def calcular_allocation_global(
    pe_arg: float, pe_global: float, tasa_dto: float, prob_crisis: float, impacto_divergencia: float = 0.0
) -> dict:
    """
    Determina la distribución óptima entre los tres pilares.

    Lógica:
    - Yield gap LOCAL  = E/P_local  - tasa_descuento
    - Yield gap GLOBAL = E/P_global - tasa_descuento (misma tasa de referencia)
    - El pilar más atractivo recibe el mayor peso relativo de RV
    - La crisis sistémica penaliza AMBOS pilares de RV proporcionalmente

    Retorna dict con pesos brutos normalizados a 1.0.
    """
    ep_local  = 1.0 / pe_arg    if pe_arg > 0    else 0
    ep_global = 1.0 / pe_global if pe_global > 0 else 0

    yg_local  = ep_local  - tasa_dto
    yg_global = ep_global - tasa_dto

    # Puntaje base de cada pilar de RV: cuánto paga sobre la RF
    base_local  = max(0.0, yg_local  + 0.04)  # +4% buffer de crecimiento esperado
    base_global = max(0.0, yg_global + 0.04)

    # Penalización por crisis (no lineal: sqrt para suavizar la caída)
    castigo = prob_crisis ** 1.5
    rv_local_bruto  = base_local  * (1 - castigo)
    rv_global_bruto = base_global * (1 - castigo)

    # Normalizar los dos pilares de RV para que su suma no exceda (1 - RF_min)
    # RF mínima = 0.20 (nunca menos de 20% en renta fija en este modelo)
    # RF extra si crisis o Yield Gap negativo
    rf_base = 0.20
    if yg_local < 0 and yg_global < 0:
        rf_base += 0.30  # Ambos cotizan caros: mucho resguardo
    elif yg_local < 0 or yg_global < 0:
        rf_base += 0.15  # Uno está caro

    rf_crisis_extra = castigo * 0.40  # Crisis empuja hasta 40% extra a RF
    peso_rf = min(0.70, rf_base + rf_crisis_extra)

    rv_disponible = 1.0 - peso_rf

    # ── Pisos mínimos de diversificación (nunca menos de X% en ningún pilar) ───
    # Fuente: Black-Litterman (1992), Solnik (1974) - diversificación mínima obligatoria
    PISO_RV_LOCAL  = 0.15   # Mínimo 15% en acciones locales
    PISO_RV_GLOBAL = 0.15   # Mínimo 15% en acciones globales
    PISO_RF_LOCAL  = 0.20   # Mínimo 20% en renta fija (Markowitz 1952: amortiguador mínimo institucional)

    # Total de pisos reservados: el resto se distribuye dinámicamente
    total_pisos = PISO_RV_LOCAL + PISO_RV_GLOBAL + PISO_RF_LOCAL    # = 0.45
    excedente   = max(0.0, 1.0 - total_pisos)                        # = 0.55

    # Distribuir el excedente con la lógica cuantitativa del Yield Gap
    total_bruto = rv_local_bruto + rv_global_bruto
    if total_bruto == 0:
        # Sin ventaja: Local y Global se reparten equitativamente el excedente de equity
        frac_local  = 0.50
        frac_global = 0.50
        frac_rf     = 0.00
    else:
        # RF captura excedente proporcional a su señal defensiva (inverse yield gap)
        frac_local  = rv_local_bruto  / total_bruto
        frac_global = rv_global_bruto / total_bruto
        frac_rf     = 0.00  # Excedente va a equities; el RF ya tiene su piso cubierto

    # Ajustar fraccion local por divergencia estructural (ventana 1 año)
    frac_local = max(0.0, frac_local + impacto_divergencia)
    # Renormalizar fracciones de equity
    total_frac = frac_local + frac_global
    if total_frac > 0:
        frac_local  /= total_frac
        frac_global /= total_frac

    # Pesos finales = piso + porción del excedente
    excedente_rv = excedente * (1.0 - frac_rf)
    peso_local  = round(PISO_RV_LOCAL  + excedente_rv * frac_local,  4)
    peso_global = round(PISO_RV_GLOBAL + excedente_rv * frac_global, 4)
    peso_rf     = round(1.0 - peso_local - peso_global,              4)

    # Garantía final: si RF quedó < su piso por redondeo, compensar desde local
    if peso_rf < PISO_RF_LOCAL:
        diff = PISO_RF_LOCAL - peso_rf
        peso_local = round(peso_local - diff, 4)
        peso_rf    = PISO_RF_LOCAL


    return {
        'RV_Local':         peso_local,
        'RV_Global':        peso_global,
        'RF_Local':         peso_rf,
        'Yield_Gap_Local':  round(yg_local,   4),
        'Yield_Gap_Global': round(yg_global,  4),
        'Prob_Crisis':      round(prob_crisis, 4),
    }


def seleccionar_por_umbral(
    df_ranking: pd.DataFrame,
    peso_total: float,
    umbral_score: float,
    min_activos: int = 3,
    max_activos: int = 15,
    aplicar_momentum: bool = True
) -> pd.DataFrame:
    """
    Distribuye el peso de un pilar entre los activos que superan el umbral
    de FF Score, con filtro de momentum MA52 opcional.

    Fundámento:
        Grinold & Kahn (1999). 'Active Portfolio Management', Cap. 6.
        'Ley Fundamental de la Gestión Activa: IR ≈ IC * sqrt(N).'
        Con IC alto (Fama-French), concentración es óptima.
        El umbral dinámico evita el error de incluir activos de quality baja.

    Supuesto de momentum:
        Jegadeesh & Titman (1993). Solo se incluyen activos con precio
        por encima de la MA52 semanal (tendencia alcista confirmada).

    Args:
        df_ranking:       DataFrame con columnas ['Ticker', 'Sector', 'Final_Score'].
        peso_total:       Peso total del pilar a distribuir (0.0 a 1.0).
        umbral_score:     FF Score mínimo para ser considerado.
        min_activos:      Mínimo de activos a incluir aunque no pasen el umbral.
        max_activos:      Máximo de activos a incluir.
        aplicar_momentum: Si True, aplica filtro MA52 sobre cada ticker.

    Returns:
        DataFrame con Ticker, Sector, Final_Score, Peso_Total.
    """
    if df_ranking.empty:
        return pd.DataFrame()

    # 1. Filtrar por umbral de calidad
    df_ok = df_ranking[df_ranking['Final_Score'] >= umbral_score].copy()

    # 2. Si menos activos que el mínimo pasan el umbral, relajar al top-min_activos
    if len(df_ok) < min_activos:
        logger.warning(
            f"Solo {len(df_ok)} activos superan umbral {umbral_score}. "
            f"Completando hasta {min_activos} (relajando umbral)."
        )
        df_ok = df_ranking.head(min_activos).copy()

    # 3. Limitar al máximo
    df_ok = df_ok.head(max_activos)

    # 4. Filtro de Momentum MA52 (sólo incluir activos en tendencia alcista)
    if aplicar_momentum and not df_ok.empty:
        tickers_momentum = []
        for ticker in df_ok['Ticker'].tolist():
            try:
                hist = yf.Ticker(ticker).history(period="1y", interval="1wk")['Close']
                if len(hist) >= 20:  # mínimo de historia razonable
                    ma52 = hist.mean()  # media del año (aproximación a MA52w)
                    precio_actual = hist.iloc[-1]
                    if precio_actual >= ma52:
                        tickers_momentum.append(ticker)
                    else:
                        logger.info(f"   [Momentum] {ticker} EXCLUIDO: precio {precio_actual:.2f} < MA52 {ma52:.2f}")
                else:
                    tickers_momentum.append(ticker)  # sin historia suficiente: incluir
            except Exception:
                tickers_momentum.append(ticker)  # error de descarga: incluir

        df_ok = df_ok[df_ok['Ticker'].isin(tickers_momentum)]

        # Si el filtro de momentum elimina todo, volver al mínimo sin filtro
        if df_ok.empty:
            logger.warning("Filtro de momentum eliminó todos los activos. Usando top-min sin filtro.")
            df_ok = df_ranking.head(min_activos).copy()

    # 5. Distribuir capital con softmax sobre FF Score (Grinold & Kahn, 1999)
    scores = df_ok['Final_Score'].values
    exp_s  = np.exp(scores - scores.max())  # Estabilidad numérica
    df_ok  = df_ok.copy()
    df_ok['Peso_Pilar'] = exp_s / exp_s.sum()
    df_ok['Peso_Total'] = (df_ok['Peso_Pilar'] * peso_total).round(4)

    return df_ok[['Ticker', 'Sector', 'Final_Score', 'Peso_Total']]



def obtener_yields_bonos(df_bonos: pd.DataFrame) -> dict:
    """
    Extrae TIR, Modified Duration, Convexidad, y Paridad desde el DataFrame
    de Screenermatic para los 4 segmentos de RF.

    Retorna:
        dict {ticker: {'desc': str, 'segmento': str, 'tir': float,
                       'md': float, 'cvx': float, 'paridad': float,
                       'tem': float}}

    Referencia Duration y Convexidad:
        Fabozzi, F. J. (2007). "Fixed Income Mathematics". McGraw-Hill, Cap. 4-5.
    """
    todos = {}
    for ticker, desc in BONOS_SOBERANOS.items():
        todos[ticker] = {'desc': desc, 'segmento': 'Soberano'}
    for ticker, desc in BONOS_CORPORATIVOS.items():
        todos[ticker] = {'desc': desc, 'segmento': 'Corporativo'}
    for ticker, desc in BONOS_SUBSOBERANOS.items():
        todos[ticker] = {'desc': desc, 'segmento': 'Subsoberano'}
    for ticker, desc in BONOS_PESOS_CER.items():
        todos[ticker] = {'desc': desc, 'segmento': 'Pesos/CER'}

    for ticker in todos:
        fila = df_bonos[df_bonos['simbolo'] == ticker]
        if not fila.empty:
            tir = fila.iloc[0].get('tir_pct')
            tir_decimal = tir / 100.0 if pd.notna(tir) else None
            todos[ticker]['tir'] = tir_decimal
            todos[ticker]['md'] = fila.iloc[0].get('modified_dur')
            todos[ticker]['cvx'] = fila.iloc[0].get('convexidad')
            todos[ticker]['paridad'] = fila.iloc[0].get('paridad_pct')
            # TEM: Tasa Efectiva Mensual = (1 + TIR_anual)^(1/12) - 1
            todos[ticker]['tem'] = (1 + tir_decimal) ** (1/12) - 1 if tir_decimal is not None else None
        else:
            todos[ticker]['tir']   = None
            todos[ticker]['md']    = None
            todos[ticker]['cvx']   = None
            todos[ticker]['paridad'] = None
            todos[ticker]['tem']   = None

    return todos


def _calcular_tir_minima_hd(df_embi: pd.DataFrame) -> float:
    """
    Calcula la TIR mínima aceptable para bonos Hard Dollar soberanos/subsoberanos.

    Fórmula dinámica:
        TIR_min = Yield_Treasury_10Y + (EMBI/10000) +/- tolerancia (1%)

    Supuestos:
        - El Yield del Treasury 10Y se obtiene del símbolo '^TNX' de yfinance.
        - El EMBI es el último dato del DataFrame (puntos básicos).
        - La tolerancia del ±1% representa el rango de aceptación implícito del mercado.

    Referencia:
        Emerging Markets Bond Index Plus (EMBI+) — J.P. Morgan (1999).
        Guideline: TIR_bono >= RF_libre_riesgo + Spread_pais

    Returns:
        float con la TIR mínima anualizada como decimal (ej: 0.0875 = 8.75%).
    """
    try:
        tnx_data = yf.Ticker("^TNX").history(period="5d")
        treasury_yield = tnx_data['Close'].iloc[-1] / 100 if not tnx_data.empty else 0.043
    except Exception:
        treasury_yield = 0.043  # 4.3% fallback

    embi_puntos = df_embi['embi_puntos'].iloc[-1] if not df_embi.empty else 600
    embi_decimal = embi_puntos / 10000.0

    tir_minima = treasury_yield + embi_decimal - 0.01  # -1% de tolerancia
    tir_maxima = treasury_yield + embi_decimal + 0.01  # +1% de tolerancia

    logger.info(
        f"Treasury 10Y: {treasury_yield:.2%} | EMBI+: {embi_puntos:.0f} pts | "
        f"TIR HD mínima: {tir_minima:.2%} | máxima: {tir_maxima:.2%}"
    )
    return tir_minima, tir_maxima, treasury_yield, embi_puntos


# ─────────────────────────────────────────────────────────────────────────────
# 4. EJECUCIÓN PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    SEP = "=" * 65

    print(f"\n{SEP}")
    print("\U0001f3db\ufe0f   ALLOCATION TRES PILARES  \u2014  CARTERA INTEGRAL")
    print(f"{SEP}")

    # ── 0. Imagen Presidencial (hardcodeada según conviccion del usuario) ───
    # Imagen de Gobierno = 56% (Mar-2026).
    # Ajustar este valor cuando cambien las encuestas de imagen presidencial.
    confianza_gobierno: float = 56.0
    logger.info(f"Imagen presidencial configurada: {confianza_gobierno}%")

    # ── 1. Obtener Datos (Screenermatic Cache y EMBI Histórico) ───
    print("\n⏳ [1/5] Inicializando datos y Riesgo País...")
    try:
        df_bonos = obtener_bonos_frescos(forzar=False)
        df_embi  = obtener_riesgo_pais_fresco(forzar=False)
        print(f"   ✅ Bonos Screenermatic cargados: {len(df_bonos)} instrumentos.")
        print(f"   ✅ EMBI+ Histórico cargado: {len(df_embi)} registros, últ: {df_embi['embi_puntos'].iloc[-1]} pts.")
    except Exception as e:
        print(f"   ❌ Error cargando datos externos: {e}")
        sys.exit(1)

    # ── 2. Leer Rankings ─────────────────────────────────────────────
    print("\n📋 [2/5] Leyendo rankings Fama-French...")
    try:
        df_arg = pd.read_excel(RANKING_ARG).sort_values('Final_Score', ascending=False)
        print(f"   ✅ Argentina: {len(df_arg)} tickers  |  Top 5 seleccionados")
    except Exception as e:
        print(f"   ❌ Ranking Argentina no disponible: {e}")
        df_arg = pd.DataFrame()

    # Global Unificado
    try:
        df_sec = pd.read_excel(RANKING_SEC)
    except Exception as e:
        df_sec = pd.DataFrame()
        logger.warning(f"Ranking Global SEC no disponible: {e}")

    try:
        df_intl = pd.read_excel(RANKING_INTL)
    except Exception as e:
        df_intl = pd.DataFrame()
        logger.warning(f"Ranking Global Intl no disponible: {e}")

    if df_sec.empty and df_intl.empty:
        print("   ❌ Ambos rankings globales (SEC e INTL) no disponibles")
        df_global_unificado = pd.DataFrame()
    else:
        df_global_unificado = pd.concat([df_sec, df_intl], ignore_index=True)
        df_global_unificado = df_global_unificado.sort_values('Final_Score', ascending=False)
        print(f"   ✅ Global Unificado (SEC+Intl): {len(df_global_unificado)} tickers combinados | Top 10 seleccionados")

    # ── 3. Selección dinámica por umbral + P/E de cada universo ────────
    print("\n📊 [3/5] Selección dinámica por umbral Fama-French + filtro Momentum MA52...")

    # Umbral: 0.30 local (mercado emergente poca liquidez), 0.50 global
    # Ref: Grinold & Kahn (1999), Evans & Archer (1968), De Groot et al. (2012)
    UMBRAL_LOCAL  = 0.30
    UMBRAL_GLOBAL = 0.50

    # Pre-selección dinámica para calcular P/E representativo
    df_arg_sel = seleccionar_por_umbral(
        df_arg, peso_total=alloc_preliminar_rv_local if 'alloc_preliminar_rv_local' in dir() else 0.50,
        umbral_score=UMBRAL_LOCAL, min_activos=3, max_activos=10,
        aplicar_momentum=True
    ) if not df_arg.empty else pd.DataFrame()

    df_global_sel = seleccionar_por_umbral(
        df_global_unificado, peso_total=alloc_preliminar_rv_global if 'alloc_preliminar_rv_global' in dir() else 0.15,
        umbral_score=UMBRAL_GLOBAL, min_activos=5, max_activos=15,
        aplicar_momentum=True
    ) if not df_global_unificado.empty else pd.DataFrame()

    tickers_arg_dyn   = df_arg_sel['Ticker'].tolist()   if not df_arg_sel.empty   else []
    tickers_global_dyn = df_global_sel['Ticker'].tolist() if not df_global_sel.empty else []

    n_arg    = len(tickers_arg_dyn)
    n_global = len(tickers_global_dyn)
    print(f"   RV Local:  {n_arg} activos seleccionados (umbral FF >= {UMBRAL_LOCAL}, momentum MA52)")
    print(f"   RV Global: {n_global} activos seleccionados (umbral FF >= {UMBRAL_GLOBAL}, momentum MA52)")

    pe_arg,    pe_dict_arg    = obtener_pe_ponderado(tickers_arg_dyn)
    pe_global, pe_dict_global = obtener_pe_ponderado(tickers_global_dyn)

    print(f"   P/E LOCAL  ({n_arg} ARG):      {pe_arg:.1f}x  →  E/P: {1/pe_arg:.2%}")
    print(f"   P/E GLOBAL ({n_global} Unif.): {pe_global:.1f}x  →  E/P: {1/pe_global:.2%}")

    # --- PARÁMETROS MACRO ---
    print(f"\n{SEP}")
    print("📊 CONFIGURACIÓN DE ESCENARIO MACRO")
    print(f"{SEP}")
    try:
        conf_str = input("  🏛️  Imagen/Confianza en el Gobierno (0-100, ej: 56): ").strip()
        confianza_gobierno = float(conf_str) if conf_str else 56.0
    except (ValueError, EOFError):
        confianza_gobierno = 56.0
    
    try:
        inf_previa_str = input("  📈  Inflación del mes previo (ej: 2.9): ").strip()
        inflacion_mensual_previa = float(inf_previa_str)/100.0 if inf_previa_str else 0.029
    except (ValueError, EOFError):
        inflacion_mensual_previa = 0.029

    try:
        inf_exp_str = input("  📈  Inflación mensual esperada (ej: 2.7): ").strip()
        inflacion_mensual_esperada = float(inf_exp_str)/100.0 if inf_exp_str else 0.027
    except (ValueError, EOFError):
        inflacion_mensual_esperada = 0.027

    try:
        dev_str = input("  💵  Devaluación mensual CCL esperada (ej: 2.0): ").strip()
        devalu_mensual_esperada = float(dev_str)/100.0 if dev_str else 0.02
    except (ValueError, EOFError):
        devalu_mensual_esperada = 0.02
    
    print(f"  ✅ Escenario: Confianza {confianza_gobierno}% | Infla {inflacion_mensual_esperada:.1%} | Devalu {devalu_mensual_esperada:.1%}")

    # ── 4. Tasa de descuento + Crisis ───────────────────────────────
    print("\n🚦 [4/5] Tasa de descuento + señales de crisis...")
    tasa_dto, tasa_label = obtener_tasa_descuento(df_bonos)
    print(f"   Tasa Descuento Local: {tasa_label}")

    signals = leer_crisis_signals()
    prob_c  = estimar_prob_crisis(signals)
    iconos  = {0: "🟢", 1: "🟡", 2: "🔴", -1: "⚫"}
    print(f"   Curva 10Y-2Y → {iconos.get(signals['Curva_10Y2Y'],'⚫')} Nivel {signals['Curva_10Y2Y']}")
    print(f"   High Yield   → {iconos.get(signals['High_Yield'],'⚫')} Nivel {signals['High_Yield']}")
    print(f"   VIX          → {iconos.get(signals['VIX'],'⚫')} Nivel {signals['VIX']}")
    print(f"   Prob. Crisis Sistémica: {prob_c:.1%}")

    # ── 4.5. Divergencias Merval vs EMBI ────────────────────────────────
    print("\n📡 Analizando Indicadores Adelantados: RF vs RV...")
    divergencia = analizar_divergencia_merval_embi(df_embi)
    print(f"   → Estado Táctico: {divergencia['tipo']}")
    print(f"   → Racionalidad:   {divergencia['mensaje']}")

    # ── 5. Allocation ────────────────────────────────────────────────
    print("\n⚙️  [5/5] Calculando allocation óptimo...")
    alloc = calcular_allocation_global(pe_arg, pe_global, tasa_dto, prob_c, divergencia['impacto_rv'])

    # ─── OUTPUT FINAL ─────────────────────────────────────────────────
    rv_local_pct  = alloc['RV_Local']  * 100
    rv_global_pct = alloc['RV_Global'] * 100
    rf_pct        = alloc['RF_Local']  * 100

    def barra(pct, largo=20):
        n = int(pct / 5)
        return "█" * n + "░" * (largo - n)

    print(f"\n{SEP}")
    print("🎯  ASIGNACIÓN DE CAPITAL  —  TRES PILARES")
    print(f"{SEP}")
    print(f"\n  📈  RV Local   (ARG)       [{barra(rv_local_pct)}]   {rv_local_pct:>5.1f}%")
    print(f"  🌎  RV Global  (SEC+INTL)  [{barra(rv_global_pct)}]   {rv_global_pct:>5.1f}%")
    print(f"  🛡️   RF Local        [{barra(rf_pct)}]   {rf_pct:>5.1f}%")
    print(f"\n  Yield Gap Local:   {alloc['Yield_Gap_Local']:+.2%}   |   "
          f"Yield Gap Global: {alloc['Yield_Gap_Global']:+.2%}")

    # ─── PILAR 1: RV LOCAL ────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"📈  PILAR 1 — RENTA VARIABLE LOCAL  ({rv_local_pct:.1f}% del capital)")
    print(f"{'─'*65}")
    if not df_arg_sel.empty and alloc['RV_Local'] > 0:
        top_arg = seleccionar_por_umbral(
            df_arg, peso_total=alloc['RV_Local'],
            umbral_score=UMBRAL_LOCAL, min_activos=3, max_activos=10,
            aplicar_momentum=True
        )
        print(f"  {'Ticker':<10} {'Sector':<25} {'FF Score':>8}  {'% Capital':>10}")
        print(f"  {'-'*60}")
        for _, row in top_arg.iterrows():
            pe_str = f"P/E {pe_dict_arg[row['Ticker']]:.1f}x" if row['Ticker'] in pe_dict_arg else "sin P/E"
            print(f"  {row['Ticker']:<10} {str(row['Sector']):<25} {row['Final_Score']:>8.2f}  "
                  f"{row['Peso_Total']*100:>8.1f}%   ({pe_str})")
    else:
        print("  ⚠️  Sin datos de ranking ARG o peso = 0.")

    # ─── PILAR 2: RV GLOBAL UNIFICADO ─────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"🌎  PILAR 2 — RENTA VARIABLE GLOBAL (UNIFICADO)  ({rv_global_pct:.1f}% del capital)")
    print(f"{'─'*65}")
    if not df_global_sel.empty and alloc['RV_Global'] > 0:
        top_sec = seleccionar_por_umbral(
            df_global_unificado, peso_total=alloc['RV_Global'],
            umbral_score=UMBRAL_GLOBAL, min_activos=5, max_activos=15,
            aplicar_momentum=True
        )
        print(f"  {'Ticker':<10} {'Sector':<25} {'FF Score':>8}  {'% Capital':>10}")
        print(f"  {'-'*60}")
        for _, row in top_sec.iterrows():
            pe_str = f"P/E {pe_dict_global[row['Ticker']]:.1f}x" if row['Ticker'] in pe_dict_global else "sin P/E"
            print(f"  {row['Ticker']:<10} {str(row['Sector']):<25} {row['Final_Score']:>8.2f}  "
                  f"{row['Peso_Total']*100:>8.1f}%   ({pe_str})")
    else:
        print("  ⚠️  Sin datos de ranking global o peso = 0.")

    # ─── PILAR 3: RF LOCAL ────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"🛡️   PILAR 3 — RENTA FIJA LOCAL  ({rf_pct:.1f}% del capital)")
    print(f"{'─'*65}")
    yields_bonos = obtener_yields_bonos(df_bonos)

    # Calcular TIR mínima dinámica para Hard Dollar
    tir_hd_min, tir_hd_max, treasury_10y, embi_actual = _calcular_tir_minima_hd(df_embi)
    print(f"  📡  Treasury 10Y: {treasury_10y:.2%}  |  EMBI+: {embi_actual:.0f} pts")
    print(f"  📏  TIR HD mínima aceptable: {tir_hd_min:.2%} (±1% tolerancia)")

    # Los inputs ya fueron solicitados arriba en el bloque de Escenario Macro

    # Agrupar por segmento y guardar bonos viables para sugerencia
    bonos_viables_hd = []
    bonos_viables_pesos = []

    # Recopilar TEM nominal de pesos para ponderar el mercado
    tem_pesos_list = []
    for tk, info in yields_bonos.items():
        if info['segmento'] == 'Pesos/CER' and info.get('tem') is not None:
            es_cer = 'CER' in info['desc'] or 'UVA' in info['desc']
            tem_nom_calc = (info['tem'] + inflacion_mensual_esperada) if es_cer else info['tem']
            tem_pesos_list.append(tem_nom_calc)
    tem_promedio_mercado = sum(tem_pesos_list) / len(tem_pesos_list) if tem_pesos_list else inflacion_mensual_esperada

    for segmento in ['Soberano', 'Subsoberano', 'Corporativo', 'Pesos/CER']:
        print(f"  [{segmento.upper()}]")
        bonos_segmento = []
        for ticker, info in yields_bonos.items():
            if info['segmento'] != segmento:
                continue
            tir = info['tir']
            if tir is not None:
                paridad = info.get('paridad') or 0.0
                cvx     = info.get('cvx') or 0.0
                md      = info.get('md') or 0.0
                tem     = info.get('tem')

                if segmento in ('Soberano', 'Subsoberano'):
                    # ----------------------------------------------------
                    # Soberanos / Subsoberanos HD:
                    # - Sin límite de paridad
                    # - TIR mínima dinámica: Treasury + EMBI - 1% (tolerancia)
                    # - Horizonte Milei (~6 años): usar filtros de Convexidad alta y MD razonable
                    #   para maximizar upside ante baja riesgo país y minimizar caída si sube
                    # Referencia: Fabozzi (2007) Cap. 7 — Convexidad positiva como seguro de tasa
                    # ----------------------------------------------------
                    es_viable = (tir >= tir_hd_min) and (cvx > 0) and (md <= 10)
                    etiqueta_paridad = "-"  # Sin restricción de paridad en soberanos

                elif segmento == 'Corporativo':
                    # ----------------------------------------------------
                    # Corporativos HD:
                    # - Paridad: 90% ≤ paridad ≤ 100.9% (refleja calidad crediticia)
                    # - Criterio compuesto: TIR + Convexidad + MD
                    #   Mayor TIR = mercado confía en la empresa
                    #   Mayor CVX = mayor upside ante calda de tasas
                    #   Menor MD = menor castigo ante suba de riesgo país
                    # ----------------------------------------------------
                    paridad_ok = (90.0 <= paridad <= 100.9)
                    es_viable = paridad_ok and (tir >= tir_hd_min) and (cvx >= 0) and (md <= 7)
                    etiqueta_paridad = f"{paridad:.1f}%"

                else:  # Pesos/CER
                    # ----------------------------------------------------
                    # Bonos en Pesos (Carry Trade / CER):
                    # - Ajustar TEM a Nominal si es CER/UVA
                    # - TEM Nominal > inflación y > 90% prom mercado
                    # - MD < 5 (sensibilidad manejable para carry)
                    # Referencia: Carry Trade — Brunnermeier et al. (2008)
                    # ----------------------------------------------------
                    es_cer = 'CER' in info['desc'] or 'UVA' in info['desc']
                    tem_nominal = (tem + inflacion_mensual_esperada) if tem is not None and es_cer else tem
                    
                    tem_ok = (tem_nominal is not None) and (tem_nominal > max(inflacion_mensual_esperada, tem_promedio_mercado * 0.9))
                    es_viable = tem_ok and (md < 5)
                    etiqueta_paridad = f"{paridad:.1f}%" if paridad else "-"
                    
                    if es_viable:
                        tem_usd = (1 + tem_nominal) / (1 + devalu_mensual_esperada) - 1
                        tir_usd_proyectada = (1 + tem_usd) ** 12 - 1
                    else:
                        tir_usd_proyectada = 0.0

                marca = "⭐" if es_viable else "  "

                # Línea de display con TEM y Carry para pesos/CER
                if segmento == 'Pesos/CER' and tem is not None:
                    es_cer = 'CER' in info['desc'] or 'UVA' in info['desc']
                    tem_nom_print = (tem + inflacion_mensual_esperada) if es_cer else tem
                    print(f"  {marca}  {ticker:<8} {info['desc']:<28}  TIR:{tir:.2%} TEM_nom:{tem_nom_print:.2%}  MD:{md:.2f}  Par:{etiqueta_paridad}")
                    if es_viable:
                        print(f"           ↳ Carry Trade Proyectado en USD: {tir_usd_proyectada:.1%} anual")
                else:
                    print(f"  {marca}  {ticker:<8} {info['desc']:<28}  TIR:{tir:.2%}  MD:{md:.2f}  CVX:{cvx:.2f}  Par:{etiqueta_paridad}")

                if es_viable:
                    b_dict = {'Ticker': ticker, 'Desc': info['desc'], 'TIR': tir,
                              'MD': md, 'CVX': cvx, 'TEM': tem}
                    if segmento == 'Pesos/CER':
                        b_dict['TEM_Nominal'] = tem_nominal
                        b_dict['TIR_USD_Carry'] = tir_usd_proyectada
                        bonos_viables_pesos.append(b_dict)
                    else:
                        bonos_segmento.append(b_dict)
                        bonos_viables_hd.append(b_dict)
            else:
                print(f"       {ticker:<8} {info['desc']:<30}  Sin datos hoy")

        # Ranking compuesto para HD dentro de cada segmento
        if bonos_segmento and segmento != 'Pesos/CER':
            # Score compuesto: balancear TIR (1pt), CVX (1pt) e inversa MD (1pt)
            # Normalizar CVX e inversa de MD sobre el propio segmento
            df_seg = pd.DataFrame(bonos_segmento)
            if len(df_seg) > 1:
                df_seg['score_compuesto'] = (
                    (df_seg['TIR'] / df_seg['TIR'].max() if df_seg['TIR'].max() > 0 else 0) +
                    (df_seg['CVX'] / df_seg['CVX'].max() if df_seg['CVX'].max() > 0 else 0) +
                    ((1 / df_seg['MD'].replace(0, np.nan)) / (1 / df_seg['MD'].replace(0, np.nan)).max())
                )
                df_seg = df_seg.sort_values('score_compuesto', ascending=False)
                print(f"  ↳ Ranking {segmento}: {' > '.join(df_seg['Ticker'].tolist())}")
        print()

    # Pesos en pesos: ranking por Carry USD descendente
    if bonos_viables_pesos:
        bonos_viables_pesos.sort(key=lambda x: x.get('TIR_USD_Carry') or 0, reverse=True)
        print(f"  ↳ Ranking Pesos/CER por Carry USD: {' > '.join(b['Ticker'] for b in bonos_viables_pesos)}")
        print(f"  📊  TEM Nom promedio mercado: {tem_promedio_mercado:.2%}  |  Inflación: {inflacion_mensual_esperada:.2%}  |  Devaluación: {devalu_mensual_esperada:.2%}")

    # Distribución Dinámica por Riesgo Político (imagen presidencial) y Carry Trade
    peso_base_pesos = confianza_gobierno / 100.0
    carry_spread = 0.0
    
    if bonos_viables_pesos and bonos_viables_hd:
        mejor_tir_usd_pesos = bonos_viables_pesos[0]['TIR_USD_Carry']
        mejor_tir_hd = max(b['TIR'] for b in bonos_viables_hd)
        carry_spread = mejor_tir_usd_pesos - mejor_tir_hd
        
        # Ajuste elástico: cada 10% de spread a favor de pesos suma 20% al allocation
        ajuste_carry = carry_spread * 2.0 
        peso_rel_pesos = max(0.1, min(0.9, peso_base_pesos + ajuste_carry))
    else:
        peso_rel_pesos = peso_base_pesos
        
    peso_rel_hd = 1.0 - peso_rel_pesos
    
    if not bonos_viables_pesos and bonos_viables_hd:
        peso_rel_hd = 1.0; peso_rel_pesos = 0.0
    elif not bonos_viables_hd and bonos_viables_pesos:
        peso_rel_pesos = 1.0; peso_rel_hd = 0.0
        
    peso_rf_pesos = alloc['RF_Local'] * peso_rel_pesos
    peso_rf_hd = alloc['RF_Local'] * peso_rel_hd
    
    print(f"  ⚖️  Ajuste por Imagen Presidencial ({confianza_gobierno}%) y Carry Trade:")
    print(f"      → Spread Carry vs HD: {carry_spread:+.2%} (Mejor Carry USD vs Mejor TIR HD)")
    print(f"      → Ponderación Renta Fija: {peso_rel_pesos*100:.1f}% Pesos/CER | {peso_rel_hd*100:.1f}% Hard Dollar")
    print(f"      → Horizonte estratégico: ~6 años (gobierno Milei). Revisar si hay cambio político.")
    print(f"  ℹ️  Estrategia: Ajustar CER si Inflación > Devaluación (Breakeven superado).")
    print(f"  ℹ️  Los GD (ley NY) se sugieren SOLO si spread vs AL > 30 bps.")

    # ─── EXPORTACIÓN A CSV ────────────────────────────────────────────
    cartera_csv = []
    
    if not df_arg.empty and alloc['RV_Local'] > 0:
        for _, row in top_arg.iterrows():
            cartera_csv.append({
                'Ticker': row['Ticker'],
                'Instrumento': 'RV_Local',
                'Peso_Sugerido': round(row['Peso_Total'], 4),
                'Retorno_Esperado': round(row['Final_Score'], 2)
            })
            
    if not df_global_unificado.empty and alloc['RV_Global'] > 0:
        for _, row in top_sec.iterrows():
            cartera_csv.append({
                'Ticker': row['Ticker'],
                'Instrumento': 'RV_Global',
                'Peso_Sugerido': round(row['Peso_Total'], 4),
                'Retorno_Esperado': round(row['Final_Score'], 2)
            })

    if rf_pct > 0:
        if bonos_viables_pesos or bonos_viables_hd:
            if bonos_viables_pesos:
                peso_por_bono = peso_rf_pesos / len(bonos_viables_pesos)
                for b in bonos_viables_pesos:
                    cartera_csv.append({
                        'Ticker': b['Ticker'],
                        'Instrumento': f"RF_Local_Pesos ({b['Desc']})",
                        'Peso_Sugerido': round(peso_por_bono, 4),
                        'Retorno_Esperado': round(b['TIR'], 4),
                        'TEM': round(b.get('TEM_Nominal', b['TEM']), 4),
                        'TNA': round(b['TIR'], 4)  # TIR como proxy de TNA
                    })
            if bonos_viables_hd:
                peso_por_bono = peso_rf_hd / len(bonos_viables_hd)
                for b in bonos_viables_hd:
                    cartera_csv.append({
                        'Ticker': b['Ticker'],
                        'Instrumento': f"RF_Local_HD ({b['Desc']})",
                        'Peso_Sugerido': round(peso_por_bono, 4),
                        'Retorno_Esperado': round(b['TIR'], 4),
                        'TEM': round((1+b['TIR'])**(1/12)-1, 4),
                        'TNA': round(b['TIR'], 4)
                    })
        else:
            # Fallback si no hay bonos viables > 7%
            cartera_csv.append({
                'Ticker': 'RF_RESERVA',
                'Instrumento': 'Renta_Fija_Reserva',
                'Peso_Sugerido': round(alloc['RF_Local'], 4),
                'Retorno_Esperado': round(tasa_dto, 4),
                'TEM': round((1+tasa_dto)**(1/12)-1, 4),
                'TNA': round(tasa_dto, 4)
            })

    df_cartera = pd.DataFrame(cartera_csv)
    # Rellenar NaNs para activos de RV
    df_cartera['TEM'] = df_cartera['TEM'].fillna(0)
    df_cartera['TNA'] = df_cartera['TNA'].fillna(0)
    output_path = os.path.join(ROOT_DIR, 'data/processed/Portfolio_Recommendation.csv')
    df_cartera.to_csv(output_path, index=False)
    print(f"\n📁 Recomendación de Cartera exportada a: {output_path}")

    # ─── RESUMEN FINAL ────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("📊  RESUMEN — DISTRIBUCIÓN TOTAL DEL CAPITAL")
    print(f"{SEP}")
    print(f"  {'INSTRUMENTO':<30} {'% CAPITAL':>10}")
    print(f"  {'-'*42}")
    if not df_arg.empty and alloc['RV_Local'] > 0:
        for _, row in top_arg.iterrows():
            print(f"  {row['Ticker']:<30} {row['Peso_Total']*100:>9.1f}%")
    if not df_global_unificado.empty and alloc['RV_Global'] > 0:
        for _, row in top_sec.iterrows():
            print(f"  {row['Ticker']:<30} {row['Peso_Total']*100:>9.1f}%")
    print(f"  {'RENTA FIJA (total)':30} {rf_pct:>9.1f}%")
    print(f"  {'-'*42}")
    total_check = (alloc['RV_Local'] + alloc['RV_Global'] + alloc['RF_Local']) * 100
    print(f"  {'TOTAL':30} {total_check:>9.1f}%")
    print(f"{SEP}")

    # ─── BENCHMARKS Y MÉTRICAS DE CARTERA ────────────────────────────
    print(f"\n{SEP}")
    print("📈  BENCHMARKS Y MÉTRICAS DE CARTERA")
    print(f"{SEP}")

    # SPX (S&P 500 en USD — no se ajusta a CCL, es el índice base en dólares)
    try:
        spy_hist = yf.Ticker("^GSPC").history(period="1y")
        spx_1a = (spy_hist['Close'].iloc[-1] / spy_hist['Close'].iloc[0] - 1) if not spy_hist.empty else None
        spx_mtd = (spy_hist['Close'].iloc[-1] / spy_hist['Close'].iloc[-22] - 1) if len(spy_hist) > 22 else None
        print(f"  🌎  S&P 500 (USD):      {spx_1a:+.2%} (1A)  |  {spx_mtd:+.2%} (MTD)" if spx_1a else "  🌎  S&P 500: Sin datos")
    except Exception:
        print("  🌎  S&P 500: Sin datos")

    # Merval en CCL (Merval ARS / CCL)
    try:
        from src.models.screener_fundamental import obtener_serie_ccl
        merval_hist = yf.Ticker("^MERV").history(period="1y")
        ccl_serie = obtener_serie_ccl()
        if not merval_hist.empty and not ccl_serie.empty:
            merv_close = merval_hist['Close'].copy()
            merv_close.index = pd.to_datetime(merv_close.index).normalize().tz_localize(None)
            ccl_serie.index = pd.to_datetime(ccl_serie.index).normalize().tz_localize(None)
            df_bm = pd.concat([merv_close.rename("merv"), ccl_serie.rename("ccl")], axis=1).dropna()
            merval_ccl = df_bm["merv"] / df_bm["ccl"]
            merval_1a = (merval_ccl.iloc[-1] / merval_ccl.iloc[0] - 1)
            merval_mtd = (merval_ccl.iloc[-1] / merval_ccl.iloc[-22] - 1) if len(merval_ccl) > 22 else None
            print(f"  🇦🇷  Merval (CCL-USD): {merval_1a:+.2%} (1A)  |  {merval_mtd:+.2%} (MTD)" if merval_mtd else f"  🇦🇷  Merval CCL: {merval_1a:+.2%} (1A)")
        else:
            print("  🇦🇷  Merval CCL: Sin datos")
    except Exception as e:
        print(f"  🇦🇷  Merval CCL: Sin datos ({e})")

    # Beta de la Cartera (ponderada por peso de cada activo)
    try:
        tickers_rv = []
        pesos_rv = []
        if not df_arg.empty and alloc['RV_Local'] > 0:
            for _, row in top_arg.iterrows():
                tickers_rv.append(row['Ticker'])
                pesos_rv.append(row['Peso_Total'])
        if not df_global_unificado.empty and alloc['RV_Global'] > 0:
            for _, row in top_sec.iterrows():
                tickers_rv.append(row['Ticker'])
                pesos_rv.append(row['Peso_Total'])

        if tickers_rv:
            spy_ret = yf.download("^GSPC", period="1y", progress=False)
            if isinstance(spy_ret.columns, pd.MultiIndex):
                spy_ret = spy_ret.xs('Close', level='Price', axis=1).squeeze()
            else:
                spy_ret = spy_ret['Close']
            spy_ret = spy_ret.pct_change().dropna()

            betas_activos = []
            for ticker in tickers_rv:
                try:
                    hist_t = yf.download(ticker, period="1y", progress=False)
                    if isinstance(hist_t.columns, pd.MultiIndex):
                        hist_t = hist_t.xs('Close', level='Price', axis=1).squeeze()
                    else:
                        hist_t = hist_t['Close']
                    ret_t = hist_t.pct_change().dropna()
                    from src.models.screener_fundamental import calcular_beta
                    b = calcular_beta(ret_t, spy_ret, min_obs=30)
                    betas_activos.append(b if not np.isnan(b) else 1.0)
                except Exception:
                    betas_activos.append(1.0)

            beta_cartera = sum(p * b for p, b in zip(pesos_rv, betas_activos)) / sum(pesos_rv)
            print(f"  ⚡  Beta Cartera vs SPY:  {beta_cartera:.3f}")
        else:
            print("  ⚡  Beta Cartera: Sin activos de RV seleccionados")
    except Exception as e:
        print(f"  ⚡  Beta Cartera: Error ({e})")

    vol_anual = 0
    sharpe_cartera = 0
    max_dd = 0
    
    # Max Drawdown y Volatilidad de la cartera (basado en retornos históricos ponderados)
    try:
        if tickers_rv and pesos_rv:
            retornos_df = pd.DataFrame()
            # Descargar SPY para alinear fechas
            spy_base = yf.download("^GSPC", period="1y", progress=False)
            if isinstance(spy_base.columns, pd.MultiIndex):
                spy_base = spy_base.xs('Close', level='Price', axis=1).squeeze()
            else:
                spy_base = spy_base['Close']
            
            common_index = spy_base.index
            
            for ticker, peso in zip(tickers_rv, pesos_rv):
                try:
                    hist_t = yf.download(ticker, period="1y", progress=False)
                    if isinstance(hist_t.columns, pd.MultiIndex):
                        close_t = hist_t.xs('Close', level='Price', axis=1).squeeze()
                    else:
                        close_t = hist_t['Close']
                    ret_t = close_t.pct_change().reindex(common_index).fillna(0)
                    retornos_df[ticker] = ret_t * peso
                except Exception:
                    pass

            if not retornos_df.empty:
                retorno_cartera_dia = retornos_df.sum(axis=1)
                # Volatilidad Anualizada
                vol_diaria = retorno_cartera_dia.std()
                vol_anual = vol_diaria * np.sqrt(252)
                
                # Sharpe (Risk Free = 4.0% anual aprox)
                rf_anual = 0.04
                ret_total_cartera = (1 + retorno_cartera_dia).cumprod().iloc[-1] - 1
                sharpe_cartera = (ret_total_cartera - rf_anual) / vol_anual if vol_anual > 0 else 0
                
                nav = (1 + retorno_cartera_dia).cumprod()
                max_nav = nav.cummax()
                drawdown = (nav - max_nav) / max_nav
                max_dd = drawdown.min()
                
                print(f"  ⚡  Volatilidad (1A):     {vol_anual:.2%}")
                print(f"  ⚡  Sharpe Ratio:         {sharpe_cartera:.2f}")
                print(f"  📉  Max Drawdown (1A):   {max_dd:.2%}")
    except Exception as e:
        print(f"  📉  Métricas de Riesgo: Error ({e})")

    # --- EXPORTAR METADATOS PARA DASHBOARD ---
    import json
    # --- EXPORTAR METADATOS PARA DASHBOARD ---
    import json
    metadata = {
        "confianza_gob": confianza_gobierno,
        "inflacion_previa": inflacion_mensual_previa,
        "inflacion_esperada": inflacion_mensual_esperada,
        "devalu_esperada": devalu_mensual_esperada,
        "signals_crisis": signals,
        "prob_crisis": prob_c,
        "divergencia": divergencia,
        "beta_cartera": round(beta_cartera, 3) if 'beta_cartera' in locals() else 0,
        "vol_anual": round(vol_anual, 4),
        "sharpe_cartera": round(sharpe_cartera, 2),
        "max_dd_1a": round(max_dd, 4),
        "fecha_run": datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    meta_path = os.path.join(ROOT_DIR, 'data/processed/Metadata_Allocation.json')
    with open(meta_path, 'w') as f:
        json.dump(metadata, f, indent=4)

    print(f"{SEP}\n")
