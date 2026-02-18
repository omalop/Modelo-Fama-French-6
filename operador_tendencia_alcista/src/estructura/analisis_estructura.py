import pandas as pd
import logging
from ..indicadores.tunel_domenec import IndicadoresDomenec

logger = logging.getLogger(__name__)

class AnalizadorEstructura:
    """
    Analiza la estructura del mercado: Tendencia y Fractalidad.
    """
    
    @staticmethod
    def analizar_tendencia(df: pd.DataFrame) -> str:
        """
        Determina la tendencia basada en Túnel Domènec.
        Retorna: 'ALCISTA', 'BAJISTA', 'NEUTRAL'
        """
        if df.empty or 'Genial_Line' not in df.columns:
            return 'NEUTRAL'
        
        precio_actual = df['Close'].iloc[-1]
        genial_line = df['Genial_Line'].iloc[-1]
        
        # Lógica simplificada: Precio vs Genial Line (SMA 34)
        # Se puede refinar con la pendiente de la Genial Line
        slope = df['Genial_Line'].diff(3).iloc[-1] # Pendiente de 3 periodos
        
        if precio_actual > genial_line and slope > 0:
            return 'ALCISTA'
        elif precio_actual < genial_line and slope < 0:
            return 'BAJISTA'
        else:
            return 'LATERAL'

    @staticmethod
    def verificar_alineacion_fractal(df_superior: pd.DataFrame, df_inferior: pd.DataFrame) -> bool:
        """
        Verifica si la tendencia del timeframe superior apoya al inferior.
        Regla: No operar en Diario si Semanal no es Alcista (o al menos no Bajista Fuerte).
        """
        tendencia_sup = AnalizadorEstructura.analizar_tendencia(df_superior)
        
        if tendencia_sup == 'BAJISTA':
            logger.info("Fractalidad: Timeframe superior BAJISTA. Operación descartada.")
            return False
            
        # Si es Alcista o Lateral (en acumulación), permitimos buscar entrada en inferior
        return True

    @staticmethod
    def validar_contexto(datos_multitemporal: dict) -> dict:
        """
        Analiza contexto general.
        Retorna dict con estado de cada timeframe.
        """
        resultado = {}
        for tf, df in datos_multitemporal.items():
            if df is None or df.empty:
                resultado[tf] = 'SIN_DATOS'
                continue
                
            # Asegurar que indicadores estén calculados
            if 'Genial_Line' not in df.columns:
                IndicadoresDomenec.aplicar(df)
                
            resultado[tf] = AnalizadorEstructura.analizar_tendencia(df)
            
        return resultado
