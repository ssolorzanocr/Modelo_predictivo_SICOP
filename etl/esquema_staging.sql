-- Esquema `staging`: una tabla por CSV, todas las columnas como VARCHAR.
-- El tipado real (DOUBLE, DATE, etc.) se hace en construir_modelo_final.py.

CREATE TABLE IF NOT EXISTS staging.dim_instituciones (
    cedula VARCHAR,
    nombre_institucion VARCHAR,
    zona_geo_inst VARCHAR,
    fecha_ingreso VARCHAR,
    periodo VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.dim_proveedores (
    cedula_proveedor VARCHAR,
    nombre_proveedor VARCHAR,
    tipo_proveedor VARCHAR,
    tamano_proveedor VARCHAR,
    zona_geo_prov VARCHAR,
    fecha_registro VARCHAR,
    periodo VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.fact_carteles (
    nro_sicop VARCHAR,
    cedula_institucion VARCHAR,
    fecha_publicacion VARCHAR,
    nro_procedimiento VARCHAR,
    tipo_procedimiento VARCHAR,
    modalidad_procedimiento VARCHAR,
    cartel_stat VARCHAR,
    cartel_nm VARCHAR,
    fechah_apertura VARCHAR,
    clas_obj VARCHAR,
    monto_est VARCHAR,
    periodo VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.fact_lineas_carteles (
    nro_sicop VARCHAR,
    numero_linea VARCHAR,
    numero_partida VARCHAR,
    cantidad_solicitada VARCHAR,
    precio_unitario_estimado VARCHAR,
    tipo_moneda VARCHAR,
    tipo_cambio_crc VARCHAR,
    codigo_identificacion VARCHAR,
    monto_reservado VARCHAR,
    desc_linea VARCHAR,
    periodo VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.fact_ofertas (
    nro_sicop VARCHAR,
    nro_oferta VARCHAR,
    cedula_proveedor VARCHAR,
    fecha_presenta_oferta VARCHAR,
    tipo_oferta VARCHAR,
    periodo VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.fact_lineas_ofertas (
    nro_oferta VARCHAR,
    nro_linea VARCHAR,
    codigo_producto_cl VARCHAR,
    cantidad_ofertada VARCHAR,
    precio_unitario_ofertado VARCHAR,
    tipo_moneda VARCHAR,
    tipo_cambio_crc VARCHAR,
    periodo VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.fact_adjudicaciones (
    nro_sicop VARCHAR,
    cedula VARCHAR,
    numero_procedimiento VARCHAR,
    descr_procedimiento VARCHAR,
    linea VARCHAR,
    prod_id VARCHAR,
    fecha_adjud_firme VARCHAR,
    monto_adju_linea VARCHAR,
    periodo VARCHAR
);

CREATE TABLE IF NOT EXISTS staging.fact_lineas_adjudicadas (
    nro_sicop VARCHAR,
    nro_oferta VARCHAR,
    nro_linea VARCHAR,
    codigo_producto VARCHAR,
    cedula_proveedor VARCHAR,
    cantidad_adjudicada VARCHAR,
    precio_unitario_adjudicado VARCHAR,
    tipo_moneda VARCHAR,
    tipo_cambio_crc VARCHAR,
    periodo VARCHAR
);
