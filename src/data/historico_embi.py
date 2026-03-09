"""
Extracción de Datos Históricos de Riesgo País (EMBI+ Argentina).
Guarda la serie de tiempo completa en la base duckdb del sistema.
"""

import os
import json
import logging
from datetime import datetime

import sys
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

import duckdb
import pandas as pd
import requests

from src.data.scraping_screenermatic import _DB_PATH_DEFAULT

logger = logging.getLogger(__name__)

URL_AMBITO_RIESGO_PAIS = "https://mercados.ambito.com/riesgopais/historico-general/1999-01-22/{}"

def inicializar_tabla(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS riesgo_pais_historico (
            fecha       TIMESTAMP PRIMARY KEY,
            embi_puntos DOUBLE
        )
    """)

def obtener_riesgo_pais_fresco(db_path: str = _DB_PATH_DEFAULT, forzar: bool = False) -> pd.DataFrame:
    """Consigue el histórico de Riesgo País, con caching diario."""
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = duckdb.connect(db_path)
    
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    
    try:
        inicializar_tabla(conn)
        if not forzar:
            try:
                max_fecha = conn.execute("SELECT MAX(fecha) FROM riesgo_pais_historico").fetchone()[0]
                if max_fecha is not None:
                    max_fecha_str = max_fecha.strftime("%Y-%m-%d")
                    if max_fecha_str == fecha_hoy:
                        logger.info("EMBI+ Histórico ya actualizado hoy. Cargando desde caché...")
                        return conn.execute("SELECT * FROM riesgo_pais_historico ORDER BY fecha ASC").df()
            except Exception as e:
                logger.warning(f"No se pudo chequear caché EMBI: {e}")
    finally:
        conn.close()
        
    print(f"📥 Solicitando histórico EMBI+ Argentina actualizado...")
    
    url = URL_AMBITO_RIESGO_PAIS.format(fecha_hoy)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    res = requests.get(url, headers=headers)
    res.raise_for_status()
    datos_crudos = res.json()
    
    if len(datos_crudos) < 2:
        # Fallback por si la API falla, retornar lo del cache aunque sea viejo
        conn = duckdb.connect(db_path)
        try:
            return conn.execute("SELECT * FROM riesgo_pais_historico ORDER BY fecha ASC").df()
        finally:
            conn.close()
        
    # Ignorar cabecera ['Fecha', 'Puntos'] y armar el DataFrame
    filas = datos_crudos[1:]
    df = pd.DataFrame(filas, columns=["fecha", "embi_puntos"])
    
    # Parsear tipos de datos
    df['fecha'] = pd.to_datetime(df['fecha'], format='%d-%m-%Y')
    df['embi_puntos'] = df['embi_puntos'].str.replace('.', '', regex=False)
    df['embi_puntos'] = df['embi_puntos'].str.replace(',', '.', regex=False)
    df['embi_puntos'] = pd.to_numeric(df['embi_puntos'])
    
    # Ordenar cronológicamente y eliminar duplicados de la misma fecha
    df = df.sort_values("fecha", ascending=True)
    df = df.drop_duplicates(subset=['fecha'], keep='last').reset_index(drop=True)
    
    # Persistir en DB
    conn = duckdb.connect(db_path)
    try:
        inicializar_tabla(conn)
        conn.execute("DELETE FROM riesgo_pais_historico")
        conn.execute("""
            INSERT INTO riesgo_pais_historico (fecha, embi_puntos)
            SELECT fecha, embi_puntos FROM df
        """)
    finally:
        conn.close()
        
    return df

if __name__ == "__main__":
    df_rp = obtener_riesgo_pais_fresco()
    
    # Mostremos cómo se comportó entre Julio y Diciembre 2025:
    print("\n🔍 Comportamiento Riesgo País - Trade Electoral de 2025:")
    print("="*65)
    trade_electoral = df_rp[
        (df_rp['fecha'] >= '2025-07-01') & 
        (df_rp['fecha'] <= '2025-12-31')
    ]
    
    # Resteamos a resample por fin de mes para el reporte visual
    trade_mensual = trade_electoral.set_index('fecha').resample('M').last()
    
    for fecha, fila in trade_mensual.iterrows():
        puntos = fila['embi_puntos']
        # Lógica visual
        barra = "▓" * int(puntos / 100)
        print(f"  {fecha.strftime('%B %Y')[:10]:<12} | {puntos:>6.0f} pts | {barra}")
        
    print("="*65)
    print("Notar el pico máximo antes de las elecciones legislativas (Sep/Oct) y el desplome histórico posterior.")
