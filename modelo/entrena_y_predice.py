"""Entrena (opcionalmente) el clasificador de probabilidad de adjudicación
por proveedor, y genera predicciones para las ofertas de líneas de cartel
todavía abiertas.

Por defecto solo genera predicciones con el último modelo guardado -- eso
mantiene el job diario liviano. Reentrena desde cero los lunes, o si se pasa
--forzar (útil tras un backfill o un cambio en el modelo).

NOTA sobre las llaves: fact_ofertas usa nro_oferta_nro_linea, mientras que
fact_carteles y fact_adjudicaciones usan nro_sicop_nro_linea. Aquí se deriva
nro_linea asumiendo que nro_oferta_nro_linea tiene el formato
"{nro_oferta}-{nro_linea}" -- confírmalo contra datos reales y ajusta el
separador de split_part si hace falta.
"""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb
import joblib
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

RUTA_DUCKDB = "data/sicop.duckdb"
RUTA_MODELO = "modelo/clasificador_adjudicacion.joblib"

COLUMNAS_CATEGORICAS = ["cod_producto", "tipo_moneda", "tipo_oferta"]
COLUMNAS_NUMERICAS = ["cantidad_ofertada", "precio_unitario_ofertado", "tipo_cambio_crc", "monto_linea"]

CONSULTA_ENTRENAMIENTO = """
    SELECT
        o.*,
        (a.nro_sicop_nro_linea IS NOT NULL) AS fue_adjudicado
    FROM (
        SELECT *,
            nro_sicop || '-' || split_part(nro_oferta_nro_linea, '-', 2) AS nro_sicop_nro_linea
        FROM fact_ofertas
    ) o
    LEFT JOIN fact_adjudicaciones a
        USING (nro_sicop_nro_linea, cedula_proveedor)
"""

CONSULTA_PENDIENTES = """
    SELECT o.*
    FROM fact_ofertas o
    INNER JOIN fact_carteles c
        ON c.nro_sicop_nro_linea = o.nro_sicop || '-' || split_part(o.nro_oferta_nro_linea, '-', 2)
"""


def debe_reentrenar(forzar: bool) -> bool:
    if forzar:
        return True
    hoy = datetime.now(ZoneInfo("America/Costa_Rica"))
    return hoy.weekday() == 0  # 0 = lunes


def construir_pipeline() -> Pipeline:
    preprocesador = ColumnTransformer(
        [("categoricas", OneHotEncoder(handle_unknown="ignore"), COLUMNAS_CATEGORICAS)],
        remainder="passthrough",
    )
    return Pipeline([
        ("preprocesamiento", preprocesador),
        ("clasificador", RandomForestClassifier(n_estimators=300, random_state=42, n_jobs=-1)),
    ])


def entrenar(con) -> None:
    df = con.execute(CONSULTA_ENTRENAMIENTO).df()
    y = df.pop("fue_adjudicado")
    X = df[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS]
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    pipeline = construir_pipeline()
    pipeline.fit(X_train, y_train)
    print(f"Exactitud en validación: {pipeline.score(X_test, y_test):.3f}")
    joblib.dump(pipeline, RUTA_MODELO)


def predecir(con) -> None:
    pipeline = joblib.load(RUTA_MODELO)
    pendientes = con.execute(CONSULTA_PENDIENTES).df()
    if pendientes.empty:
        print("No hay ofertas pendientes por predecir.")
        return
    X = pendientes[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS]
    pendientes["probabilidad_adjudicacion"] = pipeline.predict_proba(X)[:, 1]
    resultado = pendientes[["nro_oferta_nro_linea", "cedula_proveedor", "probabilidad_adjudicacion"]]
    con.execute("CREATE OR REPLACE TABLE predicciones_adjudicacion AS SELECT * FROM resultado")
    print(f"{len(resultado)} predicciones guardadas en predicciones_adjudicacion")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--forzar", action="store_true")
    args = parser.parse_args()

    con = duckdb.connect(RUTA_DUCKDB)
    if debe_reentrenar(args.forzar):
        entrenar(con)
    predecir(con)
    con.close()
