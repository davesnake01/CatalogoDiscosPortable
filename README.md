# Catálogo de Discos Externos — Portable

Escanea tus discos externos (USB, HDD, SSD), guarda un índice de todos los
archivos y te deja **navegar y buscar** el catálogo. Todo funciona **en local**:
no hay servidor, ni base de datos que instalar, ni conexión a internet. El
catálogo vive en un único archivo SQLite.

- **App de escritorio** hecha con [Flet](https://flet.dev).
- **Base de datos**: SQLite (un archivo, viene incluido con Python).
- **Sistemas**: Linux y Windows.

## Descargar

Ve a la página de [**Releases**](../../releases) y baja el binario de tu sistema:

| Sistema | Archivo |
|---|---|
| Linux | `CatalogoDiscosPortable-x86_64.AppImage` |
| Windows | `CatalogoDiscosPortable-windows-x86_64.zip` |

### Linux

```bash
chmod +x CatalogoDiscosPortable-x86_64.AppImage
./CatalogoDiscosPortable-x86_64.AppImage
```

Si no arranca, puede faltar `libfuse2`:

- Debian/Ubuntu: `sudo apt install libfuse2`
- Arch/CachyOS: `sudo pacman -S fuse2`

### Windows

Descomprime el `.zip` y ejecuta `CatalogoDiscosPortable.exe`.

## Cómo se usa

1. **Escanear**: pulsa *Detectar discos*, elige la unidad y dale un nombre
   (y, si quieres, un grupo). La primera vez puede tardar según cuántos
   archivos tenga el disco.
2. **Catálogos**: navega por grupos → discos → carpetas, como un explorador.
3. **Buscar**: busca por nombre de archivo o de carpeta. La coincidencia se
   resalta en amarillo. Puedes limitar la búsqueda a un grupo.
4. **Datos**: muestra dónde está el archivo del catálogo y cuánto ocupa.

## Dónde se guardan los datos

El catálogo es un archivo `catalogo.db` en la carpeta de datos del usuario:

| Sistema | Ruta |
|---|---|
| Linux | `~/.local/share/CatalogoDiscos/catalogo.db` |
| Windows | `%LOCALAPPDATA%\CatalogoDiscos\catalogo.db` |

Para mover el catálogo a otro equipo, copia ese archivo. También puedes forzar
otra ubicación con la variable de entorno `CATALOGO_DB`.

## Ejecutar desde el código

Requiere Python 3.10 o superior.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## Compilar un ejecutable

```bash
pip install pyinstaller
flet pack app.py --name CatalogoDiscosPortable --icon icon.png --add-data "icon.png:."
```

En Windows el separador de `--add-data` es `;` en vez de `:`.

El workflow [`.github/workflows/build.yml`](.github/workflows/build.yml)
compila automáticamente para Linux y Windows en cada push a `master`, y
publica los binarios en un Release.

## Estructura

| Archivo | Rol |
|---|---|
| `app.py` | Interfaz Flet, escaneo de discos y vistas. |
| `db.py` | Capa de base de datos SQLite (esquema, consultas, utilidades). |
| `config.py` | Rutas locales (dónde vive `catalogo.db`). |

## Licencia

[MIT](LICENSE).
