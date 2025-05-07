#!/usr/bin/env python3

import sys
import platform
import subprocess
import os
import datetime
import argparse
import asyncio
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QLabel, QLineEdit, QPushButton, QTextEdit,
    QVBoxLayout, QHBoxLayout, QWidget, QComboBox, QFileDialog, QMessageBox,
    QMenuBar, QAction, QDialog, QListWidget, QTextEdit, QLabel, QPushButton,
    QVBoxLayout, QScrollArea, QScrollBar
)
from PyQt5.QtCore import Qt, QTimer
from jinja2 import Template
from yaml_config import YamlConfig
from common_util import task_csv_to_json, extract_markdown_text, open_link
from llm_service import get_llm_service_instance, read_llm_config
from common_util import logger
import dotenv
dotenv.load_dotenv()

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MINS = 25
TIME_FORMAT = "%H:%M:%S"
DATE_FORMAT = "%Y%m%d"
FULL_TIME_FORMAT = "%Y%m%d_%H%M%S"


def get_resource_path(relative_path):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(CURRENT_DIR)
    logger.info(f"load resource from {base_path}")
    return os.path.join(base_path, relative_path)


class StickyNote(QMainWindow):
    def __init__(self, config_file, prompt_config_file, template_name):
        super().__init__()
        self._config = YamlConfig(config_file)
        self._title = self._config.get_config_item_2("config", "title")
        self._frame_size = self._config.get_config_item_2("config", "frame_size")
        self._started = False
        self._tomato_min = int(self._config.get_config_item_2("config", "default_tomato_min"))
        self._deadline = datetime.datetime.strptime(self._config.get_config_item_2("config", "deadline"), "%Y-%m-%d")
        self._short_break_min = int(self._config.get_config_item_2("config", "short_break_min"))
        self._tomato_count = 0
        self._left_seconds = 0

        self._llm_config = read_llm_config(self._config)

        if self._llm_config.api_key:
            self._llm_service = get_llm_service_instance(self._llm_config, prompt_config_file)
        self._folder = self._config.get_config_item_2("config", "folder")
        if not self._folder:
            self._folder = os.getcwd()

        today = datetime.date.today()
        self.datestr = today.strftime(DATE_FORMAT)

        self.default_filename = f"diary_{self.datestr}.md"
        self.auto_save_interval = int(self._config.get_config_item_2("config", "save_interval_ms"))
        self.last_modified_time = None
        self.commands = self._config.get_config_item_2("config", "commands")
        self.command_dict = {}
        self._templates = self._config.get_config_item("templates")
        self._template_name = template_name
        self._note_text = self._templates.get(self._template_name, "")
        logger.info(f"template={self._template_name}, prompt_config_file={prompt_config_file}")

        self.initUI()

    def initUI(self):
        self.setWindowTitle(self._title)
        size = self._frame_size.split('x')
        width, height = int(size[0]), int(size[1].split('+')[0])
        self.setGeometry(100, 100, width, height)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout()

        # Template selection
        self.template_frame = QWidget()
        template_layout = QHBoxLayout()
        self.template_label = QLabel("Template: ")
        template_layout.addWidget(self.template_label)

        self.template_choices = list(self._templates.keys())
        self.template_box = QComboBox()
        self.template_box.addItems(self.template_choices)
        self.template_box.currentIndexChanged.connect(self.on_template_select)
        template_layout.addWidget(self.template_box)

        self.arrange_time_boxes(template_layout)
        self.template_frame.setLayout(template_layout)
        self.layout.addWidget(self.template_frame)

        # File name input
        self.file_name_frame = QWidget()
        file_name_layout = QHBoxLayout()
        self.file_name_label = QLabel("File name: ")
        file_name_layout.addWidget(self.file_name_label)

        self.file_name_entry = QLineEdit()
        self.file_name_entry.setText(self.default_filename)
        file_name_layout.addWidget(self.file_name_entry)

        start_button = QPushButton("Start")
        start_button.clicked.connect(self.start)
        file_name_layout.addWidget(start_button)

        stop_button = QPushButton("Stop")
        stop_button.clicked.connect(self.pause)
        file_name_layout.addWidget(stop_button)

        reset_button = QPushButton("Reset")
        reset_button.clicked.connect(self.reset)
        file_name_layout.addWidget(reset_button)

        self.file_name_frame.setLayout(file_name_layout)
        self.layout.addWidget(self.file_name_frame)

        # Text area
        self.text_area = QTextEdit()
        self.text_area.setStyleSheet("""
            background-color: #9acd32;
            color: #333333;
            font-size: 14pt;
        """)
        self.text_area.setMarkdown(f"# {self.datestr}\n\n{self._note_text}")
        self.layout.addWidget(self.text_area)

        # Buttons
        self.arrange_buttons()
        self.layout.addWidget(self.button_frame)

        # Set layout
        self.central_widget.setLayout(self.layout)

        # Menus
        self.add_top_menu()

        # Timer for countdown
        self.timer = QTimer()
        self.timer.timeout.connect(self.countdown)

        # Async loop
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self.timer.start(1000)
        QTimer.singleShot(self.auto_save_interval, self.auto_save)

        # Load default note
        file_path = f"{self._folder}/{self.default_filename}"
        if os.path.exists(file_path):
            self.load_note()

    def arrange_time_boxes(self, layout):
        self._day_box = QLineEdit()
        self._hour_box = QLineEdit()
        self._minute_box = QLineEdit()
        self._second_box = QLineEdit()

        self._day_box.setFixedWidth(40)
        self._hour_box.setFixedWidth(40)
        self._minute_box.setFixedWidth(40)
        self._second_box.setFixedWidth(40)

        left_days = (self._deadline - datetime.datetime.now()).days
        self._day_box.setText(str(left_days))
        self.set_time(0, self._tomato_min, 0)

        layout.addWidget(self._day_box)
        layout.addWidget(QLabel("days"))
        layout.addWidget(self._hour_box)
        layout.addWidget(self._minute_box)
        layout.addWidget(self._second_box)

    def set_time(self, hour, minute, sec=0):
        self._hour_box.setText(f"{hour:02d}")
        self._minute_box.setText(f"{minute:02d}")
        self._second_box.setText(f"{sec:02d}")

    def get_hour_min_sec(self, left_seconds):
        mins, secs = divmod(left_seconds, 60)
        hours = 0
        if mins > 60:
            hours, mins = divmod(mins, 60)
        return hours, mins, secs

    def countdown(self):
        if not self._started:
            return
        try:
            self._left_seconds = int(self._hour_box.text()) * 3600 + int(self._minute_box.text()) * 60 + int(self._second_box.text())
        except ValueError:
            QMessageBox.warning(self, 'Input Error', 'Invalid time value!')
            return

        if self._left_seconds == 0:
            self._tomato_count += 1
            response = QMessageBox.question(self, 'Break Time',
                                            f"Have a break for {self._short_break_min} min at {datetime.datetime.now().strftime(TIME_FORMAT)}?",
                                            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if response == QMessageBox.Yes:
                self.set_time(0, self._short_break_min, 0)
                self._left_seconds = self._short_break_min * 60
            elif response == QMessageBox.No:
                self.set_time(0, self._tomato_min, 0)
                self._left_seconds = self._tomato_min * 60
            else:
                self._started = False
                return
        else:
            self._left_seconds -= 1

        hours, mins, secs = self.get_hour_min_sec(self._left_seconds)
        self.set_time(hours, mins, secs)

        self.timer.start(1000)

    def start(self):
        try:
            self._left_seconds = int(self._hour_box.text()) * 3600 + int(self._minute_box.text()) * 60 + int(self._second_box.text())
        except ValueError:
            QMessageBox.warning(self, 'Input Error', 'Invalid time value!')
            return
        if self._left_seconds <= 0:
            QMessageBox.warning(self, 'Time Required', 'Please set a valid time!')
            return
        self._started = True
        self.countdown()

    def pause(self):
        self._started = False
        self.timer.stop()

    def reset(self):
        self._started = False
        self.set_time(0, self._tomato_min, 0)

    def arrange_buttons(self):
        self.button_frame = QWidget()
        button_layout = QHBoxLayout()

        load_button = QPushButton("Load")
        load_button.clicked.connect(self.open_file_dialog)
        button_layout.addWidget(load_button)

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_note)
        button_layout.addWidget(save_button)

        self.command_var = self.get_default_command()
        self.command_choices = self.load_commands()
        self.command_box = QComboBox()
        self.command_box.addItems(self.command_choices)
        button_layout.addWidget(self.command_box)

        execute_button = QPushButton("Execute")
        execute_button.clicked.connect(self.execute_command)
        button_layout.addWidget(execute_button)

        aigc_button = QPushButton("Tool")
        aigc_button.clicked.connect(self.show_aigc_dialog)
        button_layout.addWidget(aigc_button)

        quit_button = QPushButton("Quit")
        quit_button.clicked.connect(self.quit_app)
        button_layout.addWidget(quit_button)

        self.button_frame.setLayout(button_layout)

    def add_top_menu(self):
        menubar = self.menuBar()

        file_menu = menubar.addMenu('File')
        file_menu.addAction('New', self.new_note)
        file_menu.addAction('Open', self.open_file_dialog)
        file_menu.addAction('Save', self.save_note)
        file_menu.addSeparator()
        file_menu.addAction('Exit', self.quit_app)

        edit_menu = menubar.addMenu('Edit')
        edit_menu.addAction('Undo', self.text_area.undo)
        edit_menu.addAction('Redo', self.text_area.redo)
        edit_menu.addAction('Cut', self.text_area.cut)
        edit_menu.addAction('Copy', self.text_area.copy)
        edit_menu.addAction('Paste', self.text_area.paste)

        link_menu = menubar.addMenu('Links')
        link_menu.addAction('About', self.show_about_dialog)
        links = self.load_links()
        for link in links:
            action = QAction(link.get("name"), self)
            action.triggered.connect(lambda checked, url=link.get("url"): open_link(url))
            link_menu.addAction(action)

        help_menu = menubar.addMenu('Help')
        help_menu.addAction('About', self.show_about_dialog)

    def show_about_dialog(self):
        QMessageBox.information(self, "About", "Sticky Note App v1.0\nCreated by Walter Fan")

    def new_note(self, title="Create New Note", new_file_name=True):
        selected_template = self.template_box.currentText()
        reply = QMessageBox.question(self, title, "Replace current note?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self._note_text = self._templates.get(selected_template, "")
            self.text_area.setPlainText(f"# {self.datestr}\n\n{self._note_text}")
            self.datestr = datetime.datetime.now().strftime(FULL_TIME_FORMAT)
            if new_file_name or (not self.file_name_entry.text().startswith(selected_template)):
                self.file_name_entry.setText(f"{selected_template}_{self.datestr}.md")

    def save_note(self, prompt=True):
        content = self.text_area.toMarkdown().strip()
        file_name = self.file_name_entry.text().strip()
        if not content:
            QMessageBox.warning(self, "Empty Note", "Cannot save an empty note!")
            return
        if not file_name:
            QMessageBox.warning(self, "Missing Info", "Please enter a file name!")
            return

        file_path = os.path.join(self._folder, file_name)
        with open(file_path, "w") as f:
            f.write(content)
        if prompt:
            QMessageBox.information(self, "Saved", f"Saved to {file_path}")
        self.last_modified_time = os.path.getmtime(file_path)

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select File", "", "All Files (*)")
        if path:
            self._folder = os.path.dirname(path)
            self.file_name_entry.setText(os.path.basename(path))
            self.load_note()

    def load_note(self):
        file_name = self.file_name_entry.text().strip()
        file_path = os.path.join(self._folder, file_name)
        if not file_name:
            QMessageBox.warning(self, "Missing File", "Enter a file name.")
            return
        try:
            with open(file_path, "r") as f:
                self.text_area.setPlainText(f.read())
        except FileNotFoundError:
            QMessageBox.warning(self, "Not Found", f"No note found: {file_name}")

    def auto_save(self):
        if self.is_already_modified():
            reply = QMessageBox.question(self, "Modified", "Overwrite?", QMessageBox.Yes | QMessageBox.No)
            if reply == QMessageBox.Yes:
                self.save_note(True)
        else:
            self.save_note(False)
        QTimer.singleShot(self.auto_save_interval, self.auto_save)

    def is_already_modified(self):
        file_name = self.file_name_entry.text().strip()
        file_path = os.path.join(self._folder, file_name)
        if os.path.exists(file_path):
            mtime = os.path.getmtime(file_path)
            if self.last_modified_time and mtime > self.last_modified_time:
                self.last_modified_time = mtime
                return True
        return False

    def execute_command(self):
        cmd_name = self.command_box.currentText()
        if cmd_name not in self.command_dict:
            QMessageBox.warning(self, "Invalid", f"Command '{cmd_name}' not found.")
            return
        cmd = self.command_dict[cmd_name]
        shell = cmd.get("shell", "")
        new_window = cmd.get("new_window", False)
        os_type = platform.system()

        try:
            if new_window:
                if os_type == "Windows":
                    subprocess.Popen(["start", "cmd", "/k", shell], shell=True)
                elif os_type == "Darwin":
                    subprocess.Popen(['osascript', '-e', f'tell application "Terminal" to do script "{shell}"'])
                else:
                    subprocess.Popen(["gnome-terminal", "--", "bash", "-c", f"{shell}; exec bash"])
            else:
                os.system(shell)
        except Exception as e:
            logger.error(f"Error: {e}")

    def load_commands(self):
        commands = self._config.get_config_item_2("config", "commands")
        result = []
        for cmd in commands:
            self.command_dict[cmd["name"]] = cmd
            result.append(cmd["name"])
        return result

    def load_links(self):
        return self._config.get_config_item_2("config", "links")

    def get_default_command(self):
        return self._config.get_config_item_2("config", "default_command")

    def on_template_select(self, index):
        self.new_note("Load Template", False)

    def show_aigc_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("AI Tool")
        dialog.resize(600, 400)

        layout = QVBoxLayout(dialog)

        self.prompt_list = QListWidget()
        layout.addWidget(self.prompt_list)

        self.hint_text = QTextEdit()
        layout.addWidget(QLabel("Prompt:"))
        layout.addWidget(self.hint_text)

        self.output_text = QTextEdit()
        layout.addWidget(QLabel("Output:"))
        layout.addWidget(self.output_text)

        btn_layout = QHBoxLayout()
        perform_btn = QPushButton("Perform")
        cancel_btn = QPushButton("Cancel")
        btn_layout.addWidget(perform_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        # Populate prompts
        prompt_templates = self._llm_service.get_prompt_templates()
        items = sorted(prompt_templates.get_prompts())
        self.prompt_list.addItems(items)

        def update_prompt():
            selected = self.prompt_list.currentItem().text()
            prompt = prompt_templates.get_prompt_tpl(selected)
            rendered = self._llm_service.build_prompt(prompt["variables"], f"{prompt['system_prompt']}\n{prompt['user_prompt']}")
            self.hint_text.setPlainText(rendered)

        self.prompt_list.itemSelectionChanged.connect(update_prompt)

        async def ask(prompt, user_input):
            res = await self._llm_service.ask(prompt["system_prompt"], user_input)
            self.output_text.setPlainText(res)

        def on_perform():
            selected = self.prompt_list.currentItem().text()
            prompt = prompt_templates.get_prompt_tpl(selected)
            user_input = self.hint_text.toPlainText()
            asyncio.run_coroutine_threadsafe(ask(prompt, user_input), self._loop)

        perform_btn.clicked.connect(on_perform)
        cancel_btn.clicked.connect(dialog.close)

        dialog.exec_()

    def quit_app(self):
        self.close()

    def _run_async_tasks(self):
        self._loop.run_until_complete(asyncio.sleep(0))
        QTimer.singleShot(100, self._run_async_tasks)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily Sticky Note Application")
    parser.add_argument('-f', '--config_file', action='store', dest='config_file', help='Path to the YAML configuration file')
    parser.add_argument('-p', '--prompt_file', action='store', dest='prompt_file', help='specify prompt template name')
    parser.add_argument('-t', '--template_name', action='store', dest='template_name', default="diary", help='specify note template name')

    args = parser.parse_args()

    config_path = args.config_file or get_resource_path("etc/sticky_note.yaml")
    prompt_file = args.prompt_file or get_resource_path("etc/prompt_template.yaml")

    logger.info(f"Loading config from: {config_path}, {prompt_file}, {args.template_name}")

    app = QApplication(sys.argv)
    window = StickyNote(config_path, prompt_file, args.template_name)
    window.show()
    sys.exit(app.exec_())