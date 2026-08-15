"""Soulvise Plmate 的暮色小镇主题令牌与 Qt 样式。"""

THEME_COLORS = {
    "midnight": "#17131B",
    "wine": "#3A1626",
    "gothic_red": "#8F2D4A",
    "blush": "#F1B3C7",
    "parchment": "#F6E9D8",
    "copper": "#D98C5F",
    "warm_white": "#FFF9F3",
}

APP_STYLE = """
QWidget {
  color: #3A1626;
  font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif;
  font-size: 13px;
}
QMainWindow, QDialog { background: #17131B; }
QWidget#appShell, QScrollArea#pageScroll, QWidget#pageContent {
  background: #F6E9D8;
}
QWidget#navigation { background: #17131B; border-right: 2px solid #6A432F; }
QScrollArea#pageScroll { border: none; }
QFrame#card, QFrame#dashboardPanel {
  background: rgba(255, 249, 243, 225);
  border: 1px solid rgba(143, 45, 74, 65);
  border-radius: 14px;
}
QFrame#dashboardMetric {
  background: transparent;
  border: none;
  border-bottom: 1px solid rgba(143, 45, 74, 42);
}
QLabel#brandTitle {
  color: #FFF9F3;
  font-family: Georgia, "Microsoft YaHei UI";
  font-size: 22px;
  font-weight: 600;
}
QLabel#brandSubtitle { color: #D7BFAF; font-size: 11px; }
QLabel#townPreview {
  background: #251A21;
  border: 1px solid #8F623F;
  border-radius: 10px;
}
QListWidget#sidebar {
  background: #17131B;
  color: #E8D6C7;
  border: none;
  padding: 8px 10px;
  outline: none;
}
QListWidget#sidebar::item {
  padding: 13px 14px;
  margin: 4px 0;
  border: 1px solid transparent;
  border-radius: 12px;
}
QListWidget#sidebar::item:hover { background: rgba(143, 45, 74, 65); }
QListWidget#sidebar::item:selected {
  background: #3A1626;
  color: #FFF9F3;
  border: 1px solid #8F2D4A;
}
QLabel#pageHeading {
  color: #3A1626;
  font-family: "KaiTi", "STKaiti", Georgia, "Microsoft YaHei UI";
  font-size: 25px;
  font-weight: 700;
}
QLabel#pageSubtitle { color: #765D57; font-size: 12px; }
QLabel#sectionTitle { color: #3A1626; font-size: 16px; font-weight: 700; }
QLabel#sectionHeading {
  color: #3A1626;
  font-family: "KaiTi", "STKaiti", Georgia, "Microsoft YaHei UI";
  font-size: 22px;
  font-weight: 700;
}
QLabel#metricTitle { color: #3A1626; font-size: 15px; font-weight: 600; }
QLabel#metricDetail { color: #765D57; font-size: 12px; }
QLabel#sceneryHint { color: #765D57; font-size: 11px; }
QPushButton {
  background: #8F2D4A;
  color: #FFF9F3;
  border: 1px solid #8F2D4A;
  border-radius: 9px;
  padding: 8px 14px;
  min-height: 18px;
}
QPushButton:hover { background: #A13B59; border-color: #D98C5F; }
QPushButton:pressed { background: #6F2038; }
QPushButton:disabled { background: #B7A9A3; border-color: #B7A9A3; }
QPushButton[secondary="true"] {
  background: #FFF9F3;
  color: #3A1626;
  border: 1px solid #C99B83;
}
QWidget#navigation QPushButton[exitButton="true"] {
  background: #3A1626;
  color: #FFF9F3;
  border: 1px solid #8F2D4A;
  font-weight: 700;
}
QWidget#navigation QPushButton[exitButton="true"]:hover {
  background: #8F2D4A;
  border-color: #D98C5F;
}
QToolButton {
  color: #6A3B49;
  background: transparent;
  border: none;
  padding: 6px 2px;
  font-weight: 600;
}
QToolButton:hover { color: #8F2D4A; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox {
  background: #FFF9F3;
  color: #3A1626;
  border: 1px solid #C99B83;
  border-radius: 7px;
  padding: 6px;
  selection-background-color: #8F2D4A;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus,
QComboBox:focus, QSpinBox:focus { border: 1px solid #8F2D4A; }
QTabWidget::pane {
  background: rgba(255, 249, 243, 190);
  border: 1px solid #D9B8A2;
  border-radius: 8px;
}
QTabBar::tab {
  background: #E9D7C5;
  color: #5C3840;
  padding: 8px 14px;
  margin-right: 3px;
  border-top-left-radius: 7px;
  border-top-right-radius: 7px;
}
QTabBar::tab:selected { background: #8F2D4A; color: #FFF9F3; }
QGroupBox {
  font-weight: 600;
  border: 1px solid #D9B8A2;
  border-radius: 9px;
  margin-top: 12px;
  padding-top: 12px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px; }
QCheckBox { spacing: 8px; }
QProgressBar {
  border: 1px solid #CFAF9B;
  background: #E8D9CB;
  border-radius: 7px;
  text-align: center;
  color: #3A1626;
  min-height: 14px;
}
QProgressBar::chunk {
  background: #D78AA0;
  border-radius: 6px;
}
QListWidget#cognitionRuleList, QTableWidget, QTextBrowser {
  background: rgba(255, 249, 243, 220);
  border: 1px solid #D9B8A2;
  border-radius: 8px;
  gridline-color: #E8D6C7;
}
QListWidget#cognitionRuleList::item { padding: 7px 8px; }
QListWidget#cognitionRuleList::item:selected {
  background: #F1B3C7;
  color: #3A1626;
}
QHeaderView::section {
  background: #E9D7C5;
  color: #3A1626;
  border: none;
  border-bottom: 1px solid #C99B83;
  padding: 7px;
}
QToolTip {
  background: #17131B;
  color: #FFF9F3;
  border: 1px solid #D98C5F;
  padding: 5px;
}
"""
