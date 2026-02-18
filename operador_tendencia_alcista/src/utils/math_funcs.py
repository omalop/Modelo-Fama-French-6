import pandas as pd
import numpy as np

def calculate_rma(series: pd.Series, length: int) -> pd.Series:
    """
    Calcula la Wilder's Moving Average (RMA).
    Equivalente a ta.rma de PineScript.
    """
    return series.ewm(alpha=1/length, min_periods=length, adjust=False).mean()

def calculate_wpr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Calcula Williams %R.
    Formula: -100 * (Highest High - Close) / (Highest High - Lowest Low)
    """
    highest_high = high.rolling(window=period).max()
    lowest_low = low.rolling(window=period).min()
    denom = highest_high - lowest_low
    # Evitar división por cero reemplazando con NaN
    denom = denom.replace(0, np.nan)
    wpr = -100 * ((highest_high - close) / denom)
    return wpr

def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Calcula el Average Directional Index (ADX) usando metodología Wilder.
    """
    # True Range
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Directional Movement
    up = high - high.shift(1)
    down = low.shift(1) - low

    pos_dm = np.where((up > down) & (up > 0), up, 0.0)
    neg_dm = np.where((down > up) & (down > 0), down, 0.0)

    pos_dm = pd.Series(pos_dm, index=high.index)
    neg_dm = pd.Series(neg_dm, index=high.index)

    # Smoothing (RMA)
    tr_smooth = calculate_rma(tr, period)
    pos_dm_smooth = calculate_rma(pos_dm, period)
    neg_dm_smooth = calculate_rma(neg_dm, period)

    # DI+ and DI-
    # Evitar división por cero
    tr_smooth = tr_smooth.replace(0, np.nan)
    pos_di = 100 * (pos_dm_smooth / tr_smooth)
    neg_di = 100 * (neg_dm_smooth / tr_smooth)
    
    # DX and ADX
    # Manejo de NaNs en suma para evitar resultados erróneos
    sum_di = pos_di + neg_di
    sum_di = sum_di.replace(0, np.nan)

    dx = 100 * (abs(pos_di - neg_di) / sum_di)
    adx = calculate_rma(dx, period)
    
    return adx
