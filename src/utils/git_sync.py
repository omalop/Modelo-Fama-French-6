import subprocess
import os
import logging
from datetime import datetime

# Configuración de logging
os.makedirs('logs', exist_ok=True)  # Crear carpeta si no existe (necesario en CI/CD)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/git_sync.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def run_git_command(command):
    try:
        result = subprocess.run(
            command, 
            check=True, 
            capture_output=True, 
            text=True, 
            shell=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error(f"Error en comando git: {e.stderr}")
        return None

def sync():
    logger.info("Iniciando sincronización con GitHub...")
    
    # Asegurarse de estar en el directorio raíz del proyecto
    # Asumimos que el script está en src/utils/
    root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    os.chdir(root_dir)
    
    # 1. Agregar cambios
    run_git_command("git add .")
    
    # 2. Commit con timestamp
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    commit_msg = f"Auto-sync: {timestamp}"
    run_git_command(f'git commit -m "{commit_msg}"')
    
    # 3. Push
    push_result = run_git_command("git push origin main")
    if push_result is not None:
        logger.info("Sincronización exitosa.")
    else:
        logger.warning("No se pudo completar el push. Verifique conexión o conflictos.")

if __name__ == "__main__":
    sync()
