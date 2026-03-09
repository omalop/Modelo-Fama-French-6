import matplotlib.pyplot as plt
import pandas as pd
from typing import List
from ..estructura.cotas_historicas import Cota

class VisualizadorCotas:
    """
    Genera gráficos estáticos para visualizar Cotas Históricas.
    """
    
    @staticmethod
    def plot_cotas(df: pd.DataFrame, cotas: List[Cota], ticker: str):
        """
        Grafica velas japonesas, indicadores y cotas en escala logarítmica.
        """
        # Configuración de estilo
        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(16, 9))
        
        # 1. Velas Japonesas (Manual sin mplfinance)
        # Separar alcistas y bajistas
        df = df.copy()
        df['Date'] = pd.to_datetime(df.index)
        # Mapear fechas a indices numericos para evitar huecos de fines de semana
        df = df.reset_index(drop=True)
        
        up = df[df['Close'] >= df['Open']]
        down = df[df['Close'] < df['Open']]
        
        width = 0.6
        width2 = 0.05
        
        # Colores
        col_up = '#26a69a' # Verde TradingView
        col_down = '#ef5350' # Rojo TradingView
        
        # Velas Alcistas
        ax.bar(up.index, up['Close'] - up['Open'], width, bottom=up['Open'], color=col_up, edgecolor=col_up)
        ax.bar(up.index, up['High'] - up['Close'], width2, bottom=up['Close'], color=col_up, edgecolor=col_up)
        ax.bar(up.index, up['Low'] - up['Open'], width2, bottom=up['Open'], color=col_up, edgecolor=col_up)
        
        # Velas Bajistas
        ax.bar(down.index, down['Open'] - down['Close'], width, bottom=down['Close'], color=col_down, edgecolor=col_down)
        ax.bar(down.index, down['High'] - down['Open'], width2, bottom=down['Open'], color=col_down, edgecolor=col_down)
        ax.bar(down.index, down['Low'] - down['Close'], width2, bottom=down['Close'], color=col_down, edgecolor=col_down)
        
        # 2. Indicadores (Si existen en el DF)
        if 'Genial_Line' in df.columns:
            ax.plot(df.index, df['Genial_Line'], color='#fbc02d', linewidth=2, label='Genial Line (SMA 34)')
            
        if 'EMA_8' in df.columns and 'Wilder_8' in df.columns:
            ax.plot(df.index, df['EMA_8'], color='#00e5ff', linewidth=1, label='EMA 8')
            ax.plot(df.index, df['Wilder_8'], color='#e040fb', linewidth=1, label='Wilder 8')
            
        # Tunel (Gris suave)
        for col in df.columns:
            if col.startswith('EMA_') and col != 'EMA_8':
                ax.plot(df.index, df[col], color='gray', linewidth=0.5, alpha=0.5)

        # 3. Cotas Históricas
        # Mapa de colores y estilos acelerados
        estilos_cotas = {
            'Trimestral': {'color': '#0000FF', 'ls': '-', 'lw': 2.5, 'alpha': 0.9},
            'Mensual':    {'color': '#1E88E5', 'ls': '-', 'lw': 2.0, 'alpha': 0.8},
            'Semanal':    {'color': '#FF9800', 'ls': '--', 'lw': 1.5, 'alpha': 0.9},
            'Diaria':     {'color': '#D50000', 'ls': ':', 'lw': 1.0, 'alpha': 0.6}
        }

        # Calcular limites de visualizacion (percentiles para ignorar outliers)
        # Importante para grafica LOG: Evitar valores cercanos a 0 que estiran el eje.
        if not df.empty:
            min_p = df['Low'].quantile(0.01) * 0.9
            max_p = df['High'].quantile(0.99) * 1.1
            # Asegurar minimo razonable para log (ej: 0.1)
            min_view = max(0.1, min_p)
            ax.set_ylim(bottom=min_view, top=max_p)
        else:
            min_view, max_p = 0.1, 100

        # Dibujar cotas (Solo las visibles en el rango actual)
        processed_prices = []
        for cota in cotas:
            # Filtrar estrictamente por rango visual
            if not (min_view <= cota.precio <= max_p):
                continue
                
            conf = estilos_cotas.get(cota.jerarquia, estilos_cotas['Diaria'])
            ax.axhline(y=cota.precio, color=conf['color'], linestyle=conf['ls'], 
                       linewidth=conf['lw'], alpha=conf['alpha'])
            
            # Texto
            too_close = any(abs(cota.precio - p) / p < 0.05 for p in processed_prices)
            if not too_close:
                # Pegar texto a la derecha, pero dentro del rango X
                x_pos = df.index[-1]
                ax.text(x_pos, cota.precio, f" {cota.jerarquia} {cota.precio:.2f}", 
                        color=conf['color'], verticalalignment='center', fontsize=9, fontweight='bold')
                processed_prices.append(cota.precio)

        from matplotlib.ticker import ScalarFormatter
        
        # 4. Configuración Final
        ax.set_yscale('log')
        ax.yaxis.set_major_formatter(ScalarFormatter())
        ax.set_title(f"Análisis Cotas Históricas {ticker} [Log] - Velas Diarias (10 Años)", fontsize=14, color='white')
            
        ax.grid(True, which='major', linestyle='--', linewidth=0.5, alpha=0.5)
        ax.grid(True, which='minor', linestyle=':', linewidth=0.3, alpha=0.2)
        
        # Formatear Eje X
        step = max(1, len(df) // 10)
        ax.set_xticks(df.index[::step])
        ax.set_xticklabels(df['Date'].dt.strftime('%Y-%m-%d').iloc[::step], rotation=45, ha='right')
        
        # Leyenda compacta
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(handles, labels, loc='upper left', facecolor='black', framealpha=0.5, fontsize=8)
            
        plt.tight_layout()
        plt.show()
