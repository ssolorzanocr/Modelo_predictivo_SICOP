"""Descarga los ZIP mensuales del Observatorio de Compra Pública de Costa Rica.

Modo normal (sin argumentos): descarga solo el mes vigente, calculado en
hora de Costa Rica, que es el único archivo que el Observatorio reescribe
por completo cada día (a las 08:00).

Modo --backfill: descarga un rango de meses ya cerrados (no cambian más),
pensado para la carga histórica inicial.
"""
import argparse
import io
import os
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE_URL = (
    "https://dlsaobservatorioprod.blob.core.windows.net/"
    "fs-synapse-observatorio-produccion/Zip/{yyyymm}.zip"
)
CARPETA_RAW = "data/raw"


def mes_actual_costa_rica() -> str:
    return datetime.now(ZoneInfo("America/Costa_Rica")).strftime("%Y%m")


def rango_meses(inicio: str, fin: str) -> list[str]:
    ini = datetime.strptime(inicio, "%Y%m")
    fin_dt = (
        datetime.strptime(fin, "%Y%m")
        if fin
        else datetime.now(ZoneInfo("America/Costa_Rica"))
    )
    meses = []
    actual = ini
    while actual <= fin_dt:
        meses.append(actual.strftime("%Y%m"))
        if actual.month == 12:
            actual = actual.replace(year=actual.year + 1, month=1)
        else:
            actual = actual.replace(month=actual.month + 1)
    return meses


def descargar_mes(yyyymm: str, intentos: int = 3) -> str:
    destino = os.path.join(CARPETA_RAW, yyyymm)
    os.makedirs(destino, exist_ok=True)
    url = BASE_URL.format(yyyymm=yyyymm)
    ultimo_error = None
    for intento in range(1, intentos + 1):
        try:
            resp = requests.get(url, timeout=180)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                z.extractall(destino)
            print(f"[{yyyymm}] descargado y descomprimido en {destino}")
            return destino
        except Exception as e:
            ultimo_error = e
            print(f"[{yyyymm}] intento {intento} falló: {e}")
    raise RuntimeError(f"No se pudo descargar {yyyymm} tras {intentos} intentos") from ultimo_error


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--inicio", default="202301")
    parser.add_argument("--fin", default="")
    args = parser.parse_args()

    if args.backfill:
        #Modo --backfill.
        meses = rango_meses(args.inicio, args.fin)
        print(f"Backfill de {len(meses)} meses: {meses[0]} a {meses[-1]}")
        for m in meses:
            descargar_mes(m)
    else:
        #Modo normal (sin argumentos): descarga solo el mes vigente.
        descargar_mes(mes_actual_costa_rica())