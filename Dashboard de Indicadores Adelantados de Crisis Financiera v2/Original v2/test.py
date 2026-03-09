#!/usr/bin/env python3
"""Test simple de MA-34"""

import pandas as pd
from fredapi import Fred
import os

# API key
try:
    with open('FRED_API_KEY.env', 'r') as f:
        for line in f:
            if line.startswith('FRED_API_KEY'):
                api_key = line.split('=')[1].strip()
                break
except:
    api_key = os.getenv('FRED_API_KEY')

fred = Fred(api_key=api_key)

print("Descargando datos...")
series = fred.get_series('BAMLH0A0HYM2')

# Último valor
current = series.iloc[-1]
last_date = series.index[-1]

# MA-34 días
ma_34d = series.rolling(window=34).mean()
current_ma = ma_34d.iloc[-1]

# Dispersión
dispersion = ((current - current_ma) / current_ma) * 100

print(f"\n📊 RESULTADOS:")
print(f"   Fecha: {last_date.strftime('%Y-%m-%d')}")
print(f"   Spread actual: {current:.4f}%")
print(f"   MA-34 días: {current_ma:.4f}%")
print(f"   Dispersión: {dispersion:.2f}%")

print(f"\nVerificación manual:")
print(f"   ({current:.4f} - {current_ma:.4f}) / {current_ma:.4f} × 100 = {dispersion:.2f}%")

# Verificar con mean de últimos 34
manual_ma = series.tail(34).mean()
print(f"\n   MA manual (mean de últimos 34): {manual_ma:.4f}%")
print(f"   Diferencia con rolling: {abs(current_ma - manual_ma):.6f}%")