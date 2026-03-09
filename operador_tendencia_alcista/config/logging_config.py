import logging
import logging.config
import os
from .settings import LOG_DIR

def setup_logging(default_path='logging.yaml', default_level=logging.INFO, env_key='LOG_CFG'):
    """
    Setup logging configuration
    """
    path = default_path
    value = os.getenv(env_key, None)
    if value:
        path = value
    
    if os.path.exists(path):
        import yaml
        with open(path, 'rt') as f:
            config = yaml.safe_load(f.read())
        logging.config.dictConfig(config)
    else:
        # Configuración por defecto (Artículo 5)
        logging.basicConfig(
            level=default_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(LOG_DIR / 'app.log', encoding='utf-8'),
                logging.StreamHandler()
            ]
        )
        
    # Log inicial
    logger = logging.getLogger(__name__)
    logger.info("Logging científico inicializado. Directorio: %s", LOG_DIR)
