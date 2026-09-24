"""Construye el esquema `final` -- curado, tipado, y listo para el
dashboard y el modelo de ML -- a partir del esquema `staging`.

Se reconstruye completo desde cero en cada corrida (CREATE OR REPLACE),
leyendo TODO el staging acumulado (no solo el mes vigente). Con el volumen
de datos de este proyecto, DuckDB hace esto en segundos, así que es más
simple que mantener un "final" incremental.

Tres decisiones de diseño importantes:

1. TRY_CAST en vez de CAST: si un valor no se puede convertir, el resultado
   es NULL en vez de que falle toda la carga. Ojo con esto: TRY_CAST no
   avisa cuando falla, solo devuelve NULL en silencio -- si ves NULL
   inesperados en una columna, es la señal de que el formato de origen no
   es el que se está asumiendo.

2. Fechas con formato mixto: en los CSV reales aparecen fechas como
   "2017-11-08 00:00:00.0000000" (ISO con fracción de segundo, típico de
   exportaciones de SQL Server) y como "25/11/2025" (DD/MM/AAAA). Como no
   interesa la hora, la macro `fecha_flexible` recorta el string a los
   primeros 10 caracteres (que cubre cualquier variante de ISO, sin
   importar cuántos dígitos de fracción traiga) y, si eso no calza, intenta
   explícitamente el formato DD/MM/AAAA con strptime. Si aparece un tercer
   formato en el futuro, es una rama más dentro del COALESCE de la macro.

3. fact_lineas_carteles NO se filtra a solo líneas pendientes. Se conserva
   todo el histórico y se agrega una columna `adjudicada` (booleana) en su
   lugar. Así no se pierde el contexto del cartel (monto estimado,
   clasificación, etc.) para las líneas que sí se adjudicaron, que es
   justamente lo que hace falta para entrenar el modelo de clasificación.

Supuesto a confirmar contra datos reales: en fact_adjudicaciones, la
columna `linea` se asume equivalente a `nro_linea` de fact_lineas_adjudicadas
para poder unir cabecera con detalle. Si no calzan, ajusta la condición del
JOIN en final.fact_lineas_adjudicadas más abajo.
"""
import duckdb

RUTA_DUCKDB = "data/sicop.duckdb"


def construir(con) -> None:
    con.execute("CREATE SCHEMA IF NOT EXISTS final")

    # Macro reutilizable para las dos variantes de fecha que aparecen en los
    # CSV. LEFT(valor, 10) toma solo "AAAA-MM-DD" de cualquier timestamp ISO
    # (con o sin fracción de segundo, sin importar cuántos dígitos traiga);
    # si eso falla (p. ej. porque venía como "25/11/2025"), se intenta ese
    # formato explícitamente. Nunca lanza error -- si ninguna de las dos
    # formas calza, el resultado es NULL.
    con.execute("""
        CREATE OR REPLACE MACRO fecha_flexible(valor) AS
            COALESCE(
                TRY_CAST(LEFT(valor, 10) AS DATE),
                TRY_STRPTIME(valor, '%d/%m/%Y')::DATE
            )
    """)

    con.execute("""
        CREATE OR REPLACE TABLE final.dim_instituciones AS
        SELECT
            cedula,
            nombre_institucion,
            zona_geo_inst,
            fecha_flexible(fecha_ingreso) AS fecha_ingreso
        FROM staging.dim_instituciones
        QUALIFY ROW_NUMBER() OVER (PARTITION BY cedula ORDER BY periodo DESC) = 1
    """)

    con.execute("""
        CREATE OR REPLACE TABLE final.dim_proveedores AS
        SELECT
            cedula_proveedor,
            nombre_proveedor,
            tipo_proveedor,
            tamano_proveedor,
            zona_geo_prov,
            fecha_flexible(fecha_registro) AS fecha_registro
        FROM staging.dim_proveedores
        QUALIFY ROW_NUMBER() OVER (PARTITION BY cedula_proveedor ORDER BY periodo DESC) = 1
    """)

    con.execute("""
        CREATE OR REPLACE TABLE final.fact_lineas_carteles AS
        SELECT
            c.nro_sicop,
            l.numero_linea,
            l.numero_partida,
            c.cedula_institucion,
            fecha_flexible(c.fecha_publicacion) AS fecha_publicacion,
            c.nro_procedimiento,
            c.tipo_procedimiento,
            c.modalidad_procedimiento,
            c.cartel_stat,
            c.cartel_nm,
            fecha_flexible(c.fechah_apertura) AS fecha_apertura,
            c.clas_obj AS clasificacion_cartel,
            TRY_CAST(c.monto_est AS DOUBLE) AS monto_estimado_cartel,
            l.codigo_identificacion AS cod_producto,
            TRY_CAST(l.cantidad_solicitada AS DOUBLE) AS cantidad_solicitada,
            TRY_CAST(l.precio_unitario_estimado AS DOUBLE) AS precio_unitario_estimado,
            l.tipo_moneda,
            TRY_CAST(l.tipo_cambio_crc AS DOUBLE) AS tipo_cambio_crc,
            TRY_CAST(l.monto_reservado AS DOUBLE) AS monto_linea,
            l.desc_linea,
            EXISTS (
                SELECT 1 FROM staging.fact_lineas_adjudicadas a
                WHERE a.nro_sicop = c.nro_sicop AND a.nro_linea = l.numero_linea
            ) AS adjudicada
        FROM staging.fact_carteles c
        JOIN staging.fact_lineas_carteles l USING (nro_sicop)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY c.nro_sicop, l.numero_linea ORDER BY l.periodo DESC
        ) = 1
    """)

    con.execute("""
        CREATE OR REPLACE TABLE final.fact_lineas_ofertas AS
        SELECT
            o.nro_sicop,
            o.nro_oferta,
            lo.nro_linea,
            o.cedula_proveedor,
            fecha_flexible(o.fecha_presenta_oferta) AS fecha_oferta,
            o.tipo_oferta,
            lo.codigo_producto_cl AS cod_producto,
            TRY_CAST(lo.cantidad_ofertada AS DOUBLE) AS cantidad_ofertada,
            TRY_CAST(lo.precio_unitario_ofertado AS DOUBLE) AS precio_unitario_ofertado,
            lo.tipo_moneda,
            TRY_CAST(lo.tipo_cambio_crc AS DOUBLE) AS tipo_cambio_crc,
            TRY_CAST(lo.cantidad_ofertada AS DOUBLE)
                * TRY_CAST(lo.precio_unitario_ofertado AS DOUBLE) AS monto_linea
        FROM staging.fact_ofertas o
        JOIN staging.fact_lineas_ofertas lo USING (nro_oferta)
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY o.nro_oferta, lo.nro_linea ORDER BY lo.periodo DESC
        ) = 1
    """)

    con.execute("""
        CREATE OR REPLACE TABLE final.fact_lineas_adjudicadas AS
        SELECT
            a.nro_sicop,
            a.linea AS nro_linea,
            la.nro_oferta,
            a.cedula AS cedula_institucion,
            a.numero_procedimiento,
            a.descr_procedimiento,
            fecha_flexible(a.fecha_adjud_firme) AS fecha_adjudicacion,
            TRY_CAST(a.monto_adju_linea AS DOUBLE) AS monto_adjudicado_linea,
            la.cedula_proveedor,
            la.codigo_producto AS cod_producto,
            TRY_CAST(la.cantidad_adjudicada AS DOUBLE) AS cantidad_adjudicada,
            TRY_CAST(la.precio_unitario_adjudicado AS DOUBLE) AS precio_unitario_adjudicado,
            la.tipo_moneda,
            TRY_CAST(la.tipo_cambio_crc AS DOUBLE) AS tipo_cambio_crc
        FROM staging.fact_adjudicaciones a
        JOIN staging.fact_lineas_adjudicadas la
            ON la.nro_sicop = a.nro_sicop AND la.nro_linea = a.linea
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY a.nro_sicop, a.linea, la.nro_oferta ORDER BY la.periodo DESC
        ) = 1
    """)

    print(
        "esquema final reconstruido: dim_instituciones, dim_proveedores, "
        "fact_lineas_carteles, fact_lineas_ofertas, fact_lineas_adjudicadas"
    )


if __name__ == "__main__":
    con = duckdb.connect(RUTA_DUCKDB)
    construir(con)
    con.close()