import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Optional, Tuple
from pydantic import BaseModel
from scipy.signal import argrelextrema

logger = logging.getLogger(__name__)

class Cota(BaseModel):
    precio: float
    jerarquia: str  # 'Trimestral', 'Mensual', 'Semanal', 'Diaria'
    validaciones: int
    color: str # 'Azul', 'Cian', 'Naranja', 'Rojo'
    rango_tolerancia: float = 0.01 

class DetectorCotas:
    """
    Detecta niveles clave de soporte y resistencia (cotas) usando un enfoque secuencial:
    1. Trimestral: Define ZONAS MACRO (Azul).
    2. Semanal: Ajusta precisión de Trimestrales (<1%) o agrega NUEVAS (Naranja).
    3. Diario: Ajusta precisión de CUALQUIER cota (<0.5%). NO agrega nuevas.
    """
    
    def __init__(self):
        # Parametros de tolerancia para DETECCION DE BASES
        self.tol = {
            'trimestral': 0.10, # Aumentado de 5% a 10% para agrupar mejor zonas macro
            'semanal': 0.05,    # Aumentado de 2% a 5%
            'diario': 0.02      # Aumentado de 1.5% a 2%
        }
        # Tolerancia para INTEGRACION (Snap to level)
        self.tol_snap = {
            'semanal_vs_trim': 0.05, # Aumentado: si está a <5% de Trimestral, se fusiona
            'diario_vs_any': 0.01    # 1% para ajuste fino diario
        }
        # Minimo de toques para validar base
        self.min_toques = {
            'trimestral': 1, 
            'semanal': 3, # Aumentado de 2 a 3: Solo niveles muy confirmados
            'diario': 3 
        }

    def detectar(self, datos_multitemporal: Dict[str, pd.DataFrame]) -> List[Cota]:
        """
        Ejecuta la detección secuencial LINEAL.
        """
        cotas_finales = []

        # ----------------------------------------------------
        # 1. FASE TRIMESTRAL (BASE MACRO)
        # ----------------------------------------------------
        df_3m = datos_multitemporal.get('trimestral')
        if df_3m is not None and not df_3m.empty:
            # A. Detectar Clusters y Pivotes
            cotas_trim = self._detectar_bases(
                df_3m, 
                jerarquia='Trimestral', 
                color='Azul', 
                tol_cluster=self.tol['trimestral'], 
                min_validaciones=self.min_toques['trimestral'],
                order=2
            )
            # B. Forzar Maximos y Minimos Historicos Absolutos
            self._asegurar_extremos(cotas_trim, df_3m, 'Trimestral', 'Azul')
            
            cotas_finales = cotas_trim
            logger.info(f"Fase 1 (Trimestral): {len(cotas_finales)} cotas base.")

        # ----------------------------------------------------
        # 2. FASE SEMANAL (AJUSTE + NUEVAS)
        # ----------------------------------------------------
        df_1w = datos_multitemporal.get('semanal')
        if df_1w is not None and not df_1w.empty:
            cotas_sem = self._detectar_bases(
                df_1w, 
                jerarquia='Semanal', 
                color='Naranja', 
                tol_cluster=self.tol['semanal'], 
                min_validaciones=self.min_toques['semanal'],
                order=6
            )
            
            # Integrar: Ajustar Trimestrales si coinciden, sino Agregar Semanales
            cotas_finales = self._integrar_niveles(
                bases=cotas_finales, 
                nuevas=cotas_sem, 
                tolerancia=self.tol_snap['semanal_vs_trim'], 
                modo='agregar' # Agregar si no coincide
            )
            logger.info(f"Fase 2 (Semanal): {len(cotas_finales)} cotas tras integración.")

        # ----------------------------------------------------
        # 3. FASE DIARIA (SOLO AJUSTE FINO)
        # ----------------------------------------------------
        df_1d = datos_multitemporal.get('diario')
        if df_1d is not None and not df_1d.empty:
            cotas_dia = self._detectar_bases(
                df_1d, 
                jerarquia='Diaria', 
                color='Rojo', # (No se usará color rojo pq solo ajustamos)
                tol_cluster=self.tol['diario'], 
                min_validaciones=self.min_toques['diario'],
                order=5
            )
            
            # Integrar: Solo ajustar precios existentes. Descartar nuevas.
            cotas_finales = self._integrar_niveles(
                bases=cotas_finales, 
                nuevas=cotas_dia, 
                tolerancia=self.tol_snap['diario_vs_any'], 
                modo='solo_ajuste' # DESCARTAR nuevas
            )
            logger.info(f"Fase 3 (Diario): {len(cotas_finales)} cotas tras ajuste fino.")

            # ----------------------------------------------------
            # 4. FILTRADO DE VISIBILIDAD (No Relevancia)
            # ----------------------------------------------------
            # Mostrar todo lo que entre en el rango vertical del grafico historico
            cotas_visibles = []
            min_v = df_1d['Low'].min() * 0.8
            max_v = df_1d['High'].max() * 1.2
            
            for c in cotas_finales:
                if min_v <= c.precio <= max_v:
                    cotas_visibles.append(c)
            
            return cotas_visibles

        return cotas_finales

    def _detectar_bases(self, df: pd.DataFrame, jerarquia: str, color: str, 
                       tol_cluster: float, min_validaciones: int, order: int) -> List[Cota]:
        """
        Detecta niveles base en un timeframe especifico (independiente).
        """
        cotas = []
        if df.empty: return cotas
        
        # Encontrar pivotes
        pivotes = self._encontrar_pivotes(df, order=order)
        # Agrupar
        clusters = self._clusterizar_niveles(pivotes, tol_cluster)
        
        for c in clusters:
            if c['validaciones'] >= min_validaciones:
                cotas.append(Cota(
                    precio=c['precio'],
                    jerarquia=jerarquia,
                    validaciones=c['validaciones'],
                    color=color
                ))
        return cotas

    def _integrar_niveles(self, bases: List[Cota], nuevas: List[Cota], tolerancia: float, modo: str) -> List[Cota]:
        """
        Fusiona listas de cotas.
        Si 'nueva' está cerca de 'base' (< tolerancia):
           -> Actualiza 'base.precio' con 'nueva.precio' (mayor precisión).
           -> Mantiene atributos de 'base' (jerarquia, color).
        Si no coincide:
           -> Si modo='agregar': Agrega 'nueva' a la lista.
           -> Si modo='solo_ajuste': Descarta 'nueva'.
        """
        # Clonar lista de bases para trabajar
        lista_trabajo = [
            Cota(
                precio=b.precio, 
                jerarquia=b.jerarquia, 
                validaciones=b.validaciones, 
                color=b.color, 
                rango_tolerancia=b.rango_tolerancia
            ) 
            for b in bases
        ]
        
        for nueva in nuevas:
            mejor_match_idx = -1
            menor_dist = float('inf')
            
            # Buscar match mas cercano
            for i, base in enumerate(lista_trabajo):
                dist_pct = abs(base.precio - nueva.precio) / base.precio
                if dist_pct < tolerancia and dist_pct < menor_dist:
                    menor_dist = dist_pct
                    mejor_match_idx = i
            
            if mejor_match_idx != -1:
                # HIT: Ajustar precio (Snap)
                lista_trabajo[mejor_match_idx].precio = nueva.precio
                lista_trabajo[mejor_match_idx].validaciones += 1
            else:
                # MISS
                if modo == 'agregar':
                    lista_trabajo.append(nueva)
                    
        return lista_trabajo

    def _asegurar_extremos(self, cotas: List[Cota], df: pd.DataFrame, jerarquia: str, color: str):
        """Asegura que Max y Min absolutos estén en la lista."""
        if df.empty: return
        max_val = df['High'].max()
        min_val = df['Low'].min()
        
        # Chequear si ya existen (aprox 1%)
        tiene_max = any(abs(c.precio - max_val)/max_val < 0.01 for c in cotas)
        tiene_min = any(abs(c.precio - min_val)/min_val < 0.01 for c in cotas)
        
        if not tiene_max:
            cotas.append(Cota(precio=max_val, jerarquia=jerarquia, validaciones=1, color=color))
        if not tiene_min:
            cotas.append(Cota(precio=min_val, jerarquia=jerarquia, validaciones=1, color=color))

    def _encontrar_pivotes(self, df: pd.DataFrame, order: int = 5) -> pd.Series:
        if df.empty: return pd.Series()
        high_idx = argrelextrema(df['High'].values, np.greater_equal, order=order)[0]
        low_idx = argrelextrema(df['Low'].values, np.less_equal, order=order)[0]
        return pd.concat([df.iloc[high_idx]['High'], df.iloc[low_idx]['Low']])

    def _clusterizar_niveles(self, niveles: pd.Series, tolerancia: float) -> List[Dict]:
        if niveles.empty: return []
        niveles = niveles.sort_values()
        clusters = []
        current = [niveles.iloc[0]]
        for nivel in niveles.iloc[1:]:
            promedio = np.mean(current)
            if abs(nivel - promedio) / promedio <= tolerancia:
                current.append(nivel)
            else:
                clusters.append({'precio': np.mean(current), 'validaciones': len(current)})
                current = [nivel]
        if current:
            clusters.append({'precio': np.mean(current), 'validaciones': len(current)})
        return clusters
