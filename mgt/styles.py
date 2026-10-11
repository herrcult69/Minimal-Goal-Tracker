"""Pure-black theme: QSS stylesheet, app font, and the generated tray icon.

Everything here is Qt-side and only importable once PyQt6 is present. The
stylesheet is applied **once** on the ``QApplication`` (never per row/per
widget): every re-polish of a styled widget is costly (§8).

Conventions enforced to avoid console warnings:
- all sizes in ``px`` (never ``pt``; ``pt`` maps to different pixel sizes across
  Windows/Linux/WSL and triggers ``QFont::setPointSize`` warnings when <= 0);
- no ``letter-spacing`` (unsupported by QSS);
- no image/asset references — the icon is painted at runtime with ``QPainter``.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication

# Fallback list is applied via QFont.setFamilies(); QSS font-family is not a
# fallback list, so it is intentionally omitted from the stylesheet below.
FONT_FAMILIES = ("Segoe UI", "Ubuntu", "Noto Sans", "DejaVu Sans")
FONT_PX = 13

#: The single application stylesheet. Selectors use objectName and dynamic
#: properties (``nav``, ``active``, ``done``, ``urgency``) so widgets can change
#: state by setting a property and re-polishing only that widget.
STYLESHEET = """
* { color: #ffffff; }

QFrame#Root { background-color: #000000; border: 1px solid #222222; border-radius: 8px; }
QFrame#Card { background-color: #0d0d0d; border: 1px solid #1a1a1a; border-radius: 6px; }

QLineEdit, QComboBox, QSpinBox, QDateTimeEdit {
    background: #111111; border: 1px solid #262626; color: #ffffff;
    border-radius: 4px; padding: 6px 10px;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDateTimeEdit:focus {
    border: 1px solid #3a3a3a;
}
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: #111111; border: 1px solid #262626; color: #ffffff;
    selection-background-color: #222222; selection-color: #ffffff;
    outline: none;
}

QPushButton { background: transparent; border: none; padding: 6px 10px; }
QPushButton:hover { background: #222222; }
QPushButton:pressed { background: #2a2a2a; }
QPushButton[nav="true"] { color: #555555; border-bottom: 2px solid transparent; }
QPushButton[nav="true"][active="true"] { color: #ffffff; border-bottom: 2px solid #ffffff; }

QLabel#Title { color: #dddddd; }
QLabel#Title[done="true"] { color: #555555; text-decoration: line-through; }

QLabel#Badge { border-radius: 4px; padding: 2px 6px; font-size: 11px; color: #888888; }
QLabel#Badge[urgency="overdue"]     { color: #ff5252; }
QLabel#Badge[urgency="critical"]    { color: #ff5252; }
QLabel#Badge[urgency="urgent"]      { color: #ffb300; }
QLabel#Badge[urgency="approaching"] { color: #64b5f6; }

QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #222222; background: #000000; }
QCheckBox::indicator:checked { background: #ffffff; }

QMenu { background: #0d0d0d; border: 1px solid #262626; }
QMenu::item { padding: 6px 18px; }
QMenu::item:selected { background: #222222; }
QMenu::separator { height: 1px; background: #1a1a1a; margin: 4px 8px; }

QToolTip { background: #0d0d0d; border: 1px solid #262626; color: #ffffff; }

QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 0; }
QScrollBar::handle:vertical { background: #262626; border-radius: 4px; min-height: 24px; }
QScrollBar::handle:vertical:hover { background: #3a3a3a; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""

_applied = False
_icon: QIcon | None = None


def apply(app: QApplication) -> None:
    """Set the app font and stylesheet. Idempotent; safe to call twice."""
    global _applied
    if _applied:
        return

    font = QFont()
    font.setFamilies(list(FONT_FAMILIES))
    font.setPixelSize(FONT_PX)
    app.setFont(font)
    app.setStyleSheet(STYLESHEET)
    _applied = True


def icon_pixmap(size: int = 64) -> QPixmap:
    """Paint a white ring on a black disc. No asset files."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)

    p = QPainter(pm)
    try:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        margin = size * 0.06
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0))
        p.drawEllipse(QRectF(margin, margin, size - 2 * margin, size - 2 * margin))

        pen = QPen(QColor(255, 255, 255))
        pen.setWidthF(max(2.0, size * 0.11))
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        r = size * 0.30
        p.drawEllipse(QRectF(size / 2 - r, size / 2 - r, 2 * r, 2 * r))
    finally:
        p.end()

    return pm


def app_icon() -> QIcon:
    """Return the cached tray/window icon. Requires a live QGuiApplication."""
    global _icon
    if _icon is None:
        _icon = QIcon(icon_pixmap(64))
    return _icon
