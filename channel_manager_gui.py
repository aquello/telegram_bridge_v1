import logging
import sys
import traceback
from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .db import get_channel_config_map, init_schema, upsert_channel_config, disable_channel_config
from .listener import TelegramSignalListener
from .telegram_service import TelegramDialogInfo, TelegramDialogService

logger = logging.getLogger(__name__)


@dataclass
class RowState:
    selected: bool = False
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

    def __init__(self, service: TelegramDialogService):
        super().__init__()
        self.service = service

    def run(self):
        try:
            dialogs = self.service.fetch_dialogs()
            self.finished_ok.emit(dialogs)
        except Exception:
            self.failed.emit(traceback.format_exc())


class TestConnectionWorker(QThread):
    finished_ok = Signal(bool)
    failed = Signal(str)

    def __init__(self, service: TelegramDialogService):
        super().__init__()
        self.service = service

    def run(self):
        try:
            ok = self.service.test_connection()
            self.finished_ok.emit(ok)
        except Exception:
            self.failed.emit(traceback.format_exc())


class ListenerWorker(QThread):
    status_text = Signal(str)
    failed = Signal(str)

    def __init__(self, api_id: int, api_hash: str, session_name: str = "tg_session_v1"):
        super().__init__()
        self.api_id = api_id
        self.api_hash = api_hash
        self.session_name = session_name

    def run(self):
        try:
            self.status_text.emit("Iniciando listener...")
            listener = TelegramSignalListener(self.api_id, self.api_hash, session_name=self.session_name)
            listener.run_forever()
        except Exception:
            self.failed.emit(traceback.format_exc())


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
        self.load_worker: Optional[LoadDialogsWorker] = None
        self.test_worker: Optional[TestConnectionWorker] = None

        init_schema()
        self._setup_ui()
        self.refresh_dialogs()

    def _setup_ui(self):
        self.setWindowTitle("Telegram Trade Bridge · Channel Manager")
        self.resize(1360, 820)

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
        subtitle = QLabel("Gestión visual de canales y configuración persistente por telegram_id")
        subtitle.setObjectName("SubtitleLabel")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)

        top_layout.addLayout(title_box)
        top_layout.addStretch()

        self.btn_test = QPushButton("Probar conexión Telegram")
        self.btn_refresh = QPushButton("Refrescar canales")
        self.btn_show_configured = QPushButton("Ver solo configurados: No")
        self.btn_save = QPushButton("Guardar configuración")
        self.btn_start = QPushButton("Iniciar listener")

        self.btn_show_configured.setCheckable(True)

        top_layout.addWidget(self.btn_test)
        top_layout.addWidget(self.btn_refresh)
        top_layout.addWidget(self.btn_show_configured)
        top_layout.addWidget(self.btn_save)
        top_layout.addWidget(self.btn_start)

        root.addWidget(top_bar)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        left_panel = QFrame()
        left_panel.setObjectName("Panel")
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(12, 12, 12, 12)
        left_layout.setSpacing(10)

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels([
            "Sel",
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
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(8, QHeaderView.ResizeToContents)

        left_layout.addWidget(self.table)
        splitter.addWidget(left_panel)

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

        section_title = QLabel("Configuración del canal")
        section_title.setObjectName("SectionTitle")
        right_layout.addWidget(section_title)

        form_wrap = QWidget()
        form = QFormLayout(form_wrap)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(10)

        self.lbl_selected_channel = QLabel("Sin selección")
        self.lbl_selected_channel.setObjectName("SelectedChannelLabel")

        self.chk_enabled = QCheckBox("Canal activo")
        self.chk_selected = QCheckBox("Seleccionar este canal")
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

        form.addRow("Canal", self.lbl_selected_channel)
        form.addRow("", self.chk_selected)
        form.addRow("", self.chk_enabled)
        form.addRow("Magic", self.input_magic)
        form.addRow("", self.chk_reverse)
        form.addRow("Magic inverso", self.input_magic_reverse)
        form.addRow("Exec. normal", self.cmb_exec_type)
        form.addRow("Exec. inversa", self.cmb_exec_type_rev)
        form.addRow("Riesgo %", self.input_risk)

        right_layout.addWidget(form_wrap)

        actions = QHBoxLayout()
        self.btn_apply_row = QPushButton("Aplicar a esta fila")
        self.btn_reload_row = QPushButton("Recargar fila")
        actions.addWidget(self.btn_apply_row)
        actions.addWidget(self.btn_reload_row)
        right_layout.addLayout(actions)

        info_box = QLabel(
            "Marca los canales que quieres usar, asigna magic y guarda. "
            "La configuración se persiste en channel_config para que al reiniciar "
            "ya aparezcan como configurados."
        )
        info_box.setWordWrap(True)
        info_box.setObjectName("InfoBox")
        right_layout.addWidget(info_box)
        right_layout.addStretch(1)

        splitter.addWidget(right_scroll)
        splitter.setSizes([980, 420])

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

        self._build_menu()
        self._apply_styles()

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
        #TopBar, #Panel {
            background: #171a21;
            border: 1px solid #252b36;
            border-radius: 14px;
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
        QLineEdit, QComboBox {
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

    def _show_error_dialog(self, title: str, err: str):
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
        if message:
            self.status.showMessage(message)

    def test_connection(self):
        self.set_busy(True, "Probando conexión Telegram...")
        self.test_worker = TestConnectionWorker(self.service)
        self.test_worker.finished_ok.connect(self._on_test_connection_ok)
        self.test_worker.failed.connect(self._on_worker_failed)
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
        self.load_worker.start()

    def _on_worker_failed(self, err: str):
        self.set_busy(False, "Error")
        logger.error("Error en worker GUI:\n%s", err)
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
                        selected=bool(cfg.get("enabled", 0)),
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
                        selected=False,
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

    def _render_table(self):
        self.table.blockSignals(True)

        show_only = self.btn_show_configured.isChecked()
        visible_rows = []
        for idx, r in enumerate(self.rows):
            if show_only and not r.configured:
                continue
            visible_rows.append((idx, r))

        self.table.setRowCount(len(visible_rows))

        for table_row, (real_idx, row) in enumerate(visible_rows):
            chk_item = QTableWidgetItem()
            chk_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            chk_item.setCheckState(Qt.Checked if row.selected else Qt.Unchecked)
            chk_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 0, chk_item)

            name_item = QTableWidgetItem(row.channel_name)
            name_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 1, name_item)

            id_item = QTableWidgetItem(str(row.telegram_id))
            id_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 2, id_item)

            type_item = QTableWidgetItem(row.entity_type)
            type_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 3, type_item)

            magic_item = QTableWidgetItem(row.magic)
            magic_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 4, magic_item)

            rev_item = QTableWidgetItem("Sí" if row.enable_reverse else "No")
            rev_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 5, rev_item)

            magic_rev_item = QTableWidgetItem(row.magic_reverse)
            magic_rev_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 6, magic_rev_item)

            risk_item = QTableWidgetItem(row.risk_pct)
            risk_item.setData(Qt.UserRole, real_idx)
            self.table.setItem(table_row, 7, risk_item)

            conf_item = QTableWidgetItem("Sí" if row.configured else "No")
            conf_item.setData(Qt.UserRole, real_idx)
            conf_item.setForeground(QColor("#72e3c0") if row.configured else QColor("#f0c36d"))
            self.table.setItem(table_row, 8, conf_item)

        self.table.resizeRowsToContents()
        self.table.blockSignals(False)

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
        if item.column() != 0:
            return
        real_idx = item.data(Qt.UserRole)
        if real_idx is None:
            return
        self.rows[int(real_idx)].selected = item.checkState() == Qt.Checked

    def load_current_row_into_editor(self):
        if self.selected_row_index is None:
            return

        row = self.rows[self.selected_row_index]
        self.lbl_selected_channel.setText(f"{row.channel_name} · {row.telegram_id}")
        self.chk_selected.setChecked(row.selected)
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
        row.selected = self.chk_selected.isChecked()
        row.enabled = self.chk_enabled.isChecked()
        row.magic = self.input_magic.text().strip()
        row.enable_reverse = self.chk_reverse.isChecked()
        row.magic_reverse = self.input_magic_reverse.text().strip()
        row.execution_type = self.cmb_exec_type.currentText()
        row.execution_type_rev = self.cmb_exec_type_rev.currentText()
        row.risk_pct = self.input_risk.text().strip() or "2.0"
        row.configured = bool(row.magic)

        self.status.showMessage(f"Fila actualizada: {row.channel_name}")
        self._render_table()

    def _validate_row(self, row: RowState):
        if row.selected:
            if not row.magic:
                raise ValueError(f"El canal '{row.channel_name}' está seleccionado pero no tiene magic.")
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
            if row.selected:
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
            f"Configuración guardada. Activos: {saved} · Desactivados: {disabled}"
        )
        QMessageBox.information(
            self,
            "Guardar configuración",
            f"Configuración guardada correctamente.\n"
            f"Canales activos: {saved}\n"
            f"Canales desactivados: {disabled}",
        )
        self._render_table()

    def start_listener(self):
        try:
            self.save_all()
        except Exception as e:
            logger.exception("Error iniciando listener")
            self._show_error_dialog("Error", traceback.format_exc())
            return

        if self.listener_worker and self.listener_worker.isRunning():
            QMessageBox.information(self, "Listener", "El listener ya está en ejecución.")
            return

        self.listener_worker = ListenerWorker(self.api_id, self.api_hash, self.session_name)
        self.listener_worker.status_text.connect(self.status.showMessage)
        self.listener_worker.failed.connect(self._on_worker_failed)
        self.listener_worker.start()

        QMessageBox.information(
            self,
            "Listener",
            "Listener iniciado en segundo plano.\nLa ventana puede permanecer abierta.",
        )


def run_gui(api_id: int, api_hash: str, session_name: str = "tg_session_v1"):
    app = QApplication.instance() or QApplication(sys.argv)
    win = ChannelManagerWindow(api_id, api_hash, session_name)
    win.show()
    return app.exec()
