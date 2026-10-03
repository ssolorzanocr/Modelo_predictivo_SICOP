"""Entrena (opcionalmente) el clasificador de probabilidad de adjudicación
por proveedor, y genera predicciones para las ofertas de líneas de cartel
todavía abiertas.

Por defecto solo genera predicciones con el último modelo guardado -- eso
mantiene el job diario liviano. Reentrena desde cero los lunes, si se pasa
--forzar (útil tras un backfill o un cambio en el modelo), o si el archivo
del modelo simplemente no existe todavía (primera corrida). El .joblib se
persiste entre corridas subiéndolo al mismo Release que sicop.duckdb -- ver
el workflow actualizacion_diaria.yml.

Variables de entrada (revisadas para reducir la cardinalidad de la rama
categórica frente a la versión anterior, que usaba cod_producto completo):
- categóricas: tamano_proveedor (dim_proveedores), tipo_procedimiento
  (fact_lineas_carteles).
- numéricas: proveedores_adjudicados_distintos (dim_instituciones),
  porcentaje_exito_historico y productos_distintos_historico (ambas
  calculadas "a la fecha" de cada oferta, ver CTE_ENRIQUECIMIENTO_HISTORICO
  -- NO son dim_proveedores.porcentaje_exito ni
  dim_proveedores.productos_distintos_ofertados, que son agregados fijos
  sobre todo el histórico y tienen fuga de información), radio_competitividad,
  cantidad_ofertada, intensidad_competencia y precio_relativo_competidores
  (las cuatro de fact_lineas_ofertas), cantidad_solicitada
  (fact_lineas_carteles). Las últimas dos miden competencia dentro de la
  misma línea (cuántos proveedores ofertaron, y el precio de esta oferta
  contra la mediana de los demás) -- no necesitan tratamiento point-in-time
  como las "_historico", porque todas las ofertas de una línea se presentan
  dentro de la misma ventana de apertura del cartel, no a lo largo de meses.

Todas estas viven en tablas distintas, así que las dos consultas SQL unen
fact_lineas_ofertas con fact_lineas_carteles, dim_proveedores, dim_productos
y dim_instituciones -- no hace falta ninguna transformación en Python, todo
el join queda resuelto en DuckDB antes de que pandas reciba el resultado.
Etiquetado de fue_adjudicado (importante): una oferta sin adjudicación
registrada NO siempre significa "perdió" -- puede que el procedimiento
todavía no se haya resuelto. Se etiqueta FALSE en cuanto el nro_sicop ya
tiene alguna adjudicación registrada (sin esperar ningún número fijo de
días), con BUFFER_DIAS_DECISION como respaldo solo para procedimientos que
nunca adjudican nada a nadie (desiertos/cancelados). Ver el comentario
junto a CONSULTA_ENTRENAMIENTO para el detalle completo de las 4 reglas.
Las ofertas sin ninguna de esas señales quedan en NULL y se excluyen por
completo del entrenamiento y la prueba.

El split de entrenamiento/prueba es cronológico, no aleatorio: las ofertas
más antiguas van a entrenamiento, las más recientes (dentro del rango ya
confiable) van a prueba. Así se imita cómo se usa el modelo en producción
-- predecir el futuro con el pasado -- en vez de dejar que el modelo
entrene con datos posteriores a los que se usan para evaluarlo.
"""
import argparse
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import duckdb
import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

RUTA_DUCKDB = "data/sicop.duckdb"
RUTA_MODELO = "modelo/clasificador_adjudicacion.joblib"

# Días desde fecha_oferta a partir de los cuales se asume que un
# procedimiento (nro_sicop) que nunca adjudicó nada a nadie fue declarado
# desierto/infructuoso o cancelado -- es el RESPALDO para cuando la señal
# principal (ver más abajo) nunca llega, no la vía principal hacia FALSE.
# Se fijó en 90 con base en un análisis real del tiempo entre oferta y
# adjudicación: mediana 27 días, p90 84 días, p99 168 días -- 90 queda
# apenas por encima del p90. Si el ritmo de resolución cambia con el
# tiempo, vale la pena revisar este número corriendo de nuevo ese análisis.
BUFFER_DIAS_DECISION = 90

COLUMNAS_CATEGORICAS = ["tamano_proveedor", "tipo_procedimiento"]
COLUMNAS_NUMERICAS = [
    "proveedores_adjudicados_distintos",
    "porcentaje_exito_historico",
    "radio_competitividad",
    "productos_distintos_historico",
    "cantidad_solicitada",
    "cantidad_ofertada",
    "intensidad_competencia",
    "precio_relativo_competidores",
]

# Las cuatro tablas de enriquecimiento (fact_lineas_carteles, dim_proveedores,
# dim_productos, dim_instituciones) se unen igual en entrenamiento y en
# predicción, así que quedan como un solo bloque de JOINs reutilizado --
# solo cambia el SELECT final y si se filtra por líneas pendientes.
# Nota: dim_proveedores.porcentaje_exito y
# dim_proveedores.productos_distintos_ofertados (ambos promedios/conteos
# sobre todo el histórico) ya NO se usan como variables del modelo -- se
# reemplazaron por porcentaje_exito_historico y productos_distintos_historico
# (ver CTE_ENRIQUECIMIENTO_HISTORICO abajo), que sí respetan la fecha de cada
# oferta. Las dos columnas de dim_proveedores se dejan tal cual para uso
# descriptivo/BI (dashboards, estudios de proveedores), donde la fuga de
# información no aplica -- pero no deben volver a usarse para entrenar.

JOINS_ENRIQUECIMIENTO = """
    FROM final.fact_lineas_ofertas o
    JOIN final.fact_lineas_carteles c
        ON c.nro_sicop = o.nro_sicop AND c.numero_linea = o.nro_linea
    LEFT JOIN final.dim_proveedores p ON p.cedula_proveedor = o.cedula_proveedor
    LEFT JOIN final.dim_productos pr ON pr.cod_producto = TRY_CAST(o.cod_producto AS BIGINT)
    LEFT JOIN final.dim_instituciones i ON i.cedula = c.cedula_institucion
    LEFT JOIN enriquecimiento_historico eh
        ON eh.cedula_proveedor = o.cedula_proveedor AND eh.nro_oferta = o.nro_oferta
"""

# CTE compartida por las dos consultas de abajo: calcula, para cada oferta
# de cada proveedor, su tasa de éxito y su cantidad de productos distintos
# ofertados, usando SOLO ofertas anteriores en el tiempo (ventana expansiva
# por fecha) -- nunca la oferta actual ni ninguna posterior. Esto es
# justamente lo que evita la fuga de información que tenían las versiones
# agregadas sobre todo el histórico (iguales sin importar si la fila caía
# en entrenamiento o en prueba).
#
# Las ofertas ganadas SÍ se pueden acumular con una simple suma por ventana
# (resumen_diario/acumulado_diario): agrupar primero por día para que
# "antes de esta fecha" sea inequívoco aunque el proveedor presente varias
# ofertas el mismo día, y "ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING"
# sobre esos días ya ordenados da exactamente "todo lo estrictamente
# anterior a este día".
#
# Los productos DISTINTOS no se pueden acumular de la misma forma -- un
# mismo producto puede repetirse en días distintos, así que sumar
# "productos nuevos por día" no da el conteo distinto correcto. Por eso
# productos_historico usa una subconsulta correlacionada (más cara que una
# función de ventana, pero la única forma correcta de contar distintos
# "hasta esta fecha" sin arrastrar de más).
CTE_ENRIQUECIMIENTO_HISTORICO = """
    ofertas_historial AS (
        SELECT DISTINCT cedula_proveedor, nro_oferta, fecha_oferta
        FROM final.fact_lineas_ofertas
    ),
    resultado_ofertas AS (
        SELECT
            h.cedula_proveedor, h.nro_oferta, h.fecha_oferta,
            EXISTS (
                SELECT 1 FROM final.fact_lineas_adjudicadas a
                WHERE a.nro_oferta = h.nro_oferta AND a.cedula_proveedor = h.cedula_proveedor
            ) AS gano
        FROM ofertas_historial h
    ),
    resumen_diario AS (
        SELECT cedula_proveedor, fecha_oferta,
               COUNT(*) AS ofertas_del_dia,
               COUNT(*) FILTER (WHERE gano) AS ganadas_del_dia
        FROM resultado_ofertas
        GROUP BY cedula_proveedor, fecha_oferta
    ),
    acumulado_diario AS (
        SELECT
            cedula_proveedor,
            fecha_oferta,
            SUM(ofertas_del_dia) OVER (
                PARTITION BY cedula_proveedor ORDER BY fecha_oferta
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS ofertas_previas,
            SUM(ganadas_del_dia) OVER (
                PARTITION BY cedula_proveedor ORDER BY fecha_oferta
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS ofertas_previas_ganadas
        FROM resumen_diario
    ),
    productos_por_proveedor_fecha AS (
        SELECT DISTINCT cedula_proveedor, cod_producto, fecha_oferta
        FROM final.fact_lineas_ofertas
    ),
    productos_historico AS (
        SELECT
            h.cedula_proveedor,
            h.nro_oferta,
            (
                SELECT COUNT(DISTINCT pf.cod_producto)
                FROM productos_por_proveedor_fecha pf
                WHERE pf.cedula_proveedor = h.cedula_proveedor
                  AND pf.fecha_oferta < h.fecha_oferta
            ) AS productos_distintos_historico
        FROM ofertas_historial h
    ),
    enriquecimiento_historico AS (
        SELECT
            r.cedula_proveedor,
            r.nro_oferta,
            ad.ofertas_previas_ganadas / NULLIF(ad.ofertas_previas, 0) AS porcentaje_exito_historico,
            ph.productos_distintos_historico
        FROM resultado_ofertas r
        JOIN acumulado_diario ad
            ON ad.cedula_proveedor = r.cedula_proveedor AND ad.fecha_oferta = r.fecha_oferta
        JOIN productos_historico ph
            ON ph.cedula_proveedor = r.cedula_proveedor AND ph.nro_oferta = r.nro_oferta
    )
"""

# Etiquetado de fue_adjudicado, en orden de prioridad:
#   1. Esta oferta puntual (nro_sicop+nro_linea+nro_oferta+cedula_proveedor)
#      aparece en fact_lineas_adjudicadas -> TRUE.
#   2. El nro_sicop YA tiene alguna adjudicación registrada (de otra oferta,
#      otra línea) -> el procedimiento se resolvió y no fue esta -> FALSE.
#      Esta es la señal rápida: no espera ningún número fijo de días, usa
#      el hecho de que SICOP ya publicó una decisión para ese procedimiento.
#   3. El nro_sicop no tiene NINGUNA adjudicación todavía, pero ya pasó
#      BUFFER_DIAS_DECISION desde la oferta -> se asume desierto/cancelado
#      -> FALSE. Este es el respaldo para procedimientos que nunca
#      adjudican nada a nadie (si no existiera, esas ofertas quedarían en
#      NULL para siempre y el modelo nunca vería ese tipo de caso).
#   4. Cualquier otro caso -> NULL, se excluye (ver el WHERE más abajo).
#
# fecha_oferta se incluye para poder hacer el split cronológico en Python;
# no es una variable del modelo (no está en COLUMNAS_NUMERICAS).
CONSULTA_ENTRENAMIENTO = f"""
    WITH {CTE_ENRIQUECIMIENTO_HISTORICO},
    sicops_resueltos AS (
        SELECT DISTINCT nro_sicop FROM final.fact_lineas_adjudicadas
    ),
    etiquetado AS (
        SELECT
            p.tamano_proveedor,
            -- pr.segmento,
            c.tipo_procedimiento,
            i.proveedores_adjudicados_distintos,
            eh.porcentaje_exito_historico,
            o.radio_competitividad,
            eh.productos_distintos_historico,
            c.cantidad_solicitada,
            o.cantidad_ofertada,
            o.intensidad_competencia,
            o.precio_relativo_competidores,
            o.fecha_oferta,
            CASE
                WHEN a.nro_oferta IS NOT NULL THEN TRUE
                WHEN sr.nro_sicop IS NOT NULL THEN FALSE
                WHEN DATE_DIFF('day', o.fecha_oferta, CURRENT_DATE) > {BUFFER_DIAS_DECISION} THEN FALSE
                ELSE NULL
            END AS fue_adjudicado
        {JOINS_ENRIQUECIMIENTO}
        LEFT JOIN final.fact_lineas_adjudicadas a
            ON a.nro_sicop = o.nro_sicop AND a.nro_linea = o.nro_linea
           AND a.nro_oferta = o.nro_oferta AND a.cedula_proveedor = o.cedula_proveedor
        LEFT JOIN sicops_resueltos sr ON sr.nro_sicop = o.nro_sicop
    )
    SELECT * FROM etiquetado WHERE fue_adjudicado IS NOT NULL
"""

# CONSULTA_PENDIENTES SÍ necesita su propio filtro de fechas -- a diferencia
# de lo que un comentario anterior de este archivo asumía, fact_lineas_
# carteles.adjudicada = false por sí solo NO distingue entre tres casos muy
# distintos:
#   1. Líneas que todavía NO cierran para recibir ofertas (fecha_apertura en
#      el futuro) -- acá intensidad_competencia y precio_relativo_
#      competidores todavía no son definitivos: el conjunto de competidores
#      sigue creciendo. Se excluyen con fecha_apertura <= CURRENT_DATE.
#   2. Líneas recién cerradas, esperando una decisión que todavía es
#      plausible -- el caso real que queremos predecir.
#   3. Líneas cerradas hace mucho tiempo sin decisión -- casi seguro
#      abandonadas/desiertas, nunca se van a resolver. Predecir sobre ellas
#      desperdicia cómputo y llena predicciones_adjudicacion de filas que
#      nadie va a poder usar. Se excluyen igual que en el entrenamiento, con
#      el mismo BUFFER_DIAS_DECISION (ahora medido desde fecha_apertura, no
#      desde fecha_oferta -- son fechas muy cercanas entre sí, pero
#      apertura es la referencia correcta aquí: es cuando se cierra el
#      conjunto de competidores, no cuando cada proveedor presentó la suya).
#
# Las dos variables "_historico" sí aplican sin ningún ajuste adicional:
# para una oferta de hoy, cuentan usando todo lo anterior a hoy -- el mismo
# criterio que se usó para cada fila de entrenamiento en su propio momento.

CONSULTA_PENDIENTES = f"""
    WITH {CTE_ENRIQUECIMIENTO_HISTORICO}
    SELECT
        o.nro_sicop,
        o.nro_linea,
        o.nro_oferta,
        o.cedula_proveedor,
        p.tamano_proveedor,
        c.tipo_procedimiento,
        i.proveedores_adjudicados_distintos,
        eh.porcentaje_exito_historico,
        o.radio_competitividad,
        eh.productos_distintos_historico,
        c.cantidad_solicitada,
        o.cantidad_ofertada,
        o.intensidad_competencia,
        o.precio_relativo_competidores
    {JOINS_ENRIQUECIMIENTO}
    WHERE c.adjudicada = false
      AND c.fecha_apertura <= CURRENT_DATE
      AND DATE_DIFF('day', c.fecha_apertura, CURRENT_DATE) <= {BUFFER_DIAS_DECISION}
"""



def debe_reentrenar(forzar: bool) -> bool:
    if forzar:
        return True
    if not os.path.exists(RUTA_MODELO):
        # No hay modelo persistido todavía (primera corrida, o el archivo
        # se perdió por algún motivo) -- entrenar sin importar el día.
        return True
    hoy = datetime.now(ZoneInfo("America/Costa_Rica"))
    return hoy.weekday() == 0  # 0 = lunes


def dividir_train_test_cronologico(
    df: pd.DataFrame, columna_fecha: str = "fecha_oferta", proporcion_test: float = 0.2
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Divide por fecha en vez de al azar: lo más antiguo a entrenamiento, lo
    más reciente a prueba. No usa stratify -- con un corte cronológico no
    tiene sentido forzar la misma proporción de clases en ambos lados; es
    normal (y esperado) que difiera un poco entre períodos."""
    df_ordenado = df.sort_values(columna_fecha).reset_index(drop=True)
    corte = int(len(df_ordenado) * (1 - proporcion_test))
    return df_ordenado.iloc[:corte], df_ordenado.iloc[corte:]


def construir_pipeline() -> Pipeline:
    # RandomForestClassifier no acepta NaN de forma nativa, así que hay que
    # imputar los dos grupos de columnas antes de que lleguen al bosque:
    # - categóricas: los nulos se rellenan con un valor fijo ("desconocido")
    #   antes de codificar, para que nunca lleguen strings vacíos al encoder.
    # - numéricas: los nulos se rellenan con la mediana de esa columna --
    #   más robusta que la media frente a valores atípicos (montos muy altos).
    transformador_categoricas = Pipeline([
        ("imputar", SimpleImputer(strategy="constant", fill_value="desconocido")),
        ("codificar", OneHotEncoder(handle_unknown="ignore")),
    ])
    transformador_numericas = SimpleImputer(strategy="median")

    preprocesador = ColumnTransformer([
        ("categoricas", transformador_categoricas, COLUMNAS_CATEGORICAS),
        ("numericas", transformador_numericas, COLUMNAS_NUMERICAS),
    ])
    return Pipeline([
        ("preprocesamiento", preprocesador),
        ("clasificador", RandomForestClassifier(
            # Parámetros elegidos tras un random search con validación cruzada (5 folds)
            class_weight="balanced_subsample", # balancea clases en cada árbol, no en todo el bosque.
            n_estimators=100,
            min_samples_leaf=20,
            max_depth=20,
            max_features="sqrt", 
            random_state=42, 
            n_jobs=-1)),
    ])


def entrenar(con) -> None:
    df = con.execute(CONSULTA_ENTRENAMIENTO).df()
    df["fue_adjudicado"] = df["fue_adjudicado"].astype(bool)
    train_df, test_df = dividir_train_test_cronologico(df)

    y_train = train_df.pop("fue_adjudicado")
    X_train = train_df[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS]
    y_test = test_df.pop("fue_adjudicado")
    X_test = test_df[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS]

    pipeline = construir_pipeline()
    pipeline.fit(X_train, y_train)
    print(f"Exactitud en validación (test): {pipeline.score(X_test, y_test):.3f}")
    joblib.dump(pipeline, RUTA_MODELO)


def predecir(con) -> None:
    pipeline = joblib.load(RUTA_MODELO)
    pendientes = con.execute(CONSULTA_PENDIENTES).df()
    if pendientes.empty:
        print("No hay ofertas pendientes por predecir.")
        return
    X = pendientes[COLUMNAS_CATEGORICAS + COLUMNAS_NUMERICAS]
    pendientes["probabilidad_adjudicacion"] = pipeline.predict_proba(X)[:, 1]
    resultado = pendientes[["nro_sicop", "nro_linea", "nro_oferta", "cedula_proveedor", "probabilidad_adjudicacion"]]
    con.execute("CREATE OR REPLACE TABLE final.predicciones_adjudicacion AS SELECT * FROM resultado")
    print(f"{len(resultado)} predicciones guardadas en final.predicciones_adjudicacion")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--forzar", action="store_true")
    args = parser.parse_args()

    con = duckdb.connect(RUTA_DUCKDB)
    if debe_reentrenar(args.forzar):
        entrenar(con)
    predecir(con)
    con.close()