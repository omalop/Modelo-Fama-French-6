import argparse
import logging
import sys
import os

# Configurar path para importar src
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from src.data.db_manager import DBManager

# Configuración de Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("Maintenance")

def main():
    parser = argparse.ArgumentParser(description="Herramienta de Mantenimiento de Base de Datos Quantamental")
    parser.add_argument(
        "--vaciar", 
        choices=["precios", "fundamentales", "todo"],
        help="Especifica qué información desea eliminar de la base de datos."
    )
    
    args = parser.parse_args()

    if not args.vaciar:
        parser.print_help()
        return

    try:
        db = DBManager()
        
        if args.vaciar == "precios":
            logger.info("Iniciando limpieza de PRECIOS...")
            db.clear_table("prices")
            logger.info("Limpieza de PRECIOS completada.")
            
        elif args.vaciar == "fundamentales":
            logger.info("Iniciando limpieza de FUNDAMENTALES...")
            db.clear_table("financials")
            logger.info("Limpieza de FUNDAMENTALES completada.")
            
        elif args.vaciar == "todo":
            logger.info("Iniciando limpieza TOTAL de la base de datos...")
            db.clear_table("all")
            logger.info("Base de datos reseteada por completo.")
            
        db.close()
        
    except Exception as e:
        logger.error(f"Error durante el mantenimiento: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
