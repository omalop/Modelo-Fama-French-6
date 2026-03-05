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

warnings.filterwarnings("ignore")

# Logging limpio
logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# Imports internos del proyecto
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, ROOT_DIR)

from src.data.docta_api import DoctaCapitalAPI
from src.data.cache_docta import CacheDoctaAPI

# ─────────────────────────────────────────────────────────────────────────────
# 1. CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

CLIENT_ID     = os.getenv("DOCTA_CLIENT_ID",     "docta-api-cf68347b-omlop")
CLIENT_SECRET = os.getenv("DOCTA_CLIENT_SECRET", "_ciyJML_JOgBD89Ft39PL6Az-ps9BJAAapzkQJ-u-LM")

RANKING_ARG    = os.path.join(ROOT_DIR, 'data/processed/Ranking_Argentina_Top.xlsx')
RANKING_SEC    = os.path.join(ROOT_DIR, 'data/processed/Ranking_Global_SEC_Top.xlsx')
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


def obtener_tasa_descuento(docta: DoctaCapitalAPI) -> tuple[float, str]:
    """
    Obtiene la tasa de descuento local desde la curva soberana ley arg (AL30/AE38).
    Fallback: Treasury 10Y + EMBI+ estimado.
    """
    tir = docta.get_bond_yield("AL30") or docta.get_bond_yield("AE38")
    if tir:
        return tir, f"AL30/AE38 (Docta API): {tir:.2%}"
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


# ─────────────────────────────────────────────────────────────────────────────
# 3. MOTOR DE ALLOCATION TRES PILARES
# ─────────────────────────────────────────────────────────────────────────────

def calcular_allocation_global(
    pe_arg: float, pe_global: float, tasa_dto: float, prob_crisis: float
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

    # Split local vs global proporcional a sus Yield Gaps positivos
    total_bruto = rv_local_bruto + rv_global_bruto
    if total_bruto == 0:
        # Sin ventaja en ninguno: dividir equitativamente
        peso_local  = rv_disponible * 0.50
        peso_global = rv_disponible * 0.50
    else:
        peso_local  = rv_disponible * (rv_local_bruto  / total_bruto)
        peso_global = rv_disponible * (rv_global_bruto / total_bruto)

    return {
        'RV_Local':         round(peso_local,  4),
        'RV_Global':        round(peso_global, 4),
        'RF_Local':         round(peso_rf,     4),
        'Yield_Gap_Local':  round(yg_local,    4),
        'Yield_Gap_Global': round(yg_global,   4),
        'Prob_Crisis':      round(prob_crisis,  4),
    }


def distribuir_intra_pilar(df_ranking: pd.DataFrame, n: int, peso_total: float) -> pd.DataFrame:
    """
    Distribuye el peso de un pilar entre los primeros N tickers
    proporcional al Final_Score (siempre positivo usando softmax).
    """
    top = df_ranking.head(n).copy()

    # Usamos softmax sobre Final_Score para evitar pesos negativos
    scores = top['Final_Score'].values
    exp_s  = np.exp(scores - scores.max())  # Estabilidad numérica
    top['Peso_Pilar'] = exp_s / exp_s.sum()
    top['Peso_Total'] = (top['Peso_Pilar'] * peso_total).round(4)

    return top[['Ticker', 'Sector', 'Final_Score', 'Peso_Total']]


def obtener_yields_bonos(docta: DoctaCapitalAPI) -> dict:
    """
    Descarga yields intradiarios de los tres segmentos de RF.
    Retorna dict {ticker: {'desc': str, 'tir': float, 'segmento': str}}
    """
    todos = {}
    for ticker, desc in BONOS_SOBERANOS.items():
        todos[ticker] = {'desc': desc, 'segmento': 'Soberano'}
    for ticker, desc in BONOS_CORPORATIVOS.items():
        todos[ticker] = {'desc': desc, 'segmento': 'Corporativo'}
    for ticker, desc in BONOS_SUBSOBERANOS.items():
        todos[ticker] = {'desc': desc, 'segmento': 'Subsoberano'}

    for ticker in todos:
        tir = docta.get_bond_yield(ticker)
        todos[ticker]['tir'] = tir

    return todos


# ─────────────────────────────────────────────────────────────────────────────
# 4. EJECUCIÓN PRINCIPAL
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    SEP = "=" * 65

    print(f"\n{SEP}")
    print("🏛️   ALLOCATION TRES PILARES  —  CARTERA INTEGRAL")
    print(f"{SEP}")

    # ── 1. Conexión Docta con caché DuckDB (TTL 7 días por instrumento) ───
    print("\n⏳ [1/5] Inicializando cliente Docta Capital con caché local...")
    try:
        _docta_raw = DoctaCapitalAPI(CLIENT_ID, CLIENT_SECRET)
        docta = CacheDoctaAPI(_docta_raw)   # <-- usa caché, max 1 llamado/semana/instrumento
        llamados_semana = docta.llamados_esta_semana()
        print(f"   ✅ Token OK  |  Llamados reales esta semana: {llamados_semana}")
        if llamados_semana > 0:
            print("   ℹ️  Los datos cacheados se usarán hasta que venza el TTL (7 días).")
    except Exception as e:
        print(f"   ❌ {e}")
        sys.exit(1)

    # ── 2. Leer Rankings ─────────────────────────────────────────────
    print("\n📋 [2/5] Leyendo rankings Fama-French...")
    try:
        df_arg = pd.read_excel(RANKING_ARG).sort_values('Final_Score', ascending=False)
        print(f"   ✅ Argentina: {len(df_arg)} tickers  |  Top 5 seleccionados")
    except Exception as e:
        print(f"   ❌ Ranking Argentina no disponible: {e}")
        df_arg = pd.DataFrame()

    try:
        df_sec = pd.read_excel(RANKING_SEC).sort_values('Final_Score', ascending=False)
        print(f"   ✅ Global SEC: {len(df_sec)} tickers  |  Top 10 seleccionados")
    except Exception as e:
        print(f"   ❌ Ranking Global SEC no disponible: {e}")
        df_sec = pd.DataFrame()

    # ── 3. P/E de cada universo ──────────────────────────────────────
    print("\n📊 [3/5] Calculando Earnings Yield de cada universo...")
    tickers_arg_top5  = df_arg['Ticker'].head(5).tolist()  if not df_arg.empty  else []
    tickers_sec_top10 = df_sec['Ticker'].head(10).tolist() if not df_sec.empty else []

    pe_arg,    pe_dict_arg    = obtener_pe_ponderado(tickers_arg_top5)
    pe_global, pe_dict_global = obtener_pe_ponderado(tickers_sec_top10)

    print(f"   P/E LOCAL  (Top 5 ARG):       {pe_arg:.1f}x  →  E/P: {1/pe_arg:.2%}")
    print(f"   P/E GLOBAL (Top 10 SEC):       {pe_global:.1f}x  →  E/P: {1/pe_global:.2%}")

    # ── 4. Tasa de descuento + Crisis ───────────────────────────────
    print("\n🚦 [4/5] Tasa de descuento + señales de crisis...")
    tasa_dto, tasa_label = obtener_tasa_descuento(docta)
    print(f"   Tasa Descuento Local: {tasa_label}")

    signals = leer_crisis_signals()
    prob_c  = estimar_prob_crisis(signals)
    iconos  = {0: "🟢", 1: "🟡", 2: "🔴", -1: "⚫"}
    print(f"   Curva 10Y-2Y → {iconos.get(signals['Curva_10Y2Y'],'⚫')} Nivel {signals['Curva_10Y2Y']}")
    print(f"   High Yield   → {iconos.get(signals['High_Yield'],'⚫')} Nivel {signals['High_Yield']}")
    print(f"   VIX          → {iconos.get(signals['VIX'],'⚫')} Nivel {signals['VIX']}")
    print(f"   Prob. Crisis Sistémica: {prob_c:.1%}")

    # ── 5. Allocation ────────────────────────────────────────────────
    print("\n⚙️  [5/5] Calculando allocation óptimo...")
    alloc = calcular_allocation_global(pe_arg, pe_global, tasa_dto, prob_c)

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
    print(f"\n  📈  RV Local  (ARG)  [{barra(rv_local_pct)}]   {rv_local_pct:>5.1f}%")
    print(f"  🌎  RV Global (SEC)  [{barra(rv_global_pct)}]   {rv_global_pct:>5.1f}%")
    print(f"  🛡️   RF Local        [{barra(rf_pct)}]   {rf_pct:>5.1f}%")
    print(f"\n  Yield Gap Local:   {alloc['Yield_Gap_Local']:+.2%}   |   "
          f"Yield Gap Global: {alloc['Yield_Gap_Global']:+.2%}")

    # ─── PILAR 1: RV LOCAL ────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"📈  PILAR 1 — RENTA VARIABLE LOCAL  ({rv_local_pct:.1f}% del capital)")
    print(f"{'─'*65}")
    if not df_arg.empty and alloc['RV_Local'] > 0:
        top_arg = distribuir_intra_pilar(df_arg, 5, alloc['RV_Local'])
        print(f"  {'Ticker':<10} {'Sector':<25} {'FF Score':>8}  {'% Capital':>10}")
        print(f"  {'-'*60}")
        for _, row in top_arg.iterrows():
            pe_str = f"P/E {pe_dict_arg[row['Ticker']]:.1f}x" if row['Ticker'] in pe_dict_arg else "sin P/E"
            print(f"  {row['Ticker']:<10} {str(row['Sector']):<25} {row['Final_Score']:>8.2f}  "
                  f"{row['Peso_Total']*100:>8.1f}%   ({pe_str})")
    else:
        print("  ⚠️  Sin datos de ranking ARG o peso = 0.")

    # ─── PILAR 2: RV GLOBAL ───────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"🌎  PILAR 2 — RENTA VARIABLE GLOBAL SEC  ({rv_global_pct:.1f}% del capital)")
    print(f"{'─'*65}")
    if not df_sec.empty and alloc['RV_Global'] > 0:
        top_sec = distribuir_intra_pilar(df_sec, 10, alloc['RV_Global'])
        print(f"  {'Ticker':<10} {'Sector':<25} {'FF Score':>8}  {'% Capital':>10}")
        print(f"  {'-'*60}")
        for _, row in top_sec.iterrows():
            pe_str = f"P/E {pe_dict_global[row['Ticker']]:.1f}x" if row['Ticker'] in pe_dict_global else "sin P/E"
            print(f"  {row['Ticker']:<10} {str(row['Sector']):<25} {row['Final_Score']:>8.2f}  "
                  f"{row['Peso_Total']*100:>8.1f}%   ({pe_str})")
    else:
        print("  ⚠️  Sin datos de ranking SEC o peso = 0.")

    # ─── PILAR 3: RF LOCAL ────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"🛡️   PILAR 3 — RENTA FIJA LOCAL  ({rf_pct:.1f}% del capital)")
    print(f"{'─'*65}")
    print("  Obteniendo TIRs en tiempo real...\n")
    yields_bonos = obtener_yields_bonos(docta)

    # Agrupar por segmento
    for segmento in ['Soberano', 'Subsoberano', 'Corporativo']:
        print(f"  [{segmento.upper()}]")
        for ticker, info in yields_bonos.items():
            if info['segmento'] != segmento:
                continue
            tir = info['tir']
            if tir is not None:
                marca = "⭐" if tir >= 0.07 else "  "
                # Arbitraje GD vs AL (si aplica)
                gd_par = {"AL30": "GD30", "AE38": "GD38", "AL35": "GD35"}.get(ticker)
                spread_str = ""
                if gd_par:
                    tir_gd = docta.get_bond_yield(gd_par)
                    if tir_gd:
                        spread = tir_gd - tir
                        spread_str = f"  |  vs {gd_par}: {spread:+.2%}"
                        if spread > 0.003:
                            spread_str += " ⚡ ARBIT."
                print(f"  {marca}  {ticker:<8} {info['desc']:<30}  TIR: {tir:.2%}{spread_str}")
            else:
                print(f"       {ticker:<8} {info['desc']:<30}  Sin datos hoy")
        print()

    print(f"  ℹ️  Estrategia: Buy & Hold. Revisar mensualmente.")
    print(f"  ℹ️  Los GD (ley NY) se sugieren SOLO si spread vs AL > 30 bps.")

    # ─── RESUMEN FINAL ────────────────────────────────────────────────
    print(f"\n{SEP}")
    print("📊  RESUMEN — DISTRIBUCIÓN TOTAL DEL CAPITAL")
    print(f"{SEP}")
    print(f"  {'INSTRUMENTO':<30} {'% CAPITAL':>10}")
    print(f"  {'-'*42}")
    if not df_arg.empty and alloc['RV_Local'] > 0:
        for _, row in top_arg.iterrows():
            print(f"  {row['Ticker']:<30} {row['Peso_Total']*100:>9.1f}%")
    if not df_sec.empty and alloc['RV_Global'] > 0:
        for _, row in top_sec.iterrows():
            print(f"  {row['Ticker']:<30} {row['Peso_Total']*100:>9.1f}%")
    print(f"  {'RENTA FIJA (total)':30} {rf_pct:>9.1f}%")
    print(f"  {'-'*42}")
    total_check = (alloc['RV_Local'] + alloc['RV_Global'] + alloc['RF_Local']) * 100
    print(f"  {'TOTAL':30} {total_check:>9.1f}%")
    print(f"{SEP}\n")
