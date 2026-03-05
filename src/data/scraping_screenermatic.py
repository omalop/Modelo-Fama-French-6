"""
Módulo de extracción y persistencia de datos de bonos desde Screenermatic.

Descripción:
    Implementa un cliente HTTP con técnicas de evasión de detección (stealth)
    para extraer datos de bonos en modo autenticado. Utiliza cookies de sesión
    del navegador para acceder a funcionalidades premium.

    El módulo cubre las siguientes secciones del screener:
    - Descriptivos: información básica del instrumento
    - Tasas/YTM:    TIR, Macaulay Duration, Modified Duration, Convexidad, Paridad

    Los datos descargados se persisten en DuckDB (mismo archivo que cache_docta)
    con TTL de 1 día hábil. Si el dato existe y es del día actual, no se vuelve
    a descargar, evitando requests innecesarios al servidor.

    Patrón de caché:
        Fowler, M. (2002). Patterns of Enterprise Application Architecture. Cap. 18.
        Cache-Aside / Stale-While-Revalidate.

    Limitaciones conocidas:
    - Las cookies de sesión expiran (aprox. 24-48h) y requieren renovación manual.
    - Los datos de la sección "Curvas" se renderizan vía JS y no están disponibles
      como tablas HTML estáticas; requieren enfoque de automatización de navegador.

Referencia técnica:
    Mitchell, R. (2015). Web Scraping with Python. O'Reilly Media.
    - Cap. 11: Scrapers in the Real World - manejo de sesiones y cookies.

Supuestos:
    - Las cookies de sesión tienen una vida útil de al menos una jornada bursátil.
    - El servidor no implementa rate-limiting agresivo para usuarios premium.
    - Los datos de precio tienen un retraso de ~15 min respecto al mercado en tiempo real.

Limitaciones conocidas:
    - Si el servidor cambia la estructura HTML de las tablas, el parser puede fallar.
    - Las cookies expiran y requieren renovación periódica vía navegador.
    - Los datos de la sección "Curvas" no están disponibles en tablas HTML estáticas
      (se renderizan vía JavaScript/canvas) y requieren un enfoque diferente.
"""

import os
import re
import time
import random
import logging
import warnings
from io import StringIO
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import duckdb
import pandas as pd
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
#  Configuración de logging
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  Constantes
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL = "https://www.screenermatic.com"
PAGINA_BONOS = 20          # Screenermatic tiene paginación de 20 filas
MAX_PAGINAS = 50           # Límite de seguridad anti-loop infinito (1000 bonos)
DELAY_MIN_SEG = 1.2        # Pausa mínima entre requests (stealth)
DELAY_MAX_SEG = 2.8        # Pausa máxima entre requests (stealth)

# Headers de navegador Chrome real para evitar detección como bot
HEADERS_NAVEGADOR_CHROME = {
    "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                 "AppleWebKit/537.36 (KHTML, like Gecko) "
                                 "Chrome/122.0.0.0 Safari/537.36",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;"
                                 "q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language":           "es-AR,es;q=0.9,en-US;q=0.7,en;q=0.5",
    "Accept-Encoding":           "gzip, deflate, br",
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":            "document",
    "Sec-Fetch-Mode":            "navigate",
    "Sec-Fetch-Site":            "same-origin",
    "Referer":                   f"{BASE_URL}/",
}

# ─────────────────────────────────────────────────────────────────────────────
#  URLs de las secciones de bonos
# ─────────────────────────────────────────────────────────────────────────────

URLS_SECCIONES = {
    "descriptivos": f"{BASE_URL}/bondsdescriptive.php",
    "tasas_ytm":    f"{BASE_URL}/bondsytm.php",
    "rendimiento":  f"{BASE_URL}/bondsperformance.php",
}

# ─────────────────────────────────────────────────────────────────────────────
#  Configuración de cookies (se cargan desde .env)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CookiesSession:
    """
    Contenedor de cookies de sesión premium de Screenermatic.

    Las cookies se obtienen iniciando sesión manualmente en el navegador
    y extrayéndolas desde DevTools > Application > Cookies.

    Vida útil estimada: 24-48 horas (depende de inactividad del servidor).
    """
    phpsessid:      str
    maticlang:      str = "esp"
    visitor_id:     str = ""
    ga:             str = ""

    def as_dict(self) -> dict:
        """Convierte las cookies a formato dict para requests.Session."""
        cookies = {
            "PHPSESSID":  self.phpsessid,
            "maticlang":  self.maticlang,
        }
        if self.visitor_id:
            cookies["visitor_id"] = self.visitor_id
        if self.ga:
            cookies["_ga"] = self.ga
        return cookies

    @classmethod
    def desde_env(cls) -> "CookiesSession":
        """
        Carga las cookies desde variables de entorno (.env).

        Raises:
            ValueError: Si SCREENERMATIC_PHPSESSID no está configurado.
        """
        load_dotenv()
        phpsessid = os.getenv("SCREENERMATIC_PHPSESSID", "")
        if not phpsessid:
            raise ValueError(
                "SCREENERMATIC_PHPSESSID no encontrado en .env.\n"
                "Obtenerlo desde el navegador: F12 → Application → Cookies → "
                "screenermatic.com → PHPSESSID"
            )
        return cls(
            phpsessid=phpsessid,
            maticlang=os.getenv("SCREENERMATIC_MATICLANG", "esp"),
            visitor_id=os.getenv("SCREENERMATIC_VISITOR_ID", ""),
            ga=os.getenv("SCREENERMATIC_GA", ""),
        )


# ─────────────────────────────────────────────────────────────────────────────
#  Helpers de limpieza
# ─────────────────────────────────────────────────────────────────────────────

def _limpiar_porcentaje(valor) -> Optional[float]:
    """
    Convierte strings de porcentaje como '10.08 %' a float 10.08.
    Retorna None si el valor no es convertible.
    """
    if pd.isna(valor):
        return None
    texto = str(valor).strip().replace("%", "").replace(",", ".").strip()
    try:
        return float(texto)
    except ValueError:
        return None


def _detectar_paywall(df: pd.DataFrame) -> bool:
    """
    Detecta si el DataFrame contiene el mensaje de paywall de Screenermatic.
    Si más del 50% de los valores de texto contienen 'Acced' o 'Registr',
    se considera que la sesión no tiene acceso premium.
    """
    textos = df.select_dtypes(include="object").values.flatten()
    if len(textos) == 0:
        return False
    paywall_count = sum(
        1 for v in textos
        if isinstance(v, str) and ("Acced" in v or "Registr" in v)
    )
    return paywall_count / len(textos) > 0.30


def _pausa_stealth():
    """Pausa aleatoria entre requests para simular comportamiento humano."""
    segundos = random.uniform(DELAY_MIN_SEG, DELAY_MAX_SEG)
    time.sleep(segundos)


# ─────────────────────────────────────────────────────────────────────────────
#  Clase principal del scraper
# ─────────────────────────────────────────────────────────────────────────────

class ScreenermaticScraper:
    """
    Cliente de extracción de datos de bonos para Screenermatic.

    Patrón de uso:
        scraper = ScreenermaticScraper.desde_env()
        df_bonos = scraper.get_bonos_completo()

    Referencia:
        ScreenerMatic - Documentación de secciones de bonos:
        https://www.screenermatic.com/apidoc.php
    """

    def __init__(self, cookies: CookiesSession):
        self._cookies = cookies
        self._session = requests.Session()
        self._session.headers.update(HEADERS_NAVEGADOR_CHROME)
        self._session.cookies.update(cookies.as_dict())
        logger.info(
            "ScreenermaticScraper inicializado | "
            f"PHPSESSID: ...{cookies.phpsessid[-6:]}"
        )

    @classmethod
    def desde_env(cls) -> "ScreenermaticScraper":
        """Crea el scraper cargando credenciales desde .env."""
        cookies = CookiesSession.desde_env()
        return cls(cookies)

    # ─── Métodos internos ───────────────────────────────────────────────────

    def _get_pagina(self, url: str, ini: int = 0) -> Optional[pd.DataFrame]:
        """
        Descarga y parsea una página de tabla de bonos.

        Args:
            url:  URL base de la sección (sin parámetros de paginación).
            ini:  Offset de inicio de paginación (múltiplos de PAGINA_BONOS).

        Returns:
            DataFrame con los datos de la página, o None si hay error/paywall.
        """
        params = {
            "variable":    "",
            "ordenamiento": "",
            "hojassel":    str(PAGINA_BONOS),
            "ini":         str(ini),
        }
        try:
            response = self._session.get(url, params=params, timeout=15)
            response.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"Error HTTP en {url} (ini={ini}): {e}")
            return None

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                dfs = pd.read_html(StringIO(response.text))
            except ValueError:
                logger.warning(f"Sin tablas HTML en {url} (ini={ini})")
                return None

        if not dfs:
            return None

        df = max(dfs, key=len)

        if _detectar_paywall(df):
            logger.error(
                f"PAYWALL detectado en {url}. "
                "Las cookies de sesión pueden haber expirado. "
                "Renovarlas desde el navegador y actualizar SCREENERMATIC_PHPSESSID en .env."
            )
            return None

        return df

    def _paginar_seccion(self, seccion: str) -> pd.DataFrame:
        """
        Descarga todas las páginas de una sección y las concatena.

        Args:
            seccion: Clave de URLS_SECCIONES ('descriptivos', 'tasas_ytm', etc.)

        Returns:
            DataFrame consolidado con todos los bonos de la sección.
        """
        if seccion not in URLS_SECCIONES:
            raise ValueError(
                f"Sección '{seccion}' no reconocida. "
                f"Opciones: {list(URLS_SECCIONES.keys())}"
            )

        url = URLS_SECCIONES[seccion]
        paginas = []
        ini = 0

        logger.info(f"Iniciando descarga de sección '{seccion}' desde {url}")

        for num_pagina in range(MAX_PAGINAS):
            _pausa_stealth()
            df_pag = self._get_pagina(url, ini=ini)

            if df_pag is None:
                logger.warning(
                    f"Sección '{seccion}': no se pudo obtener página con ini={ini}. "
                    "Deteniendo paginación."
                )
                break

            # Si la página trae menos filas que el tamaño de página, es la última
            paginas.append(df_pag)
            logger.info(
                f"  Página {num_pagina + 1} descargada | "
                f"ini={ini} | {len(df_pag)} filas"
            )

            if len(df_pag) < PAGINA_BONOS:
                logger.info(
                    f"Sección '{seccion}': última página alcanzada "
                    f"({len(df_pag)} < {PAGINA_BONOS} filas)."
                )
                break

            ini += PAGINA_BONOS

        if not paginas:
            logger.error(f"Sección '{seccion}': no se obtuvo ningún dato.")
            return pd.DataFrame()

        df_completo = pd.concat(paginas, ignore_index=True)
        logger.info(
            f"Sección '{seccion}' completada: "
            f"{len(df_completo)} bonos totales."
        )
        return df_completo

    # ─── Métodos públicos ────────────────────────────────────────────────────

    def get_descriptivos(self) -> pd.DataFrame:
        """
        Retorna los datos descriptivos de todos los bonos.

        Campos disponibles:
            Simbolo, Descripcion, Emisor, Tipo, País, Mercado, Moneda, Precio, Variación

        Returns:
            DataFrame con información básica de cada instrumento.
        """
        df = self._paginar_seccion("descriptivos")
        if df.empty:
            return df

        # Normalización de columnas
        df = df.rename(columns={
            "Pa\ufffdls":       "Pais",
            "Variaci\ufffd\ufffdn": "Variacion_pct",
        })
        df.columns = [c.replace("/", "_").replace(" ", "_").lower() for c in df.columns]

        # Limpiar columna de variación
        if "variacion_pct" in df.columns:
            df["variacion_pct"] = df["variacion_pct"].apply(_limpiar_porcentaje)

        # Eliminar columna de simulador (solo contiene texto "Simulador")
        if "simulacion" in df.columns:
            df.drop(columns=["simulacion"], inplace=True)

        logger.info(f"Descriptivos: {len(df)} bonos | Cols: {df.columns.tolist()}")
        return df

    def get_tasas_ytm(self) -> pd.DataFrame:
        """
        Retorna métricas de tasa y sensibilidad de todos los bonos.

        Campos disponibles:
            Simbolo, RV%, V.Nominal, V.Nominal_Act, Int.Corrido, Valor_Tecnico,
            Paridad, TIR, Macaulay_Dur, Modified_Dur, Effective_Dur, Convexidad,
            Precio, Variacion_pct

        Fundamento:
            - Duration de Macaulay: Macaulay, F. (1938). "The Movements of Interest
              Rates, Bond Yields and Stock Prices in the United States since 1856".
              National Bureau of Economic Research.
            - Duration Modificada: Hicks, J.R. (1939). "Value and Capital".
              Oxford University Press.
            - Convexidad: Frank Fabozzi (2007). "Fixed Income Mathematics". McGraw-Hill.

        Returns:
            DataFrame con métricas cuantitativas de riesgo de tasa de interés.
        """
        df = self._paginar_seccion("tasas_ytm")
        if df.empty:
            return df

        # Renombrar columnas problemáticas con caracteres especiales
        renombres = {
            "Variaci\ufffd\ufffdn": "Variacion_pct",
            "RV %":                 "rv_pct",
            "V. Nominal":           "v_nominal",
            "V. Nominal Act.":      "v_nominal_actualizado",
            "Int. Corrido":         "interes_corrido",
            "Valor Tecnico":        "valor_tecnico",
            "Paridad":              "paridad_pct",
            "TIR":                  "tir_pct",
            "Macaulay Dur.":        "macaulay_dur",
            "Modified Dur.":        "modified_dur",
            "Effective Dur.":       "effective_dur",
            "Convexidad":           "convexidad",
            "Precio":               "precio",
        }
        df = df.rename(columns=renombres)
        df.columns = [c.lower() for c in df.columns]

        # Limpiar columnas de porcentaje
        for col in ["paridad_pct", "tir_pct", "variacion_pct"]:
            if col in df.columns:
                df[col] = df[col].apply(_limpiar_porcentaje)

        # Convertir columnas numéricas
        cols_numericas = [
            "rv_pct", "v_nominal", "v_nominal_actualizado", "interes_corrido",
            "valor_tecnico", "macaulay_dur", "modified_dur",
            "effective_dur", "convexidad", "precio"
        ]
        for col in cols_numericas:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        logger.info(f"Tasas/YTM: {len(df)} bonos | Cols: {df.columns.tolist()}")
        return df

    def get_bonos_completo(self) -> pd.DataFrame:
        """
        Retorna el universo completo de bonos con datos descriptivos y de tasas,
        unificados en un único DataFrame por símbolo (ticker).

        El join se realiza por la columna 'simbolo' (ticker del instrumento).
        Si alguna sección falla, retorna solamente la que estuvo disponible.

        Returns:
            DataFrame consolidado. En caso de error total, DataFrame vacío.
        """
        logger.info("Iniciando descarga completa del universo de bonos...")

        df_desc = self.get_descriptivos()
        _pausa_stealth()
        df_tasas = self.get_tasas_ytm()

        if df_desc.empty and df_tasas.empty:
            logger.error("No se pudo obtener ningún dato. Revisar cookies.")
            return pd.DataFrame()

        if df_desc.empty:
            logger.warning("Descriptivos no disponibles. Retornando solo Tasas/YTM.")
            return df_tasas

        if df_tasas.empty:
            logger.warning("Tasas/YTM no disponibles. Retornando solo Descriptivos.")
            return df_desc

        # Unir por ticker
        # Tasas ya tiene 'precio' y 'variacion_pct', los eliminamos de descriptivos para evitar duplicados
        cols_a_eliminar_desc = [
            c for c in ["precio", "variacion_pct", "fecha_hora", "volumen"]
            if c in df_desc.columns
        ]
        df_desc_slim = df_desc.drop(columns=cols_a_eliminar_desc)

        df_completo = pd.merge(
            df_desc_slim,
            df_tasas,
            on="simbolo",
            how="outer",
            suffixes=("_desc", "_ytm"),
        )

        logger.info(
            f"DataFrame consolidado: {len(df_completo)} bonos | "
            f"{len(df_completo.columns)} columnas."
        )
        return df_completo

    def verificar_sesion(self) -> bool:
        """
        Verifica si la sesión actual tiene acceso a datos premium.

        Returns:
            True si la sesión es válida y accede a datos premium.
            False si las cookies expiraron o hubo un error.
        """
        logger.info("Verificando validez de la sesión premium...")
        df = self._get_pagina(URLS_SECCIONES["tasas_ytm"], ini=0)
        if df is None or df.empty:
            logger.warning("Sesión inválida o expirada.")
            return False
        logger.info("Sesión premium válida y activa.")
        return True


# ─────────────────────────────────────────────────────────────────────────────
#  Capa de Persistencia DuckDB
# ─────────────────────────────────────────────────────────────────────────────

ROOT_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
_DB_PATH_DEFAULT = os.path.join(ROOT_DIR, "data", "docta_cache.duckdb")
_TTL_HORAS = 8  # Refrescar si los datos tienen más de 8 horas (media jornada)


class CacheScreenermatic:
    """
    Capa de persistencia DuckDB para datos de bonos de Screenermatic.

    Patrón Cache-Aside:
      1. Consultar base local (tabla screenermatic_bonos).
      2. Si el dato existe y tiene menos de _TTL_HORAS horas → servir desde DB.
      3. Si está vencido o vacío → ejecutar ScreenermaticScraper completo,
         persistir el resultado y devolverlo.

    Esta clase envuelve a ScreenermaticScraper de forma transparente:
    el código consumidor (optimizador_cartera.py) solo llama
    ``get_bonos()`` y nunca sabe si los datos vinieron de la red o del caché.

    Referencia:
        Patrón Cache-Aside / Proxy.
        Fowler, M. (2002). Patterns of Enterprise Application Architecture.
        Addison-Wesley, pp. 497-502.

    Supuestos:
        - Los precios de cierre de bonos se actualizan una vez por jornada.
        - Un TTL de 8 horas es suficientemente conservador para no servir
          datos de jornadas anteriores en horario de mercado.

    Args:
        db_path:   Ruta al archivo .duckdb (default: data/docta_cache.duckdb).
        ttl_horas: Horas de validez del caché (default: 8).
    """

    def __init__(
        self,
        db_path: str = _DB_PATH_DEFAULT,
        ttl_horas: int = _TTL_HORAS,
    ):
        self._ttl = timedelta(hours=ttl_horas)
        self._db_path = db_path
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._conn = duckdb.connect(self._db_path)
        self._inicializar_schema()
        logger.info(
            "CacheScreenermatic inicializado | DB: %s | TTL: %dh",
            self._db_path,
            ttl_horas,
        )

    # ── Infraestructura interna ─────────────────────────────────────────────

    def _inicializar_schema(self) -> None:
        """Crea la tabla screenermatic_bonos si no existe (idempotente)."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS screenermatic_bonos (
                simbolo             VARCHAR,
                descripcion         VARCHAR,
                emisor              VARCHAR,
                tipo                VARCHAR,
                pais                VARCHAR,
                mercado             VARCHAR,
                moneda              VARCHAR,
                precio              DOUBLE,
                rv_pct              DOUBLE,
                v_nominal           DOUBLE,
                v_nominal_actualizado DOUBLE,
                interes_corrido     DOUBLE,
                valor_tecnico       DOUBLE,
                paridad_pct         DOUBLE,
                tir_pct             DOUBLE,
                macaulay_dur        DOUBLE,
                modified_dur        DOUBLE,
                effective_dur       DOUBLE,
                convexidad          DOUBLE,
                variacion_pct       DOUBLE,
                ultima_actualizacion TIMESTAMP,
                PRIMARY KEY (simbolo)
            )
        """)
        logger.debug("Schema screenermatic_bonos verificado.")

    def _cache_vigente(self) -> bool:
        """
        Verifica si el caché tiene datos frescos.

        Returns:
            True si la tabla tiene datos y la actualización más reciente
            no supera el TTL configurado.
        """
        resultado = self._conn.execute(
            "SELECT MAX(ultima_actualizacion) FROM screenermatic_bonos"
        ).fetchone()

        if resultado is None or resultado[0] is None:
            logger.info("Caché Screenermatic vacío.")
            return False

        ultima = resultado[0]
        if isinstance(ultima, str):
            ultima = datetime.fromisoformat(ultima)

        antigüedad = datetime.now() - ultima
        vigente = antigüedad <= self._ttl
        logger.info(
            "Caché Screenermatic | Última actualización: %s | "
            "Antigüedad: %.1fh | Vigente: %s",
            ultima.strftime("%Y-%m-%d %H:%M"),
            antigüedad.total_seconds() / 3600,
            vigente,
        )
        return vigente

    def _persistir(self, df: pd.DataFrame) -> None:
        """
        Guarda el DataFrame de bonos en DuckDB.
        Reemplaza los datos existentes (DELETE + INSERT) para mantener
        siempre los datos más frescos como fuente de verdad.

        Args:
            df: DataFrame con columnas del schema screenermatic_bonos.
        """
        if df.empty:
            logger.warning("DataFrame vacío — no se persiste nada.")
            return

        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        df = df.copy()
        df["ultima_actualizacion"] = ahora

        # Aseguramos que todas las columnas del schema estén presentes
        schema_cols = [
            "simbolo", "descripcion", "emisor", "tipo", "pais", "mercado",
            "moneda", "precio", "rv_pct", "v_nominal", "v_nominal_actualizado",
            "interes_corrido", "valor_tecnico", "paridad_pct", "tir_pct",
            "macaulay_dur", "modified_dur", "effective_dur", "convexidad",
            "variacion_pct", "ultima_actualizacion",
        ]
        for col in schema_cols:
            if col not in df.columns:
                df[col] = None

        df_db = df[schema_cols]

        # Borrar y reinsertar (patrón TRUNCATE + INSERT, más seguro que UPSERT
        # masivo en DuckDB para DataFrames grandes)
        self._conn.execute("DELETE FROM screenermatic_bonos")
        self._conn.execute(
            "INSERT INTO screenermatic_bonos SELECT * FROM df_db"
        )
        logger.info(
            "Screenermatic persistido en DB: %d bonos | Timestamp: %s",
            len(df_db),
            ahora,
        )

    def _cargar_desde_db(self) -> pd.DataFrame:
        """Carga los datos frescos desde DuckDB."""
        df = self._conn.execute(
            "SELECT * FROM screenermatic_bonos ORDER BY simbolo"
        ).df()
        logger.info("Datos servidos desde caché DuckDB: %d bonos.", len(df))
        return df

    # ── Interfaz pública ────────────────────────────────────────────────────

    def get_bonos(self, forzar_actualizacion: bool = False) -> pd.DataFrame:
        """
        Retorna el universo completo de bonos de Screenermatic.

        Si los datos en DuckDB son recientes (< TTL), los devuelve directamente
        sin hacer requests HTTP. Si están vencidos o no existen, ejecuta el
        scraper completo, persiste el resultado y lo devuelve.

        Args:
            forzar_actualizacion: Si True, ignora el TTL y siempre descarga.

        Returns:
            DataFrame con 682+ bonos argentinos y sus métricas de tasas.

        Raises:
            ValueError: Si las cookies de sesión están ausentes en .env.
            RuntimeError: Si el scraping falló y no hay datos en caché.
        """
        if not forzar_actualizacion and self._cache_vigente():
            return self._cargar_desde_db()

        logger.info(
            "Caché vencido o forzado. Iniciando descarga desde Screenermatic..."
        )

        scraper = ScreenermaticScraper.desde_env()

        if not scraper.verificar_sesion():
            # Intentar servir datos vencidos antes de fallar
            df_viejo = self._cargar_desde_db()
            if not df_viejo.empty:
                logger.warning(
                    "Sesión Screenermatic expirada. Sirviendo datos del caché "
                    "(potencialmente desactualizados). "
                    "Renovar SCREENERMATIC_PHPSESSID en .env."
                )
                return df_viejo
            raise RuntimeError(
                "Sesión Screenermatic inválida y sin datos en caché. "
                "Iniciar sesión en https://www.screenermatic.com y actualizar "
                "SCREENERMATIC_PHPSESSID en el archivo .env."
            )

        df = scraper.get_bonos_completo()

        if df.empty:
            raise RuntimeError(
                "ScreenermaticScraper retornó DataFrame vacío. "
                "Verificar conectividad y estado de la sesión."
            )

        self._persistir(df)
        return df

    def estado(self) -> dict:
        """
        Retorna un resumen del estado actual del caché.

        Returns:
            dict con keys: n_bonos, ultima_actualizacion, vigente, ttl_horas.
        """
        resultado = self._conn.execute(
            """
            SELECT COUNT(*), MAX(ultima_actualizacion)
            FROM screenermatic_bonos
            """
        ).fetchone()

        n_bonos = resultado[0] or 0
        ultima = resultado[1]

        if isinstance(ultima, str) and ultima:
            ultima = datetime.fromisoformat(ultima)

        return {
            "n_bonos": n_bonos,
            "ultima_actualizacion": ultima,
            "vigente": self._cache_vigente(),
            "ttl_horas": self._ttl.total_seconds() / 3600,
            "db_path": self._db_path,
        }

    def cerrar(self) -> None:
        """Cierra la conexión DuckDB de forma segura."""
        self._conn.close()
        logger.debug("CacheScreenermatic: conexión DuckDB cerrada.")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cerrar()


# ─────────────────────────────────────────────────────────────────────────────
#  Función utilitaria de alto nivel para consumidores externos
# ─────────────────────────────────────────────────────────────────────────────

def obtener_bonos_frescos(
    db_path: str = _DB_PATH_DEFAULT,
    forzar: bool = False,
) -> pd.DataFrame:
    """
    Función de conveniencia para obtener el universo de bonos de Screenermatic.

    Envuelve CacheScreenermatic en un context manager, facilitando el uso
    desde optimizador_cartera.py y otros módulos consumidores.

    Uso:
        from src.data.scraping_screenermatic import obtener_bonos_frescos
        df_bonos = obtener_bonos_frescos()

    Args:
        db_path: Ruta al archivo DuckDB (default: data/docta_cache.duckdb).
        forzar:  Si True, ignora el caché y descarga siempre.

    Returns:
        DataFrame con todos los bonos y sus métricas (TIR, Duration, etc.).
    """
    with CacheScreenermatic(db_path=db_path) as cache:
        return cache.get_bonos(forzar_actualizacion=forzar)
