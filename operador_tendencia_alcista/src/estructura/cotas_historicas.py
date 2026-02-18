import pandas as pd
import numpy as np
import logging
from typing import List, Dict, Optional
from pydantic import BaseModel
from scipy.signal import argrelextrema

logger = logging.getLogger(__name__)

class Cota(BaseModel):
    precio: float
    jerarquia: str  # 'Trimestral' (Azul), 'Semanal' (Naranja), 'Diaria' (Verde)
    validaciones: int
    color: str # 'Azul', 'Naranja', 'Verde'
    rango_tolerancia: float = 0.01 # 1% por defecto

class DetectorCotas:
    """
    Detecta niveles de soporte y resistencia (Cotas Históricas) 
    analizando fractales en múltiples temporalidades.
    """
    
    def __init__(self):
        # Parametros de tolerancia por timeframe
        self.tol = {
            'trimestral': 0.02, # 2%
            'mensual': 0.015,
            'semanal': 0.01,
            'diario': 0.005
        }
        # Minimo de toques para validar
        self.min_toques = {
            'trimestral': 2,
            'mensual': 3,
            'semanal': 3,
            'diario': 5 # Requerimos mas ruido en diario para ser relevante
        }

    def _encontrar_pivotes(self, df: pd.DataFrame, order: int = 5) -> pd.Series:
        """
        Encuentra highs y lows locales.
        """
        if df.empty: return pd.Series()
        
        highs = df.iloc[argrelextrema(df['High'].values, np.greater_equal, order=order)[0]]['High']
        lows = df.iloc[argrelextrema(df['Low'].values, np.less_equal, order=order)[0]]['Low']
        
        return pd.concat([highs, lows])

    def _clusterizar_niveles(self, niveles: pd.Series, tolerancia: float) -> List[Dict]:
        """
        Agrupa niveles cercanos y calcula su precio promedio y fuerza.
        """
        if niveles.empty: return []
        
        niveles = niveles.sort_values()
        clusters = []
        
        current_cluster = [niveles.iloc[0]]
        
        for nivel in niveles.iloc[1:]:
            promedio_actual = np.mean(current_cluster)
            if abs(nivel - promedio_actual) / promedio_actual <= tolerancia:
                current_cluster.append(nivel)
            else:
                # Guardar cluster previo
                clusters.append({
                    'precio': np.mean(current_cluster),
                    'validaciones': len(current_cluster)
                })
                current_cluster = [nivel]
        
        # Ultimo cluster
        if current_cluster:
            clusters.append({
                'precio': np.mean(current_cluster),
                'validaciones': len(current_cluster)
            })
            
        return clusters

    def detectar(self, datos_multitemporal: Dict[str, pd.DataFrame]) -> List[Cota]:
        """
        Analiza dict de DataFrames (keys: 'trimestral', 'mensual', etc)
        y devuelve lista de Cotas jerarquizadas.
        """
        cotas_finales = []
        
        # 1. Analisis Trimestral (Nivel 1 - Azul)
        df_3m = datos_multitemporal.get('trimestral')
        if df_3m is not None and not df_3m.empty:
            pivotes = self._encontrar_pivotes(df_3m, order=2) # Order bajo pq hay pocas velas
            clusters = self._clusterizar_niveles(pivotes, self.tol['trimestral'])
            
            for c in clusters:
                if c['validaciones'] >= self.min_toques['trimestral']:
                    cotas_finales.append(Cota(
                        precio=c['precio'],
                        jerarquia='Trimestral',
                        validaciones=c['validaciones'],
                        color='Azul',
                        rango_tolerancia=self.tol['trimestral']
                    ))
                    
        # 2. Analisis Semanal (Nivel 2 - Naranja)
        # Filtramos niveles que ya esten cubiertos por trimestrales
        df_1w = datos_multitemporal.get('semanal')
        if df_1w is not None and not df_1w.empty:
            pivotes = self._encontrar_pivotes(df_1w, order=5)
            clusters = self._clusterizar_niveles(pivotes, self.tol['semanal'])
            
            for c in clusters:
                # Verificar si ya existe una cota cercana más fuerte (Trimestral)
                es_nueva = True
                for cota_existente in cotas_finales:
                    diff_pct = abs(c['precio'] - cota_existente.precio) / cota_existente.precio
                    if diff_pct < self.tol['trimestral']: # Si esta cerca de una trimestral
                        # Reforzar trimestral (opcional) o ignorar semanal
                        cota_existente.validaciones += 1 # Sumar validacion
                        es_nueva = False
                        break
                
                if es_nueva and c['validaciones'] >= self.min_toques['semanal']:
                    cotas_finales.append(Cota(
                        precio=c['precio'],
                        jerarquia='Semanal',
                        validaciones=c['validaciones'],
                        color='Naranja',
                        rango_tolerancia=self.tol['semanal']
                    ))

        # 3. Analisis Diario (Nivel 3 - Verde)
        df_1d = datos_multitemporal.get('diario')
        if df_1d is not None and not df_1d.empty:
            pivotes = self._encontrar_pivotes(df_1d, order=10) # Order alto, solo swing points importantes
            clusters = self._clusterizar_niveles(pivotes, self.tol['diario'])
            
            for c in clusters:
                es_nueva = True
                for cota_existente in cotas_finales:
                    diff_pct = abs(c['precio'] - cota_existente.precio) / cota_existente.precio
                    # Usamos tolerancia de la cota mayor
                    tol_check = cota_existente.rango_tolerancia
                    if diff_pct < tol_check:
                        cota_existente.validaciones += 1
                        es_nueva = False
                        break
                
                if es_nueva and c['validaciones'] >= self.min_toques['diario']:
                    cotas_finales.append(Cota(
                        precio=c['precio'],
                        jerarquia='Diaria',
                        validaciones=c['validaciones'],
                        color='Verde',
                        rango_tolerancia=self.tol['diario']
                    ))
                    
        # Ordenar por precio descendente
        cotas_finales.sort(key=lambda x: x.precio, reverse=True)
        return cotas_finales
