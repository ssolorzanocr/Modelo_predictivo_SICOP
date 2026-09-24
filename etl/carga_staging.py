"""Carga las tablas de staging (1:1 con cada CSV) al esquema `staging` de
sicop.duckdb.

La idempotencia es uniforme para las 8 tablas, dimensiones incluidas: se
borra el `periodo` que se está cargando y se vuelve a insertar completo. A
diferencia del diseño anterior, aquí NO se deduplica nada todavía -- staging
es una copia fiel de lo que trae cada CSV, mes a mes. La deduplicación (por
ejemplo, quedarse con el nombre más reciente de un proveedor) ocurre en
construir_modelo_final.py, no aquí.
"""
import argparse
import os

import duckdb

from csv_config import CSV_CONFIG

RUTA_DUCKDB = "data/sicop.duckdb"
RUTA_ESQUEMA = "etl/esquema_staging.sql"
CARPETA_STAGING = "data/staging"

TABLAS = sorted({cfg["tabla"] for cfg in CSV_CONFIG.values()})


def aplicar_esquema(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    with open(RUTA_ESQUEMA) as f:
        for sentencia in f.read().split(";"):
            sentencia = sentencia.strip()
            if sentencia:
                con.execute(sentencia)


def cargar_mes(con, mes: str) -> None:
    carpeta = os.path.join(CARPETA_STAGING, mes)
    for tabla in TABLAS:
        ruta = os.path.join(carpeta, f"{tabla}.parquet")
        if not os.path.exists(ruta):
            print(f"[{mes}] {tabla}: no hay parquet de staging para este mes, se omite")
            continue
        con.execute(f"DELETE FROM staging.{tabla} WHERE periodo = ?", [mes])
        con.execute(f"INSERT INTO staging.{tabla} SELECT * FROM read_parquet(?)", [ruta])
    print(f"[{mes}] staging cargado")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()

    con = duckdb.connect(RUTA_DUCKDB)
    aplicar_esquema(con)

    if args.backfill:
        meses = sorted(os.listdir(CARPETA_STAGING))
    else:
        from descarga import mes_actual_costa_rica
        meses = [mes_actual_costa_rica()]

    for mes in meses:
        cargar_mes(con, mes)
    con.close()
