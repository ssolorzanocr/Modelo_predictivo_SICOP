"""Transforma los CSV crudos del Observatorio a las 6 tablas del modelo de datos:
fact_ofertas, fact_adjudicaciones, fact_carteles, dim_proveedores,
dim_instituciones y dim_productos.

IMPORTANTE: los nombres de columna de los CSV originales del Observatorio no
están confirmados en este esqueleto -- todo lo marcado con TODO depende de
que abras un ZIP real y verifiques los nombres exactos y el filtro que
distingue cartel/oferta/adjudicación. La lógica de selección de columnas,
cálculo de montos, orden de columnas y deduplicación sí es funcional.
"""
import argparse
import glob
import os

import pandas as pd

CARPETA_RAW = "data/raw"
CARPETA_PROCESADO = "data/procesado"


def listar_csv(mes: str) -> list[str]:
    return glob.glob(os.path.join(CARPETA_RAW, mes, "*.csv"))


def cargar_mes(mes: str) -> pd.DataFrame:
    archivos = listar_csv(mes)
    print(f"Archivos para el mes {mes}: {archivos}", flush=True)
    if not archivos:
        raise FileNotFoundError(
            f"No hay CSV para el mes {mes} en {CARPETA_RAW}/{mes}"
        )
    partes = []
    for a in archivos:
        print(f"Procesando archivo: {a}", flush=True)
        parte = pd.read_csv(
            a,
            encoding="utf-8-sig",
            sep=";",
            quotechar='"',
            engine="python",
            on_bad_lines="warn",
        )
        partes.append(parte)
    return pd.concat(partes, ignore_index=True)


# --- Dimensiones -------------------------------------------------------------

def construir_dim_proveedores(df: pd.DataFrame) -> pd.DataFrame:
    # TODO: confirmar nombres reales de columna de origen
    return df[["cedula_proveedor", "nombre_proveedor", "tipo_proveedor"]].drop_duplicates(
        subset="cedula_proveedor"
    )


def construir_dim_instituciones(df: pd.DataFrame) -> pd.DataFrame:
    return df[["cedula_institucion", "nombre_institucion"]].drop_duplicates(
        subset="cedula_institucion"
    )


def construir_dim_productos(df: pd.DataFrame) -> pd.DataFrame:
    return df[["cod_producto", "descripcion_producto", "segmento", "nombre_segmento"]].drop_duplicates(
        subset="cod_producto"
    )


# --- Hechos --------------------------------------------------------------------

def construir_fact_ofertas(df: pd.DataFrame, mes: str) -> pd.DataFrame:
    # TODO: ajustar el filtro que identifica las filas de tipo "oferta" en el CSV real
    ofertas = df[df["tipo_registro"] == "OFERTA"].copy()
    out = ofertas[[
        "nro_oferta_nro_linea", "cod_producto", "nro_sicop", "cedula_proveedor",
        "cantidad_ofertada", "precio_unitario_ofertado", "tipo_moneda",
        "tipo_cambio_crc", "tipo_oferta", "fecha_oferta",
    ]].copy()
    out["monto_linea"] = out["cantidad_ofertada"] * out["precio_unitario_ofertado"]
    out["fecha_oferta"] = pd.to_datetime(out["fecha_oferta"], errors="coerce").dt.date
    out["periodo"] = mes
    # Reordenar para que coincida exactamente con esquema.sql (el INSERT es posicional)
    return out[[
        "nro_oferta_nro_linea", "cod_producto", "nro_sicop", "cedula_proveedor",
        "cantidad_ofertada", "precio_unitario_ofertado", "tipo_moneda",
        "tipo_cambio_crc", "monto_linea", "tipo_oferta", "fecha_oferta", "periodo",
    ]]


def construir_fact_adjudicaciones(df: pd.DataFrame, mes: str) -> pd.DataFrame:
    # TODO: ajustar el filtro que identifica las filas de tipo "adjudicación" en el CSV real
    adjudicaciones = df[df["tipo_registro"] == "ADJUDICACION"].copy()
    out = adjudicaciones[[
        "nro_sicop_nro_linea", "nro_linea", "nro_oferta", "descr_procedimiento",
        "monto_adjudicado_linea", "cod_producto", "nro_sicop", "cedula_proveedor",
        "cedula_institucion", "moneda_adjudicada", "tipo_cambio_crc", "fecha_adjudicacion",
    ]].copy()
    out["fecha_adjudicacion"] = pd.to_datetime(out["fecha_adjudicacion"], errors="coerce").dt.date
    out["periodo"] = mes
    return out


def construir_fact_carteles(df: pd.DataFrame, mes: str) -> pd.DataFrame:
    # TODO: ajustar el filtro que identifica las filas de tipo "cartel" en el CSV real.
    # Esta tabla debe tener solo líneas NO adjudicadas; la reconciliación final
    # (en carga_duckdb.py) saca las que sí se adjudicaron, aunque hayan llegado
    # aquí en un mes distinto al de su adjudicación.
    carteles = df[df["tipo_registro"] == "CARTEL"].copy()
    out = carteles[[
        "nro_sicop_nro_linea", "nro_linea", "nro_oferta", "nombre_cartel",
        "precio_unitario_linea", "cantidad", "monto_estimado_cartel", "cod_producto",
        "nro_sicop", "tipo_moneda", "tipo_cambio_crc", "cedula_institucion",
        "clasificacion_cartel", "tipo_procedimiento", "fecha_publicacion", "fecha_apertura",
    ]].copy()
    out["monto_total_linea"] = out["precio_unitario_linea"] * out["cantidad"]
    out["fecha_publicacion"] = pd.to_datetime(out["fecha_publicacion"], errors="coerce").dt.date
    out["fecha_apertura"] = pd.to_datetime(out["fecha_apertura"], errors="coerce").dt.date
    out["periodo"] = mes
    return out[[
        "nro_sicop_nro_linea", "nro_linea", "nro_oferta", "nombre_cartel",
        "precio_unitario_linea", "cantidad", "monto_total_linea", "monto_estimado_cartel",
        "cod_producto", "nro_sicop", "tipo_moneda", "tipo_cambio_crc", "cedula_institucion",
        "clasificacion_cartel", "tipo_procedimiento", "fecha_publicacion", "fecha_apertura", "periodo",
    ]]


def procesar_mes(mes: str) -> None:
    df = cargar_mes(mes)
    tablas = {
        "dim_proveedores": construir_dim_proveedores(df),
        "dim_instituciones": construir_dim_instituciones(df),
        "dim_productos": construir_dim_productos(df),
        "fact_ofertas": construir_fact_ofertas(df, mes),
        "fact_adjudicaciones": construir_fact_adjudicaciones(df, mes),
        "fact_carteles": construir_fact_carteles(df, mes),
    }
    carpeta_salida = os.path.join(CARPETA_PROCESADO, mes)
    os.makedirs(carpeta_salida, exist_ok=True)
    for nombre, tabla in tablas.items():
        ruta = os.path.join(carpeta_salida, f"{nombre}.parquet")
        tabla.to_parquet(ruta, index=False)
        print(f"[{mes}] {nombre}: {len(tabla)} filas -> {ruta}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    args = parser.parse_args()

    if args.backfill:
        meses = sorted(os.listdir(CARPETA_RAW))
    else:
        from descarga import mes_actual_costa_rica
        meses = [mes_actual_costa_rica()]

    for mes in meses:
        procesar_mes(mes)
