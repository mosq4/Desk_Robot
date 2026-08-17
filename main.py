"""Desk Robot 绘图机器人上位机 —— 程序入口。

运行: python main.py
"""
import sys

from PyQt5.QtWidgets import QApplication

from app.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Desk Robot")
    app.setStyle("Fusion")
    app.setStyleSheet(
        "QGroupBox { font-weight: bold; margin-top: 8px; }"
        "QGroupBox::title { subcontrol-origin: margin; left: 8px; padding: 0 4px; }"
    )
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
