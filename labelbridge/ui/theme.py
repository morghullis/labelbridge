"""Tema visual: claro con barra lateral oscura, acento índigo."""

from __future__ import annotations

ACCENT = "#4F46E5"
ACCENT_HOVER = "#4338CA"
TEXT = "#0F172A"
MUTED = "#64748B"
BORDER = "#E2E8F0"
BG = "#F4F6FB"
SIDEBAR = "#0F172A"

STATE_COLORS = {
    "ok": ("#DCFCE7", "#166534"),
    "warn": ("#FEF3C7", "#92400E"),
    "connecting": ("#E0E7FF", "#3730A3"),
    "info": ("#E0E7FF", "#3730A3"),
    "error": ("#FEE2E2", "#991B1B"),
    "off": ("#F1F5F9", "#475569"),
}

JOB_STATE = {"queued": "info", "printing": "warn", "done": "ok", "failed": "error", "canceled": "off"}


def job_state(job: dict) -> str:
    if job.get("status") == "queued" and job.get("error"):
        return "warn"
    return JOB_STATE.get(job.get("status", ""), "off")

STYLESHEET = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QWidget#Content {{ background: {BG}; }}
QLabel {{ color: {TEXT}; }}
QToolTip {{ background: {TEXT}; color: white; border: none; padding: 6px 8px; border-radius: 6px; }}

QFrame#Sidebar {{ background: {SIDEBAR}; }}
QFrame#Sidebar QLabel {{ color: #E2E8F0; }}
QLabel#Brand {{ color: white; font-size: 17px; font-weight: 700; }}
QLabel#BrandSub {{ color: #94A3B8; font-size: 11px; }}
QPushButton#NavButton {{
    text-align: left; padding: 9px 12px; border: none; border-radius: 8px;
    color: #CBD5E1; background: transparent; font-size: 13px;
}}
QPushButton#NavButton:hover {{ background: #1E293B; color: white; }}
QPushButton#NavButton:checked {{ background: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#NavButton[toggle="true"]:checked {{
    background: #1E293B; color: white; font-weight: 600; border: 1px solid #B8892F; padding: 8px 11px;
}}
QFrame#SidebarStatus {{ background: #1E293B; border-radius: 10px; }}
QLabel#NavBadge {{
    background: #F59E0B; color: #1E1B4B; border-radius: 9px; padding: 1px 7px; font-size: 11px; font-weight: 700;
}}

QLabel#PageTitle {{ font-size: 22px; font-weight: 700; }}
QLabel#PageSubtitle {{ color: {MUTED}; font-size: 13px; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Small {{ color: {MUTED}; font-size: 11px; }}
QLabel#SectionTitle {{ font-size: 14px; font-weight: 600; }}
QLabel#StatValue {{ font-size: 26px; font-weight: 700; }}
QLabel#StatLabel {{ color: {MUTED}; font-size: 11px; font-weight: 600; letter-spacing: 0.5px; }}
QLabel#Warning {{ background: #FFFBEB; color: #92400E; border: 1px solid #FDE68A; border-radius: 8px; padding: 8px 10px; }}
QLabel#Hint {{ background: #EEF2FF; color: #3730A3; border: 1px solid #C7D2FE; border-radius: 8px; padding: 8px 10px; }}

QFrame#Card {{ background: white; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#CardTitle {{ font-size: 15px; font-weight: 600; }}
QLabel#CardDesc {{ color: {MUTED}; }}

QPushButton {{
    background: white; color: {TEXT}; border: 1px solid #CBD5E1; border-radius: 8px;
    padding: 7px 14px; font-weight: 500;
}}
QPushButton:hover {{ background: #F8FAFC; border-color: #94A3B8; }}
QPushButton:pressed {{ background: #F1F5F9; }}
QPushButton:disabled {{ color: #94A3B8; background: #F8FAFC; border-color: {BORDER}; }}
QPushButton[variant="primary"] {{ background: {ACCENT}; color: white; border: 1px solid {ACCENT}; }}
QPushButton[variant="primary"]:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton[variant="primary"]:disabled {{ background: #A5B4FC; border-color: #A5B4FC; }}
QPushButton[variant="danger"] {{ color: #B91C1C; border-color: #FCA5A5; }}
QPushButton[variant="danger"]:hover {{ background: #FEF2F2; }}
QPushButton[variant="ghost"] {{ border: none; background: transparent; padding: 6px 8px; }}
QPushButton[variant="ghost"]:hover {{ background: #F1F5F9; }}
QPushButton[variant="chip"] {{
    background: #EEF2FF; color: #3730A3; border: 1px solid #C7D2FE; border-radius: 12px; padding: 6px 13px;
}}
QPushButton[variant="chip"]:hover {{ background: #E0E7FF; border-color: #A5B4FC; }}
QPushButton[variant="chip"]:disabled {{ color: #94A3B8; background: #F8FAFC; border-color: {BORDER}; }}
QPushButton[variant="segment"] {{
    background: transparent; color: {MUTED}; border: 1px solid {BORDER}; border-radius: 14px; padding: 5px 12px;
    font-weight: 600;
}}
QPushButton[variant="segment"]:hover {{ background: #F1F5F9; color: {TEXT}; }}
QPushButton[variant="segment"]:checked {{ background: #EEF2FF; color: {ACCENT_HOVER}; border-color: #C7D2FE; }}
QFrame#ActionItem {{ background: #FFFBEB; border: 1px solid #FDE68A; border-radius: 10px; }}
QFrame#DelayItem {{ background: #FFF1F2; border: 1px solid #FECDD3; border-radius: 10px; }}
QLabel#Overdue {{ background: #FFE4E6; color: #BE123C; border-radius: 9px; padding: 2px 9px; font-weight: 700; font-size: 11px; }}

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit {{
    background: white; color: {TEXT}; border: 1px solid #CBD5E1; border-radius: 8px;
    padding: 6px 8px; selection-background-color: {ACCENT}; selection-color: white;
}}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{ background: #F8FAFC; color: #94A3B8; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: white; border: 1px solid {BORDER}; selection-background-color: #EEF2FF; selection-color: {TEXT}; }}
QPlainTextEdit#Code {{
    font-family: "Cascadia Mono", Consolas, Menlo, monospace; font-size: 12px;
    background: #0F172A; color: #E2E8F0; border: none; border-radius: 8px; padding: 10px;
}}

QCheckBox {{ spacing: 8px; color: {TEXT}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #94A3B8; background: white; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QRadioButton {{ spacing: 8px; color: {TEXT}; }}

QTableView, QTableWidget {{
    background: white; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: transparent;
    selection-background-color: #EEF2FF; selection-color: {TEXT}; alternate-background-color: #FAFBFD;
}}
QTableView::item, QTableWidget::item {{ padding: 6px; border-bottom: 1px solid #F1F5F9; }}
QHeaderView::section {{
    background: #F8FAFC; color: #475569; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px; font-weight: 600; font-size: 12px;
}}
QTableCornerButton::section {{ background: #F8FAFC; border: none; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 8px 14px; margin-right: 4px;
    border-bottom: 2px solid transparent; font-weight: 600;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QProgressBar {{ background: #E2E8F0; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QScrollArea {{ border: none; background: transparent; }}
QFrame#AssistantCard {{ background: white; border: 1px solid {BORDER}; border-radius: 16px; }}
QFrame#AssistantHeader {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #141C2E, stop:1 #0B1020);
    border: none; border-top-left-radius: 15px; border-top-right-radius: 15px;
}}
QLabel#AssistantTitle {{ color: white; font-size: 15px; font-weight: 700; }}
QLabel#AssistantStatus {{ color: #94A3B8; font-size: 12px; }}
QPushButton#HeaderButton {{ background: transparent; border: none; border-radius: 8px; padding: 0; }}
QPushButton#HeaderButton:hover {{ background: rgba(255, 255, 255, 0.10); }}
QPushButton#HeaderButton:pressed {{ background: rgba(255, 255, 255, 0.16); }}
QPushButton#HeaderButton:focus {{ border: 1px solid #818CF8; }}
QWidget#AssistantChat {{ background: transparent; }}
QScrollArea#KeyScroll {{ background: transparent; border: none; }}
QScrollArea#ChatScroll {{ background: #F8FAFC; border: none; }}
QWidget#ChatArea {{ background: #F8FAFC; }}
QFrame#Composer {{ background: transparent; border: none; border-top: 1px solid {BORDER}; }}
QFrame#Composer QPlainTextEdit {{ border: 1px solid #CBD5E1; border-radius: 10px; background: white; padding: 4px 6px; font-size: 14px; }}
QFrame#Composer QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QPushButton#IconButton {{ padding: 0; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #CBD5E1; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QMenu {{ background: white; border: 1px solid {BORDER}; padding: 4px; border-radius: 8px; }}
QMenu::item {{ padding: 6px 18px; border-radius: 6px; }}
QMenu::item:selected {{ background: #EEF2FF; color: {TEXT}; }}
"""
