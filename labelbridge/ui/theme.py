"""Tema visual de LabelBridge: oscuro con acentos verde neón.

Fondo casi negro con un toque verde, tarjetas un poco más claras y texto claro; el verde neón marca lo importante (el
botón principal, la pestaña activa, el estado y los gráficos) y brilla apenas en lo que está activo. Nada de cajas con
borde: los grupos se separan con espacio y líneas finas, y los avisos llevan una barra de color a la izquierda.
Todos los colores salen de los tokens de abajo: ninguna pantalla debería escribir un color a mano (así un cambio de
diseño se hace en un solo lugar). El dorado queda para la marca (logo y mascota).
"""

from __future__ import annotations


class Color:
    """Tokens de color. Texto ≥ 4.5:1 sobre las superficies (salvo el de deshabilitado); el neón ≥ 3:1 como gráfico."""

    BG = "#0A0F0D"  # fondo de la ventana
    SURFACE = "#111915"  # tarjetas y tablas
    SURFACE_RAISED = "#16201B"  # campos, menús y burbujas (sobre una tarjeta)
    SURFACE_HOVER = "#18231D"
    SURFACE_PRESSED = "#1D2A23"
    SURFACE_SELECTED = "#1C2C24"
    NAV = "#0D1411"  # menú lateral y barra inferior
    NAV_HOVER = "#16201B"
    TEXT = "#DCE7E0"
    TEXT_STRONG = "#F4FBF7"
    TEXT_SECONDARY = "#93A69A"
    TEXT_DISABLED = "#56655C"
    ICON = "#AFC2B6"
    DIVIDER = "#202C26"
    INPUT_BORDER = "#2D3D34"
    INPUT_BG = "#0D1411"
    FOCUS = "#3DFF9A"
    PRIMARY = "#2BF08C"  # el verde neón: botones principales, interruptores, casillas, gráficos
    PRIMARY_HOVER = "#5BFFAA"
    PRIMARY_PRESSED = "#1FC872"
    PRIMARY_DISABLED = "#22352B"
    ON_PRIMARY = "#03160C"  # texto e iconos sobre el neón
    NEON = PRIMARY
    NEON_SOFT = "#0F3324"  # fondo verde suave (pestaña activa, selección)
    SELECTION = "#1F5A3D"
    GOLD = "#D4A84B"  # la marca: logo, mascota y algún detalle
    GOLD_SOFT = "#2A2112"
    SUCCESS = "#2BE283"  # puntos de estado
    WARNING = "#FFC53D"
    CRITICAL = "#FF5A6B"
    INFO = "#59A8FF"
    CODE_BG = "#070B09"
    CODE_TEXT = "#C8F7DA"
    CARRIERS = {  # (fondo, texto) de la etiqueta de cada paquetería
        "USPS": ("#0E2A4A", "#9CC9FF"),
        "FedEx": ("#2A1A4A", "#CDB0FF"),
        "UPS": ("#3A2A10", "#F2C27A"),
        "DHL": ("#3A3410", "#FFE36E"),
    }


class Font:
    TITLE = 20
    SECTION = 14
    BODY = 13
    SMALL = 12
    TINY = 11


# Estados (fondo, texto) para etiquetas de estado. Fondos suaves, sin borde.
STATE_COLORS = {
    "ok": ("#0F3324", "#7DFFB9"),
    "warn": ("#3D2F0A", "#FFD580"),
    "connecting": ("#0E2A47", "#9CCBFF"),
    "info": ("#0E2A47", "#9CCBFF"),
    "error": ("#46121A", "#FF9AA5"),
    "off": ("#1E2722", "#A8B5AD"),
}
# El color del punto de cada estado (indicador de estado, filas de pedidos).
STATE_DOTS = {"ok": Color.SUCCESS, "warn": Color.WARNING, "connecting": Color.INFO, "info": Color.INFO,
              "error": Color.CRITICAL, "off": "#6B7C72"}

# Nombres de antes (las pantallas los importan): apuntan a los tokens nuevos.
ACCENT = Color.PRIMARY
ACCENT_HOVER = Color.PRIMARY_HOVER
TEXT = Color.TEXT
MUTED = Color.TEXT_SECONDARY
BORDER = Color.DIVIDER
BG = Color.BG
SIDEBAR = Color.NAV

JOB_STATE = {"queued": "info", "printing": "warn", "done": "ok", "failed": "error", "canceled": "off"}


def job_state(job: dict) -> str:
    if job.get("status") == "queued" and job.get("error"):
        return "warn"
    return JOB_STATE.get(job.get("status", ""), "off")


C = Color
STYLESHEET = f"""
QMainWindow, QDialog {{ background: {C.BG}; }}
QWidget#Content {{ background: {C.BG}; }}
QLabel {{ color: {C.TEXT}; }}
QToolTip {{ background: {C.SURFACE_RAISED}; color: {C.TEXT_STRONG}; border: 1px solid {C.INPUT_BORDER}; padding: 6px 8px;
    border-radius: 6px; }}

/* Menú lateral */
QFrame#Sidebar {{ background: {C.NAV}; border-right: 1px solid {C.DIVIDER}; }}
QLabel#Brand {{ color: {C.TEXT_STRONG}; font-size: 15px; font-weight: 600; }}
QLabel#BrandSub {{ color: {C.TEXT_SECONDARY}; font-size: {Font.TINY}px; }}
QLabel#NavSection {{ color: {C.TEXT_SECONDARY}; font-size: {Font.TINY}px; font-weight: 600; padding: 12px 10px 4px 10px; }}
QFrame#BottomNav {{ background: {C.NAV}; border-top: 1px solid {C.DIVIDER}; }}

/* Textos */
QLabel#PageTitle {{ color: {C.TEXT_STRONG}; font-size: {Font.TITLE}px; font-weight: 600; }}
QLabel#PageSubtitle {{ color: {C.TEXT_SECONDARY}; font-size: {Font.BODY}px; }}
QLabel#AboutTitle {{ color: {C.TEXT_STRONG}; font-size: 22px; font-weight: 700; }}
QLabel#Muted {{ color: {C.TEXT_SECONDARY}; }}
QLabel#Small {{ color: {C.TEXT_SECONDARY}; font-size: {Font.SMALL}px; }}
QLabel#SectionTitle {{ color: {C.TEXT_STRONG}; font-size: {Font.SECTION}px; font-weight: 600; }}
QLabel#ItemTitle {{ color: {C.TEXT_STRONG}; font-weight: 600; }}
QLabel#StatLabel {{ color: {C.TEXT_SECONDARY}; font-size: {Font.SMALL}px; font-weight: 500; }}
QLabel#StatValue {{ color: {C.TEXT_STRONG}; font-size: 26px; font-weight: 600; }}
QLabel#StripValue {{ color: {C.TEXT_STRONG}; font-size: 20px; font-weight: 600; }}
QLabel#CardTitle {{ color: {C.TEXT_STRONG}; font-size: 15px; font-weight: 600; }}
QLabel#CardDesc {{ color: {C.TEXT_SECONDARY}; }}
QLabel {{ selection-background-color: {C.SELECTION}; }}
/* Avisos: una barra de color a la izquierda, sin caja. */
QLabel#Warning {{ color: #FFD580; background: transparent; border: none; border-left: 3px solid {C.WARNING};
    padding: 2px 0 2px 10px; }}
QLabel#Hint {{ color: {C.TEXT}; background: transparent; border: none; border-left: 3px solid {C.INPUT_BORDER};
    padding: 2px 0 2px 10px; }}
QLabel#Overdue {{ color: {C.CRITICAL}; font-weight: 600; font-size: {Font.SMALL}px; }}
QLabel#Critical {{ color: {C.CRITICAL}; }}
/* Aviso dentro de la ventana (ui/notice_bar.py): arriba de la pantalla, con la línea del color del aviso. */
QFrame#NoticeBar {{ background: {C.SURFACE}; border: none; border-left: 3px solid {C.CRITICAL};
    border-bottom: 1px solid {C.DIVIDER}; }}
QFrame#NoticeBar[level="warn"] {{ border-left-color: {C.WARNING}; }}
QFrame#NoticeBar[level="info"] {{ border-left-color: {C.INFO}; }}
QLabel#NoticeTitle {{ color: {C.TEXT_STRONG}; font-weight: 600; }}
/* Reporte: cada punto con la línea de su color (importante, para revisar, va bien). */
QFrame#FindingRow {{ background: transparent; border: none; border-left: 3px solid {C.WARNING}; }}
QFrame#FindingRow[severity="critical"] {{ border-left-color: {C.CRITICAL}; }}
QFrame#FindingRow[severity="strength"] {{ border-left-color: {C.SUCCESS}; }}
QLabel#NoticeText {{ color: {C.TEXT_SECONDARY}; }}

/* Tarjetas: un poco más claras que el fondo, sin borde. */
QFrame#Card {{ background: {C.SURFACE}; border: none; border-radius: 14px; }}
QFrame#Divider {{ background: {C.DIVIDER}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#VDivider {{ background: {C.DIVIDER}; border: none; max-width: 1px; min-width: 1px; }}
QFrame#ActionItem, QFrame#DelayItem {{ background: transparent; border: none; border-bottom: 1px solid {C.DIVIDER}; }}
QPushButton#ConnItem {{ background: transparent; border: none; border-radius: 8px; text-align: left; }}
QPushButton#ConnItem:hover {{ background: {C.SURFACE_HOVER}; }}
QLabel#Photo {{ background: {C.SURFACE_RAISED}; border-radius: 8px; }}
QLabel#Note {{ background: {C.SURFACE_RAISED}; color: {C.TEXT}; border: none; border-radius: 8px; padding: 10px 12px; }}

/* Botones */
QPushButton {{
    background: {C.SURFACE_RAISED}; color: {C.TEXT}; border: 1px solid {C.INPUT_BORDER};
    border-radius: 8px; padding: 6px 13px; font-weight: 500;
}}
QPushButton:hover {{ background: {C.SURFACE_HOVER}; border-color: #3E5A4B; }}
QPushButton:pressed {{ background: {C.SURFACE_PRESSED}; }}
QPushButton:focus {{ border-color: {C.FOCUS}; }}
QPushButton:disabled {{ color: {C.TEXT_DISABLED}; background: {C.SURFACE}; border-color: {C.DIVIDER}; }}
QPushButton[variant="primary"] {{ background: {C.PRIMARY}; color: {C.ON_PRIMARY}; border: 1px solid {C.PRIMARY};
    font-weight: 600; }}
QPushButton[variant="primary"]:hover {{ background: {C.PRIMARY_HOVER}; border-color: {C.PRIMARY_HOVER}; }}
QPushButton[variant="primary"]:pressed {{ background: {C.PRIMARY_PRESSED}; border-color: {C.PRIMARY_PRESSED}; }}
QPushButton[variant="primary"]:focus {{ border-color: {C.TEXT_STRONG}; }}
QPushButton[variant="primary"]:disabled {{ background: {C.PRIMARY_DISABLED}; border-color: {C.PRIMARY_DISABLED};
    color: {C.TEXT_DISABLED}; }}
QPushButton[variant="danger"] {{ color: {C.CRITICAL}; }}
QPushButton[variant="danger"]:hover {{ background: #2A1216; border-color: #5A2028; }}
QPushButton[variant="ghost"] {{ border: none; background: transparent; padding: 6px 8px; }}
QPushButton[variant="ghost"]:hover {{ background: {C.SURFACE_PRESSED}; }}
QPushButton[variant="link"] {{ border: none; background: transparent; color: {C.PRIMARY}; padding: 2px 0;
    text-align: left; font-weight: 600; }}
QPushButton[variant="link"]:hover {{ color: {C.PRIMARY_HOVER}; text-decoration: underline; }}
QPushButton[variant="chip"] {{ background: {C.SURFACE_SELECTED}; color: {C.TEXT}; border: none; border-radius: 8px;
    padding: 6px 12px; }}
QPushButton[variant="chip"]:hover {{ background: {C.SURFACE_PRESSED}; }}
QPushButton[variant="chip"]:disabled {{ color: {C.TEXT_DISABLED}; }}
QPushButton[variant="segment"] {{ background: transparent; color: {C.TEXT_SECONDARY}; border: none; border-radius: 8px;
    padding: 6px 12px; font-weight: 600; }}
QPushButton[variant="segment"]:hover {{ background: {C.SURFACE_SELECTED}; color: {C.TEXT}; }}
QPushButton[variant="segment"]:checked {{ background: {C.NEON_SOFT}; color: {C.PRIMARY}; }}
QPushButton#HeaderButton {{ background: transparent; border: none; border-radius: 8px; padding: 0; }}
QPushButton#HeaderButton:hover {{ background: rgba(255, 255, 255, 0.08); }}
QPushButton#HeaderButton:pressed {{ background: rgba(255, 255, 255, 0.14); }}
QPushButton#IconButton {{ padding: 0; }}

/* Campos */
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit, QComboBox, QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {C.INPUT_BG}; color: {C.TEXT}; border: 1px solid {C.INPUT_BORDER}; border-radius: 8px;
    padding: 6px 8px; selection-background-color: {C.SELECTION}; selection-color: {C.TEXT_STRONG};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover {{ border-color: #3E5A4B; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {C.FOCUS}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QTimeEdit:disabled {{
    background: {C.SURFACE}; color: {C.TEXT_DISABLED}; border-color: {C.DIVIDER}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {C.SURFACE_RAISED}; color: {C.TEXT}; border: 1px solid {C.INPUT_BORDER};
    selection-background-color: {C.NEON_SOFT}; selection-color: {C.TEXT_STRONG}; outline: none; }}
QListView, QTreeView {{ background: {C.SURFACE}; color: {C.TEXT}; }}
QTextBrowser#LegalText {{ background: {C.SURFACE}; border: none; padding: 14px 18px; }}
QPlainTextEdit#Code {{
    font-family: "Cascadia Mono", Consolas, Menlo, monospace; font-size: 12px;
    background: {C.CODE_BG}; color: {C.CODE_TEXT}; border: none; border-radius: 8px; padding: 10px;
}}

/* Casillas: la palomita es una imagen (assets/ui, ver devtools/theme_assets.py); las opciones, un anillo. */
QCheckBox, QRadioButton {{ spacing: 8px; color: {C.TEXT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #4A5F53; background: {C.INPUT_BG}; }}
QCheckBox::indicator:hover {{ border-color: {C.PRIMARY}; }}
QCheckBox::indicator:checked {{ background: {C.PRIMARY}; border-color: {C.PRIMARY}; image: url(theme:check.png); }}
QCheckBox::indicator:disabled {{ background: {C.SURFACE}; border-color: {C.DIVIDER}; }}
QCheckBox::indicator:checked:disabled {{ background: {C.PRIMARY_DISABLED}; border-color: {C.PRIMARY_DISABLED}; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid #4A5F53;
    background: {C.INPUT_BG}; }}
QRadioButton::indicator:hover {{ border-color: {C.PRIMARY}; }}
QRadioButton::indicator:checked {{ width: 8px; height: 8px; border: 5px solid {C.PRIMARY}; background: {C.ON_PRIMARY}; }}
QRadioButton::indicator:disabled {{ background: {C.SURFACE}; border-color: {C.DIVIDER}; }}
QRadioButton::indicator:checked:disabled {{ border-color: {C.PRIMARY_DISABLED}; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {C.TEXT_DISABLED}; }}

/* Tablas: sin marco, filas separadas por líneas finas. */
QTableView, QTableWidget {{
    background: {C.SURFACE}; color: {C.TEXT}; border: none; gridline-color: transparent;
    selection-background-color: {C.NEON_SOFT}; selection-color: {C.TEXT_STRONG};
    alternate-background-color: {C.SURFACE}; outline: none;
}}
QTableView::item, QTableWidget::item {{ padding: 6px; border-bottom: 1px solid {C.DIVIDER}; }}
QTableView::item:hover, QTableWidget::item:hover {{ background: {C.SURFACE_HOVER}; }}
QTableView::item:selected, QTableWidget::item:selected {{ background: {C.NEON_SOFT}; color: {C.TEXT_STRONG}; }}
QHeaderView::section {{
    background: {C.SURFACE}; color: {C.TEXT_SECONDARY}; border: none; border-bottom: 1px solid {C.DIVIDER};
    padding: 8px 6px; font-weight: 600; font-size: {Font.SMALL}px;
}}
QTableCornerButton::section {{ background: {C.SURFACE}; border: none; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {C.TEXT_SECONDARY}; padding: 8px 14px; margin-right: 4px;
    border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:hover {{ color: {C.TEXT}; }}
QTabBar::tab:selected {{ color: {C.TEXT_STRONG}; border-bottom: 2px solid {C.PRIMARY}; }}
QProgressBar {{ background: {C.SURFACE_SELECTED}; border: none; border-radius: 3px; min-height: 6px; max-height: 6px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {C.PRIMARY}; border-radius: 3px; }}
QProgressBar[complete="true"]::chunk {{ background: {C.SUCCESS}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2A3830; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3E5A4B; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #2A3830; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QMenu {{ background: {C.SURFACE_RAISED}; color: {C.TEXT}; border: 1px solid {C.INPUT_BORDER}; padding: 4px;
    border-radius: 10px; }}
QMenu::item {{ padding: 6px 18px 6px 10px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C.NEON_SOFT}; color: {C.TEXT_STRONG}; }}
QMenu::item:disabled {{ color: {C.TEXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {C.DIVIDER}; margin: 4px 6px; }}

/* Asistente */
QFrame#AssistantCard {{ background: {C.SURFACE}; border: 1px solid {C.INPUT_BORDER}; border-radius: 16px; }}
QFrame#AssistantHeader {{ background: {C.NAV}; border: none; border-bottom: 1px solid {C.NEON_SOFT};
    border-top-left-radius: 15px; border-top-right-radius: 15px; }}
QLabel#AssistantTitle {{ color: {C.TEXT_STRONG}; font-size: 15px; font-weight: 600; }}
QLabel#AssistantStatus {{ color: {C.TEXT_SECONDARY}; font-size: {Font.SMALL}px; }}
QWidget#AssistantChat {{ background: transparent; }}
QScrollArea#KeyScroll {{ background: transparent; border: none; }}
QScrollArea#ChatScroll {{ background: {C.BG}; border: none; }}
QWidget#ChatArea {{ background: {C.BG}; }}
QFrame#Composer {{ background: transparent; border: none; border-top: 1px solid {C.DIVIDER}; }}
QFrame#Composer QPlainTextEdit {{ border: 1px solid {C.INPUT_BORDER}; border-radius: 10px; background: {C.INPUT_BG};
    padding: 4px 6px; font-size: 14px; }}
QFrame#Composer QPlainTextEdit:focus {{ border: 1px solid {C.FOCUS}; }}
"""


def apply_theme(app) -> None:
    """La app siempre en oscuro (también la barra de título de Windows 11 y de la Mac), con Windows en claro o en
    oscuro: esquema de color, paleta y hoja de estilos. La paleta cubre lo que la hoja de estilos no pinta."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette

    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+
        hints.setColorScheme(Qt.ColorScheme.Dark)
    palette = QPalette()
    roles = QPalette.ColorRole
    for role, color in (
        (roles.Window, C.BG), (roles.WindowText, C.TEXT), (roles.Base, C.SURFACE), (roles.AlternateBase, C.SURFACE),
        (roles.Text, C.TEXT), (roles.Button, C.SURFACE_RAISED), (roles.ButtonText, C.TEXT),
        (roles.BrightText, C.TEXT_STRONG), (roles.ToolTipBase, C.SURFACE_RAISED), (roles.ToolTipText, C.TEXT_STRONG),
        (roles.Highlight, C.SELECTION), (roles.HighlightedText, C.TEXT_STRONG), (roles.PlaceholderText, "#6B7C72"),
        (roles.Link, C.PRIMARY), (roles.LinkVisited, C.PRIMARY_PRESSED),
    ):
        palette.setColor(role, QColor(color))
    for role in (roles.Text, roles.WindowText, roles.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(C.TEXT_DISABLED))
    app.setPalette(palette)
    register_assets()
    app.setStyleSheet(STYLESHEET)


def register_assets() -> None:
    """Las imágenes de la hoja de estilos se piden como «theme:archivo.png»: así la ruta real (que puede tener «;» o
    espacios) nunca entra en el texto del estilo."""
    from PySide6.QtCore import QDir

    from ..paths import resource_path

    folder = resource_path("assets", "ui")
    if not (folder / "check.png").is_file():  # sin la imagen, la casilla marcada queda negra lisa: que quede escrito
        import logging

        logging.getLogger(__name__).warning("Faltan las imágenes del tema en %s", folder)
    QDir.setSearchPaths("theme", [str(folder)])
