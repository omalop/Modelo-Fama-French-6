
import yfinance as yf
import pandas as pd
import numpy as np
import logging
import scipy.optimize as sco
from datetime import datetime, timedelta

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/optimizador_cartera.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Importar lógica técnica refactorizada
try:
    from script_deteccion_momentum_domenec import get_data_for_timeframe, apply_indicators
except ImportError:
    # Fallback si el nombre del archivo tiene espacios y no fue renombrado
    import importlib.util
    spec = importlib.util.spec_from_file_location("domenec", "script deteccion momentum domenec.py")
    domenec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(domenec)
    get_data_for_timeframe = domenec.get_data_for_timeframe
    apply_indicators = domenec.apply_indicators

class GestorBlackLitterman:
    """
    Implementación del Modelo Black-Litterman para Swing Trading Quantamental.
    
    Referencia: He & Litterman (1999). "The Intuition Behind Black-Litterman Model Portfolios".
    
    Adaptación:
    - Prior: Mercado (Equilibrio CAPM).
    - Vistas (P, Q): Derivadas del Score Fundamental (Fama-French).
    - Incertidumbre (Omega): Derivada de la Señal Técnica (Túnel Domènec).
    """
    
    def __init__(self, tickers, benchmark='SPY', risk_aversion=3.0):
        self.tickers = tickers
        self.benchmark = benchmark
        self.delta = risk_aversion
        self.data = pd.DataFrame()
        self.market_caps = {}
        
    def fetch_market_data(self, period='2y'):
        """Descarga precios y market caps."""
        logger.info(f"Descargando datos de mercado para {len(self.tickers)} activos...")
        
        # Descargar tickers + benchmark
        all_syms = self.tickers + [self.benchmark]
        df = yf.download(all_syms, period=period, auto_adjust=True, progress=False)['Close']
        
        self.data = df[self.tickers]
        self.benchmark_data = df[self.benchmark]
        
        # Obtener Market Caps (para equilibrio)
        for t in self.tickers:
            try:
                self.market_caps[t] = yf.Ticker(t).info.get('marketCap', 1e9) # Default 1B si falla
            except:
                self.market_caps[t] = 1e9
                
    def get_technical_signals(self):
        """Obtiene señales del Túnel Domènec para ajustar la incertidumbre (Omega)."""
        logger.info("Calculando señales técnicas...")
        # Usamos la función del script existente
        # Simulamos timeframe diario para la señal actual
        data_dict = get_data_for_timeframe(self.tickers, '1d', '1y', ['GGAL.BA', 'GGAL']) # Dummy ccl ref
        
        signals = {}
        for t, df in data_dict.items():
            if df.empty:
                signals[t] = 'Neutral'
                continue
            
            last = df.iloc[-1]
            status = last.get('Status_Control', 'Neutral')
            signals[t] = status
            
        return signals

    def optimize(self, fundamental_scores):
        """
        Ejecuta la optimización BL.
        
        Args:
            fundamental_scores (dict): Dictionary {ticker: z_score}
        """
        returns = self.data.pct_change().dropna()
        cov_matrix = returns.cov() * 252 # Anualizada
        
        # 1. Prior de Mercado (Equilibrio)
        # Pesos por market cap
        total_cap = sum(self.market_caps.values())
        w_mkt = np.array([self.market_caps[t]/total_cap for t in self.tickers])
        
        # Retornos Implícitos de Equilibrio (Pi)
        # Pi = delta * Sigma * w_mkt
        pi = self.delta * cov_matrix.dot(w_mkt)
        
        # 2. Vistas (Views) y Matriz de Incertidumbre (Omega)
        # P: Matriz de identidad (vistas absolutas sobre cada activo)
        # Q: Retorno esperado extra (View Vector)
        # Omega: Diagonal con varianza del error de la vista
        
        P = np.eye(len(self.tickers))
        Q = []
        omega_diag = []
        
        tech_signals = self.get_technical_signals()
        
        for i, t in enumerate(self.tickers):
            z_score = fundamental_scores.get(t, 0)
            signal = tech_signals.get(t, 'Neutral')
            
            # Construir Vista (Q) basada en Fundamental
            # Z=2 -> +5% sobre mercado (heurística ajustable)
            view_return = pi[i] + (z_score * 0.05) 
            Q.append(view_return)
            
            # Construir Incertidumbre (Omega) basada en Técnico
            # Impulso Fuerte (Verde) -> Alta Confianza -> Baja varianza (tau * P * Sigma * P')
            # Corrección/Rojo -> Baja Confianza -> Alta varianza
            
            base_uncertainty = 0.05 # Varianza base
            
            if 'Verde' in signal or 'Impulso' in signal:
                conf_multiplier = 0.1 # Muy confiable (reduce varianza)
            elif 'Azul' in signal or 'Pullback' in signal:
                conf_multiplier = 1.0 # Normal
            else:
                conf_multiplier = 10.0 # Poco confiable (aumenta varianza -> el modelo ignorará la vista)
                
            omega_diag.append(base_uncertainty * conf_multiplier)
            
        Q = np.array(Q)
        Omega = np.diag(omega_diag)
        
        # 3. Calculo Posterior BL
        # E[R] = [ (tau*Sigma)^-1 + P' Omega^-1 P ]^-1 * [ (tau*Sigma)^-1 * Pi + P' Omega^-1 Q ]
        
        tau = 0.025 # Escalar estándar en literatura BL
        sigma_scaled = cov_matrix * tau
        sigma_inv = np.linalg.inv(sigma_scaled)
        omega_inv = np.linalg.inv(Omega)
        
        # Término A (Precisión Combinada)
        A = sigma_inv + np.dot(np.dot(P.T, omega_inv), P)
        
        # Término B (Retornos Ponderados por Precisión)
        B = np.dot(sigma_inv, pi) + np.dot(np.dot(P.T, omega_inv), Q)
        
        # Retornos Posteriores
        posterior_returns = np.dot(np.linalg.inv(A), B)
        
        # 4. Optimización de Pesos (Media-Varianza con Retornos BL)
        # Max Sharpe: w = (Sigma^-1 * E[R]) / sum(...)
        
        weights_unconstrained = np.dot(np.linalg.inv(cov_matrix), posterior_returns)
        weights_norm = weights_unconstrained / weights_unconstrained.sum()
        
        # Limpieza: No cortos (Long Only), min 0%
        def objective(w):
            port_ret = np.dot(w, posterior_returns)
            port_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
            return -(port_ret / port_vol) # Max Sharpe
            
        constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - 1})
        bounds = tuple((0.0, 0.30) for _ in range(len(self.tickers))) # Max 30% por activo
        
        res = sco.minimize(objective, len(self.tickers)*[1./len(self.tickers)], 
                           method='SLSQP', bounds=bounds, constraints=constraints)
        
        final_weights = pd.Series(res.x, index=self.tickers)
        return final_weights.round(4)

if __name__ == "__main__":
    print("--- OPTIMIZADOR DE CARTERA BLACK-LITTERMAN ---")
    tickers_input = input("Ingrese tickers separados por coma (ej: AAPL,MSFT,GOOGL): ")
    tickers = [t.strip().upper() for t in tickers_input.split(',') if t.strip()]
    
    if len(tickers) < 2:
        print("Se necesitan al menos 2 activos para optimizar.")
    else:
        # Inputs simulados de Score Fundamental (Z-Scores)
        # En producción, esto vendría del archivo Excel seleccionado
        print("\nIngrese Z-Scores Fundamentales estimados (0 es promedio, 2 es excelente):")
        scores = {}
        for t in tickers:
            try:
                s = float(input(f"Score para {t} (default 0): ") or 0)
                scores[t] = s
            except:
                scores[t] = 0
                
        optimizer = GestorBlackLitterman(tickers)
        optimizer.fetch_market_data()
        weights = optimizer.optimize(scores)
        
        print("\n--- PESOS ÓPTIMOS (BL) ---")
        print(weights.sort_values(ascending=False))
        
        # Calcular Métricas Rápidas
        pass
