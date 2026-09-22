"""Carga las tablas transformadas (Parquet) al archivo único sicop.duckdb.

Estrategia de idempotencia:
- Los hechos (fact_*) se reemplazan por completo para el `periodo` que se
  está cargando: primero se borra ese mes, luego se inserta de nuevo. Como
  el mes vigente es acumulativo, esto evita duplicados sin necesidad de un
  UPSERT fila por fila.
- Las dimensiones se insertan con INSERT OR IGNORE sobre su llave natural,
  para no duplicar proveedores/instituciones/productos ya conocidos.
- Al final se "reconcilia" fact_carteles: cualquier línea que ya aparezca en
  fact_adjudicaciones se elimina de fact_carteles, sin importar en qué mes
  se haya cargado originalmente esa línea de cartel. Es necesario porque un
  cartel publicado en un mes puede adjudicarse varios meses después.
"""
import argparse
import glob
import os

import duckdb

RUTA_DUCKDB = "data/sicop.duckdb"
RUTA_ESQUEMA = "etl/esquema.sql"
CARPETA_PROCESADO = "data/procesado"

TABLAS_DIMENSION = {"dim_proveedores", "dim_instituciones", "dim_productos"}


def aplicar_esquema(con) -> None:
    with open(RUTA_ESQUEMA) as f:
        for sentencia in f.read().split(";"):
            sentencia = sentencia.strip()
            if sentencia:
                con.execute(sentencia)


def cargar_dimension(con, tabla: str, ruta_parquet: str) -> None:
    con.execute(f"INSERT OR IGNORE INTO {tabla} SELECT * FROM read_parquet(?)", [ruta_parquet])


def reemplazar_hechos(con, tabla: str, ruta_parquet: str, mes: str) -> None:
    con.execute(f"DELETE FROM {tabla} WHERE periodo = ?", [mes])
    con.execute(f"INSERT INTO {tabla} SELECT * FROM read_parquet(?)", [ruta_parquet])


def cargar_mes(con, mes: str) -> None:
    carpeta = os.path.join(CARPETA_PROCESADO, mes)
    for ruta in glob.glob(os.path.join(carpeta, "*.parquet")):
        tabla = os.path.splitext(os.path.basename(ruta))[0]
        if tabla in TABLAS_DIMENSION:
            cargar_dimension(con, tabla, ruta)
        else:
            reemplazar_hechos(con, tabla, ruta, mes)
    print(f"[{mes}] cargado en {RUTA_DUCKDB}")


def reconciliar_carteles(con) -> None:
    """Quita de fact_carteles cualquier línea que ya tenga adjudicación,
    sin importar en qué mes se haya cargado originalmente."""
    antes = con.execute("SELECT count(*) FROM fact_carteles").fetchone()[0]
    con.execute("""
        DELETE FROM fact_carteles
        WHERE nro_sicop_nro_linea IN (SELECT nro_sicop_nro_linea FROM fact_adjudicaciones)
    """)
    despues = con.execute("SELECT count(*) FROM fact_carteles").fetchone()[0]
    print(f"fact_carteles reconciliado: {antes - despues} líneas ya adjudicadas removidas")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()

    con = duckdb.connect(RUTA_DUCKDB)
    aplicar_esquema(con)

    if args.backfill:
        meses = sorted(os.listdir(CARPETA_PROCESADO))
    else:
        from descarga import mes_actual_costa_rica
        meses = [mes_actual_costa_rica()]

    for mes in meses:
        cargar_mes(con, mes)

    reconciliar_carteles(con)
    con.close()
