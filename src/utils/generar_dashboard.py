"""
Generador de Dashboard Profesional de Cartera.

Fuente de datos:
    - Portfolio_Recommendation.csv  (cartera actual)
    - DuckDB (riesgo_pais_historico)
    - yfinance (precios históricos 6M + MEP proxy)

Referencias:
    - Jegadeesh & Titman (1993): momentum MA52
    - Solnik (1974): diversificación internacional
    - Grinold & Kahn (1999): selección dinámica por umbral
"""

import json
import logging
import os
import sys
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

# Forzar UTF-8 en Windows para evitar UnicodeEncodeError con emojis
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')


import duckdb
import numpy as np
import pandas as pd
import yfinance as yf

# ── Config ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

RAIZ         = Path(__file__).resolve().parents[2]
DB_PATH      = RAIZ / "data" / "docta_cache.duckdb"
CSV_CARTERA  = RAIZ / "data" / "processed" / "Portfolio_Recommendation.csv"
SALIDA_HTML  = RAIZ / "data" / "processed" / "Dashboard_Cartera.html"

# Activos excluidos por momentum (detectados en el último run)
WATCHLIST_MOMENTUM = {
    "BHIP.BA": {"sector": "Financial Services", "ff_score": 0.89, "motivo": "Precio < MA52 (corrección técnica)"},
    "HAPV3.SA": {"sector": "Financial Services", "ff_score": 1.84, "motivo": "Precio < MA52 (corrección técnica)"},
}

# Mapeo de señales de crisis (último run conocido)
SIGNALS_CRISIS = {"Curva_10Y2Y": 2, "High_Yield": 1, "VIX": 0}
PROB_CRISIS    = 0.25

# Divergencia EMBI
DIVERGENCIA = {
    "tipo": "Divergencia Alcista Estructural",
    "ggal_1a": -0.234,
    "embi_1a": -0.245,
    "impacto": "+15% RV Local",
}

CONFIANZA_GOB = 56.0

# ── 1. Leer cartera ───────────────────────────────────────────────────────────
def cargar_cartera() -> pd.DataFrame:
    df = pd.read_csv(CSV_CARTERA)
    df.columns = [c.strip() for c in df.columns]
    return df

# ── 2. Cargar EMBI+ 2025 ──────────────────────────────────────────────────────
def cargar_embi_2025() -> pd.DataFrame:
    try:
        conn = duckdb.connect(str(DB_PATH))
        df = conn.execute(
            "SELECT fecha, embi_puntos FROM riesgo_pais_historico "
            "WHERE fecha >= '2025-01-01' ORDER BY fecha ASC"
        ).df()
        conn.close()
        df["fecha"] = pd.to_datetime(df["fecha"]).dt.strftime("%Y-%m-%d")
        return df
    except Exception as e:
        logger.warning(f"EMBI no disponible: {e}")
        return pd.DataFrame()

# ── 3. Rendimiento histórico 6M en dólar MEP ─────────────────────────────────
def calcular_rendimiento_6m(df_cartera: pd.DataFrame) -> dict:
    """
    Simula la cartera completa 6 meses atrás usando los pesos actuales.

    Metodología:
    - RV Local (.BA): precio ARS en BYMA → dividir por MEP = USD MEP
    - RV Global: precio USD en NYSE directamente
    - RF Hard Dollar (AL30, AE38, NDT25, YFC2O): cotizan en ARS en BYMA (.BA)
      → dividir por MEP aproxima el USD MEP (coherente con paridad implícita)
    - RF Pesos/CER (S31L6, S31G6, TZXD6, TZXD7, TZX28): cotizan en ARS (.BA)
      → dividir por MEP mide la rentabilidad real en USD MEP

    Tipo de cambio MEP:
        MEP = GGAL.BA (ARS) / GGAL (USD) * 10
        ffill en días de mercado asimétrico (feriados NYSE ≠ BYMA).

    Referencia:
        Lintner (1965). 'The valuation of risk assets and the selection
        of risky investments.' Review of Economics and Statistics.
    """
    fin    = datetime.today()
    inicio = fin - timedelta(days=182)

    # ── MEP GGAL.BA/GGAL*10 ───────────────────────────────────────────────
    mep_series = None
    try:
        ggal_usd = yf.Ticker("GGAL").history(start=inicio, end=fin)["Close"]
        ggal_ars = yf.Ticker("GGAL.BA").history(start=inicio, end=fin)["Close"]
        ggal_usd.index = pd.to_datetime(ggal_usd.index).normalize().tz_localize(None)
        ggal_ars.index = pd.to_datetime(ggal_ars.index).normalize().tz_localize(None)
        idx_union = ggal_usd.index.union(ggal_ars.index)
        ggal_usd  = ggal_usd.reindex(idx_union).ffill()
        ggal_ars  = ggal_ars.reindex(idx_union).ffill()
        mep_series = (ggal_ars / ggal_usd * 10).rename("mep_ars_usd").dropna()
        logger.info(
            f"MEP: inicio={mep_series.iloc[0]:.0f}  "
            f"fin={mep_series.iloc[-1]:.0f} ARS/USD  ({len(mep_series)} dias)"
        )
    except Exception as e:
        logger.warning(f"MEP proxy no disponible: {e}")

    # ── Mapeo ticker → ticker BYMA (.BA) para activos argentinos ─────────
    # Los bonos que no tienen ya ".BA" necesitan el sufijo para consultarlos en yfinance
    def ticker_byma(ticker: str, instrumento: str) -> str:
        """Devuelve el ticker correcto para yfinance según el instrumento."""
        if ticker.endswith(".BA") or ticker.endswith(".SA"):
            return ticker          # RV Local ya tiene .BA
        if "RV_Global" in instrumento:
            return ticker          # Acciones globales: no .BA
        # Bonos argentinos (RF_Local_*): agregar .BA
        return ticker + ".BA"

    # ── Determinar si un instrumento se convierte por MEP ─────────────────
    def usar_mep(ticker: str, instrumento: str) -> bool:
        """True si el precio del activo está en ARS y debe convertirse a USD MEP."""
        return ticker.endswith(".BA") or instrumento.startswith("RF_Local")

    # ── Loop por todos los activos ─────────────────────────────────────────
    retornos_ponderados = None
    tickers_ok          = []
    pesos_ok            = []

    for _, fila in df_cartera.iterrows():
        ticker_orig = fila["Ticker"]
        instrumento = fila["Instrumento"]
        peso        = fila["Peso_Sugerido"]
        ticker_yf   = ticker_byma(ticker_orig, instrumento)

        try:
            hist = yf.Ticker(ticker_yf).history(start=inicio, end=fin)["Close"]
            if hist.empty or len(hist) < 20:
                logger.warning(f"   {ticker_orig} ({ticker_yf}): sin datos históricos suficientes.")
                continue

            # Normalizar tz a naive (necesario para mezclar BYMA + NYSE)
            hist.index = pd.to_datetime(hist.index).normalize().tz_localize(None)

            if usar_mep(ticker_yf, instrumento) and mep_series is not None:
                # Precio ARS → USD MEP
                idx_c = hist.index.intersection(mep_series.index)
                if len(idx_c) > 10:
                    precio_usd = hist.loc[idx_c] / mep_series.loc[idx_c]
                    ret_norm   = precio_usd / precio_usd.iloc[0]
                else:
                    ret_norm = hist / hist.iloc[0]
            else:
                # Precio ya en USD (acciones globales)
                ret_norm = hist / hist.iloc[0]

            ret_pond = ret_norm * peso
            retornos_ponderados = (
                ret_pond if retornos_ponderados is None
                else retornos_ponderados.add(ret_pond, fill_value=0)
            )
            tickers_ok.append(ticker_orig)
            pesos_ok.append(peso)
            logger.info(f"   {ticker_orig} ({ticker_yf}): OK, {len(hist)} obs")

        except Exception as e:
            logger.warning(f"   {ticker_orig} ({ticker_yf}): error — {e}")

    if retornos_ponderados is None or len(retornos_ponderados) < 5:
        return {"fechas": [], "valores": [], "retorno_total": 0.0, "cobertura_pct": 0.0}

    # Normalizar por el peso efectivamente descargado
    peso_efectivo = sum(pesos_ok)
    if peso_efectivo > 0:
        retornos_ponderados = retornos_ponderados / peso_efectivo

    cobertura_pct = round(peso_efectivo * 100, 1)
    fechas        = retornos_ponderados.index.strftime("%Y-%m-%d").tolist()
    valores       = [round(v, 6) for v in retornos_ponderados.values.tolist()]
    retorno_total = round((retornos_ponderados.iloc[-1] - 1.0) * 100, 2)

    logger.info(
        f"Rendimiento 6M calculado: {retorno_total:+.1f}% "
        f"(cobertura {cobertura_pct}% del capital / {len(tickers_ok)} activos)"
    )
    return {
        "fechas":         fechas,
        "valores":        valores,
        "retorno_total":  retorno_total,
        "cobertura_pct":  cobertura_pct,
    }



# ── 4. Preparar datos JS ──────────────────────────────────────────────────────
def preparar_datos_js(df_cartera: pd.DataFrame, df_embi: pd.DataFrame, rendimiento: dict) -> str:
    rv_local  = df_cartera[df_cartera["Instrumento"] == "RV_Local"]
    rv_global = df_cartera[df_cartera["Instrumento"] == "RV_Global"]
    rf_rows   = df_cartera[~df_cartera["Instrumento"].isin(["RV_Local", "RV_Global"])]

    peso_rv_local  = round(rv_local["Peso_Sugerido"].sum() * 100, 1)
    peso_rv_global = round(rv_global["Peso_Sugerido"].sum() * 100, 1)
    peso_rf        = round(rf_rows["Peso_Sugerido"].sum() * 100, 1)

    datos = {
        # Pilares
        "pilares": {
            "labels":  ["RV Local (ARG)", "RV Global", "RF Local"],
            "valores": [peso_rv_local, peso_rv_global, peso_rf],
        },
        # RV Local
        "rv_local": {
            "tickers": rv_local["Ticker"].tolist(),
            "pesos":   (rv_local["Peso_Sugerido"] * 100).round(1).tolist(),
            "scores":  rv_local["Retorno_Esperado"].round(2).tolist(),
        },
        # RV Global
        "rv_global": {
            "tickers": rv_global["Ticker"].tolist(),
            "pesos":   (rv_global["Peso_Sugerido"] * 100).round(1).tolist(),
            "scores":  rv_global["Retorno_Esperado"].round(2).tolist(),
        },
        # RF
        "rf_instrumentos": rf_rows[["Ticker", "Instrumento", "Peso_Sugerido", "Retorno_Esperado"]].to_dict("records"),
        # EMBI
        "embi_fechas":  df_embi["fecha"].tolist()  if not df_embi.empty else [],
        "embi_valores": df_embi["embi_puntos"].tolist() if not df_embi.empty else [],
        # Rendimiento 6M
        "rend_fechas":        rendimiento["fechas"],
        "rend_valores":       rendimiento["valores"],
        "rend_retorno_total": rendimiento["retorno_total"],
        # Watchlist
        "watchlist": [
            {
                "ticker":    t,
                "sector":    v["sector"],
                "ff_score":  v["ff_score"],
                "motivo":    v["motivo"],
            }
            for t, v in WATCHLIST_MOMENTUM.items()
        ],
        # Macro
        "signals": SIGNALS_CRISIS,
        "prob_crisis": PROB_CRISIS,
        "divergencia": DIVERGENCIA,
        "confianza_gob": CONFIANZA_GOB,
        "fecha_generacion": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }

    return json.dumps(datos, ensure_ascii=False)

# ── 5. Generar HTML ───────────────────────────────────────────────────────────
def generar_html(datos_js: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard Cartera — Modelo Fama-French 6</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
  --bg:       #0d0f14;
  --card:     rgba(255,255,255,0.05);
  --border:   rgba(255,255,255,0.08);
  --accent1:  #6366f1;
  --accent2:  #22d3ee;
  --accent3:  #f59e0b;
  --green:    #10b981;
  --red:      #ef4444;
  --yellow:   #f59e0b;
  --text:     #e2e8f0;
  --muted:    #64748b;
  --font:     'Inter', sans-serif;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 14px;
  min-height: 100vh;
}}

/* ── Header ── */
.header {{
  background: linear-gradient(135deg, #1a1f35 0%, #0d1117 100%);
  border-bottom: 1px solid var(--border);
  padding: 28px 40px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 16px;
}}
.header-title {{ font-size: 22px; font-weight: 700; color: #fff; }}
.header-title span {{ color: var(--accent1); }}
.header-meta {{ font-size: 12px; color: var(--muted); }}
.badge {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 5px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  border: 1px solid;
}}
.badge-alcista  {{ background: rgba(16,185,129,.15); color: var(--green);  border-color: rgba(16,185,129,.3); }}
.badge-bajista  {{ background: rgba(239,68,68,.15);  color: var(--red);    border-color: rgba(239,68,68,.3); }}
.badge-neutral  {{ background: rgba(100,116,139,.15); color: var(--muted); border-color: rgba(100,116,139,.3); }}

/* ── Layout ── */
.contenedor {{ max-width: 1400px; margin: 0 auto; padding: 32px 24px; }}
.grid-2 {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin-bottom: 20px; }}
.grid-3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 20px; margin-bottom: 20px; }}
.grid-4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 20px; }}
.full   {{ grid-column: 1 / -1; }}
@media (max-width: 900px) {{
  .grid-2, .grid-3, .grid-4 {{ grid-template-columns: 1fr; }}
}}

/* ── Cards ── */
.card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 22px;
  backdrop-filter: blur(8px);
  transition: border-color .2s;
}}
.card:hover {{ border-color: rgba(99,102,241,.35); }}
.card-title {{
  font-size: 11px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--muted);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 8px;
}}
.card-title-icon {{ font-size: 15px; }}

/* ── KPI ── */
.kpi-value  {{ font-size: 36px; font-weight: 700; color: #fff; line-height: 1; }}
.kpi-sub    {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
.kpi-up     {{ color: var(--green); }}
.kpi-down   {{ color: var(--red); }}

/* ── Semáforos ── */
.signal-grid {{ display: flex; flex-direction: column; gap: 12px; }}
.signal-row  {{
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  border-radius: 10px;
  background: rgba(255,255,255,.03);
  border: 1px solid var(--border);
}}
.signal-label {{ font-weight: 500; }}
.signal-dot {{
  width: 12px; height: 12px; border-radius: 50%;
  display: inline-block; margin-right: 8px;
  box-shadow: 0 0 8px currentColor;
}}
.dot-green  {{ background: var(--green);  color: var(--green); }}
.dot-yellow {{ background: var(--yellow); color: var(--yellow); }}
.dot-red    {{ background: var(--red);    color: var(--red); }}
.dot-grey   {{ background: #475569;       color: #475569; }}

/* ── Tablas ── */
.tabla-scroll {{ overflow-x: auto; margin-top: 12px; }}
table {{
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}}
th {{
  padding: 8px 12px;
  text-align: left;
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .06em;
  color: var(--muted);
  border-bottom: 1px solid var(--border);
}}
td {{ padding: 9px 12px; border-bottom: 1px solid rgba(255,255,255,.03); }}
tr:hover td {{ background: rgba(255,255,255,.03); }}
.estrella {{ color: var(--accent3); }}
.pill {{
  display: inline-block;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 600;
}}
.pill-local  {{ background: rgba(99,102,241,.2);  color: var(--accent1); }}
.pill-global {{ background: rgba(34,211,238,.2);  color: var(--accent2); }}
.pill-hd     {{ background: rgba(245,158,11,.2);  color: var(--accent3); }}
.pill-pesos  {{ background: rgba(16,185,129,.2);  color: var(--green); }}
.pill-watch  {{ background: rgba(239,68,68,.15);  color: var(--red); }}

/* ── Gauge bar ── */
.gauge-bar-wrap {{ margin-top: 12px; }}
.gauge-label    {{ display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
.gauge-track    {{ height: 8px; border-radius: 4px; background: rgba(255,255,255,.08); overflow: hidden; }}
.gauge-fill     {{ height: 100%; border-radius: 4px; transition: width .6s ease; }}

/* ── Alarms ── */
.alarm-list {{ list-style: none; display: flex; flex-direction: column; gap: 10px; margin-top: 12px; }}
.alarm-item {{
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 12px;
  border-radius: 10px;
  border: 1px solid var(--border);
  background: rgba(255,255,255,.02);
}}
.alarm-icon {{ font-size: 18px; flex-shrink: 0; margin-top: 1px; }}
.alarm-body {{ flex: 1; }}
.alarm-title {{ font-weight: 600; font-size: 13px; margin-bottom: 3px; }}
.alarm-desc  {{ font-size: 12px; color: var(--muted); line-height: 1.4; }}
.sev-alta  {{ border-left: 3px solid var(--red); }}
.sev-media {{ border-left: 3px solid var(--yellow); }}
.sev-baja  {{ border-left: 3px solid var(--accent2); }}

/* ── Hipótesis ── */
.hip-grid {{ display: flex; flex-direction: column; gap: 8px; margin-top: 12px; }}
.hip-item {{
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: 10px 14px;
  border-radius: 8px;
  background: rgba(255,255,255,.03);
  border: 1px solid var(--border);
}}
.hip-num {{ font-weight: 700; color: var(--accent1); flex-shrink: 0; min-width: 20px; }}
.hip-text {{ font-size: 13px; line-height: 1.45; }}

/* ── Chart containers ── */
.chart-wrap {{ position: relative; }}
canvas {{ max-width: 100%; }}
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="header-title">Dashboard Cartera · <span>Modelo Fama-French 6</span></div>
    <div class="header-meta" id="fechaGen"></div>
  </div>
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center;">
    <span class="badge badge-alcista" id="badgeDiverg"></span>
    <span class="badge badge-neutral" id="badgeConf"></span>
    <span class="badge" id="badgeCrisis" style="background:rgba(245,158,11,.15);color:#f59e0b;border-color:rgba(245,158,11,.3)"></span>
  </div>
</div>

<div class="contenedor">

  <!-- Fila KPIs -->
  <div class="grid-4" style="margin-bottom:20px;">
    <div class="card">
      <div class="card-title"><span class="card-title-icon">📈</span>RV Local</div>
      <div class="kpi-value" id="kpiRVLocal">—</div>
      <div class="kpi-sub">del capital · <span id="kpiNLocal">—</span> activos</div>
    </div>
    <div class="card">
      <div class="card-title"><span class="card-title-icon">🌎</span>RV Global</div>
      <div class="kpi-value" id="kpiRVGlobal">—</div>
      <div class="kpi-sub">del capital · <span id="kpiNGlobal">—</span> activos</div>
    </div>
    <div class="card">
      <div class="card-title"><span class="card-title-icon">🛡️</span>Renta Fija</div>
      <div class="kpi-value" id="kpiRF">—</div>
      <div class="kpi-sub">56% Pesos/CER · 44% Hard Dollar</div>
    </div>
    <div class="card">
      <div class="card-title"><span class="card-title-icon">📊</span>Rendimiento 6M RV (USD MEP)</div>
      <div class="kpi-value" id="kpiRend6M">—</div>
      <div class="kpi-sub">Solo componente RV · bonos excluidos (B&H)</div>
    </div>
  </div>

  <!-- Fila: Pilares + Rendimiento 6M -->
  <div class="grid-2">
    <div class="card">
      <div class="card-title"><span class="card-title-icon">🎯</span>Distribución Tres Pilares</div>
      <div class="chart-wrap" style="height:260px;">
        <canvas id="chartPilares"></canvas>
      </div>
    </div>
    <div class="card">
      <div class="card-title"><span class="card-title-icon">📈</span>Rendimiento Acumulado 6M — Renta Variable (USD MEP) <span style="font-size:10px;color:#475569;font-weight:400;margin-left:6px;">Bonos: B&H sin dato OHLCV</span></div>
      <div class="chart-wrap" style="height:260px;">
        <canvas id="chartRend6M"></canvas>
      </div>
    </div>
  </div>

  <!-- Fila: RV Local + RV Global -->
  <div class="grid-2">
    <div class="card">
      <div class="card-title"><span class="card-title-icon">🇦🇷</span>Pilar 1 — RV Local (Dinámico · FF≥0.30 + Momentum MA52)</div>
      <div class="chart-wrap" style="height:240px;">
        <canvas id="chartRVLocal"></canvas>
      </div>
    </div>
    <div class="card">
      <div class="card-title"><span class="card-title-icon">🌐</span>Pilar 2 — RV Global (Dinámico · FF≥0.50 + Momentum MA52)</div>
      <div class="chart-wrap" style="height:240px;">
        <canvas id="chartRVGlobal"></canvas>
      </div>
    </div>
  </div>

  <!-- Fila: Señales Macro + EMBI -->
  <div class="grid-2">
    <div class="card">
      <div class="card-title"><span class="card-title-icon">🚦</span>Señales Macro de Crisis Sistémica</div>
      <div class="signal-grid" id="signalGrid"></div>
      <div class="gauge-bar-wrap" style="margin-top:20px;">
        <div class="gauge-label"><span>Probabilidad de Crisis Sistémica</span><span id="probCrisisLabel">—</span></div>
        <div class="gauge-track">
          <div class="gauge-fill" id="gaugeCrisis" style="background:linear-gradient(90deg,#10b981,#f59e0b,#ef4444);"></div>
        </div>
      </div>
      <div class="gauge-bar-wrap" style="margin-top:14px;">
        <div class="gauge-label"><span>Imagen Presidencial (confianza)</span><span id="confLabel">—</span></div>
        <div class="gauge-track">
          <div class="gauge-fill" id="gaugeConf" style="background:var(--accent1);"></div>
        </div>
      </div>
    </div>
    <div class="card">
      <div class="card-title"><span class="card-title-icon">🌡️</span>EMBI+ Argentina · Riesgo País 2025</div>
      <div class="chart-wrap" style="height:260px;">
        <canvas id="chartEMBI"></canvas>
      </div>
    </div>
  </div>

  <!-- Renta Fija Full -->
  <div class="card" style="margin-bottom:20px;">
    <div class="card-title"><span class="card-title-icon">🛡️</span>Pilar 3 — Renta Fija Local · Detalle de Instrumentos</div>
    <div class="tabla-scroll">
      <table id="tablaRF">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Tipo</th>
            <th>% Capital</th>
            <th>TIR Estimada</th>
            <th>Estrategia</th>
          </tr>
        </thead>
        <tbody id="tbodyRF"></tbody>
      </table>
    </div>
  </div>

  <!-- Watchlist Momentum -->
  <div class="card" style="margin-bottom:20px;">
    <div class="card-title"><span class="card-title-icon">👁️</span>Watchlist — Activos Excluidos por Momentum (seguimiento, NO en cartera)</div>
    <div style="font-size:12px;color:var(--muted);margin-bottom:12px;">
      Estos activos tienen un FF Score superior al umbral pero fueron excluidos porque su precio está por debajo de la MA52 (tendencia bajista).
      Monitorear mensualmente: si recuperan la MA52, pueden ingresar a la cartera.
    </div>
    <div class="tabla-scroll">
      <table id="tablaWatch">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Sector</th>
            <th>FF Score</th>
            <th>Motivo de Exclusión</th>
            <th>Acción Sugerida</th>
          </tr>
        </thead>
        <tbody id="tbodyWatch"></tbody>
      </table>
    </div>
  </div>

  <!-- Objetivos e Hipótesis + Alarmas -->
  <div class="grid-2">
    <div class="card">
      <div class="card-title"><span class="card-title-icon">🎯</span>Objetivos e Hipótesis del Modelo</div>
      <div class="hip-grid">
        <div class="hip-item">
          <div class="hip-num">H1</div>
          <div class="hip-text"><strong>Macroestabilidad Milei:</strong> El superávit fiscal y la ancla cambiaria se mantienen hasta 2026-2028, haciendo al carry trade en pesos una oportunidad de riesgo/retorno atractiva.</div>
        </div>
        <div class="hip-item">
          <div class="hip-num">H2</div>
          <div class="hip-text"><strong>Re-rating de Renta Variable:</strong> La compresión del EMBI+ no fue aún capturada en precios de acciones locales (Divergencia Alcista Estructural activa). Esperamos convergencia en 12-24 meses.</div>
        </div>
        <div class="hip-item">
          <div class="hip-num">H3</div>
          <div class="hip-text"><strong>Diversificación Internacional:</strong> El 15% mínimo en RV Global actúa como cobertura ante riesgo país residual y reduce correlación sistémica (Solnik, 1974).</div>
        </div>
        <div class="hip-item">
          <div class="hip-num">H4</div>
          <div class="hip-text"><strong>Momentum como Filtro:</strong> Solo activos con precio > MA52 semanal se incluyen en cartera, reduciendo exposición a activos en corrección técnica (Jegadeesh & Titman, 1993).</div>
        </div>
        <div class="hip-item">
          <div class="hip-num">OBJ</div>
          <div class="hip-text"><strong>Horizonte 2-6 años. Retorno objetivo:</strong> Superar la inflación en USD en +5% anual promedio. Revisar cartera mensualmente. Rebalanceo si desvío > 10% del peso objetivo.</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span class="card-title-icon">🚨</span>Señales de Alarma — Triggers para Desarmar la Cartera</div>
      <ul class="alarm-list">
        <li class="alarm-item sev-alta">
          <div class="alarm-icon">🔴</div>
          <div class="alarm-body">
            <div class="alarm-title">EMBI+ supera los 1.500 puntos</div>
            <div class="alarm-desc">Señal de stress soberano extremo. Reducir RV Local al piso mínimo (15%) y rotar a bonos HD y cash USD.</div>
          </div>
        </li>
        <li class="alarm-item sev-alta">
          <div class="alarm-icon">🔴</div>
          <div class="alarm-body">
            <div class="alarm-title">Cambio de régimen político (abandono del ajuste fiscal)</div>
            <div class="alarm-desc">Si el gobierno abandona el superávit primario o reimplanta el cepo cambiario: rotación inmediata a RF Hard Dollar y reducción de pesos.</div>
          </div>
        </li>
        <li class="alarm-item sev-alta">
          <div class="alarm-icon">🔴</div>
          <div class="alarm-body">
            <div class="alarm-title">VIX > 40 sostenido + HY Spread > 900 bps</div>
            <div class="alarm-desc">Crisis sistémica global. Reducir RV Global y Local al piso. Incrementar RF hasta 50%+. El modelo activa modo defensivo automáticamente.</div>
          </div>
        </li>
        <li class="alarm-item sev-media">
          <div class="alarm-icon">🟡</div>
          <div class="alarm-body">
            <div class="alarm-title">Imagen presidencial cae por debajo del 40%</div>
            <div class="alarm-desc">Aumentar peso HD vs pesos en RF. Recalibrar `confianza_gobierno` en el modelo. Revisar exposición a LECAPs.</div>
          </div>
        </li>
        <li class="alarm-item sev-media">
          <div class="alarm-icon">🟡</div>
          <div class="alarm-body">
            <div class="alarm-title">3 o más activos de RV Local caen bajo MA52</div>
            <div class="alarm-desc">El filtro de momentum excluirá automáticamente. Si quedan menos de 3 activos con FF≥0.30, considerar aumentar temporalmente el peso de RF.</div>
          </div>
        </li>
        <li class="alarm-item sev-baja">
          <div class="alarm-icon">🔵</div>
          <div class="alarm-body">
            <div class="alarm-title">Curva de rendimientos USA se normaliza (10Y-2Y > 150bps)</div>
            <div class="alarm-desc">Posible reducción del apetito por emergentes. Monitorear flujos. No requiere acción inmediata pero incrementar seguimiento del EMBI.</div>
          </div>
        </li>
      </ul>
    </div>
  </div>

  <div style="text-align:center;padding:24px;color:var(--muted);font-size:11px;">
    Modelo Fama-French 6 + Optimizador Tres Pilares — Desarrollado con Antigravity AI · Solo para fines educativos y de investigación. No constituye recomendación de inversión.
  </div>

</div><!-- /contenedor -->

<script>
const D = {datos_js};

// ── Utilidades ─────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);
const pct = v => v.toFixed(1) + '%';

// ── Header ─────────────────────────────────────────────────────────────────
$('fechaGen').textContent = 'Generado: ' + D.fecha_generacion;
$('badgeDiverg').innerHTML = '📡 ' + D.divergencia.tipo;
$('badgeConf').innerHTML   = '🏛️ Imagen Gob. ' + D.confianza_gob + '%';
$('badgeCrisis').innerHTML = '⚠️ P(Crisis) ' + (D.prob_crisis * 100).toFixed(0) + '%';

// ── KPIs ───────────────────────────────────────────────────────────────────
const rvlPct = D.pilares.valores[0];
const rvgPct = D.pilares.valores[1];
const rfPct  = D.pilares.valores[2];
$('kpiRVLocal').textContent  = pct(rvlPct);
$('kpiRVGlobal').textContent = pct(rvgPct);
$('kpiRF').textContent       = pct(rfPct);
$('kpiNLocal').textContent   = D.rv_local.tickers.length;
$('kpiNGlobal').textContent  = D.rv_global.tickers.length;

const rTotal = D.rend_retorno_total;
$('kpiRend6M').innerHTML = `<span class="${{rTotal >= 0 ? 'kpi-up' : 'kpi-down'}}">${{rTotal >= 0 ? '+' : ''}}${{rTotal.toFixed(1)}}%</span>`;

// ── Palette ────────────────────────────────────────────────────────────────
const C = {{
  local: '#6366f1', global: '#22d3ee', rf: '#f59e0b',
  green: '#10b981', red: '#ef4444', yellow: '#f59e0b',
  localArr: ['#6366f1','#818cf8','#a5b4fc','#c7d2fe','#e0e7ff','#ede9fe','#f5f3ff'],
  globalArr: ['#22d3ee','#38bdf8','#7dd3fc','#bae6fd','#e0f2fe','#06b6d4','#0e7490',
              '#0369a1','#075985','#0c4a6e','#155e75','#164e63'],
}};

// ── Chart: Pilares (Donut) ─────────────────────────────────────────────────
new Chart($('chartPilares'), {{
  type: 'doughnut',
  data: {{
    labels: D.pilares.labels,
    datasets: [{{ data: D.pilares.valores, backgroundColor: [C.local, C.global, C.rf],
      borderColor: '#0d0f14', borderWidth: 3, hoverOffset: 8 }}]
  }},
  options: {{
    cutout: '68%',
    plugins: {{
      legend: {{ position: 'right', labels: {{ color: '#e2e8f0', font: {{ size: 12 }}, padding: 14 }} }},
      tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.label}}: ${{ctx.parsed.toFixed(1)}}%` }} }}
    }}
  }}
}});

// ── Chart: Rendimiento 6M ───────────────────────────────────────────────────
if (D.rend_fechas.length > 0) {{
  new Chart($('chartRend6M'), {{
    type: 'line',
    data: {{
      labels: D.rend_fechas,
      datasets: [{{
        label: 'Cartera (base 1.0)',
        data: D.rend_valores,
        borderColor: C.green,
        backgroundColor: 'rgba(16,185,129,0.08)',
        borderWidth: 2,
        pointRadius: 0,
        fill: true,
        tension: 0.35
      }},{{
        label: 'Base 1.0',
        data: D.rend_fechas.map(() => 1.0),
        borderColor: 'rgba(255,255,255,0.18)',
        borderWidth: 1,
        borderDash: [4,4],
        pointRadius: 0,
        fill: false,
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      scales: {{
        x: {{ ticks: {{ color: '#64748b', maxTicksLimit: 6, font: {{ size: 11 }} }}, grid: {{ color: 'rgba(255,255,255,.05)' }} }},
        y: {{ ticks: {{ color: '#64748b', font: {{ size: 11 }},
          callback: v => (((v-1)*100).toFixed(1) + '%') }},
          grid: {{ color: 'rgba(255,255,255,.05)' }} }}
      }},
      plugins: {{ legend: {{ labels: {{ color: '#e2e8f0', font: {{ size: 11 }} }} }} }}
    }}
  }});
}} else {{
  $('chartRend6M').parentElement.innerHTML = '<div style="color:#64748b;text-align:center;padding:80px 0;font-size:13px;">Sin datos históricos disponibles</div>';
}}

// ── Chart: RV Local (barra horizontal) ───────────────────────────────────
new Chart($('chartRVLocal'), {{
  type: 'bar',
  data: {{
    labels: D.rv_local.tickers,
    datasets: [{{
      label: '% Capital',
      data: D.rv_local.pesos,
      backgroundColor: C.localArr.slice(0, D.rv_local.tickers.length),
      borderRadius: 6,
    }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    scales: {{
      x: {{ ticks: {{ color: '#64748b', callback: v => v + '%' }}, grid: {{ color: 'rgba(255,255,255,.05)' }} }},
      y: {{ ticks: {{ color: '#e2e8f0', font: {{ size: 12 }} }}, grid: {{ display: false }} }}
    }},
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.parsed.x.toFixed(1)}}% del capital` }} }}
    }}
  }}
}});

// ── Chart: RV Global (barra horizontal) ──────────────────────────────────
new Chart($('chartRVGlobal'), {{
  type: 'bar',
  data: {{
    labels: D.rv_global.tickers,
    datasets: [{{
      label: '% Capital',
      data: D.rv_global.pesos,
      backgroundColor: C.globalArr.slice(0, D.rv_global.tickers.length),
      borderRadius: 6,
    }}]
  }},
  options: {{
    indexAxis: 'y', responsive: true, maintainAspectRatio: false,
    scales: {{
      x: {{ ticks: {{ color: '#64748b', callback: v => v + '%' }}, grid: {{ color: 'rgba(255,255,255,.05)' }} }},
      y: {{ ticks: {{ color: '#e2e8f0', font: {{ size: 11 }} }}, grid: {{ display: false }} }}
    }},
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{ callbacks: {{ label: ctx => ` ${{ctx.parsed.x.toFixed(1)}}% del capital` }} }}
    }}
  }}
}});

// ── Señales Macro ─────────────────────────────────────────────────────────
const signalMap = {{
  'Curva_10Y2Y': '📉 Curva 10Y-2Y (USA)',
  'High_Yield':  '💳 US High Yield Spread',
  'VIX':         '🌊 Índice VIX',
}};
const nivelLabel = {{0: 'Normal 🟢', 1: 'Elevado 🟡', 2: 'Crítico 🔴', '-1': 'Sin datos ⚫'}};
const dotClass   = {{0: 'dot-green', 1: 'dot-yellow', 2: 'dot-red', '-1': 'dot-grey'}};
const sg = $('signalGrid');
Object.entries(D.signals).forEach(([key, val]) => {{
  const div = document.createElement('div');
  div.className = 'signal-row';
  div.innerHTML = `
    <span class="signal-label">${{signalMap[key] || key}}</span>
    <span><span class="signal-dot ${{dotClass[val]}}"></span>${{nivelLabel[val] || val}}</span>
  `;
  sg.appendChild(div);
}});

// Divergencia
const dDiv = document.createElement('div');
dDiv.className = 'signal-row';
dDiv.innerHTML = `
  <span class="signal-label">📡 EMBI vs GGAL (1A)</span>
  <span style="color:#10b981;font-weight:600;font-size:12px;">${{D.divergencia.tipo}}</span>
`;
sg.appendChild(dDiv);

// Gauges
const pc = (D.prob_crisis * 100).toFixed(0);
$('gaugeCrisis').style.width = pc + '%';
$('probCrisisLabel').textContent = pc + '%';
$('gaugeConf').style.width  = D.confianza_gob + '%';
$('confLabel').textContent   = D.confianza_gob + '%';

// ── Chart: EMBI 2025 ──────────────────────────────────────────────────────
if (D.embi_fechas.length > 0) {{
  const embiCtx = $('chartEMBI');
  const chart = new Chart(embiCtx, {{
    type: 'line',
    data: {{
      labels: D.embi_fechas,
      datasets: [{{
        label: 'EMBI+ (puntos)',
        data: D.embi_valores,
        borderColor: C.red,
        backgroundColor: 'rgba(239,68,68,0.07)',
        borderWidth: 2,
        pointRadius: 0,
        fill: true,
        tension: 0.3
      }}]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      scales: {{
        x: {{ ticks: {{ color: '#64748b', maxTicksLimit: 8, font: {{ size: 10 }} }}, grid: {{ color: 'rgba(255,255,255,.04)' }} }},
        y: {{ ticks: {{ color: '#64748b', font: {{ size: 11 }} }}, grid: {{ color: 'rgba(255,255,255,.04)' }} }}
      }},
      plugins: {{
        legend: {{ labels: {{ color: '#e2e8f0' }} }},
        tooltip: {{ callbacks: {{ label: ctx => ` EMBI: ${{ctx.parsed.y}} pts` }} }}
      }}
    }}
  }});

  // Anotaciones manuales (líneas electorales)
  const elecciones = [
    {{ fecha: '2025-09-07', label: '🗳️ PBA' }},
    {{ fecha: '2025-10-26', label: '🗳️ PASO/Leg.' }},
  ];
  // Nota: sin plugin de anotaciones nativo; se indica en footer del chart
  const nota = document.createElement('div');
  nota.style.cssText = 'font-size:11px;color:#64748b;margin-top:6px;';
  nota.textContent = '🗳️ Eventos electorales 2025: Elecciones PBA (Sep-25) y Legislativas (Oct-25)';
  embiCtx.parentElement.appendChild(nota);
}} else {{
  $('chartEMBI').parentElement.innerHTML = '<div style="color:#64748b;text-align:center;padding:80px 0;font-size:13px;">EMBI+ no disponible</div>';
}}

// ── Tabla Renta Fija ──────────────────────────────────────────────────────
const tbodyRF = $('tbodyRF');
D.rf_instrumentos.forEach(r => {{
  const esPesos = r.Instrumento.includes('Pesos');
  const tipoPill = esPesos ? '<span class="pill pill-pesos">Pesos/CER</span>' : '<span class="pill pill-hd">Hard Dollar</span>';
  const tir = (r.Retorno_Esperado * 100).toFixed(2);
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><strong>${{r.Ticker}}</strong></td>
    <td>${{tipoPill}}</td>
    <td>${{(r.Peso_Sugerido * 100).toFixed(2)}}%</td>
    <td><span style="color:${{esPesos ? '#10b981' : '#f59e0b'}};font-weight:600;">${{tir}}%</span></td>
    <td style="color:#64748b;font-size:12px;">Buy & Hold · Revisión mensual</td>
  `;
  tbodyRF.appendChild(tr);
}});

// ── Tabla Watchlist ───────────────────────────────────────────────────────
const tbodyW = $('tbodyWatch');
D.watchlist.forEach(w => {{
  const tr = document.createElement('tr');
  tr.innerHTML = `
    <td><strong style="color:#ef4444;">${{w.ticker}}</strong></td>
    <td style="color:#64748b;">${{w.sector}}</td>
    <td><span style="color:#f59e0b;font-weight:700;">${{w.ff_score.toFixed(2)}}</span></td>
    <td><span class="pill pill-watch">⚠️ ${{w.motivo}}</span></td>
    <td style="font-size:12px;color:#64748b;">Monitorear MA52 — si supera, considerar ingreso a cartera</td>
  `;
  tbodyW.appendChild(tr);
}});
</script>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n═══════════════════════════════════════════════════════")
    print("📊  GENERADOR DE DASHBOARD PROFESIONAL")
    print("    Modelo Fama-French 6 · Tres Pilares")
    print("═══════════════════════════════════════════════════════\n")

    # 1. Cargar cartera
    print("⏳ [1/4] Leyendo Portfolio_Recommendation.csv...")
    df_cartera = cargar_cartera()
    print(f"   ✅ {len(df_cartera)} instrumentos cargados.")

    # 2. EMBI 2025
    print("⏳ [2/4] Cargando EMBI+ 2025 desde DuckDB...")
    df_embi = cargar_embi_2025()
    print(f"   ✅ {len(df_embi)} observaciones del Riesgo País 2025.")

    # 3. Rendimiento 6M
    print("⏳ [3/4] Calculando rendimiento 6M en dólar MEP (yfinance)...")
    rendimiento = calcular_rendimiento_6m(df_cartera)
    retorno = rendimiento["retorno_total"]
    print(f"   ✅ Retorno acumulado 6M (USD MEP): {'+' if retorno >= 0 else ''}{retorno:.1f}%")

    # 4. Generar HTML
    print("⏳ [4/4] Generando Dashboard HTML...")
    datos_js = preparar_datos_js(df_cartera, df_embi, rendimiento)
    html = generar_html(datos_js)
    SALIDA_HTML.write_text(html, encoding="utf-8")
    print(f"\n✅  Dashboard generado exitosamente:")
    print(f"   📄  {SALIDA_HTML}\n")

    # Abrir en browser
    webbrowser.open(SALIDA_HTML.as_uri())


if __name__ == "__main__":
    main()
