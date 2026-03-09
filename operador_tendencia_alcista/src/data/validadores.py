from pydantic import BaseModel, validator, Field
import pandas as pd
import numpy as np
from typing import List, Optional

class DatosOHLCV(BaseModel):
    """
    Esquema de validación para una fila de datos OHLCV.
    Artículo 3: Validación estricta de datos antes de procesamiento.
    """
    date: pd.Timestamp
    open: float = Field(gt=0, description="Precio de apertura debe ser positivo")
    high: float = Field(gt=0, description="Precio máximo debe ser positivo")
    low: float = Field(gt=0, description="Precio mínimo debe ser positivo")
    close: float = Field(gt=0, description="Precio de cierre debe ser positivo")
    volume: float = Field(ge=0, description="Volumen no puede ser negativo")

    @validator('high')
    def validar_high_mayor_low(cls, v, values):
        if 'low' in values and v < values['low']:
            raise ValueError(f"Inconsistencia: High ({v}) < Low ({values['low']})")
        return v

    @validator('high')
    def validar_high_mayor_open_close(cls, v, values):
        # High debe ser mayor o igual que Open y Close
        if 'open' in values and v < values['open']:
             raise ValueError(f"Inconsistencia: High ({v}) < Open ({values['open']})")
        if 'close' in values and v < values['close']:
             raise ValueError(f"Inconsistencia: High ({v}) < Close ({values['close']})")
        return v
    
    @validator('low')
    def validar_low_menor_open_close(cls, v, values):
        # Low debe ser menor o igual que Open y Close (Validación cruzada ya se hace implícitamente con High, pero reforzamos)
        if 'open' in values and v > values['open']:
             raise ValueError(f"Inconsistencia: Low ({v}) > Open ({values['open']})")
        if 'close' in values and v > values['close']:
             raise ValueError(f"Inconsistencia: Low ({v}) > Close ({values['close']})")
        return v

    class Config:
        arbitrary_types_allowed = True

class ValidadorDataFrame:
    @staticmethod
    def validar(df: pd.DataFrame) -> pd.DataFrame:
        """
        Valida un DataFrame completo de OHLCV.
        Retorna el DataFrame limpio o lanza excepción si es crítico.
        """
        # 1. Verificar columnas requeridas
        required_cols = ['Open', 'High', 'Low', 'Close', 'Volume']
        if not all(col in df.columns for col in required_cols):
            missing = [col for col in required_cols if col not in df.columns]
            raise ValueError(f"Faltan columnas requeridas: {missing}")

        # 2. Verificar NaNs/Infinitos
        if df[required_cols].isnull().any().any():
            # Si hay pocos, dropear. Si son muchos, error.
            na_count = df[required_cols].isnull().sum().sum()
            total_cells = df[required_cols].size
            if na_count / total_cells > 0.1: # >10% datos corruptos
                raise ValueError("Exceso de valores nulos en datos OHLCV")
            df = df.dropna(subset=required_cols)

        # 3. Validar consistencia lógica vectorial
        inconsistencies = df[df['High'] < df['Low']]
        if not inconsistencies.empty:
            raise ValueError(f"Inconsistencia High < Low detectada en {len(inconsistencies)} filas")
            
        return df
