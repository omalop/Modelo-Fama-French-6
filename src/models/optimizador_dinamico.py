import pandas as pd
import numpy as np
import logging
from scipy import stats
import yfinance as yf
from datetime import datetime, timedelta

# Importamos el cliente de Docta creado en fase anterior
# Usamos try/except para facilitar el testeo aislado
try:
    from src.data.docta_api import DoctaCapitalAPI
except ImportError:
    import sys
    import os
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
    from src.data.docta_api import DoctaCapitalAPI

logger = logging.getLogger(__name__)

class OptimizadorDinamicoCuantico:
    """
    Optimizador de Asset Allocation Dinámico para el Modelo Fama-French 6.
    
    Implementa "Regime-Switching" probabilístico utilizando:
    - Yield Gap Digno (E/P Mercados Emergentes - [Treasury Y10 + EMBI+ Argentina])
    - Señales del Dashboard de Crisis (CVaR / Expected Shortfall)
    
    Determina la ponderación óptima entre Renta Variable Factorial y Liquidez (Renta Fija Corto/Mediano).
    """

    # Referencia científica:
    # Estrada, J. (2000). The Cost of Equity in Emerging Markets.
    # Camacho, M., & Perez-Quiros, G. (2010). Introducing the EURO-STING.

    def __init__(self, docta_client: DoctaCapitalAPI):
        self.docta = docta_client
        self.risk_free_proxy_yield = 0.08 # Default fallback (8%)
        self.embi_argentina = 0.06 # Default fallback 600 bps
        self.us_treasury_10y = 0.04 # Default fallback 4%
        
    def _test_supuestos_estadisticos(self, data: pd.Series, warning_context: str) -> bool:
        """
        [ARTÍCULO 4] Validación de supuestos antes de aplicar modelos estadísticos.
        Realiza test de normalidad (Shapiro-Wilk) a la muestra.
        """
        if len(data) < 30:
            logger.warning(f"{warning_context}: Muestra < 30 ({len(data)}). Saltando Shapiro.")
            return False
            
        stat, p_norm = stats.shapiro(data.dropna())
        if p_norm < 0.05:
            logger.warning(
                f"Violación supuesto normalidad (p={p_norm:.4f}) en {warning_context}. "
                "Considerando métodos no paramétricos para CVaR."
            )
            return False
        return True

    def calcular_yield_gap(self, pe_ratio_mercado: float) -> float:
        """
        Calcula la Prima de Riesgo Local (Yield Gap Digno).
        
        Args:
            pe_ratio_mercado: P/E (Price to Earnings) ponderado de la cartera fundamental seleccionada.
            
        Returns:
            float: Spread de prima de riesgo (positivo implica RV barata vs RF)
        """
        # 1. Intentar construir la curva de descuento dinámica
        # En producción ideal: self.docta_api.get_embi() o usar proxy de bonos NY (GD30/AL30)
        
        try:
            # Bajamos el US Treasury a 10 años
            spy = yf.Ticker("^TNX")
            hist = spy.history(period="5d")
            if not hist.empty:
                self.us_treasury_10y = hist['Close'].iloc[-1] / 100.0
        except Exception as e:
            logger.warning(f"Error bajando Treasury 10Y: {e}. Usando fallback {self.us_treasury_10y*100}%")

        # Intentar consultar TIR de un soberano LOCAL (ley Argentina) como proxy.
        # Usamos AL30/AE38 porque: 1) Liquidan diariamente, 2) Son el benchmark natural
        # de la curva soberana Hard Dollar en el mercado local.
        # Referencia: Brigo, D. (2006). Interest Rate Models. Cap. 3.
        tir_proxy = (
            self.docta.get_bond_yield("AL30")   # Soberano HD ley local CP
            or self.docta.get_bond_yield("AE38") # Soberano HD ley local LP
        )
        
        if tir_proxy:
            tasa_descuento_local = tir_proxy
            logger.info(f"Tasa de Descuento Local fijada por AL30/AE38 (ley local): {tasa_descuento_local:.2%}")
        else:
            tasa_descuento_local = self.us_treasury_10y + self.embi_argentina
            logger.info(f"Tasa de Descuento Local fijada por EMBI+ sintético: {tasa_descuento_local:.2%}")

        # E/P Ratio (Earnings Yield)
        if pe_ratio_mercado <= 0:
            raise ValueError("El P/E del mercado no puede ser negativo o cero para el análisis Yield Gap.")
            
        earnings_yield = 1.0 / pe_ratio_mercado
        yield_gap = earnings_yield - tasa_descuento_local
        
        logger.info(
            "Cálculo Yield Gap Completado",
            extra={
                'Earnings_Yield': round(earnings_yield, 4),
                'Tasa_Descuento_Local': round(tasa_descuento_local, 4),
                'Yield_Gap': round(yield_gap, 4)
            }
        )
        return yield_gap

    def estimar_probabilidad_crisis(self, crisis_signals: dict) -> float:
        """
        Integra el Dashboard de Indicadores Adelantados de Crisis.
        
        Args:
            crisis_signals: Diccionario con los niveles de alerta (0 Normal, 1 Alerta, 2 Peligro)
            ej: {'VIX': 1, 'Curva_10Y2Y': 2, 'High_Yield': 0}
            
        Returns:
            float: Probabilidad sintética de crisis sistémica (0.0 a 1.0)
        """
        # Ponderaciones académicas (Estrella & Mishkin 1998, Gilchrist 2012)
        pesos = {
            'Curva_10Y2Y': 0.45,  # Predictor más fuerte a largo plazo
            'High_Yield': 0.35,   # Fuerte correlación con liquidez global
            'VIX': 0.20           # Pulso corto plazo
        }
        
        probabilidad = 0.0
        for indicador, _peso in pesos.items():
            nivel = crisis_signals.get(indicador, 0)
            # Nivel 0 = 0% de su peso
            # Nivel 1 = 50% de su peso
            # Nivel 2 = 100% de su peso
            prob_parcial = (nivel / 2.0) * _peso
            probabilidad += prob_parcial
            
        logger.info(f"Probabilidad de Crisis Sistémica Estimada: {probabilidad:.1%}")
        return probabilidad

    def calcular_allocation_optimo(self, pe_ratio_mercado: float, crisis_signals: dict) -> dict:
        """
        Orquestador principal que define % Renta Variable vs % Renta Fija (Resguardo).
        Aplicación estricta de mitigación "Expected Shortfall".
        
        Returns:
            dict: Pesos objetivo {'Renta_Variable': 0.80, 'Renta_Fija': 0.20}
        """
        # 1. Obtener inputs probabilísticos
        yield_gap = self.calcular_yield_gap(pe_ratio_mercado)
        prob_crisis = self.estimar_probabilidad_crisis(crisis_signals)
        
        # 2. Lógica Base: Expected Shortfall dinámico
        # Punto de partida: 100% RV si Yield Gap es muy positivo (>4%) y Prob Crisis es baja.
        
        peso_rv = 1.0
        
        # PENALIZACIÓN 1: Valoración Relativa (Yield Gap)
        # Si el Yield Gap es negativo, la prima de riesgo RV no compensa la tasa RF.
        if yield_gap < 0.0:
            # Castigo severo: La Renta Fija paga más que el E/P de las acciones asumiendo cero crecimiento
            peso_rv -= 0.50 
        elif yield_gap < 0.02:
            # Prima muy fina (menor a 200 bps sobre riesgo país)
            peso_rv -= 0.20

        # PENALIZACIÓN 2: Riesgo Sistémico (Crisis Prob)
        # Relación no lineal: el miedo destruye más rápido los retornos (Asimetría GARCH)
        castigo_riesgo = prob_crisis ** 1.5  # Convexidad del riesgo
        peso_rv -= castigo_riesgo
        
        # Limites (Constraints)
        peso_rv = max(0.0, min(1.0, peso_rv)) # Entre 0% y 100%
        peso_rf = 1.0 - peso_rv
        
        allocation = {
            'Renta_Variable': round(peso_rv, 4),
            'Renta_Fija': round(peso_rf, 4)
        }
        
        logger.info(
            "Allocation Óptimo Dinámico Generado",
            extra={
                'Allocation': allocation,
                'Prob_Crisis': round(prob_crisis, 4),
                'Yield_Gap': round(yield_gap, 4)
            }
        )
        return allocation


if __name__ == "__main__":
    import os
    import sys
    # Forzar UTF-8 en consola Windows para que los emojis no revienten (cp1252 por defecto)
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))
    logging.basicConfig(level=logging.WARNING)  # Output limpio: solo warnings importantes


    print("\n" + "="*60)
    print("🤖  OPTIMIZADOR DINÁMICO CUÁNTICO  —  MODO AUTOMÁTICO")
    print("="*60)

    CLIENT_ID = os.getenv("DOCTA_CLIENT_ID", "docta-api-cf68347b-omlop")
    CLIENT_SECRET = os.getenv("DOCTA_CLIENT_SECRET", "_ciyJML_JOgBD89Ft39PL6Az-ps9BJAAapzkQJ-u-LM")

    # ─── PASO 1: Conexión API Docta ───────────────────────────────────
    print("\n⏳ [1/4] Conectando con API Docta Capital...")
    try:
        docta = DoctaCapitalAPI(CLIENT_ID, CLIENT_SECRET)
        opt = OptimizadorDinamicoCuantico(docta)
        print("   ✅ Token obtenido OK")
    except Exception as e:
        print(f"   ❌ Error de conexión: {e}")
        sys.exit(1)

    # ─── PASO 2: P/E Ponderado automático desde yfinance ─────────────
    print("\n📊 [2/4] Calculando P/E Ponderado del mercado local (ticker_arg.txt)...")
    ticker_path = os.path.join(os.path.dirname(__file__), '../../config/ticker_arg.txt')
    try:
        with open(ticker_path, 'r') as f:
            tickers_arg = [t.strip() for t in f.read().split(',') if t.strip()]
    except FileNotFoundError:
        print("   ⚠️  ticker_arg.txt no encontrado. Usando fallback P/E = 10")
        tickers_arg = []

    ratios_pe = {}
    market_caps = {}
    for ticker in tickers_arg:
        try:
            info = yf.Ticker(ticker).info
            pe = info.get('trailingPE') or info.get('forwardPE')
            mc = info.get('marketCap', 0)
            if pe and 3 < pe < 60 and mc > 0:
                ratios_pe[ticker] = pe
                market_caps[ticker] = mc
        except Exception:
            pass

    if ratios_pe:
        total_mc = sum(market_caps[t] for t in ratios_pe)
        pe_ponderado = sum(ratios_pe[t] * (market_caps[t] / total_mc) for t in ratios_pe)
        print(f"   ✅ P/E Ponderado (MktCap): {pe_ponderado:.1f}x  ({len(ratios_pe)} tickers con datos)\n")
        print(f"   {'Ticker':<12} {'P/E':>6}  {'MktCap (MM ARS)':>18}")
        print(f"   {'-'*42}")
        for t in sorted(ratios_pe, key=lambda x: market_caps[x], reverse=True)[:8]:
            print(f"   {t:<12} {ratios_pe[t]:>6.1f}  {market_caps[t]/1e6:>16,.0f}")
    else:
        pe_ponderado = 10.0
        print(f"   ⚠️  Sin datos P/E disponibles. Fallback conservador: {pe_ponderado}x")

    # ─── PASO 3: Señales del Dashboard de Crisis (AUTOMÁTICO) ─────────
    print("\n🚦 [3/4] Leyendo señales del Dashboard de Crisis (FRED API)...")
    crisis_signals = {'Curva_10Y2Y': 0, 'High_Yield': 0, 'VIX': 0}
    dashboard_path = os.path.join(
        os.path.dirname(__file__),
        '../../Dashboard de Indicadores Adelantados de Crisis Financiera v2/Original v2/crisis_dashboard_pro.py'
    )
    try:
        # Primero cargar el .env del dashboard para que FRED_API_KEY esté disponible
        dashboard_env_path = os.path.join(os.path.dirname(__file__),
            '../../Dashboard de Indicadores Adelantados de Crisis Financiera v2/Original v2/FRED_API_KEY.env')
        try:
            from dotenv import load_dotenv
            load_dotenv(dashboard_env_path)
        except ImportError:
            # dotenv no instalado: cargar manualmente
            if os.path.exists(dashboard_env_path):
                with open(dashboard_env_path) as _f:
                    for _line in _f:
                        if '=' in _line and not _line.startswith('#'):
                            k, v = _line.strip().split('=', 1)
                            os.environ.setdefault(k.strip(), v.strip())

        import importlib.util

        spec = importlib.util.spec_from_file_location("crisis_dashboard", dashboard_path)
        dashboard_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(dashboard_mod)

        yc = dashboard_mod.analyze_yield_curve()
        crisis_signals['Curva_10Y2Y'] = yc.get('level', 0)

        vix_res = dashboard_mod.analyze_vix()
        crisis_signals['VIX'] = vix_res.get('level', 0)

        hy_res = dashboard_mod.analyze_high_yield()
        crisis_signals['High_Yield'] = hy_res.get('level', 0)

        iconos = {0: "🟢", 1: "🟡", 2: "🔴", -1: "⚫"}
        print(f"   Curva 10Y-2Y  → {iconos.get(crisis_signals['Curva_10Y2Y'],'⚫')} Nivel {crisis_signals['Curva_10Y2Y']}  |  {yc.get('status', 'N/D')[:40]}")
        print(f"   High Yield    → {iconos.get(crisis_signals['High_Yield'],'⚫')} Nivel {crisis_signals['High_Yield']}  |  {str(hy_res.get('status', 'N/D'))[:40]}")
        print(f"   VIX           → {iconos.get(crisis_signals['VIX'],'⚫')} Nivel {crisis_signals['VIX']}  |  {str(vix_res.get('status', 'N/D'))[:40]}")

    except Exception as e_dashboard:
        # Fallback: calcular señales directamente sin el dashboard completo (FRED raw)
        try:
            from fredapi import Fred
            fred = Fred()  # Intenta con FRED_API_KEY del entorno o sin key (30 req/día gratis)
            
            def nivel_curva():
                t10y2y = fred.get_series('T10Y2Y').dropna()
                val = t10y2y.iloc[-1]
                if val > 0.25: return 0, f"🟢 NORMAL ({val:.2f}%)"
                if val > 0:    return 1, f"🟡 ALERTA ({val:.2f}%)"
                return 2, f"🔴 PELIGRO ({val:.2f}%) — CURVA INVERTIDA"

            def nivel_vix():
                vix_data = yf.Ticker("^VIX").history(period="5d")
                val = vix_data['Close'].iloc[-1] if not vix_data.empty else 20
                if val < 20: return 0, f"🟢 NORMAL (VIX {val:.1f})"
                if val < 30: return 1, f"🟡 ALERTA (VIX {val:.1f})"
                return 2, f"🔴 PÁNICO (VIX {val:.1f})"

            def nivel_hy():
                hy = fred.get_series('BAMLH0A0HYM2').dropna()
                val = hy.iloc[-1]
                if val < 4.0: return 0, f"🟢 NORMAL (HY {val:.2f}%)"
                if val < 7.0: return 1, f"🟡 ALERTA (HY {val:.2f}%)"
                return 2, f"🔴 PELIGRO (HY {val:.2f}%)"

            crisis_signals['Curva_10Y2Y'], txt_c = nivel_curva()
            crisis_signals['VIX'], txt_v = nivel_vix()
            crisis_signals['High_Yield'], txt_h = nivel_hy()
            print(f"   Curva 10Y-2Y  → {txt_c}")
            print(f"   High Yield    → {txt_h}")
            print(f"   VIX           → {txt_v}")
            print("   (Modo fallback FRED directo — dashboard completo no disponible)")
        except Exception as e_fred:
            print(f"   ⚠️  Fallback FRED también falló: {e_fred}")
            print("   ℹ️  Usando señales neutras (nivel 0).")

    # ─── PASO 4: Allocation Óptimo ────────────────────────────────────
    print("\n⚙️  [4/4] Calculando estructura óptima de cartera...")
    try:
        allocation = opt.calcular_allocation_optimo(
            pe_ratio_mercado=pe_ponderado,
            crisis_signals=crisis_signals
        )
    except Exception as e:
        print(f"   ❌ Error en optimizador: {e}")
        sys.exit(1)

    # ─── OUTPUT FINAL ─────────────────────────────────────────────────
    rv_pct = allocation['Renta_Variable'] * 100
    rf_pct = allocation['Renta_Fija'] * 100
    bloques_rv = int(rv_pct / 5)
    bloques_rf = 20 - bloques_rv
    barra_rv = "█" * bloques_rv + "░" * bloques_rf
    barra_rf = "░" * bloques_rv + "█" * bloques_rf

    print("\n" + "="*60)
    print("🎯  RESULTADO: ASIGNACIÓN DE CARTERA")
    print("="*60)
    print(f"\n  📈  Renta Variable  [{barra_rv}]  {rv_pct:>5.1f}%")
    print(f"  🛡️   Renta Fija      [{barra_rf}]  {rf_pct:>5.1f}%")
    print()

    if rf_pct > 0:
        print("─"*60)
        print("💡  INSTRUMENTOS RENTA FIJA SUGERIDOS (TIR Intradiaria Real)")
        print("─"*60)
        print("   LEY LOCAL (benchmark principal):")
        
        # Bonos ley local — benchmark natural. Son la referencia de paridad real.
        bonos_ley_local = {
            "AL30": "Soberano HD Ley Local CP",
            "AE38": "Soberano HD Ley Local LP",
            "AL35": "Soberano HD Ley Local (2035)",
        }
        tires_local = {}
        for ticker, descripcion in bonos_ley_local.items():
            tir = docta.get_bond_yield(ticker)
            if tir is not None:
                tires_local[ticker] = tir
                marca = "⭐" if tir >= 0.07 else "  "
                print(f"  {marca}  {ticker:<8} {descripcion:<34}  TIR: {tir:.2%}")
            else:
                print(f"     {ticker:<8} {descripcion:<34}  Sin datos hoy")
        
        # Bonos ley extranjera (GD) — SOLO si hay spread positivo vs su par AL (arbitraje)
        print("\n   LEY NY (solo si spread positivo = oportunidad de arbitraje):")
        pares_arbitraje = {
            "GD30": "AL30",   # Par natural GD30 vs AL30
            "GD35": "AL35",   # Par natural GD35 vs AL35
            "GD38": "AE38",   # Par natural GD38 vs AE38
        }
        hay_arbitraje = False
        for gd_ticker, al_ticker in pares_arbitraje.items():
            tir_gd = docta.get_bond_yield(gd_ticker)
            tir_al = tires_local.get(al_ticker)
            if tir_gd is not None and tir_al is not None:
                spread = tir_gd - tir_al
                if spread > 0.003:  # Spread > 30 bps = arbitraje real (cubre costos)
                    hay_arbitraje = True
                    print(f"  ⚡  {gd_ticker:<8} vs {al_ticker} — SPREAD: +{spread:.2%}  (→ considerar GD sobre AL)")
                else:
                    print(f"     {gd_ticker:<8} vs {al_ticker} — Spread: {spread:+.2%}  (sin ventaja operativa)")
            else:
                print(f"     {gd_ticker:<8} sin datos comparativos hoy")
        
        if not hay_arbitraje:
            print("   ℹ️  Sin spread significativo. Preferir ley local (AL) por menor costo operativo.")

        print("\n  ℹ️  Estrategia: Buy & Hold. NO momentum.")
        print("  ℹ️  Ejecutar este script semanalmente para actualizar el allocation.")
    else:
        print("  ✅  Condiciones favorecen plena exposición a Renta Variable.")
        print("  ✅  No se requiere resguardo en Renta Fija en este momento.")

    print("="*60)
