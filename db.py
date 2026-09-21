"""
db.py
=====
Base de datos **local** del Catálogo de Discos (versión portable).

Usa SQLite de la biblioteca estándar: no hay servidor, no hay credenciales y
todo el catálogo vive en un único archivo (`catalogo.db`), cuya ubicación la
decide `config.ruta_bd()`.

El esquema es el mismo que la versión con PostgreSQL (escaneos + archivos),
pero adaptado a SQLite: fechas en texto ISO, `LIKE` en vez de `ILIKE` y sin
tipos/casts exclusivos de Postgres.
"""

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import config


# ─────────────────────────────────────────────────────────────
#  CONEXIÓN
# ─────────────────────────────────────────────────────────────

def conectar(ruta=None, **kw) -> sqlite3.Connection:
    """Abre (y crea si hace falta) la base de datos SQLite.

    Devuelve filas tipo diccionario (`sqlite3.Row`) para que la UI pueda hacer
    `fila["columna"]`, igual que antes con psycopg2.
    """
    destino = Path(ruta) if ruta else config.ruta_bd()
    destino.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(destino), timeout=30, **kw)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


@contextmanager
def get_db(ruta=None):
    conn = conectar(ruta)
    try:
        yield conn
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
#  ESQUEMA
# ─────────────────────────────────────────────────────────────

def _columnas(cursor, tabla) -> set:
    cursor.execute(f"PRAGMA table_info({tabla})")
    return {fila["name"] for fila in cursor.fetchall()}


def crear_tablas() -> bool:
    """Crea/actualiza el esquema de tablas e índices."""
    conn = conectar()
    try:
        with conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS escaneos (
                    id TEXT PRIMARY KEY,
                    uuid_hardware TEXT UNIQUE,
                    nombre_personalizado TEXT,
                    etiqueta_sistema TEXT,
                    espacio_total_bytes INTEGER,
                    espacio_libre_bytes INTEGER,
                    fecha_escaneo TEXT,
                    ruta_raiz TEXT,
                    grupo TEXT DEFAULT 'Sin grupo'
                );
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS archivos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    escaneo_id TEXT REFERENCES escaneos(id) ON DELETE CASCADE,
                    ruta_relativa TEXT,
                    nombre TEXT,
                    tamano_bytes INTEGER,
                    fecha_modificacion TEXT,
                    extension TEXT
                );
            """)
            # Compatibilidad con catálogos creados por versiones anteriores.
            if "grupo" not in _columnas(c, "escaneos"):
                c.execute("ALTER TABLE escaneos ADD COLUMN grupo TEXT DEFAULT 'Sin grupo'")
            for idx in (
                "CREATE INDEX IF NOT EXISTS idx_archivos_escaneo   ON archivos(escaneo_id);",
                "CREATE INDEX IF NOT EXISTS idx_archivos_nombre    ON archivos(nombre);",
                "CREATE INDEX IF NOT EXISTS idx_archivos_extension ON archivos(extension);",
                "CREATE INDEX IF NOT EXISTS idx_escaneos_uuid      ON escaneos(uuid_hardware);",
                "CREATE INDEX IF NOT EXISTS idx_escaneos_fecha     ON escaneos(fecha_escaneo);",
            ):
                c.execute(idx)
        return True
    except Exception as e:
        print(f"[db] Error creando tablas: {e}")
        return False
    finally:
        conn.close()


def inicializar_bd() -> bool:
    """Prepara la base local (la crea si no existe)."""
    return crear_tablas()


def insertar_archivos(cursor, filas) -> None:
    """Inserta un lote de archivos (equivalente a execute_values de psycopg2)."""
    cursor.executemany("""
        INSERT INTO archivos
            (escaneo_id, ruta_relativa, nombre, tamano_bytes,
             fecha_modificacion, extension)
        VALUES (?, ?, ?, ?, ?, ?)
    """, filas)


# ─────────────────────────────────────────────────────────────
#  UTILIDADES
# ─────────────────────────────────────────────────────────────

def legible(b, vacio=None) -> str | None:
    """Formatea bytes a una unidad legible (B, KB, MB, GB, TB, PB)."""
    if b is None:
        return vacio
    b = int(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.1f} {u}"
        b /= 1024
    return f"{b:.1f} PB"


def terminos_busqueda(q: str) -> list[str]:
    """Divide la consulta en términos usando . _ y espacio como separadores.

    Ej: 'LJA.Vengadores' -> ['LJA', 'Vengadores']
    """
    return [p for p in re.split(r"[._ ]+", q.strip()) if p]


def patron_busqueda(q: str) -> str:
    """Convierte separadores (. _ espacio) en comodines SQL.

    Ej: 'LJA.Vengadores' -> '%LJA%Vengadores%'
    """
    return "%" + "%".join(terminos_busqueda(q)) + "%"
