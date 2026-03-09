"""
Generador de Dashboard Profesional de Cartera.

Versión 6.2 - Corregido cálculo de retornos históricos y KPIs duales.
"""

import json
import logging
import os
import sys
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

# Forzar UTF-8 en Windows
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import duckdb
import numpy as np
import pandas as pd
import yfinance as yf

# ── Config ────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

RAIZ           = Path(__file__).resolve().parents[2]
DB_PATH        = RAIZ / "data" / "docta_cache.duckdb"
CSV_CARTERA    = RAIZ / "data" / "processed" / "Portfolio_Recommendation.csv"
JSON_METADATA  = RAIZ / "data" / "processed" / "Metadata_Allocation.json"
SALIDA_HTML    = RAIZ / "data" / "processed" / "Dashboard_Final.html"

WATCHLIST_MOMENTUM = {
    "BHIP.BA": {"sector": "Financial Services", "ff_score": 0.89, "motivo": "Precio < MA52 (corrección técnica)"},
    "HAPV3.SA": {"sector": "Financial Services", "ff_score": 1.84, "motivo": "Precio < MA52 (corrección técnica)"},
}

# ── 1. Cargar datos ───────────────────────────────────────────────────────────
def cargar_cartera() -> pd.DataFrame:
    if not CSV_CARTERA.exists():
        return pd.DataFrame()
    df = pd.read_csv(CSV_CARTERA)
    df.columns = [c.strip() for c in df.columns]
    return df

def cargar_metadatos() -> dict:
    if JSON_METADATA.exists():
        try:
            with open(JSON_METADATA, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error cargando metadatos: {e}")
    return {}

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
        return pd.DataFrame()

# ── 3. Rendimiento histórico 6M Multi-Benchmark ── ROBUSTO ──────────────────
def calcular_rendimiento_multibenchmark(df_cartera: pd.DataFrame) -> dict:
    fin    = datetime.today()
    inicio = fin - timedelta(days=182)

    # 1. MEP Proxy (Alineado)
    mep_series = None
    try:
        # Usamos periodos más largos para asegurar que el reindex inicial tenga datos
        ggal_usd = yf.download("GGAL", start=inicio - timedelta(days=10), end=fin, progress=False)
        ggal_ars = yf.download("GGAL.BA", start=inicio - timedelta(days=10), end=fin, progress=False)
        
        g_usd = ggal_usd.xs('Close', level='Price', axis=1).squeeze() if isinstance(ggal_usd.columns, pd.MultiIndex) else ggal_usd['Close']
        g_ars = ggal_ars.xs('Close', level='Price', axis=1).squeeze() if isinstance(ggal_ars.columns, pd.MultiIndex) else ggal_ars['Close']
        
        g_usd.index = pd.to_datetime(g_usd.index).normalize().tz_localize(None)
        g_ars.index = pd.to_datetime(g_ars.index).normalize().tz_localize(None)
        
        idx_union = g_usd.index.intersection(g_ars.index)
        mep_series = (g_ars.loc[idx_union] / g_usd.loc[idx_union] * 10).dropna()
        mep_series = mep_series[mep_series.index >= inicio.strftime('%Y-%m-%d')]
    except Exception as e:
        logger.warning(f"MEP/CCL proxy no disponible: {e}")

    if mep_series is None or mep_series.empty:
        return {"fechas": [], "cartera": [], "spy": [], "merval": [], "retorno_total": 0}

    common_index = mep_series.index

    # 2. Benchmarks
    spy_vals = []
    merv_vals = []
    try:
        spy = yf.download("^GSPC", start=mep_series.index[0], end=fin, progress=False)
        spy = spy.xs('Close', level='Price', axis=1).squeeze() if isinstance(spy.columns, pd.MultiIndex) else spy['Close']
        spy.index = pd.to_datetime(spy.index).normalize().tz_localize(None)
        spy = spy.reindex(common_index).ffill()
        spy_norm = spy / spy.iloc[0]
        spy_vals = [round(v, 4) for v in spy_norm.values]

        merv = yf.download("^MERV", start=mep_series.index[0], end=fin, progress=False)
        merv = merv.xs('Close', level='Price', axis=1).squeeze() if isinstance(merv.columns, pd.MultiIndex) else merv['Close']
        merv.index = pd.to_datetime(merv.index).normalize().tz_localize(None)
        merv_usd = (merv.reindex(common_index).ffill() / mep_series).dropna()
        merv_norm = merv_usd / merv_usd.iloc[0]
        merv_vals = [round(v, 4) for v in merv_norm.values]
    except Exception as e:
        logger.warning(f"Error en benchmarks: {e}")

    # 3. Cartera
    def ticker_yf(t, inst):
        if t.endswith(".BA") or t.endswith(".SA"): return t
        if "RV_Global" in inst: return t
        return t + ".BA"

    ret_cartera = pd.Series(0.0, index=common_index)
    peso_efectivo = 0.0
    
    for _, fila in df_cartera.iterrows():
        t = ticker_yf(fila["Ticker"], fila["Instrumento"])
        p = fila["Peso_Sugerido"]
        try:
            h = yf.download(t, start=mep_series.index[0] - timedelta(days=7), end=fin, progress=False, timeout=15)
            if h.empty: continue
            
            h = h.xs('Close', level='Price', axis=1).squeeze() if isinstance(h.columns, pd.MultiIndex) else h['Close']
            h.index = pd.to_datetime(h.index).normalize().tz_localize(None)
            h = h.reindex(common_index).ffill()
            
            p0 = h.dropna().iloc[0]
            if p0 == 0 or np.isnan(p0): continue
            
            if t.endswith(".BA") or t.endswith(".SA") or fila["Instrumento"].startswith("RF_Local"):
                h_usd = h / mep_series
            else:
                h_usd = h
            
            p0_usd = h_usd.dropna().iloc[0]
            norm_series = h_usd / p0_usd
            
            # Solo sumamos si la serie tiene datos validos al inicio
            if not np.isnan(norm_series.iloc[0]):
                ret_cartera = ret_cartera.add(norm_series.fillna(0) * p, fill_value=0)
                peso_efectivo += p
        except Exception as e:
            logger.debug(f"Ticker {t} falló: {e}")
            continue

    if peso_efectivo > 0:
        ret_cartera = ret_cartera / peso_efectivo
    
    # Aseguramos que el retorno empiece en 1.0 (0%)
    if not ret_cartera.empty and ret_cartera.iloc[0] != 0:
        ret_cartera = ret_cartera / ret_cartera.iloc[0]
    
    return {
        "fechas":  common_index.strftime("%Y-%m-%d").tolist(),
        "cartera": [round(v, 4) for v in ret_cartera.values],
        "spy":     spy_vals,
        "merval":  merv_vals,
        "retorno_total": round((ret_cartera.iloc[-1]-1)*100, 2) if not ret_cartera.empty else 0
    }

# ── 4. Preparar datos JS ──────────────────────────────────────────────────────
def preparar_datos_js(df_cartera: pd.DataFrame, df_embi: pd.DataFrame, rend: dict, meta: dict) -> str:
    rv_local  = df_cartera[df_cartera["Instrumento"] == "RV_Local"]
    rv_global = df_cartera[df_cartera["Instrumento"] == "RV_Global"]
    rf_rows   = df_cartera[~df_cartera["Instrumento"].isin(["RV_Local", "RV_Global"])]

    datos = {
        "pilares": {
            "labels":  ["RV Local", "RV Global", "Renta Fija"],
            "valores": [round(rv_local["Peso_Sugerido"].sum()*100,1), 
                        round(rv_global["Peso_Sugerido"].sum()*100,1), 
                        round(rf_rows["Peso_Sugerido"].sum()*100,1)]
        },
        "rv_local": { "tickers": rv_local["Ticker"].tolist(), "pesos": (rv_local["Peso_Sugerido"] * 100).round(1).tolist() },
        "rv_global": { "tickers": rv_global["Ticker"].tolist(), "pesos": (rv_global["Peso_Sugerido"] * 100).round(1).tolist() },
        "rf_items": rf_rows.to_dict("records"),
        "embi": { "fechas": df_embi["fecha"].tolist(), "valores": df_embi["embi_puntos"].tolist() },
        "rend": rend,
        "meta": meta,
        "fecha_gen": datetime.now().strftime("%d/%m/%Y %H:%M")
    }
    return json.dumps(datos, ensure_ascii=False)

# ── 5. HTML Template ──────────────────────────────────────────────────────────
def generar_html(datos_js: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Dashboard Cartera — Modelo Fama-French 6</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
:root {{
    --bg: #0b0e14;
    --card: #151921;
    --border: #242b36;
    --accent: #6366f1;
    --accent2: #06b6d4;
    --green: #10b981;
    --red: #ef4444;
    --orange: #f59e0b;
    --text: #f8fafc;
    --muted: #94a3b8;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text); font-family: 'Outfit', sans-serif; font-size: 14px; padding: 24px; line-height: 1.5; }}
.container {{ max-width: 1440px; margin: 0 auto; display: grid; grid-template-columns: repeat(12, 1fr); gap: 16px; }}
.header {{ grid-column: span 12; display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }}
.header h1 {{ font-size: 24px; font-weight: 700; letter-spacing: -0.02em; }}
.header .date {{ color: var(--muted); font-size: 11px; }}

.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 16px; position: relative; }}
.card-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 16px; font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--muted); font-weight: 700; border-bottom: 1px solid rgba(255,255,255,0.03); padding-bottom: 8px; }}

/* KPIs */
.kpi-row {{ grid-column: span 12; display: grid; grid-template-columns: repeat(7, 1fr); gap: 12px; }}
.kpi-card {{ text-align: center; display: flex; flex-direction: column; justify-content: center; height: 100px; }}
.kpi-val {{ font-size: 26px; font-weight: 700; display: block; }}
.kpi-label {{ color: var(--muted); font-size: 10px; text-transform: uppercase; margin-top: 4px; }}

.span-8 {{ grid-column: span 8; }}
.span-4 {{ grid-column: span 4; }}
.span-6 {{ grid-column: span 6; }}
.chart-h {{ height: 280px; position: relative; }}

/* Tables */
.tabla {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
.tabla th {{ text-align: left; padding: 10px; color: var(--muted); font-weight: 600; border-bottom: 1px solid var(--border); font-size: 10px; text-transform: uppercase; }}
.tabla td {{ padding: 10px; border-bottom: 1px solid rgba(255,255,255,0.02); }}
.pill {{ padding: 3px 8px; border-radius: 4px; font-size: 10px; font-weight: 700; }}
.pill-hd {{ background: rgba(245,158,11,0.1); color: var(--orange); }}
.pill-ps {{ background: rgba(16,185,129,0.1); color: var(--green); }}

.badge-macro {{ background: rgba(255,255,255,0.03); padding: 8px 12px; border-radius: 8px; display: flex; justify-content: space-between; margin-bottom: 6px; font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>Portfolio Optimizer Pro <span style="color:var(--accent)">v6.2</span></h1>
        <div class="date">Último Run: <span id="runDate"></span></div>
    </div>

    <!-- KPIs -->
    <div class="kpi-row">
        <div class="card kpi-card">
            <span class="kpi-val" id="kpiRet6m" style="color:var(--green)">0%</span>
            <span class="kpi-label">Retorno 6M (USD)</span>
        </div>
        <div class="card kpi-card" title="CAGR Anualizado Histórico 1A">
            <span class="kpi-val" id="kpiCAGR" style="color:var(--accent2)">0%</span>
            <span class="kpi-label">CAGR (1A)</span>
        </div>
        <div class="card kpi-card">
            <span class="kpi-val" id="kpiBetaSPY">0.00</span>
            <span class="kpi-label">Beta SPY</span>
        </div>
        <div class="card kpi-card">
            <span class="kpi-val" id="kpiBetaMerv">0.00</span>
            <span class="kpi-label">Beta MERV</span>
        </div>
        <div class="card kpi-card">
            <span class="kpi-val" id="kpiVol" style="color:var(--muted)">0%</span>
            <span class="kpi-label">Volatilidad</span>
        </div>
        <div class="card kpi-card">
            <span class="kpi-val" id="kpiSharpe" style="color:#a5b4fc">0.0</span>
            <span class="kpi-label">Sharpe Ratio</span>
        </div>
        <div class="card kpi-card">
            <span class="kpi-val" id="kpiMaxDD" style="color:var(--red)">0%</span>
            <span class="kpi-label">Max Drawdown</span>
        </div>
    </div>

    <!-- CHART -->
    <div class="card span-8">
        <div class="card-header">📈 Rendimiento Proyectado 6M vs Benchmarks (USD)</div>
        <div class="chart-h"><canvas id="chartMain"></canvas></div>
    </div>

    <!-- MACRO -->
    <div class="card span-4">
        <div class="card-header">🚦 Señales Macro y Escenario</div>
        <div id="macroSignals"></div>
        <div style="margin-top:16px;">
            <div class="badge-macro"><span>Prob. Crisis Sistémica</span><span id="probCrisisTxt" style="font-weight:700;">0%</span></div>
            <div class="badge-macro"><span>Confianza Gobierno</span><span id="confTxt" style="font-weight:700; color:var(--accent2);">0%</span></div>
            <div class="badge-macro"><span>Divergencia RF/RV</span><span id="divTxt" style="font-weight:700;">Neutral</span></div>
        </div>
    </div>

    <!-- PILLARS -->
    <div class="card span-4">
        <div class="card-header">🎯 Distribución por Pilares</div>
        <div style="height:250px;"><canvas id="chartPilares"></canvas></div>
    </div>

    <!-- BONDS -->
    <div class="card span-8">
        <div class="card-header">🛡️ Renta Fija Sugerida</div>
        <table class="tabla">
            <thead>
                <tr><th>Ticker</th><th>Tipo</th><th>Tir (%)</th><th>TEM / TNA</th><th>Peso</th></tr>
            </thead>
            <tbody id="tbodyRF"></tbody>
        </table>
    </div>

    <!-- RV TABLES -->
    <div class="card span-6">
        <div class="card-header">🌎 RV Global (Unificado)</div>
        <div class="chart-h"><canvas id="chartRVG"></canvas></div>
    </div>
    <div class="card span-6">
        <div class="card-header">🇦🇷 RV Local (Argentina)</div>
        <div class="chart-h"><canvas id="chartRVL"></canvas></div>
    </div>
</div>

<script>
const D = {datos_js};
document.getElementById('runDate').textContent = D.meta.fecha_run || D.fecha_gen;

// KPIs
document.getElementById('kpiRet6m').textContent = (D.rend.retorno_total > 0 ? '+' : '') + D.rend.retorno_total + '%';
document.getElementById('kpiCAGR').textContent = ((D.meta.cagr_cartera || 0)*100).toFixed(1) + '%';
document.getElementById('kpiBetaSPY').textContent = D.meta.beta_cartera_spy || '--';
document.getElementById('kpiBetaMerv').textContent = D.meta.beta_cartera_merv || '--';
document.getElementById('kpiVol').textContent = ((D.meta.vol_anual || 0)*100).toFixed(1) + '%';
document.getElementById('kpiSharpe').textContent = D.meta.sharpe_cartera || '0.0';
document.getElementById('kpiMaxDD').textContent = ((D.meta.max_dd_1a || 0)*100).toFixed(1) + '%';

// Macro
const signalsCtx = document.getElementById('macroSignals');
const sigNames = {{'Curva_10Y2Y':'Curva 10Y/2Y USA','High_Yield':'US High Yield','VIX':'Índice VIX'}};
const levelColor = {{0:'#10b981', 1:'#f59e0b', 2:'#ef4444', '-1':'#475569'}};
Object.entries(D.meta.signals_crisis || {{}}).forEach(([k,v]) => {{
    const div = document.createElement('div');
    div.className = 'badge-macro';
    div.innerHTML = `<span>${{sigNames[k]||k}}</span><span style="color:${{levelColor[v]}}">${{v==0?'Normal':v==1?'Elevado':'Crítico'}}</span>`;
    signalsCtx.appendChild(div);
}});
document.getElementById('probCrisisTxt').textContent = ((D.meta.prob_crisis||0)*100).toFixed(0) + '%';
document.getElementById('confTxt').textContent = (D.meta.confianza_gob||0) + '%';
document.getElementById('divTxt').textContent = D.meta.divergencia?.tipo || 'Neutral';

// Main Chart
new Chart(document.getElementById('chartMain'), {{
    type: 'line',
    data: {{
        labels: D.rend.fechas,
        datasets: [
            {{ label: 'Cartera Sugerida', data: D.rend.cartera, borderColor: '#6366f1', borderWidth: 2.5, pointRadius: 0, tension:0.2, fill: false }},
            {{ label: 'S&P 500 (USD)', data: D.rend.spy, borderColor: '#94a3b8', borderWidth: 1, borderDash: [5,5], pointRadius: 0, fill: false }},
            {{ label: 'Merval (USD-CCL)', data: D.rend.merval, borderColor: '#10b981', borderWidth: 1, pointRadius: 0, fill: false }}
        ]
    }},
    options: {{ 
        responsive: true, maintainAspectRatio: false, 
        scales: {{ 
            y: {{ grid:{{color:'#242b36'}}, ticks:{{callback:v=>(v*100-100).toFixed(0)+'%'}} }},
            x: {{ grid:{{display:false}}, ticks:{{maxTicksLimit:10}} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#fff', boxWidth:12 }} }} }}
    }}
}});

// Pillars
new Chart(document.getElementById('chartPilares'), {{
    type: 'doughnut',
    data: {{ labels: D.pilares.labels, datasets: [{{ data: D.pilares.valores, backgroundColor: ['#6366f1','#06b6d4','#f59e0b'], borderWidth:0 }}] }},
    options: {{ maintainAspectRatio:false, plugins:{{ legend:{{position:'bottom', labels:{{color:'#fff', boxWidth:12}}}} }} }}
}});

// RV Bar Charts
const barCfg = (labels, data, color) => ({{
    type: 'bar', data: {{ labels, datasets: [{{ data, backgroundColor: color, borderRadius: 4 }}] }},
    options: {{ indexAxis:'y', maintainAspectRatio:false, plugins:{{legend:{{display:false}}}}, scales:{{ x:{{grid:{{display:false}}, ticks:{{callback:v=>v+'%'}} }}, y:{{grid:{{display:false}}}} }} }}
}});
new Chart(document.getElementById('chartRVG'), barCfg(D.rv_global.tickers, D.rv_global.pesos, '#06b6d4'));
new Chart(document.getElementById('chartRVL'), barCfg(D.rv_local.tickers, D.rv_local.pesos, '#6366f1'));

// RF Table
const tbody = document.getElementById('tbodyRF');
D.rf_items.forEach(r => {{
    const row = document.createElement('tr');
    const isPs = r.Instrumento.includes('Pesos');
    row.innerHTML = `
        <td><strong style="color:#fff">${{r.Ticker}}</strong></td>
        <td><span class="pill ${{isPs?'pill-ps':'pill-hd'}}">${{isPs?'PESOS/CER':'USD HD'}}</span></td>
        <td style="color:${{isPs?'var(--green)':'var(--orange)'}}; font-weight:700;">${{(r.Retorno_Esperado*100).toFixed(1)}}%</td>
        <td style="color:var(--muted); font-size:11px;">TEM: ${{r.TEM? (r.TEM*100).toFixed(2)+'%':'--'}} / TNA: ${{r.TNA?(r.TNA*100).toFixed(2)+'%':'--'}}</td>
        <td>${{(r.Peso_Sugerido*100).toFixed(1)}}%</td>
    `;
    tbody.appendChild(row);
}});
</script>
</body>
</html>"""

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*60)
    print("📊  DASHBOARD GENERATOR v6.2")
    print("="*60)

    df_cartera = cargar_cartera()
    meta = cargar_metadatos()
    df_embi = cargar_embi_2025()
    
    print("⌛ Calculando rendimiento multi-benchmark (6M)...")
    rend = calcular_rendimiento_multibenchmark(df_cartera)
    
    print("⌛ Preparando Dashboard HTML...")
    datos_js = preparar_datos_js(df_cartera, df_embi, rend, meta)
    html = generar_html(datos_js)
    
    SALIDA_HTML.write_text(html, encoding="utf-8")
    print(f"\n✅ Dashboard generado exitosamente.")
    webbrowser.open(SALIDA_HTML.as_uri())

if __name__ == "__main__":
    main()
