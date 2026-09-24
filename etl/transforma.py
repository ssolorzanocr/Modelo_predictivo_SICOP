"""Convierte cada CSV crudo del Observatorio en su tabla de staging (1:1),
según CSV_CONFIG. Esta capa NO tipa ni calcula nada -- solo selecciona y
renombra columnas, dejando todo como texto (VARCHAR). El tipado y los
cálculos pasan a construir_modelo_final.py, que sí conoce los tipos reales.

Dos problemas que resuelve este diseño:
1. Problema 1: Los headers de un mismo CSV cambian de nombre/mayúsculas entre años
   (p. ej. "TAMAÑO_PROVEEDOR" un año, "Tamano_Proveedor" otro). Se resuelve
   comparando nombres normalizados (minúsculas, sin tildes) en vez de una
   igualdad exacta.
2. Problema 2: El ZIP trae más archivos de los que se necesitan para contruir el modelo.
   Solo se procesan los que están listados en CSV_CONFIG; el resto se reporta y se ignora.
   Esto reduce la cantidad de datos que se cargan en staging y evita errores por CSV inesperados.

Si una columna configurada no aparece en el CSV de un mes en particular
(cambió de nombre o se dejó de publicar), la columna se llena con NULL y se
imprime un aviso -- no se detiene la carga de todo el mes.
"""
import argparse
import glob
import os
import unicodedata

import pandas as pd

from csv_config import CSV_CONFIG #archivo con detalles de los csv que se van a procesar.

CARPETA_RAW = "data/raw"
CARPETA_STAGING = "data/staging"


def normalizar(texto: str) -> str:
    """minúsculas, sin tildes/ñ, sin espacios en los extremos -- para poder
    comparar nombres de columna o de archivo que cambian de formato entre años."""
    texto = texto.strip().lower()
    texto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in texto if not unicodedata.combining(c))


CONFIG_POR_ARCHIVO = {normalizar(nombre): (nombre, cfg) for nombre, cfg in CSV_CONFIG.items()}


def procesar_csv(ruta_csv: str, config: dict, mes: str) -> pd.DataFrame:
    # dtype=str: todo se lee como texto para no pelear con formatos de
    # fecha/número que cambian entre años -- eso se resuelve en la capa final.
    df = pd.read_csv(
            ruta_csv,
            encoding="utf-8-sig",
            sep=";",
            quotechar='"',
            engine="python",
            on_bad_lines="warn",
        )
    columnas_disponibles = {normalizar(c): c for c in df.columns}

    salida = {}
    for columna_esperada in config["columnas"]:
        clave = normalizar(columna_esperada)
        columna_real = columnas_disponibles.get(clave)
        if columna_real is not None:
            salida[clave] = df[columna_real].values
        else:
            print(f"  aviso [{mes}] falta la columna '{columna_esperada}' en {os.path.basename(ruta_csv)}")
            salida[clave] = pd.NA

    resultado = pd.DataFrame(salida)
    resultado["periodo"] = mes
    return resultado


def procesar_mes(mes: str) -> None:
    carpeta_raw = os.path.join(CARPETA_RAW, mes)
    carpeta_salida = os.path.join(CARPETA_STAGING, mes)
    os.makedirs(carpeta_salida, exist_ok=True)

    archivos_usados = set()
    for ruta in glob.glob(os.path.join(carpeta_raw, "*.csv")):
        nombre_archivo = os.path.basename(ruta)
        entrada = CONFIG_POR_ARCHIVO.get(normalizar(nombre_archivo))
        if entrada is None:
            print(f"[{mes}] omitido (no está en CSV_CONFIG): {nombre_archivo}")
            continue
        nombre_config, config = entrada
        tabla = config["tabla"]
        df = procesar_csv(ruta, config, mes)
        ruta_parquet = os.path.join(carpeta_salida, f"{tabla}.parquet")
        df.to_parquet(ruta_parquet, index=False)
        archivos_usados.add(nombre_config)
        print(f"[{mes}] {tabla}: {len(df)} filas <- {nombre_archivo}")

    for nombre_config in set(CSV_CONFIG) - archivos_usados:
        print(f"[{mes}] aviso: {nombre_config} no estaba en el ZIP de este mes")


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
