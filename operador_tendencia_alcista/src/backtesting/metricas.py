import pandas as pd
import numpy as np
from scipy import stats
import logging

logger = logging.getLogger(__name__)

class Metricas:
    """
    Cálculo de métricas de rendimiento con rigor científico (Artículo 2).
    """

    @staticmethod
    def calcular_sharpe(returns: pd.Series, rf: float = 0.0, periodos: int = 252) -> float:
        """
        Sharpe Ratio anualizado.
        
        Referencia: Opdyke, J.D. (2007). "Comparing Sharpe ratios".
        Journal of Asset Management.
        
        Nota: Para un cálculo riguroso de p-value y ajuste por asimetría/curtosis
        se requiere una implementación más compleja. Aquí usamos la definición estándar
        pero validamos supuestos básicos.
        """
        if len(returns) < 30:
            logger.warning("Muestra insuficiente para Sharpe Ratio (<30)")
            return 0.0
            
        exceso_retorno = returns - (rf / periodos)
        std_dev = exceso_retorno.std()
        
        if std_dev == 0:
            return 0.0
            
        sharpe = (exceso_retorno.mean() / std_dev) * np.sqrt(periodos)
        
        # Validación de asimetría (Skewness)
        skew = stats.skew(returns)
        if abs(skew) > 2:
            logger.warning(f"Alta asimetría ({skew:.2f}) en retornos. Sharpe puede estar sesgado.")
            
        return sharpe

    @staticmethod
    def calcular_max_drawdown(equity_curve: pd.Series) -> float:
        """
        Calcula la máxima caída desde un pico (Max Drawdown).
        Retorna valor positivo (ej: 0.15 para 15%).
        """
        if equity_curve.empty: return 0.0
        
        cummax = equity_curve.cummax()
        drawdown = (cummax - equity_curve) / cummax
        max_dd = drawdown.max()
        
        return max_dd

    @staticmethod
    def calcular_estadisticas_generales(trades: pd.DataFrame) -> dict:
        """
        Win Rate, Profit Factor, Promedio Ganancia/Pérdida.
        """
        # Esquema base por defecto
        resultado = {
            'total_trades': 0,
            'win_rate': 0.0,
            'profit_factor': 0.0,
            'avg_profit': 0.0,
            'avg_loss': 0.0,
            'total_pnl': 0.0
        }
        
        if trades.empty:
            return resultado
        
        ganadoras = trades[trades['pnl'] > 0]
        perdedoras = trades[trades['pnl'] <= 0]
        
        resultado['total_trades'] = len(trades)
        resultado['win_rate'] = len(ganadoras) / len(trades)
        
        gross_profit = ganadoras['pnl'].sum()
        gross_loss = abs(perdedoras['pnl'].sum())
        
        resultado['profit_factor'] = gross_profit / gross_loss if gross_loss != 0 else float('inf')
        resultado['avg_profit'] = ganadoras['pnl'].mean() if not ganadoras.empty else 0.0
        resultado['avg_loss'] = perdedoras['pnl'].mean() if not perdedoras.empty else 0.0
        resultado['total_pnl'] = trades['pnl'].sum()
        
        return resultado
