import pandas as pd
import logging
from typing import Dict, List, Optional
from pydantic import BaseModel
from ..indicadores.tunel_domenec import IndicadoresDomenec
from ..indicadores.clasificador_velas import ClasificadorVelas
from ..estructura.analisis_estructura import AnalizadorEstructura

logger = logging.getLogger(__name__)

class SenalEntrada(BaseModel):
    ticker: str
    fecha: pd.Timestamp
    precio: float
    tipo: str # 'LONG'
    setup: str # 'REBOTE_GENIAL_LINE', 'REBOTE_ZONA_CORRECCION'
    score_confianza: int # 0-100

    class Config:
        arbitrary_types_allowed = True

class GeneradorSenales:
    """
    Motor de generación de señales de compra.
    Reglas:
    1. Alineación Fractal (Semanal Alcista).
    2. Pullback validado a Zona (Genial Line o Zona Corrección).
    3. Gatillo: Vela de confirmación (Status Control positivo).
    """

    def __init__(self):
        pass

    def analizar_ticker(self, ticker: str, datos: Dict[str, pd.DataFrame]) -> Optional[SenalEntrada]:
        """
        Analiza un ticker con sus datos multitemporales.
        """
        df_diario = datos.get('diario')
        df_semanal = datos.get('semanal')

        if df_diario is None or df_semanal is None or df_diario.empty or df_semanal.empty:
            return None

        # 1. Alineación Fractal
        if not AnalizadorEstructura.verificar_alineacion_fractal(df_semanal, df_diario):
            return None

        # Asegurar indicadores en Diario
        if 'Genial_Line' not in df_diario.columns:
            # Los indicadores ahora devuelven una copia explicita, debemos asignarla
            df_diario = IndicadoresDomenec.aplicar(df_diario)
            df_diario = ClasificadorVelas.clasificar(df_diario)

        # Analizar última vela cerrada (y previa para contexto)
        vela_actual = df_diario.iloc[-1]
        vela_previa = df_diario.iloc[-2]

        # 2. Lógica de Triggers (Simplificada)
        
        # Setup A: Rebote en Genial Line
        # Precio toca o se acerca a Genial Line y rebota
        distancia_gl = (vela_actual['Low'] - vela_actual['Genial_Line']) / vela_actual['Genial_Line']
        en_zona_gl = abs(distancia_gl) < 0.015 # 1.5% tolerancia

        # Setup B: Rebote en Zona Corrección
        # Vela previa dentro/debajo de zona, Vela actual cierra arriba
        cruce_zona_alza = (vela_previa['Close'] < vela_previa['Genial_Line']) and (vela_actual['Close'] > vela_actual['Genial_Line'])
        
        # Validación de Momentum (Control Total)
        # Requerimos que la vela actual tenga fuerza (Impulso o Pullback confirmando)
        status_ok = vela_actual['Score_Control'] >= ClasificadorVelas.ESTADO_PULLBACK # Azul o Verde
        
        setup_detectado = None
        
        if (en_zona_gl or cruce_zona_alza) and status_ok:
            if en_zona_gl:
                setup_detectado = 'REBOTE_GENIAL_LINE'
            else:
                setup_detectado = 'RECUPERACION_TENDENCIA'
                
            return SenalEntrada(
                ticker=ticker,
                fecha=vela_actual.name,
                precio=vela_actual['Close'],
                tipo='LONG',
                setup=setup_detectado,
                score_confianza=80 # PlaceholderScore
            )

        return None
