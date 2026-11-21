"""
Capa de datos de la aplicación: conexión a DuckDB y reglas comunes.

La aplicación lee el archivo `sicop.duckdb` que publica el pipeline automatizado
del grupo (repositorio Modelo_predictivo_SICOP, release `datos_actuales_sicop`).
Todo el acceso a datos pasa por este módulo: si mañana la base vuelve a estar en
un servidor (PostgreSQL/Supabase), solo cambia este archivo.

"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import duckdb
import pandas as pd
import requests
import streamlit as st

# ------------------------------------------------------------------
# Lectura segura de secrets
# ------------------------------------------------------------------
# En versiones recientes de Streamlit, leer st.secrets sin un secrets.toml
# lanza StreamlitSecretNotFoundError; en versiones viejas esa clase no existe.
_ErrorSecretos = getattr(st.errors, "StreamlitSecretNotFoundError", FileNotFoundError)


def _secreto(seccion: str, clave: str, defecto=None):
    """Devuelve st.secrets[seccion][clave] o `defecto` si no existe."""
    try:
        return st.secrets[seccion][clave]
    except (KeyError, FileNotFoundError, _ErrorSecretos):
        return defecto


# ------------------------------------------------------------------
# Configuración del Release de GitHub
# ------------------------------------------------------------------
# Se leen de Settings → Secrets en Streamlit Cloud. El dueño (owner) es obligatorio.
GITHUB_OWNER: str | None = _secreto("github", "owner")
GITHUB_REPO: str = _secreto("github", "repo", "Modelo_predictivo_SICOP")
RELEASE_TAG: str = _secreto("github", "tag", "datos_actuales_sicop")  # o "latest"
ASSET_NAME: str = _secreto("github", "asset", "sicop.duckdb")
GITHUB_TOKEN: str | None = _secreto("github", "token")  # solo para repos privados

API_BASE = "https://api.github.com"
# Copia descargada del Release en el disco temporal del servidor de Streamlit Cloud.
RUTA_DESCARGA = Path(tempfile.gettempdir()) / ASSET_NAME

# Carpeta con archivos .parquet que viajan dentro del repositorio de la app.
CARPETA_DATOS_APP = Path(__file__).parent / "datos"

# Sistema visual común del grupo (skill dataviz). Slots categóricos en orden fijo.
SERIE = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
GRIS = "#c3c2b7"


class ErrorBaseSICOP(Exception):
    """Cualquier problema para ubicar u obtener la base sicop.duckdb."""


# ------------------------------------------------------------------
# Descarga desde el Release de GitHub
# ------------------------------------------------------------------
def _origen_descarga() -> tuple[str, dict[str, str]]:
    """URL y encabezados para descargar el asset del Release.

    - Sin token (repo público): URL directa de descarga. No consume la cuota de
      la API (60 consultas/hora por IP, compartida en Streamlit Cloud).
    - Con token (repo privado): se busca el asset por la API y se descarga con
      'Accept: application/octet-stream'.
    """
    if not GITHUB_OWNER:
        raise ErrorBaseSICOP(
            "Falta configurar el dueño del repositorio: agrega `owner` en la "
            "sección `[github]` de los secrets."
        )

    ultimo = RELEASE_TAG.lower() == "latest"

    if not GITHUB_TOKEN:
        repo = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
        url = (f"{repo}/latest/download/{ASSET_NAME}" if ultimo
               else f"{repo}/download/{RELEASE_TAG}/{ASSET_NAME}")
        return url, {}

    base = {"Authorization": f"Bearer {GITHUB_TOKEN}",
            "X-GitHub-Api-Version": "2022-11-28"}
    api_repo = f"{API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases"
    url_release = f"{api_repo}/latest" if ultimo else f"{api_repo}/tags/{RELEASE_TAG}"
    resp = requests.get(url_release, headers={**base, "Accept": "application/vnd.github+json"},
                        timeout=30)
    if resp.status_code == 404:
        raise ErrorBaseSICOP(
            f"No existe el release `{RELEASE_TAG}` en `{GITHUB_OWNER}/{GITHUB_REPO}` "
            "o el token no tiene acceso al repositorio."
        )
    resp.raise_for_status()
    for asset in resp.json().get("assets", []):
        if asset["name"] == ASSET_NAME:
            return asset["url"], {**base, "Accept": "application/octet-stream"}
    raise ErrorBaseSICOP(f"El release `{RELEASE_TAG}` no contiene el archivo `{ASSET_NAME}`.")


def descargar_release(destino: Path = RUTA_DESCARGA) -> Path:
    """Descarga sicop.duckdb del Release (por bloques) y la deja en `destino`.

    Se escribe primero en un archivo .part y se renombra al terminar: si la
    descarga se corta, no queda una base a medias que DuckDB no pueda abrir."""
    url, headers = _origen_descarga()
    parcial = destino.with_suffix(destino.suffix + ".part")
    try:
        with requests.get(url, headers=headers, stream=True, timeout=(30, 300)) as r:
            if r.status_code == 404:
                raise ErrorBaseSICOP(
                    f"GitHub respondió 404 para `{url}`. Revisa owner/repo/tag/asset; "
                    "si el repositorio es privado, agrega un `token` en `[github]`."
                )
            r.raise_for_status()
            esperado = int(r.headers.get("Content-Length", 0))
            with open(parcial, "wb") as f:
                for bloque in r.iter_content(chunk_size=8 * 1024 * 1024):  # 8 MB
                    f.write(bloque)
        if esperado and parcial.stat().st_size != esperado:
            raise ErrorBaseSICOP("La descarga quedó incompleta; vuelve a intentarlo.")
        os.replace(parcial, destino)
    except requests.RequestException as error:
        raise ErrorBaseSICOP(f"No se pudo descargar la base desde GitHub: {error}") from error
    finally:
        parcial.unlink(missing_ok=True)
    return destino


def ruta_duckdb() -> Path:
    """Ruta de la copia descargada del Release. Solo se descarga si todavía
    no está en el disco temporal del servidor."""
    if RUTA_DESCARGA.exists():
        return RUTA_DESCARGA
    return descargar_release(RUTA_DESCARGA)


# ------------------------------------------------------------------
# Conexión
# ------------------------------------------------------------------
@st.cache_resource(show_spinner="Preparando la base de datos SICOP…")
def conexion() -> duckdb.DuckDBPyConnection:
    """Una conexión de solo lectura para toda la aplicación. Cada consulta usa
    su propio cursor (DuckDB no comparte un cursor entre hilos de Streamlit).
    Si falla, Streamlit no guarda el error en caché y reintenta en el próximo rerun."""
    return duckdb.connect(str(ruta_duckdb()), read_only=True)


def consultar(sql: str, params: list | None = None) -> pd.DataFrame:
    # `with` cierra el cursor al terminar; .df() ya materializó el resultado.
    with conexion().cursor() as cur:
        return cur.execute(sql, params or []).df()


def recargar_base() -> None:
    """Fuerza una nueva descarga (útil cuando se actualiza el Release)."""
    try:
        conexion().close()
    except Exception:  # noqa: BLE001 – si no había conexión, no importa
        pass
    conexion.clear()
    st.cache_data.clear()
    RUTA_DESCARGA.unlink(missing_ok=True)


def a_crc(valor: str, moneda: str, tipo_cambio: str) -> str:
    """Expresión SQL que lleva un precio o monto a colones.

    Regla verificada contra los datos (30/09/2026): `tipo_cambio_crc` contiene el
    tipo de cambio del DÓLAR (~500) en todas las filas, incluso en CRC y EUR.
    Por eso solo se convierte USD; otras monedas (≈0,7 % de las líneas) quedan
    NULL: mejor sin dato que un dato mal convertido."""
    return (f"CASE WHEN {moneda} = 'CRC' THEN {valor} "
            f"WHEN {moneda} = 'USD' AND {tipo_cambio} > 0 THEN {valor} * {tipo_cambio} END")


# ------------------------------------------------------------------
# Formatos (convención costarricense: punto de miles, coma decimal)
# ------------------------------------------------------------------

def colones(valor) -> str:
    if valor is None or pd.isna(valor):
        return "s/d"
    return "₡ {:,.0f}".format(valor).replace(",", ".")


def colones_compacto(valor) -> str:
    """Para tarjetas de KPI, donde el monto completo no cabe: '₡ 2.203,5 M'."""
    if valor is None or pd.isna(valor):
        return "s/d"
    if abs(valor) >= 1e9:
        return f"₡ {valor / 1e9:,.1f} mil M".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"₡ {valor / 1e6:,.1f} M".replace(",", "X").replace(".", ",").replace("X", ".")


def entero(valor) -> str:
    if valor is None or pd.isna(valor):
        return "s/d"
    return "{:,.0f}".format(valor).replace(",", ".")


def porcentaje(valor, decimales: int = 0) -> str:
    if valor is None or pd.isna(valor):
        return "s/d"
    return f"{valor * 100:.{decimales}f} %".replace(".", ",")


def variacion(razon) -> str:
    """0,88 -> '-12,0 %'; 1,05 -> '+5,0 %'."""
    if razon is None or pd.isna(razon):
        return "s/d"
    return f"{(float(razon) - 1) * 100:+.1f} %".replace(".", ",")


def dias(valor) -> str:
    if valor is None or pd.isna(valor):
        return "s/d"
    return f"{float(valor):.0f} días"


# ------------------------------------------------------------------
# Consultas compartidas
# ------------------------------------------------------------------

@st.cache_data(ttl=3600)
def fecha_maxima():
    return consultar("SELECT MAX(fecha_publicacion) FROM final.fact_lineas_carteles").iat[0, 0]


@st.cache_data(ttl=3600)
def segmentos() -> pd.DataFrame:
    """Segmentos UNSPSC ordenados por volumen de líneas publicadas."""
    return consultar("""
        SELECT p.nombre_segmento, COUNT(*) AS lineas
        FROM final.fact_lineas_carteles c
        JOIN final.dim_productos p ON p.cod_producto = TRY_CAST(c.cod_producto AS BIGINT)
        WHERE p.nombre_segmento IS NOT NULL
        GROUP BY 1 ORDER BY 2 DESC
    """)


def leer_parquet(nombre: str) -> pd.DataFrame | None:
    ruta = CARPETA_DATOS_APP / nombre
    return pd.read_parquet(ruta) if ruta.exists() else None


def verificar_conexion() -> None:
    """Detiene la página con un mensaje claro si no se puede abrir la base."""
    try:
        conexion()
    except ErrorBaseSICOP as error:
        st.error(
            f"No fue posible obtener `sicop.duckdb`.\n\n**Detalle:** {error}\n\n"
            "Revisa la sección `[github]` en Settings → Secrets de Streamlit Cloud "
            "(owner, repo, tag, asset y, si el repositorio es privado, token)."
        )
        st.stop()
    except duckdb.Error as error:
        st.error(
            f"La base se encontró pero DuckDB no pudo abrirla.\n\n**Detalle:** {error}\n\n"
            "Causa frecuente: la versión de `duckdb` de requirements.txt no coincide "
            "con la que creó el archivo."
        )
        st.stop()
