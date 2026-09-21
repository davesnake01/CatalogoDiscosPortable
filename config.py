"""
config.py
=========
Rutas locales de la versión portable.

No hay credenciales ni servidor: lo único que hay que decidir es **dónde vive
el archivo del catálogo**. Se usa la carpeta de datos estándar de cada sistema,
así que el programa nunca escribe dentro de su propia carpeta (que puede ser de
solo lectura, como el montaje de un AppImage).

    Linux/BSD : $XDG_DATA_HOME/CatalogoDiscos/catalogo.db  (~/.local/share/...)
    macOS     : ~/Library/Application Support/CatalogoDiscos/catalogo.db
    Windows   : %LOCALAPPDATA%\\CatalogoDiscos\\catalogo.db

Se puede forzar otra ubicación con la variable de entorno `CATALOGO_DB`.
"""

import os
import sys
from pathlib import Path

NOMBRE_APP = "CatalogoDiscos"
NOMBRE_BD = "catalogo.db"


def dir_datos() -> Path:
    """Carpeta de datos del usuario, según el sistema operativo."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        raiz = Path(base) if base else Path.home() / "AppData" / "Local"
        return raiz / NOMBRE_APP
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / NOMBRE_APP
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(Path.home(), ".local", "share")
    return Path(base) / NOMBRE_APP


def ruta_bd() -> Path:
    """Ruta completa del archivo de la base de datos."""
    forzada = os.environ.get("CATALOGO_DB")
    if forzada:
        return Path(forzada).expanduser()
    return dir_datos() / NOMBRE_BD
