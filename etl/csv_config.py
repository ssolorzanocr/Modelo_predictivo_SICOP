"""Configuración de qué columnas extraer de cada CSV del Observatorio y a
qué tabla de staging corresponde cada uno.

Un archivo del ZIP que NO aparece aquí como llave se ignora por completo en
transforma.py -- así se resuelve el caso de archivos extraídos que no hacen
falta para el modelo.
"""

CSV_CONFIG = {

    "InstitucionesRegistradas.csv": {
        "tabla": "dim_instituciones",
        "columnas": [
            "CEDULA",
            "NOMBRE_INSTITUCION",
            "ZONA_GEO_INST",
            "FECHA_INGRESO",
        ]
    },

    "Proveedores.csv": {
        "tabla": "dim_proveedores",
        "columnas": [
            "CEDULA_PROVEEDOR",
            "NOMBRE_PROVEEDOR",
            "TIPO_PROVEEDOR",
            "TAMAÑO_PROVEEDOR",  ## TAMAÑO
            "zona_geo_prov",
            "fecha_registro"
        ]
    },

    "DetalleCarteles.csv": {
        "tabla": "fact_carteles",
        "columnas": [
            "NRO_SICOP",
            "CEDULA_INSTITUCION",
            "FECHA_PUBLICACION",
            "NRO_PROCEDIMIENTO",
            "TIPO_PROCEDIMIENTO",
            "MODALIDAD_PROCEDIMIENTO",
            "CARTEL_STAT",  ## STATUS_CARTEL
            "CARTEL_NM",  ## NOMBRE_CARTEL
            "FECHAH_APERTURA",
            "CLAS_OBJ",  ## CLASIFICACION_CARTEL
            "MONTO_EST"  ## Puede contener NULL, ya que no todos los carteles tienen un monto estimado.
        ]
    },

    "DetalleLineaCartel.csv": {
        "tabla": "fact_lineas_carteles",
        "columnas": [
            "NRO_SICOP",
            "NUMERO_LINEA",
            "NUMERO_PARTIDA",
            "CANTIDAD_SOLICITADA",
            "PRECIO_UNITARIO_ESTIMADO",
            "TIPO_MONEDA",
            "TIPO_CAMBIO_CRC",
            "CODIGO_IDENTIFICACION",
            "MONTO_RESERVADO",  ## Se refiere al total de cada línea (Cantidad X Precio unitario) en la moneda especificada.
            "DESC_LINEA"
        ]
    },

    "Ofertas.csv": {
        "tabla": "fact_ofertas",
        "columnas": [
            "NRO_SICOP",  ## Opcional, puede eliminarse.
            "NRO_OFERTA",
            "CEDULA_PROVEEDOR",
            "FECHA_PRESENTA_OFERTA",
            "TIPO_OFERTA"
        ]
    },

    "LineasOfertadas.csv": {
        "tabla": "fact_lineas_ofertas",
        "columnas": [
            "NRO_OFERTA",
            "NRO_LINEA",
            "CODIGO_PRODUCTO_CL",
            "CANTIDAD_OFERTADA",
            "PRECIO_UNITARIO_OFERTADO",
            "TIPO_MONEDA",
            "TIPO_CAMBIO_CRC"
        ]
    },

    "ProcedimientoAdjudicacion.csv": {
        "tabla": "fact_adjudicaciones",
        "columnas": [
            "NRO_SICOP",
            "CEDULA",  ## CEDULA INSTITUCIÓN
            "NUMERO_PROCEDIMIENTO",
            "DESCR_PROCEDIMIENTO",
            "LINEA",
            "PROD_ID",  ## CODIGO DE PRODUCTO
            "FECHA_ADJUD_FIRME",
            "MONTO_ADJU_LINEA"  ## MONTO_ADJUDICADO_LINEA
        ]
    },

    "LineasAdjudicadas.csv": {
        "tabla": "fact_lineas_adjudicadas",
        "columnas": [
            "NRO_SICOP",
            "NRO_OFERTA",
            "NRO_LINEA",
            "CODIGO_PRODUCTO",
            "CEDULA_PROVEEDOR",
            "CANTIDAD_ADJUDICADA",
            "PRECIO_UNITARIO_ADJUDICADO",
            "TIPO_MONEDA",
            "TIPO_CAMBIO_CRC"
        ]
    }
}
