"""
Módulo de Selección Inteligente de Renta Fija (Milei Regime-Based).

Este módulo implementa la lógica de decisión para elegir entre bonos en pesos
(LECAPs, CER) y bonos en dólares (Soberanos HD, ONs Corporativas) basándose en
las políticas monetarias actuales de Javier Milei y el contexto de riesgo político.

Conceptos Clave:
1. Carry Trade: Aprovechar tasas reales en pesos vs crawling peg / breakeven cambiario.
2. Riesgo Kuka: Prima de riesgo político estimada en ~300 bps sobre el EMBI+.
3. Estrategia Milei: Escasez de pesos ("Emisión Cero") y acumulación de reservas.

Referencia:
NotebookLM - Investigación Renta Fija Emergentes.
"""

import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class SelectorRentaFija:
    """
    Selector de activos de Renta Fija con enfoque en el régimen monetario actual.
    """

    def __init__(self, df_bonos: pd.DataFrame):
        """
        Args:
            df_bonos: DataFrame proveniente de ScreenermaticScraper / CacheScreenermatic.
        """
        self.df = df_bonos
        self.riesgo_kuka_premium = 0.03 # 300 bps
        
    def calcular_dolar_mep_implicito(self) -> float:
        """
        Calcula el tipo de cambio MEP implícito usando el par AL30 / AL30D.
        Fórmula: Precio_ARS / Precio_USD
        """
        al30_ars = self.df[self.df['simbolo'] == 'AL30']['precio'].mean()
        al30_usd = self.df[self.df['simbolo'] == 'AL30D']['precio'].mean()
        
        if pd.isna(al30_ars) or pd.isna(al30_usd) or al30_usd == 0:
            # Fallback a AE38
            al30_ars = self.df[self.df['simbolo'] == 'AE38']['precio'].mean()
            al30_usd = self.df[self.df['simbolo'] == 'AE38D']['precio'].mean()
            
        if not pd.isna(al30_ars) and not pd.isna(al30_usd) and al30_usd != 0:
            mep = al30_ars / al30_usd
            logger.info(f"Dólar MEP Implícito Detectado: ${mep:.2f}")
            return mep
        
        logger.warning("No se pudo calcular MEP implícito. Usando fallback $1100")
        return 1100.0

    def categorizar_bonos(self) -> pd.DataFrame:
        """
        Clasifica los bonos por tipo de ajuste y moneda.
        """
        df = self.df.copy()
        
        def asignar_categoria(row):
            simbolo = str(row['simbolo']).upper()
            desc = str(row['descripcion']).upper()
            tipo = str(row['tipo']).upper()
            moneda = str(row['moneda']).upper()
            
            # LECAPs (S% ARS)
            if simbolo.startswith('S') and 'LECAP' in desc and moneda == 'ARS':
                return 'LECAP'
            
            # CER (Ajuste por inflación)
            if 'CER' in desc or simbolo in ['TX26', 'TX28', 'TX24', 'DICP', 'CUAP', 'PARP']:
                return 'CER'
            
            # Soberanos Hard Dollar (AL, GD en USD)
            if tipo == 'SOBERANO' and moneda in ['USD']:
                if simbolo.startswith('AL'): return 'SOBERANO_AL'
                if simbolo.startswith('GD'): return 'SOBERANO_GD'
                return 'SOBERANO_HD_OTRO'
            
            # Corporativos (ONs)
            if tipo == 'CORPORATIVO':
                if moneda == 'USD': return 'ON_HARD_DOLAR'
                return 'ON_PESOS'
                
            return 'OTRO'

        df['categoria_milei'] = df.apply(asignar_categoria, axis=1)
        return df

    def calcular_carry_trade_breakeven(self, spot_usd: float, days: int = 30) -> pd.DataFrame:
        """
        Calcula el Dólar Breakeven para las LECAPs.
        
        Args:
            spot_usd: Precio actual del dólar (MEP o CCL).
            days: Horizonte de tiempo para el carry trade.
            
        Returns:
            DataFrame con los tickers de carry trade y su dólar de equilibrio.
        """
        df = self.categorizar_bonos()
        lecaps = df[df['categoria_milei'] == 'LECAP'].copy()
        
        # Tir_pct es anualizada. Calculamos la tasa efectiva para el periodo 'days'
        # T_efectiva = (1 + TIR)^(days/365) - 1
        lecaps['tasa_efectiva_periodo'] = (1 + lecaps['tir_pct'] / 100) ** (days / 365.0) - 1
        
        # Dollar Breakeven = Spot * (1 + Tasa_Efectiva_ARS) / (1 + Tasa_Efectiva_USD_Esperada)
        # Asumiendo Tasa USD esperada = 0 para simplicidad de breakeven nominal
        lecaps['usd_breakeven'] = spot_usd * (1 + lecaps['tasa_efectiva_periodo'])
        
        return lecaps[['simbolo', 'tir_pct', 'tasa_efectiva_periodo', 'usd_breakeven']]

    def analizar_riesgo_kuka(self, embi_actual: float) -> dict:
        """
        Calcula el impacto del Riesgo Kuka en la valoración soberana.
        
        Si el EMBI - 300 bps sigue siendo alto, la rentabilidad es fundamentalmente económica.
        Si converge a niveles regionales, el upside por política ya se dio.
        """
        embi_limpio = embi_actual - self.riesgo_kuka_premium
        
        analisis = {
            'embi_actual': embi_actual,
            'embi_limpio': embi_limpio,
            'atractivo_soberano': 'ALTO' if embi_limpio > 0.05 else 'MODERADO', # niveles de 500-800 bps limpios son atractivos
            'estrategia': 'Compresión de Spreads (GD35/GD41)' if embi_actual > 0.10 else 'Hold/Cupón'
        }
        return analisis

    def recomendar_ponderacion(self, 
                              inflacion_esperada: float, 
                              crawling_peg: float,
                              riesgo_pais: float) -> dict:
        """
        Define pesos sugeridos para la porción de Renta Fija.
        
        Args:
            inflacion_esperada: TEM (Tasa Efectiva Mensual) de inflación.
            crawling_peg: TEM de devaluación oficial/paralela.
            riesgo_pais: EMBI+ en puntos básicos (ej: 1200).
        """
        # 1. Regímenes de Milei
        # CASO A: Tasa Real Pesos > Inflación > Crawl -> FAVORECE CARRY (LECAP)
        # CASO B: Inflación > Tasa Real Pesos -> FAVORECE CER
        # CASO C: Riesgo País > 1000 y comprimiendo -> FAVORECE SOBERANO HD
        
        recomendacion = {}
        
        # Heurística basada en investigación NotebookLM
        if crawling_peg < 0.02 and riesgo_pais > 800:
            # Contexto de estabilidad cambiaria y alto riesgo país -> Carry + Soberano HD
            recomendacion['LECAP'] = 0.40
            recomendacion['SOBERANO_HD'] = 0.40
            recomendacion['ON_USD'] = 0.20
        elif inflacion_esperada > 0.04:
            # Alta inflación -> Pesos CER
            recomendacion['CER'] = 0.50
            recomendacion['SOBERANO_HD'] = 0.30
            recomendacion['ON_USD'] = 0.20
        else:
            # Equilibrio
            recomendacion['LECAP'] = 0.30
            recomendacion['SOBERANO_HD'] = 0.30
            recomendacion['CER'] = 0.20
            recomendacion['ON_USD'] = 0.20
            
        return recomendacion

    def seleccionar_top_activos(self, categoria: str, limit: int = 3) -> pd.DataFrame:
        """
        Selecciona los mejores activos por TIR/Duration dentro de una categoría.
        """
        df = self.categorizar_bonos()
        filtro = df[df['categoria_milei'].str.contains(categoria.upper())].copy()
        
        if filtro.empty: return pd.DataFrame()
        
        # Top por TIR descendente
        return filtro.sort_values(by='tir_pct', ascending=False).head(limit)
