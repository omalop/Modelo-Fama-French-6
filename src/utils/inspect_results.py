import pandas as pd
import os

files = ['Ranking_Global_Top.xlsx', 'Ranking_Argentina_Top.xlsx']

for f in files:
    if os.path.exists(f):
        print(f"\n--- INSPECCIONANDO {f} ---")
        try:
            df = pd.read_excel(f)
            print("COLUMNAS:", list(df.columns))
            
            # Verificar columnas nuevas
            cols_check = ['Z_Inv_Capped', 'Raw_Mom_Score', 'Z_Mom', 'Final_Score', 'Profitability']
            missing = [c for c in cols_check if c not in df.columns]
            if missing:
                print(f"FALTAN COLUMNAS: {missing}")
            else:
                print("Estructura de columnas CORRECTA.")
                
                # Verificación de Winsorization (Rango -3 a 3)
                print("\nESTADISTICAS Z-SCORES (Deben estar entre -3 y 3 aprox):")
                z_cols = [
                    'Z_Value', 'Z_Prof', 'Z_Inv', 'Z_Mom',
                    'Z_Book_to_Market', 'Z_Profitability', 'Z_Asset_Growth'
                ]
                existing_z = [c for c in z_cols if c in df.columns]
                if existing_z:
                    print(df[existing_z].describe().loc[['min', 'max', 'mean']].to_string())
                
                # Check específico MA y ALAB
                print("\nCASOS AUDITORIA (MA, ALAB, BIOX):")
                audit_tickers = ['MA', 'ALAB', 'BIOX', 'SPCE']
                mask_audit = df['Ticker'].isin(audit_tickers)
                if mask_audit.any():
                    cols_audit = ['Ticker', 'Final_Score', 'Profitability', 'Z_Prof', 'Z_Profitability']
                    cols_present = [c for c in cols_audit if c in df.columns]
                    print(df.loc[mask_audit, cols_present].to_string())
            
            # Top 5
            print("\nTOP 5:")
            print(df[['Ticker', 'Final_Score', 'Raw_Mom_Score', 'Profitability']].head().to_string())
            
            # Chequeo de casos especificos
            check_tickers = ['PCAR3', 'SPCE', 'BIOX', 'AAPL', 'CL']
            print("\nCASOS ESPECIFICOS:")
            found = df[df['Ticker'].isin(check_tickers)]
            if not found.empty:
                print(found[['Ticker', 'Final_Score', 'Z_Inv_Capped', 'Profitability', 'Raw_Mom_Score']].to_string())
            else:
                print("No se encontraron los tickers de prueba.")
                
        except Exception as e:
            print(f"Error leyendo {f}: {e}")
    else:
        print(f"Archivo {f} NO ENCONTRADO (quizás sigue procesando).")
