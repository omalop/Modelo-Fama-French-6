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

import numpy as np
import pandas as pd
import yfinance as yf
import requests
import time
import random

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


def _get_session():
    s = requests.Session()
    s.headers.update({'User-Agent': 'Omar Lopez (omlop90@gmail.com)'})
    return s

def obtener_pe_ponderado(tickers: list[str]) -> tuple[float, dict]:
    """
    Calcula el P/E ponderado por Market Cap para un conjunto de tickers.
    Retorna (pe_ponderado, dict {ticker: pe}).
    """
    ratios, caps = {}, {}
    session = _get_session()
    for t in tickers:
        try:
            info = yf.Ticker(t, session=session).info
            pe = info.get('trailingPE') or info.get('forwardPE')
            mc = info.get('marketCap', 0)
            if pe and 3 < pe < 80 and mc > 0:
                ratios[t] = pe
                caps[t] = mc
            time.sleep(1.0) # Ser gentil
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
            session = _get_session()
            vix_ticker = yf.Ticker("^VIX", session=session)
            vix_data = vix_ticker.history(period="5d")
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
        session = _get_session()
        tnx_ticker = yf.Ticker("^TNX", session=session)
        tnx_hist = tnx_ticker.history(period="5d")
        tnx = tnx_hist['Close'].iloc[-1] / 100 if not tnx_hist.empty else 0.045
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
    de Screenermatic para los 3 segmentos de RF.
    Retorna dict {ticker: {'desc': str, 'segmento': str, 'tir': float, 'md': float, 'cvx': float, 'paridad': float}}
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
            todos[ticker]['tir'] = tir / 100.0 if pd.notna(tir) else None
            todos[ticker]['md'] = fila.iloc[0].get('modified_dur')
            todos[ticker]['cvx'] = fila.iloc[0].get('convexidad')
            todos[ticker]['paridad'] = fila.iloc[0].get('paridad_pct')
        else:
            todos[ticker]['tir'] = None
            todos[ticker]['md'] = None
            todos[ticker]['cvx'] = None
            todos[ticker]['paridad'] = None

    return todos


def obtener_metricas_riesgo_cartera(cartera_csv: list, df_arg: pd.DataFrame, df_global_unificado: pd.DataFrame) -> dict:
    """
    Calcula Beta ponderado, Volatilidad Anualizada y Max Drawdown
    del portafolio propuesto descargando historial limpio (CCL) a 1 año.
    """
    try:
        # Calcular Beta Ponderado desde los DataFrames ya extraidos
        beta_pond = 0.0
        for row in cartera_csv:
            t = row['Ticker']
            w = row['Peso_Sugerido']
            if row['Instrumento'] == 'RV_Local' and not df_arg.empty and 'Beta' in df_arg.columns:
                b = df_arg.loc[df_arg['Ticker'] == t, 'Beta']
                if not b.empty and pd.notna(b.iloc[0]): beta_pond += w * b.iloc[0]
                else: beta_pond += w * 1.0 # Mkt avg fallback
            elif row['Instrumento'] == 'RV_Global' and not df_global_unificado.empty and 'Beta' in df_global_unificado.columns:
                b = df_global_unificado.loc[df_global_unificado['Ticker'] == t, 'Beta']
                if not b.empty and pd.notna(b.iloc[0]): beta_pond += w * b.iloc[0]
                else: beta_pond += w * 1.0
            elif row['Instrumento'].startswith('RF_'):
                beta_pond += w * 0.0 # RF tiene beta casi 0 vs equities
        
        # Volatilidad y MaxDD: Necesitamos la serie historica.
        tickers_rv = [r['Ticker'] for r in cartera_csv if "RV_" in r['Instrumento']]
        pesos_rv = {r['Ticker']: r['Peso_Sugerido'] for r in cartera_csv if "RV_" in r['Instrumento']}
        
        if not tickers_rv:
            return {"beta": beta_pond, "vol_anual": 0.0, "max_dd": 0.0}
            
        import yfinance as yf
        def dwn(t):
             try:
                 d = yf.download(t, period='1y', auto_adjust=True, progress=False)
                 if d.empty: return None
                 if isinstance(d.columns, pd.MultiIndex):
                     col = 'Close' if 'Close' in d.columns else 'Adj Close'
                     return d[col][t].copy()
                 else:
                     col = 'Close' if 'Close' in d.columns else 'Adj Close'
                     return d[col].copy()
             except: return None
             
        ccl_series = None
        if any(t.endswith('.BA') for t in tickers_rv):
             ggal_ba = dwn('GGAL.BA')
             ggal_ad = dwn('GGAL')
             if ggal_ba is not None and ggal_ad is not None:
                 c_df = pd.concat([ggal_ba, ggal_ad], axis=1).dropna()
                 ccl_series = (c_df.iloc[:,0]*10) / c_df.iloc[:,1]
                 
        dfs = []
        for t in tickers_rv:
             s = dwn(t)
             if s is not None:
                 if t.endswith('.BA') and ccl_series is not None:
                      aligned = pd.concat([s, ccl_series], axis=1).dropna()
                      s = aligned.iloc[:,0] / aligned.iloc[:,1]
                 s.name = t
                 s.index = pd.to_datetime(s.index).tz_localize(None)
                 dfs.append(s)
                 
        if not dfs:
            return {"beta": beta_pond, "vol_anual": 0.0, "max_dd": 0.0}
            
        # Matriz de retornos log
        df_hist = pd.concat(dfs, axis=1).ffill().dropna()
        returns = np.log(df_hist / df_hist.shift(1)).replace([np.inf, -np.inf], np.nan).dropna()
        
        # Rendimiento diario promedio de RF asumiendo Tasa Libre de Riesgo/EMBI segura (~6% anual)
        ret_diario_rf = 0.06 / 252 
        
        cartera_ret = 0
        for t in returns.columns:
            cartera_ret += returns[t] * pesos_rv[t]
        
        peso_rf = sum([r['Peso_Sugerido'] for r in cartera_csv if "RF_" in r['Instrumento']])
        cartera_ret = cartera_ret + (peso_rf * ret_diario_rf)
        
        vol_anual = float(cartera_ret.std() * np.sqrt(252))
        
        cum_ret = (1 + cartera_ret).cumprod()
        roll_max = cum_ret.cummax()
        drawdown = (cum_ret / roll_max) - 1.0
        max_dd = float(drawdown.min())
        
        return {
             "beta": float(beta_pond),
             "vol_anual": vol_anual,
             "max_dd": max_dd
        }
    except Exception as e:
        logger.warning(f"Error calculando métricas de riesgo: {e}")
        return {"beta": 0.0, "vol_anual": 0.0, "max_dd": 0.0}

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

    # ── 4. Tasa de descuento + Crisis ───────────────────────────────
    print("\n🚦 [4/5] Tasa de descuento + señales de crisis...")
    tasa_dto, tasa_label = obtener_tasa_descuento(df_bonos)
    
    # Extraer TNX (Treasury 10Y) para el cálculo del umbral de bonos
    try:
        tnx_val = yf.Ticker("^TNX").history(period="1d")['Close'].iloc[-1] / 100.0
    except Exception:
        tnx_val = 0.042 # Fallback académico razonable
        
    print(f"   Tasa Descuento Local: {tasa_label}")
    print(f"   Treasury 10Y (TNX): {tnx_val:.2%}")

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

    # Agrupar por segmento y guardar bonos viables para sugerencia
    bonos_viables_hd = []
    bonos_viables_pesos = []
    for segmento in ['Soberano', 'Subsoberano', 'Corporativo', 'Pesos/CER']:
        print(f"  [{segmento.upper()}]")
        for ticker, info in yields_bonos.items():
            if info['segmento'] != segmento:
                continue
            tir = info['tir']
            if tir is not None:
                paridad = info.get('paridad') or 0
                cvx = info.get('cvx') or 0
                md = info.get('md') or 0
                
                # ─── Criterios de Viabilidad Académicos (Ajustados por el Usuario) ───
                # 1. TIR >= Riesgo País + TNX (Costo de oportunidad + Spread Soberano)
                # 2. 90% <= Paridad <= 101% (Evitar distress extremo o sobrecompra)
                # 3. MD < 5 (Control de volatilidad de precio - Homer & Leibowitz)
                # 4. Convexidad > 0 (Curvatura positiva para protección de capital - Fabozzi)
                
                embi_decimal = df_embi['embi_puntos'].iloc[-1] / 10000.0
                umbral_tir_hd = embi_decimal + tnx_val
                
                if segmento == 'Pesos/CER':
                    # TIR real positiva o nominal atractiva, paridad no tan restringida (<110)
                    es_viable = tir >= 0.02 and md < 5 and paridad < 110
                elif segmento == 'Corporativo':
                    # Corporativos: Paridad estricta (90-100.9), TIR >= Umbral, MD < 5, CVX > 0
                    es_viable = (
                        tir >= umbral_tir_hd and 
                        90.0 <= paridad <= 100.9 and 
                        md < 5.0 and 
                        cvx > 0.0
                    )
                else:
                    # Soberanos y Subsoberanos: Lógica de captura de Upside (Alta Convexidad)
                    # Tolerancia: Si falta hasta 1% de TIR para llegar al umbral, se acepta.
                    tir_con_tolerancia = tir + 0.01
                    
                    # Combo de Validación: TIR suficiente + MD bajo control + Convexidad positiva relevante
                    # El objetivo es maximizar la captura del upside ante bajas de tasas (Buy the Dip)
                    es_viable = (
                        tir_con_tolerancia >= umbral_tir_hd and 
                        md < 5.0 and 
                        cvx > 5.0  # Exigimos una convexidad mínima para asegurar el upside
                    )

                marca = "⭐" if es_viable else "  "
                
                # Conversión a TEM y TNA para comparación de mercado
                tem = (1 + tir)**(1/12) - 1 if tir and tir > -1 else 0
                tna = tem * 12
                
                info_tir = f"TIR: {tir:.2%}"
                info_tem = f"TEM: {tem:.2%}"
                info_tna = f"TNA: {tna:.2%}"
                
                print(f"  {marca}  {ticker:<8} {info['desc']:<25}  {info_tir:<12} | {info_tem:<11} | {info_tna:<11} | MD: {md:.2f} | P: {paridad:.1f}%")
                
                if es_viable:
                    b_dict = {
                        'Ticker': ticker,
                        'Desc': info['desc'],
                        'TIR': tir,
                        'TEM': tem,
                        'TNA': tna
                    }
                    if segmento == 'Pesos/CER':
                        bonos_viables_pesos.append(b_dict)
                    else:
                        bonos_viables_hd.append(b_dict)
            else:
                print(f"       {ticker:<8} {info['desc']:<30}  Sin datos hoy")
        print()

    # Distribución Dinámica por Riesgo Político
    peso_rel_pesos = max(0.1, min(0.9, confianza_gobierno / 100.0))
    peso_rel_hd = 1.0 - peso_rel_pesos
    
    if not bonos_viables_pesos and bonos_viables_hd:
        peso_rel_hd = 1.0; peso_rel_pesos = 0.0
    elif not bonos_viables_hd and bonos_viables_pesos:
        peso_rel_pesos = 1.0; peso_rel_hd = 0.0
        
    peso_rf_pesos = alloc['RF_Local'] * peso_rel_pesos
    peso_rf_hd = alloc['RF_Local'] * peso_rel_hd
    
    print(f"  ⚖️  Ajuste por Imagen Presidencial ({confianza_gobierno}%):")
    print(f"      → Ponderación Renta Fija: {peso_rel_pesos*100:.1f}% Pesos/CER | {peso_rel_hd*100:.1f}% Hard Dollar\n")

    print(f"  ℹ️  Estrategia: Buy & Hold. Revisar mensualmente.")
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
                        'TIR': round(b['TIR'], 4),
                        'TEM': round(b['TEM'], 4),
                        'TNA': round(b['TNA'], 4)
                    })
            if bonos_viables_hd:
                peso_por_bono = peso_rf_hd / len(bonos_viables_hd)
                for b in bonos_viables_hd:
                    cartera_csv.append({
                        'Ticker': b['Ticker'],
                        'Instrumento': f"RF_Local_HD ({b['Desc']})",
                        'Peso_Sugerido': round(peso_por_bono, 4),
                        'TIR': round(b['TIR'], 4),
                        'TEM': round(b['TEM'], 4),
                        'TNA': round(b['TNA'], 4)
                    })
        else:
            # Fallback si no hay bonos viables > 7%
            cartera_csv.append({
                'Ticker': 'RF_RESERVA',
                'Instrumento': 'Renta_Fija_Reserva',
                'Peso_Sugerido': round(alloc['RF_Local'], 4),
                'Retorno_Esperado': round(tasa_dto, 4)
            })

    df_cartera = pd.DataFrame(cartera_csv)
    output_path = os.path.join(ROOT_DIR, 'data/processed/Portfolio_Recommendation.csv')
    df_cartera.to_csv(output_path, index=False)
    print(f"\n📁 Recomendación de Cartera exportada a: {output_path}")

    # ─── MTRICAS DE RIESGO GLOBALES DEL PORTFOLIO ─────────────────────
    print("\n   ⚙️ Calculando Riesgo Global de la Cartera (Beta, Vol, MaxDD)...")
    riesgo = obtener_metricas_riesgo_cartera(cartera_csv, df_arg, df_global_unificado)
    
    # ─── RESUMEN FINAL ────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("📊  RESUMEN — DISTRIBUCIÓN TOTAL Y MÉTRICAS DE RIESGO")
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
    print(f"  {'TOTAL ALLOCATION':30} {total_check:>9.1f}%")
    
    # Impresión de Riesgo
    print(f"\n  📉 Métricas de Riesgo Estructural (1Y histórico en USD CCL):")
    print(f"      • Beta de la Cartera :  {riesgo['beta']:.2f}")
    print(f"      • Volatilidad Anual  :  {riesgo['vol_anual']:.2%}")
    print(f"      • Max Drawdown (1Y)  :  {riesgo['max_dd']:.2%}")
    print(f"{SEP}\n")
