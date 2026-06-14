import sys
import traceback
from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Optional

from PySide6.QtCore import Qt, QDate, QThread, Signal
from PySide6.QtGui import QAction, QColor, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db import (
    DB_PATH_BT,
    DB_PATH_REAL,
    disable_channel_config,
    get_channel_config_map,
    init_schema,
    set_db_path,
    upsert_channel_config,
)
from .listener import TelegramSignalListener
from .telegram_service import TelegramDialogInfo, TelegramDialogService


@dataclass
class RowState:
    selected_live: bool = False
    selected_replay: bool = False
    enabled: bool = True
    channel_name: str = ""
    telegram_id: int = 0
    entity_type: str = "unknown"
    magic: str = ""
    enable_reverse: bool = False
    magic_reverse: str = ""
    execution_type: str = "MARKET"
    execution_type_rev: str = "MARKET"
    risk_pct: str = "2.0"
    configured: bool = False


class LoadDialogsWorker(QThread):
    finished_ok = Signal(list)
    failed = Signal(str)
    log_message = Signal(str)

    def __init__(self, service: TelegramDialogService):
        super().__init__()
        self.service = service

    def run(self):
        try:
            self.log_message.emit("INFO | TELEGRAM | Cargando diálogos desde Telegram...")
            dialogs = self.service.fetch_dialogs()
            self.log_message.emit(f"INFO | TELEGRAM | Diálogos cargados: {len(dialogs)}")
            self.finished_ok.emit(dialogs)
        except Exception:
            self.failed.emit(traceback.format_exc())


class TestConnectionWorker(QThread):
    finished_ok = Signal(bool)
    failed = Signal(str)
    log_message = Signal(str)

    def __init__(self, service: TelegramDialogService):
        super().__init__()
        self.service = service

    def run(self):
        try:
            self.log_message.emit("INFO | TELEGRAM | Probando conexión...")
            ok = self.service.test_connection()
            self.log_message.emit(
                "INFO | TELEGRAM | Conexión correcta" if ok else "WARN | TELEGRAM | No se pudo validar la conexión"
            )
            self.finished_ok.emit(ok)
        except Exception:
            self.failed.emit(traceback.format_exc())


class ListenerWorker(QThread):
    status_text = Signal(str)
    failed = Signal(str)
    log_message = Signal(str)

    def __init__(self, api_id: int, api_hash: str, session_name: str = "tg_session_v1", db_path: Optional[str] = None):
        super().__init__()
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.db_path = db_path

    def run(self):
        try:
            if self.db_path:
                set_db_path(self.db_path)
                init_schema()

            self.status_text.emit("Iniciando listener live...")
            self.log_message.emit(f"INFO | LIVE | Usando DB: {self.db_path or DB_PATH_REAL}")
            self.log_message.emit("INFO | LIVE | Creando TelegramSignalListener")
            listener = TelegramSignalListener(self.api_id, self.api_hash, session_name=self.session_name)
            self.log_message.emit("INFO | LIVE | Listener live arrancando")
            listener.run_forever()
        except Exception:
            self.failed.emit(traceback.format_exc())


class ReplayWorker(QThread):
    status_text = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)
    log_message = Signal(str)

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str,
        selected_channel_ids: List[int],
        date_from: datetime,
        date_to: Optional[datetime],
        db_path: str,
    ):
        super().__init__()
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.selected_channel_ids = selected_channel_ids
        self.date_from = date_from
        self.date_to = date_to
        self.db_path = db_path

    def run(self):
        try:
            set_db_path(self.db_path)
            init_schema()

            self.status_text.emit(f"Replay usando DB: {self.db_path}")
            self.log_message.emit(f"INFO | REPLAY | Usando DB: {self.db_path}")
            self.log_message.emit(f"INFO | REPLAY | Canales seleccionados: {len(self.selected_channel_ids)}")
            self.log_message.emit(f"INFO | REPLAY | Desde: {self.date_from}")
            self.log_message.emit(f"INFO | REPLAY | Hasta: {self.date_to if self.date_to else 'sin límite'}")

            listener = TelegramSignalListener(self.api_id, self.api_hash, session_name=self.session_name)

            total = listener.run_replay(
                selected_channel_ids=self.selected_channel_ids,
                date_from=self.date_from,
                date_to=self.date_to,
                progress_callback=self._emit_progress,
            )

            self.log_message.emit(f"INFO | REPLAY | Finalizado. Mensajes procesados: {total}")
            self.finished_ok.emit(f"Replay finalizado. Mensajes procesados: {total}")
        except Exception:
            self.failed.emit(traceback.format_exc())

    def _emit_progress(self, text: str):
        self.status_text.emit(text)
        self.log_message.emit(f"INFO | REPLAY | {text}")


class ChannelManagerWindow(QMainWindow):
    def __init__(self, api_id: int, api_hash: str, session_name: str = "tg_session_v1"):
        super().__init__()
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name
        self.service = TelegramDialogService(api_id, api_hash, session_name)

        self.rows: List[RowState] = []
        self.dialogs: List[TelegramDialogInfo] = []
        self.selected_row_index: Optional[int] = None
        self.listener_worker: Optional[ListenerWorker] = None
        self.replay_worker: Optional[ReplayWorker] = None
        self.load_worker: Optional[LoadDialogsWorker] = None
        self.test_worker: Optional[TestConnectionWorker] = None

        init_schema()
        self._setup_ui()
        self.refresh_dialogs()

    def _setup_ui(self):
        self.setWindowTitle("Telegram Trade Bridge · Channel Manager")
        self.resize(1500, 940)

        central = QWidget()
        self.setCentralWidget(central)

        root = QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 14, 16, 14)

        title_box = QVBoxLayout()
        title = QLabel("Telegram Bridge")
        title.setObjectName("TitleLabel")
        subtitle = QLabel("Gestión visual de canales, live, replay y seguimiento en tiempo real")
        subtitle.setObjectName("SubtitleLabel")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        top_layout.addLayout(title_box)
        top_layout.addStretch()

        self.btn_test = QPushButton("Probar conexión Telegram")
        self.btn_refresh = QPushButton("Refrescar canales")
        self.btn_show_configured = QPushButton("Ver solo configurados: No")
        self.btn_save = QPushButton("Guardar configuración live")
        self.btn_start = QPushButton("Iniciar listener live")

        self.btn_show_configured.setCheckable(True)

        top_layout.addWidget(self.btn_test)
        top_layout.addWidget(self.btn_refresh)
        top_layout.addWidget(self.btn_show_configured)
        top_layout.addWidget(self.btn_save)
        top_layout.addWidget(self.btn_start)

        root.addWidget(top_bar)

        splitter_main = QSplitter(Qt.Vertical)
        splitter_main.setChildrenCollapsible(False)
        root.addWidget(splitter_main, 1)

        splitter_top = QSplitter()
        splitter_top.setChildrenCollapsible(False)
        splitter_main.addWidget(splitter_top)

        left_panel = QFrame()
        left_panel.setObjectName("Panel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        table_tools = QFrame()
        table_tools.setObjectName("InnerPanel")
        table_tools_layout = QHBoxLayout(table_tools)
        table_tools_layout.setContentsMargins(10, 10, 10, 10)

        self.btn_replay_all_visible = QPushButton("Marcar replay visibles")
        self.btn_replay_none = QPushButton("Quitar replay")
        self.btn_replay_from_live = QPushButton("Replay = Live")
        self.lbl_table_hint = QLabel("Live se guarda en BBDD; Replay solo afecta a esta sesión.")

        table_tools_layout.addWidget(self.btn_replay_all_visible)
        table_tools_layout.addWidget(self.btn_replay_none)
        table_tools_layout.addWidget(self.btn_replay_from_live)
        table_tools_layout.addStretch()
        table_tools_layout.addWidget(self.lbl_table_hint)

        left_layout.addWidget(table_tools)

        self.table = QTableWidget(0, 10)
        self.table.setHorizontalHeaderLabels([
            "Live",
            "Replay",
            "Canal",
            "Telegram ID",
            "Tipo",
            "Magic",
            "Inverso",
            "Magic Rev",
            "Risk %",
            "Configurado",
        ])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(9, QHeaderView.ResizeToContents)

        left_layout.addWidget(self.table)
        splitter_top.addWidget(left_panel)

        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_scroll.setObjectName("RightScroll")

        right_panel = QFrame()
        right_panel.setObjectName("Panel")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(16, 16, 16, 16)
        right_layout.setSpacing(14)

        right_scroll.setWidget(right_panel)

        live_box = QFrame()
        live_box.setObjectName("InnerPanel")
        live_layout = QVBoxLayout(live_box)
        live_layout.setContentsMargins(12, 12, 12, 12)
        live_layout.setSpacing(10)

        live_title = QLabel("Configuración Live")
        live_title.setObjectName("SectionTitle")
        live_layout.addWidget(live_title)

        live_form_wrap = QWidget()
        live_form = QFormLayout(live_form_wrap)
        live_form.setContentsMargins(0, 0, 0, 0)
        live_form.setSpacing(10)

        self.lbl_selected_channel = QLabel("Sin selección")
        self.lbl_selected_channel.setObjectName("SelectedChannelLabel")

        self.chk_live = QCheckBox("Activo en modo live")
        self.chk_replay = QCheckBox("Seleccionar para replay")
        self.chk_enabled = QCheckBox("Canal activo")
        self.input_magic = QLineEdit()
        self.input_magic.setPlaceholderText("Ej: 1001")

        self.chk_reverse = QCheckBox("Activar inverso")
        self.input_magic_reverse = QLineEdit()
        self.input_magic_reverse.setPlaceholderText("Ej: 9001")

        self.cmb_exec_type = QComboBox()
        self.cmb_exec_type.addItems(["MARKET", "PENDING"])

        self.cmb_exec_type_rev = QComboBox()
        self.cmb_exec_type_rev.addItems(["MARKET", "PENDING"])

        self.input_risk = QLineEdit()
        self.input_risk.setPlaceholderText("2.0")
        self.input_risk.setText("2.0")

        live_form.addRow("Canal", self.lbl_selected_channel)
        live_form.addRow("", self.chk_live)
        live_form.addRow("", self.chk_enabled)
        live_form.addRow("Magic", self.input_magic)
        live_form.addRow("", self.chk_reverse)
        live_form.addRow("Magic inverso", self.input_magic_reverse)
        live_form.addRow("Exec. normal", self.cmb_exec_type)
        live_form.addRow("Exec. inversa", self.cmb_exec_type_rev)
        live_form.addRow("Riesgo %", self.input_risk)

        live_layout.addWidget(live_form_wrap)

        live_actions = QHBoxLayout()
        self.btn_apply_row = QPushButton("Aplicar cambios fila")
        self.btn_reload_row = QPushButton("Recargar fila")
        live_actions.addWidget(self.btn_apply_row)
        live_actions.addWidget(self.btn_reload_row)
        live_layout.addLayout(live_actions)

        right_layout.addWidget(live_box)

        replay_box = QFrame()
        replay_box.setObjectName("InnerPanel")
        replay_layout = QVBoxLayout(replay_box)
        replay_layout.setContentsMargins(12, 12, 12, 12)
        replay_layout.setSpacing(10)

        replay_title = QLabel("Replay / Backtesting")
        replay_title.setObjectName("SectionTitle")
        replay_layout.addWidget(replay_title)

        replay_form_wrap = QWidget()
        replay_form = QFormLayout(replay_form_wrap)
        replay_form.setContentsMargins(0, 0, 0, 0)
        replay_form.setSpacing(10)

        self.chk_replay_editor = QCheckBox("Seleccionar este canal para replay")
        self.chk_replay_editor.setChecked(False)

        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        self.date_from.setDate(QDate.currentDate().addDays(-7))

        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        self.date_to.setDate(QDate.currentDate())

        self.chk_date_to = QCheckBox("Usar fecha hasta")
        self.chk_date_to.setChecked(False)
        self.date_to.setEnabled(False)

        self.input_db_path = QLineEdit()
        self.input_db_path.setText(DB_PATH_BT)
        self.btn_browse_db = QPushButton("Examinar...")

        db_row = QHBoxLayout()
        db_row.addWidget(self.input_db_path, 1)
        db_row.addWidget(self.btn_browse_db)

        db_wrap = QWidget()
        db_wrap.setLayout(db_row)

        self.btn_start_replay = QPushButton("Lanzar replay")

        replay_form.addRow("", self.chk_replay_editor)
        replay_form.addRow("Desde", self.date_from)
        replay_form.addRow("", self.chk_date_to)
        replay_form.addRow("Hasta", self.date_to)
        replay_form.addRow("BBDD destino", db_wrap)

        replay_layout.addWidget(replay_form_wrap)
        replay_layout.addWidget(self.btn_start_replay)

        right_layout.addWidget(replay_box)

        info_box = QLabel(
            "Live y Replay están desacoplados.\n"
            "Guardar configuración solo afecta a channel_config.\n"
            "La selección Replay no toca la configuración persistente y sirve para depuración histórica."
        )
        info_box.setWordWrap(True)
        info_box.setObjectName("InfoBox")
        right_layout.addWidget(info_box)
        right_layout.addStretch(1)

        splitter_top.addWidget(right_scroll)
        splitter_top.setSizes([980, 460])

        log_panel = QFrame()
        log_panel.setObjectName("Panel")
        log_layout = QVBoxLayout(log_panel)
        log_layout.setContentsMargins(12, 12, 12, 12)
        log_layout.setSpacing(10)

        log_header = QHBoxLayout()
        log_title = QLabel("Actividad en tiempo real")
        log_title.setObjectName("SectionTitle")
        self.btn_clear_log = QPushButton("Limpiar log")
        log_header.addWidget(log_title)
        log_header.addStretch()
        log_header.addWidget(self.btn_clear_log)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(2000)
        self.log_output.setPlaceholderText("Aquí verás el detalle del listener, replay, parser y errores...")

        log_layout.addLayout(log_header)
        log_layout.addWidget(self.log_output, 1)

        splitter_main.addWidget(log_panel)
        splitter_main.setSizes([650, 260])

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status.showMessage("Listo")

        self.btn_refresh.clicked.connect(self.refresh_dialogs)
        self.btn_test.clicked.connect(self.test_connection)
        self.btn_save.clicked.connect(self.save_all)
        self.btn_start.clicked.connect(self.start_listener)
        self.btn_show_configured.clicked.connect(self.toggle_show_configured)
        self.btn_apply_row.clicked.connect(self.apply_editor_to_current_row)
        self.btn_reload_row.clicked.connect(self.load_current_row_into_editor)
        self.table.itemSelectionChanged.connect(self.on_table_selection_changed)
        self.table.itemChanged.connect(self.on_table_item_changed)

        self.chk_date_to.toggled.connect(self.date_to.setEnabled)
        self.btn_browse_db.clicked.connect(self.browse_db_path)
        self.btn_start_replay.clicked.connect(self.start_replay)

        self.btn_replay_all_visible.clicked.connect(self.mark_replay_all_visible)
        self.btn_replay_none.clicked.connect(self.unmark_replay_all)
        self.btn_replay_from_live.clicked.connect(self.copy_live_to_replay)

        self.btn_clear_log.clicked.connect(self.log_output.clear)

        self._build_menu()
        self._apply_styles()

        self.append_log("INFO | GUI | Aplicación iniciada")

    def _build_menu(self):
        action_exit = QAction("Salir", self)
        action_exit.triggered.connect(self.close)
        menu = self.menuBar().addMenu("Archivo")
        menu.addAction(action_exit)

    def _apply_styles(self):
        self.setStyleSheet("""
        QMainWindow {
            background: #0f1115;
        }
        #TopBar, #Panel, #InnerPanel {
            background: #171a21;
            border: 1px solid #252b36;
            border-radius: 14px;
        }
        #InnerPanel {
            background: #141820;
        }
        #TitleLabel {
            font-size: 24px;
            font-weight: 700;
            color: #f3f5f7;
        }
        #SubtitleLabel {
            color: #99a2b2;
            font-size: 13px;
        }
        #SectionTitle {
            font-size: 18px;
            font-weight: 700;
            color: #eef2f7;
        }
        #SelectedChannelLabel {
            color: #72e3c0;
            font-weight: 600;
        }
        #InfoBox {
            background: #11151c;
            border: 1px solid #252b36;
            border-radius: 12px;
            padding: 12px;
            color: #b3bcc8;
        }
        QPushButton {
            background: #232938;
            color: #f2f4f8;
            border: 1px solid #2f3747;
            border-radius: 10px;
            padding: 10px 14px;
            font-weight: 600;
        }
        QPushButton:hover {
            background: #2b3344;
        }
        QPushButton:pressed {
            background: #1f2531;
        }
        QLineEdit, QComboBox, QDateEdit, QPlainTextEdit {
            background: #0f1319;
            color: #eef2f7;
            border: 1px solid #313948;
            border-radius: 10px;
            padding: 8px 10px;
            min-height: 18px;
        }
        QTableWidget {
            background: #11151c;
            alternate-background-color: #151a23;
            color: #edf1f7;
            border: 1px solid #252b36;
            border-radius: 12px;
            gridline-color: transparent;
        }
        QHeaderView::section {
            background: #171d27;
            color: #dbe2ec;
            border: none;
            border-bottom: 1px solid #252b36;
            padding: 10px;
            font-weight: 700;
        }
        QTableWidget::item:selected {
            background: #20314b;
        }
        QLabel, QCheckBox {
            color: #dbe2ec;
        }
        QStatusBar {
            background: #131720;
            color: #c7d0db;
        }
        QScrollArea {
            border: none;
            background: transparent;
        }
        """)

    def append_log(self, text: str):
        now = datetime.now().strftime("%H:%M:%S")
        self.log_output.appendPlainText(f"{now} | {text}")
        cursor = self.log_output.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.log_output.setTextCursor(cursor)
        self.log_output.ensureCursorVisible()

    def _show_error_dialog(self, title: str, err: str):
        self.append_log("ERROR | GUI | Se produjo un error, revisa el detalle")
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle(title)
        msg.setText("Se produjo un error.")
        msg.setDetailedText(err)
        msg.setStandardButtons(QMessageBox.Ok)
        msg.exec()

    def set_busy(self, busy: bool, message: str = ""):
        self.btn_refresh.setEnabled(not busy)
        self.btn_test.setEnabled(not busy)
        self.btn_save.setEnabled(not busy)
        self.btn_start.setEnabled(not busy)
        self.btn_start_replay.setEnabled(not busy)
        self.btn_replay_all_visible.setEnabled(not busy)
        self.btn_replay_none.setEnabled(not busy)
        self.btn_replay_from_live.setEnabled(not busy)
        if message:
            self.status.showMessage(message)
            self.append_log(f"INFO | GUI | {message}")

    def test_connection(self):
        self.set_busy(True, "Probando conexión Telegram...")
        self.test_worker = TestConnectionWorker(self.service)
        self.test_worker.finished_ok.connect(self._on_test_connection_ok)
        self.test_worker.failed.connect(self._on_worker_failed)
        self.test_worker.log_message.connect(self.append_log)
        self.test_worker.start()

    def _on_test_connection_ok(self, ok: bool):
        self.set_busy(False, "Conexión correcta" if ok else "No se pudo validar la conexión")
        if ok:
            QMessageBox.information(self, "Telegram", "Conexión correcta con Telegram.")
        else:
            QMessageBox.warning(self, "Telegram", "No se pudo validar la conexión.")

    def refresh_dialogs(self):
        self.set_busy(True, "Cargando canales desde Telegram...")
        self.load_worker = LoadDialogsWorker(self.service)
        self.load_worker.finished_ok.connect(self._on_dialogs_loaded)
        self.load_worker.failed.connect(self._on_worker_failed)
        self.load_worker.log_message.connect(self.append_log)
        self.load_worker.start()

    def _on_worker_failed(self, err: str):
        self.set_busy(False, "Error")
        self.append_log("ERROR | WORKER | Fallo en worker")
        self.append_log(err.strip().splitlines()[-1] if err.strip() else "ERROR | WORKER | Error desconocido")
        self._show_error_dialog("Error", err)

    def _on_dialogs_loaded(self, dialogs: List[TelegramDialogInfo]):
        self.set_busy(False, f"Se cargaron {len(dialogs)} diálogos")
        self.dialogs = dialogs
        self._merge_dialogs_with_db()
        self._render_table()

    def _merge_dialogs_with_db(self):
        config_map = get_channel_config_map()
        rows: List[RowState] = []

        for d in self.dialogs:
            cfg = config_map.get(int(d.telegram_id))
            if cfg:
                rows.append(
                    RowState(
                        selected_live=bool(cfg.get("enabled", 0)),
                        selected_replay=False,
                        enabled=bool(cfg.get("enabled", 0)),
                        channel_name=cfg.get("channel_name") or d.title,
                        telegram_id=int(d.telegram_id),
                        entity_type=d.entity_type,
                        magic=str(cfg.get("magic") or ""),
                        enable_reverse=bool(cfg.get("enable_reverse", 0)),
                        magic_reverse=str(cfg.get("magic_reverse") or ""),
                        execution_type=cfg.get("execution_type") or "MARKET",
                        execution_type_rev=cfg.get("execution_type_rev") or "MARKET",
                        risk_pct=str(cfg.get("risk_pct") or "2.0"),
                        configured=True,
                    )
                )
            else:
                rows.append(
                    RowState(
                        selected_live=False,
                        selected_replay=False,
                        enabled=True,
                        channel_name=d.title,
                        telegram_id=int(d.telegram_id),
                        entity_type=d.entity_type,
                        magic="",
                        enable_reverse=False,
                        magic_reverse="",
                        execution_type="MARKET",
                        execution_type_rev="MARKET",
                        risk_pct="2.0",
                        configured=False,
                    )
                )

        self.rows = rows
        self.append_log(f"INFO | GUI | Filas preparadas: {len(self.rows)}")

    def _get_visible_real_indexes(self) -> List[int]:
        show_only = self.btn_show_configured.isChecked()
        visible_rows = []
        for idx, r in enumerate(self.rows):
            if show_only and not r.configured:
                continue
            visible_rows.append(idx)
        return visible_rows

    def _render_table(self):
        self.table.blockSignals(True)

        visible_indexes = self._get_visible_real_indexes()
        self.table.setRowCount(len(visible_indexes))

        for table_row, real_idx in enumerate(visible_indexes):
            row = self.rows[real_idx]

            live_item = QTableWidgetItem()
            live_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            live_item.setCheckState(Qt.Checked if row.selected_live else Qt.Unchecked)
            live_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 0, live_item)

            replay_item = QTableWidgetItem()
            replay_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            replay_item.setCheckState(Qt.Checked if row.selected_replay else Qt.Unchecked)
            replay_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 1, replay_item)

            name_item = QTableWidgetItem(row.channel_name)
            name_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 2, name_item)

            id_item = QTableWidgetItem(str(row.telegram_id))
            id_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 3, id_item)

            type_item = QTableWidgetItem(row.entity_type)
            type_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 4, type_item)

            magic_item = QTableWidgetItem(row.magic)
            magic_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 5, magic_item)

            rev_item = QTableWidgetItem("Sí" if row.enable_reverse else "No")
            rev_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 6, rev_item)

            magic_rev_item = QTableWidgetItem(row.magic_reverse)
            magic_rev_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 7, magic_rev_item)

            risk_item = QTableWidgetItem(row.risk_pct)
            risk_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 8, risk_item)

            conf_item = QTableWidgetItem("Sí" if row.configured else "No")
            conf_item.setData(Qt.UserRole, real_idx)
            conf_item.setForeground(QColor("#72e3c0") if row.configured else QColor("#f0c36d"))
            self.table.setItem(table_row, 9, conf_item)

        self.table.resizeRowsToContents()
        self.table.blockSignals(False)
        self.append_log(f"INFO | GUI | Tabla renderizada con {len(visible_indexes)} filas visibles")

    def toggle_show_configured(self):
        state = self.btn_show_configured.isChecked()
        self.btn_show_configured.setText(f"Ver solo configurados: {'Sí' if state else 'No'}")
        self._render_table()

    def on_table_selection_changed(self):
        items = self.table.selectedItems()
        if not items:
            self.selected_row_index = None
            return

        real_idx = items[0].data(Qt.UserRole)
        self.selected_row_index = int(real_idx)
        self.load_current_row_into_editor()

    def on_table_item_changed(self, item: QTableWidgetItem):
        real_idx = item.data(Qt.UserRole)
        if real_idx is None:
            return

        row = self.rows[int(real_idx)]

        if item.column() == 0:
            row.selected_live = item.checkState() == Qt.Checked
        elif item.column() == 1:
            row.selected_replay = item.checkState() == Qt.Checked

        if self.selected_row_index == int(real_idx):
            self.load_current_row_into_editor()

    def load_current_row_into_editor(self):
        if self.selected_row_index is None:
            return

        row = self.rows[self.selected_row_index]
        self.lbl_selected_channel.setText(f"{row.channel_name} · {row.telegram_id}")
        self.chk_live.setChecked(row.selected_live)
        self.chk_replay.setChecked(row.selected_replay)
        self.chk_replay_editor.setChecked(row.selected_replay)
        self.chk_enabled.setChecked(row.enabled)
        self.input_magic.setText(row.magic)
        self.chk_reverse.setChecked(row.enable_reverse)
        self.input_magic_reverse.setText(row.magic_reverse)
        self.cmb_exec_type.setCurrentText(row.execution_type or "MARKET")
        self.cmb_exec_type_rev.setCurrentText(row.execution_type_rev or "MARKET")
        self.input_risk.setText(row.risk_pct or "2.0")

    def apply_editor_to_current_row(self):
        if self.selected_row_index is None:
            QMessageBox.warning(self, "Canal", "Selecciona una fila primero.")
            return

        row = self.rows[self.selected_row_index]
        row.selected_live = self.chk_live.isChecked()
        row.selected_replay = self.chk_replay.isChecked() or self.chk_replay_editor.isChecked()
        row.enabled = self.chk_enabled.isChecked()
        row.magic = self.input_magic.text().strip()
        row.enable_reverse = self.chk_reverse.isChecked()
        row.magic_reverse = self.input_magic_reverse.text().strip()
        row.execution_type = self.cmb_exec_type.currentText()
        row.execution_type_rev = self.cmb_exec_type_rev.currentText()
        row.risk_pct = self.input_risk.text().strip() or "2.0"
        row.configured = bool(row.magic)

        self.status.showMessage(f"Fila actualizada: {row.channel_name}")
        self.append_log(f"INFO | GUI | Fila actualizada: {row.channel_name} ({row.telegram_id})")
        self._render_table()

    def _validate_row(self, row: RowState):
        if row.selected_live:
            if not row.magic:
                raise ValueError(f"El canal '{row.channel_name}' está activo en live pero no tiene magic.")
            int(row.magic)
            float(row.risk_pct)

        if row.enable_reverse:
            if not row.magic_reverse:
                raise ValueError(
                    f"El canal '{row.channel_name}' tiene inverso activado pero no magic inverso."
                )
            int(row.magic_reverse)

    def save_all(self):
        if self.selected_row_index is not None:
            self.apply_editor_to_current_row()

        saved = 0
        disabled = 0

        for row in self.rows:
            if row.selected_live:
                self._validate_row(row)
                upsert_channel_config(
                    channel_name=row.channel_name,
                    telegram_id=row.telegram_id,
                    magic=int(row.magic),
                    enabled=1 if row.enabled else 0,
                    enable_reverse=1 if row.enable_reverse else 0,
                    magic_reverse=int(row.magic_reverse) if row.magic_reverse else None,
                    execution_type=row.execution_type,
                    execution_type_rev=row.execution_type_rev if row.enable_reverse else None,
                    risk_pct=float(row.risk_pct),
                )
                row.configured = True
                saved += 1
            else:
                disable_channel_config(row.telegram_id)
                if row.configured:
                    disabled += 1

        self.status.showMessage(
            f"Configuración live guardada. Activos: {saved} · Desactivados: {disabled}"
        )
        self.append_log(f"INFO | DB | Configuración live guardada. Activos={saved} Desactivados={disabled}")
        QMessageBox.information(
            self,
            "Guardar configuración",
            f"Configuración live guardada correctamente.\n"
            f"Canales activos: {saved}\n"
            f"Canales desactivados: {disabled}",
        )
        self._render_table()

    def browse_db_path(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Seleccionar BBDD SQLite",
            self.input_db_path.text().strip() or DB_PATH_BT,
            "SQLite DB (*.db *.sqlite *.sqlite3);;Todos los archivos (*)",
        )
        if path:
            self.input_db_path.setText(path)
            self.append_log(f"INFO | REPLAY | DB seleccionada: {path}")

    def _get_replay_channel_ids(self) -> List[int]:
        return [r.telegram_id for r in self.rows if r.selected_replay]

    def mark_replay_all_visible(self):
        if self.selected_row_index is not None:
            self.apply_editor_to_current_row()

        count = 0
        for idx in self._get_visible_real_indexes():
            self.rows[idx].selected_replay = True
            count += 1

        self._render_table()
        self.status.showMessage(f"Canales marcados para replay: {count}")
        self.append_log(f"INFO | REPLAY | Canales visibles marcados para replay: {count}")

    def unmark_replay_all(self):
        if self.selected_row_index is not None:
            self.apply_editor_to_current_row()

        for row in self.rows:
            row.selected_replay = False

        self._render_table()
        if self.selected_row_index is not None:
            self.load_current_row_into_editor()
        self.status.showMessage("Selección replay limpiada")
        self.append_log("INFO | REPLAY | Selección replay limpiada")

    def copy_live_to_replay(self):
        if self.selected_row_index is not None:
            self.apply_editor_to_current_row()

        count = 0
        for row in self.rows:
            row.selected_replay = bool(row.selected_live)
            if row.selected_replay:
                count += 1

        self._render_table()
        if self.selected_row_index is not None:
            self.load_current_row_into_editor()
        self.status.showMessage(f"Replay sincronizado con Live: {count} canales")
        self.append_log(f"INFO | REPLAY | Replay sincronizado con Live: {count} canales")

    def start_listener(self):
        try:
            self.save_all()
        except Exception:
            self.append_log("ERROR | LIVE | Error validando antes de iniciar listener")
            self._show_error_dialog("Error", traceback.format_exc())
            return

        if self.listener_worker and self.listener_worker.isRunning():
            QMessageBox.information(self, "Listener", "El listener ya está en ejecución.")
            self.append_log("WARN | LIVE | El listener ya estaba en ejecución")
            return

        db_path = DB_PATH_REAL

        self.listener_worker = ListenerWorker(
            self.api_id,
            self.api_hash,
            self.session_name,
            db_path=db_path,
        )
        self.listener_worker.status_text.connect(self.status.showMessage)
        self.listener_worker.failed.connect(self._on_worker_failed)
        self.listener_worker.log_message.connect(self.append_log)

        self.append_log("INFO | LIVE | Iniciando listener live...")
        self.listener_worker.start()

        QMessageBox.information(
            self,
            "Listener",
            "Listener live iniciado en segundo plano.\nLa ventana puede permanecer abierta.",
        )

    def start_replay(self):
        if self.selected_row_index is not None:
            self.apply_editor_to_current_row()

        selected_channel_ids = self._get_replay_channel_ids()
        if not selected_channel_ids:
            QMessageBox.warning(self, "Replay", "Marca al menos un canal en la columna Replay.")
            self.append_log("WARN | REPLAY | No hay canales seleccionados para replay")
            return

        db_path = self.input_db_path.text().strip()
        if not db_path:
            QMessageBox.warning(self, "Replay", "Indica una BBDD destino para el replay.")
            self.append_log("WARN | REPLAY | No se indicó DB destino")
            return

        qdate_from = self.date_from.date()
        date_from = datetime.combine(qdate_from.toPython(), time.min)

        date_to = None
        if self.chk_date_to.isChecked():
            qdate_to = self.date_to.date()
            date_to = datetime.combine(qdate_to.toPython(), time.max)
            if date_to < date_from:
                QMessageBox.warning(self, "Replay", "La fecha hasta no puede ser anterior a la fecha desde.")
                self.append_log("WARN | REPLAY | Fecha hasta anterior a fecha desde")
                return

        if self.replay_worker and self.replay_worker.isRunning():
            QMessageBox.information(self, "Replay", "Ya hay un replay en ejecución.")
            self.append_log("WARN | REPLAY | Ya hay un replay en ejecución")
            return

        self.replay_worker = ReplayWorker(
            api_id=self.api_id,
            api_hash=self.api_hash,
            session_name=self.session_name,
            selected_channel_ids=selected_channel_ids,
            date_from=date_from,
            date_to=date_to,
            db_path=db_path,
        )
        self.replay_worker.status_text.connect(self.status.showMessage)
        self.replay_worker.failed.connect(self._on_worker_failed)
        self.replay_worker.finished_ok.connect(self._on_replay_finished)
        self.replay_worker.log_message.connect(self.append_log)

        self.set_busy(True, "Iniciando replay...")
        self.append_log("INFO | REPLAY | Lanzando replay...")
        self.replay_worker.start()

    def _on_replay_finished(self, message: str):
        self.set_busy(False, message)
        self.append_log(f"INFO | REPLAY | {message}")
        QMessageBox.information(self, "Replay", message)


def run_gui(api_id: int, api_hash: str, session_name: str = "tg_session_v1"):
    app = QApplication.instance() or QApplication(sys.argv)
    win = ChannelManagerWindow(api_id, api_hash, session_name)
    win.show()
    return app.exec()
