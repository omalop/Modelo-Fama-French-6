# import pytest (Removed dependency)
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

# Path hack
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.backtesting.motor_backtest import MotorBacktest
from src.senales.generador_senales import GeneradorSenales, SenalEntrada
from src.gestion.gestor_posicion import GestorPosicion
from src.indicadores.tunel_domenec import IndicadoresDomenec
from src.indicadores.clasificador_velas import ClasificadorVelas

class MockRepo:
    """Repo simulado con datos deterministas para test."""
    def obtener_todo_multitemporal(self, ticker):
        # Generar DF alcista perfecto
        dates = pd.date_range(start='2024-01-01', periods=100, freq='D')
        df = pd.DataFrame(index=dates)
        df['Close'] = np.linspace(100, 200, 100) # Tendencia alcista
        df['Open'] = df['Close'] - 1
        df['High'] = df['Close'] + 2
        df['Low'] = df['Close'] - 2
        df['Volume'] = 1000
        
        # Simular pullback en ultimos dias
        df.iloc[-5:]['Close'] -= 5 # Retroceso
        
        # Calcular indicadores
        IndicadoresDomenec.aplicar(df)
        ClasificadorVelas.clasificar(df)
        
        return {
            'diario': df,
            'semanal': df.resample('W').last(), # Mock simple
            'mensual': df.resample('M').last(),
            'trimestral': df.resample('Q').last()
        }

def test_flujo_completo_simulado():
    """
    Simula una ejecución de backtest con un escenario controlado.
    """
    motor = MotorBacktest()
    # Inyectar repo mock
    motor.repo = MockRepo()
    
    tickers = ['TEST_TICKER']
    
    # Ejecutar motor (mocked logic inside would act)
    # Nota: MotorBacktest usa GeneradorSenales que usa AnalisisEstructura. 
    # AnalisisEstructura requiere 'Genial_Line'. 
    # El mock repo ya aplica indicadores.
    
    # Sin embargo, el motor corta los datos por fecha (Walk-Forward).
    # Necesitamos asegurar que el slice tenga datos suficientes para indicadores
    # si se recalcularan. Pero Generador espera que ya vengaan o los calcula.
    # En el motor: datos_wf[tf] -> Generador -> Check columns -> Aplicar si falta.
    
    # Ejecutamos
    resultado = motor.ejecutar(tickers, '2024-03-01', '2024-04-01')
    
    assert resultado is not None
    assert 'metricas' in resultado
    # No garantizamos trades porque el mock es muy simple lineal, 
    # tal vez no de fractalidad o pullback exacto.
    # Pero verificamos que no crashee y devuelva estructura.
    print("Test Integración OK: Motor ejecutado sin errores.")

if __name__ == "__main__":
    test_flujo_completo_simulado()
