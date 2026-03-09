"""
Tests unitarios para CacheDoctaAPI.

Verifica el comportamiento del caché DuckDB:
  - Cache MISS: el primer llamado dispara la API real (mock).
  - Cache HIT:  el segundo llamado devuelve el dato local sin llamar la API.
  - Cache STALE: TTL vencido fuerza llamado real.
  - Auditoría: el log de llamados contabiliza correctamente.
"""

import os
import sys
import pytest
import tempfile
from datetime import datetime, timedelta
from unittest.mock import MagicMock

# Ajustar path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.data.cache_docta import CacheDoctaAPI, _CACHE_MISS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_docta():
    """Mock de DoctaCapitalAPI que devuelve valores controlados."""
    mock = MagicMock()
    mock.get_bond_yield.return_value = 0.0950
    return mock


@pytest.fixture
def db_temporal():
    """Crea una base DuckDB temporal en un directorio temporal."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, 'test_cache.duckdb')
        yield db_path


@pytest.fixture
def cache(mock_docta, db_temporal):
    """CacheDoctaAPI lista para tests, se cierra automáticamente."""
    c = CacheDoctaAPI(mock_docta, db_path=db_temporal, ttl_dias=7)
    yield c
    c.cerrar()


# ---------------------------------------------------------------------------
# Tests de comportamiento del caché
# ---------------------------------------------------------------------------

class TestCacheMissHit:
    """Verifica que la primera consulta llama a la API y las siguientes no."""

    def test_primera_consulta_llama_api(self, cache, mock_docta):
        """Cache MISS: debe llamar a la API exactamente una vez."""
        tir = cache.get_bond_yield("AL30")
        assert tir == 0.0950
        mock_docta.get_bond_yield.assert_called_once_with("AL30")

    def test_segunda_consulta_usa_cache(self, cache, mock_docta):
        """Cache HIT: segunda consulta no debe llamar a la API."""
        cache.get_bond_yield("AL30")   # Primera → API
        cache.get_bond_yield("AL30")   # Segunda → caché
        mock_docta.get_bond_yield.assert_called_once()  # Solo 1 llamado total

    def test_distintos_tickers_llaman_api_individual(self, cache, mock_docta):
        """Cada ticker nuevo dispara un llamado independiente."""
        mock_docta.get_bond_yield.side_effect = lambda t: {
            "AL30": 0.09, "AE38": 0.10, "AL35": 0.11
        }.get(t)

        cache.get_bond_yield("AL30")
        cache.get_bond_yield("AE38")
        cache.get_bond_yield("AL35")

        assert mock_docta.get_bond_yield.call_count == 3

        # Segunda ronda → debe usar caché, cero llamados adicionales
        cache.get_bond_yield("AL30")
        cache.get_bond_yield("AE38")
        cache.get_bond_yield("AL35")

        assert mock_docta.get_bond_yield.call_count == 3  # Sin cambios


class TestCacheStale:
    """Verifica que el TTL se respeta y los datos vencidos se refrescan."""

    def test_ttl_1_dia_vence_correctamente(self, mock_docta, db_temporal):
        """Con TTL de 1 día, datos de hace 2 días se consideran vencidos."""
        cache = CacheDoctaAPI(mock_docta, db_path=db_temporal, ttl_dias=1)

        cache.get_bond_yield("AL30")   # Inserta en caché

        # Alterar manualmente el timestamp para simular dato viejo (2 días atrás)
        hace_2_dias = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        cache._conn.execute(
            "UPDATE docta_yields SET ultima_actualizacion = ? WHERE ticker = 'AL30'",
            [hace_2_dias],
        )

        # Consulta con dato vencido → debe llamar a la API de nuevo
        cache.get_bond_yield("AL30")
        assert mock_docta.get_bond_yield.call_count == 2

        cache.cerrar()

    def test_dato_dentro_ttl_no_llama_api(self, mock_docta, db_temporal):
        """Con TTL de 7 días, dato de hace 3 días sigue vigente."""
        cache = CacheDoctaAPI(mock_docta, db_path=db_temporal, ttl_dias=7)

        cache.get_bond_yield("AL30")

        # Ajustar timestamp a 3 días atrás (dentro del TTL de 7)
        hace_3_dias = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")
        cache._conn.execute(
            "UPDATE docta_yields SET ultima_actualizacion = ? WHERE ticker = 'AL30'",
            [hace_3_dias],
        )

        cache.get_bond_yield("AL30")  # Debe usar caché
        assert mock_docta.get_bond_yield.call_count == 1

        cache.cerrar()


class TestAuditoria:
    """Verifica el sistema de log de auditoría de llamados."""

    def test_log_registra_llamados_reales(self, cache, mock_docta):
        """El log de auditoría debe registrar solo llamados reales a la API."""
        cache.get_bond_yield("AL30")
        cache.get_bond_yield("AL30")  # Desde caché, no se registra
        cache.get_bond_yield("AE38")

        df_log = cache.resumen_llamados_api()
        assert len(df_log) == 2  # Solo los 2 llamados reales

    def test_llamados_esta_semana_cuenta_correctamente(self, cache):
        """llamados_esta_semana() debe contar solo llamados de los últimos 7 días."""
        cache.get_bond_yield("AL30")
        cache.get_bond_yield("AE38")

        assert cache.llamados_esta_semana() == 2


class TestGestion:
    """Tests de herramientas de gestión del caché."""

    def test_estado_cache_devuelve_dataframe(self, cache):
        """estado_cache() debe retornar DataFrame con columna 'vigente'."""
        cache.get_bond_yield("AL30")
        df = cache.estado_cache()
        assert 'vigente' in df.columns
        assert df[df['ticker'] == 'AL30']['vigente'].iloc[0] == True  # noqa: E712

    def test_limpiar_cache_solo_vencidos(self, mock_docta, db_temporal):
        """limpiar_cache(solo_vencidos=True) no elimina datos vigentes."""
        cache = CacheDoctaAPI(mock_docta, db_path=db_temporal, ttl_dias=7)
        cache.get_bond_yield("AL30")

        # Simular un dato vencido
        cache._conn.execute(
            "INSERT INTO docta_yields VALUES ('VIEJO', 0.05, '2020-01-01', 'api')"
        )

        cache.limpiar_cache(solo_vencidos=True)

        # AL30 vigente no debe haberse eliminado
        resultado = cache._conn.execute(
            "SELECT COUNT(*) FROM docta_yields WHERE ticker = 'AL30'"
        ).fetchone()[0]
        assert resultado == 1

        # VIEJO vencido sí debe haberse eliminado
        resultado_viejo = cache._conn.execute(
            "SELECT COUNT(*) FROM docta_yields WHERE ticker = 'VIEJO'"
        ).fetchone()[0]
        assert resultado_viejo == 0

        cache.cerrar()

    def test_api_none_se_cachea(self, mock_docta, db_temporal):
        """
        Cuando la API devuelve None, se guarda None en el caché para evitar
        reintentos continuos dentro del TTL.
        """
        mock_docta.get_bond_yield.return_value = None
        cache = CacheDoctaAPI(mock_docta, db_path=db_temporal, ttl_dias=7)

        resultado1 = cache.get_bond_yield("NULO")
        resultado2 = cache.get_bond_yield("NULO")  # Debe usar caché (None guardado)

        assert resultado1 is None
        assert resultado2 is None
        mock_docta.get_bond_yield.assert_called_once()  # Solo 1 llamado real

        cache.cerrar()
