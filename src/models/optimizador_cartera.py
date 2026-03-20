
import os
import importlib.util
import yfinance as yf
import pandas as pd
import numpy as np
import logging
import scipy.optimize as sco
from datetime import datetime, timedelta

# Configuración de Logging
os.makedirs('logs', exist_ok=True)  # Crear carpeta si no existe (necesario en CI/CD)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/optimizador_cartera.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Importar Optimizador Dinámico
try:
    from optimizador_dinamico import OptimizadorDinamicoCuantico
except ImportError:
    current_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(current_dir, "optimizador_dinamico.py")
    spec = importlib.util.spec_from_file_location("optimizador_dinamico", script_path)
    opt_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(opt_mod)
    OptimizadorDinamicoCuantico = opt_mod.OptimizadorDinamicoCuantico

# Importar lógica técnica refactorizada
try:
    from script_deteccion_momentum_domenec import get_data_for_timeframe, apply_indicators
except ImportError:
    # Fallback si el nombre del archivo tiene espacios y no fue renombrado
    current_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(current_dir, "script deteccion momentum domenec.py")
    spec = importlib.util.spec_from_file_location("domenec", script_path)
    domenec = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(domenec)
    get_data_for_timeframe = domenec.get_data_for_timeframe
    apply_indicators = domenec.apply_indicators

# Importar Scraping y Selección Inteligente de Renta Fija
from src.data.scraping_screenermatic import obtener_bonos_frescos
from src.models.selector_renta_fija import SelectorRentaFija

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
        
        # --- NUEVO: ASSET ALLOCATION DINÁMICO (CUÁNTICO) ---
        # 1. Instanciar Optimizador Dinámico
        try:
            from docta_api import DoctaCapitalAPI
        except ImportError:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            script_path = os.path.join(current_dir, "../data/docta_api.py")
            spec = importlib.util.spec_from_file_location("docta_api", script_path)
            docta_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(docta_mod)
            DoctaCapitalAPI = docta_mod.DoctaCapitalAPI

        CLIENT_ID = os.getenv("DOCTA_CLIENT_ID", "docta-api-cf68347b-omlop")
        CLIENT_SECRET = os.getenv("DOCTA_CLIENT_SECRET", "_ciyJML_JOgBD89Ft39PL6Az-ps9BJAAapzkQJ-u-LM")
        docta = DoctaCapitalAPI(CLIENT_ID, CLIENT_SECRET)
        
        optimizador_cuantico = OptimizadorDinamicoCuantico(docta)
        
        # 2. Obtener variables locales (Mock PE Ratio para simplificar - En PROD vendria del fundamental_scores)
        # Asumimos que los z_score 0 equivale a un PE normal de 15.
        avg_z_score = np.mean(list(fundamental_scores.values())) if fundamental_scores else 0
        pe_estimado = max(5.0, 15.0 - (avg_z_score * 3)) # Z alto = PE Bajo (barato)
        
        # 3. Integrar Dashboard de crisis (Mock de señales para MVP - En PROD se corre la lógica entera)
        crisis_signals = {
            'Curva_10Y2Y': 0, # Se debería consultar el dashboard
            'High_Yield': 0,
            'VIX': 0
        }
        
        # 4. Modulación de capital Renta Variable vs Liquidez
        allocation = optimizador_cuantico.calcular_allocation_optimo(pe_estimado, crisis_signals)
        max_rv_peso = allocation['Renta_Variable']
        logger.info(f"PESO MÁXIMO AUTORIZADO RV: {max_rv_peso:.2%}")

        # 4. Optimización de Pesos (Media-Varianza con Retornos BL)
        # Max Sharpe: w = (Sigma^-1 * E[R]) / sum(...)
        
        # Limpieza: No cortos (Long Only), min 0%
        def objective(w):
            port_ret = np.dot(w, posterior_returns)
            port_vol = np.sqrt(np.dot(w.T, np.dot(cov_matrix, w)))
            return -(port_ret / port_vol) # Max Sharpe
            
        constraints = ({'type': 'eq', 'fun': lambda x: np.sum(x) - max_rv_peso})
        bounds = tuple((0.0, max_rv_peso) for _ in range(len(self.tickers))) # Max el peso total

        
        res = sco.minimize(objective, len(self.tickers)*[(max_rv_peso/len(self.tickers))], 
                           method='SLSQP', bounds=bounds, constraints=constraints)
        
        final_weights = pd.Series(res.x, index=self.tickers)
        
        # Agregar el peso en efectivo/renta fija hard dollar
        final_weights['RENTA_FIJA_RESERVA'] = allocation['Renta_Fija']
        
        return final_weights.round(4)

if __name__ == "__main__":
    import sys
    # Forzar UTF-8 en consola Windows
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    print("\n" + "="*70)
    print("💎  ESTRATEGIA QUANTAMENTAL FAMA-FRENCH 6  —  MILEI REGIME")
    print("="*70)
    
    tickers_input = input("Ingrese tickers de Renta Variable (ej: GGAL.BA,YPFD.BA,PAM): ")
    tickers = [t.strip().upper() for t in tickers_input.split(',') if t.strip()]
    
    if len(tickers) < 2:
        print("❌ Se necesitan al menos 2 activos para optimizar.")
    else:
        # 1. Ejecutar Optimización combinada (BL + Dinámico)
        scores = {}
        print("\n📊 Score Fundamental (Z-Score): 0 (Neutral), 2 (Excelente)")
        for t in tickers:
            try:
                s = float(input(f"   Score para {t}: ") or 0)
                scores[t] = s
            except: scores[t] = 0
                
        optimizer = GestorBlackLitterman(tickers)
        optimizer.fetch_market_data()
        weights = optimizer.optimize(scores)
        
        # 2. Análisis de Renta Fija Inteligente (Screenermatic)
        print("\n" + "-"*70)
        print("🛡️   ANÁLISIS DE RESGUARDO (RENTA FIJA)")
        print("-"*70)
        
        try:
            df_bonos = obtener_bonos_frescos()
            selector = SelectorRentaFija(df_bonos)
            
            # A. Dólar MEP Implícito
            mep = selector.calcular_dolar_mep_implicito()
            print(f"💵  Dólar MEP Implícito: ${mep:,.2f}")
            
            # B. Análisis Riesgo Kuka (Asumiendo EMBI actual - se podría automatizar via Fred/Docta)
            # EMBI Argentina aprox 12% (1200 bps)
            kuka = selector.analizar_riesgo_kuka(embi_actual=0.12)
            print(f"🗳️  Riesgo País (EMBI): {kuka['embi_actual']:.0%} | Limpio (sin Kuka): {kuka['embi_limpio']:.0%}")
            print(f"🎯  Estrategia Soberana: {kuka['estrategia']}")
            
            # C. Dólar Breakeven de Carry Trade (Horizonte 30 días)
            print("\n📈  CARRY TRADE — Punto de Equilibrio (Breakeven):")
            carry = selector.calcular_carry_trade_breakeven(spot_usd=mep, days=30)
            print(f"   {'Ticker':<8} {'TIR':>6}  {'Breakeven a 30 días':>20}")
            print(f"   {'-'*38}")
            for _, r in carry.head(5).iterrows():
                print(f"   {r['simbolo']:<8} {r['tir_pct']:>6.1f}%  ${r['usd_breakeven']:>19,.2f}")
            print(f"\n   💡 Si el dólar es MENOR a ${carry['usd_breakeven'].mean():.2f} en 30 días, el Carry en Pesos gana.")

            # D. Selección de Activos Top por Bucket
            rf_reserva = weights.get('RENTA_FIJA_RESERVA', 0)
            if rf_reserva > 0:
                print(f"\n🔥  SELECCIÓN SUGERIDA PARA EL {rf_reserva:.1%} DE LA CARTERA:")
                buckets = ['LECAP', 'SOBERANO_GD', 'ON_HARD_DOLAR', 'CER']
                for b in buckets:
                    top = selector.seleccionar_top_activos(b, 1)
                    if not top.empty:
                        item = top.iloc[0]
                        print(f"   ✅ {b:<15}: {item['simbolo']:<8} (TIR: {item['tir_pct']:.1f}% | MD: {item.get('modified_dur', 0):.2f})")

        except Exception as e_rf:
            logger.error(f"Error en análisis de renta fija: {e_rf}")
            print("   ⚠️ No se pudo completar el análisis detallado de renta fija.")

        # 3. Output Final de Pesos
        print("\n" + "="*70)
        print("🎯  PESOS FINALES DE LA CARTERA (MODELO COMBINADO)")
        print("="*70)
        print(weights.sort_values(ascending=False).to_frame('Peso'))
        print("\n✨ Proceso completado exitosamente.")
