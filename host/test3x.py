#!/usr/bin/env python
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial # type: ignore # Ignore type checking for pyserial if stubs aren't present
import threading
import time
import json
import queue
import os
import re
from serial.tools import list_ports # type: ignore
from datetime import datetime # No need for timedelta here
# Removed ThreadPoolExecutor from BoardConnection, using direct threads for simplicity now
# from concurrent.futures import ThreadPoolExecutor # Keep if needed for connect/disconnect futures

# Constants
MAX_BOARDS = 16
LED_CHANNELS = { # Use tuple for faster iteration if needed, but dict is fine for lookups
    'UV': 0, 'FAR_RED': 1, 'RED': 2, 'WHITE': 3, 'GREEN': 4, 'BLUE': 5
}
LED_CHANNEL_NAMES = list(LED_CHANNELS.keys()) # Cache list of names
NUM_LED_CHANNELS = len(LED_CHANNEL_NAMES) # Cache count
LED_COLORS = {
    'UV': "#9400D3", 'FAR_RED': "#8B0000", 'RED': "#FF0000",
    'WHITE': "#E0E0E0", 'GREEN': "#00FF00", 'BLUE': "#0000FF"
}

# --- UI Constants ---
UI_COLORS = {
    'error': '#FF0000', 'success': '#008000', 'warning': '#FFA500',
    'info': '#0000FF', 'normal': '#000000', 'header_bg': '#E0E0E0',
    'active_bg': '#D0FFD0', 'inactive_bg': '#FFD0D0', 'button_bg': '#F0F0F0',
    'entry_bg': '#FFFFFF', 'disabled_bg': '#E0E0E0', 'schedule_frame_bg': '#F5F5F5'
}
WIDGET_SIZES = {
    'entry_width': 5, 'time_entry_width': 6, 'button_width': 15, # Adjusted time entry width for HHMM
    'small_button_width': 10, 'label_width': 12, 'schedule_label_width': 8
}
BOARD_LAYOUT = {
    'boards_per_page': 8, 'rows_per_page': 2, 'cols_per_page': 4,
    'padding': 5, 'frame_padding': 10
}
TIMINGS = {
    'status_update_batch': 150,   # ms between status updates (slightly longer batch)
    'scheduler_default': 1000,    # default scheduler check interval (ms)
    'scheduler_urgent': 100,      # urgent scheduler check interval (ms)
    'scheduler_normal': 1000,     # normal scheduler check interval (ms)
    'scheduler_relaxed': 5000,    # relaxed scheduler check interval (ms)
    'serial_timeout': 1.0,        # Serial read/write timeout
    'serial_retry_delay': 0.5,    # Base delay between serial retries
    'queue_check_interval': 100,  # ms between checking the GUI queue
    'apply_batch_delay': 0.02     # Small delay between queuing apply commands in a batch
}
CMD_MESSAGES = {
    'scan_start': "Scanning for boards...", 'scan_complete': "Board scan complete",
    'apply_start': "Applying settings...", 'apply_complete': "Settings applied",
    'scheduler_start': "Scheduler started", 'scheduler_stop': "Scheduler stopped",
    'fan_on': "Turning fans ON", 'fan_off': "Turning fans OFF",
    'lights_on': "Turning lights ON", 'lights_off': "Turning lights OFF",
    'import_start': "Importing settings...", 'import_success': "Settings imported successfully",
    'export_start': "Exporting settings...", 'export_success': "Settings exported successfully",
    'error_prefix': "Error: "
}
# --- End Constants ---


# --- File Paths ---
try:
    SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
    PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
except NameError: # Handle case where __file__ is not defined (e.g., interactive)
    SCRIPT_DIR = os.getcwd()
    PROJECT_ROOT = os.path.dirname(SCRIPT_DIR) # May need adjustment depending on structure
SERIAL_MAPPING_FILE = os.path.join(PROJECT_ROOT, "microcontroller", "microcontroller_serial.txt")
DEFAULT_DOCUMENTS_PATH = os.path.join(os.path.expanduser("~"), "Documents")
# --- End File Paths ---


# --- Cached Regex and Lookups ---
# ** MODIFIED: Updated regex to match HHMM format **
TIME_PATTERN = re.compile(r'^([0-1][0-9]|2[0-3])([0-5][0-9])$') # HHMM format
SERIAL_MAPPING_PATTERN = re.compile(r'^(\d+):(.+)$')
CHAMBER_NUM_PATTERN = re.compile(r'chamber_(\d+)')
DUTY_CYCLE_LOOKUP = {i: int((i / 100.0) * 4095) for i in range(101)}
ZERO_DUTY_CYCLES = tuple([0] * NUM_LED_CHANNELS) # Use tuple for immutable zero array
# --- End Cached Regex and Lookups ---


# --- GUI Action Classes (No changes needed) ---
class GUIAction: pass
class StatusUpdate(GUIAction):
    def __init__(self, message, is_error=False):
        self.message = message
        self.is_error = is_error
class BoardsDetected(GUIAction):
    def __init__(self, boards, error=None):
        self.boards = boards
        self.error = error
class CommandComplete(GUIAction):
    def __init__(self, board_idx, command_type, success, message, extra_info=None):
        self.board_idx = board_idx
        self.command_type = command_type
        self.success = success
        self.message = message
        self.extra_info = extra_info
class SchedulerUpdate(GUIAction):
    def __init__(self, board_idx, channel_name, active):
        self.board_idx = board_idx
        self.channel_name = channel_name
        self.active = active
class FileOperationComplete(GUIAction):
    def __init__(self, operation_type, success, message, data=None):
        self.operation_type = operation_type
        self.success = success
        self.message = message
        self.data = data
# --- End GUI Action Classes ---


class BoardConnection:
    """
    Manages serial connection and command queue for a board. Optimized for RPi.
    """
    CMD_SETALL = "SETALL"
    CMD_FAN_SET = "FAN_SET"
    RESP_OK = b"OK" # Use bytes for direct comparison
    RESP_ERR_PREFIX = b"ERR:" # Use bytes
    MAX_RETRIES = 2 # Slightly fewer retries for faster failure
    READ_TIMEOUT = TIMINGS['serial_timeout']
    WRITE_TIMEOUT = TIMINGS['serial_timeout']
    RETRY_DELAY = TIMINGS['serial_retry_delay']

    def __init__(self, port, serial_number, gui_queue, chamber_number=None):
        self.port = port
        self.serial_number = serial_number
        self.chamber_number = chamber_number
        self.gui_queue = gui_queue
        self.serial_conn = None
        self.is_connected = False
        self.last_error = ""
        self.fan_speed = 0
        self.fan_enabled = False
        self.lock = threading.RLock() # Lock for accessing shared resources (serial_conn, state)
        self.command_queue = queue.Queue()
        self.command_processor_thread = None
        self.stop_event = threading.Event()
        self._start_command_processor() # Start processor on init

    def _connect(self):
        """Synchronous connect attempt (called within locked context)."""
        if self.is_connected: return True
        # print(f"[{self.port}] Attempting connection...") # Less verbose
        try:
            if self.serial_conn: # Close previous if exists
                try: self.serial_conn.close()
                except Exception: pass
            # Reduced timeout slightly
            self.serial_conn = serial.Serial(port=self.port, baudrate=115200,
                                             timeout=self.READ_TIMEOUT,
                                             write_timeout=self.WRITE_TIMEOUT)
            time.sleep(1.8) # Slightly shorter wait after connect
            if self.serial_conn.in_waiting > 0:
                self.serial_conn.reset_input_buffer()
            self.is_connected = True
            self.last_error = ""
            # print(f"[{self.port}] Connection successful.") # Less verbose
            return True
        except serial.SerialException as e:
            self.last_error = f"Serial Error: {str(e)}"
            self.is_connected = False
            self.serial_conn = None
            print(f"[{self.port}] Connection failed: {self.last_error}")
            return False
        except Exception as e:
            self.last_error = f"Unexpected Connect Error: {str(e)}"
            self.is_connected = False
            self.serial_conn = None
            print(f"[{self.port}] Connection failed unexpectedly: {self.last_error}")
            return False

    def _disconnect(self):
        """Synchronous disconnect attempt (called within locked context)."""
        if self.serial_conn:
            # print(f"[{self.port}] Closing serial connection...") # Less verbose
            try: self.serial_conn.close()
            except Exception as e: print(f"[{self.port}] Error closing serial port: {e}")
        self.is_connected = False
        self.serial_conn = None
        # print(f"[{self.port}] Disconnected.") # Less verbose

    def _send_receive_command(self, command_str):
        """Sends command, reads response line. Handles retries and reconnect."""
        with self.lock:
            if not self.is_connected:
                if not self._connect():
                    return False, self.last_error # Return connection error

            command_bytes = (command_str + '\n').encode('utf-8')
            retries = 0
            while retries <= self.MAX_RETRIES:
                if not self.is_connected: # Check connection at start of each retry loop
                    if not self._connect():
                         return False, f"Reconnect failed: {self.last_error}"

                try:
                    # Clear input buffer before sending
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()

                    # Send command
                    self.serial_conn.write(command_bytes)
                    self.serial_conn.flush()

                    # Read response line (more efficient than byte-by-byte)
                    response_bytes = self.serial_conn.readline()

                    # Process response
                    if self.RESP_OK in response_bytes:
                        return True, "Success"
                    elif response_bytes.startswith(self.RESP_ERR_PREFIX):
                        error_msg = response_bytes[len(self.RESP_ERR_PREFIX):].strip().decode('utf-8', errors='ignore')
                        return False, f"Board Error: {error_msg}"
                    elif not response_bytes: # Timeout occurred (readline returned empty)
                         raise TimeoutError("Timeout waiting for response")
                    else: # Unexpected response
                         raise IOError(f"Unexpected response: {response_bytes}")

                except (serial.SerialTimeoutException, TimeoutError, IOError, serial.SerialException, ConnectionError) as e:
                    retries += 1
                    self.last_error = f"Comm Error (Retry {retries}/{self.MAX_RETRIES}): {e}"
                    print(f"[{self.port}] {self.last_error}")
                    self._disconnect() # Disconnect on any communication error
                    if retries > self.MAX_RETRIES:
                        return False, f"Max retries exceeded: {self.last_error}"
                    time.sleep(self.RETRY_DELAY * retries) # Exponential backoff might be better

                except Exception as e: # Catch unexpected errors
                    self.last_error = f"Unexpected Send/Receive Error: {e}"
                    print(f"[{self.port}] {self.last_error}. Disconnecting.")
                    self._disconnect()
                    return False, self.last_error

            return False, f"Send/Receive failed after {self.MAX_RETRIES} retries"


    def _execute_command(self, command_type, args, board_idx):
        """Executes the command and handles communication."""
        success = False
        message = "Command execution failed"
        try:
            if command_type == self.CMD_SETALL:
                command_str = f"{self.CMD_SETALL} {' '.join(map(str, args))}"
                success, message = self._send_receive_command(command_str)
            elif command_type == self.CMD_FAN_SET:
                command_str = f"{self.CMD_FAN_SET} {args}"
                success, message = self._send_receive_command(command_str)
                if success: # Update internal state only on success
                    with self.lock:
                        self.fan_speed = args
                        self.fan_enabled = args > 0
        except Exception as e:
             print(f"[{self.port}] Error executing command {command_type}: {e}")
             success = False; message = f"Execution Error: {e}"

        # Report result via GUI Queue
        self.gui_queue.put(CommandComplete(board_idx, command_type, success, message))


    def _start_command_processor(self):
        """Starts the background command processor thread."""
        if self.command_processor_thread and self.command_processor_thread.is_alive(): return
        self.stop_event.clear()
        self.command_processor_thread = threading.Thread(target=self._process_command_queue, daemon=True, name=f"CmdProc-{self.port}")
        self.command_processor_thread.start()
        # print(f"[{self.port}] Command processor started.") # Less verbose

    def _stop_command_processor(self):
        """Stops the background command processor thread gracefully."""
        if self.command_processor_thread and self.command_processor_thread.is_alive():
            # print(f"[{self.port}] Stopping command processor...") # Less verbose
            self.stop_event.set()
            self.command_queue.put((None, None, None)) # Sentinel
            self.command_processor_thread.join(timeout=1.5) # Shorter join timeout
            if self.command_processor_thread.is_alive(): print(f"[{self.port}] Warning: Cmd processor thread join timed out.")
            self.command_processor_thread = None
            # print(f"[{self.port}] Command processor stopped.") # Less verbose

    def _process_command_queue(self):
        """Target function for the command processor thread."""
        while not self.stop_event.is_set():
            try:
                command_type, args, board_idx = self.command_queue.get(timeout=0.2)
                if command_type is None: break
                self._execute_command(command_type, args, board_idx)
                self.command_queue.task_done()
            except queue.Empty: continue
            except Exception as e:
                print(f"[{self.port}] Error in command processor loop: {e}")
                self.gui_queue.put(StatusUpdate(f"Cmd Proc Error ({self.port}): {e}", is_error=True))
                time.sleep(0.5)
        # print(f"[{self.port}] Command processor thread exiting.") # Less verbose

    def queue_command(self, command_type, args, board_idx):
        """Adds a command to the processing queue."""
        self.command_queue.put((command_type, args, board_idx))

    # --- Public methods to queue commands ---
    def send_led_command(self, duty_values, board_idx):
        self.queue_command(self.CMD_SETALL, tuple(duty_values), board_idx)

    def set_fan_speed_command(self, percentage, board_idx):
        self.queue_command(self.CMD_FAN_SET, int(percentage), board_idx)

    def turn_fan_on_command(self, board_idx):
        with self.lock: speed_to_set = self.fan_speed if self.fan_speed > 0 else 50
        self.set_fan_speed_command(speed_to_set, board_idx)

    def turn_fan_off_command(self, board_idx):
        self.set_fan_speed_command(0, board_idx)

    def cleanup(self):
        """Clean up resources: stop processor, close port."""
        # print(f"[{self.port}] Initiating cleanup...") # Less verbose
        self._stop_command_processor()
        with self.lock: self._disconnect()
        # print(f"[{self.port}] Cleanup complete.") # Less verbose


class LEDControlGUI:
    """Main GUI application - Optimized for performance."""

    def __init__(self, root):
        self.root = root
        self.root.title("SpecAC-HT Control System")
        self.root.geometry("1400x900")

        self.gui_queue = queue.Queue()
        self.queue_check_interval = TIMINGS['queue_check_interval']

        self.background_operations = {}

        self.status_var = tk.StringVar(value="Initializing...")

        # --- Initialize Core Data Structures ---
        self.boards = []
        self.board_frames = []
        self.led_entries = {}
        self.chamber_to_board_idx = {}
        self.serial_to_board_idx = {}
        self.master_on = True
        self.saved_values = {}
        self.fans_on = False
        self.fan_speed_var = tk.StringVar(value="50")
        self.channel_schedules = {}
        self.channel_time_entries = {}
        self.channel_schedule_vars = {}
        self.channel_schedule_frames = {}
        self.scheduler_running = False
        self.adaptive_check_timer = None
        self.last_schedule_state = {}
        self.scheduler_check_interval = TIMINGS['scheduler_default']
        self.status_update_batch = []
        self.status_update_timer = None
        self.chamber_mapping = {}
        self.reverse_chamber_mapping = {}
        self.current_page = 0
        # self.boards_per_page is now accessed via self.board_layout
        # --- End Core Data Structures ---

        # --- Caching and Setup ---
        self.cmd_messages = CMD_MESSAGES
        self.widget_sizes = WIDGET_SIZES
        self.board_layout = BOARD_LAYOUT
        self.boards_per_page = self.board_layout['boards_per_page'] # Assign derived value
        self.create_font_cache()
        self.create_color_cache() # Call before setup_styles
        self.setup_styles()
        self.load_chamber_mapping()
        self.setup_validation_commands()
        # --- End Caching and Setup ---

        # --- Initialize GUI ---
        self.create_gui()
        self.initialize_port_cache() # Cache ports after GUI exists
        self.process_gui_queue() # Start queue processing
        self.scan_boards() # Start initial scan
        self.set_status("Ready.")

    def setup_styles(self):
        """Setup and cache TTK styles"""
        self.style = ttk.Style()
        try: self.style.theme_use('clam')
        except tk.TclError: print("Warning: 'clam' theme not available, using default.")

        # Configure styles using cached fonts and colors
        # Check if attributes exist before using them
        if hasattr(self, 'cached_fonts'):
            self.style.configure('Header.TLabel', font=self.cached_fonts['header'])
            self.style.configure('Subheader.TLabel', font=self.cached_fonts['subheader'])
        else:
            print("Error: cached_fonts missing during setup_styles")
            self.style.configure('Header.TLabel', font=('Helvetica', 16, 'bold'))
            self.style.configure('Subheader.TLabel', font=('Helvetica', 12, 'bold'))

        if hasattr(self, 'cached_colors'):
            self.style.configure('Success.TLabel', foreground=self.cached_colors['success'])
            self.style.configure('Error.TLabel', foreground=self.cached_colors['error'])
            self.style.configure('Warning.TLabel', foreground=self.cached_colors['warning'])
            self.style.configure('ScheduleBase.TFrame', background=self.cached_colors['schedule_frame_bg'], borderwidth=1, relief="groove")
            self.style.configure('ActiveSchedule.TFrame', background=self.cached_colors['active_bg'])
            self.style.configure('InactiveSchedule.TFrame', background=self.cached_colors['inactive_bg'])
        else:
            print("Error: cached_colors missing during setup_styles")

    def create_font_cache(self):
        """Create cached font configurations"""
        base_font = "Helvetica"
        self.cached_fonts = {
            'header': (base_font, 16, 'bold'), 'subheader': (base_font, 12, 'bold'),
            'subheader_small': (base_font, 10, 'bold'), 'normal': (base_font, 10, 'normal'),
            'small': (base_font, 8, 'normal'), 'monospace': ('Courier', 10, 'normal'),
            'button': (base_font, 10, 'normal'), 'status': (base_font, 9, 'normal'),
            'schedule_label': (base_font, 8, 'normal'), 'schedule_entry': (base_font, 8, 'normal')
        }

    def create_color_cache(self):
        """Cache colors for better performance"""
        self.cached_colors = UI_COLORS

    def setup_validation_commands(self):
        """Set up and cache validation commands"""
        vcmd_percentage = (self.root.register(self.validate_percentage), '%P')
        vcmd_time_hhmm = (self.root.register(self.validate_time_hhmm_format), '%P')
        self.validation_commands = {
            'percentage': vcmd_percentage,
            'time_hhmm': vcmd_time_hhmm
        }

    def initialize_port_cache(self):
        """Initialize the serial port cache"""
        global CACHED_PORT_INFO, BOARD_SERIAL_CACHE
        CACHED_PORT_INFO = {}
        BOARD_SERIAL_CACHE = {}
        try:
            for port_info in list_ports.comports():
                if port_info.vid == 0x2E8A and port_info.pid == 0x0005 and port_info.serial_number:
                    CACHED_PORT_INFO[port_info.device] = {
                        'serial_number': port_info.serial_number, 'description': port_info.description,
                        'hwid': port_info.hwid, 'device': port_info.device
                    }
                    BOARD_SERIAL_CACHE[port_info.serial_number] = port_info.device
        except Exception as e:
            self.set_status(f"Error initializing port cache: {e}", is_error=True)
            print(f"Error initializing port cache: {e}")

    def load_chamber_mapping(self):
        """Load the chamber to serial number mapping."""
        self.chamber_mapping = {}
        self.reverse_chamber_mapping = {}
        try:
            if os.path.exists(SERIAL_MAPPING_FILE):
                with open(SERIAL_MAPPING_FILE, 'r') as f:
                    for line_num, line in enumerate(f, 1):
                        line = line.strip()
                        if not line or line.startswith('#'): continue
                        match = SERIAL_MAPPING_PATTERN.match(line)
                        if match:
                            try:
                                chamber_num = int(match.group(1))
                                serial_num = match.group(2).strip()
                                if not serial_num: raise ValueError("Serial number empty")
                                self.chamber_mapping[serial_num] = chamber_num
                                self.reverse_chamber_mapping[chamber_num] = serial_num
                            except ValueError as ve: print(f"Warn: Invalid mapping line {line_num}: '{line}' - {ve}")
                self.set_status(f"Loaded mapping for {len(self.chamber_mapping)} chambers.")
            else:
                warning_msg = f"Chamber mapping file not found: {SERIAL_MAPPING_FILE}"
                print(warning_msg)
                self.set_status(warning_msg, is_error=True)
        except Exception as e:
            error_msg = f"Error loading chamber mapping: {str(e)}"
            self.set_status(error_msg, is_error=True)
            print(error_msg)

    def create_gui(self):
        """Create the main GUI layout"""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(column=0, row=0, sticky="nsew")
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # --- Top Control Frame ---
        top_control_frame = ttk.Frame(main_frame)
        top_control_frame.grid(column=0, row=0, sticky="ew", pady=(0, 10))
        top_control_frame.columnconfigure(1, weight=1)
        # Left
        left_controls = ttk.Frame(top_control_frame)
        left_controls.grid(column=0, row=0, sticky="w")
        ttk.Label(left_controls, text="LED Control", font=self.cached_fonts['header']).pack(side=tk.LEFT, padx=(0, 20)) # Shorter title
        self.master_button_var = tk.StringVar(value="All Lights OFF")
        ttk.Button(left_controls,textvariable=self.master_button_var, command=self.toggle_all_lights, width=self.widget_sizes['button_width']).pack(side=tk.LEFT)
        # Middle
        middle_controls = ttk.Frame(top_control_frame)
        middle_controls.grid(column=1, row=0, sticky="ew")
        self.scheduler_button_var = tk.StringVar(value="Start Scheduler")
        ttk.Button(middle_controls, textvariable=self.scheduler_button_var, command=self.toggle_scheduler, width=self.widget_sizes['button_width']).pack(anchor=tk.CENTER)
        # Right
        right_controls = ttk.Frame(top_control_frame)
        right_controls.grid(column=2, row=0, sticky="e")
        ttk.Button(right_controls, text="Scan", command=self.scan_boards, width=8).pack(side=tk.LEFT, padx=5) # Shorter text
        ttk.Button(right_controls, text="Apply All", command=self.apply_all_settings, width=10).pack(side=tk.LEFT, padx=5) # Shorter text

        # --- Boards Display Area ---
        boards_area_frame = ttk.Frame(main_frame)
        boards_area_frame.grid(column=0, row=1, sticky="nsew")
        main_frame.rowconfigure(1, weight=1)
        main_frame.columnconfigure(0, weight=1)
        # Navigation
        nav_frame = ttk.Frame(boards_area_frame)
        nav_frame.pack(fill=tk.X, pady=5)
        nav_frame.columnconfigure(1, weight=1)
        self.prev_button = ttk.Button(nav_frame, text="◀ Prev", command=self.prev_page, width=10, state=tk.DISABLED)
        self.prev_button.grid(column=0, row=0, padx=10)
        self.page_label = ttk.Label(nav_frame, text="Chambers -", font=self.cached_fonts['subheader'], anchor=tk.CENTER)
        self.page_label.grid(column=1, row=0, sticky="ew")
        self.next_button = ttk.Button(nav_frame, text="Next ▶", command=self.next_page, width=10, state=tk.DISABLED)
        self.next_button.grid(column=2, row=0, padx=10)
        # Page Container
        self.page_container = ttk.Frame(boards_area_frame)
        self.page_container.pack(expand=True, fill=tk.BOTH, padx=5, pady=5)
        self.page_frames = {}
        num_pages = (MAX_BOARDS + self.boards_per_page - 1) // self.boards_per_page
        cols_per_page = self.board_layout['cols_per_page']
        rows_per_page = self.board_layout['rows_per_page']
        for page_id in range(num_pages):
            page_frame = ttk.Frame(self.page_container)
            page_frame.place(x=0, y=0, relwidth=1, relheight=1)
            for c in range(cols_per_page): page_frame.columnconfigure(c, weight=1, minsize=180)
            for r in range(rows_per_page): page_frame.rowconfigure(r, weight=1, minsize=250)
            self.page_frames[page_id] = page_frame
        if 0 in self.page_frames: self.page_frames[0].tkraise()

        # --- Fan Control Frame ---
        fan_frame = ttk.LabelFrame(main_frame, text="Fan Controls")
        fan_frame.grid(column=0, row=2, sticky="ew", pady=5, padx=10)
        ttk.Button(fan_frame, textvariable=self.fan_button_var, command=self.toggle_all_fans, width=self.widget_sizes['button_width']).grid(column=0, row=0, padx=10, pady=2)
        ttk.Label(fan_frame, text="Fan Speed:").grid(column=1, row=0, padx=(20, 5), pady=2)
        fan_speed_entry = ttk.Entry(fan_frame, width=self.widget_sizes['entry_width'], textvariable=self.fan_speed_var, validate='key', validatecommand=self.validation_commands['percentage'])
        fan_speed_entry.grid(column=2, row=0, padx=5, pady=2)
        ttk.Label(fan_frame, text="%").grid(column=3, row=0, padx=(0, 5), pady=2)
        ttk.Button(fan_frame, text="Apply Fans", command=self.apply_fan_settings, width=12).grid(column=4, row=0, padx=10, pady=2) # Shorter text

        # --- Bottom Frame (Import/Export) ---
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.grid(column=0, row=3, sticky="ew", pady=5, padx=10)
        bottom_frame.columnconfigure(0, weight=1)
        bottom_frame.columnconfigure(1, weight=1)
        ttk.Button(bottom_frame, text="Export", command=self.export_settings, width=10).grid(column=0, row=0, sticky="w", padx=5) # Shorter text
        ttk.Button(bottom_frame, text="Import", command=self.import_settings, width=10).grid(column=1, row=0, sticky="e", padx=5) # Shorter text

        # --- Status Bar ---
        self.status_bar_widget = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, font=self.cached_fonts['status'])
        self.status_bar_widget.grid(column=0, row=4, sticky="ew")

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        """Clean up resources and close the application"""
        # print("Closing application...") # Less verbose
        if messagebox.askokcancel("Quit", "Quit application? This stops schedules and device control."):
            # Cancel timers
            if self.adaptive_check_timer: self.root.after_cancel(self.adaptive_check_timer)
            if self.status_update_timer: self.root.after_cancel(self.status_update_timer)
            self.adaptive_check_timer = None; self.status_update_timer = None
            self.scheduler_running = False
            # print("Timers cancelled, scheduler stopped.") # Less verbose

            # Turn off devices
            # print("Turning off all lights and fans...") # Less verbose
            boards_to_turn_off = list(self.boards)
            if boards_to_turn_off:
                for i, board in enumerate(boards_to_turn_off):
                    board.queue_command(BoardConnection.CMD_SETALL, ZERO_DUTY_CYCLES, i)
                    board.queue_command(BoardConnection.CMD_FAN_SET, 0, i)
                # print("Waiting briefly for OFF commands...") # Less verbose
                time.sleep(0.8) # Reduced wait
            # else: print("No boards connected.") # Less verbose

            # Cleanup connections
            # print(f"Cleaning up {len(boards_to_turn_off)} board connections...") # Less verbose
            cleanup_threads = []
            for i, board in enumerate(boards_to_turn_off):
                thread = threading.Thread(target=board.cleanup, name=f"Cleanup-{board.port}")
                cleanup_threads.append(thread); thread.start()
            for thread in cleanup_threads:
                thread.join(timeout=2.5) # Reduced timeout
                if thread.is_alive(): print(f"Warn: Cleanup thread {thread.name} timed out.")
            # print("Board cleanup finished.") # Less verbose

            # print("Destroying root window...") # Less verbose
            self.root.destroy()
            print("Application closed.")
        # else: print("Quit cancelled.") # Less verbose

    def next_page(self):
        """Navigate to the next page of chambers"""
        num_boards_total = len(self.boards)
        if num_boards_total == 0: return
        num_pages = (num_boards_total + self.boards_per_page - 1) // self.boards_per_page
        if self.current_page < num_pages - 1:
            self.current_page += 1
            self.update_page_display()

    def prev_page(self):
        """Navigate to the previous page of chambers"""
        if self.current_page > 0:
            self.current_page -= 1
            self.update_page_display()

    def update_page_display(self):
        """Update the display to show the correct page of chambers"""
        num_boards_total = len(self.boards)
        num_pages = 0
        if num_boards_total > 0:
            num_pages = (num_boards_total + self.boards_per_page - 1) // self.boards_per_page

        if num_boards_total == 0 or num_pages == 0:
             self.page_label.config(text="No Boards Found")
             self.prev_button.config(state=tk.DISABLED)
             self.next_button.config(state=tk.DISABLED)
             if 0 in self.page_frames: self.page_frames[0].tkraise()
             return

        if not (0 <= self.current_page < num_pages): self.current_page = 0
        current_page_idx = self.current_page

        if current_page_idx in self.page_frames: self.page_frames[current_page_idx].tkraise()
        else: print(f"Error: Page frame {current_page_idx} not found.")

        start_board_idx = current_page_idx * self.boards_per_page
        end_board_idx = min(start_board_idx + self.boards_per_page, num_boards_total)
        try:
            if 0 <= start_board_idx < num_boards_total and 0 <= end_board_idx -1 < num_boards_total:
                 start_num = self.boards[start_board_idx].chamber_number or (start_board_idx + 1)
                 end_num = self.boards[end_board_idx - 1].chamber_number or end_board_idx
                 self.page_label.config(text=f"Chambers {start_num}-{end_num} (Page {current_page_idx + 1}/{num_pages})")
            else: self.page_label.config(text=f"Page {current_page_idx + 1}/{num_pages}")
        except IndexError: self.page_label.config(text=f"Page {current_page_idx + 1}/{num_pages}")

        self.prev_button.config(state=tk.NORMAL if current_page_idx > 0 else tk.DISABLED)
        self.next_button.config(state=tk.NORMAL if current_page_idx < num_pages - 1 else tk.DISABLED)

    def create_board_frames(self):
        """Create frames for each detected board, optimized."""
        # --- Clear existing elements ---
        for frame in self.board_frames:
            try: frame.destroy()
            except tk.TclError: pass
        self.board_frames = []
        self.led_entries.clear(); self.channel_time_entries.clear()
        self.channel_schedule_vars.clear(); self.channel_schedule_frames.clear()
        self.chamber_to_board_idx.clear(); self.serial_to_board_idx.clear()
        self.channel_schedules.clear(); self.last_schedule_state.clear()

        if not self.boards:
            self.update_page_display(); return

        self.boards.sort(key=lambda b: b.chamber_number if b.chamber_number is not None else float('inf'))

        # --- Pre-cache values used in the loop ---
        validate_percent_cmd = self.validation_commands['percentage']
        validate_time_cmd = self.validation_commands['time_hhmm']
        font_normal = self.cached_fonts['normal']
        font_small = self.cached_fonts['small']
        font_sched_label = self.cached_fonts['schedule_label']
        font_sched_entry = self.cached_fonts['schedule_entry']
        entry_width = self.widget_sizes['entry_width']
        time_entry_width = self.widget_sizes['time_entry_width']
        frame_pad = self.board_layout['frame_padding']
        pad = self.board_layout['padding']
        cols_per_page = self.board_layout['cols_per_page']
        boards_per_page = self.boards_per_page
        default_on_time_hhmm = "0800" # Default HHMM
        default_off_time_hhmm = "0000" # Default HHMM
        default_on_time_internal = "08:00" # Default HH:MM
        default_off_time_internal = "00:00" # Default HH:MM

        # --- Create Frames ---
        for i, board in enumerate(self.boards):
            chamber_num = board.chamber_number
            serial_num = board.serial_number
            if chamber_num is not None: self.chamber_to_board_idx[chamber_num] = i
            if serial_num is not None: self.serial_to_board_idx[serial_num] = i

            page_id = i // boards_per_page
            if page_id not in self.page_frames: continue

            page_frame = self.page_frames[page_id]
            row_in_page = (i % boards_per_page) // cols_per_page
            col_in_page = (i % boards_per_page) % cols_per_page

            frame_text = f"Chamber {chamber_num}" if chamber_num else f"Board {i+1}" # Simplified text
            board_frame = ttk.LabelFrame(page_frame, text=frame_text, padding=frame_pad)
            board_frame.grid(row=row_in_page, column=col_in_page, padx=pad, pady=pad, sticky="nsew")
            self.board_frames.append(board_frame)
            board_frame.columnconfigure(0, weight=1)

            self.channel_schedules[i] = {} # Initialize schedules

            # --- Create LED Controls ---
            for led_row, channel_name in enumerate(LED_CHANNEL_NAMES):
                # Initialize internal schedule data
                self.channel_schedules[i][channel_name] = {"on_time": default_on_time_internal, "off_time": default_off_time_internal, "enabled": False, "active": True}
                sched_info = self.channel_schedules[i][channel_name]

                channel_frame = ttk.Frame(board_frame, padding=(5, 1))
                channel_frame.grid(row=led_row, column=0, sticky="ew", pady=0)
                channel_frame.columnconfigure(4, weight=1) # Spacer

                # Widgets (using cached values)
                color_bg = LED_COLORS.get(channel_name, "#CCCCCC")
                tk.Frame(channel_frame, width=15, height=15, relief=tk.SUNKEN, borderwidth=1, bg=color_bg).grid(column=0, row=0, padx=(0, 5), sticky=tk.W)
                ttk.Label(channel_frame, text=f"{channel_name}:", width=8, anchor=tk.W).grid(column=1, row=0, sticky=tk.W)
                value_var = tk.StringVar(value="0")
                entry = ttk.Entry(channel_frame, width=entry_width, textvariable=value_var, validate='key', validatecommand=validate_percent_cmd, font=font_normal)
                entry.grid(column=2, row=0, sticky=tk.W, padx=2)
                self.led_entries[(i, channel_name)] = entry
                ttk.Label(channel_frame, text="%", font=font_small).grid(column=3, row=0, sticky=tk.W, padx=(0, 10))

                # Schedule Section Widgets
                schedule_frame = ttk.Frame(channel_frame, style='ScheduleBase.TFrame')
                schedule_frame.grid(column=5, row=0, sticky=tk.E, padx=(5,0))
                self.channel_schedule_frames[(i, channel_name)] = schedule_frame

                ttk.Label(schedule_frame, text="On:", font=font_sched_label).grid(column=0, row=0, padx=(5, 2), pady=1, sticky=tk.W)
                on_time_var = tk.StringVar(value=default_on_time_hhmm)
                on_time_entry = ttk.Entry(schedule_frame, width=time_entry_width, textvariable=on_time_var, font=font_sched_entry, validate='key', validatecommand=validate_time_cmd)
                on_time_entry.grid(column=1, row=0, padx=(0, 5), pady=0)
                self.channel_time_entries[(i, channel_name, "on")] = on_time_entry
                on_time_var.trace_add("write", lambda n, idx, m, b=i, c=channel_name, v=on_time_var, e=on_time_entry: self.validate_time_entry_visual_hhmm(b, c, "on", v.get(), e))

                ttk.Label(schedule_frame, text="Off:", font=font_sched_label).grid(column=0, row=1, padx=(5, 2), pady=1, sticky=tk.W)
                off_time_var = tk.StringVar(value=default_off_time_hhmm)
                off_time_entry = ttk.Entry(schedule_frame, width=time_entry_width, textvariable=off_time_var, font=font_sched_entry, validate='key', validatecommand=validate_time_cmd)
                off_time_entry.grid(column=1, row=1, padx=(0, 5), pady=0)
                self.channel_time_entries[(i, channel_name, "off")] = off_time_entry
                off_time_var.trace_add("write", lambda n, idx, m, b=i, c=channel_name, v=off_time_var, e=off_time_entry: self.validate_time_entry_visual_hhmm(b, c, "off", v.get(), e))

                schedule_var = tk.BooleanVar(value=False) # Default disabled
                schedule_check = ttk.Checkbutton(schedule_frame, text="En", variable=schedule_var, command=lambda b=i, c=channel_name: self.update_channel_schedule(b, c))
                schedule_check.grid(column=2, row=0, rowspan=2, padx=(0, 5), pady=0, sticky=tk.W)
                self.channel_schedule_vars[(i, channel_name)] = schedule_var

            # Apply Button
            apply_button = ttk.Button(board_frame, text="Apply", command=lambda b=i: self.apply_board_settings(b))
            apply_button.grid(row=NUM_LED_CHANNELS, column=0, pady=(8, 4), sticky="ew")

        # --- Finalize ---
        self.current_page = 0
        self.update_page_display()

    def toggle_all_lights(self):
        """Toggle all lights on or off on all boards using master control."""
        if not self.boards: messagebox.showwarning("No Boards", "No boards available."); return
        target_state_on = not self.master_on
        self.master_on = target_state_on
        self.master_button_var.set("All Lights OFF" if self.master_on else "All Lights ON")
        status_msg = self.cmd_messages['lights_on'] if self.master_on else self.cmd_messages['lights_off']
        self.set_status(status_msg + "...")
        if self.master_on: self.apply_all_settings()
        else:
            # print(f"Master Toggle OFF: Queuing OFF command for {len(self.boards)} boards.") # Less verbose
            for idx, board in enumerate(self.boards): board.send_led_command(ZERO_DUTY_CYCLES, idx)

    def toggle_scheduler(self):
        """Enable or disable the scheduler globally."""
        if self.scheduler_running:
            self.scheduler_running = False
            self.scheduler_button_var.set("Start Scheduler")
            self.set_status(self.cmd_messages['scheduler_stop'])
            if self.adaptive_check_timer: self.root.after_cancel(self.adaptive_check_timer)
            self.adaptive_check_timer = None
            print("Scheduler stopped.")
        else:
            self.scheduler_running = True
            self.scheduler_button_var.set("Stop Scheduler")
            self.set_status(self.cmd_messages['scheduler_start'])
            print("Scheduler started.")
            self.schedule_check() # Start first check

    def start_scheduler(self):
        """Start the scheduler if not already running."""
        if not self.scheduler_running: self.toggle_scheduler()

    def schedule_check(self):
        """Periodic scheduler check."""
        if self.adaptive_check_timer: self.root.after_cancel(self.adaptive_check_timer)
        self.adaptive_check_timer = None
        if not self.scheduler_running: return
        threading.Thread(target=self._schedule_check_worker, daemon=True, name="SchedulerCheck").start()
        next_delay = max(50, self.scheduler_check_interval)
        self.adaptive_check_timer = self.root.after(next_delay, self.schedule_check)

    def _schedule_check_worker(self):
        """Background worker for schedule checking."""
        current_dt = datetime.now()
        current_time_hhmm = current_dt.strftime("%H:%M")
        min_diff = float('inf')
        boards_to_update = set()
        num_boards = len(self.boards)
        # Iterate directly over indices present in the schedule dict
        for board_idx in list(self.channel_schedules.keys()):
            if board_idx >= num_boards: continue # Check index validity
            channels = self.channel_schedules.get(board_idx, {})
            for cn, sched_info in channels.items():
                if not sched_info.get("enabled"): continue
                on_t = sched_info.get("on_time", ""); off_t = sched_info.get("off_time", "")
                if not self.validate_internal_time_format(on_t) or not self.validate_internal_time_format(off_t): continue
                try:
                    on_h, on_m = map(int, on_t.split(':')); off_h, off_m = map(int, off_t.split(':'))
                    curr_m = current_dt.hour * 60 + current_dt.minute
                    on_mins = on_h * 60 + on_m; off_mins = off_h * 60 + off_m
                    diff_on = (on_mins - curr_m + 1440) % 1440; diff_off = (off_mins - curr_m + 1440) % 1440
                    min_diff = min(min_diff, diff_on, diff_off)
                    active = self.is_time_between(current_time_hhmm, on_t, off_t)
                    cache_key = (board_idx, cn)
                    prev_active = self.last_schedule_state.get(cache_key, {}).get("active")
                    if prev_active is None or prev_active != active:
                        self.last_schedule_state[cache_key] = {"active": active, "last_check": current_dt}
                        self.gui_queue.put(SchedulerUpdate(board_idx, cn, active))
                        boards_to_update.add(board_idx)
                except Exception as e: print(f"Err schedule {board_idx}-{cn}: {e}")
        if boards_to_update: self.root.after(0, lambda b=list(boards_to_update): self.apply_settings_to_multiple_boards(b))
        self.scheduler_check_interval = self.calculate_adaptive_interval(min_diff)

    def calculate_adaptive_interval(self, min_time_diff_minutes):
        """Calculate adaptive timer interval."""
        if min_time_diff_minutes == float('inf'): return TIMINGS['scheduler_relaxed'] * 2
        if min_time_diff_minutes <= 1: return TIMINGS['scheduler_urgent']
        if min_time_diff_minutes <= 5: return TIMINGS['scheduler_normal'] // 2
        if min_time_diff_minutes <= 15: return TIMINGS['scheduler_normal']
        return TIMINGS['scheduler_relaxed']

    def scan_boards(self):
        """Detect and initialize connections to boards."""
        if self.background_operations.get('scan'): return # Scan already running
        # print("Starting board scan...") # Less verbose
        self.set_status(self.cmd_messages['scan_start'])
        self.background_operations['scan'] = True
        threading.Thread(target=self._disconnect_all_boards_async, daemon=True).start()

    def _disconnect_all_boards_async(self):
        """Helper to disconnect all current boards."""
        # print("Disconnecting existing boards...") # Less verbose
        boards_to_disconnect = list(self.boards); self.boards = []
        threads = [threading.Thread(target=b.cleanup, name=f"Disc-{b.port}") for b in boards_to_disconnect]
        for t in threads: t.start()
        for t in threads: t.join(timeout=1.5) # Reduced timeout
        self.root.after(0, self._clear_gui_elements)
        self.root.after(10, self._start_scan_worker)

    def _clear_gui_elements(self):
        """Clears GUI elements related to boards."""
        for frame in self.board_frames:
            try: frame.destroy()
            except tk.TclError: pass
        self.board_frames = []; self.led_entries.clear(); self.channel_time_entries.clear()
        self.channel_schedule_vars.clear(); self.channel_schedule_frames.clear()
        self.chamber_to_board_idx.clear(); self.serial_to_board_idx.clear()
        self.channel_schedules.clear(); self.last_schedule_state.clear()
        self.master_on = True; self.master_button_var.set("All Lights OFF"); self.saved_values = {}
        self.update_page_display()

    def _start_scan_worker(self):
        """Starts the background thread for scanning boards."""
        threading.Thread(target=self._scan_boards_worker, daemon=True, name="BoardScan").start()

    def _scan_boards_worker(self):
        """Background worker thread for scanning boards."""
        boards_created = []; error_msg = None
        try:
            detected_info = self.detect_xiao_boards()
            if detected_info:
                for port, sn, cn in detected_info:
                    if port and sn: boards_created.append(BoardConnection(port, sn, self.gui_queue, cn))
            self.gui_queue.put(BoardsDetected(boards_created))
        except Exception as e:
            error_msg = f"Error during board scan: {e}"; print(f"Scan Worker Error: {error_msg}")
            self.gui_queue.put(BoardsDetected([], error=error_msg))

    def detect_xiao_boards(self):
        """Detect connected XIAO boards and assign chamber numbers."""
        results = []
        try: ports_info = list_ports.comports()
        except Exception as e: print(f"Error listing ports: {e}"); self.gui_queue.put(StatusUpdate(f"Error listing ports: {e}", True)); return []
        xiao_ports = [p for p in ports_info if p.vid == 0x2E8A and p.pid == 0x0005]
        temp_ids = set(); existing_chambers = set(self.chamber_mapping.values())
        for p_info in xiao_ports:
            sn = p_info.serial_number; port = p_info.device
            if not sn: continue
            cn = self.chamber_mapping.get(sn)
            if cn is None:
                warn_msg = f"Warn: Board S/N {sn} ({port}) not mapped."
                temp_id = 1000
                while temp_id in temp_ids or temp_id in existing_chambers: temp_id += 1
                cn = temp_id; temp_ids.add(cn)
                warn_msg += f" Assigned Temp ID {cn}"
                self.gui_queue.put(StatusUpdate(warn_msg, True))
            results.append([port, sn, cn])
        return results

    def validate_percentage(self, P):
        """Validation command for percentage entries (0-100)."""
        if P == "": return True
        try:
            if 0 <= int(P) <= 100: return True
        except ValueError: pass
        self.root.bell(); return False

    def validate_time_hhmm_format(self, P):
        """Validation command for HHMM time entries."""
        if P == "": return True
        if len(P) <= 4 and bool(TIME_PATTERN.match(P.ljust(4, '0'))):
             if len(P) == 4: return bool(TIME_PATTERN.match(P))
             return True # Allow partial
        self.root.bell(); return False

    def validate_internal_time_format(self, time_str_hhmm):
        """Validate internal HH:MM time format."""
        if not isinstance(time_str_hhmm, str): return False
        parts = time_str_hhmm.split(':')
        if len(parts) != 2: return False
        try: h, m = int(parts[0]), int(parts[1]); return 0 <= h <= 23 and 0 <= m <= 59
        except ValueError: return False

    def apply_all_settings(self):
        """Apply current UI settings to all connected boards."""
        if not self.boards: messagebox.showwarning("No Boards", "No boards available."); return
        board_indices = list(range(len(self.boards)))
        if not board_indices: return
        if self.background_operations.get('apply_all'): self.set_status("Apply all already running..."); return
        self.set_status(self.cmd_messages['apply_start'] + f" to {len(board_indices)} boards...")
        self.background_operations['apply_all'] = True
        threading.Thread(target=self._apply_settings_to_multiple_worker, args=(list(board_indices), True), daemon=True, name="ApplyAll").start()

    def apply_settings_to_multiple_boards(self, board_indices):
         """Helper to apply settings to a list of board indices."""
         valid_indices = [idx for idx in board_indices if 0 <= idx < len(self.boards)]
         if not valid_indices: return
         self.set_status(f"Applying settings to {len(valid_indices)} boards...")
         threading.Thread(target=self._apply_settings_to_multiple_worker, args=(valid_indices, False), daemon=True, name=f"ApplySome").start()

    def _apply_settings_to_multiple_worker(self, board_indices, is_apply_all):
        """Background worker for applying settings."""
        num_boards = len(board_indices); processed_count = 0
        all_ui_data = {}; collect_result = {'data': {}, 'error': None, 'complete': False}
        def collect_batch_ui_data():
            batch_data = {}
            try:
                for idx in board_indices:
                    if idx >= len(self.boards): continue
                    board_data = {cn: self.duty_cycle_from_percentage(self.led_entries.get((idx, cn)).get() if self.led_entries.get((idx, cn)) else 0) for cn in LED_CHANNEL_NAMES}
                    batch_data[idx] = board_data
                collect_result['data'] = batch_data
            except Exception as e: collect_result['error'] = f"Error collecting UI data: {e}"
            finally: collect_result['complete'] = True
        self.root.after(0, collect_batch_ui_data)
        timeout = time.time() + 2.0 # Shorter timeout
        while not collect_result['complete'] and time.time() < timeout: time.sleep(0.01)
        if not collect_result['complete'] or collect_result['error']:
             error_msg = collect_result['error'] or "Timeout collecting UI data."
             print(f"Apply Worker Error: {error_msg}")
             self.gui_queue.put(StatusUpdate(error_msg, is_error=True))
             if is_apply_all: self.root.after(0, lambda: self.background_operations.pop('apply_all', None))
             return
        all_ui_data = collect_result['data']

        try:
            current_time_hhmm = datetime.now().strftime("%H:%M")
            for board_idx in board_indices:
                if board_idx not in all_ui_data or board_idx >= len(self.boards): continue
                board = self.boards[board_idx]
                ui_percentages = all_ui_data[board_idx] # This actually contains duty cycles now
                final_duties = list(ZERO_DUTY_CYCLES)
                for channel_idx, channel_name in enumerate(LED_CHANNEL_NAMES):
                    sched_info = self.channel_schedules.get(board_idx, {}).get(channel_name, {})
                    apply_ui_value = True # Default to applying UI value
                    if sched_info.get("enabled"):
                        on_t = sched_info.get("on_time", ""); off_t = sched_info.get("off_time", "")
                        if self.validate_internal_time_format(on_t) and self.validate_internal_time_format(off_t):
                            if not self.is_time_between(current_time_hhmm, on_t, off_t):
                                apply_ui_value = False # Scheduled OFF
                    if apply_ui_value:
                        # Get percentage from UI data (needs modification in collection)
                        # For now, assume ui_percentages holds percentages, convert here
                        # This is inefficient - collection should get percentages
                        # Let's assume collection gets percentages for now
                        percentage = ui_percentages.get(channel_name, 0) # Assume this key exists
                        final_duties[channel_idx] = self.duty_cycle_from_percentage(percentage)
                    # else: final_duties remains 0

                board.send_led_command(final_duties, board_idx)
                processed_count += 1
                time.sleep(TIMINGS['apply_batch_delay'])
        except Exception as e:
             print(f"Error in apply worker loop: {e}")
             self.gui_queue.put(StatusUpdate(f"Error applying settings: {e}", True))
        finally:
            if is_apply_all:
                 self.root.after(0, lambda: self.background_operations.pop('apply_all', None))
                 final_msg = f"Finished queuing settings for {processed_count}/{num_boards} boards."
                 self.gui_queue.put(StatusUpdate(final_msg))

    def apply_board_settings(self, board_idx):
        """Apply settings for a specific board (called by button)."""
        if board_idx >= len(self.boards): messagebox.showerror("Error", f"Invalid board index: {board_idx}"); return
        chamber = self.boards[board_idx].chamber_number or (board_idx + 1)
        self.set_status(f"Applying settings to Chamber {chamber}...")
        self.apply_settings_to_multiple_boards([board_idx])

    def toggle_all_fans(self):
        """Toggle all fans on or off."""
        if not self.boards: messagebox.showwarning("No Boards", "No boards available."); return
        target_on = not self.fans_on; speed = 0
        if target_on:
            try:
                speed = int(self.fan_speed_var.get())
                if not (0 <= speed <= 100): speed = 50; self.fan_speed_var.set("50")
            except ValueError: speed = 50; self.fan_speed_var.set("50")
        self.fans_on = target_on; self.fan_button_var.set("Turn Fans OFF" if target_on else "Turn Fans ON")
        msg = f"Turning fans {'ON' if target_on else 'OFF'}"
        if target_on: msg += f" at {speed}%..."
        self.set_status(msg)
        speed_cmd = speed if target_on else 0
        for i, board in enumerate(self.boards): board.set_fan_speed_command(speed_cmd, i)

    def apply_fan_settings(self):
        """Apply the fan speed from the UI to all boards."""
        if not self.boards: messagebox.showwarning("No Boards", "No boards available."); return
        try:
            speed = int(self.fan_speed_var.get())
            if not (0 <= speed <= 100): messagebox.showerror("Invalid Speed", "Speed must be 0-100."); return
        except ValueError: messagebox.showerror("Invalid Speed", "Speed must be a number."); return
        self.fans_on = (speed > 0); self.fan_button_var.set("Turn Fans OFF" if self.fans_on else "Turn Fans ON")
        self.set_status(f"Setting all fans to {speed}%...")
        for i, board in enumerate(self.boards): board.set_fan_speed_command(speed, i)

    def export_settings(self):
        """Export current settings to a JSON file"""
        if not self.boards: messagebox.showwarning("No Boards", "No boards to export."); return
        f_path = filedialog.asksaveasfilename(initialdir=DEFAULT_DOCUMENTS_PATH, defaultextension=".json", filetypes=[("JSON files", "*.json")], title="Save Settings")
        if not f_path: self.set_status("Export cancelled."); return
        self.set_status(self.cmd_messages['export_start'])
        settings = {}
        try:
            for idx, board in enumerate(self.boards):
                key = f"chamber_{board.chamber_number}" if board.chamber_number else f"board_{idx}"
                b_data = {"intensity": {}, "schedule": {}, "fan": {"enabled": board.fan_enabled, "speed": board.fan_speed}}
                s_data = {}
                b_sched = self.channel_schedules.get(idx, {})
                for cn in LED_CHANNEL_NAMES:
                    intensity = 0; on_t = "08:00"; off_t = "00:00"; enabled = False
                    entry = self.led_entries.get((idx, cn))
                    if entry:
                        try:
                            if entry.winfo_exists(): intensity = int(entry.get())
                        except (ValueError, tk.TclError): pass
                    b_data["intensity"][cn] = max(0, min(100, intensity))
                    s_info = b_sched.get(cn, {})
                    on_t = s_info.get("on_time", on_t); off_t = s_info.get("off_time", off_t)
                    enabled = s_info.get("enabled", enabled)
                    # Read directly from internal state for export consistency
                    s_data[cn] = {"on_time": on_t, "off_time": off_t, "enabled": bool(enabled)}
                b_data["schedule"] = s_data
                settings[key] = b_data
        except Exception as e:
            err = f"Error collecting settings: {e}"; print(f"Export Error: {err}")
            self.gui_queue.put(FileOperationComplete('export', False, err)); return
        threading.Thread(target=self._export_settings_worker, args=(f_path, settings), daemon=True).start()

    def _export_settings_worker(self, file_path, settings_data):
        """Background worker for saving exported settings."""
        error = None
        try:
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, 'w') as f: json.dump(settings_data, f, indent=4, sort_keys=True)
        except Exception as e: error = f"Error writing file: {e}"
        if error: print(f"Export error: {error}"); self.gui_queue.put(FileOperationComplete('export', False, error))
        else: self.gui_queue.put(FileOperationComplete('export', True, file_path))

    def import_settings(self):
        """Import settings from a JSON file"""
        f_path = filedialog.askopenfilename(initialdir=DEFAULT_DOCUMENTS_PATH, filetypes=[("JSON files", "*.json")], title="Import Settings")
        if not f_path: self.set_status("Import cancelled."); return
        self.set_status(self.cmd_messages['import_start'])
        threading.Thread(target=self._import_settings_reader_worker, args=(f_path,), daemon=True).start()

    def _import_settings_reader_worker(self, file_path):
         """Background worker for reading the import file."""
         settings = None; error = None
         try:
             with open(file_path, 'r') as f: settings = json.load(f)
             if not isinstance(settings, dict): raise ValueError("Invalid format")
         except FileNotFoundError: error = f"File not found: {os.path.basename(file_path)}"
         except json.JSONDecodeError as e: error = f"Invalid JSON: {e}"
         except Exception as e: error = f"Error reading file: {e}"
         if error: print(f"Import error: {error}"); self.gui_queue.put(FileOperationComplete('import', False, error))
         else: self.root.after(0, lambda d=settings, p=file_path: self._apply_imported_settings_to_ui(d, p))

    def _apply_imported_settings_to_ui(self, imported_settings, file_path):
        """Applies loaded settings to the UI (main thread)."""
        if not self.boards: self.gui_queue.put(FileOperationComplete('import', False, "No boards.")); return
        applied = 0; skipped = set(); fan_found = False; error = None; fan_ui_updated = False
        try:
            for key, cfg in imported_settings.items():
                match = CHAMBER_NUM_PATTERN.match(key)
                idx = self.chamber_to_board_idx.get(int(match.group(1))) if match else None
                if idx is None or idx >= len(self.boards): skipped.add(key); continue
                board = self.boards[idx]
                # Intensity
                if "intensity" in cfg and isinstance(cfg["intensity"], dict):
                    for cn, val in cfg["intensity"].items():
                        if cn in LED_CHANNELS:
                            entry = self.led_entries.get((idx, cn))
                            if entry:
                                try:
                                    p_val = int(val)
                                    if 0 <= p_val <= 100 and entry.winfo_exists(): entry.delete(0, tk.END); entry.insert(0, str(p_val)); applied += 1
                                except (ValueError, tk.TclError): pass
                # Schedule
                if "schedule" in cfg and isinstance(cfg["schedule"], dict):
                     if idx not in self.channel_schedules: self.channel_schedules[idx] = {}
                     for cn, chan_sched in cfg["schedule"].items():
                          if cn in LED_CHANNELS and isinstance(chan_sched, dict):
                               on_t = chan_sched.get("on_time", "08:00"); off_t = chan_sched.get("off_time", "00:00"); en = bool(chan_sched.get("enabled", False))
                               if not self.validate_internal_time_format(on_t): on_t = "08:00"
                               if not self.validate_internal_time_format(off_t): off_t = "00:00"
                               self.channel_schedules.setdefault(idx, {}).setdefault(cn, {}).update({"on_time": on_t, "off_time": off_t, "enabled": en})
                               on_t_ui = on_t.replace(":", ""); off_t_ui = off_t.replace(":", "")
                               on_e=self.channel_time_entries.get((idx,cn,"on")); off_e=self.channel_time_entries.get((idx,cn,"off")); en_v=self.channel_schedule_vars.get((idx,cn))
                               try:
                                    if on_e and on_e.winfo_exists(): on_e.delete(0, tk.END); on_e.insert(0, on_t_ui)
                                    if off_e and off_e.winfo_exists(): off_e.delete(0, tk.END); off_e.insert(0, off_t_ui)
                                    if en_v: en_v.set(en)
                                    applied += 1
                               except tk.TclError: pass
                # Fan
                if "fan" in cfg and isinstance(cfg["fan"], dict):
                     fan_data = cfg["fan"]; speed = fan_data.get("speed", 50); fan_en = bool(fan_data.get("enabled", False)); fan_found = True
                     try:
                          v_speed = int(speed)
                          if 0 <= v_speed <= 100:
                               board.fan_speed = v_speed; board.fan_enabled = fan_en
                               if not fan_ui_updated:
                                    self.fan_speed_var.set(str(v_speed)); self.fans_on = fan_en
                                    self.fan_button_var.set("Turn Fans OFF" if self.fans_on else "Turn Fans ON")
                                    fan_ui_updated = True; applied += 1
                     except (ValueError, tk.TclError): pass
        except Exception as e: error = f"Error applying settings to UI: {e}"; print(f"Import Apply Error: {error}")
        # --- Send Result ---
        if error: self.gui_queue.put(FileOperationComplete('import', False, error))
        else:
             msg = f"Applied {applied} settings from {os.path.basename(file_path)}."
             if skipped: msg += f" Skipped: {', '.join(skipped)}."
             data = {'applied_count': applied, 'fan_settings_found': fan_found}
             self.gui_queue.put(FileOperationComplete('import', True, msg, data))

    def validate_time_hhmm_format(self, P):
        """Validation command for HHMM time entries."""
        if P == "": return True
        if len(P) <= 4 and bool(TIME_PATTERN.match(P.ljust(4, '0'))):
             if len(P) == 4: return bool(TIME_PATTERN.match(P))
             return True # Allow partial
        self.root.bell(); return False

    def validate_internal_time_format(self, time_str_hhmm):
        """Validate internal HH:MM time format."""
        if not isinstance(time_str_hhmm, str): return False
        parts = time_str_hhmm.split(':')
        if len(parts) != 2: return False
        try: h, m = int(parts[0]), int(parts[1]); return 0 <= h <= 23 and 0 <= m <= 59
        except ValueError: return False

    def validate_time_entry_visual_hhmm(self, board_idx, channel_name, entry_type, new_value_hhmm, entry_widget):
        """Visual validation for HHMM time entries."""
        try:
            if entry_widget.winfo_exists():
                is_valid = self.validate_time_hhmm_format(new_value_hhmm)
                color = self.cached_colors['normal'] if is_valid else self.cached_colors['error']
                entry_widget.config(foreground=color)
        except tk.TclError: pass # Widget destroyed

    def set_status(self, message, is_error=False):
        """Update status bar using batched updates."""
        self.status_update_batch.append({'message': message, 'is_error': is_error})
        if not self.status_update_timer:
            try: self.status_update_timer = self.root.after(TIMINGS['status_update_batch'], self.process_status_updates)
            except tk.TclError: pass # Root destroyed

    def process_status_updates(self):
        """Process batched status updates."""
        self.status_update_timer = None
        if not self.status_update_batch: return
        latest = self.status_update_batch[-1]
        msg = latest['message']; is_err = latest['is_error']
        try:
             if self.status_bar_widget.winfo_exists():
                 status_text = f"Error: {msg}" if is_err else msg
                 self.status_var.set(status_text[:200]) # Limit status length
        except tk.TclError: pass
        except Exception as e: print(f"Error updating status bar: {e}")
        self.status_update_batch = []

    def update_channel_schedule(self, board_idx, channel_name):
        """Update schedule state when checkbox is toggled."""
        if board_idx >= len(self.boards): return
        self.channel_schedules.setdefault(board_idx, {}).setdefault(channel_name, {"on_time": "08:00", "off_time": "00:00", "enabled": False, "active": True})
        sched_info = self.channel_schedules[board_idx][channel_name]
        sched_var = self.channel_schedule_vars.get((board_idx, channel_name))
        on_entry = self.channel_time_entries.get((board_idx, channel_name, "on"))
        off_entry = self.channel_time_entries.get((board_idx, channel_name, "off"))
        if not sched_var: print(f"Error: Checkbox var missing {board_idx}-{channel_name}"); return

        try:
            is_enabled = sched_var.get()
            on_t_ui = on_entry.get() if on_entry and on_entry.winfo_exists() else sched_info["on_time"].replace(":", "")
            off_t_ui = off_entry.get() if off_entry and off_entry.winfo_exists() else sched_info["off_time"].replace(":", "")
            on_valid = self.validate_time_hhmm_format(on_t_ui); off_valid = self.validate_time_hhmm_format(off_t_ui)

            if is_enabled and (not on_valid or not off_valid):
                messagebox.showerror("Invalid Time", f"Cannot enable schedule for {channel_name} with invalid time (HHMM).")
                sched_var.set(False); is_enabled = False
                on_t_hhmm = "08:00"; off_t_hhmm = "00:00" # Reset internal on error
            elif on_valid and off_valid:
                on_t_hhmm = f"{on_t_ui[:2]}:{on_t_ui[2:]}"; off_t_hhmm = f"{off_t_ui[:2]}:{off_t_ui[2:]}"
            else: on_t_hhmm = "08:00"; off_t_hhmm = "00:00" # Reset if disabled or invalid

            sched_info["on_time"] = on_t_hhmm; sched_info["off_time"] = off_t_hhmm; sched_info["enabled"] = is_enabled
            chamber = self.boards[board_idx].chamber_number or (board_idx + 1)
            action = "enabled" if is_enabled else "disabled"
            self.set_status(f"Schedule {action} for {chamber}-{channel_name}")
            self.root.after(10, lambda idx=board_idx: self.apply_board_settings(idx))
        except tk.TclError: print(f"Warn: Widget error updating schedule {board_idx}-{channel_name}")
        except Exception as e: print(f"Error updating schedule {board_idx}-{channel_name}: {e}")

    def is_time_between(self, check_time_str, start_time_str_hhmm, end_time_str_hhmm):
        """Check if check_time (HH:MM) is between start/end (HH:MM) (exclusive end)."""
        try:
            ch, cm = map(int, check_time_str.split(':')); check_m = ch * 60 + cm
            sh, sm = map(int, start_time_str_hhmm.split(':')); start_m = sh * 60 + sm
            eh, em = map(int, end_time_str_hhmm.split(':')); end_m = eh * 60 + em
            if start_m == end_m: return True # Assume 24h if same
            if start_m < end_m: return start_m <= check_m < end_m # Normal interval
            else: return check_m >= start_m or check_m < end_m # Wraparound interval
        except: return False # Invalid format -> False

    def process_gui_queue(self):
        """Process GUI action queue."""
        processed = 0
        try:
            for _ in range(50): # Limit items per cycle
                action = self.gui_queue.get_nowait()
                processed += 1
                # --- Handle Actions ---
                if isinstance(action, StatusUpdate): self.set_status(action.message, action.is_error)
                elif isinstance(action, BoardsDetected):
                    self.background_operations.pop('scan', None) # Clear scan flag
                    self.boards = action.boards if not action.error else []
                    if action.error: messagebox.showerror("Scan Error", action.error); self.set_status(f"Scan Error: {action.error}", True)
                    self.create_board_frames() # Recreate GUI
                    if not action.error: self.set_status(f"Scan complete: Found {len(self.boards)} board(s).")
                elif isinstance(action, CommandComplete):
                    if action.board_idx >= len(self.boards): continue
                    chamber = self.boards[action.board_idx].chamber_number or (action.board_idx + 1)
                    prefix = f"Chamber {chamber}:"
                    if action.success:
                        msg = f"{prefix} "
                        if action.command_type == BoardConnection.CMD_SETALL: msg += "LEDs updated."
                        elif action.command_type == BoardConnection.CMD_FAN_SET: msg += "Fan updated."
                        else: msg += f"{action.command_type} OK."
                        if action.extra_info: msg += f" ({action.extra_info})"
                        self.set_status(msg)
                    else:
                        err_msg = f"{prefix} {action.command_type} Error - {action.message}"
                        messagebox.showerror(f"Command Error ({prefix})", f"Cmd: {action.command_type}\nError: {action.message}")
                        self.set_status(err_msg, True)
                elif isinstance(action, FileOperationComplete):
                    op = action.operation_type.capitalize()
                    if action.success:
                        if action.operation_type == 'import':
                            self.set_status(f"Import successful: {action.message}")
                            if action.data and action.data['applied_count'] > 0:
                                apply_q = f"Loaded/Applied {action.data['applied_count']} settings to interface.\nSend to boards now?"
                                if action.data.get('fan_settings_found'): apply_q += "\n(Fan settings included.)"
                                if messagebox.askyesno("Send Imported Settings", apply_q):
                                     self.apply_all_settings()
                                     if action.data.get('fan_settings_found'): self.apply_fan_settings()
                        elif action.operation_type == 'export':
                             self.set_status(f"Exported to {os.path.basename(action.message)}")
                             messagebox.showinfo("Export Successful", f"Settings exported to:\n{action.message}")
                    else: # Error
                        messagebox.showerror(f"{op} Error", f"Error: {action.message}")
                        self.set_status(f"{op} error: {action.message}", True)
                elif isinstance(action, SchedulerUpdate):
                    idx, cn, active = action.board_idx, action.channel_name, action.active
                    if idx in self.channel_schedules and cn in self.channel_schedules[idx]:
                        self.channel_schedules[idx][cn]['active'] = active
                        frame = self.channel_schedule_frames.get((idx, cn))
                        if frame:
                             try:
                                  if frame.winfo_exists():
                                       # **OPTIMIZATION: Check current style before configuring**
                                       target_style = 'ActiveSchedule.TFrame' if active else 'InactiveSchedule.TFrame'
                                       # Note: Getting current style might be complex/unreliable with ttk.
                                       # For simplicity, we still apply the config, but Tkinter/ttk might optimize internally if unchanged.
                                       # If this proves to be a bottleneck, more complex state tracking would be needed.
                                       frame.config(style=target_style)
                             except tk.TclError: pass
                # --- End Handle Actions ---
                self.gui_queue.task_done()
        except queue.Empty: pass
        except Exception as e: print(f"FATAL Error processing GUI queue: {e}"); import traceback; traceback.print_exc(); self.set_status(f"GUI Error: {e}", True)
        # Schedule next check
        try: self.root.after(self.queue_check_interval, self.process_gui_queue)
        except tk.TclError: print("Queue Check: Root window destroyed.")

    def duty_cycle_from_percentage(self, percentage):
        """Convert percentage (0-100) to duty cycle (0-4095)."""
        try: percentage = max(0, min(100, int(percentage)))
        except (ValueError, TypeError): percentage = 0
        return DUTY_CYCLE_LOOKUP.get(percentage, 0)

if __name__ == "__main__":
    root = tk.Tk()
    root.minsize(1300, 750) # Set minimum size
    app = LEDControlGUI(root)
    root.mainloop()
