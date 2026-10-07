"""Tema visual de LabelBridge: sobrio y claro, al estilo del admin de Shopify.

Superficies blancas sobre gris muy claro, texto casi negro, botones negros y el oro de la marca solo en los detalles
(la pestaña activa y algún acento). Nada de cajas con fondo y borde: los grupos se separan con espacio y líneas finas,
y los avisos llevan una barra de color a la izquierda. Todos los colores salen de los tokens de abajo: ninguna pantalla
debería escribir un color a mano (así un cambio de diseño se hace en un solo lugar).
"""

from __future__ import annotations


class Color:
    """Tokens de color (contraste de texto ≥ 4.5:1 sobre blanco, salvo el de deshabilitado)."""

    BG = "#F1F1F1"  # fondo de la ventana
    SURFACE = "#FFFFFF"  # tarjetas, tablas, campos
    SURFACE_HOVER = "#F7F7F7"
    SURFACE_PRESSED = "#EDEDED"
    SURFACE_SELECTED = "#EBEBEB"
    NAV = "#EBEBEB"  # menú lateral y barra inferior
    NAV_HOVER = "#E1E1E1"
    TEXT = "#303030"
    TEXT_STRONG = "#1A1A1A"
    TEXT_SECONDARY = "#616161"
    TEXT_DISABLED = "#A8A8A8"
    ICON = "#4A4A4A"
    DIVIDER = "#E3E3E3"
    INPUT_BORDER = "#C9C9C9"
    FOCUS = "#005BD3"
    PRIMARY = "#303030"  # botones principales, interruptores y casillas marcadas
    PRIMARY_HOVER = "#1A1A1A"
    PRIMARY_PRESSED = "#000000"
    PRIMARY_DISABLED = "#B5B5B5"
    GOLD = "#B98A2C"  # la marca: solo detalles
    GOLD_SOFT = "#F6EEDC"
    SUCCESS = "#29845A"  # puntos de estado
    WARNING = "#B28400"
    CRITICAL = "#C70A24"
    INFO = "#2463D0"


class Font:
    TITLE = 20
    SECTION = 14
    BODY = 13
    SMALL = 12
    TINY = 11


# Estados (fondo, texto) para etiquetas de estado. Fondos suaves, sin borde.
STATE_COLORS = {
    "ok": ("#DDF3E6", "#0C5132"),
    "warn": ("#FFEBC2", "#5E4200"),
    "connecting": ("#E0EDFF", "#00418A"),
    "info": ("#E0EDFF", "#00418A"),
    "error": ("#FEE1DE", "#8E1F0B"),
    "off": ("#EBEBEB", "#4A4A4A"),
}
# El color del punto de cada estado (indicador de estado, filas de pedidos).
STATE_DOTS = {"ok": Color.SUCCESS, "warn": Color.WARNING, "connecting": Color.INFO, "info": Color.INFO,
              "error": Color.CRITICAL, "off": "#8A8A8A"}

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
QToolTip {{ background: {C.TEXT_STRONG}; color: white; border: none; padding: 6px 8px; border-radius: 6px; }}

/* Menú lateral */
QFrame#Sidebar {{ background: {C.NAV}; border-right: 1px solid {C.DIVIDER}; }}
QLabel#Brand {{ color: {C.TEXT_STRONG}; font-size: 15px; font-weight: 600; }}
QLabel#BrandSub {{ color: {C.TEXT_SECONDARY}; font-size: {Font.TINY}px; }}
QLabel#NavSection {{ color: {C.TEXT_SECONDARY}; font-size: {Font.TINY}px; font-weight: 600; padding: 12px 10px 4px 10px; }}
QFrame#BottomNav {{ background: {C.NAV}; border-top: 1px solid {C.DIVIDER}; }}

/* Textos */
QLabel#PageTitle {{ color: {C.TEXT_STRONG}; font-size: {Font.TITLE}px; font-weight: 600; }}
QLabel#PageSubtitle {{ color: {C.TEXT_SECONDARY}; font-size: {Font.BODY}px; }}
QLabel#Muted {{ color: {C.TEXT_SECONDARY}; }}
QLabel#Small {{ color: {C.TEXT_SECONDARY}; font-size: {Font.SMALL}px; }}
QLabel#SectionTitle {{ color: {C.TEXT_STRONG}; font-size: {Font.SECTION}px; font-weight: 600; }}
QLabel#ItemTitle {{ color: {C.TEXT_STRONG}; font-weight: 600; }}
QLabel#StatLabel {{ color: {C.TEXT_SECONDARY}; font-size: {Font.SMALL}px; font-weight: 500; }}
QLabel#StatValue {{ color: {C.TEXT_STRONG}; font-size: 26px; font-weight: 600; }}
QLabel#StripValue {{ color: {C.TEXT_STRONG}; font-size: 20px; font-weight: 600; }}
QLabel#CardTitle {{ color: {C.TEXT_STRONG}; font-size: 15px; font-weight: 600; }}
QLabel#CardDesc {{ color: {C.TEXT_SECONDARY}; }}
/* Avisos: una barra de color a la izquierda, sin caja. */
QLabel#Warning {{ color: #5E4200; background: transparent; border: none; border-left: 3px solid #E8A72B;
    padding: 2px 0 2px 10px; }}
QLabel#Hint {{ color: {C.TEXT}; background: transparent; border: none; border-left: 3px solid {C.DIVIDER};
    padding: 2px 0 2px 10px; }}
QLabel#Overdue {{ color: {C.CRITICAL}; font-weight: 600; font-size: {Font.SMALL}px; }}
/* Aviso dentro de la ventana (ui/notice_bar.py): arriba de la pantalla, con la línea del color del aviso. */
QFrame#NoticeBar {{ background: {C.SURFACE}; border: none; border-left: 3px solid {C.CRITICAL};
    border-bottom: 1px solid {C.DIVIDER}; }}
QFrame#NoticeBar[level="warn"] {{ border-left-color: #E8A72B; }}
QFrame#NoticeBar[level="info"] {{ border-left-color: {C.INFO}; }}
QLabel#NoticeTitle {{ color: {C.TEXT_STRONG}; font-weight: 600; }}
/* Reporte: cada punto con la línea de su color (importante, para revisar, va bien). */
QFrame#FindingRow {{ background: transparent; border: none; border-left: 3px solid {C.WARNING}; }}
QFrame#FindingRow[severity="critical"] {{ border-left-color: {C.CRITICAL}; }}
QFrame#FindingRow[severity="strength"] {{ border-left-color: {C.SUCCESS}; }}
QLabel#NoticeText {{ color: {C.TEXT_SECONDARY}; }}

/* Tarjetas: blancas sobre el gris, sin borde. */
QFrame#Card {{ background: {C.SURFACE}; border: none; border-radius: 12px; }}
QFrame#Divider {{ background: {C.DIVIDER}; border: none; max-height: 1px; min-height: 1px; }}
QFrame#VDivider {{ background: {C.DIVIDER}; border: none; max-width: 1px; min-width: 1px; }}
QFrame#ActionItem, QFrame#DelayItem {{ background: transparent; border: none; border-bottom: 1px solid {C.DIVIDER}; }}
QPushButton#ConnItem {{ background: transparent; border: none; border-radius: 8px; text-align: left; }}
QPushButton#ConnItem:hover {{ background: {C.SURFACE_HOVER}; }}

/* Botones */
QPushButton {{
    background: {C.SURFACE}; color: {C.TEXT}; border: 1px solid #D4D4D4; border-bottom-color: #BDBDBD;
    border-radius: 8px; padding: 6px 13px; font-weight: 500;
}}
QPushButton:hover {{ background: {C.SURFACE_HOVER}; }}
QPushButton:pressed {{ background: {C.SURFACE_PRESSED}; }}
QPushButton:focus {{ border-color: {C.FOCUS}; }}
QPushButton:disabled {{ color: {C.TEXT_DISABLED}; background: {C.SURFACE}; border-color: {C.DIVIDER}; }}
QPushButton[variant="primary"] {{ background: {C.PRIMARY}; color: white; border: 1px solid {C.PRIMARY}; }}
QPushButton[variant="primary"]:hover {{ background: {C.PRIMARY_HOVER}; border-color: {C.PRIMARY_HOVER}; }}
QPushButton[variant="primary"]:pressed {{ background: {C.PRIMARY_PRESSED}; }}
QPushButton[variant="primary"]:focus {{ border-color: {C.FOCUS}; }}
QPushButton[variant="primary"]:disabled {{ background: {C.PRIMARY_DISABLED}; border-color: {C.PRIMARY_DISABLED}; color: white; }}
QPushButton[variant="danger"] {{ color: #8E1F0B; }}
QPushButton[variant="danger"]:hover {{ background: #FFF4F2; }}
QPushButton[variant="ghost"] {{ border: none; background: transparent; padding: 6px 8px; }}
QPushButton[variant="ghost"]:hover {{ background: {C.SURFACE_PRESSED}; }}
QPushButton[variant="chip"] {{ background: {C.SURFACE_SELECTED}; color: {C.TEXT}; border: none; border-radius: 8px;
    padding: 6px 12px; }}
QPushButton[variant="chip"]:hover {{ background: {C.NAV_HOVER}; }}
QPushButton[variant="chip"]:disabled {{ color: {C.TEXT_DISABLED}; }}
QPushButton[variant="segment"] {{ background: transparent; color: {C.TEXT_SECONDARY}; border: none; border-radius: 8px;
    padding: 6px 12px; font-weight: 600; }}
QPushButton[variant="segment"]:hover {{ background: {C.SURFACE_SELECTED}; color: {C.TEXT}; }}
QPushButton[variant="segment"]:checked {{ background: {C.SURFACE_SELECTED}; color: {C.TEXT_STRONG}; }}
QPushButton#HeaderButton {{ background: transparent; border: none; border-radius: 8px; padding: 0; }}
QPushButton#HeaderButton:hover {{ background: rgba(255, 255, 255, 0.12); }}
QPushButton#HeaderButton:pressed {{ background: rgba(255, 255, 255, 0.18); }}
QPushButton#IconButton {{ padding: 0; }}

/* Campos */
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateEdit, QComboBox, QPlainTextEdit, QTextEdit {{
    background: {C.SURFACE}; color: {C.TEXT}; border: 1px solid {C.INPUT_BORDER}; border-radius: 8px;
    padding: 6px 8px; selection-background-color: #CFE0FF; selection-color: {C.TEXT_STRONG};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover {{ border-color: #A8A8A8; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border: 1px solid {C.FOCUS}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QTimeEdit:disabled {{
    background: {C.SURFACE_HOVER}; color: {C.TEXT_DISABLED}; border-color: {C.DIVIDER}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {C.SURFACE}; color: {C.TEXT}; border: 1px solid {C.DIVIDER};
    selection-background-color: {C.SURFACE_SELECTED}; selection-color: {C.TEXT_STRONG}; outline: none; }}
QListView, QTreeView {{ background: {C.SURFACE}; color: {C.TEXT}; }}
QPlainTextEdit#Code {{
    font-family: "Cascadia Mono", Consolas, Menlo, monospace; font-size: 12px;
    background: #1F1F1F; color: #E3E3E3; border: none; border-radius: 8px; padding: 10px;
}}

/* Casillas: la palomita es una imagen (assets/ui, ver devtools/theme_assets.py); las opciones, un anillo. */
QCheckBox, QRadioButton {{ spacing: 8px; color: {C.TEXT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #8A8A8A; background: white; }}
QCheckBox::indicator:hover {{ border-color: {C.TEXT}; }}
QCheckBox::indicator:checked {{ background: {C.PRIMARY}; border-color: {C.PRIMARY}; image: url(theme:check.png); }}
QCheckBox::indicator:disabled {{ background: {C.SURFACE_HOVER}; border-color: {C.DIVIDER}; }}
QCheckBox::indicator:checked:disabled {{ background: {C.PRIMARY_DISABLED}; border-color: {C.PRIMARY_DISABLED}; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border-radius: 9px; border: 1px solid #8A8A8A; background: white; }}
QRadioButton::indicator:hover {{ border-color: {C.TEXT}; }}
QRadioButton::indicator:checked {{ width: 8px; height: 8px; border: 5px solid {C.PRIMARY}; }}
QRadioButton::indicator:disabled {{ background: {C.SURFACE_HOVER}; border-color: {C.DIVIDER}; }}
QRadioButton::indicator:checked:disabled {{ border-color: {C.PRIMARY_DISABLED}; background: white; }}
QCheckBox:disabled, QRadioButton:disabled {{ color: {C.TEXT_DISABLED}; }}

/* Tablas: sin marco, filas separadas por líneas finas. */
QTableView, QTableWidget {{
    background: {C.SURFACE}; color: {C.TEXT}; border: none; gridline-color: transparent;
    selection-background-color: {C.SURFACE_SELECTED}; selection-color: {C.TEXT_STRONG};
    alternate-background-color: {C.SURFACE}; outline: none;
}}
QTableView::item, QTableWidget::item {{ padding: 6px; border-bottom: 1px solid #F0F0F0; }}
QTableView::item:hover, QTableWidget::item:hover {{ background: {C.SURFACE_HOVER}; }}
QTableView::item:selected, QTableWidget::item:selected {{ background: {C.SURFACE_SELECTED}; color: {C.TEXT_STRONG}; }}
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
QTabBar::tab:selected {{ color: {C.TEXT_STRONG}; border-bottom: 2px solid {C.TEXT_STRONG}; }}
QProgressBar {{ background: {C.SURFACE_SELECTED}; border: none; border-radius: 3px; min-height: 6px; max-height: 6px;
    text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {C.PRIMARY}; border-radius: 3px; }}
QProgressBar[complete="true"]::chunk {{ background: {C.SUCCESS}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C9C9C9; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #A8A8A8; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C9C9C9; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QMenu {{ background: {C.SURFACE}; color: {C.TEXT}; border: 1px solid {C.DIVIDER}; padding: 4px; border-radius: 10px; }}
QMenu::item {{ padding: 6px 18px 6px 10px; border-radius: 6px; }}
QMenu::item:selected {{ background: {C.SURFACE_HOVER}; color: {C.TEXT_STRONG}; }}
QMenu::item:disabled {{ color: {C.TEXT_DISABLED}; }}
QMenu::separator {{ height: 1px; background: {C.DIVIDER}; margin: 4px 6px; }}

/* Asistente */
QFrame#AssistantCard {{ background: {C.SURFACE}; border: 1px solid {C.DIVIDER}; border-radius: 16px; }}
QFrame#AssistantHeader {{ background: #1F1F1F; border: none; border-top-left-radius: 15px; border-top-right-radius: 15px; }}
QLabel#AssistantTitle {{ color: white; font-size: 15px; font-weight: 600; }}
QLabel#AssistantStatus {{ color: #B5B5B5; font-size: {Font.SMALL}px; }}
QWidget#AssistantChat {{ background: transparent; }}
QScrollArea#KeyScroll {{ background: transparent; border: none; }}
QScrollArea#ChatScroll {{ background: {C.SURFACE_HOVER}; border: none; }}
QWidget#ChatArea {{ background: {C.SURFACE_HOVER}; }}
QFrame#Composer {{ background: transparent; border: none; border-top: 1px solid {C.DIVIDER}; }}
QFrame#Composer QPlainTextEdit {{ border: 1px solid {C.INPUT_BORDER}; border-radius: 10px; background: white;
    padding: 4px 6px; font-size: 14px; }}
QFrame#Composer QPlainTextEdit:focus {{ border: 1px solid {C.FOCUS}; }}
"""


def apply_theme(app) -> None:
    """La app siempre en claro, también con Windows en modo oscuro: esquema de color, paleta y hoja de estilos.
    (Sin esto, Qt en Windows 11 sigue el modo oscuro y tablas, menús y listas quedan con texto blanco sobre blanco.)"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette

    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt 6.8+
        hints.setColorScheme(Qt.ColorScheme.Light)
    palette = QPalette()
    roles = QPalette.ColorRole
    for role, color in (
        (roles.Window, C.BG), (roles.WindowText, C.TEXT), (roles.Base, C.SURFACE), (roles.AlternateBase, C.SURFACE),
        (roles.Text, C.TEXT), (roles.Button, C.SURFACE), (roles.ButtonText, C.TEXT), (roles.BrightText, "#FFFFFF"),
        (roles.ToolTipBase, C.TEXT_STRONG), (roles.ToolTipText, "#FFFFFF"), (roles.Highlight, "#CFE0FF"),
        (roles.HighlightedText, C.TEXT_STRONG), (roles.PlaceholderText, "#8A8A8A"), (roles.Link, C.INFO),
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
