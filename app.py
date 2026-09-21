"""
app.py
======
Catálogo de Discos Externos — versión **portable** (Flet + SQLite).

Escanea unidades conectadas, guarda el índice en un archivo SQLite local y
permite navegar y buscar el catálogo. No necesita servidor, ni base de datos
externa, ni conexión a internet.
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime

import psutil
import flet as ft

import db
import config


ES_WINDOWS = sys.platform.startswith("win")
ES_MAC = sys.platform == "darwin"


def ruta_recurso(nombre):
    """Ruta a un recurso empaquetado (PyInstaller) o junto al script."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, nombre)


# ─────────────────────────────────────────────────────────────
#  PALETA Y CONSTANTES
# ─────────────────────────────────────────────────────────────

PALETA_OSCURA = {
    "BG": "#0d0f14",
    "SURFACE": "#141720",
    "CARD": "#1a1d2b",
    "BORDER": "#252840",
    "ACCENT": "#4f8aff",
    "ACCENT2": "#7c5cfc",
    "GREEN": "#3ddc84",
    "RED": "#ff4d6a",
    "TEXT": "#e8eaf0",
    "MUTED": "#5a5f7a",
    "SUBTEXT": "#aab4c8",
}

PALETA_CLARA = {
    "BG": "#eef1f7",
    "SURFACE": "#ffffff",
    "CARD": "#ffffff",
    "BORDER": "#d5dae6",
    "ACCENT": "#2f6fed",
    "ACCENT2": "#7c5cfc",
    "GREEN": "#1f9d55",
    "RED": "#d64560",
    "TEXT": "#161a24",
    "MUTED": "#6b7280",
    "SUBTEXT": "#4b5563",
}

BG = PALETA_OSCURA["BG"]
SURFACE = PALETA_OSCURA["SURFACE"]
CARD = PALETA_OSCURA["CARD"]
BORDER = PALETA_OSCURA["BORDER"]
ACCENT = PALETA_OSCURA["ACCENT"]
ACCENT2 = PALETA_OSCURA["ACCENT2"]
GREEN = PALETA_OSCURA["GREEN"]
RED = PALETA_OSCURA["RED"]
TEXT = PALETA_OSCURA["TEXT"]
MUTED = PALETA_OSCURA["MUTED"]
SUBTEXT = PALETA_OSCURA["SUBTEXT"]
MONO = "Courier New"
RESALTE_BG = "#ffd54a"  # amarillo para resaltar coincidencias de búsqueda
RESALTE_FG = "#1a1a1a"

FS_EXTERNOS = {"vfat", "ntfs", "exfat", "fuseblk", "hfs", "apfs"}
MONTAJES_EXTERNOS = ("/media/", "/run/media/", "/mnt/", "/Volumes/")


def aplicar_paleta(claro: bool):
    """Reasigna los colores globales según el tema seleccionado."""
    global BG, SURFACE, CARD, BORDER, ACCENT, ACCENT2, GREEN, RED, TEXT, MUTED, SUBTEXT
    p = PALETA_CLARA if claro else PALETA_OSCURA
    BG = p["BG"]
    SURFACE = p["SURFACE"]
    CARD = p["CARD"]
    BORDER = p["BORDER"]
    ACCENT = p["ACCENT"]
    ACCENT2 = p["ACCENT2"]
    GREEN = p["GREEN"]
    RED = p["RED"]
    TEXT = p["TEXT"]
    MUTED = p["MUTED"]
    SUBTEXT = p["SUBTEXT"]


# ─────────────────────────────────────────────────────────────
#  BASE DE DATOS (SQLite local)
# ─────────────────────────────────────────────────────────────

def conectar_bd():
    return db.conectar()


def inicializar_bd():
    db.inicializar_bd()


def listar_grupos():
    """Devuelve lista de grupos existentes ordenados."""
    conn = conectar_bd()
    c = conn.cursor()
    c.execute("SELECT DISTINCT grupo FROM escaneos WHERE grupo IS NOT NULL AND grupo != '' ORDER BY grupo")
    rows = c.fetchall()
    conn.close()
    return [r["grupo"] for r in rows]


def escaneo_por_uuid(uuid_hardware):
    """Escaneo previo de un disco por UUID, o None. Devuelve {id, nombre_personalizado}."""
    conn = conectar_bd()
    c = conn.cursor()
    c.execute("SELECT id, nombre_personalizado FROM escaneos WHERE uuid_hardware = ?",
              (uuid_hardware,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ─────────────────────────────────────────────────────────────
#  HELPERS Y EXPLORADOR
# ─────────────────────────────────────────────────────────────

def legible(b):
    return db.legible(b, vacio="—")


def obtener_arbol_disco(disco_id, ruta_relativa=""):
    """Devuelve subcarpetas y archivos directos para simular un explorador."""
    conn = conectar_bd()
    c = conn.cursor()
    c.execute("""
        SELECT ruta_relativa, tamano_bytes
        FROM archivos
        WHERE escaneo_id = ? AND ruta_relativa IS NOT NULL
    """, (disco_id,))
    filas = c.fetchall()

    prefix = (ruta_relativa + "/") if ruta_relativa else ""
    subcarpetas_map = {}

    for f in filas:
        rr = f["ruta_relativa"]
        if ruta_relativa and not rr.startswith(prefix):
            continue
        resto = rr[len(prefix):]
        if not resto:
            continue
        segmento = resto.split("/")[0]
        if segmento:
            clave = prefix + segmento if ruta_relativa else segmento
            if clave not in subcarpetas_map:
                subcarpetas_map[clave] = {"tamano": 0, "archivos": 0}
            subcarpetas_map[clave]["tamano"] += int(f["tamano_bytes"] or 0)
            subcarpetas_map[clave]["archivos"] += 1

    subcarpetas = [{"nombre": k.split("/")[-1], "ruta": k} for k in
                   sorted(subcarpetas_map.keys(), key=lambda x: x.lower())]

    c.execute("""
        SELECT nombre, tamano_bytes, extension, fecha_modificacion
        FROM archivos
        WHERE escaneo_id = ? AND ruta_relativa = ?
        ORDER BY nombre ASC
    """, (disco_id, ruta_relativa))
    archivos = c.fetchall()
    conn.close()

    return subcarpetas, archivos


# ─────────────────────────────────────────────────────────────
#  DETECCIÓN DE DISCOS (multiplataforma)
# ─────────────────────────────────────────────────────────────

def _info_volumen_windows(ruta):
    """Serial y etiqueta de un volumen en Windows (ctypes, sin dependencias)."""
    try:
        import ctypes
        from ctypes import wintypes

        volumen = ruta.rstrip("\\/") + "\\"
        max_len = 261
        nombre = ctypes.create_unicode_buffer(max_len)
        serial = wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(volumen),
            nombre, max_len,
            ctypes.byref(serial), None, None,
            None, 0,
        )
        if not ok:
            return None
        return {"serial": f"{serial.value:08X}", "label": nombre.value}
    except Exception:
        return None


def es_disco_externo(part):
    if ES_WINDOWS:
        mp = (part.mountpoint or "").strip()
        if not mp:
            return False
        sistema = (os.environ.get("SystemDrive", "C:") + "\\").lower()
        if mp.replace("/", "\\").lower() == sistema:
            return False
        return True
    if part.mountpoint in ("/", "/boot", "/boot/efi"):
        return False
    if part.mountpoint.startswith("/boot"):
        return False
    fs = (part.fstype or "").lower()
    return fs in FS_EXTERNOS or any(part.mountpoint.startswith(p) for p in MONTAJES_EXTERNOS)


def obtener_uuid(ruta):
    if ES_WINDOWS:
        info = _info_volumen_windows(ruta)
        if info and info.get("serial"):
            return f"WIN-{info['serial']}"
        return f"FALLBACK-{hashlib.md5(ruta.encode()).hexdigest()[:12].upper()}"
    try:
        out = subprocess.check_output(["findmnt", "-no", "UUID", ruta], text=True).strip()
        if out:
            return out
    except Exception:
        pass
    return f"FALLBACK-{hashlib.md5(ruta.encode()).hexdigest()[:12].upper()}"


def obtener_etiqueta(ruta):
    if ES_WINDOWS:
        info = _info_volumen_windows(ruta) or {}
        if info.get("label"):
            return info["label"]
        return os.path.basename(ruta.rstrip("\\/")) or "Sin_Etiqueta"
    try:
        out = subprocess.check_output(["findmnt", "-no", "LABEL", ruta], text=True).strip()
        if out:
            return out
    except Exception:
        pass
    base = os.path.basename(ruta.rstrip("/"))
    return base if base else "Sin_Etiqueta"


def listar_discos_externos():
    unidades = []
    for part in psutil.disk_partitions(all=False):
        if not es_disco_externo(part):
            continue
        ruta = part.mountpoint
        try:
            total, _, libre = shutil.disk_usage(ruta)
        except OSError:
            continue
        unidades.append({
            "ruta": ruta,
            "etiqueta": obtener_etiqueta(ruta),
            "uuid": obtener_uuid(ruta),
            "total": total,
            "libre": libre,
            "fs": part.fstype,
            "device": part.device,
        })
    return unidades


# ─────────────────────────────────────────────────────────────
#  CONSULTAS DEL CATÁLOGO
# ─────────────────────────────────────────────────────────────

def listar_catalogos():
    conn = conectar_bd()
    c = conn.cursor()
    c.execute("""
        SELECT e.id, e.nombre_personalizado, e.etiqueta_sistema,
               e.espacio_total_bytes, e.espacio_libre_bytes, e.fecha_escaneo,
               e.ruta_raiz, e.grupo,
               (SELECT COUNT(*) FROM archivos a WHERE a.escaneo_id = e.id) AS total_archivos
        FROM escaneos e
        ORDER BY e.fecha_escaneo DESC
    """)
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        r = dict(r)
        for col in ("espacio_total_bytes", "espacio_libre_bytes", "total_archivos"):
            try:
                r[col] = int(r[col]) if r[col] is not None else 0
            except (ValueError, TypeError):
                r[col] = 0
        fe = r.get("fecha_escaneo")
        if isinstance(fe, str) and fe:
            try:
                r["fecha_escaneo"] = datetime.fromisoformat(fe)
            except ValueError:
                r["fecha_escaneo"] = None
        result.append(r)
    return result


def buscar_archivos(q, grupo=None, limit=100):
    patron = db.patron_busqueda(q)
    conn = conectar_bd()
    c = conn.cursor()
    # LIKE en SQLite ya es insensible a mayúsculas para ASCII.
    coincide = "(a.nombre LIKE ? OR a.ruta_relativa LIKE ?)"
    if grupo:
        c.execute(f"""
            SELECT a.nombre, a.ruta_relativa, a.tamano_bytes, a.extension,
                   a.fecha_modificacion, e.nombre_personalizado AS disco
            FROM archivos a
            JOIN escaneos e ON e.id = a.escaneo_id
            WHERE e.grupo = ? AND {coincide}
            ORDER BY a.nombre
            LIMIT ?
        """, (grupo, patron, patron, limit))
    else:
        c.execute(f"""
            SELECT a.nombre, a.ruta_relativa, a.tamano_bytes, a.extension,
                   a.fecha_modificacion, e.nombre_personalizado AS disco
            FROM archivos a
            JOIN escaneos e ON e.id = a.escaneo_id
            WHERE {coincide}
            ORDER BY a.nombre
            LIMIT ?
        """, (patron, patron, limit))
    rows = c.fetchall()
    conn.close()
    return rows


def eliminar_catalogo(disco_id):
    conn = conectar_bd()
    c = conn.cursor()
    c.execute("DELETE FROM escaneos WHERE id = ?", (disco_id,))
    conn.commit()
    conn.close()


def estadisticas():
    conn = conectar_bd()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) AS n FROM escaneos")
    discos = c.fetchone()["n"]
    c.execute("SELECT COUNT(*) AS n FROM archivos")
    archivos = c.fetchone()["n"]
    conn.close()
    return discos, archivos


def escanear(unidad, nombre, scan_id_viejo, on_progress, on_done, on_error):
    """Recorre el disco y guarda el índice en SQLite (en el hilo actual)."""
    try:
        conn = conectar_bd()
    except Exception as e:
        on_error(str(e))
        return

    c = conn.cursor()
    nuevo_id = str(uuid.uuid4())
    try:
        if scan_id_viejo:
            c.execute("UPDATE escaneos SET uuid_hardware = NULL WHERE id = ?", (scan_id_viejo,))

        c.execute("""
            INSERT INTO escaneos
                (id, uuid_hardware, nombre_personalizado, etiqueta_sistema,
                 espacio_total_bytes, espacio_libre_bytes, fecha_escaneo, ruta_raiz, grupo)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (nuevo_id, unidad["uuid"], nombre, unidad["etiqueta"],
              unidad["total"], unidad["libre"], datetime.now().isoformat(),
              unidad["ruta"], unidad.get("grupo")))

        buffer, lote, contador, errores = [], 1000, 0, 0
        for raiz_actual, dirs, archivos in os.walk(unidad["ruta"]):
            for archivo in archivos:
                ruta_completa = os.path.join(raiz_actual, archivo)
                try:
                    stats = os.stat(ruta_completa)
                    rel = os.path.relpath(raiz_actual, unidad["ruta"])
                    rel = "" if rel == "." else rel
                    _, ext = os.path.splitext(archivo)
                    buffer.append((nuevo_id, rel, archivo, stats.st_size,
                                   datetime.fromtimestamp(stats.st_mtime).isoformat(), ext.lower()))
                    contador += 1
                    if len(buffer) >= lote:
                        db.insertar_archivos(c, buffer)
                        buffer = []
                        on_progress(contador)
                except (PermissionError, FileNotFoundError, OSError):
                    errores += 1
                    continue

        if buffer:
            db.insertar_archivos(c, buffer)

        if scan_id_viejo:
            c.execute("DELETE FROM escaneos WHERE id = ?", (scan_id_viejo,))

        conn.commit()
        on_done(contador)
    except Exception as e:
        conn.rollback()
        on_error(str(e))
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────
#  COMPONENTES UI
# ─────────────────────────────────────────────────────────────

def titulo(texto, size=18, color=None, weight=ft.FontWeight.W_600):
    return ft.Text(texto, size=size, color=color or TEXT, weight=weight, font_family=MONO)


def texto_resaltado(texto, terminos, **kwargs):
    """ft.Text con la frase buscada resaltada en amarillo.

    La frase abarca de forma contigua desde el primer término hasta el último
    encontrado (incluye los separadores intermedios). Si los términos no
    aparecen en orden, se resalta cada uno por separado.
    """
    if not terminos:
        return ft.Text(texto, **kwargs)
    frase = re.compile(".*?".join(re.escape(t) for t in terminos), re.IGNORECASE | re.DOTALL)
    coincidencias = list(frase.finditer(texto))
    if not coincidencias:
        alterna = re.compile("|".join(re.escape(t) for t in sorted(terminos, key=len, reverse=True)),
                             re.IGNORECASE)
        coincidencias = list(alterna.finditer(texto))
    if not coincidencias:
        return ft.Text(texto, **kwargs)
    spans, pos = [], 0
    for m in coincidencias:
        if m.start() > pos:
            spans.append(ft.TextSpan(texto[pos:m.start()]))
        spans.append(ft.TextSpan(
            m.group(0),
            style=ft.TextStyle(bgcolor=RESALTE_BG, color=RESALTE_FG, weight=ft.FontWeight.W_700),
        ))
        pos = m.end()
    if pos < len(texto):
        spans.append(ft.TextSpan(texto[pos:]))
    return ft.Text(spans=spans, **kwargs)


def badge(texto, color=None, size=11):
    color = color or ACCENT
    return ft.Container(
        ft.Text(texto, size=size, color=color, font_family=MONO),
        border=ft.Border(left=ft.BorderSide(1, color), right=ft.BorderSide(1, color), top=ft.BorderSide(1, color),
                         bottom=ft.BorderSide(1, color)),
        border_radius=4,
        padding=ft.Padding(left=8, right=8, top=2, bottom=2),
    )


def card(content, padding=16, on_click=None, ink=False):
    return ft.Container(
        content,
        bgcolor=CARD,
        border=ft.Border(left=ft.BorderSide(1, BORDER), right=ft.BorderSide(1, BORDER), top=ft.BorderSide(1, BORDER),
                         bottom=ft.BorderSide(1, BORDER)),
        border_radius=10,
        padding=padding,
        on_click=on_click,
        ink=ink,
    )


def divider():
    return ft.Divider(height=1, color=BORDER)


def snack(page, msg, color=None):
    page.snack_bar = ft.SnackBar(
        ft.Text(msg, color=color or TEXT, font_family=MONO),
        bgcolor=CARD,
        duration=3000,
    )
    page.snack_bar.open = True
    page.update()


def abrir_carpeta(ruta):
    """Abre una carpeta en el explorador del sistema."""
    try:
        if ES_WINDOWS:
            os.startfile(ruta)  # type: ignore[attr-defined]
        elif ES_MAC:
            subprocess.Popen(["open", ruta])
        else:
            subprocess.Popen(["xdg-open", ruta])
    except Exception as e:
        print(f"No se pudo abrir la carpeta: {e}")


# ─────────────────────────────────────────────────────────────
#  VISTAS
# ─────────────────────────────────────────────────────────────

def vista_datos(page: ft.Page):
    """Información local: dónde vive el catálogo y cuánto ocupa."""
    ruta = config.ruta_bd()
    try:
        discos, archivos = estadisticas()
        error = None
    except Exception as ex:
        discos, archivos, error = 0, 0, str(ex)

    tamaño = legible(ruta.stat().st_size) if ruta.exists() else "—"

    filas = [
        ("Ubicación", str(ruta)),
        ("Discos catalogados", f"{discos:,}"),
        ("Archivos indexados", f"{archivos:,}"),
        ("Tamaño de la base", tamaño),
        ("Sistema", "Windows" if ES_WINDOWS else ("macOS" if ES_MAC else "Linux")),
    ]

    info = ft.Column([
        ft.Row([
            ft.Text(etiqueta, color=SUBTEXT, size=12, font_family=MONO, width=170),
            ft.Text(valor, color=TEXT, size=13, font_family=MONO, selectable=True, expand=True,
                    overflow=ft.TextOverflow.ELLIPSIS),
        ], spacing=10)
        for etiqueta, valor in filas
    ], spacing=10)

    def copiar_ruta(e):
        page.set_clipboard(str(ruta))
        snack(page, "Ruta copiada al portapapeles", GREEN)

    return ft.Column([
        titulo("DATOS Y ALMACENAMIENTO", 16),
        divider(),
        ft.Text("Esta versión guarda todo en tu equipo: no usa servidor ni internet.",
                color=SUBTEXT, font_family=MONO, size=12),
        card(info),
        ft.Row([
            ft.Button("ABRIR CARPETA DE DATOS", icon=ft.Icons.FOLDER_OPEN,
                      on_click=lambda e: abrir_carpeta(str(ruta.parent)),
                      style=ft.ButtonStyle(bgcolor=ACCENT, color=TEXT,
                                           shape=ft.RoundedRectangleBorder(radius=8))),
            ft.OutlinedButton("COPIAR RUTA", icon=ft.Icons.CONTENT_COPY, on_click=copiar_ruta,
                              style=ft.ButtonStyle(side=ft.BorderSide(1, SUBTEXT), color=SUBTEXT,
                                                   shape=ft.RoundedRectangleBorder(radius=8))),
        ], spacing=12),
        ft.Text(error or "", color=RED, font_family=MONO, size=12) if error else ft.Container(),
        ft.Container(expand=True),
    ], spacing=14, expand=True)


def vista_escanear(page: ft.Page, on_refresh):
    unidades = []
    disco_sel = {"idx": None}
    nombre_ctrl = ft.TextField(
        label="Nombre descriptivo",
        bgcolor=SURFACE,
        border_color=BORDER,
        focused_border_color=ACCENT,
        color=TEXT,
        label_style=ft.TextStyle(color=SUBTEXT, font_family=MONO),
        text_style=ft.TextStyle(color=TEXT, font_family=MONO),
        visible=False,
    )
    progress_txt = ft.Text("", color=SUBTEXT, size=13, font_family=MONO)
    progress_bar = ft.ProgressBar(color=ACCENT, bgcolor=BORDER, visible=False, width=float("inf"))
    btn_escanear = ft.Button(
        "ESCANEAR",
        icon=ft.Icons.RADAR,
        disabled=True,
        style=ft.ButtonStyle(
            bgcolor=ACCENT, color=TEXT,
            shape=ft.RoundedRectangleBorder(radius=8)
        ),
    )
    btn_ver_catalogo = ft.Button(
        "VER CATÁLOGOS",
        icon=ft.Icons.STORAGE,
        visible=False,
        style=ft.ButtonStyle(
            bgcolor=ACCENT2, color=TEXT,
            shape=ft.RoundedRectangleBorder(radius=8)
        ),
        on_click=lambda e: on_refresh(),
    )
    grupo_seleccionado = {"valor": None}

    def build_dd_grupo():
        try:
            grupos = sorted(listar_grupos(), key=lambda x: x.lower())
        except Exception:
            grupos = []
        return [ft.dropdown.Option(key="", text="Sin grupo")] + [
            ft.dropdown.Option(key=g, text=g) for g in grupos
        ]

    dd_grupo = ft.Dropdown(
        label="Grupo",
        options=build_dd_grupo(),
        value="",
        expand=True,
        bgcolor=SURFACE, border_color=BORDER, focused_border_color=ACCENT2,
        color=TEXT, label_style=ft.TextStyle(color=SUBTEXT, font_family=MONO),
        text_style=ft.TextStyle(font_family=MONO, color=TEXT),
        visible=False,
    )

    lista_discos = ft.Column(spacing=8)
    estado_txt = ft.Text("", color=SUBTEXT, size=12, font_family=MONO)

    def refrescar_discos(e=None):
        nonlocal unidades
        disco_sel["idx"] = None
        nombre_ctrl.visible = False
        btn_escanear.disabled = True
        lista_discos.controls.clear()
        estado_txt.value = "Detectando discos..."
        page.update()
        try:
            unidades = listar_discos_externos()
        except Exception as ex:
            estado_txt.value = f"Error: {ex}"
            page.update()
            return

        if not unidades:
            estado_txt.value = "No se detectaron discos externos."
            page.update()
            return

        estado_txt.value = f"{len(unidades)} disco(s) detectado(s)"
        for i, u in enumerate(unidades):
            idx = i

            def hacer_click(e, i=idx):
                disco_sel["idx"] = i
                u = unidades[i]
                try:
                    row = escaneo_por_uuid(u["uuid"])
                    nombre_ctrl.value = row["nombre_personalizado"] if row else ""
                except Exception:
                    nombre_ctrl.value = ""
                nombre_ctrl.visible = False
                btn_escanear.disabled = False
                btn_escanear.text = "ESCANEAR"
                btn_escanear.icon = ft.Icons.RADAR
                btn_escanear.style = ft.ButtonStyle(
                    bgcolor=ACCENT, color=TEXT,
                    shape=ft.RoundedRectangleBorder(radius=8)
                )
                btn_ver_catalogo.visible = False
                page.update()

            pct_libre = int((u["libre"] / u["total"] * 100)) if u["total"] else 0
            tile = card(
                ft.Column([
                    ft.Row([
                        ft.Icon(ft.Icons.USB, color=ACCENT, size=20),
                        ft.Text(u["device"], color=TEXT, size=14, font_family=MONO, weight=ft.FontWeight.W_600),
                        badge(u["fs"].upper(), ACCENT2),
                        ft.Text(u["etiqueta"], color=SUBTEXT, size=12, font_family=MONO),
                    ], spacing=8),
                    ft.Row([
                        ft.Text(f"📂 {u['ruta']}", color=SUBTEXT, size=12, font_family=MONO),
                    ]),
                    ft.Row([
                        ft.Text(f"💾 {legible(u['libre'])} libres / {legible(u['total'])}", color=TEXT, size=12,
                                font_family=MONO),
                        ft.Text(f"({pct_libre}% libre)", color=GREEN if pct_libre > 20 else RED, size=12,
                                font_family=MONO),
                    ], spacing=8),
                ], spacing=4),
                padding=12,
                on_click=hacer_click,
                ink=True
            )
            lista_discos.controls.append(tile)
        page.update()

    def do_escanear(e):
        idx = disco_sel["idx"]
        if idx is None:
            return

        u = unidades[idx]
        nombre_previo = nombre_ctrl.value.strip() or f"Disco_{u['etiqueta']}_{datetime.now().strftime('%Y%m%d')}"
        tf_nombre = ft.TextField(
            label="Nombre descriptivo",
            value=nombre_previo,
            hint_text=nombre_previo,
            bgcolor=SURFACE, border_color=BORDER, focused_border_color=ACCENT,
            color=TEXT,
            label_style=ft.TextStyle(color=SUBTEXT, font_family=MONO),
            text_style=ft.TextStyle(color=TEXT, font_family=MONO),
        )
        try:
            grupos_existentes = sorted(listar_grupos(), key=lambda x: x.lower())
        except Exception:
            grupos_existentes = []

        dd = ft.Dropdown(
            label="Grupo (opcional)",
            options=[ft.dropdown.Option(key="", text="Sin grupo")] +
                    [ft.dropdown.Option(key=g, text=g) for g in grupos_existentes],
            value="",
            bgcolor=SURFACE, border_color=BORDER, focused_border_color=ACCENT2,
            color=TEXT,
            label_style=ft.TextStyle(color=SUBTEXT, font_family=MONO),
            text_style=ft.TextStyle(font_family=MONO, color=TEXT),
            expand=True,
        )
        tf_nuevo_grupo = ft.TextField(
            label="O escribe un grupo nuevo",
            bgcolor=SURFACE, border_color=ACCENT2, focused_border_color=ACCENT2,
            color=TEXT,
            label_style=ft.TextStyle(color=ACCENT2, font_family=MONO),
            text_style=ft.TextStyle(color=TEXT, font_family=MONO),
        )

        def confirmar(e):
            page.pop_dialog()
            nombre_final = tf_nombre.value.strip() or f"Disco_{u['etiqueta']}_{datetime.now().strftime('%Y%m%d')}"
            nombre_ctrl.value = nombre_final
            grupo_val = tf_nuevo_grupo.value.strip() or dd.value or None
            iniciar_escaneo_real(nombre_final, grupo_val)

        def sin_grupo(e):
            page.pop_dialog()
            nombre_final = tf_nombre.value.strip() or f"Disco_{u['etiqueta']}_{datetime.now().strftime('%Y%m%d')}"
            nombre_ctrl.value = nombre_final
            iniciar_escaneo_real(nombre_final, None)

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row([
                ft.Icon(ft.Icons.STORAGE, color=ACCENT, size=20),
                ft.Text(f"{u['etiqueta'] or u['device']}", color=TEXT,
                        font_family=MONO, weight=ft.FontWeight.W_600),
            ], spacing=8),
            content=ft.Column([
                tf_nombre,
                ft.Divider(height=1, color=BORDER),
                ft.Text("Grupo", color=SUBTEXT, font_family=MONO, size=12),
                dd,
                tf_nuevo_grupo,
            ], spacing=10, tight=True, width=400),
            actions=[
                ft.OutlinedButton("Sin grupo", on_click=sin_grupo,
                                  style=ft.ButtonStyle(side=ft.BorderSide(1, SUBTEXT), color=SUBTEXT,
                                                       shape=ft.RoundedRectangleBorder(radius=6))),
                ft.Button("Escanear", icon=ft.Icons.RADAR, on_click=confirmar,
                          style=ft.ButtonStyle(bgcolor=ACCENT, color=TEXT,
                                               shape=ft.RoundedRectangleBorder(radius=6))),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
            bgcolor=CARD,
        )
        page.show_dialog(dlg)

    def iniciar_escaneo_real(nombre, grupo_val):
        idx = disco_sel["idx"]
        u = unidades[idx]
        u["grupo"] = grupo_val

        try:
            row = escaneo_por_uuid(u["uuid"])
            scan_id_viejo = row["id"] if row else None
        except Exception as ex:
            snack(page, f"Error: {ex}", RED)
            return

        btn_escanear.disabled = True
        progress_bar.visible = True
        progress_txt.value = "Iniciando escaneo..."
        page.update()

        def on_progress(n):
            progress_txt.value = f"Archivos procesados: {n:,}..."
            page.update()

        def on_done(n):
            progress_bar.visible = False
            progress_txt.value = f"✅ Completado: {n:,} archivos guardados."
            btn_escanear.disabled = False
            btn_escanear.text = "ESCANEAR OTRO"
            btn_escanear.icon = ft.Icons.CHECK_CIRCLE
            btn_escanear.style = ft.ButtonStyle(
                bgcolor=GREEN, color=TEXT,
                shape=ft.RoundedRectangleBorder(radius=8)
            )
            btn_ver_catalogo.visible = True
            page.update()

            def ir_catalogo(e):
                page.pop_dialog()
                on_refresh()

            def seguir_escaneando(e):
                page.pop_dialog()
                btn_escanear.text = "ESCANEAR"
                btn_escanear.icon = ft.Icons.RADAR
                btn_escanear.style = ft.ButtonStyle(
                    bgcolor=ACCENT, color=TEXT,
                    shape=ft.RoundedRectangleBorder(radius=8)
                )
                btn_escanear.disabled = True
                btn_ver_catalogo.visible = False
                progress_txt.value = ""
                dd_grupo.options = build_dd_grupo()
                dd_grupo.value = ""
                dd_grupo.visible = False
                nombre_ctrl.visible = False
                nombre_ctrl.value = ""
                disco_sel["idx"] = None
                page.update()

            dlg_done = ft.AlertDialog(
                modal=True,
                title=ft.Row([
                    ft.Icon(ft.Icons.CHECK_CIRCLE, color=GREEN, size=24),
                    ft.Text("¡Escaneo completado!", color=TEXT, font_family=MONO),
                ], spacing=10),
                content=ft.Text(
                    f"'{nombre}' — {n:,} archivos guardados correctamente.",
                    color=SUBTEXT, font_family=MONO
                ),
                actions=[
                    ft.OutlinedButton("Seguir escaneando", icon=ft.Icons.RADAR, on_click=seguir_escaneando),
                    ft.Button("Ir al catálogo", icon=ft.Icons.STORAGE, on_click=ir_catalogo),
                ],
                bgcolor=CARD,
            )
            page.run_thread(lambda: page.show_dialog(dlg_done))

        def on_error(msg):
            progress_bar.visible = False
            progress_txt.value = f"❌ Error: {msg}"
            btn_escanear.disabled = False
            page.update()
            snack(page, f"❌ {msg}", RED)

        threading.Thread(
            target=escanear,
            args=(u, nombre, scan_id_viejo, on_progress, on_done, on_error),
            daemon=True
        ).start()

    btn_escanear.on_click = do_escanear
    btn_refrescar = ft.OutlinedButton("DETECTAR DISCOS", icon=ft.Icons.REFRESH, on_click=refrescar_discos)
    refrescar_discos()

    return ft.Column([
        ft.Row([titulo("ESCANEAR DISCO", 16), ft.Container(expand=True), btn_refrescar],
               alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        estado_txt,
        divider(),
        lista_discos,
        progress_bar,
        progress_txt,
        ft.Row([btn_escanear, btn_ver_catalogo], spacing=12),
    ], spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)


def vista_catalogos(page: ft.Page, on_refresh):
    estado_nav = {
        "vista": "grupos",
        "grupo": None,
        "disco": None,
        "ruta_carpeta": ""
    }

    contenido = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

    def render(e=None):
        contenido.controls.clear()
        v = estado_nav["vista"]

        try:
            catalogos = listar_catalogos()
        except Exception as ex:
            contenido.controls.append(ft.Text(f"⚠️ Error leyendo la base: {ex}", color=RED, font_family=MONO, size=13))
            page.update()
            return

        if v == "grupos":
            if not catalogos:
                contenido.controls.append(ft.Text("No hay discos catalogados.", color=SUBTEXT, font_family=MONO))
                page.update()
                return

            from collections import defaultdict
            grupos = defaultdict(list)
            for r in catalogos:
                g = r.get("grupo") or "Sin grupo"
                grupos[g].append(r)

            grupos_ordenados = sorted(grupos.items(), key=lambda x: (x[0] == "Sin grupo", x[0].lower()))

            for g, disks in grupos_ordenados:
                def click_grupo(e, grupo_nom=g):
                    estado_nav["vista"] = "discos"
                    estado_nav["grupo"] = grupo_nom
                    render()

                contenido.controls.append(
                    card(
                        ft.Row([
                            ft.Icon(ft.Icons.FOLDER_SPECIAL, color=ACCENT2, size=24),
                            ft.Column([
                                ft.Text(g.upper(), color=TEXT, size=15, weight=ft.FontWeight.W_700, font_family=MONO),
                                ft.Text(f"{len(disks)} disco(s) catalogado(s)", color=SUBTEXT, size=12, font_family=MONO),
                            ], spacing=2, expand=True),
                            ft.Icon(ft.Icons.CHEVRON_RIGHT, color=SUBTEXT),
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        padding=14,
                        on_click=click_grupo,
                        ink=True
                    )
                )

        elif v == "discos":
            g_sel = estado_nav["grupo"]
            disks_grupo = sorted(
                [r for r in catalogos if (r.get("grupo") or "Sin grupo") == g_sel],
                key=lambda x: (x.get("nombre_personalizado") or "").lower()
            )

            def volver_grupos(e):
                estado_nav["vista"] = "grupos"
                estado_nav["grupo"] = None
                render()

            contenido.controls.append(
                ft.Row([
                    ft.OutlinedButton("Atrás", icon=ft.Icons.ARROW_BACK, on_click=volver_grupos,
                                      style=ft.ButtonStyle(side=ft.BorderSide(1, SUBTEXT), color=SUBTEXT,
                                                           shape=ft.RoundedRectangleBorder(radius=6))),
                    titulo(f"GRUPO: {g_sel.upper()}", 15)
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
            )
            contenido.controls.append(divider())

            for row in disks_grupo:
                disco_id = row["id"]

                def click_disco(e, r=row):
                    estado_nav["vista"] = "explorador"
                    estado_nav["disco"] = r
                    estado_nav["ruta_carpeta"] = ""
                    render()

                def confirmar_eliminar(e, did=disco_id, nombre=row["nombre_personalizado"]):
                    def do_eliminar(e):
                        page.pop_dialog()
                        try:
                            eliminar_catalogo(did)
                            snack(page, f"🗑 '{nombre}' eliminado.")
                            render()
                            on_refresh()
                        except Exception as ex:
                            snack(page, f"Error: {ex}", RED)

                    def cancelar(e):
                        page.pop_dialog()

                    dlg = ft.AlertDialog(
                        modal=True,
                        title=ft.Text("¿Eliminar catálogo?", color=TEXT, font_family=MONO),
                        content=ft.Text(f"Se eliminarán todos los registros de '{nombre}'.", color=SUBTEXT,
                                        font_family=MONO),
                        actions=[ft.OutlinedButton("Cancelar", on_click=cancelar),
                                 ft.Button("Eliminar", on_click=do_eliminar)],
                        bgcolor=CARD,
                    )
                    page.show_dialog(dlg)

                fecha = row["fecha_escaneo"].strftime("%Y-%m-%d %H:%M") if row["fecha_escaneo"] else "—"
                pct = int((row["espacio_libre_bytes"] or 0) / (row["espacio_total_bytes"] or 1) * 100)

                tile_disk = card(
                    ft.Column([
                        ft.Row([
                            ft.Icon(ft.Icons.STORAGE, color=ACCENT, size=22),
                            ft.Text(row["nombre_personalizado"] or "—", color=TEXT, size=15, weight=ft.FontWeight.W_600,
                                    font_family=MONO, expand=True),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, icon_color=RED, on_click=confirmar_eliminar,
                                          tooltip="Eliminar catálogo"),
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        ft.Row([
                            badge(row["etiqueta_sistema"] or "—", SUBTEXT),
                            ft.Text(f"📅 {fecha}", color=SUBTEXT, size=12, font_family=MONO),
                            ft.Text(f"📄 {row['total_archivos']:,} archivos", color=TEXT, size=12, font_family=MONO),
                        ], spacing=10, wrap=True),
                        ft.Row([
                            ft.Text(
                                f"💾 {legible(row['espacio_libre_bytes'])} libres / {legible(row['espacio_total_bytes'])}",
                                color=TEXT, size=12, font_family=MONO),
                            ft.Text(f"({pct}% libre)", color=GREEN if pct > 20 else RED, size=12, font_family=MONO),
                        ], spacing=8),
                    ], spacing=6),
                    on_click=click_disco,
                    ink=True
                )

                contenido.controls.append(tile_disk)

        elif v == "explorador":
            d = estado_nav["disco"]
            ruta_act = estado_nav["ruta_carpeta"]
            did = d["id"]

            def volver_discos(e):
                if ruta_act == "":
                    estado_nav["vista"] = "discos"
                    estado_nav["disco"] = None
                else:
                    partes = ruta_act.split("/")
                    estado_nav["ruta_carpeta"] = "/".join(partes[:-1])
                render()

            subcarpetas, archivos = obtener_arbol_disco(did, ruta_act)
            breadcrumb = "/" + ruta_act if ruta_act else "/"

            contenido.controls.append(
                ft.Row([
                    ft.OutlinedButton("Atrás", icon=ft.Icons.ARROW_BACK, on_click=volver_discos,
                                      style=ft.ButtonStyle(side=ft.BorderSide(1, SUBTEXT), color=SUBTEXT,
                                                           shape=ft.RoundedRectangleBorder(radius=6))),
                    ft.Text(f"💽 {d['nombre_personalizado']} {breadcrumb}", color=TEXT, size=13, font_family=MONO,
                            weight=ft.FontWeight.W_600, expand=True, overflow=ft.TextOverflow.ELLIPSIS)
                ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN)
            )
            contenido.controls.append(divider())

            if not subcarpetas and not archivos:
                contenido.controls.append(ft.Text("Carpeta vacía.", color=SUBTEXT, font_family=MONO))

            for sub in subcarpetas:
                nombre_sub = sub["nombre"]
                ruta_sub = sub["ruta"]

                def click_carpeta(e, rs=ruta_sub):
                    estado_nav["ruta_carpeta"] = rs
                    render()

                contenido.controls.append(
                    card(
                        ft.Row([
                            ft.Icon(ft.Icons.FOLDER, color=ACCENT2, size=20),
                            ft.Text(nombre_sub, color=TEXT, size=13, font_family=MONO, weight=ft.FontWeight.W_500,
                                    expand=True),
                            ft.Icon(ft.Icons.CHEVRON_RIGHT, color=SUBTEXT, size=16),
                        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                        padding=10,
                        on_click=click_carpeta,
                        ink=True
                    )
                )

            for f in archivos:
                ext = (f.get("extension") or "").upper().lstrip(".")
                contenido.controls.append(
                    card(ft.Column([
                        ft.Row([
                            badge(ext or "—", ACCENT),
                            ft.Text(f["nombre"], color=TEXT, size=13, font_family=MONO, weight=ft.FontWeight.W_400,
                                    expand=True),
                            ft.Text(legible(f.get("tamano_bytes")), color=SUBTEXT, size=12, font_family=MONO),
                        ], spacing=8),
                    ], spacing=2), padding=10)
                )

        page.update()

    render()

    btn_refrescar = ft.OutlinedButton("ACTUALIZAR", icon=ft.Icons.REFRESH, on_click=render)

    col = ft.Column([
        ft.Row([titulo("CATÁLOGOS", 16), ft.Container(expand=True), btn_refrescar],
               alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        divider(),
        contenido,
    ], spacing=12, expand=True)

    col.cargar = render
    return col


def vista_buscar(page: ft.Page):
    resultados = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)
    estado_txt = ft.Text("", color=SUBTEXT, size=12, font_family=MONO)

    try:
        grupos = sorted(listar_grupos(), key=lambda x: x.lower())
        opciones = [ft.dropdown.Option(key="", text="Todos los grupos")] + [
            ft.dropdown.Option(key=g, text=g) for g in grupos
        ]
    except Exception:
        opciones = [ft.dropdown.Option(key="", text="Todos los grupos")]

    dd_grupo = ft.Dropdown(options=opciones, value="", bgcolor=SURFACE, border_color=BORDER, color=TEXT, width=220,
                           label="Grupo")
    campo = ft.TextField(hint_text="Buscar archivos...", prefix_icon=ft.Icons.SEARCH, bgcolor=SURFACE,
                         border_color=BORDER, color=TEXT, expand=True)

    def buscar(e=None):
        q = campo.value.strip()
        terminos = db.terminos_busqueda(q)
        resultados.controls.clear()
        if len(q) < 2:
            estado_txt.value = "Escribe al menos 2 caracteres."
            page.update()
            return

        estado_txt.value = "Buscando..."
        page.update()

        try:
            grupo_sel = dd_grupo.value or None
            rows = buscar_archivos(q, grupo=grupo_sel)
        except Exception as ex:
            estado_txt.value = f"Error: {ex}"
            page.update()
            return

        if not rows:
            estado_txt.value = f'Sin resultados para "{q}"'
            page.update()
            return

        estado_txt.value = f"{len(rows)} resultado(s)"
        for row in rows:
            ext = (row.get("extension") or "").upper().lstrip(".")
            disco = row.get("disco", "")
            ruta = row.get("ruta_relativa") or "/"
            resultados.controls.append(
                ft.Container(
                    ft.Column([
                        ft.Row([
                            badge(ext or "—", ACCENT2),
                            texto_resaltado(row["nombre"], terminos, color=TEXT, size=14, font_family=MONO,
                                            weight=ft.FontWeight.W_500, expand=True,
                                            overflow=ft.TextOverflow.ELLIPSIS),
                            ft.Text(legible(row.get("tamano_bytes")), color=SUBTEXT, size=12, font_family=MONO),
                        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                        ft.Row([
                            ft.Icon(ft.Icons.FOLDER_OPEN, size=14, color=SUBTEXT),
                            texto_resaltado(ruta, terminos, color=SUBTEXT, size=12, font_family=MONO,
                                            expand=True, overflow=ft.TextOverflow.ELLIPSIS),
                            badge(disco, ACCENT, 12) if disco else ft.Container(),
                        ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    ], spacing=4),
                    bgcolor=CARD, border=ft.Border(left=ft.BorderSide(1, BORDER), right=ft.BorderSide(1, BORDER),
                                                   top=ft.BorderSide(1, BORDER), bottom=ft.BorderSide(1, BORDER)),
                    border_radius=8, padding=12,
                )
            )
        page.update()

    campo.on_submit = buscar
    btn_buscar = ft.IconButton(ft.Icons.SEARCH, icon_color=ACCENT, on_click=buscar)

    return ft.Column([
        titulo("BUSCAR ARCHIVOS", 16),
        divider(),
        ft.Row([campo, dd_grupo, btn_buscar], spacing=8),
        estado_txt,
        resultados,
    ], spacing=12, expand=True)


# ─────────────────────────────────────────────────────────────
#  APP PRINCIPAL
# ─────────────────────────────────────────────────────────────

def main(page: ft.Page):
    page.title = "Catálogo de Discos"
    page.padding = 0
    page.window.maximized = True

    icono_ventana = ruta_recurso("icon.png")
    if os.path.exists(icono_ventana):
        page.window.icon = icono_ventana

    estado = {"tema": "oscuro", "vista": 0}
    aplicar_paleta(False)
    page.bgcolor = BG
    page.theme = ft.Theme(color_scheme_seed=ACCENT)
    page.theme_mode = ft.ThemeMode.DARK

    try:
        inicializar_bd()
    except Exception as e:
        print(f"Advertencia: no se pudo preparar la base local: {e}")

    area = ft.Column(expand=True)

    def construir_vista(idx):
        if idx == 0:
            return vista_escanear(page, lambda: ir_a(1))
        if idx == 1:
            return vista_catalogos(page, lambda: ir_a(1))
        if idx == 2:
            return vista_buscar(page)
        return vista_datos(page)

    def ir_a(idx):
        estado["vista"] = idx
        nav.selected_index = idx
        area.controls.clear()
        area.controls.append(construir_vista(idx))
        page.update()

    def alternar_tema():
        estado["tema"] = "claro" if estado["tema"] == "oscuro" else "oscuro"
        claro = estado["tema"] == "claro"
        aplicar_paleta(claro)
        page.theme = ft.Theme(color_scheme_seed=ACCENT)
        page.theme_mode = ft.ThemeMode.LIGHT if claro else ft.ThemeMode.DARK
        pintar_layout()
        ir_a(estado["vista"])

    # ── Layout raíz (se construye una sola vez) ──
    icono_app = ft.Icon(ft.Icons.DISC_FULL, color=ACCENT, size=22)
    titulo_app = ft.Text("CATÁLOGO DE DISCOS", color=TEXT, size=15,
                         weight=ft.FontWeight.W_700, font_family=MONO)
    btn_tema = ft.IconButton(ft.Icons.LIGHT_MODE, icon_color=ACCENT,
                             tooltip="Cambiar tema claro/oscuro",
                             on_click=lambda e: alternar_tema())

    nav = ft.NavigationRail(
        selected_index=0,
        label_type=ft.NavigationRailLabelType.ALL,
        bgcolor=SURFACE,
        indicator_color=ft.Colors.with_opacity(0.15, ACCENT),
        destinations=[
            ft.NavigationRailDestination(icon=ft.Icons.RADAR_OUTLINED, selected_icon=ft.Icons.RADAR,
                                         label="Escanear"),
            ft.NavigationRailDestination(icon=ft.Icons.STORAGE_OUTLINED, selected_icon=ft.Icons.STORAGE,
                                         label="Catálogos"),
            ft.NavigationRailDestination(icon=ft.Icons.SEARCH_OUTLINED, selected_icon=ft.Icons.SEARCH,
                                         label="Buscar"),
            ft.NavigationRailDestination(icon=ft.Icons.FOLDER_OUTLINED, selected_icon=ft.Icons.FOLDER,
                                         label="Datos"),
        ],
        on_change=lambda e: ir_a(e.control.selected_index),
    )

    header = ft.Container(
        ft.Row([
            icono_app,
            titulo_app,
            ft.Container(expand=True),
            btn_tema,
        ], spacing=10),
        bgcolor=SURFACE,
        padding=ft.Padding(left=20, right=20, top=14, bottom=14),
        border=ft.Border(bottom=ft.BorderSide(1, BORDER)),
    )

    div_vert = ft.VerticalDivider(width=1, color=BORDER)
    contenido = ft.Container(area, padding=20, expand=True)

    def pintar_layout():
        page.bgcolor = BG
        icono_app.color = ACCENT
        titulo_app.color = TEXT
        btn_tema.icon_color = ACCENT
        btn_tema.icon = ft.Icons.LIGHT_MODE if estado["tema"] == "oscuro" else ft.Icons.DARK_MODE
        nav.bgcolor = SURFACE
        nav.indicator_color = ft.Colors.with_opacity(0.15, ACCENT)
        header.bgcolor = SURFACE
        header.border = ft.Border(bottom=ft.BorderSide(1, BORDER))
        div_vert.color = BORDER

    page.add(
        ft.Column([
            header,
            ft.Row([
                nav,
                div_vert,
                contenido,
            ], expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.START),
        ], spacing=0, expand=True)
    )

    pintar_layout()
    ir_a(0)


if __name__ == "__main__":
    ft.run(main)
