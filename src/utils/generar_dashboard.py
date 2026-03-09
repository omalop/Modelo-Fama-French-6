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
SALIDA_HTML    = RAIZ / "index.html"

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
def obtener_nombres_yf(tickers: list) -> list:
    nombres = []
    for t in tickers:
        t_yf = t if (t.endswith(".BA") or t.endswith(".SA")) else t + ".BA"
        if "RV_Global" in t: t_yf = t # Patch for global if needed
        try:
            info = yf.Ticker(t_yf).info
            nombre = info.get('shortName') or info.get('longName') or t
            # Limpiar algunos sufijos comunes de Yahoo
            nombre = nombre.replace(" S.A.", "").replace(" SA", "").replace(" Inc.", "").split(",")[0].strip()
            nombres.append(nombre)
        except:
            nombres.append(t)
    return nombres

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
        "rv_local": { 
            "tickers": rv_local["Ticker"].tolist(), 
            "nombres": obtener_nombres_yf(rv_local["Ticker"].tolist()),
            "pesos": (rv_local["Peso_Sugerido"] * 100).round(1).tolist() 
        },
        "rv_global": { 
            "tickers": rv_global["Ticker"].tolist(), 
            "nombres": obtener_nombres_yf(rv_global["Ticker"].tolist()),
            "pesos": (rv_global["Peso_Sugerido"] * 100).round(1).tolist() 
        },
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
<html class="dark" lang="es"><head>
<meta charset="utf-8"/>
<meta content="width=device-width, initial-scale=1.0" name="viewport"/>
<title>Dashboard Cartera — Modelo Fama-French 6</title>
<script src="https://cdn.tailwindcss.com?plugins=forms,container-queries"></script>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet"/>
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet"/>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script id="tailwind-config">
        tailwind.config = {{
            darkMode: "class",
            theme: {{
                extend: {{
                    colors: {{
                        "primary": "#258aef",
                        "background-light": "#f6f7f8",
                        "background-dark": "#0B0E11",
                        "card-dark": "#161b22",
                        "success": "#10b981",
                        "danger": "#ef4444",
                    }},
                    fontFamily: {{
                        "display": ["Inter", "sans-serif"]
                    }},
                    borderRadius: {{"DEFAULT": "0.25rem", "lg": "0.5rem", "xl": "0.75rem", "full": "9999px"}},
                }},
            }},
        }}
    </script>
<style>
        body {{ font-family: 'Inter', sans-serif; }}
        .glass-panel {{ background: rgba(22, 27, 34, 0.7); backdrop-filter: blur(12px); border: 1px solid rgba(255, 255, 255, 0.05); }}
        .custom-scrollbar::-webkit-scrollbar {{ width: 4px; }}
        .custom-scrollbar::-webkit-scrollbar-track {{ background: transparent; }}
        .custom-scrollbar::-webkit-scrollbar-thumb {{ background: #233648; border-radius: 10px; }}
    </style>
</head>
<body class="bg-background-light dark:bg-background-dark text-slate-900 dark:text-slate-100 min-h-screen flex overflow-hidden">
<!-- Left Sidebar -->
<aside class="w-64 border-r border-slate-200 dark:border-slate-800 flex flex-col h-screen bg-white dark:bg-background-dark shrink-0">
<div class="p-6 flex flex-col gap-1">
<h1 class="text-primary text-xl font-black tracking-tight flex items-center gap-2">
<span class="material-symbols-outlined text-primary">analytics</span> QUANT TERMINAL </h1>
<p class="text-slate-500 dark:text-slate-400 text-xs font-medium uppercase tracking-widest">Premium Analytics v6.2</p>
</div>
<nav class="flex-1 px-4 space-y-1 mt-4">
<a href="index.html" class="flex items-center gap-3 px-3 py-2 bg-primary/10 text-primary rounded-lg transition-colors">
<span class="material-symbols-outlined">dashboard</span><span class="text-sm font-medium">Dashboard</span></a>
<a href="curvas.html" class="flex items-center gap-3 px-3 py-2 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors">
<span class="material-symbols-outlined">monitoring</span><span class="text-sm font-medium">Monitor de Curvas</span></a>
<a href="carry_trade.html" class="flex items-center gap-3 px-3 py-2 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors">
<span class="material-symbols-outlined">currency_exchange</span><span class="text-sm font-medium">Carry Trade</span></a>
<a href="cartera.html" class="flex items-center gap-3 px-3 py-2 text-slate-500 dark:text-slate-400 hover:bg-slate-100 dark:hover:bg-slate-800 rounded-lg transition-colors">
<span class="material-symbols-outlined">account_balance_wallet</span><span class="text-sm font-medium">Portafolio y Diario</span></a>
</nav>
<div class="p-4 mt-auto border-t border-slate-200 dark:border-slate-800">
<div class="mt-4 flex items-center gap-3 px-3 py-2">
<div class="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center text-primary font-bold text-xs">OM</div>
<div class="flex flex-col">
<span class="text-sm font-bold">Omar Lopez</span>
<span class="text-[10px] text-slate-500 uppercase font-bold">Pro Member</span>
</div>
</div>
</div>
</aside>
<!-- Main Content Area -->
<main class="flex-1 flex flex-col h-screen overflow-y-auto custom-scrollbar">
<header class="h-16 border-b border-slate-200 dark:border-slate-800 flex items-center justify-between px-8 bg-white/50 dark:bg-background-dark/50 backdrop-blur-md sticky top-0 z-10">
<div class="flex items-center gap-4">
<h2 class="text-lg font-bold">Fama-French Market Overview</h2>
<div class="flex items-center gap-2 px-3 py-1 bg-success/10 text-success rounded-full text-xs font-bold">
<span class="relative flex h-2 w-2"><span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-success opacity-75"></span><span class="relative inline-flex rounded-full h-2 w-2 bg-success"></span></span> Live Data
</div>
</div>
<div class="flex items-center gap-4">
<div class="flex items-center gap-2 text-sm font-medium text-slate-500"><span>Último Run: <span id="runDate"></span></span></div>
</div>
</header>
<div class="p-8 space-y-6">
<!-- Row 1: Portfolio Summary -->
<section class="grid grid-cols-1 lg:grid-cols-3 gap-6">
<!-- Donut & Allocation -->
<div class="lg:col-span-2 glass-panel rounded-xl p-6 flex flex-col md:flex-row items-center gap-8">
<div class="relative w-48 h-48 flex items-center justify-center">
<canvas id="chartPilares"></canvas>
</div>
<div class="flex-1 grid grid-cols-1 sm:grid-cols-2 gap-4 w-full">
<div class="flex flex-col gap-1">
<h3 class="text-slate-500 text-xs font-bold uppercase tracking-wider">Asset Allocation</h3>
<div class="space-y-3 mt-2" id="allocationList"></div>
</div>
<div class="flex flex-col justify-center gap-2">
<div class="bg-primary/10 border border-primary/20 rounded-lg p-3 flex items-center justify-between">
<div>
<p class="text-[10px] font-bold text-primary uppercase">Current Signal</p>
<p class="text-sm font-black text-primary" id="divTxt">Neutral</p>
</div>
<span class="material-symbols-outlined text-primary">trending_up</span>
</div>
<div class="mt-2 text-xs font-bold text-slate-400">
Prob Crisis Sistémica: <span id="probCrisisTxt" class="text-danger">0%</span><br>
Confianza Gobierno: <span id="confTxt" class="text-success">0%</span>
</div>
</div>
</div>
</div>
<!-- KPIs -->
<div class="grid grid-cols-2 gap-3" id="kpiGrid">
    <div class="glass-panel rounded-xl p-4 flex flex-col justify-center">
        <p class="text-slate-500 text-[10px] font-bold uppercase">Retorno 6M</p>
        <p class="text-lg font-black text-success" id="kpiRet6m">0%</p>
    </div>
    <div class="glass-panel rounded-xl p-4 flex flex-col justify-center">
        <p class="text-slate-500 text-[10px] font-bold uppercase">CAGR (1A)</p>
        <p class="text-lg font-black text-primary" id="kpiCAGR">0%</p>
    </div>
    <div class="glass-panel rounded-xl p-4 flex flex-col justify-center">
        <p class="text-slate-500 text-[10px] font-bold uppercase">Beta SPY</p>
        <p class="text-lg font-black text-slate-100" id="kpiBetaSPY">0.0</p>
    </div>
    <div class="glass-panel rounded-xl p-4 flex flex-col justify-center">
        <p class="text-slate-500 text-[10px] font-bold uppercase">Beta MERV</p>
        <p class="text-lg font-black text-slate-100" id="kpiBetaMerv">0.0</p>
    </div>
    <div class="glass-panel rounded-xl p-4 flex flex-col justify-center">
        <p class="text-slate-500 text-[10px] font-bold uppercase">Sharpe</p>
        <p class="text-lg font-black text-purple-400" id="kpiSharpe">0.0</p>
    </div>
    <div class="glass-panel rounded-xl p-4 flex flex-col justify-center">
        <p class="text-slate-500 text-[10px] font-bold uppercase">Max DD</p>
        <p class="text-lg font-black text-danger" id="kpiMaxDD">0%</p>
    </div>
</div>
</section>

<!-- Row 2: Asset Metrics Tables -->
<section class="grid grid-cols-1 xl:grid-cols-2 gap-6">
<!-- Stocks/FF Rankings -->
<div class="glass-panel rounded-xl overflow-hidden flex flex-col">
<div class="px-6 py-4 border-b border-slate-800 bg-slate-800/30 flex justify-between items-center">
<h3 class="text-sm font-bold flex items-center gap-2"><span class="material-symbols-outlined text-primary text-lg">public</span> Renta Variable (Local & Global)</h3>
</div>
<div class="overflow-x-auto">
<table class="w-full text-left text-sm">
<thead>
<tr class="text-slate-500 border-b border-slate-800">
<th class="px-6 py-3 font-bold uppercase text-[10px]">Ticker</th>
<th class="px-6 py-3 font-bold uppercase text-[10px]">Región</th>
<th class="px-6 py-3 font-bold uppercase text-[10px]">Peso</th>
</tr>
</thead>
<tbody class="divide-y divide-slate-800" id="tbodyRV"></tbody>
</table>
</div>
</div>
<!-- Fixed Income -->
<div class="glass-panel rounded-xl overflow-hidden flex flex-col">
<div class="px-6 py-4 border-b border-slate-800 bg-slate-800/30 flex justify-between items-center">
<h3 class="text-sm font-bold flex items-center gap-2"><span class="material-symbols-outlined text-emerald-500 text-lg">account_balance</span> Renta Fija Sugerida</h3>
</div>
<div class="overflow-x-auto">
<table class="w-full text-left text-sm">
<thead>
<tr class="text-slate-500 border-b border-slate-800">
<th class="px-6 py-3 font-bold uppercase text-[10px]">Ticker</th>
<th class="px-6 py-3 font-bold uppercase text-[10px]">Tipo</th>
<th class="px-6 py-3 font-bold uppercase text-[10px]">TIR / Rend</th>
<th class="px-6 py-3 font-bold uppercase text-[10px]">Peso</th>
</tr>
</thead>
<tbody class="divide-y divide-slate-800" id="tbodyRF"></tbody>
</table>
</div>
</div>
</section>

<!-- Row 3: Curve Monitor -->
<section class="grid grid-cols-1 gap-6">
<div class="glass-panel rounded-xl flex flex-col">
<div class="p-6 border-b border-slate-800"><h3 class="text-sm font-bold flex items-center gap-2"><span class="material-symbols-outlined text-primary text-lg">show_chart</span> Rendimiento Proyectado 6M vs Benchmarks</h3></div>
<div class="p-6 flex-1 min-h-[350px] relative w-full"><canvas id="chartMain"></canvas></div>
</div>
</section>
</div>
</main>
<script>
const D = {datos_js};
document.getElementById('runDate').textContent = D.meta.fecha_run || D.fecha_gen;

document.getElementById('kpiRet6m').textContent = (D.rend.retorno_total > 0 ? '+' : '') + D.rend.retorno_total + '%';
document.getElementById('kpiCAGR').textContent = ((D.meta.cagr_cartera || 0)*100).toFixed(1) + '%';
document.getElementById('kpiBetaSPY').textContent = D.meta.beta_cartera_spy || '--';
document.getElementById('kpiBetaMerv').textContent = D.meta.beta_cartera_merv || '--';
document.getElementById('kpiSharpe').textContent = D.meta.sharpe_cartera || '0.0';
document.getElementById('kpiMaxDD').textContent = ((D.meta.max_dd_1a || 0)*100).toFixed(1) + '%';

document.getElementById('probCrisisTxt').textContent = ((D.meta.prob_crisis||0)*100).toFixed(0) + '%';
document.getElementById('confTxt').textContent = (D.meta.confianza_gob||0) + '%';
document.getElementById('divTxt').textContent = D.meta.divergencia?.tipo || 'Neutral';

const colors = ['#258aef', '#10b981', '#64748b'];
const allocHtml = D.pilares.labels.map((l, i) => `
    <div class="flex items-center justify-between text-sm">
        <span class="flex items-center gap-2"><span class="w-2 h-2 rounded-full" style="background:${{colors[i]}}"></span> ${{l}}</span>
        <span class="font-bold">${{D.pilares.valores[i]}}%</span>
    </div>
`).join('');
document.getElementById('allocationList').innerHTML = allocHtml;

new Chart(document.getElementById('chartPilares'), {{
    type: 'doughnut',
    data: {{ labels: D.pilares.labels, datasets: [{{ data: D.pilares.valores, backgroundColor: colors, borderWidth:0, cutout: '75%' }}] }},
    options: {{ maintainAspectRatio:false, plugins:{{ legend:{{display:false}} }} }}
}});

new Chart(document.getElementById('chartMain'), {{
    type: 'line',
    data: {{
        labels: D.rend.fechas,
        datasets: [
            {{ label: 'Cartera', data: D.rend.cartera, borderColor: '#258aef', borderWidth: 3, pointRadius: 0, tension:0.2, fill: true, backgroundColor:'rgba(37, 138, 239, 0.1)' }},
            {{ label: 'S&P 500', data: D.rend.spy, borderColor: '#64748b', borderWidth: 1.5, borderDash: [5,5], pointRadius: 0, fill: false }},
            {{ label: 'Merval (CCL)', data: D.rend.merval, borderColor: '#10b981', borderWidth: 1.5, pointRadius: 0, fill: false }}
        ]
    }},
    options: {{ 
        responsive: true, maintainAspectRatio: false, 
        scales: {{ 
            y: {{ grid:{{color:'rgba(255,255,255,0.05)'}}, ticks:{{color:'#94a3b8', callback:v=>(v*100-100).toFixed(0)+'%'}} }},
            x: {{ grid:{{display:false}}, ticks:{{color:'#94a3b8', maxTicksLimit:8}} }}
        }},
        plugins: {{ legend: {{ labels: {{ color: '#f8fafc', boxWidth:12 }} }} }}
    }}
}});

const tbodyRV = document.getElementById('tbodyRV');
D.rv_global.tickers.forEach((t, i) => {{
    let n = D.rv_global.nombres[i];
    tbodyRV.innerHTML += `<tr class="hover:bg-slate-800/20"><td class="px-6 py-4"><span class="font-bold text-primary block">${{n}}</span><span class="text-xs text-slate-500">${{t}}</span></td><td class="px-6 py-4 text-slate-400">Global CEDEAR</td><td class="px-6 py-4 font-bold">${{D.rv_global.pesos[i]}}%</td></tr>`;
}});
D.rv_local.tickers.forEach((t, i) => {{
    let n = D.rv_local.nombres[i];
    tbodyRV.innerHTML += `<tr class="hover:bg-slate-800/20"><td class="px-6 py-4"><span class="font-bold text-emerald-500 block">${{n}}</span><span class="text-xs text-slate-500">${{t}}</span></td><td class="px-6 py-4 text-slate-400">Local Merval</td><td class="px-6 py-4 font-bold">${{D.rv_local.pesos[i]}}%</td></tr>`;
}});

const tbodyRF = document.getElementById('tbodyRF');
D.rf_items.forEach(r => {{
    const isPs = r.Instrumento.includes('Pesos');
    tbodyRF.innerHTML += `
        <tr class="hover:bg-slate-800/20">
            <td class="px-6 py-4 font-bold text-amber-500">${{r.Ticker}}</td>
            <td class="px-6 py-4"><span class="px-2 py-0.5 rounded text-xs font-bold ${{isPs?'bg-emerald-500/20 text-emerald-500':'bg-amber-500/20 text-amber-500'}}">${{isPs?'PESOS/CER':'USD HD'}}</span></td>
            <td class="px-6 py-4 font-bold text-slate-300">${{(r.Retorno_Esperado*100).toFixed(1)}}%</td>
            <td class="px-6 py-4 font-bold">${{(r.Peso_Sugerido*100).toFixed(1)}}%</td>
        </tr>`;
}});
</script>
</body></html>"""


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
