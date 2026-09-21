# AGENTS.md — Catálogo de Discos (Portable)

Guía breve para agentes/desarrolladores que retomen este proyecto.

## Qué es

Versión **portable** del catálogo de discos: app de escritorio **Flet** con
**SQLite**, sin servidor, sin web y sin credenciales. Es un proyecto hermano
(pero independiente) de `CatalogExternalDiscSelf`, que sí usa API + PostgreSQL.

El catálogo completo vive en un único archivo `catalogo.db` en la carpeta de
datos del usuario (ver `config.dir_datos()`). Arranca **vacío**: cada usuario
escanea sus propios discos.

## Archivos

| Archivo | Rol |
|---|---|
| `app.py` | UI Flet, detección/escaneo de discos, vistas y consultas. |
| `db.py` | SQLite: esquema, conexión, utilidades (`legible`, `terminos_busqueda`, `patron_busqueda`). |
| `config.py` | Rutas locales (`dir_datos`, `ruta_bd`); override con `CATALOGO_DB`. |
| `.github/workflows/build.yml` | CI: compila AppImage (Linux) y `.exe`/ZIP (Windows) y publica Release. |

## Notas de arquitectura

- **Un solo modo**: siempre SQLite local. No hay `MODO_CONEXION` ni `api_client`.
- El esquema es el mismo que el proyecto con Postgres, adaptado: fechas en texto
  ISO, `LIKE` en vez de `ILIKE`, `INTEGER PRIMARY KEY AUTOINCREMENT` en vez de
  `BIGSERIAL`, y `executemany` (`db.insertar_archivos`) en vez de `execute_values`.
- **Detección de discos multiplataforma**: Linux usa `findmnt`; Windows usa
  `ctypes.GetVolumeInformationW` para serial/etiqueta. Ver `es_disco_externo()`.
- `app.py` debe poder importarse sin abrir la ventana: `ft.run(main)` va bajo
  `if __name__ == "__main__":` (permite testear el backend).
- El `.db` puede forzarse con `CATALOGO_DB` (útil para pruebas).

## Probar

```bash
python3 -m py_compile app.py db.py config.py
CATALOGO_DB=/tmp/prueba.db python3 app.py   # arranca la UI
```

No hay tests automatizados. Verificación = ejecutar y mirar la UI, más pruebas
de las funciones de `db.py`/`app.py` contra una base temporal.

## Publicar

Push a `master` → el workflow calcula el siguiente tag `v*`, compila
Linux+Windows y crea el Release. No pushear tags a mano.

## Bitácora de cambios

1. **Creación del proyecto portable**: extraído de `CatalogExternalDiscSelf`
   pero **sin** la parte web. Se conserva la UI Flet (escaneo, catálogos,
   búsqueda con resaltado, tema claro/oscuro) y se reemplazó PostgreSQL por
   **SQLite**. Nuevos módulos `db.py` (SQLite) y `config.py` (rutas), README,
   LICENSE (MIT) y CI con matriz Linux+Windows. La UI pasó a tener una pestaña
   **Datos** (ubicación del `.db`, estadísticas) en vez de la de credenciales.
   Detección de discos adaptada a Windows (serial/etiqueta vía ctypes).
