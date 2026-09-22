-- Esquema de las tablas de hechos y dimensiones del modelo de datos SICOP.
-- Se aplica con CREATE TABLE IF NOT EXISTS, así que es seguro ejecutarlo en cada corrida.
-- Nota: se quitaron tildes de los nombres de columna (descripcion, clasificacion)
-- para evitar problemas de encoding en SQL; el significado es el mismo.

CREATE TABLE IF NOT EXISTS dim_proveedores (
    cedula_proveedor VARCHAR PRIMARY KEY,
    nombre_proveedor VARCHAR,
    tipo_proveedor VARCHAR
);

CREATE TABLE IF NOT EXISTS dim_instituciones (
    cedula_institucion VARCHAR PRIMARY KEY,
    nombre_institucion VARCHAR
);

CREATE TABLE IF NOT EXISTS dim_productos (
    cod_producto VARCHAR PRIMARY KEY,
    descripcion_producto VARCHAR,
    segmento VARCHAR,
    nombre_segmento VARCHAR
);

-- Grano: una fila por cada línea de oferta presentada por un proveedor.
-- Tabla acumulativa (nunca se borran filas, solo se reemplaza el `periodo` vigente).
CREATE TABLE IF NOT EXISTS fact_ofertas (
    nro_oferta_nro_linea VARCHAR,
    cod_producto VARCHAR,
    nro_sicop VARCHAR,
    cedula_proveedor VARCHAR,
    cantidad_ofertada DOUBLE,
    precio_unitario_ofertado DOUBLE,
    tipo_moneda VARCHAR,
    tipo_cambio_crc DOUBLE,
    monto_linea DOUBLE,      -- calculado: cantidad_ofertada * precio_unitario_ofertado
    tipo_oferta VARCHAR,
    fecha_oferta DATE,
    periodo VARCHAR          -- YYYYMM del mes en que se cargó, para el reemplazo idempotente
);

-- Grano: una fila por cada línea efectivamente adjudicada. Tabla acumulativa.
CREATE TABLE IF NOT EXISTS fact_adjudicaciones (
    nro_sicop_nro_linea VARCHAR,
    nro_linea VARCHAR,
    nro_oferta VARCHAR,
    descr_procedimiento VARCHAR,
    monto_adjudicado_linea DOUBLE,
    cod_producto VARCHAR,
    nro_sicop VARCHAR,
    cedula_proveedor VARCHAR,
    cedula_institucion VARCHAR,
    moneda_adjudicada VARCHAR,
    tipo_cambio_crc DOUBLE,
    fecha_adjudicacion DATE,
    periodo VARCHAR
);

-- Grano: una fila por cada línea de cartel TODAVÍA NO adjudicada.
-- A diferencia de las otras dos, esta tabla se "reconcilia" después de cada
-- carga: cualquier línea que ya aparezca en fact_adjudicaciones se elimina
-- de aquí, sin importar en qué mes se haya insertado originalmente.
CREATE TABLE IF NOT EXISTS fact_carteles (
    nro_sicop_nro_linea VARCHAR,
    nro_linea VARCHAR,
    nro_oferta VARCHAR,
    nombre_cartel VARCHAR,
    precio_unitario_linea DOUBLE,
    cantidad DOUBLE,
    monto_total_linea DOUBLE,      -- calculado: precio_unitario_linea * cantidad
    monto_estimado_cartel DOUBLE,
    cod_producto VARCHAR,
    nro_sicop VARCHAR,
    tipo_moneda VARCHAR,
    tipo_cambio_crc DOUBLE,
    cedula_institucion VARCHAR,
    clasificacion_cartel VARCHAR,
    tipo_procedimiento VARCHAR,
    fecha_publicacion DATE,
    fecha_apertura DATE,
    periodo VARCHAR
);
