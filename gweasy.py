import os
import sys
import logging
import socket
import time
import subprocess
import json
import threading
import pandas as pd
from gwpy.timeseries import TimeSeries
from gwosc.datasets import find_datasets
from gwosc.locate import get_urls
from datetime import datetime
from scipy.signal import get_window
import argparse
import shutil
import platform
import ctypes
from concurrent.futures import ThreadPoolExecutor
from PyQt5.QtWidgets import (QApplication, QMainWindow, QTabWidget, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QComboBox, QLineEdit, QFileDialog, QLabel, QCheckBox,
                             QTextEdit, QScrollArea, QFrame, QSlider, QMessageBox, QProgressBar,QListWidget)
from PyQt5.QtCore import Qt, QTimer, QMetaObject, QGenericArgument, pyqtSignal, QObject
from PyQt5.QtGui import QFont, QPalette, QColor, QLinearGradient, QBrush, QPainter
from PyQt5.QtWidgets import QDialog
import requests_pelican as rp
from gwdatafind import find_urls, find_types
from PyQt5.QtCore import pyqtSignal
import requests
from requests.exceptions import RequestException
import traceback
from gwpy.detector import ChannelList, Channel
from gwpy.timeseries import TimeSeries
import re

# ANSI color codes for CLI output
COLORS = {
    "reset": "\033[0m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m"
}

# Set up logging to a file and console
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('GWeasy_log.txt'),
        logging.StreamHandler(sys.stdout)
    ]
)

# Ensure the path to the shared library is added to the system path
os.environ['FRAME_LIB_PATH'] = os.path.expanduser('~/miniconda3/envs/GWeasy/lib/')

logging.debug(f"FRAME_LIB_PATH is set to: {os.environ['FRAME_LIB_PATH']}")

# Create default GWFout directory at launch
DEFAULT_GWFOUT = "./GWFout"
if not os.path.exists(DEFAULT_GWFOUT):
    os.makedirs(DEFAULT_GWFOUT, exist_ok=True)
    logging.info(f"Created default output directory: {DEFAULT_GWFOUT}")

HISTORY_FILE = "gravfetch_history.json"
DEFAULT_CONFIG = """\
DATA CHANNELS\tNo Channels Available
DATA FFL\t
DATA SAMPLEFREQUENCY\t2048
PARAMETER TIMING\t64 4
PARAMETER FREQUENCYRANGE\t16 1000
PARAMETER QRANGE\t4 100
PARAMETER MISMATCHMAX\t0.3
PARAMETER SNRTHRESHOLD\t6.5
PARAMETER PSDLENGTH\t300
OUTPUT DIRECTORY\t./OmicronOut
OUTPUT FORMAT\troot
OUTPUT PRODUCTS\ttriggers html
OUTPUT VERBOSITY\t0
"""

# Grok-themed color scheme
COLOR_BG_TOP = "#D4D7D8"  # Pearlescent white, 50% brightness
COLOR_BG_BOTTOM = "#101718"  # Shiny black
COLOR_TAB = "#FFFFFFFF"
COLOR_FG = "#00B7EB"  # Electric blue for all text
COLOR_ACCENT = "#FFFFFF"  # Darkened gray for buttons and borders
COLOR_HOVER = "#1C2526"  # Shiny black for hover
COLOR_SUCCESS = "#28A745"  # Keep green for success
COLOR_ERROR = "#DC3545"  # Keep red for errors
COLOR_WARNING = "#FFC107"  # Keep yellow for warnings

FONT_HEADER = QFont("Helvetica", 14, QFont.Bold)
FONT_LABEL = QFont("Helvetica", 14, QFont.Bold)
FONT_BUTTON = QFont("Helvetica", 12, QFont.Bold)
FONT_TERMINAL = QFont("Consolas", 12)

class GradientWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)

    def paintEvent(self, event):
        painter = QPainter(self)
        gradient = QLinearGradient(0, 0, 0, self.height())
        gradient.setColorAt(0, QColor(COLOR_BG_BOTTOM))  # Shiny black at top
        gradient.setColorAt(1, QColor(COLOR_BG_TOP))     # Dulled white at bottom
        painter.fillRect(self.rect(), QBrush(gradient))

class TerminalWidget(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFont(FONT_TERMINAL)
        palette = self.palette()
        palette.setColor(QPalette.Base, QColor('#000000'))  # Pitch black
        palette.setColor(QPalette.Text, QColor(COLOR_FG))   # Electric blue
        self.setPalette(palette)
        self.setStyleSheet("""
            QTextEdit {
                border: 1px solid #A9A9A9;
                border-radius: 5px;
                padding: 5px;
            }
        """)

    def append_output(self, message, level="info"):
        colors = {"error": COLOR_ERROR, "success": COLOR_SUCCESS, "warning": COLOR_WARNING, "info": COLOR_FG}
        color = colors.get(level, COLOR_FG)
        self.append(f'<span style="color:{color}">{message}</span>')
        logging.log(
            {"error": logging.ERROR, "success": logging.INFO, "warning": logging.WARNING, "info": logging.INFO}[level],
            message
        )

class SplashScreen(QWidget):
    def __init__(self, callback):
        super().__init__()
        self.setWindowTitle("GWeasy")
        self.setFixedSize(400, 250)
        self.setWindowFlags(Qt.SplashScreen | Qt.WindowStaysOnTopHint)
        self.callback = callback

        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)

        logo = QLabel("GWeasy", self)
        logo.setFont(QFont("Helvetica", 24, QFont.Bold))
        logo.setStyleSheet(f"color: {COLOR_FG}; background-color: transparent;")
        logo.setAlignment(Qt.AlignCenter)
        layout.addWidget(logo)

        self.progress = QProgressBar(self)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #CED4DA;
                border-radius: 5px;
                text-align: center;
                background-color: #1C2526;
                color: #FFFFFF;
            }
            QProgressBar::chunk {
                background-color: #CED4DA;
                border-radius: 3px;
            }
        """)
        layout.addWidget(self.progress)

        version = QLabel("Version 2.0", self)
        version.setFont(FONT_LABEL)
        version.setStyleSheet(f"color: {COLOR_FG}; background-color: transparent;")
        version.setAlignment(Qt.AlignCenter)
        layout.addWidget(version)

        self.setLayout(layout)
        self.setStyleSheet(f"background-color: {COLOR_BG_BOTTOM};")

        self.progress_value = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_progress)
        self.timer.start(30)  # 5 seconds = 5000ms / 50ms = 100 steps

    def update_progress(self):
        self.progress_value += 1
        self.progress.setValue(self.progress_value)
        if self.progress_value >= 100:
            self.timer.stop()
            self.close()
            self.callback()

class MainWindow(QMainWindow):
    def __init__(self, cli_mode=False):
        super().__init__()
        self.terminal = None
        self.cli_mode = cli_mode
        if not cli_mode:
            self.init_ui()

    def init_ui(self):
        self.setWindowTitle("GWeasy")
        self.setGeometry(100, 100, 1024, 768)

        self.terminal = TerminalWidget(self)
        self.tabs = QTabWidget()
        grav_tab = GravfetchApp(self.tabs, self.append_output)
        omicron_tab = OmicronApp(self.tabs, self.append_output)
        omiviz_tab = GradientWidget()  # Placeholder for Omiviz

        self.tabs.addTab(grav_tab, "Gravfetch")
        self.tabs.addTab(omicron_tab, "OMICRON")
        self.tabs.addTab(omiviz_tab, "Omiviz")

        main_widget = GradientWidget()
        main_layout = QVBoxLayout()
        main_layout.addWidget(self.tabs)
        main_layout.addWidget(self.terminal)
        main_layout.setStretch(0, 3)
        main_layout.setStretch(1, 1)
        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)
        self.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                            stop:0 #1C2526, stop:1 #747576);
            }
            QTabWidget::pane {
                border: 1px solid #A9A9A9;
                border-radius: 2px;
                margin-right: 4px;
            }
            QTabBar::tab {
                background: #B0B0B0;
                color: #005566;
                font-size: 12px;
                font-weight: bold;
                padding: 6px 12px;
                min-width: 110px;
                width: auto;
                text-align: center;
                white-space: nowrap;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
            QTabBar::tab:selected {
                background: #D3D3D3;
                color: #005566;
                font-size: 14px;
                font-weight: bold;
                padding: 6px 12px;
                min-width: 110 px;
                width: auto;
                text-align: center;
                white-space: nowrap;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
        """)

    def append_output(self, message, level="info"):
        if self.terminal:
            self.terminal.append_output(message, level)
        else:
            color = {"error": COLORS["red"], "success": COLORS["green"], "warning": COLORS["yellow"], "info": COLORS["blue"]}.get(level, "")
            print(f"{color}{message}{COLORS['reset']}")
        logging.log(
            {"error": logging.ERROR, "success": logging.INFO, "warning": logging.WARNING, "info": logging.INFO}[level],
            message
        )

########################################################################################################################################
############################################################    GRAVFETCH    ###########################################################
########################################################################################################################################

class GravfetchApp(GradientWidget):
    log_signal = pyqtSignal(str, str)  # message, level

    def __init__(self, parent, append_output_callback):
        super().__init__(parent)
        os.environ['GW_DATAFIND_URL_TYPE'] = 'file'
        self.time_csv_file = ""
        self.channel_csv_file = ""
        self.gwfout_path = DEFAULT_GWFOUT
        self.execution_running = False
        self.process = None
        self.loaded_channels = []
        self.time_ranges = None
        self.selected_channel = None
        self.selected_segments = []
        self.channel_to_rate = {}
        self.selected_frametype = None
        self.selected_host = None
        self.append_output = append_output_callback
        self.log_signal.connect(self.append_output)
        # OSDF-specific attributes
        self.detectors = [
            ("LIGO-Hanford", "H"),
            ("LIGO-Livingston", "L"),
            ("Virgo", "V"),
            ("KAGRA", "K"),
        ]
        self.frame_types = {}  # Cache: {detector_code: [frame_types]}
        self.time_segments = {}  # Cache: {(detector_code, frame_type): [segments]}
        self.selected_detector = None
        self.selected_detector_code = None
        self.selected_osdf_frametype = None
        self.selected_osdf_segments = []
        self.nds_detectors = [
            ("LIGO-Hanford", "H1"),
            ("LIGO-Livingston", "L1"),
            ("Virgo", "V1"),
            ("KAGRA", "K1"),
            ("","G1")
        ]
        self.nds_channels = {}  # Cache: {detector_code: [(channel_name, sample_rate)]}
        self.nds_groups = {}    # Cache: {detector_code: [group_names]}
        self.nds_segments = {}  # Cache: {channel_name: [segments]}
        self.selected_nds_detector = None
        self.selected_nds_detector_code = None
        self.selected_nds_group = None
        self.selected_nds_channel = None
        self.selected_nds_segments = []
        self.channel_combo_bulk_nds = None
        self.selected_bulk_nds_channel = None
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    history = json.load(f)
                    self.gwfout_path = history.get("gwfout_path", DEFAULT_GWFOUT)
            except Exception as e:
                self.append_output(f"Failed to read history file: {e}", "error")

        self.setup_ui()
        self.refresh_osdf_data() 
        self.refresh_nds_data()

    def setup_ui(self):
        self.tabs = QTabWidget()
        self.public_tab = GradientWidget()
        self.assoc_tab = GradientWidget()
        self.tabs.addTab(self.public_tab, "Public")
        self.tabs.addTab(self.assoc_tab, "LIGO Assoc")

        layout = QVBoxLayout()
        layout.addWidget(self.tabs)
        self.setLayout(layout)

        self.setup_public_ui()
        self.setup_assoc_ui()

    def setup_public_ui(self):
        self.public_subtabs = QTabWidget()
        self.osdf_tab = GradientWidget()
        self.nds_tab = GradientWidget()
        self.bulk_nds_tab = GradientWidget()
        self.public_subtabs.addTab(self.osdf_tab, "OSDF")
        self.public_subtabs.addTab(self.nds_tab, "NDS")
        self.public_subtabs.addTab(self.bulk_nds_tab, "Bulk Fetch (NDS)")

        public_layout = QVBoxLayout()
        public_layout.addWidget(self.public_subtabs)
        self.public_tab.setLayout(public_layout)

        self.setup_osdf_ui()
        self.setup_nds_ui()
        self.setup_bulk_nds_ui()

    def setup_osdf_ui(self):
        layout = QVBoxLayout()
        self.status_label_osdf = QLabel("Idle")
        self.status_label_osdf.setFont(FONT_LABEL)
        self.status_label_osdf.setStyleSheet(f"color: {COLOR_FG}; background-color: transparent; ")
        layout.addWidget(self.status_label_osdf)

        lists_layout = QHBoxLayout()
        detector_layout = QVBoxLayout()
        detector_layout.addWidget(QLabel("Detectors:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.detector_list = QListWidget()
        self.detector_list.addItems([name for name, _ in self.detectors])
        self.detector_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QListWidget::item:selected {{
                background-color: {COLOR_ACCENT};
            }}
        """)
        self.detector_list.setFixedHeight(200)
        self.detector_list.currentItemChanged.connect(self.on_detector_select)
        detector_layout.addWidget(self.detector_list)
        lists_layout.addLayout(detector_layout)

        frametype_layout = QVBoxLayout()
        frametype_layout.addWidget(QLabel("Frame Types:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.osdf_frametype_list = QListWidget()
        self.osdf_frametype_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QListWidget::item:selected {{
                background-color: {COLOR_ACCENT};
            }}
        """)
        self.osdf_frametype_list.setFixedHeight(200)
        self.osdf_frametype_list.currentItemChanged.connect(self.on_osdf_frametype_select)
        frametype_layout.addWidget(self.osdf_frametype_list)
        lists_layout.addLayout(frametype_layout)

        segments_layout = QVBoxLayout()
        segments_layout.addWidget(QLabel("Time Segments:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.osdf_segments_list = QListWidget()
        self.osdf_segments_list.setSelectionMode(QListWidget.MultiSelection)
        self.osdf_segments_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QListWidget::item:selected {{
                background-color: {COLOR_ACCENT};
            }}
        """)
        self.osdf_segments_list.setFixedHeight(200)
        segments_layout.addWidget(self.osdf_segments_list)
        lists_layout.addLayout(segments_layout)

        layout.addLayout(lists_layout)

        custom_time_layout = QHBoxLayout()
        custom_time_layout.addWidget(QLabel("Custom GPS Start:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.custom_start_edit = QLineEdit()
        self.custom_start_edit.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        custom_time_layout.addWidget(self.custom_start_edit)
        custom_time_layout.addWidget(QLabel("Custom GPS End:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.custom_end_edit = QLineEdit()
        self.custom_end_edit.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        custom_time_layout.addWidget(self.custom_end_edit)
        layout.addLayout(custom_time_layout)

        button_layout = QHBoxLayout()
        refresh_btn = QPushButton("Refresh Lists")
        refresh_btn.setFont(FONT_BUTTON)
        refresh_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: {COLOR_FG};
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 6px;
                min-height: 28px;
            }}
            QPushButton:hover {{
                background-color: {COLOR_HOVER};
            }}
            QPushButton:pressed {{
                background-color: #A9A9A9;
            }}
        """)
        refresh_btn.clicked.connect(self.refresh_osdf_data)
        button_layout.addWidget(refresh_btn)

        download_btn = QPushButton("Download Data")
        download_btn.setFont(FONT_BUTTON)
        download_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: {COLOR_FG};
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 6px;
                min-height: 28px;
            }}
            QPushButton:hover {{
                background-color: {COLOR_HOVER};
            }}
            QPushButton:pressed {{
                background-color: #A9A9A9;
            }}
        """)
        download_btn.clicked.connect(self.download_osdf_data)
        button_layout.addWidget(download_btn)

        start_stop_btn = QPushButton("Start/Stop Execution")
        start_stop_btn.setFont(FONT_BUTTON)
        start_stop_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: {COLOR_FG};
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 6px;
                min-height: 28px;
            }}
            QPushButton:hover {{
                background-color: {COLOR_HOVER};
            }}
            QPushButton:pressed {{
                background-color: #A9A9A9;
            }}
        """)
        start_stop_btn.clicked.connect(self.toggle_osdf_execution)
        button_layout.addWidget(start_stop_btn)

        output_btn = QPushButton("Select Output Dir")
        output_btn.setFont(FONT_BUTTON)
        output_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: {COLOR_FG};
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 6px;
                min-height: 28px;
            }}
            QPushButton:hover {{
                background-color: {COLOR_HOVER};
            }}
            QPushButton:pressed {{
                background-color: #A9A9A9;
            }}
        """)
        output_btn.clicked.connect(self.select_output_dir)
        button_layout.addWidget(output_btn)

        layout.addLayout(button_layout)
        layout.addStretch()
        self.osdf_tab.setLayout(layout)

    def setup_nds_ui(self):
        layout = QVBoxLayout()
        self.status_label_nds = QLabel("Idle")
        self.status_label_nds.setFont(FONT_LABEL)
        self.status_label_nds.setStyleSheet(f"color: {COLOR_FG}; background-color: transparent; ")
        layout.addWidget(self.status_label_nds)

        lists_layout = QHBoxLayout()
        detector_layout = QVBoxLayout()
        detector_layout.addWidget(QLabel("Detectors:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.nds_detector_list = QListWidget()
        self.nds_detector_list.addItems([name for name, _ in self.nds_detectors])
        self.nds_detector_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QListWidget::item:selected {{
                background-color: {COLOR_ACCENT};
            }}
        """)
        self.nds_detector_list.setFixedHeight(200)
        self.nds_detector_list.currentItemChanged.connect(self.on_nds_detector_select)
        detector_layout.addWidget(self.nds_detector_list)
        lists_layout.addLayout(detector_layout)

        group_layout = QVBoxLayout()
        group_layout.addWidget(QLabel("Channel Groups:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.nds_group_list = QListWidget()
        self.nds_group_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QListWidget::item:selected {{
                background-color: {COLOR_ACCENT};
            }}
        """)
        self.nds_group_list.setFixedHeight(200)
        self.nds_group_list.currentItemChanged.connect(self.on_nds_group_select)
        group_layout.addWidget(self.nds_group_list)
        lists_layout.addLayout(group_layout)

        channel_layout = QVBoxLayout()
        channel_layout.addWidget(QLabel("Channels:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.nds_channel_list = QListWidget()
        self.nds_channel_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QListWidget::item:selected {{
                background-color: {COLOR_ACCENT};
            }}
        """)
        self.nds_channel_list.setFixedHeight(200)
        self.nds_channel_list.currentItemChanged.connect(self.on_nds_channel_select)
        channel_layout.addWidget(self.nds_channel_list)
        lists_layout.addLayout(channel_layout)

        segments_layout = QVBoxLayout()
        segments_layout.addWidget(QLabel("Time Segments:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.nds_segments_list = QListWidget()
        self.nds_segments_list.setSelectionMode(QListWidget.MultiSelection)
        self.nds_segments_list.setStyleSheet(f"""
            QListWidget {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QListWidget::item:selected {{
                background-color: {COLOR_ACCENT};
            }}
        """)
        self.nds_segments_list.setFixedHeight(200)
        segments_layout.addWidget(self.nds_segments_list)
        lists_layout.addLayout(segments_layout)

        layout.addLayout(lists_layout)

        custom_time_layout = QHBoxLayout()
        custom_time_layout.addWidget(QLabel("Custom GPS Start:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.nds_custom_start_edit = QLineEdit()
        self.nds_custom_start_edit.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        custom_time_layout.addWidget(self.nds_custom_start_edit)
        custom_time_layout.addWidget(QLabel("Custom GPS End:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.nds_custom_end_edit = QLineEdit()
        self.nds_custom_end_edit.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        custom_time_layout.addWidget(self.nds_custom_end_edit)
        layout.addLayout(custom_time_layout)

        button_layout = QHBoxLayout()
        buttons = [
            ("Refresh Lists", self.refresh_nds_data),
            ("Download Data", self.download_nds_data),
            ("Start/Stop Execution", self.toggle_public_execution),
            ("Select Output Dir", self.select_output_dir)
        ]

        for text, cmd in buttons:
            btn = QPushButton(text)
            btn.setFont(FONT_BUTTON)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: {COLOR_FG};
                    border: 1px solid {COLOR_FG};
                    border-radius: 5px;
                    padding: 6px;
                    min-height: 28px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_HOVER};
                }}
                QPushButton:pressed {{
                    background-color: #A9A9A9;
                }}
            """)
            btn.clicked.connect(cmd)
            button_layout.addWidget(btn)

        layout.addLayout(button_layout)
        layout.addStretch()
        self.nds_tab.setLayout(layout)

    def setup_bulk_nds_ui(self):
        layout = QVBoxLayout()
        self.status_label_bulk_nds = QLabel("Idle")
        self.status_label_bulk_nds.setFont(FONT_LABEL)
        self.status_label_bulk_nds.setStyleSheet(f"color: {COLOR_FG}; background-color: transparent;")
        layout.addWidget(self.status_label_bulk_nds)

        buttons = [
            ("Select Time CSV", self.select_time_csv),
            ("Import Channel CSV", self.select_channel_csv),
            ("Select Output Dir", self.select_output_dir),
            ("Select Time Segments", self.open_segments_dialog),
            ("Start/Stop Execution", self.toggle_bulk_nds_execution)
        ]

        for text, cmd in buttons:
            btn = QPushButton(text)
            btn.setFont(FONT_BUTTON)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: {COLOR_FG};
                    border: 1px solid {COLOR_FG};
                    border-radius: 5px;
                    padding: 6px;
                    min-height: 28px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_HOVER};
                }}
                QPushButton:pressed {{
                    background-color: #A9A9A9;
                }}
            """)
            btn.clicked.connect(cmd)
            layout.addWidget(btn)

        self.channel_combo_bulk_nds = QComboBox()
        self.channel_combo_bulk_nds.addItems(self.loaded_channels)
        self.channel_combo_bulk_nds.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
        """)
        self.channel_combo_bulk_nds.currentTextChanged.connect(self.on_channel_select_bulk_nds)
        layout.addWidget(self.channel_combo_bulk_nds)

        layout.addWidget(QLabel("Frame Type:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.bulk_frametype_edit = QLineEdit()
        self.bulk_frametype_edit.setEnabled(False)
        self.bulk_frametype_edit.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        layout.addWidget(self.bulk_frametype_edit)

        layout.addWidget(QLabel("Select Server:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.bulk_host_combo = QComboBox()
        self.bulk_host_combo.addItem("nds.gwosc.org")
        self.bulk_host_combo.setCurrentIndex(0)
        self.selected_bulk_host = "nds.gwosc.org"
        self.bulk_host_combo.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
        """)
        self.bulk_host_combo.currentTextChanged.connect(lambda text: setattr(self, 'selected_bulk_host', text))
        layout.addWidget(self.bulk_host_combo)

        layout.addStretch()
        self.bulk_nds_tab.setLayout(layout)

    def setup_assoc_ui(self):
        layout = QVBoxLayout()
        self.status_label_assoc = QLabel("Idle")
        self.status_label_assoc.setFont(FONT_LABEL)
        self.status_label_assoc.setStyleSheet(f"color: {COLOR_FG}; background-color: transparent;")
        layout.addWidget(self.status_label_assoc)

        buttons = [
            ("Select Time CSV", self.select_time_csv),
            ("Import Channel CSV", self.select_channel_csv),
            ("Select Output Dir", self.select_output_dir),
            ("Select Time Segments", self.open_segments_dialog),
            ("Start/Stop Execution", self.toggle_assoc_execution)
        ]

        for text, cmd in buttons:
            btn = QPushButton(text)
            btn.setFont(FONT_BUTTON)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: {COLOR_FG};
                    border: 1px solid {COLOR_FG};
                    border-radius: 5px;
                    padding: 6px;
                    min-height: 28px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_HOVER};
                }}
                QPushButton:pressed {{
                    background-color: #A9A9A9;
                }}
            """)
            btn.clicked.connect(cmd)
            layout.addWidget(btn)

        self.channel_combo_assoc = QComboBox()
        self.channel_combo_assoc.addItems(self.loaded_channels)
        self.channel_combo_assoc.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
        """)
        self.channel_combo_assoc.currentTextChanged.connect(self.on_channel_select_assoc)
        layout.addWidget(self.channel_combo_assoc)

        layout.addWidget(QLabel("Frame Type:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.frametype_edit = QLineEdit()
        self.frametype_edit.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        layout.addWidget(self.frametype_edit)

        layout.addWidget(QLabel("Select Server:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.host_combo = QComboBox()
        hosts = [
            "datafind2.ldas.ligo-la.caltech.edu:80",
            "datafind.ldas.cit:80",
            "10.21.201.15:80",
            "datafind.gwosc.org",
            "nds.gwosc.org"
        ]
        self.host_combo.addItems(hosts)
        self.host_combo.setCurrentIndex(0)
        self.selected_host = hosts[0]
        self.host_combo.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
        """)
        self.host_combo.currentTextChanged.connect(lambda text: setattr(self, 'selected_host', text))
        layout.addWidget(self.host_combo)

        layout.addStretch()
        self.assoc_tab.setLayout(layout)

    def refresh_osdf_data(self):
        self.status_label_osdf.setText("Refreshing OSDF data...")
        self.log_signal.emit("Refreshing OSDF data...", "info")
        try:
            for _, det_code in self.detectors:
                try:
                    # Use gw_data_find to list available frame types for the detector
                    cmd = ["gw_data_find", "-r", "datafind.gwosc.org", "-o", det_code, "--show-types"]
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    frame_types = []
                    for line in result.stdout.strip().split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            frame_types.append(line)
                    self.frame_types[det_code] = frame_types if frame_types else ["No frame types available"]
                    self.log_signal.emit(f"Fetched {len(frame_types)} frame types for {det_code}", "info")
                except subprocess.CalledProcessError as e:
                    self.log_signal.emit(f"Error fetching frame types for {det_code}: {e}", "error")
                    self.frame_types[det_code] = ["No frame types available"]
                except Exception as e:
                    self.log_signal.emit(f"Unexpected error fetching frame types for {det_code}: {e}", "error")
                    self.frame_types[det_code] = ["No frame types available"]

            # Update frame type list if a detector is selected
            if self.selected_detector_code and self.selected_detector_code in self.frame_types:
                self.osdf_frametype_list.clear()
                self.osdf_frametype_list.addItems(self.frame_types[self.selected_detector_code])
                self.log_signal.emit(f"Updated frame types for {self.selected_detector_code}", "info")

            self.status_label_osdf.setText("OSDF data refreshed")
            self.log_signal.emit("OSDF data refreshed successfully", "success")
        except Exception as e:
            self.log_signal.emit(f"Error refreshing OSDF data: {e}", "error")
            self.status_label_osdf.setText("Idle")

    def refresh_nds_data(self):
        self.status_label_nds.setText("Refreshing NDS data...")
        self.log_signal.emit("Refreshing NDS data...", "info")
        try:
            for _, det_code in self.nds_detectors:
                try:
                    chanlist = ChannelList.query_nds2(f'{det_code}:*', host='nds.gwosc.org')
                    groups = {}
                    channels = []
                    for chan in chanlist:
                        match = re.match(r'^(H1|L1|V1|K1):([A-Z]+)-?.*', chan.name)
                        if match:
                            group = match.group(2)
                            channels.append((chan.name, str(chan.sample_rate)))
                            if group not in groups:
                                groups[group] = []
                            groups[group].append((chan.name, str(chan.sample_rate)))
                    self.nds_channels[det_code] = channels
                    self.nds_groups[det_code] = sorted(groups.keys())
                except Exception as e:
                    self.log_signal.emit(f"Error fetching channels for {det_code}: {e}", "error")
                    self.nds_channels[det_code] = []
                    self.nds_groups[det_code] = ["No groups available"]

            if self.selected_nds_detector_code and self.selected_nds_detector_code in self.nds_groups:
                self.nds_group_list.clear()
                self.nds_group_list.addItems(self.nds_groups[self.selected_nds_detector_code])

            self.status_label_nds.setText("NDS data refreshed")
            self.log_signal.emit("NDS data refreshed successfully", "success")
        except Exception as e:
            self.log_signal.emit(f"Error refreshing NDS data: {e}", "error")
            self.status_label_nds.setText("Idle")

    def on_detector_select(self, current, previous):
        if current:
            self.selected_detector = current.text()
            self.selected_detector_code = next(code for name, code in self.detectors if name == self.selected_detector)
            self.osdf_frametype_list.clear()
            if self.selected_detector_code in self.frame_types:
                self.osdf_frametype_list.addItems(self.frame_types[self.selected_detector_code])
            else:
                self.osdf_frametype_list.addItems(["No frame types available"])
            self.osdf_segments_list.clear()
            self.selected_osdf_frametype = None
            self.selected_osdf_segments = []

    def on_nds_detector_select(self, current, previous):
        if current:
            self.selected_nds_detector = current.text()
            self.selected_nds_detector_code = next(code for name, code in self.nds_detectors if name == self.selected_nds_detector)
            self.nds_group_list.clear()
            if self.selected_nds_detector_code in self.nds_groups:
                self.nds_group_list.addItems(self.nds_groups[self.selected_nds_detector_code])
            else:
                self.nds_group_list.addItems(["No groups available"])
            self.nds_channel_list.clear()
            self.nds_segments_list.clear()
            self.selected_nds_group = None
            self.selected_nds_channel = None
            self.selected_nds_segments = []

    def on_nds_group_select(self, current, previous):
        if current:
            self.selected_nds_group = current.text()
            self.nds_channel_list.clear()
            if (self.selected_nds_detector_code and 
                self.selected_nds_group and 
                self.selected_nds_group != "No groups available"):
                channels = [(name, rate) for name, rate in self.nds_channels.get(self.selected_nds_detector_code, []) 
                           if re.match(rf'^{self.selected_nds_detector_code}:{self.selected_nds_group}-?.*', name)]
                self.nds_channel_list.addItems([f"{name} ({rate})" for name, rate in channels])
            else:
                self.nds_channel_list.addItems(["No channels available"])
            self.nds_segments_list.clear()
            self.selected_nds_channel = None
            self.selected_nds_segments = []

    def on_osdf_frametype_select(self, current, previous):
        if current:
            self.selected_osdf_frametype = current.text()
            self.osdf_segments_list.clear()
            if self.selected_detector_code and self.selected_osdf_frametype and self.selected_osdf_frametype != "No frame types available":
                try:
                    cmd = ["gw_data_find", "-r", "datafind.gwosc.org", "-o", self.selected_detector_code, "-t", self.selected_osdf_frametype, "--show-times"]
                    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                    segments = []
                    display_segments = []
                    for line in result.stdout.strip().split("\n"):
                        line = line.strip()
                        if line and not line.startswith("#"):
                            try:
                                parts = line.split()
                                if len(parts) >= 4:
                                    seg_id, start, end, duration = parts[:4]
                                    start, end, duration = int(start), int(end), int(duration)
                                    segments.append(f"{start}_{end}")
                                    display_segments.append(f"{seg_id} {start}_{end} ({duration}s)")
                                else:
                                    self.append_output(f"Skipping invalid segment line: {line} (insufficient fields)", "warning")
                            except ValueError as e:
                                self.append_output(f"Skipping invalid segment line: {line} ({e})", "warning")
                                continue
                    self.time_segments[(self.selected_detector_code, self.selected_osdf_frametype)] = segments if segments else ["No segments available"]
                    self.osdf_segments_list.addItems(display_segments if display_segments else ["No segments available"])
                except subprocess.CalledProcessError as e:
                    self.append_output(f"Error fetching time segments for {self.selected_detector_code}/{self.selected_osdf_frametype}: {e}", "error")
                    self.time_segments[(self.selected_detector_code, self.selected_osdf_frametype)] = ["No segments available"]
                    self.osdf_segments_list.addItems(["No segments available"])
                except Exception as e:
                    self.append_output(f"Unexpected error fetching time segments: {e}", "error")
                    self.osdf_segments_list.addItems(["No segments available"])
            else:
                self.osdf_segments_list.addItems(["No segments available"])
            self.selected_osdf_segments = []

    def download_osdf_data(self):
        if not self.selected_detector_code or not self.selected_osdf_frametype or self.selected_osdf_frametype == "No frame types available":
            self.append_output("Please select a detector and frame type.", "warning")
            QMessageBox.warning(self, "Warning", "Please select a detector and frame type.")
            return

        self.selected_osdf_segments = [item.text().split()[1].split(" ")[0] for item in self.osdf_segments_list.selectedItems()]
        custom_start = self.custom_start_edit.text().strip()
        custom_end = self.custom_end_edit.text().strip()
        segments = self.selected_osdf_segments
        if custom_start and custom_end:
            try:
                start = int(float(custom_start))
                end = int(float(custom_end))
                if start >= end:
                    self.append_output("Custom GPS start must be less than end.", "error")
                    QMessageBox.critical(self, "Error", "Custom GPS start must be less than end.")
                    return
                segments.append(f"{start}_{end}")
            except ValueError:
                self.append_output("Invalid custom GPS times. Must be numeric.", "error")
                QMessageBox.critical(self, "Error", "Invalid custom GPS times. Must be numeric.")
                return

        if not segments:
            self.append_output("No segments selected or provided.", "warning")
            QMessageBox.warning(self, "Warning", "No segments selected or provided.")
            return

        self.execution_running = True
        self.status_label_osdf.setText("Downloading OSDF data...")
        self.append_output("Starting OSDF data download...", "info")
        threading.Thread(target=self.run_osdf_download, args=(segments,), daemon=True).start()

    def download_nds_data(self):
        if not self.selected_nds_detector_code or not self.selected_nds_channel:
            self.log_signal.emit("Please select a detector and channel.", "warning")
            QMessageBox.warning(self, "Warning", "Please select a detector and channel.")
            return

        self.selected_nds_segments = [item.text().split()[1].split(" ")[0] for item in self.nds_segments_list.selectedItems()]
        custom_start = self.nds_custom_start_edit.text().strip()
        custom_end = self.nds_custom_end_edit.text().strip()
        segments = self.selected_nds_segments
        if custom_start and custom_end:
            try:
                start = int(float(custom_start))
                end = int(float(custom_end))
                if start >= end:
                    self.log_signal.emit("Custom GPS start must be less than end.", "error")
                    QMessageBox.critical(self, "Error", "Custom GPS start must be less than end.")
                    return
                segments.append(f"{start}_{end}")
            except ValueError:
                self.log_signal.emit("Invalid custom GPS times. Must be numeric.", "error")
                QMessageBox.critical(self, "Error", "Invalid custom GPS times. Must be numeric.")
                return

        if not segments:
            self.log_signal.emit("No segments selected or provided.", "warning")
            QMessageBox.warning(self, "Warning", "No segments selected or provided.")
            return

        self.execution_running = True
        self.status_label_nds.setText("Downloading NDS data...")
        self.log_signal.emit("Starting NDS data download...", "info")
        threading.Thread(target=self.run_gravfetch_public, args=(segments, False), daemon=True).start()

    def run_osdf_download(self, segments):
        try:
            os.makedirs(self.gwfout_path, exist_ok=True)
            channel = f"{self.selected_detector_code}:{self.selected_osdf_frametype}"
            ch_dir = os.path.join(self.gwfout_path, channel.replace(":", "_"))
            os.makedirs(ch_dir, exist_ok=True)
            fin_path = os.path.join(ch_dir, "fin.ffl")
            timeout_duration = 2  # Seconds between downloads
            url_fetch_delay = 1   # Seconds after fetching URLs
            host = "https://datafind.gw-openscience.org"

            downloaded_count = 0
            for seg in segments:
                if not self.execution_running:
                    self.log_signal.emit("OSDF download stopped by user.", "warning")
                    break
                try:
                    start, end = map(int, seg.split("_"))
                    self.log_signal.emit(f"Finding URLs for {channel} {start}-{end}...", "info")
                    segment_dir = os.path.join(ch_dir, f"{start}_{end}")
                    os.makedirs(segment_dir, exist_ok=True)
                    try:
                        urls = find_urls(self.selected_detector_code, self.selected_osdf_frametype, start, end, urltype='osdf', host=host)
                    except Exception as e:
                        self.log_signal.emit(f"Error fetching URLs for {channel} {start}-{end}: {e}\n{traceback.format_exc()}", "error")
                        continue
                    if not urls:
                        self.log_signal.emit(f"No files found for {start} to {end}", "warning")
                        continue
                    self.log_signal.emit(f"Found {len(urls)} URLs for {start}-{end}", "info")
                    self.log_signal.emit(f"URLs: {urls}", "info")
                    # Check segment coverage
                    url_times = []
                    for url in urls:
                        url_parts = url.split("/")[-1].split("-")
                        timestamp = int(url_parts[-2])
                        duration = int(url_parts[-1].replace(".gwf", ""))
                        url_times.append((timestamp, timestamp + duration))
                    url_times.sort()
                    expected_start = start
                    for ts, te in url_times:
                        if ts > expected_start:
                            self.log_signal.emit(f"Gap in coverage: {expected_start} to {ts}", "warning")
                        expected_start = max(expected_start, te)
                    if expected_start < end:
                        self.log_signal.emit(f"Gap in coverage: {expected_start} to {end}", "warning")
                    if len(urls) > 1:
                        self.log_signal.emit(f"Multiple URLs ({len(urls)}) for {start}-{end}, saving each to a unique file", "warning")
                    time.sleep(url_fetch_delay)  # 1-second delay after fetching URLs
                    for url in urls:
                        if not self.execution_running:
                            self.log_signal.emit("OSDF download stopped by user.", "warning")
                            break
                        # Extract timestamp and duration from URL
                        url_parts = url.split("/")[-1].split("-")
                        timestamp = url_parts[-2]
                        duration = url_parts[-1].replace(".gwf", "")
                        filename = f"{channel.replace(':','_')}_{timestamp}_{duration}.gwf"
                        filepath = os.path.join(segment_dir, filename)
                        if os.path.exists(filepath):
                            self.log_signal.emit(f"File {filename} already downloaded for {channel}. Skipping.", "info")
                            continue
                        self.log_signal.emit(f"Checking availability of {url}...", "info")
                        try:
                            head_response = rp.head(url, timeout=15)
                            if head_response.status_code != 200:
                                self.log_signal.emit(f"URL unavailable: {url} (Status: {head_response.status_code})", "warning")
                                continue
                            expected_size = int(head_response.headers.get('Content-Length', 0))
                            self.log_signal.emit(f"URL {url} is available, expected size: {expected_size} bytes", "info")
                        except RequestException as e:
                            self.log_signal.emit(f"Failed to check {url}: {e}\n{traceback.format_exc()}", "error")
                            continue
                        self.log_signal.emit(f"Downloading: {url}", "info")
                        max_retries = 5
                        for attempt in range(max_retries):
                            try:
                                content = rp.get(url, timeout=120).content
                                actual_size = len(content)
                                if expected_size > 0 and actual_size != expected_size:
                                    self.log_signal.emit(f"Size mismatch for {url}: expected {expected_size}, got {actual_size}", "error")
                                    if attempt < max_retries - 1:
                                        self.log_signal.emit(f"Retrying {url} (attempt {attempt + 2}/{max_retries})...", "info")
                                        time.sleep(timeout_duration)
                                        continue
                                    break
                                self.log_signal.emit(f"Downloaded {actual_size} bytes for {url}", "info")
                                with open(filepath, "wb") as f:
                                    f.write(content)
                                saved_size = os.path.getsize(filepath)
                                self.log_signal.emit(f"Saved: {filepath} ({saved_size} bytes)", "success")
                                rel_path = os.path.relpath(filepath, os.getcwd()).replace("\\", "/")
                                dt = int(duration)
                                with open(fin_path, "a") as fin:
                                    fin.write(f"./{rel_path} {timestamp} {dt} 0 0\n")
                                downloaded_count += 1
                                self.log_signal.emit(f"Waiting {timeout_duration}s before next download...", "info")
                                time.sleep(timeout_duration)
                                break  # Success, move to next URL
                            except RequestException as e:
                                self.log_signal.emit(f"Failed to download {url}: {e}\n{traceback.format_exc()}", "error")
                                if attempt < max_retries - 1:
                                    self.log_signal.emit(f"Retrying {url} (attempt {attempt + 2}/{max_retries})...", "info")
                                    time.sleep(timeout_duration)
                                else:
                                    self.log_signal.emit(f"Max retries reached for {url}", "error")
                                continue
                except ValueError as e:
                    self.log_signal.emit(f"Invalid segment format {seg}: {e}", "error")
                    continue
                except Exception as e:
                    self.log_signal.emit(f"Error processing segment {seg} for {channel}: {e}\n{traceback.format_exc()}", "error")
                    continue

            if downloaded_count > 0:
                self.log_signal.emit(f"Downloaded {downloaded_count} files successfully.", "success")
                if channel not in self.loaded_channels:
                    self.loaded_channels.append(channel)
                    self.save_history()
            self.execution_running = False
            self.status_label_osdf.setText("Execution Finished")
            self.log_signal.emit("OSDF download complete.", "success")
        except Exception as e:
            self.log_signal.emit(f"Error in OSDF download: {e}\n{traceback.format_exc()}", "error")
            self.execution_running = False
            self.status_label_osdf.setText("OSDF Download Failed")

    def select_time_csv(self):
        file, _ = QFileDialog.getOpenFileName(self, "Select Time CSV", "", "CSV files (*.csv)")
        if file:
            try:
                self.time_csv_file = file
                self.time_ranges = pd.read_csv(self.time_csv_file)
                if 'Start' in self.time_ranges.columns and 'End' in self.time_ranges.columns:
                    self.time_ranges = self.time_ranges.rename(columns={'Start': 'GPSstart', 'End': 'GPSend'})
                elif not all(col in self.time_ranges.columns for col in ['GPSstart', 'GPSend']):
                    self.time_ranges = pd.read_csv(self.time_csv_file, header=None, names=['GPSstart', 'GPSend'], skiprows=1)
                try:
                    self.time_ranges['GPSstart'] = pd.to_numeric(self.time_ranges['GPSstart'], errors='raise')
                    self.time_ranges['GPSend'] = pd.to_numeric(self.time_ranges['GPSend'], errors='raise')
                    invalid_rows = self.time_ranges[self.time_ranges['GPSstart'] >= self.time_ranges['GPSend']]
                    if not invalid_rows.empty:
                        self.append_output(f"Invalid segments found: GPSstart >= GPSend in {len(invalid_rows)} rows", "warning")
                        self.time_ranges = self.time_ranges[self.time_ranges['GPSstart'] < self.time_ranges['GPSend']]
                        if self.time_ranges.empty:
                            raise ValueError("No valid segments after filtering invalid rows (GPSstart >= GPSend)")
                except ValueError as e:
                    self.append_output(f"Error in Time CSV: Columns must contain numeric values. {e}", "error")
                    QMessageBox.critical(self, "Error", "Time CSV must have numeric GPSstart and GPSend columns.")
                    self.time_ranges = None
                    return
                current_tab = self.public_subtabs.currentIndex()
                if current_tab == 0:
                    status_label = self.status_label_osdf
                elif current_tab == 1:
                    status_label = self.status_label_nds
                else:
                    status_label = self.status_label_bulk_nds
                status_label.setText(f"Selected Time CSV: {os.path.basename(self.time_csv_file)}")
                self.append_output(f"Selected Time CSV: {self.time_csv_file}", "success")
            except Exception as e:
                self.append_output(f"Error loading Time CSV: {e}", "error")
                QMessageBox.critical(self, "Error", f"Failed to load Time CSV: {e}")
                self.time_ranges = None

    def select_channel_csv(self):
        file, _ = QFileDialog.getOpenFileName(self, "Select Channel CSV", "", "CSV files (*.csv)")
        if file:
            try:
                self.channel_csv_file = file
                channels_df = pd.read_csv(self.channel_csv_file, header=None, skiprows=1, names=["Channel", "Sample Rate"])
                self.loaded_channels = list(channels_df["Channel"])
                self.channel_to_rate.update(dict(zip(channels_df["Channel"], channels_df["Sample Rate"])))
                self.channel_combo_assoc.clear()
                self.channel_combo_bulk_nds.clear()
                self.channel_combo_assoc.addItems(self.loaded_channels)
                self.channel_combo_bulk_nds.addItems(self.loaded_channels)
                if self.loaded_channels:
                    self.selected_bulk_nds_channel = self.loaded_channels[0]
                    self.channel_combo_bulk_nds.setCurrentText(self.loaded_channels[0])
                    self.log_signal.emit(f"Debug: Selected default channel {self.selected_bulk_nds_channel} for Bulk NDS", "info")
                else:
                    self.selected_bulk_nds_channel = None
                    self.log_signal.emit("Debug: No channels loaded from CSV", "warning")
                self.save_history()
                current_tab = self.public_subtabs.currentIndex()
                status_label = self.status_label_nds if current_tab == 1 else self.status_label_bulk_nds if current_tab == 2 else self.status_label_assoc
                status_label.setText(f"Imported Channel CSV: {os.path.basename(self.channel_csv_file)}")
                self.append_output(f"Imported Channel CSV: {self.channel_csv_file}", "success")
            except Exception as e:
                self.append_output(f"Error loading Channel CSV: {e}", "error")
                QMessageBox.critical(self, "Error", str(e))
                self.selected_bulk_nds_channel = None

    def on_channel_select_public(self, channel):
        self.selected_channel = channel

    def on_channel_select_assoc(self, channel):
        self.selected_channel = channel
        if channel:
            site_prefix = channel.split(':')[0]
            self.frametype_edit.setText(f"{site_prefix}_HOFT_C02")
            self.selected_frametype = self.frametype_edit.text()

    def on_nds_channel_select(self, current, previous):
        if current:
            self.selected_nds_channel = current.text().split(" (")[0]
            self.nds_segments_list.clear()
            if (self.selected_nds_detector_code and 
                self.selected_nds_channel and 
                self.selected_nds_channel != "No channels available"):
                try:
                    start_time = 1238112018
                    end_time = 1238198418
                    chanlist = ChannelList([Channel(self.selected_nds_channel)])
                    available = chanlist.query_nds2_availability([chanlist[0]], start_time, end_time, host='nds.gwosc.org')
                    segments = []
                    display_segments = []
                    for chan in available:
                        for i, (start, end) in enumerate(available[chan]):
                            duration = end - start
                            segments.append(f"{start}_{end}")
                            display_segments.append(f"{i+1} {start}_{end} ({duration}s)")
                    self.nds_segments[self.selected_nds_channel] = segments if segments else ["No segments available"]
                    self.nds_segments_list.addItems(display_segments if display_segments else ["No segments available"])
                except Exception as e:
                    self.log_signal.emit(f"Error fetching time segments for {self.selected_nds_channel}: {e}", "error")
                    self.nds_segments[self.selected_nds_channel] = ["No segments available"]
                    self.nds_segments_list.addItems(["No segments available"])
            else:
                self.nds_segments_list.addItems(["No segments available"])
            self.selected_nds_segments = []

    def on_channel_select_bulk_nds(self, channel):
        self.selected_bulk_nds_channel = channel
        self.log_signal.emit(f"Debug: Selected channel {channel} for Bulk NDS", "info")

    def select_output_dir(self):
        directory = QFileDialog.getExistingDirectory(self, "Select Output Directory")
        if directory:
            self.gwfout_path = directory
            current_tab = self.public_subtabs.currentIndex()
            if current_tab == 0:
                status_label = self.status_label_osdf
            elif current_tab == 1:
                status_label = self.status_label_nds
            else:
                status_label = self.status_label_bulk_nds
            status_label.setText(f"Selected Output Dir: {self.gwfout_path}")
            self.save_history()
            self.append_output(f"Selected Output Dir: {self.gwfout_path}", "success")

    def open_segments_dialog(self):
        if self.time_ranges is None:
            self.append_output("Please select a valid Time CSV first.", "warning")
            QMessageBox.warning(self, "Warning", "Please select a valid Time CSV first.")
            return
        current_tab = self.public_subtabs.currentIndex()
        if current_tab == 2 and not self.selected_bulk_nds_channel:
            self.log_signal.emit("Please select a channel first.", "warning")
            QMessageBox.warning(self, "Warning", "Please select a channel first.")
            return
        elif current_tab == 1 and not self.selected_nds_channel:
            self.log_signal.emit("Please select a channel first.", "warning")
            QMessageBox.warning(self, "Warning", "Please select a channel first.")
            return
        elif current_tab == 0 and not self.selected_osdf_frametype:
            self.append_output("Please select a frame type first.", "warning")
            QMessageBox.warning(self, "Warning", "Please select a frame type first.")
            return

        segments = []
        try:
            for _, row in self.time_ranges.iterrows():
                try:
                    start = int(float(row['GPSstart']))
                    end = int(float(row['GPSend']))
                    segments.append(f"{start}_{end}")
                except (ValueError, TypeError) as e:
                    self.append_output(f"Skipping invalid segment: {row['GPSstart']}_{row['GPSend']} ({e})", "warning")
                    continue
        except Exception as e:
            self.append_output(f"Error parsing time segments: {e}", "error")
            QMessageBox.critical(self, "Error", f"Failed to parse time segments: {e}")
            return

        if not segments:
            self.append_output("No valid time segments found in the selected CSV.", "warning")
            QMessageBox.warning(self, "Warning", "No valid time segments found in the selected CSV.")
            return

        dialog = QMainWindow(self)
        dialog.setWindowTitle("Select Time Segments")
        dialog.setGeometry(200, 200, 500, 600)
        dialog.setStyleSheet("""
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                            stop:0 #1C2526, stop:1 #E8ECEF);
            }
        """)

        main_widget = GradientWidget()
        dialog.setCentralWidget(main_widget)
        layout = QVBoxLayout(main_widget)

        search_layout = QHBoxLayout()
        search_label = QLabel("Search:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;")
        search_edit = QLineEdit()
        search_edit.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #E8ECEF;
                color: #1C2526;
            }}
        """)
        search_layout.addWidget(search_label)
        search_layout.addWidget(search_edit)
        layout.addLayout(search_layout)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_widget = GradientWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_area.setWidget(scroll_widget)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #CED4DA;
                border-radius: 5px;
            }
        """)
        layout.addWidget(scroll_area)

        self.segment_checkboxes = {}
        for seg in segments:
            chk = QCheckBox(seg)
            chk.setFont(FONT_LABEL)
            chk.setStyleSheet(f"color: {COLOR_FG}; background-color: transparent;")
            scroll_layout.addWidget(chk)
            self.segment_checkboxes[seg] = chk

        def filter_segments():
            search_term = search_edit.text().lower()
            for seg, chk in self.segment_checkboxes.items():
                chk.setVisible(search_term in seg.lower())

        search_edit.textChanged.connect(filter_segments)

        button_layout = QHBoxLayout()
        buttons = [
            ("Select All", lambda: [chk.setChecked(True) for chk in self.segment_checkboxes.values()]),
            ("Deselect All", lambda: [chk.setChecked(False) for chk in self.segment_checkboxes.values()]),
            ("Deselect Processed", self.deselect_processed),
            ("Confirm", lambda: self.confirm_segments(dialog))
        ]

        for text, cmd in buttons:
            btn = QPushButton(text)
            btn.setFont(FONT_BUTTON)
            btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: {COLOR_FG};
                    border: 1px solid {COLOR_FG};
                    border-radius: 5px;
                    padding: 6px;
                    min-height: 28px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_HOVER};
                }}
                QPushButton:pressed {{
                    background-color: #A9A9A9;
                }}
            """)
            btn.clicked.connect(cmd)
            button_layout.addWidget(btn)

        layout.addLayout(button_layout)
        dialog.show()

    def confirm_segments(self, dialog):
        self.selected_segments = [seg for seg, chk in self.segment_checkboxes.items() if chk.isChecked()]
        if not self.selected_segments:
            self.append_output("No segments selected.", "warning")
            QMessageBox.warning(self, "Warning", "No segments selected.")
        else:
            self.append_output(f"Selected {len(self.selected_segments)} time segments.", "success")
            dialog.close()

    def deselect_processed(self):
        current_tab = self.public_subtabs.currentIndex()
        ch = self.selected_bulk_nds_channel if current_tab == 2 else self.selected_nds_channel if current_tab == 1 else self.selected_channel
        if not ch:
            self.append_output("No channel selected for deselecting processed segments.", "warning")
            return
        ch_dir = os.path.join(self.gwfout_path, ch.replace(":", "_"))
        for seg, chk in self.segment_checkboxes.items():
            tdir = os.path.join(ch_dir, seg)
            outfile = os.path.join(tdir, f"{ch.replace(':','_')}_{seg}.gwf")
            if os.path.exists(tdir) and os.path.exists(outfile):
                chk.setChecked(False)
                self.append_output(f"Deselected processed segment: {seg}", "info")

    def save_history(self):
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump({"gwfout_path": self.gwfout_path, "channels": self.loaded_channels}, f, indent=2)
            self.append_output("History saved.", "info")
        except Exception as e:
            self.append_output(f"Error saving history: {e}", "error")

    def is_internet_connected(self):
        try:
            subprocess.check_call(["ping", "-c" if sys.platform.startswith('linux') else "-n", "1", "google.com"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except subprocess.CalledProcessError:
            return False

    def wait_for_internet(self, channel, start, end):
        log_file = "internet_disconnection_log.txt"
        with open(log_file, "a") as log:
            log.write(f"{datetime.now()}: Internet disconnected while fetching {channel} from {start} to {end}\n")
        while not self.is_internet_connected():
            self.log_signal.emit("Internet disconnected. Waiting 10 minutes to retry...", "warning")
            time.sleep(600)
        self.log_signal.emit("Internet reconnected. Resuming download...", "success")

    def toggle_public_execution(self):
        if self.execution_running:
            self.execution_running = False
            self.status_label_nds.setText("Execution Stopped")
            self.log_signal.emit("NDS execution stopped.", "info")
        else:
            self.selected_nds_channel = self.nds_channel_list.currentItem().text().split(" (")[0] if self.nds_channel_list.currentItem() else None
            self.selected_nds_segments = [item.text().split()[1].split(" ")[0] for item in self.nds_segments_list.selectedItems()]
            custom_start = self.nds_custom_start_edit.text().strip()
            custom_end = self.nds_custom_end_edit.text().strip()
            segments = self.selected_nds_segments
            if custom_start and custom_end:
                try:
                    start = int(float(custom_start))
                    end = int(float(custom_end))
                    if start >= end:
                        self.log_signal.emit("Custom GPS start must be less than end.", "error")
                        QMessageBox.critical(self, "Error", "Custom GPS start must be less than end.")
                        return
                    segments.append(f"{start}_{end}")
                except ValueError:
                    self.log_signal.emit("Invalid custom GPS times. Must be numeric.", "error")
                    QMessageBox.critical(self, "Error", "Invalid custom GPS times. Must be numeric.")
                    return
            if not self.selected_nds_channel or not segments:
                self.log_signal.emit("Please select a channel and time segments.", "warning")
                QMessageBox.warning(self, "Warning", "Please select a channel and time segments.")
                return
            self.execution_running = True
            self.status_label_nds.setText("Execution Started")
            self.log_signal.emit("NDS execution started...", "info")
            threading.Thread(target=self.run_gravfetch_public, args=(segments, False), daemon=True).start()

    def toggle_assoc_execution(self):
        if self.execution_running:
            self.execution_running = False
            self.status_label_assoc.setText("Execution Stopped")
            self.append_output("Execution stopped.", "info")
        else:
            self.selected_channel = self.channel_combo_assoc.currentText()
            self.selected_frametype = self.frametype_edit.text()
            self.selected_host = self.host_combo.currentText()
            if not all([self.time_ranges is not None, self.selected_channel, self.selected_segments, self.selected_frametype, self.selected_host]):
                self.append_output("Please select time CSV, a channel, frame type, server, and time segments.", "warning")
                QMessageBox.warning(self, "Warning", "Please select time CSV, a channel, frame type, server, and time segments.")
                return
            self.execution_running = True
            self.status_label_assoc.setText("Execution Started")
            self.append_output("Execution started...", "info")
            threading.Thread(target=self.run_gravfetch_assoc, daemon=True).start()

    def toggle_bulk_nds_execution(self):
        if self.execution_running:
            self.execution_running = False
            self.status_label_bulk_nds.setText("Execution Stopped")
            self.log_signal.emit("Bulk NDS execution stopped.", "info")
        else:
            self.selected_bulk_nds_channel = self.channel_combo_bulk_nds.currentText()
            self.log_signal.emit(f"Debug: Selected channel for Bulk NDS: {self.selected_bulk_nds_channel}", "info")
            if not all([self.time_ranges is not None, self.selected_bulk_nds_channel, self.selected_segments, self.selected_bulk_nds_channel != ""]):
                self.log_signal.emit("Please select a valid time CSV, a channel, and time segments.", "warning")
                QMessageBox.warning(self, "Warning", "Please select a valid time CSV, a channel, and time segments.")
                return
            segments = []
            try:
                for _, row in self.time_ranges.iterrows():
                    try:
                        start = int(float(row['GPSstart']))
                        end = int(float(row['GPSend']))
                        segments.append(f"{start}_{end}")
                    except (ValueError, TypeError) as e:
                        self.log_signal.emit(f"Skipping invalid segment: {row['GPSstart']}_{row['GPSend']} ({e})", "warning")
                        continue
            except Exception as e:
                self.log_signal.emit(f"Error parsing time segments: {e}", "error")
                QMessageBox.critical(self, "Error", f"Failed to parse time segments: {e}")
                return
            if not segments:
                self.log_signal.emit("No valid time segments found in the selected CSV.", "warning")
                QMessageBox.warning(self, "Warning", "No valid time segments found in the selected CSV.")
                return
            self.execution_running = True
            self.status_label_bulk_nds.setText("Execution Started")
            self.log_signal.emit("Bulk NDS execution started...", "info")
            threading.Thread(target=self.run_gravfetch_public, args=(segments, True), daemon=True).start()

    def toggle_osdf_execution(self):
        if self.execution_running:
            self.execution_running = False
            self.status_label_osdf.setText("Execution Stopped")
            self.log_signal.emit("OSDF execution stopped.", "info")
        else:
            if not self.selected_detector_code or not self.selected_osdf_frametype or self.selected_osdf_frametype == "No frame types available":
                self.log_signal.emit("Please select a detector and frame type.", "warning")
                QMessageBox.warning(self, "Warning", "Please select a detector and frame type.")
                return

            self.selected_osdf_segments = [item.text().split()[1].split(" ")[0] for item in self.osdf_segments_list.selectedItems()]
            custom_start = self.custom_start_edit.text().strip()
            custom_end = self.custom_end_edit.text().strip()
            segments = self.selected_osdf_segments
            if custom_start and custom_end:
                try:
                    start = int(float(custom_start))
                    end = int(float(custom_end))
                    if start >= end:
                        self.log_signal.emit("Custom GPS start must be less than end.", "error")
                        QMessageBox.critical(self, "Error", "Custom GPS start must be less than end.")
                        return
                    segments.append(f"{start}_{end}")
                except ValueError:
                    self.log_signal.emit("Invalid custom GPS times. Must be numeric.", "error")
                    QMessageBox.critical(self, "Error", "Invalid custom GPS times. Must be numeric.")
                    return

            if not segments:
                self.log_signal.emit("No segments selected or provided.", "warning")
                QMessageBox.warning(self, "Warning", "No segments selected or provided.")
                return

            self.execution_running = True
            self.status_label_osdf.setText("Execution Started")
            self.log_signal.emit("OSDF execution started...", "info")
            threading.Thread(target=self.run_osdf_download, args=(segments,), daemon=True).start()

    def run_gravfetch_public(self, segments, is_bulk=False):
        try:
            ch = self.selected_bulk_nds_channel if is_bulk else self.selected_nds_channel
            if not ch or ch == "":
                self.log_signal.emit("No valid channel selected for NDS execution.", "error")
                self.execution_running = False
                status_label = self.status_label_bulk_nds if is_bulk else self.status_label_nds
                status_label.setText("Execution Failed")
                return
            os.makedirs(self.gwfout_path, exist_ok=True)
            ch_dir = os.path.join(self.gwfout_path, ch.replace(":", "_"))
            os.makedirs(ch_dir, exist_ok=True)
            fin_path = os.path.join(ch_dir, "fin.ffl")
            current_dir = os.getcwd()

            with open(fin_path, 'a') as fin:
                for seg in segments:
                    if not self.execution_running:
                        self.log_signal.emit("NDS execution stopped by user.", "warning")
                        break
                    try:
                        start, end = map(int, seg.split("_"))
                        tdir = os.path.join(ch_dir, f"{start}_{end}")
                        os.makedirs(tdir, exist_ok=True)
                        outfile = os.path.join(tdir, f"{ch.replace(':','_')}_{start}_{end}.gwf")
                        if os.path.exists(outfile):
                            self.log_signal.emit(f"File {outfile} already fetched for {ch}. Skipping.", "info")
                            continue
                        while self.execution_running:
                            try:
                                self.log_signal.emit(f"Fetching {ch} from {start} to {end}...", "info")
                                data = TimeSeries.fetch(ch, start=start, end=end, host="nds.gwosc.org")
                                actual_start = int(data.times[0].value)
                                actual_end = int(data.times[-1].value)
                                if actual_start > start:
                                    self.log_signal.emit(f"Gap in coverage: {start} to {actual_start}", "warning")
                                if actual_end < end:
                                    self.log_signal.emit(f"Gap in coverage: {actual_end} to {end}", "warning")
                                data.write(outfile)
                                saved_size = os.path.getsize(outfile)
                                self.log_signal.emit(f"Saved: {outfile} ({saved_size} bytes)", "success")
                                rel_path = os.path.relpath(outfile, current_dir).replace("\\", "/")
                                dt = end - start
                                fin.write(f"./{rel_path} {start} {dt} 0 0\n")
                                break
                            except (ValueError, RuntimeError, socket.error) as e:
                                self.log_signal.emit(f"Error fetching {ch} {start}-{end}: {e}", "error")
                                if os.path.exists(outfile):
                                    os.remove(outfile)
                                    self.log_signal.emit(f"Deleted interrupted file: {outfile}", "info")
                                if not self.execution_running:
                                    self.log_signal.emit("Execution stopped by user.", "warning")
                                    break
                                self.wait_for_internet(ch, start, end)
                            except Exception as e:
                                self.log_signal.emit(f"Unexpected error fetching {ch} {start}-{end}: {e}\n{traceback.format_exc()}", "error")
                                break
                    except ValueError as e:
                        self.log_signal.emit(f"Invalid segment format {seg}: {e}", "error")
                        continue
                    except Exception as e:
                        self.log_signal.emit(f"Error processing segment {seg} for {ch}: {e}\n{traceback.format_exc()}", "error")
                        continue

            if ch not in self.loaded_channels and self.execution_running:
                self.loaded_channels.append(ch)
                self.save_history()

            self.execution_running = False
            status_label = self.status_label_bulk_nds if is_bulk else self.status_label_nds
            status_label.setText("Execution Finished")
            self.log_signal.emit("NDS execution complete.", "success")
        except Exception as e:
            self.log_signal.emit(f"Error in NDS execution: {e}\n{traceback.format_exc()}", "error")
            self.execution_running = False
            status_label = self.status_label_bulk_nds if is_bulk else self.status_label_nds
            status_label.setText("Execution Failed")

    def run_gravfetch_assoc(self):
        try:
            os.makedirs(self.gwfout_path, exist_ok=True)
            ch = self.selected_channel
            if not ch or ch == "":
                self.append_output("No valid channel selected for Assoc execution.", "error")
                self.execution_running = False
                self.status_label_assoc.setText("Execution Failed")
                return
            ch_dir = os.path.join(self.gwfout_path, ch.replace(":", "_"))
            os.makedirs(ch_dir, exist_ok=True)
            fin_path = os.path.join(ch_dir, "fin.ffl")
            current_dir = os.getcwd()

            with open(fin_path, 'a') as fin:
                for seg in self.selected_segments:
                    if not self.execution_running:
                        self.append_output("Execution stopped by user.", "warning")
                        break
                    try:
                        start, end = map(int, seg.split("_"))
                        tdir = os.path.join(ch_dir, f"{start}_{end}")
                        os.makedirs(tdir, exist_ok=True)
                        outfile = os.path.join(tdir, f"{ch.replace(':','_')}_{start}_{end}.gwf")
                        if os.path.exists(outfile):
                            self.append_output(f"Segment {seg} already fetched for {ch}. Skipping.", "info")
                            continue
                        while self.execution_running:
                            try:
                                self.append_output(f"Fetching {ch} from {start} to {end}...", "info")
                                site = self.selected_frametype[0]
                                frametype = self.selected_frametype
                                host = self.selected_host
                                urls = find_urls(site, frametype, start, end, host=host)
                                if not urls:
                                    self.append_output(f"No data available for {ch} {start}-{end}. Skipping.", "warning")
                                    continue
                                data = TimeSeries.read(urls, channel=ch, start=start, end=end)
                                data.write(outfile)
                                rel_path = os.path.relpath(outfile, current_dir).replace("\\", "/")
                                dt = end - start
                                fin.write(f"./{rel_path} {start} {dt} 0 0\n")
                                self.append_output(f"Saved to {outfile}", "success")
                                break
                            except (ValueError, RuntimeError, socket.error) as e:
                                self.append_output(f"Error fetching {ch} {start}-{end}: {e}", "error")
                                if os.path.exists(outfile):
                                    os.remove(outfile)
                                    self.append_output(f"Deleted interrupted file: {outfile}", "info")
                                if not self.execution_running:
                                    self.append_output("Execution stopped by user.", "warning")
                                    break
                                self.wait_for_internet(ch, start, end)
                            except Exception as e:
                                self.append_output(f"Unexpected error fetching {ch} {start}-{end}: {e}\n{traceback.format_exc()}", "error")
                                break
                    except ValueError as e:
                        self.append_output(f"Invalid segment format {seg}: {e}", "error")
                        continue
                    except Exception as e:
                        self.append_output(f"Error processing segment {seg} for {ch}: {e}\n{traceback.format_exc()}", "error")
                        continue

            if ch not in self.loaded_channels and self.execution_running:
                self.loaded_channels.append(ch)
                self.save_history()

            self.execution_running = False
            self.status_label_assoc.setText("Execution Finished")
            self.append_output("Execution complete.", "success")
        except Exception as e:
            self.append_output(f"Error in Assoc execution: {e}\n{traceback.format_exc()}", "error")
            self.execution_running = False
            self.status_label_assoc.setText("Execution Failed")
########################################################################################################################################
#############################################################    OMICRON    ############################################################
########################################################################################################################################

class OmicronApp(QWidget):
    append_output_signal = pyqtSignal(str, str)
    show_message_box_signal = pyqtSignal(str, str, str)

    def __init__(self, parent, append_output_callback):
            super().__init__(parent)
            self.project_dir = os.getcwd().replace("\\", "/")
            self.wsl_project_dir = f"/mnt/{self.project_dir[0].lower()}/{self.project_dir[2:]}"
            self.GWFOUT_DIRECTORY = "./GWFout"
            self.config_path = "config.txt"
            self.default_output_dir = "./OmicronOut"
            self.config_data = {}
            self.ui_elements = {}
            self.append_output_signal.connect(append_output_callback)
            self.show_message_box_signal.connect(self._show_message_box)

            # Create omicron.out file
            try:
                with open("omicron.out", "w") as f:
                    f.write("")
                self.append_output_signal.emit("Created omicron.out file\n", "info")
            except Exception as e:
                self.append_output_signal.emit(f"Error creating omicron.out: {e}\n", "error")
                self.show_message_box_signal.emit("Error", f"Error creating omicron.out: {e}", "critical")

            # Create default output directory
            if not os.path.exists(self.default_output_dir):
                os.makedirs(self.default_output_dir, exist_ok=True)
                self.append_output_signal.emit(f"Created default output directory: {self.default_output_dir}\n", "info")

            self.setup_ui()
            self.load_config()

    def setup_ui(self):
            layout = QVBoxLayout()
            layout.setSpacing(10)

            # Use editable dropdown for channels to allow custom input
            self.channel_combo = self.create_editable_dropdown(
                "Select Channel:", "DATA CHANNELS", ["No Channels Available"], layout
            )
            QTimer.singleShot(0, self.update_channel_options)
            self.create_file_selector("Select .ffl File:", "DATA FFL", layout)
            self.create_editable_dropdown("Sampling Frequency:", "DATA SAMPLEFREQUENCY", ["1024", "2048", "4096"], layout)

            button_layout = QHBoxLayout()
            custom_segs_btn = QPushButton("Custom Segs")
            custom_segs_btn.setFont(FONT_BUTTON)
            custom_segs_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: {COLOR_FG};
                    border: 1px solid {COLOR_FG};
                    border-radius: 5px;
                    padding: 6px;
                    min-height: 28px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_HOVER};
                }}
                QPushButton:pressed {{
                    background-color: #A9A9A9;
                }}
            """)
            custom_segs_btn.clicked.connect(self.open_custom_segs_dialog)
            
            save_btn = QPushButton("Save Config")
            save_btn.setFont(FONT_BUTTON)
            save_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: {COLOR_FG};
                    border: 1px solid {COLOR_FG};
                    border-radius: 5px;
                    padding: 6px;
                    min-height: 28px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_HOVER};
                }}
                QPushButton:pressed {{
                    background-color: #A9A9A9;
                }}
            """)
            save_btn.clicked.connect(self.save_config)
            start_btn = QPushButton("Start OMICRON")
            start_btn.setFont(FONT_BUTTON)
            start_btn.setStyleSheet(f"""
                QPushButton {{
                    background-color: {COLOR_ACCENT};
                    color: {COLOR_FG};
                    border: 1px solid {COLOR_FG};
                    border-radius: 5px;
                    padding: 6px;
                    min-height: 28px;
                }}
                QPushButton:hover {{
                    background-color: {COLOR_HOVER};
                }}
                QPushButton:pressed {{
                    background-color: #A9A9A9;
                }}
            """)
            start_btn.clicked.connect(self.run_omicron_script)
            button_layout.addWidget(custom_segs_btn)
            button_layout.addWidget(save_btn)
            button_layout.addWidget(start_btn)
            layout.addLayout(button_layout)

            param_frame = QFrame()
            param_layout = QVBoxLayout(param_frame)
            double_entry_sizer = QHBoxLayout()
            self.create_double_entry("Timing:", "PARAMETER TIMING", double_entry_sizer)
            self.create_double_entry("Frequency Range:", "PARAMETER FREQUENCYRANGE", double_entry_sizer)
            self.create_double_entry("Q-Range:", "PARAMETER QRANGE", double_entry_sizer)
            param_layout.addLayout(double_entry_sizer)

            single_entry_sizer = QHBoxLayout()
            self.create_entry("Mismatch Max:", "PARAMETER MISMATCHMAX", single_entry_sizer)
            self.create_entry("SNR Threshold:", "PARAMETER SNRTHRESHOLD", single_entry_sizer)
            self.create_entry("PSD Length:", "PARAMETER PSDLENGTH", single_entry_sizer)
            param_layout.addLayout(single_entry_sizer)
            layout.addWidget(param_frame)

            output_frame = QFrame()
            output_layout = QVBoxLayout(output_frame)
            self.create_file_selector("Select Output Directory:", "OUTPUT DIRECTORY", output_layout, is_directory=True)
            self.create_output_products_selection(output_layout)
            self.create_dropdown("Select Format:", "OUTPUT FORMAT", ["root", "hdf5"], output_layout)
            self.create_slider("Verbosity (0-3):", "OUTPUT VERBOSITY", 0, 3, output_layout)
            layout.addWidget(output_frame)
            layout.addWidget(QLabel("Username (optional, WSL on Windows):", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
            self.wsl_username_input = QLineEdit()
            self.wsl_username_input.setPlaceholderText("Enter username for WSL (Windows only) or leave blank")
            self.wsl_username_input.setStyleSheet(f"""
                QLineEdit {{
                    border: 1px solid {COLOR_FG};
                    border-radius: 5px;
                    padding: 4px;
                    background-color: #747576;
                    color: {COLOR_FG};
                }}
            """)
            layout.addWidget(self.wsl_username_input)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll_widget = QWidget()
            scroll_widget.setLayout(layout)
            scroll.setWidget(scroll_widget)
            scroll.setStyleSheet(f"""
                QScrollArea {{
                    border: 1px solid {COLOR_FG};
                    border-radius: 5px;
                }}
            """)
            main_layout = QVBoxLayout(self)
            main_layout.addWidget(scroll)
            self.setLayout(main_layout)

    def create_entry(self, label, key, layout):
        layout.addWidget(QLabel(label, font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        element = QLineEdit(self.config_data.get(key, ""))
        element.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        layout.addWidget(element)
        self.ui_elements[key] = element

    def create_double_entry(self, label, key, layout):
        layout.addWidget(QLabel(label, font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        values = self.config_data.get(key, "0 0").split()
        element1 = QLineEdit(values[0] if len(values) > 0 else "0")
        element1.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        element2 = QLineEdit(values[1] if len(values) > 1 else "0")
        element2.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        layout.addWidget(element1)
        layout.addWidget(element2)
        self.ui_elements[key] = (element1, element2)

    def create_file_selector(self, label, key, layout, is_directory=False):
        layout.addWidget(QLabel(label, font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        element = QLineEdit(self.config_data.get(key, ""))
        element.setReadOnly(True)
        element.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
        """)
        button = QPushButton("Select")
        button.setFont(FONT_BUTTON)
        button.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: {COLOR_FG};
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 6px;
                min-height: 28px;
            }}
            QPushButton:hover {{
                background-color: {COLOR_HOVER};
            }}
            QPushButton:pressed {{
                background-color: #A9A9A9;
            }}
        """)
        button.clicked.connect(lambda: self.select_file(element, is_directory))
        layout.addWidget(element)
        layout.addWidget(button)
        self.ui_elements[key] = element

    def create_output_products_selection(self, layout):
        layout.addWidget(QLabel("Select Output Products:", font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        self.ui_elements["OUTPUT PRODUCTS"] = {}
        for product in ["triggers", "html"]:
            chk = QCheckBox(product)
            chk.setFont(FONT_LABEL)
            chk.setStyleSheet(f"color: {COLOR_FG}; background-color: transparent;")
            layout.addWidget(chk)
            self.ui_elements["OUTPUT PRODUCTS"][product] = chk

    def create_dropdown(self, label, key, options, layout):
        layout.addWidget(QLabel(label, font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        element = QComboBox()
        element.addItems(options)
        element.setCurrentText(self.config_data.get(key, options[0]))
        element.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
        """)
        layout.addWidget(element)
        self.ui_elements[key] = element

    def create_editable_dropdown(self, label, key, options, layout):
        layout.addWidget(QLabel(label, font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        element = QComboBox()
        element.setEditable(True)
        element.addItems(options)
        element.setCurrentText(self.config_data.get(key, options[0]))
        element.setStyleSheet(f"""
            QComboBox {{
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 4px;
                background-color: #747576;
                color: {COLOR_FG};
            }}
            QComboBox::drop-down {{
                border: none;
            }}
        """)
        layout.addWidget(element)
        self.ui_elements[key] = element
        return element

    def create_slider(self, label, key, min_val, max_val, layout):
        layout.addWidget(QLabel(label, font=FONT_LABEL, styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))
        element = QSlider(Qt.Horizontal)
        element.setMinimum(min_val)
        element.setMaximum(max_val)
        element.setValue(int(self.config_data.get(key, min_val)))
        element.setStyleSheet(f"""
            QSlider::groove:horizontal {{
                border: 1px solid {COLOR_FG};
                height: 8px;
                background: #747576;
                margin: 2px 0;
                border-radius: 4px;
            }}
            QSlider::handle:horizontal {{
                background: {COLOR_FG};
                border: 1px solid {COLOR_FG};
                width: 18px;
                margin: -2px 0;
                border-radius: 3px;
            }}
        """)
        layout.addWidget(element)
        self.ui_elements[key] = element
        return element

    def select_file(self, text_ctrl, is_directory=False):
        file_path = None
        if is_directory:
            file_path = QFileDialog.getExistingDirectory(self, "Choose a directory")
        else:
            file_path, _ = QFileDialog.getOpenFileName(self, "Choose a file", "", "FFL files (*.ffl)")
        if file_path:
            relative_path = os.path.relpath(file_path, os.getcwd()).replace("\\", "/")
            text_ctrl.setText(relative_path)
            self.append_output_signal.emit(f"File selected: {relative_path}\n", "info")

    def update_ui_from_config(self):
        for key, element in self.ui_elements.items():
            if isinstance(element, QLineEdit):
                element.setText(self.config_data.get(key, ""))
            elif isinstance(element, QComboBox):
                element.setCurrentText(self.config_data.get(key, element.itemText(0)))
            elif isinstance(element, tuple):
                values = self.config_data.get(key, "0 0").split()
                element[0].setText(values[0] if len(values) > 0 else "0")
                element[1].setText(values[1] if len(values) > 1 else "0")
            elif isinstance(element, QSlider):
                element.setValue(int(self.config_data.get(key, 0)))
            elif isinstance(element, dict):
                products = self.config_data.get(key, "").split()
                for product, chk in element.items():
                    chk.setChecked(product in products)

    def run_omicron_script(self):
        self.append_output_signal.emit("Starting OMICRON script...\n", "info")
        threading.Thread(target=self.start_omicron_process, daemon=True).start()

    def start_omicron_process(self):
        try:
            ffl_widget = self.ui_elements["DATA FFL"]
            ffl_file = ffl_widget.text().strip() if ffl_widget else ""
            if not ffl_file or not os.path.exists(ffl_file):
                self.append_output_signal.emit("Error: No valid .ffl file selected.\n", "error")
                self.show_message_box_signal.emit("Error", "No valid .ffl file selected.", "critical")
                return
            with open(ffl_file, "r") as f:
                lines = [line.strip().split() for line in f if line.strip()]
            if not lines or len(lines[0]) < 2 or len(lines[-1]) < 2:
                self.append_output_signal.emit("Error: Invalid .ffl file format.\n", "error")
                self.show_message_box_signal.emit("Error", "Invalid .ffl file format.", "critical")
                return
            first_time_segment = lines[0][1]
            last_time_segment = lines[-1][1]
            omicron_cmd_lx = f'eval "$(conda shell.bash hook)" && conda activate GWeasy && omicron {first_time_segment} {last_time_segment} ./config.txt > omicron.out 2>&1'
            omicron_cmd = f"omicron {first_time_segment} {last_time_segment} ./config.txt > omicron.out 2>&1"
            
            if platform.system() == "Windows":
                # Validate WSL environment
                process = subprocess.Popen(
                    ["wsl", "--list", "--all"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    shell=False
                )
                stdout, stderr = process.communicate()
                if process.returncode != 0:
                    self.append_output_signal.emit(f"Error: WSL not available: {stderr.strip()}\n", "error")
                    self.show_message_box_signal.emit("Error", f"WSL not available: {stderr.strip()}", "critical")
                    return

                # Use user-specified WSL username if provided, else find non-root user
                username = self.wsl_username_input.text().strip() if hasattr(self, 'wsl_username_input') and self.wsl_username_input.text().strip() else None
                if not username:
                    process = subprocess.Popen(
                        ["wsl", "cat", "/etc/passwd"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        shell=False
                    )
                    stdout, stderr = process.communicate()
                    if process.returncode == 0 and stdout.strip():
                        for line in stdout.splitlines():
                            if line.startswith("nobody:") or line.startswith("root:"):
                                continue
                            if "/home/" in line:
                                username = line.split(":")[0]
                                break
                if not username:
                    self.append_output_signal.emit("Error: No valid WSL username provided or found in /etc/passwd.\n", "error")
                    self.show_message_box_signal.emit("Error", "No valid WSL username provided or found in /etc/passwd.", "critical")
                    return

                # Locate Conda and validate environment
                process = subprocess.Popen(
                    ["wsl", "--user", username, "bash", "-lic", "which conda"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    shell=False
                )
                stdout, stderr = process.communicate()
                if process.returncode != 0 or not stdout.strip():
                    self.append_output_signal.emit(f"Error: Failed to locate Conda for user {username}: {stderr.strip()}\n", "error")
                    self.show_message_box_signal.emit("Error", f"Failed to locate Conda: {stderr.strip()}", "critical")
                    return
                conda_path = stdout.strip()
                conda_base = os.path.dirname(os.path.dirname(conda_path))
                conda_init = f"{conda_base}/etc/profile.d/conda.sh"

                # Prepare and run Omicron command
                omicron_cmd = f"omicron {first_time_segment} {last_time_segment} ./config.txt > omicron.out 2>&1"
                wsl_command = f'wsl --user {username} bash -lic "source {conda_init} && conda activate base && {omicron_cmd}"'
                self.append_output_signal.emit(f"Running: {wsl_command}\n", "info")

                # Run Omicron command once, no retries
                try:
                    process = subprocess.Popen(
                        wsl_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                    )
                    while True:
                        output = process.stdout.readline()
                        if output == "" and process.poll() is not None:
                            break
                        if output:
                            self.append_output_signal.emit(output.strip() + "\n", "info")
                    while True:
                        error = process.stderr.readline()
                        if error == "" and process.poll() is not None:
                            break
                        if error:
                            self.append_output_signal.emit(f"ERROR: {error.strip()}\n", "error")
                    process.wait()
                    if process.returncode == 0:
                        self.append_output_signal.emit("OMICRON process completed successfully.\n", "success")
                        self.show_message_box_signal.emit("Success", "OMICRON process completed successfully.", "information")
                    else:
                        self.append_output_signal.emit(f"Error: Command failed with return code {process.returncode}.\n", "error")
                        self.show_message_box_signal.emit("Error", f"OMICRON process failed with return code {process.returncode}.", "critical")
                except Exception as e:
                    self.append_output_signal.emit(f"Error: Command failed: {e}\n", "error")
                    self.show_message_box_signal.emit("Error", f"OMICRON process failed: {e}", "critical")
            
            
            
            ###LINUX
            else:
                print("Running in Linux")
                self.append_output_signal.emit(f"Running: {omicron_cmd_lx}\n", "info")
                process = subprocess.Popen(
                    omicron_cmd_lx, shell=True,executable="/bin/bash", stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
                )
                while True:
                    output = process.stdout.readline()
                    if output == "" and process.poll() is not None:
                        break
                    if output:
                        self.append_output_signal.emit(output.strip() + "\n", "info")

                while True:
                    error = process.stderr.readline()
                    if error == "" and process.poll() is not None:
                        break
                    if error:
                        self.append_output_signal.emit(f"ERROR: {error.strip()}\n", "error")

                process.wait()
                if process.returncode != 0:
                    self.append_output_signal.emit(f"Error: Command failed with return code {process.returncode}.\n", "error")
                    self.show_message_box_signal.emit("Error", f"Command failed with return code {process.returncode}.", "critical")
                else:
                    self.append_output_signal.emit("OMICRON process completed successfully.\n", "success")
                    self.show_message_box_signal.emit("Success", "OMICRON process completed successfully.", "information")
                            
        except Exception as e:
            self.append_output_signal.emit(f"Unexpected error: {e}\n", "error")
            self.show_message_box_signal.emit("Error", f"Unexpected error: {e}", "critical")

    def load_config(self):
        self.config_data = {}
        try:
            if not os.path.exists(self.config_path):
                with open(self.config_path, 'w') as file:
                    file.write(DEFAULT_CONFIG)  # Use updated DEFAULT_CONFIG with specified parameters
                self.append_output_signal.emit(f"Created default config: {self.config_path}\n", "info")
            with open(self.config_path, 'r') as file:
                for line in file:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) < 2:
                        self.append_output_signal.emit(f"Skipping invalid config line: {line}\n", "warning")
                        continue
                    key = parts[0]
                    value = parts[1]
                    if key.startswith("PARAMETER") and len(parts) == 3:
                        key = f"{parts[0]} {parts[1]}"
                        value = parts[2]
                    self.config_data[key] = value
            if "OUTPUT DIRECTORY" not in self.config_data or not self.config_data["OUTPUT DIRECTORY"]:
                self.config_data["OUTPUT DIRECTORY"] = self.default_output_dir
            self.append_output_signal.emit("Config loaded successfully.\n", "success")
            self.update_ui_from_config()
        except Exception as e:
            self.append_output_signal.emit(f"Error loading config: {e}\n", "error")
            self.show_message_box_signal.emit("Error", f"Error loading config: {e}", "critical")

    def open_custom_segs_dialog(self):
        channel_dir = QFileDialog.getExistingDirectory(self, "Select Channel Directory")
        if not channel_dir:
            self.append_output_signal.emit("No channel directory selected.\n", "warning")
            return

        segments = [d for d in os.listdir(channel_dir) if os.path.isdir(os.path.join(channel_dir, d))]
        if not segments:
            self.append_output_signal.emit("No time segments found in selected channel.\n", "error")
            self.show_message_box_signal.emit("Error", "No time segments found in selected channel.", "critical")
            return

        # Create a QDialog instead of QMainWindow
        self.custom_seg_dialog = QDialog(self)
        dialog = self.custom_seg_dialog
        dialog.setWindowTitle("Select Time Segments")
        dialog.resize(400, 400)
        dialog.setStyleSheet("""
            QDialog {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                                            stop:0 #E8ECEF, stop:1 #1C2526);
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Select Time Segments:", font=FONT_LABEL,
                                styleSheet=f"color: {COLOR_FG}; background-color: transparent;"))

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_widget = QWidget()  # Use a plain QWidget instead of GradientWidget for simplicity
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_area.setWidget(scroll_widget)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #CED4DA;
                border-radius: 5px;
            }
        """)
        layout.addWidget(scroll_area)

        self.segment_checkboxes = {}
        for segment in segments:
            try:
                start, end = map(int, segment.split("_"))
                if start >= end:
                    self.append_output_signal.emit(
                        f"Skipping invalid segment: {segment} (start >= end)\n", "warning")
                    continue
                chk = QCheckBox(segment)
                chk.setFont(FONT_LABEL)
                chk.setStyleSheet(f"color: {COLOR_FG}; background-color: transparent;")
                scroll_layout.addWidget(chk)
                self.segment_checkboxes[segment] = chk
            except ValueError:
                self.append_output_signal.emit(f"Skipping invalid segment format: {segment}\n", "warning")
                continue

        if not self.segment_checkboxes:
            self.append_output_signal.emit("No valid time segments found.\n", "error")
            self.show_message_box_signal.emit("Error", "No valid time segments found.", "critical")
            dialog.reject()  # Close the dialog
            return

        button_layout = QHBoxLayout()
        confirm_btn = QPushButton("Confirm")
        confirm_btn.setFont(FONT_BUTTON)
        confirm_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: {COLOR_FG};
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 6px;
                min-height: 28px;
            }}
            QPushButton:hover {{
                background-color: {COLOR_HOVER};
            }}
            QPushButton:pressed {{
                background-color: #A9A9A9;
            }}
        """)
        confirm_btn.clicked.connect(
            lambda: self.generate_fin_ffl(channel_dir,
                                        [seg for seg, chk in self.segment_checkboxes.items() if chk.isChecked()],
                                        dialog))
        toggle_btn = QPushButton("Toggle All")
        toggle_btn.setFont(FONT_BUTTON)
        toggle_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {COLOR_ACCENT};
                color: {COLOR_FG};
                border: 1px solid {COLOR_FG};
                border-radius: 5px;
                padding: 6px;
                min-height: 28px;
            }}
            QPushButton:hover {{
                background-color: {COLOR_HOVER};
            }}
            QPushButton:pressed {{
                background-color: #A9A9A9;
            }}
        """)
        toggle_btn.clicked.connect(
            lambda: [chk.setChecked(not all(c.isChecked() for c in self.segment_checkboxes.values()))
                    for chk in self.segment_checkboxes.values()])
        button_layout.addWidget(confirm_btn)
        button_layout.addWidget(toggle_btn)
        layout.addLayout(button_layout)

        dialog.exec_()  # Make the dialog modal

    def generate_fin_ffl(self, channel_dir, selected_segments, dialog):
        if not selected_segments:
            self.append_output_signal.emit("No segments selected.\n", "warning")
            self.show_message_box_signal.emit("Error", "No segments selected.", "critical")
            dialog.close()
            return
        fin_ffl_path = os.path.join(channel_dir, "fin.ffl")
        try:
            with open(fin_ffl_path, "w") as ffl_file:
                for segment in selected_segments:
                    segment_path = os.path.join(channel_dir, segment)
                    gwf_files = [file for file in os.listdir(segment_path) if file.endswith(".gwf")]
                    if not gwf_files:
                        self.append_output_signal.emit(f"No .gwf files found in segment: {segment}\n", "warning")
                        continue
                    gwf_file_path = os.path.join(segment_path, gwf_files[0])
                    gwf_file_path = os.path.relpath(gwf_file_path, start=".").replace("\\", "/")
                    try:
                        segment_parts = segment.split("_")
                        start_time = int(segment_parts[0])
                        end_time = int(segment_parts[1])
                        duration = end_time - start_time
                        if duration <= 0:
                            self.append_output_signal.emit(f"Invalid duration for segment: {segment}\n", "warning")
                            continue
                        ffl_file.write(f"./{gwf_file_path} {start_time} {duration} 0 0\n")
                    except (ValueError, IndexError) as e:
                        self.append_output_signal.emit(f"Error processing segment {segment}: {e}\n", "warning")
                        continue
            if os.path.getsize(fin_ffl_path) == 0:
                self.append_output_signal.emit(f"Error: Generated fin.ffl is empty.\n", "error")
                self.show_message_box_signal.emit("Error", "Generated fin.ffl is empty.", "critical")
                dialog.close()
                return
            relative_ffl_path = os.path.relpath(fin_ffl_path, os.getcwd()).replace("\\", "/")
            self.ui_elements["DATA FFL"].setText(relative_ffl_path)
            # Extract channel from the parent directory of fin.ffl
            channel_name = os.path.basename(channel_dir)
            if channel_name.startswith("H1_") or channel_name.startswith("L1_"):
                channel_name = channel_name.replace("_", ":", 1)
            if channel_name not in [self.channel_combo.itemText(i) for i in range(self.channel_combo.count())]:
                self.channel_combo.addItem(channel_name)
            self.channel_combo.setCurrentText(channel_name)
            self.append_output_signal.emit(f"fin.ffl created and selected: {relative_ffl_path}, channel set to {channel_name}\n", "success")
            self.show_message_box_signal.emit("Success", f"fin.ffl created and selected: {relative_ffl_path}", "information")
            dialog.close()
        except Exception as e:
            self.append_output_signal.emit(f"Error creating fin.ffl: {e}\n", "error")
            self.show_message_box_signal.emit("Error", f"An error occurred while creating fin.ffl: {e}", "critical")
            dialog.close()

    def update_channel_options(self):
        base_path = self.GWFOUT_DIRECTORY
        history_file = "gravfetch_history.json"
        channels = set()
        default_structure = {"gwfout_path": str(base_path), "channels": []}
        
        # Create history file if it doesn't exist
        if not os.path.exists(history_file):
            try:
                with open(history_file, "w") as file:
                    json.dump(default_structure, file, indent=4)
                self.append_output_signal.emit(f"Created history file: {history_file}\n", "info")
            except Exception as e:
                self.append_output_signal.emit(f"Error creating history file: {e}\n", "error")
                self.show_message_box_signal.emit("Error", f"Error creating history file: {e}", "critical")
        
        # Load channels from history file
        try:
            with open(history_file, "r") as file:
                history_data = json.load(file)
            if not isinstance(history_data, dict) or "channels" not in history_data:
                self.append_output_signal.emit("History file is malformed. Resetting.\n", "warning")
                with open(history_file, "w") as file:
                    json.dump(default_structure, file, indent=4)
                history_data = default_structure
            for channel in history_data.get("channels", []):
                # Convert H1_ or L1_ to H1: or L1:
                if channel.startswith("H1_") or channel.startswith("L1_"):
                    channel = channel.replace("_", ":", 1)
                channels.add(channel)
        except Exception as e:
            self.append_output_signal.emit(f"Error reading history file: {e}\n", "error")
            self.show_message_box_signal.emit("Error", f"Error reading history file: {e}", "critical")
        
        # Load channels from GWFOUT_DIRECTORY
        if os.path.exists(base_path) and os.path.isdir(base_path):
            for d in os.listdir(base_path):
                dir_path = os.path.join(base_path, d)
                if os.path.isdir(dir_path):
                    # Convert H1_ or L1_ to H1: or L1:
                    if d.startswith("H1_") or d.startswith("L1_"):
                        d = d.replace("_", ":", 1)
                    channels.add(d)
        
        channel_options = sorted(channels) if channels else ["No Channels Available"]
        current_text = self.channel_combo.currentText()
        # Check if a fin.ffl file is selected and extract its channel
        ffl_path = self.ui_elements["DATA FFL"].text().strip()
        ffl_channel = None
        if ffl_path and os.path.exists(ffl_path):
            try:
                channel_dir = os.path.dirname(ffl_path)
                ffl_channel = os.path.basename(channel_dir)
                if ffl_channel.startswith("H1_") or ffl_channel.startswith("L1_"):
                    ffl_channel = ffl_channel.replace("_", ":", 1)
                if ffl_channel and ffl_channel not in channel_options:
                    channel_options.append(ffl_channel)
                    channel_options = sorted(channel_options)
            except Exception as e:
                self.append_output_signal.emit(f"Error extracting channel from fin.ffl: {e}\n", "warning")
        
        self.channel_combo.clear()
        self.channel_combo.addItems(channel_options)
        # Set current text: prioritize fin.ffl channel, then current text (if valid), then first option
        if ffl_channel:
            self.channel_combo.setCurrentText(ffl_channel)
            #self.append_output_signal.emit(f"Set channel to {ffl_channel} from fin.ffl\n", "info")
        elif current_text and current_text != "No Channels Available" and current_text in channel_options:
            self.channel_combo.setCurrentText(current_text)
        elif channel_options != ["No Channels Available"]:
            self.channel_combo.setCurrentText(channel_options[0])
        QTimer.singleShot(4000, self.update_channel_options)

    def _show_message_box(self, title, message, icon_type):
        if icon_type == "critical":
            #QMessageBox.information(self, title, message)
            print(message)
        else:
            ##QMessageBox.information(self, title, message)
            print(message)

    def save_config(self):
        try:
            with open(self.config_path, 'w', encoding='utf-8') as file:
                for key, element in self.ui_elements.items():
                    if isinstance(element, tuple):
                        value = f"{element[0].text()} {element[1].text()}"
                    elif isinstance(element, dict):
                        value = " ".join([prod for prod, chk in element.items() if chk.isChecked()])
                    elif isinstance(element, QSlider):
                        value = str(element.value())
                    elif isinstance(element, QComboBox):
                        value = element.currentText()
                        if key == "DATA CHANNELS" and value != "No Channels Available":
                            # Convert H1_ or L1_ to H1: or L1:
                            if value.startswith("H1_") or value.startswith("L1_"):
                                value = value.replace("_", ":", 1)
                    elif isinstance(element, QLineEdit):
                        value = element.text()
                    else:
                        continue
                    if key == "OUTPUT DIRECTORY" and value:
                        # Create output directory if it doesn't exist
                        abs_path = os.path.abspath(value)
                        os.makedirs(abs_path, exist_ok=True)
                    if key in ["DATA FFL", "OUTPUT DIRECTORY"]:
                        if value:
                            value = value.replace("\\", "/")
                            abs_path = os.path.abspath(value).replace("\\", "/")
                            rel_path = os.path.relpath(abs_path, self.project_dir).replace("\\", "/")
                            if not rel_path.startswith(".") and not rel_path.startswith(".."):
                                rel_path = f"./{rel_path}"
                            value = rel_path
                    if key == "OUTPUT DIRECTORY" and not value:
                        value = self.default_output_dir
                    if key.startswith("DATA "):
                        formatted_line = f"{key}\t{value}\n"
                    elif key.startswith("PARAMETER "):
                        formatted_line = f"PARAMETER\t{key.split()[1]}\t{value}\n"
                    elif key.startswith("OUTPUT "):
                        formatted_line = f"OUTPUT\t{key.split()[1]}\t{value}\n"
                    else:
                        formatted_line = f"{key}\t{value}\n"
                    file.write(formatted_line)
            self.append_output_signal.emit(f"Configuration saved at '{self.config_path}'", "success")
            self.show_message_box_signal.emit("Success", f"Configuration has been saved successfully at '{self.config_path}'", "information")
        except Exception as e:
            self.append_output_signal.emit(f"Error saving config: {e}", "error")
            self.show_message_box_signal.emit("Error", f"An error occurred while saving the config: {str(e)}", "critical")

########################################################################################################################################
##########################################################################################################################################
########################################################################################################################################

def run_cli_interactive():
    # Display stylized GWEASY header
    header = """
---------------------------------------
******    **       **  *******     **      ******   **     **  
**        **       **  **         ****    **    **  **     **  
**        **       **  **        **  **   **         **   **    
**  ****  **   *   **  *****    **    **   ******     ** **    
**  ****  **  * *  **  **       ********        **     ***   
**    **  ** *   * **  **       **    **  **    **     ***    
********  ***     ***  *******  **    **   ******      ***  
---------------------------------------
                 v2.0
---------------------------------------
    """
    print(f"{COLORS['blue']}{header}{COLORS['reset']}")
    logging.info("Starting GWeasy CLI mode")

    # Prompt for tab selection
    while True:
        print(f"{COLORS['blue']}Select a tab to run (gravfetch, omicron, omiviz):{COLORS['reset']}")
        tab = input().strip().lower()
        if tab in ["gravfetch", "omicron", "omiviz"]:
            break
        print(f"{COLORS['red']}Invalid tab. Please choose 'gravfetch', 'omicron', or 'omiviz'.{COLORS['reset']}")

    if tab == "omiviz":
        print(f"{COLORS['yellow']}Omiviz is not implemented yet. Exiting.{COLORS['reset']}")
        logging.warning("Omiviz tab selected but not implemented")
        return

    if tab == "gravfetch":
        # Prompt for gravfetch parameters
        print(f"{COLORS['blue']}Enter path to time CSV file (e.g., times.csv):{COLORS['reset']}")
        while True:
            time_csv = input().strip()
            if os.path.exists(time_csv) and time_csv.endswith(".csv"):
                try:
                    pd.read_csv(time_csv)
                    break
                except Exception as e:
                    print(f"{COLORS['red']}Error reading CSV: {e}. Please try again.{COLORS['reset']}")
            else:
                print(f"{COLORS['red']}Invalid or non-existent CSV file. Please try again.{COLORS['reset']}")

        print(f"{COLORS['blue']}Enter path to channel CSV file (e.g., channels.csv):{COLORS['reset']}")
        while True:
            channel_csv = input().strip()
            if os.path.exists(channel_csv) and channel_csv.endswith(".csv"):
                try:
                    pd.read_csv(channel_csv, header=None, skiprows=1, names=["Channel", "Sample Rate"])
                    break
                except Exception as e:
                    print(f"{COLORS['red']}Error reading channel CSV: {e}. Please try again.{COLORS['reset']}")
            else:
                print(f"{COLORS['red']}Invalid or non-existent channel CSV file. Please try again.{COLORS['reset']}")

        print(f"{COLORS['blue']}Enter output directory (e.g., ./GWFout) [default: ./GWFout]:{COLORS['reset']}")
        output_dir = input().strip() or DEFAULT_GWFOUT
        if not os.path.exists(output_dir):
            print(f"{COLORS['yellow']}Output directory does not exist. Creating: {output_dir}{COLORS['reset']}")
            os.makedirs(output_dir, exist_ok=True)

        # Load time segments and channels
        try:
            time_ranges = pd.read_csv(time_csv)
            if not all(col in time_ranges.columns for col in ['GPSstart', 'GPSend']):
                time_ranges = pd.read_csv(time_csv, header=None, names=['GPSstart', 'GPSend'])
            segments = [f"{int(float(row['GPSstart']))}_{int(float(row['GPSend']))}" for _, row in time_ranges.iterrows()]
            channels_df = pd.read_csv(channel_csv, header=None, skiprows=1, names=["Channel", "Sample Rate"])
            channels = list(channels_df["Channel"])
        except Exception as e:
            print(f"{COLORS['red']}Error processing inputs: {e}{COLORS['reset']}")
            logging.error(f"Error processing inputs: {e}")
            return

        # Process each channel sequentially
        for channel in channels:
            print(f"{COLORS['blue']}Running Gravfetch for channel: {channel}{COLORS['reset']}")
            print(f"  Time CSV: {time_csv}")
            print(f"  Output Directory: {output_dir}")
            print(f"  Segments: {', '.join(segments)}")
            args = argparse.Namespace(tab="gravfetch", time_csv=time_csv, channel=channel, output_dir=output_dir, segments=",".join(segments), ffl_file=None)
            run_cli(args)

    elif tab == "omicron":
        print(f"{COLORS['blue']}Enter path to .ffl file (e.g., fin.ffl):{COLORS['reset']}")
        while True:
            ffl_file = input().strip()
            if os.path.exists(ffl_file) and ffl_file.endswith(".ffl"):
                try:
                    with open(ffl_file, "r") as f:
                        lines = [line.strip().split() for line in f if line.strip()]
                    if lines and len(lines[0]) >= 2 and len(lines[-1]) >= 2:
                        break
                    else:
                        print(f"{COLORS['red']}Invalid .ffl file format. Please try again.{COLORS['reset']}")
                except Exception as e:
                    print(f"{COLORS['red']}Error reading .ffl file: {e}. Please try again.{COLORS['reset']}")
            else:
                print(f"{COLORS['red']}Invalid or non-existent .ffl file. Please try again.{COLORS['reset']}")

        print(f"{COLORS['blue']}Running Omicron with the following parameters:{COLORS['reset']}")
        print(f"  FFL File: {ffl_file}")
        args = argparse.Namespace(tab="omicron", time_csv=None, channel=None, output_dir=None, segments=None, ffl_file=ffl_file)
        run_cli(args)

def run_cli(args):
    if args.tab == "gravfetch":
        logging.info(f"Running Gravfetch in CLI mode for channel: {args.channel}")
        if not all([args.time_csv, args.channel, args.output_dir, args.segments]):
            logging.error("Missing required arguments: time_csv, channel, output_dir, segments")
            print(f"{COLORS['red']}Missing required arguments: time_csv, channel, output_dir, segments{COLORS['reset']}")
            return
        try:
            time_ranges = pd.read_csv(args.time_csv)
            if not all(col in time_ranges.columns for col in ['GPSstart', 'GPSend']):
                time_ranges = pd.read_csv(args.time_csv, header=None, names=['GPSstart', 'GPSend'])
            segments = [f"{int(float(row['GPSstart']))}_{int(float(row['GPSend']))}" for _, row in time_ranges.iterrows()]
            os.makedirs(args.output_dir, exist_ok=True)
            ch_dir = os.path.join(args.output_dir, args.channel.replace(":", "_"))
            os.makedirs(ch_dir, exist_ok=True)
            fin_path = os.path.join(ch_dir, "fin.ffl")
            with open(fin_path, 'a') as fin:
                for seg in segments:
                    start, end = map(int, seg.split("_"))
                    tdir = os.path.join(ch_dir, f"{start}_{end}")
                    os.makedirs(tdir, exist_ok=True)
                    outfile = os.path.join(tdir, f"{args.channel.replace(':','_')}_{start}_{end}.gwf")
                    if os.path.exists(outfile):
                        logging.info(f"Segment {seg} already fetched for {args.channel}. Skipping.")
                        print(f"{COLORS['yellow']}Segment {seg} already fetched for {args.channel}. Skipping.{COLORS['reset']}")
                        continue
                    try:
                        logging.info(f"Fetching {args.channel} from {start} to {end}...")
                        print(f"{COLORS['blue']}Fetching {args.channel} from {start} to {end}...{COLORS['reset']}")
                        urls = get_urls(args.channel, start, end, host="gwosc-nds.ligo.org")
                        if not urls:
                            logging.warning(f"No data available for {args.channel} {start}-{end}. Skipping.")
                            print(f"{COLORS['yellow']}No data available for {args.channel} {start}-{end}. Skipping.{COLORS['reset']}")
                            continue
                        data = TimeSeries.read(urls, channel=args.channel, start=start, end=end)
                        data.write(outfile)
                        rel_path = os.path.relpath(outfile, os.getcwd()).replace("\\", "/")
                        dt = end - start
                        fin.write(f"./{rel_path} {start} {dt} 0 0\n")
                        logging.info(f"Saved to {outfile}")
                        print(f"{COLORS['green']}Saved to {outfile}{COLORS['reset']}")
                    except Exception as e:
                        logging.error(f"Error fetching {args.channel} {start}-{end}: {e}")
                        print(f"{COLORS['red']}Error fetching {args.channel} {start}-{end}: {e}{COLORS['reset']}")
        except Exception as e:
            logging.error(f"Error: {e}")
            print(f"{COLORS['red']}Error: {e}{COLORS['reset']}")
    elif args.tab == "omicron":
        logging.info("Running Omicron in CLI mode")
        if not args.ffl_file:
            logging.error("Missing required argument: ffl_file")
            print(f"{COLORS['red']}Missing required argument: ffl_file{COLORS['reset']}")
            return
        try:
            with open(args.ffl_file, "r") as f:
                lines = [line.strip().split() for line in f if line.strip()]
            if not lines or len(lines[0]) < 2 or len(lines[-1]) < 2:
                logging.error("Invalid .ffl file format.")
                print(f"{COLORS['red']}Invalid .ffl file format.{COLORS['reset']}")
                return
            first_time_segment = lines[0][1]
            last_time_segment = lines[-1][1]
            omicron_cmd = f"omicron {first_time_segment} {last_time_segment} ./config.txt > omicron.out 2>&1"
            logging.info(f"Running: {omicron_cmd}")
            print(f"{COLORS['blue']}Running: {omicron_cmd}{COLORS['reset']}")
            process = subprocess.Popen(omicron_cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            while True:
                output = process.stdout.readline()
                if output == "" and process.poll() is not None:
                    break
                if output:
                    logging.info(output.strip())
                    print(f"{COLORS['blue']}{output.strip()}{COLORS['reset']}")
            while True:
                error = process.stderr.readline()
                if error == "" and process.poll() is not None:
                    break
                if error:
                    logging.error(f"ERROR: {error.strip()}")
                    print(f"{COLORS['red']}ERROR: {error.strip()}{COLORS['reset']}")
            process.wait()
            if process.returncode != 0:
                logging.error(f"Command failed with return code {process.returncode}.")
                print(f"{COLORS['red']}Command failed with return code {process.returncode}.{COLORS['reset']}")
            else:
                logging.info("OMICRON process completed successfully.")
                print(f"{COLORS['green']}OMICRON process completed successfully.{COLORS['reset']}")
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            print(f"{COLORS['red']}Unexpected error: {e}{COLORS['reset']}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GWeasy CLI")
    parser.add_argument("--cli", action="store_true", help="Run in CLI mode")
    parser.add_argument("--tab", choices=["gravfetch", "omicron", "omiviz"], help="Specify tab to run")
    parser.add_argument("--time_csv", help="Path to time CSV file")
    parser.add_argument("--channel", help="Channel to fetch")
    parser.add_argument("--output_dir", help="Output directory")
    parser.add_argument("--segments", help="Comma-separated list of segments (e.g., start1_end1,start2_end2)")
    parser.add_argument("--ffl_file", help="Path to .ffl file for Omicron")
    args = parser.parse_args()

    if args.cli:
        if args.tab:
            run_cli(args)
        else:
            run_cli_interactive()
    else:
        app = QApplication(sys.argv)
        splash = SplashScreen(lambda: MainWindow().show())
        splash.show()
        sys.exit(app.exec_())
















"""

******    **       **  *******     **      ******   **     **  
**        **       **  **         ****    **    **  **     **  
**        **       **  **        **  **   **         **   **    
**  ****  **   *   **  *****    **    **   ******     ** **    
**  ****  **  * *  **  **       ********        **     ***   
**    **  ** *   * **  **       **    **  **    **     ***    
********  ***     ***  *******  **    **   ******      ***  

"""
