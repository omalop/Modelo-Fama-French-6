"""
=============================================================================
CACHÉ DUCKDB PARA API DOCTA CAPITAL — TTL Semanal por Instrumento
=============================================================================
Envuelve DoctaCapitalAPI con una capa de persistencia DuckDB de modo que:
  - Solo se realizan llamados reales a la API si el dato del instrumento
    tiene más de 7 días de antigüedad (o no existe en la base).
  - Toda consulta fuera de ese período usa directamente la base local,
    sin generar tráfico contra la API.
  - El caché admite cualquier tipo de dato (yield de bono, catálogo, etc.)

Tablas en DuckDB:
  docta_yields  →  TIR (yield) por ticker con timestamp de última actualización
  docta_instruments → catálogo maestro de bonos (TTL: 7 días también)
  docta_api_log → registro de llamados reales efectuados (auditoría de cuota)

Referencia técnica:
  Patrón Stale-While-Revalidate / Cache-Aside
  Fowler, M. (2002). Patterns of Enterprise Application Architecture. Cap. 18.

Supuestos:
  - La API de Docta Capital retorna datos diarios (el yield del día).
  - Se acepta usar el yield del cierre más reciente disponible (no intradiario
    de sesión activa) cuando el dato cacheado es del mismo día o semana.
  - El TTL de 7 días es conservador para bonos soberanos/corporativos cuya
    TIR no varía drásticamente en esa ventana temporal.
=============================================================================
"""

import os
import sys
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import duckdb
import pandas as pd

# ---------------------------------------------------------------------------
# Logger
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ruta por defecto de la base de caché
# ---------------------------------------------------------------------------
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
_DB_CACHE_DEFAULT = os.path.join(ROOT_DIR, 'data', 'docta_cache.duckdb')

# TTL en días para datos de yield por instrumento
_TTL_DIAS = 7

# ---------------------------------------------------------------------------
# Sentinel: objeto único para indicar "dato no en caché o expirado"
# Necesario para distinguir cache miss de None válido (API sin dato hoy)
# ---------------------------------------------------------------------------
_CACHE_MISS = object()


class CacheDoctaAPI:
    """
    Proxy con caché DuckDB para DoctaCapitalAPI.

    Patrón Cache-Aside:
      1. Consultar base local.
      2. Si el dato existe y está dentro del TTL  → devolver desde caché.
      3. Si el dato es inexistente o está vencido → llamar a la API real,
         persistir el resultado y devolverlo.

    Esto garantiza como máximo 1 llamado real por instrumento por semana,
    sin afectar la lógica de negocio del código consumidor.

    Args:
        docta_cliente: Instancia ya autenticada de DoctaCapitalAPI.
        db_path:       Ruta al archivo .duckdb de caché (se crea si no existe).
        ttl_dias:      Días de validez del caché (default: 7).
    """

    def __init__(
        self,
        docta_cliente,
        db_path: str = _DB_CACHE_DEFAULT,
        ttl_dias: int = _TTL_DIAS,
    ):
        self._api = docta_cliente
        self._ttl = timedelta(days=ttl_dias)
        self._db_path = db_path
        self._asegurar_directorio()
        self._conn = duckdb.connect(self._db_path)
        self._inicializar_schema()
        logger.info(
            "CacheDoctaAPI inicializado | DB: %s | TTL: %d días",
            self._db_path,
            ttl_dias,
        )

    # ------------------------------------------------------------------
    # Infraestructura interna
    # ------------------------------------------------------------------

    def _asegurar_directorio(self) -> None:
        """Crea el directorio data/ si no existe."""
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)

    def _inicializar_schema(self) -> None:
        """Crea las tablas de caché si no existen (idempotente)."""

        # Tabla principal: yields por ticker
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS docta_yields (
                ticker          VARCHAR PRIMARY KEY,
                tir             DOUBLE,
                ultima_actualizacion TIMESTAMP,
                fuente          VARCHAR   -- 'api' o 'cache'
            )
        """)

        # Tabla: catálogo de instrumentos
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS docta_instruments (
                ticker          VARCHAR PRIMARY KEY,
                descripcion     VARCHAR,
                ultima_actualizacion TIMESTAMP
            )
        """)

        # Tabla: log de auditoría de llamados reales a la API
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS docta_api_log (
                id              INTEGER,
                timestamp_utc   TIMESTAMP,
                endpoint        VARCHAR,
                ticker          VARCHAR,
                exitoso         BOOLEAN,
                tir_obtenida    DOUBLE
            )
        """)

        # Secuencia para el ID del log (DuckDB no tiene AUTOINCREMENT nativo
        # en versiones antiguas, usamos MAX+1 al insertar)
        logger.debug("Schema DuckDB de caché Docta inicializado correctamente.")

    def _dato_vigente(self, ticker: str):
        """
        Verifica si existe un dato cacheado vigente para el ticker.

        Returns:
            - float con la TIR si el caché es válido y no ha expirado.
            - None si el ticker está en caché con valor None (API no devuelve dato)
              pero el TTL aún no venció → se devuelve None y NO se llama a la API.
            - _CACHE_MISS (sentinel) si no hay entrada o el TTL venció.
        """
        resultado = self._conn.execute(
            "SELECT tir, ultima_actualizacion FROM docta_yields WHERE ticker = ?",
            [ticker],
        ).fetchone()

        if resultado is None:
            logger.debug("Caché MISS (no existe): %s", ticker)
            return _CACHE_MISS

        tir, ultima_act = resultado
        ahora = datetime.now()

        # Normalizar: DuckDB puede devolver datetime o string
        if isinstance(ultima_act, str):
            ultima_act = datetime.fromisoformat(ultima_act)

        if (ahora - ultima_act) <= self._ttl:
            logger.info(
                "Caché HIT: %s | TIR=%s | Actualizado: %s",
                ticker,
                f"{tir:.4f}" if tir is not None else "None",
                ultima_act.strftime("%Y-%m-%d %H:%M"),
            )
            return tir  # Puede ser None (API sin dato, pero vigente en caché)

        logger.info(
            "Caché STALE (expirado): %s | Última actualización: %s",
            ticker,
            ultima_act.strftime("%Y-%m-%d %H:%M"),
        )
        return _CACHE_MISS

    def _persistir_yield(self, ticker: str, tir: Optional[float]) -> None:
        """
        Guarda o actualiza el yield de un ticker en la base de caché.

        Args:
            ticker: Símbolo del instrumento.
            tir:    TIR obtenida (None si la API no devolvió dato).
        """
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self._conn.execute(
            """
            INSERT INTO docta_yields (ticker, tir, ultima_actualizacion, fuente)
            VALUES (?, ?, ?, 'api')
            ON CONFLICT (ticker) DO UPDATE SET
                tir                  = EXCLUDED.tir,
                ultima_actualizacion = EXCLUDED.ultima_actualizacion,
                fuente               = 'api'
            """,
            [ticker, tir, ahora],
        )
        logger.debug("Yield persistido en caché: %s = %s", ticker, tir)

    def _registrar_llamado(
        self, endpoint: str, ticker: str, exitoso: bool, tir: Optional[float]
    ) -> None:
        """Registra en el log de auditoría cada llamado real efectuado a la API."""
        ahora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        try:
            max_id = self._conn.execute(
                "SELECT COALESCE(MAX(id), 0) + 1 FROM docta_api_log"
            ).fetchone()[0]
            self._conn.execute(
                """
                INSERT INTO docta_api_log
                    (id, timestamp_utc, endpoint, ticker, exitoso, tir_obtenida)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [max_id, ahora, endpoint, ticker, exitoso, tir],
            )
        except Exception as e:
            logger.warning("No se pudo registrar en log de auditoría: %s", e)

    # ------------------------------------------------------------------
    # Interfaz pública — misma firma que DoctaCapitalAPI para drop-in
    # ------------------------------------------------------------------

    def get_bond_yield(self, ticker: str) -> Optional[float]:
        """
        Obtiene la TIR de un bono, preferiendo el caché local.

        Lógica:
          1. Consultar caché DuckDB.
          2. Si hay entrada vigente (inc. None) → devolver sin llamar a la API.
          3. Si está vencido o no existe → llamar a DoctaCapitalAPI.get_bond_yield(),
             persistir el resultado (sea float o None) y devolverlo.

        Args:
            ticker: Símbolo del bono (ej: 'AL30', 'AE38').

        Returns:
            float con la TIR o None si no está disponible.
        """
        # Paso 1: verificar caché
        dato = self._dato_vigente(ticker)
        if dato is not _CACHE_MISS:
            return dato  # Puede ser float o None, ambos son datos válidos cacheados

        # Paso 2: llamado real a la API
        logger.info("Llamando API Docta para: %s (caché vencido/inexistente)", ticker)
        try:
            tir_real = self._api.get_bond_yield(ticker)
            exitoso = True
        except Exception as e:
            logger.error("Error en llamado API para %s: %s", ticker, e)
            tir_real = None
            exitoso = False

        # Paso 3: persistir (incluso None, para no reintentar en la misma semana)
        self._persistir_yield(ticker, tir_real)
        self._registrar_llamado(
            endpoint="bonds/yields/{ticker}/intraday",
            ticker=ticker,
            exitoso=exitoso,
            tir=tir_real,
        )

        if tir_real is not None:
            logger.info(
                "API Docta respondió exitosamente: %s | TIR=%.4f", ticker, tir_real
            )
        else:
            logger.warning(
                "API Docta no devolvio dato para %s. "
                "Se almacena None para evitar reintentos esta semana.",
                ticker,
            )

        return tir_real

    def get_instruments(self) -> pd.DataFrame:
        """
        Descarga el catálogo de instrumentos, con caché de 7 días.

        Returns:
            DataFrame con el catálogo de bonos disponibles.
        """
        # Verificar si el catálogo está vigente en caché
        resultado = self._conn.execute(
            """
            SELECT MIN(ultima_actualizacion) as mas_antigua
            FROM docta_instruments
            """
        ).fetchone()

        mas_antigua = resultado[0] if resultado else None
        catalogo_vigente = False

        if mas_antigua is not None:
            if isinstance(mas_antigua, str):
                mas_antigua = datetime.fromisoformat(mas_antigua)
            if (datetime.now() - mas_antigua) <= self._ttl:
                catalogo_vigente = True

        if catalogo_vigente:
            logger.info("Caché HIT: catálogo de instrumentos vigente.")
            df = self._conn.execute(
                "SELECT ticker, descripcion FROM docta_instruments"
            ).df()
            return df

        # Llamado real
        logger.info("Descargando catálogo de instrumentos desde API Docta...")
        df_api = self._api.get_instruments()

        if not df_api.empty:
            # Limpiar tabla y repoblar
            self._conn.execute("DELETE FROM docta_instruments")
            ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for _, fila in df_api.iterrows():
                ticker = str(fila.get('ticker', fila.get('symbol', '')))
                desc = str(fila.get('description', fila.get('name', '')))
                self._conn.execute(
                    """
                    INSERT INTO docta_instruments (ticker, descripcion, ultima_actualizacion)
                    VALUES (?, ?, ?)
                    ON CONFLICT (ticker) DO UPDATE SET
                        descripcion = EXCLUDED.descripcion,
                        ultima_actualizacion = EXCLUDED.ultima_actualizacion
                    """,
                    [ticker, desc, ahora],
                )
            logger.info(
                "Catálogo persistido en caché: %d instrumentos.", len(df_api)
            )
            self._registrar_llamado("bonds/instruments", "*catalogo*", True, None)
        else:
            logger.warning("API Docta devolvió catálogo vacío.")

        return df_api

    # ------------------------------------------------------------------
    # Herramientas de gestión del caché
    # ------------------------------------------------------------------

    def estado_cache(self) -> pd.DataFrame:
        """
        Devuelve un resumen del estado actual del caché.

        Returns:
            DataFrame con ticker, TIR almacenada, fecha de actualización
            y si está vigente o vencida.
        """
        df = self._conn.execute(
            "SELECT ticker, tir, ultima_actualizacion, fuente FROM docta_yields ORDER BY ticker"
        ).df()

        if df.empty:
            logger.info("El caché está vacío.")
            return df

        ahora = datetime.now()
        df['ultima_actualizacion'] = pd.to_datetime(df['ultima_actualizacion'])
        df['dias_desde_actualizacion'] = (
            ahora - df['ultima_actualizacion']
        ).dt.total_seconds() / 86400
        df['vigente'] = df['dias_desde_actualizacion'] <= self._ttl.days
        return df

    def resumen_llamados_api(self) -> pd.DataFrame:
        """
        Retorna el log de auditoría de llamados reales efectuados a la API.
        Útil para monitorear el consumo de cuota.

        Returns:
            DataFrame con historial de llamados reales a Docta Capital API.
        """
        df = self._conn.execute(
            """
            SELECT
                timestamp_utc,
                endpoint,
                ticker,
                exitoso,
                tir_obtenida
            FROM docta_api_log
            ORDER BY timestamp_utc DESC
            """
        ).df()
        return df

    def llamados_esta_semana(self) -> int:
        """
        Cuenta cuántos llamados reales se hicieron a la API en los últimos 7 días.

        Returns:
            int con el número de llamados reales esta semana.
        """
        hace_7_dias = (datetime.now(timezone.utc) - timedelta(days=7)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        resultado = self._conn.execute(
            "SELECT COUNT(*) FROM docta_api_log WHERE timestamp_utc >= CAST(? AS TIMESTAMP)",
            [hace_7_dias],
        ).fetchone()
        return resultado[0] if resultado else 0

    def limpiar_cache(self, solo_vencidos: bool = True) -> None:
        """
        Limpia entradas del caché.

        Args:
            solo_vencidos: Si True, elimina solo los datos con TTL vencido.
                           Si False, limpia toda la tabla docta_yields.
        """
        if solo_vencidos:
            limite = (datetime.now() - self._ttl).strftime("%Y-%m-%d %H:%M:%S")
            eliminados = self._conn.execute(
                "SELECT COUNT(*) FROM docta_yields WHERE ultima_actualizacion < ?",
                [limite],
            ).fetchone()[0]
            self._conn.execute(
                "DELETE FROM docta_yields WHERE ultima_actualizacion < ?",
                [limite],
            )
            logger.info(
                "Caché limpiado: %d entradas vencidas eliminadas.", eliminados
            )
        else:
            self._conn.execute("DELETE FROM docta_yields")
            logger.warning("Caché Docta yields limpiado completamente.")

    def forzar_actualizacion(self, tickers: list) -> None:
        """
        Fuerza la actualización de los tickers indicados ignorando el TTL.
        Útil para actualizaciones manuales urgentes de instrumentos clave.

        Args:
            tickers: Lista de tickers a forzar actualización.
        """
        logger.info(
            "Forzando actualización de %d tickers: %s", len(tickers), tickers
        )
        # Borrar del caché para forzar llamado real en próxima consulta
        tickers_str = "'" + "','".join(tickers) + "'"
        self._conn.execute(
            f"DELETE FROM docta_yields WHERE ticker IN ({tickers_str})"
        )
        # Ahora disparar los llamados reales
        resultados = {}
        for ticker in tickers:
            tir = self.get_bond_yield(ticker)
            resultados[ticker] = tir
            logger.info("Forzado OK: %s = %s", ticker, tir)
        return resultados

    def cerrar(self) -> None:
        """Cierra la conexión DuckDB de forma segura."""
        self._conn.close()
        logger.debug("Conexión DuckDB de caché Docta cerrada.")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.cerrar()
