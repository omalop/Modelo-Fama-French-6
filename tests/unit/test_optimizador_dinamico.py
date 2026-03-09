# Tests Unitarios del Optimizador Cuántico (pytest)
import pytest
import os
import sys
import pandas as pd
import numpy as np

# Agragar ruta para imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from src.models.optimizador_dinamico import OptimizadorDinamicoCuantico

class MockDoctaAPI:
    def get_bond_yield(self, ticker: str):
        # Simulamos TIR ON Hard Dollar de YPF/Pampa al 8.5%
        return 0.085

@pytest.fixture
def optimizador():
    mock_api = MockDoctaAPI()
    opt = OptimizadorDinamicoCuantico(mock_api)
    return opt

def test_calculo_yield_gap(optimizador):
    """Prueba que el Yield Gap se calcule correctamente sin errores matemáticos."""
    pe_ratio = 10.0 # Earnings Yield = 10%
    # Yield Gap esperado = 10% - 8.5% = 1.5%
    yield_gap = optimizador.calcular_yield_gap(pe_ratio)
    
    assert isinstance(yield_gap, float)
    assert round(yield_gap, 3) == 0.015

def test_calculo_yield_gap_negativo(optimizador):
    """Debería fallar si le pasamos un PE Ratio negativo o cero."""
    with pytest.raises(ValueError):
         optimizador.calcular_yield_gap(-5)
    with pytest.raises(ValueError):
         optimizador.calcular_yield_gap(0)

def test_probabilidad_crisis(optimizador):
    """Test de estimación de crisis dadas señales predefinidas."""
    # Señales: Curva invertida brutal (2), HY normal (0), VIX medio (1)
    # Pesos: Curva 0.45, HY 0.35, VIX 0.20
    # Probabilidad = (2/2 * 0.45) + (0) + (1/2 * 0.20) = 0.45 + 0.10 = 0.55
    signals = {
        'Curva_10Y2Y': 2,
        'High_Yield': 0,
        'VIX': 1
    }
    prob = optimizador.estimar_probabilidad_crisis(signals)
    assert round(prob, 2) == 0.55

def test_allocation_optimo_normal(optimizador):
    """En un escenario de mercado barato y sin crisis, el peso RV debe ser alto."""
    # PE = 8 -> Earnings Yield = 12.5% -> Spread contra 8.5% = +4% (Mercado muy atractivo)
    signals = {'Curva_10Y2Y': 0, 'High_Yield': 0, 'VIX': 0} # Paz
    
    alloc = optimizador.calcular_allocation_optimo(pe_ratio_mercado=8.0, crisis_signals=signals)
    assert alloc['Renta_Variable'] == 1.0
    assert alloc['Renta_Fija'] == 0.0

def test_allocation_optimo_crisis(optimizador):
    """En un escenario de crisis, el capital debe huir a la Renta Fija Resguardo."""
    # PE = 20 -> Earnings Yield = 5% -> Spread contra 8.5% = -3.5% (Mercado RV Caro frente a los bonos)
    # Y full señales de Pánico:
    signals = {'Curva_10Y2Y': 2, 'High_Yield': 2, 'VIX': 2}
    
    alloc = optimizador.calcular_allocation_optimo(pe_ratio_mercado=20.0, crisis_signals=signals)
    
    # Penalización 1 (Spread neg): -0.50
    # Penalización 2 (Crisis=1^1.5): -1.0
    # Expected RV = 1 - 1.5 = < 0 -> Clipeado a 0.
    
    assert alloc['Renta_Variable'] == 0.0
    assert alloc['Renta_Fija'] == 1.0

def test_supuestos_estadisticos(optimizador):
    """Valida la regla del Artículo 4 de test de normalidad."""
    # Serie Normal -> Debería retornar True
    serie_normal = pd.Series(np.random.normal(0, 1, 100))
    # Serie No normal -> Debería retornar False
    serie_exponencial = pd.Series(np.random.exponential(1, 100))
    # Serie chica (< 30) -> Return false
    serie_chica = pd.Series(np.random.normal(0, 1, 10))

    assert optimizador._test_supuestos_estadisticos(serie_normal, "Test") == True
    assert optimizador._test_supuestos_estadisticos(serie_exponencial, "Test") == False
    assert optimizador._test_supuestos_estadisticos(serie_chica, "Test") == False
