import pandas as pd
import logging
from typing import List, Dict
from datetime import timedelta
from ..data.repo_datos import RepositorioDatos
from ..senales.generador_senales import GeneradorSenales, SenalEntrada
from ..gestion.gestor_posicion import GestorPosicion, Cota
from ..estructura.cotas_historicas import DetectorCotas
from .metricas import Metricas

logger = logging.getLogger(__name__)

class MotorBacktest:
    """
    Simulador de trading Walk-Forward.
    Maneja estado del portafolio y rotación de activos.
    """
    
    def __init__(self, capital_inicial: float = 10000.0, comision: float = 0.001):
        self.capital = capital_inicial
        self.equity = [capital_inicial]
        self.comision = comision
        self.posicion_actual = None # Dict: {ticker, precio_entrada, cantidad, stop_loss, take_profit, fecha_entrada}
        self.historial_trades = []
        
        self.repo = RepositorioDatos()
        self.generador = GeneradorSenales()
        self.detector_cotas = DetectorCotas()
        
    def ejecutar(self, tickers: List[str], fecha_inicio: str, fecha_fin: str):
        """
        Ejecuta la simulación iterando día a día.
        """
        logger.info(f"Iniciando Backtest desde {fecha_inicio} hasta {fecha_fin}")
        
        # 1. Pre-cargar datos para eficiencia (o cargar bajo demanda con caché en repo)
        # Por simplicidad, asumimos que el repo maneja caché.
        
        rango_fechas = pd.date_range(start=fecha_inicio, end=fecha_fin, freq='D')
        
        # Cache de datos multitemporales por ticker
        datos_cache = {}
        for t in tickers:
            datos_cache[t] = self.repo.obtener_todo_multitemporal(t)

        # Loop Transaccional (Diario)
        for fecha in rango_fechas:
            if self.posicion_actual:
                self._gestionar_posicion(fecha, datos_cache)
            else:
                self._buscar_oportunidad(fecha, tickers, datos_cache)
                
            self.equity.append(self.capital) # Log equity diario
            
        logger.info("Backtest finalizado.")
        return self._generar_reporte()

    def _buscar_oportunidad(self, fecha, tickers, datos_cache):
        """
        Busca señales en la lista de tickers (Rotación).
        Simula 'Ranking Global': Itera en orden.
        """
        for ticker in tickers:
            datos = datos_cache.get(ticker)
            if not datos: continue
            
            # Cortar datos hasta fecha actual (Walk-Forward)
            # Optimización: Usar slice en luegar de copia total si es posible
            datos_wf = {}
            valid_wf = True
            for tf, df in datos.items():
                if df.empty: continue
                # Filtrar hasta fecha (inclusive)
                corte = df[df.index <= fecha]
                if corte.empty: 
                    valid_wf = False
                    break
                datos_wf[tf] = corte
            
            if not valid_wf: continue
            
            # Analizar Señal
            senal = self.generador.analizar_ticker(ticker, datos_wf)
            if senal:
                self._abrir_posicion(senal, datos_wf)
                break # Solo 1 posición a la vez (por ahora)

    def _abrir_posicion(self, senal: SenalEntrada, datos_wf: Dict[str, pd.DataFrame]):
        """
        Ejecuta orden de compra.
        """
        precio = senal.precio
        cantidad = (self.capital * 0.98) / precio # Usar 98% capital disponibles (margen erro)
        costo = precio * cantidad * (1 + self.comision)
        
        if costo > self.capital:
            cantidad = self.capital / (precio * (1 + self.comision))
        
        # Calcular Stop/TP
        df_diario = datos_wf['diario']
        stop_loss = GestorPosicion.calcular_stop_loss_inicial(df_diario)
        
        # Calcular Cotas para TP
        cotas = self.detector_cotas.detectar(datos_wf)
        take_profit = GestorPosicion.calcular_take_profit(precio, cotas)
        
        self.posicion_actual = {
            'ticker': senal.ticker,
            'precio_entrada': precio,
            'cantidad': cantidad,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'fecha_entrada': senal.fecha
        }
        self.capital -= costo # Descontar efectivo
        logger.info(f"OPEN LONG {senal.ticker} @ {precio:.2f} en {senal.fecha}")

    def _gestionar_posicion(self, fecha, datos_cache):
        """
        Verifica salida (Stop, TP, Invalidez).
        """
        pos = self.posicion_actual
        ticker = pos['ticker']
        df_dia = datos_cache[ticker].get('diario')
        
        # Obtener vela del día (o ultima disponible <= fecha)
        try:
            row = df_dia.loc[fecha]
        except KeyError:
            return # No hubo mercado hoy
            
        precio_actual = row['Close']
        low = row['Low']
        high = row['High']
        
        # Chequeo Stop Loss
        if low <= pos['stop_loss']:
            self._cerrar_posicion(pos['stop_loss'], fecha, 'STOP_LOSS')
            return

        # Chequeo Take Profit
        if pos['take_profit'] and high >= pos['take_profit']:
            self._cerrar_posicion(pos['take_profit'], fecha, 'TAKE_PROFIT')
            return
            
        # Chequeo Invalidez (2 velas)
        # Necesitamos el DF hasta hoy
        df_hasta_hoy = df_dia[df_dia.index <= fecha]
        if GestorPosicion.verificar_salida_invalidez(df_hasta_hoy):
            self._cerrar_posicion(precio_actual, fecha, 'INVALIDEZ_FLUJO')
            return

    def _cerrar_posicion(self, precio, fecha, motivo):
        pos = self.posicion_actual
        ingreso = precio * pos['cantidad'] * (1 - self.comision)
        pnl = ingreso - (pos['precio_entrada'] * pos['cantidad'])
        
        self.capital += ingreso
        
        self.historial_trades.append({
            'ticker': pos['ticker'],
            'entry_date': pos['fecha_entrada'],
            'exit_date': fecha,
            'entry_price': pos['precio_entrada'],
            'exit_price': precio,
            'pnl': pnl,
            'motivo': motivo
        })
        
        logger.info(f"CLOSE {pos['ticker']} @ {precio:.2f}. PnL: {pnl:.2f}. Motivo: {motivo}")
        self.posicion_actual = None

    def _generar_reporte(self):
        """
        Calcula métricas finales usando Metricas.
        """
        df_trades = pd.DataFrame(self.historial_trades)
        equity_series = pd.Series(self.equity)
        
        stats = Metricas.calcular_estadisticas_generales(df_trades)
        dd = Metricas.calcular_max_drawdown(equity_series)
        sharpe = Metricas.calcular_sharpe(equity_series.pct_change().dropna())
        
        reporte = {
            'metricas': stats,
            'max_drawdown': dd,
            'sharpe_ratio': sharpe,
            'capital_final': self.capital,
            'trades': df_trades
        }
        return reporte
