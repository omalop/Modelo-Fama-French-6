import pandas as pd
import logging
from typing import Optional, Tuple
from ..indicadores.tunel_domenec import IndicadoresDomenec
from ..estructura.cotas_historicas import Cota

logger = logging.getLogger(__name__)

class GestorPosicion:
    """
    Gestiona reglas de salida y tamaño de posición.
    Regla de Oro: Salida por invalidez de flujo (2 velas progresivas).
    """

    @staticmethod
    def verificar_salida_invalidez(df: pd.DataFrame) -> bool:
        """
        Verifica regla de salida estricta:
        1. Vela[n-1] cierra bajo Zona Corrección.
        2. Vela[n] cierra bajo Zona Corrección.
        3. Low[n] < Low[n-1] (Progresividad).
        """
        if len(df) < 3: return False
        
        # Necesitamos calcular indicadores si no estan
        if 'Zona_Correccion_Alcista' not in df.columns:
            IndicadoresDomenec.aplicar(df)
            
        # Ultimas 2 velas cerradas
        v_last = df.iloc[-1]
        v_prev = df.iloc[-2]
        
        # Condicion 1: Cierres bajo zona (implica Zona_Correccion_Alcista = False o Close < Banda Inferior)
        # La logica exacta depende de si usamos la booleana 'Zona_Correccion_Alcista' 
        # o el cruce de precio vs bandas.
        # Asumiremos la booleana como proxy de estado de zona.
        
        zona_perdida = (not v_last['Zona_Correccion_Alcista']) and (not v_prev['Zona_Correccion_Alcista'])
        
        if not zona_perdida:
            return False
            
        # Condicion 2: Progresividad (Minimos decrecientes)
        low_decreciente = v_last['Low'] < v_prev['Low']
        
        if zona_perdida and low_decreciente:
            logger.warning(f"SALIDA TRIGGER: Invalidez de flujo confirmada en {v_last.name}")
            return True
            
        return False

    @staticmethod
    def calcular_stop_loss_inicial(df: pd.DataFrame, margen_pct: float = 0.005) -> float:
        """
        Stop Loss estructural debajo del último Swing Low relevante.
        """
        # Buscar ultimo minimo local en las ultimas 5-10 velas
        lookback = 10
        if len(df) < lookback: lookback = len(df)
        
        # Minimo del periodo reciente
        ultimo_low = df['Low'].tail(lookback).min()
        
        # Aplicar margen
        stop_price = ultimo_low * (1 - margen_pct)
        return stop_price

    @staticmethod
    def calcular_take_profit(precio_entrada: float, cotas_superiores: list[Cota]) -> Optional[float]:
        """
        TP en la siguiente Cota Histórica relevante.
        """
        for cota in cotas_superiores:
            if cota.precio > precio_entrada * 1.02: # Minimo 2% distancia
                return cota.precio
        return None
