import pandas as pd
import numpy as np
import logging
import sys
import os

# Ajuste temporal de path para imports si se ejecuta directo
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.utils.math_funcs import calculate_rma

logger = logging.getLogger(__name__)

class IndicadoresDomenec:
    """
    Implementación exacta de los indicadores propietarios 'Túnel Domènec'.
    - Genial Line (SMA 34)
    - Zona de Corrección (EMA 8 vs Wilder 8)
    - Túnel Fibonacci (EMAs 123, 188, 416, 618, 882, 1223)
    """

    @staticmethod
    def aplicar(df: pd.DataFrame) -> pd.DataFrame:
        """
        Aplica los indicadores al DataFrame inplace y retorna referencia.
        """
        if df.empty:
            logger.warning("DataFrame vacío, no se pueden calcular indicadores.")
            return df

        try:
            # Trabajar sobre copia para evitar SettingWithCopyWarning
            df = df.copy()

            # 1. GENIAL LINE (SMA 34)
            df.loc[:, 'Genial_Line'] = df['Close'].rolling(window=34).mean()

            # 2. ZONA DE CORRECCION (EMA 8 vs Wilder 8)
            df.loc[:, 'EMA_8'] = df['Close'].ewm(span=8, adjust=False).mean()
            df.loc[:, 'Wilder_8'] = calculate_rma(df['Close'], 8)
            
            # Condición: True si EMA8 > Wilder8 (Zona Alcista/Verde), False si Roja
            df.loc[:, 'Zona_Correccion_Alcista'] = df['EMA_8'] > df['Wilder_8']

            # 3. TUNEL DOMENEC (EMAs Fibonacci)
            emas = [123, 188, 416, 618, 882, 1223]
            for p in emas:
                var_name = f'EMA_{p}'
                df.loc[:, var_name] = df['Close'].ewm(span=p, adjust=False).mean()

            # 4. DISPERSIÓN (Diferencia % con SMA 34)
            # Útil para detectar agotamiento o necesidad de corrección a la media
            df.loc[:, 'Dispersion_SMA34'] = ((df['Close'] - df['Genial_Line']) / df['Genial_Line']) * 100

            logger.debug("Indicadores Domènec calculados exitosamente.")
            return df

        except Exception as e:
            logger.error(f"Error calculando indicadores Domènec: {e}")
            return df
