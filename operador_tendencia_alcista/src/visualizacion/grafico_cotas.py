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
        Grafica velas y líneas de cotas.
        """
        plt.figure(figsize=(12, 6))
        
        # Plot precio cierre (simplificado)
        plt.plot(df.index, df['Close'], label='Precio Daily', color='black', alpha=0.6)
        
        # Plot Cotas
        colores = {
            'Trimestral': 'blue', # Azul
            'Semanal': 'orange',  # Naranja
            'Diaria': 'green'     # Verde
        }
        
        estilos = {
            'Trimestral': '-',
            'Semanal': '--',
            'Diaria': ':'
        }
        
        for cota in cotas:
            c = colores.get(cota.jerarquia, 'gray')
            ls = estilos.get(cota.jerarquia, '-')
            plt.axhline(y=cota.precio, color=c, linestyle=ls, alpha=0.8, 
                        label=f"{cota.jerarquia} ({cota.precio:.2f})")
            
        plt.title(f"Análisis Cotas Históricas: {ticker}")
        plt.xlabel("Fecha")
        plt.ylabel("Precio")
        # Evitar duplicar labels en leyenda
        handles, labels = plt.gca().get_legend_handles_labels()
        by_label = dict(zip(labels, handles))
        plt.legend(by_label.values(), by_label.keys())
        
        plt.grid(True, which='both', linestyle='--', linewidth=0.5)
        plt.tight_layout()
        plt.show() # O guardar en archivo
