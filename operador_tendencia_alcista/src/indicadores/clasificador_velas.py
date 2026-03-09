import pandas as pd
import numpy as np
import logging
import sys
import os

# Ajuste temporal de path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.utils.math_funcs import calculate_wpr, calculate_adx

logger = logging.getLogger(__name__)

class ClasificadorVelas:
    """
    Clasificador de velas basado en Momentum 'Control Total' (WPR + ADX).
    Identifica la fuerza y dirección del movimiento actual.
    
    Lógica exacta de 'script deteccion momentum domenec.py'.
    """
    
    # Constantes de Estado (Score)
    ESTADO_IMPULSO_FUERTE = 5     # Verde Oscuro
    ESTADO_IMPULSO_MEDIO = 4      # Verde
    ESTADO_PULLBACK = 3           # Azul (Retroceso leve en tendencia alcista)
    ESTADO_SIN_FUERZA = 2         # Amarillo (Pausa/Indecisión)
    ESTADO_CORRECCION_FUERTE = 1  # Rojo (Corrección profunda con fuerza)
    ESTADO_BAJISTA = 0            # Zona Bajista / Neutral

    @staticmethod
    def clasificar(df: pd.DataFrame, p_dir: int = 40, p_force: int = 7) -> pd.DataFrame:
        """
        Aplica clasificación de velas vectorial.
        Agrega columna 'Status_Control' (string descriptivo) y 'Score_Control' (int).
        """
        if df.empty: return df

        try:
            # Crear copia para evitar SettingWithCopyWarning
            df = df.copy()
            
            # Calcular indicadores base
            df.loc[:, 'WPR'] = calculate_wpr(df['High'], df['Low'], df['Close'], p_dir)
            df.loc[:, 'ADX'] = calculate_adx(df['High'], df['Low'], df['Close'], p_force)

            # Valores previos (Shiftear 1 para comparar con vela anterior)
            df.loc[:, 'WPR_Prev'] = df['WPR'].shift(1)
            df.loc[:, 'ADX_Prev'] = df['ADX'].shift(1)

            # Condición tendencias locales (Logic from script)
            # df['WPR_Up'] = df['WPR'] > df['WPR_Prev']
            wpr_up = df['WPR'] > df['WPR_Prev']
            wpr_down = df['WPR'] < df['WPR_Prev']
            
            # df['Sig_Up'] = df['ADX'] >= df['ADX_Prev']
            sig_up = df['ADX'] >= df['ADX_Prev']
            sig_down = df['ADX'] < df['ADX_Prev']
            
            wpr_gt_minus_50 = df['WPR'] > -50
            upper_band = -25

            # Condiciones (Prioridad descendente en np.select)
            conditions = [
                # 1. Corrección Fuerte (Rojo)
                (wpr_gt_minus_50 & wpr_down & sig_up),
                # 2. Sin Fuerza (Amarillo)
                (wpr_gt_minus_50 & wpr_down & sig_down),
                # 3. Pullback (Azul)
                (wpr_gt_minus_50 & wpr_up & sig_down),
                # 4. Impulso Fuerte (Verde Oscuro)
                (wpr_gt_minus_50 & wpr_up & sig_up & (df['WPR'] > upper_band)),
                # 5. Impulso Medio (Verde)
                (wpr_gt_minus_50 & wpr_up & sig_up & (df['WPR'] <= upper_band))
            ]

            choices_score = [
                ClasificadorVelas.ESTADO_CORRECCION_FUERTE, # 1
                ClasificadorVelas.ESTADO_SIN_FUERZA,        # 2
                ClasificadorVelas.ESTADO_PULLBACK,          # 3
                ClasificadorVelas.ESTADO_IMPULSO_FUERTE,    # 5
                ClasificadorVelas.ESTADO_IMPULSO_MEDIO      # 4
            ]
            
            choices_label = [
                'Correccion Fuerte (Rojo)',
                'Sin Fuerza (Amarillo)',
                'Pullback (Azul)',
                'Impulso Fuerte (Verde Osc)',
                'Impulso Medio (Verde)'
            ]

            # Asignar 'Score_Control' numérico
            # Default es 0 (Bajista) si no cumple condiciones de WPR > -50
            df.loc[:, 'Score_Control'] = np.select(conditions, choices_score, default=ClasificadorVelas.ESTADO_BAJISTA)
            
            # Asignar 'Status_Control' label
            df.loc[:, 'Status_Control'] = np.select(conditions, choices_label, default='Zona Bajista / Neutral')

            return df

        except Exception as e:
            logger.error(f"Error en clasificador de velas: {e}")
            return df
