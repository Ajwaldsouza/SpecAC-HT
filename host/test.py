#!/usr/bin/env python
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial
import threading
import time
import json
import queue
import os
import re
from serial.tools import list_ports
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

# Constants
MAX_BOARDS = 16
LED_CHANNELS = {
    'UV': 0,
    'FAR_RED': 1,
    'RED': 2,
    'WHITE': 3,
    'GREEN': 4,
    'BLUE': 5
}
LED_CHANNEL_NAMES = list(LED_CHANNELS.keys()) # Cache list of names
LED_COLORS = {
    'UV': "#9400D3",
    'FAR_RED': "#8B0000",
    'RED': "#FF0000",
    'WHITE': "#FFFFFF", # Use a light gray for visibility on white background
    'GREEN': "#00FF00",
    'BLUE': "#0000FF"
}

# Additional cached colors for performance
UI_COLORS = {
    'error': '#FF0000',
    'success': '#008000',
    'warning': '#FFA500',
    'info': '#0000FF',
    'normal': '#000000',
    'header_bg': '#E0E0E0',
    'active_bg': '#D0FFD0',     # Greenish background for active schedule
    'inactive_bg': '#FFD0D0',   # Reddish background for inactive schedule
    'button_bg': '#F0F0F0',
    'entry_bg': '#FFFFFF',
    'disabled_bg': '#E0E0E0',
    'schedule_frame_bg': '#F5F5F5' # Light gray for schedule frame
}

# Path to the microcontroller serial mapping file
SERIAL_MAPPING_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "microcontroller", "microcontroller_serial.txt")

# Default path for saving/loading settings (user's Documents folder)
DEFAULT_DOCUMENTS_PATH = os.path.join(os.path.expanduser("~"), "Documents")

# Cache all commonly used regular expression patterns
TIME_PATTERN = re.compile(r'^([0-1][0-9]|2[0-3]):([0-5][0-9])$')
SERIAL_MAPPING_PATTERN = re.compile(r'^(\d+):(.+)$')
CHAMBER_NUM_PATTERN = re.compile(r'chamber_(\d+)')

# Cache serial port details for faster board detection (will be initialized at runtime)
CACHED_PORT_INFO = {}

# Cache board serial numbers to avoid repeated lookups
BOARD_SERIAL_CACHE = {}

# Pre-computed duty cycle values for common percentages
DUTY_CYCLE_LOOKUP = {i: int((i / 100.0) * 4095) for i in range(101)}

# Cache common widget sizes
WIDGET_SIZES = {
    'entry_width': 5,
    'time_entry_width': 7,
    'button_width': 15,
    'small_button_width': 10,
    'label_width': 12,
    'schedule_label_width': 8 # Smaller label for schedule times
}

# Cache board layout parameters
BOARD_LAYOUT = {
    'boards_per_page': 8,
    'rows_per_page': 2,
    'cols_per_page': 4,
    'padding': 5,
    'frame_padding': 10
}

# Cache event loop timing parameters
TIMINGS = {
    'widget_update_batch': 250,   # ms between widget update batches
    'status_update_batch': 100,   # ms between status updates
    'scheduler_default': 1000,    # default scheduler check interval (ms)
    'scheduler_urgent': 100,      # urgent scheduler check interval (ms)
    'scheduler_normal': 1000,     # normal scheduler check interval (ms)
    'scheduler_relaxed': 5000     # relaxed scheduler check interval (ms)
}

# Cache common command messages
CMD_MESSAGES = {
    'scan_start': "Scanning for boards...",
    'scan_complete': "Board scan complete",
    'apply_start': "Applying settings...",
    'apply_complete': "Settings applied",
    'scheduler_start': "Scheduler started",
    'scheduler_stop': "Scheduler stopped",
    'fan_on': "Turning fans ON",
    'fan_off': "Turning fans OFF",
    'lights_on': "Turning lights ON",
    'lights_off': "Turning lights OFF",
    'import_success': "Settings imported successfully",
    'export_success': "Settings exported successfully",
    'error_prefix': "Error: "
}

# Enhanced queue types for thread-safe communication
class GUIAction:
    """Base class for GUI action messages"""
    pass

class StatusUpdate(GUIAction):
    """Message to update status bar"""
    def __init__(self, message, is_error=False):
        self.message = message
        self.is_error = is_error

class BoardsDetected(GUIAction):
    """Message indicating board detection results"""
    def __init__(self, boards, error=None):
        self.boards = boards
        self.error = error

class SettingsApplied(GUIAction):
    """Message indicating settings application result"""
    def __init__(self, board_idx, success, message, extra_info=None):
        self.board_idx = board_idx
        self.success = success
        self.message = message
        self.extra_info = extra_info # e.g., schedule status

class SchedulerUpdate(GUIAction):
    """Message to update scheduler state for a specific channel"""
    def __init__(self, board_idx, channel_name, active):
        self.board_idx = board_idx
        self.channel_name = channel_name
        self.active = active # True if channel should be ON, False if OFF

class ToggleComplete(GUIAction):
    """Message indicating toggle operation completed"""
    def __init__(self, operation_type, success, message=None, board_idx=None, state=None):
        self.operation_type = operation_type  # 'lights' or 'fans'
        self.success = success
        self.message = message
        self.board_idx = board_idx
        self.state = state # e.g., True for ON, False for OFF

class FileOperationComplete(GUIAction):
    """Message indicating file operation completion"""
    def __init__(self, operation_type, success, message, data=None):
        self.operation_type = operation_type  # 'import' or 'export'
        self.success = success
        self.message = message
        self.data = data


class BoardConnection:
    """Manages the connection to a single XIAO RP2040 board"""

    # Static properties to cache command types
    CMD_SETALL = "SETALL"
    CMD_FAN_SET = "FAN_SET"
    CMD_FAN_ON = "FAN_ON"
    CMD_FAN_OFF = "FAN_OFF"

    # Pre-computed responses for faster comparison
    RESP_OK = "OK"
    RESP_ERR_PREFIX = "ERR:"

    # Pre-computed zero duty cycle array for turning off all LEDs
    ZERO_DUTY_CYCLES = [0, 0, 0, 0, 0, 0]

    def __init__(self, port, serial_number, chamber_number=None):
        self.port = port
        self.serial_number = serial_number
        self.chamber_number = chamber_number  # Add chamber number property
        self.serial_conn = None
        self.is_connected = False
        self.last_error = ""
        self.fan_speed = 0
        self.fan_enabled = False
        self.max_retries = 3
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.pending_tasks = []
        self.command_queue = queue.Queue()  # Queue for batching commands
        self.command_processor_running = False
        self.command_processor_thread = None
        self.last_command_time = 0
        self.command_batch_timeout = 0.1  # seconds to batch commands

        # Cache connection parameters
        self.conn_params = {
            'baudrate': 115200,
            'timeout': 2,
            'write_timeout': 2
        }

    def connect(self, callback=None):
        """Establish serial connection to the board asynchronously"""
        future = self.executor.submit(self._connect_impl)
        if callback:
            future.add_done_callback(lambda f: callback(f.result()))
        self.pending_tasks.append(future)
        return future

    def _connect_impl(self):
        """Internal implementation of connect operation"""
        with self.lock:
            if self.is_connected: # Already connected
                return True
            try:
                self.serial_conn = serial.Serial(
                    port=self.port,
                    **self.conn_params
                )
                # Give device time to reset after connection
                time.sleep(2)

                # Clear any initialization messages
                if self.serial_conn.in_waiting > 0:
                    self.serial_conn.reset_input_buffer()

                self.is_connected = True
                return True
            except serial.SerialException as e:
                self.last_error = str(e)
                self.is_connected = False
                return False
            except Exception as e: # Catch other potential errors
                self.last_error = f"Unexpected error connecting: {str(e)}"
                self.is_connected = False
                return False

    def disconnect(self, callback=None):
        """Close serial connection asynchronously"""
        future = self.executor.submit(self._disconnect_impl)
        if callback:
            future.add_done_callback(lambda f: callback(f.result()))
        self.pending_tasks.append(future)
        return future

    def _disconnect_impl(self):
        """Internal implementation of disconnect operation"""
        with self.lock:
            if self.serial_conn and self.is_connected:
                try:
                    self.serial_conn.close()
                except Exception as e:
                    print(f"Error closing serial port {self.port}: {e}") # Log error
                finally:
                    self.is_connected = False
                    self.serial_conn = None # Ensure it's None after closing
            return True

    def _send_command_impl(self, duty_values):
        """Internal implementation of send_command operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl():
                    return False, f"Connection failed: {self.last_error}"

            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    # Format: "SETALL d0 d1 d2 d3 d4 d5\n"
                    command = self.CMD_SETALL
                    for val in duty_values:
                        command += f" {val}"
                    command += "\n"

                    # Clear input buffer before sending
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()

                    # Send command
                    self.serial_conn.write(command.encode('utf-8'))
                    self.serial_conn.flush() # Ensure data is sent

                    # Read response with timeout
                    start_time = time.time()
                    response = ""
                    while time.time() - start_time < 1.5:  # Increased timeout slightly
                        if self.serial_conn.in_waiting > 0:
                            try:
                                data = self.serial_conn.read(self.serial_conn.in_waiting)
                                response += data.decode('utf-8', errors='ignore') # Ignore decoding errors
                            except Exception as read_e:
                                print(f"Error reading from {self.port}: {read_e}")
                                break # Exit read loop on error

                            if self.RESP_OK in response:
                                return True, "Success"
                            elif self.RESP_ERR_PREFIX in response:
                                # Extract error message if possible
                                error_msg = response.split(self.RESP_ERR_PREFIX, 1)[-1].strip()
                                return False, f"Board Error: {error_msg}"

                        time.sleep(0.05) # Short sleep

                    # Timeout occurred
                    retry_count += 1
                    if retry_count < self.max_retries:
                        print(f"Timeout waiting for response from {self.port}. Retrying ({retry_count}/{self.max_retries})...")
                        time.sleep(0.5)  # Wait before retry
                    else:
                        self.last_error = "Timeout waiting for response after retries"
                        # Attempt to disconnect/reconnect on persistent timeout
                        self._disconnect_impl()
                        return False, self.last_error

                except serial.SerialTimeoutException as e:
                    self.last_error = f"Write timeout: {str(e)}"
                    print(f"Write timeout on {self.port}: {e}. Retrying...")
                    retry_count += 1
                    time.sleep(0.5)
                except serial.SerialException as e:
                    self.last_error = f"Serial error: {str(e)}"
                    print(f"SerialException on {self.port}: {e}. Disconnecting.")
                    self.is_connected = False # Mark as disconnected
                    return False, self.last_error
                except Exception as e:
                    self.last_error = f"Unexpected error sending command: {str(e)}"
                    print(f"Unexpected error sending command on {self.port}: {e}")
                    self.is_connected = False # Mark as disconnected on unknown error
                    return False, self.last_error

            return False, f"Max retries exceeded for {self.port}"

    def _set_fan_speed_impl(self, percentage):
        """Internal implementation of set_fan_speed operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl():
                    return False, f"Connection failed: {self.last_error}"

            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    command = f"{self.CMD_FAN_SET} {percentage}\n"

                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()

                    self.serial_conn.write(command.encode('utf-8'))
                    self.serial_conn.flush()

                    start_time = time.time()
                    response = ""
                    while time.time() - start_time < 1.5:
                        if self.serial_conn.in_waiting > 0:
                            try:
                                data = self.serial_conn.read(self.serial_conn.in_waiting)
                                response += data.decode('utf-8', errors='ignore')
                            except Exception as read_e:
                                print(f"Error reading fan response from {self.port}: {read_e}")
                                break

                            if self.RESP_OK in response:
                                self.fan_speed = percentage
                                self.fan_enabled = percentage > 0
                                return True, "Success"
                            elif self.RESP_ERR_PREFIX in response:
                                error_msg = response.split(self.RESP_ERR_PREFIX, 1)[-1].strip()
                                return False, f"Board Error: {error_msg}"

                        time.sleep(0.05)

                    retry_count += 1
                    if retry_count < self.max_retries:
                         print(f"Timeout waiting for fan response from {self.port}. Retrying ({retry_count}/{self.max_retries})...")
                         time.sleep(0.5)
                    else:
                        self.last_error = "Timeout waiting for fan response after retries"
                        self._disconnect_impl()
                        return False, self.last_error

                except serial.SerialTimeoutException as e:
                    self.last_error = f"Write timeout (fan): {str(e)}"
                    print(f"Write timeout (fan) on {self.port}: {e}. Retrying...")
                    retry_count += 1
                    time.sleep(0.5)
                except serial.SerialException as e:
                    self.last_error = f"Serial error (fan): {str(e)}"
                    print(f"SerialException (fan) on {self.port}: {e}. Disconnecting.")
                    self.is_connected = False
                    return False, self.last_error
                except Exception as e:
                    self.last_error = f"Unexpected error setting fan speed: {str(e)}"
                    print(f"Unexpected error setting fan speed on {self.port}: {e}")
                    self.is_connected = False
                    return False, self.last_error

            return False, f"Max retries exceeded for fan command on {self.port}"

    def _turn_fan_on_impl(self):
        """Internal implementation of turn_fan_on operation"""
        # Reuses set_fan_speed_impl with the current speed or a default if speed is 0
        current_speed = self.fan_speed if self.fan_speed > 0 else 50 # Use 50% if current speed is 0
        return self._set_fan_speed_impl(current_speed)

    def _turn_fan_off_impl(self):
        """Internal implementation of turn_fan_off operation"""
        # Reuses set_fan_speed_impl with 0 speed
        return self._set_fan_speed_impl(0)

    def start_command_processor(self):
        """Start the background command processor thread"""
        with self.lock: # Ensure thread-safe start
            if not self.command_processor_running:
                self.command_processor_running = True
                self.command_processor_thread = threading.Thread(
                    target=self._process_command_queue,
                    daemon=True,
                    name=f"CmdProc-{self.port}" # Give thread a name
                )
                self.command_processor_thread.start()

    def stop_command_processor(self):
        """Stop the background command processor thread"""
        with self.lock:
            if self.command_processor_running:
                self.command_processor_running = False
                # Add sentinel value to unblock the queue if waiting
                self.command_queue.put((None, None, None))
                if self.command_processor_thread and self.command_processor_thread.is_alive():
                    self.command_processor_thread.join(timeout=1.0) # Wait briefly
                self.command_processor_thread = None

    def _process_command_queue(self):
        """Process and batch commands in background thread"""
        while self.command_processor_running:
            try:
                # Wait for a command or timeout
                try:
                    cmd, args, callback = self.command_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                # Check for sentinel value to exit
                if cmd is None:
                    break

                # Batching logic (simplified: process one command at a time for now)
                # TODO: Re-implement batching if needed, carefully handling callbacks

                success = False
                message = "Command not processed"

                if cmd == "SETALL":
                    success, message = self._send_command_impl(args)
                elif cmd == "FAN_SET":
                    success, message = self._set_fan_speed_impl(args)
                # FAN_ON and FAN_OFF are handled by FAN_SET internally now

                # Call the callback if provided
                if callback:
                    try:
                        # Schedule callback in main thread if it interacts with GUI
                        # Assuming callbacks might interact with GUI, use root.after(0, ...)
                        # This requires passing the root window object or using a global queue
                        # For simplicity here, call directly, assuming callback is thread-safe
                        # or handles its own GUI interaction safely.
                        # If callbacks update GUI, they MUST use the GUI queue.
                        callback(success, message)
                    except Exception as cb_e:
                        print(f"Error in command callback for {self.port}: {cb_e}")

                # Mark task as done
                self.command_queue.task_done()

            except Exception as e:
                # Log error but continue processing
                print(f"Command processor error for {self.port}: {str(e)}")
                time.sleep(0.1) # Avoid busy-looping on error

    def send_command(self, duty_values, callback=None):
        """Queue command to update LED brightness asynchronously"""
        if not self.command_processor_running:
            self.start_command_processor()

        self.command_queue.put(("SETALL", list(duty_values), callback)) # Ensure list copy
        return True

    def set_fan_speed(self, percentage, callback=None):
        """Queue command to set the fan speed asynchronously"""
        if not self.command_processor_running:
            self.start_command_processor()

        self.command_queue.put(("FAN_SET", int(percentage), callback))
        return True

    def turn_fan_on(self, callback=None):
        """Queue command to turn the fan on asynchronously"""
        # Determine speed to turn on (use current if > 0, else default 50)
        speed_to_set = self.fan_speed if self.fan_speed > 0 else 50
        self.set_fan_speed(speed_to_set, callback)
        return True

    def turn_fan_off(self, callback=None):
        """Queue command to turn the fan off asynchronously"""
        self.set_fan_speed(0, callback)
        return True

    def cleanup(self):
        """Clean up resources when shutting down"""
        print(f"Cleaning up board connection for {self.port}")
        self.stop_command_processor()

        # Cancel any pending futures if possible
        for task in self.pending_tasks:
            if not task.done():
                task.cancel()

        # Shutdown the executor
        # Use wait=True to ensure threads finish before closing serial port
        self.executor.shutdown(wait=True)

        # Ensure serial connection is closed
        self._disconnect_impl()
        print(f"Cleanup complete for {self.port}")


class LEDControlGUI:
    """Main GUI application for controlling LED brightness with individual channel scheduling"""

    def __init__(self, root):
        self.root = root
        self.root.title("SpecAC-HT Control System")
        # Increased default size slightly for more schedule controls
        self.root.geometry("1400x900")

        # Setup thread-safe queue for worker threads to communicate with main thread
        self.gui_queue = queue.Queue()
        self.queue_check_interval = 100  # ms between queue checks

        # Flag to track active background operations
        self.background_operations = {}

        # Cache style configurations
        self.setup_styles()

        # Initialize status variable early so it's available for all methods
        self.status_var = tk.StringVar(value="Ready")

        # Pre-cache these patterns once
        self.time_pattern = TIME_PATTERN
        self.serial_mapping_pattern = SERIAL_MAPPING_PATTERN
        self.chamber_num_pattern = CHAMBER_NUM_PATTERN

        # Pre-calculate common conversion factors
        self.duty_cycle_factor = 4095 / 100.0  # For converting percentages to duty cycles

        # Cache common duty cycle values for fast lookup
        self.duty_cycle_lookup = DUTY_CYCLE_LOOKUP

        # Cache widget sizes for consistent UI
        self.widget_sizes = WIDGET_SIZES

        # Cache board layout parameters
        self.board_layout = BOARD_LAYOUT

        # Cache timing parameters
        self.timings = TIMINGS

        # Cache command messages
        self.cmd_messages = CMD_MESSAGES

        # Cache zero duty cycle array for fast access
        self.zero_duty_cycle = [0] * len(LED_CHANNELS) # Dynamically size based on channels

        self.boards = []
        self.board_frames = []
        self.led_entries = {}  # {(board_idx, channel_name): entry_widget}

        # NEW: Add direct chamber-to-board mapping for O(1) lookups
        self.chamber_to_board_idx = {}  # {chamber_number: board_idx}

        # Track master light state
        self.master_on = True
        self.saved_values = {}  # To store values when turning off

        # Track master fan state
        self.fans_on = False
        self.fan_speed_var = tk.StringVar(value="50")

        # --- Scheduling related variables ---
        # Structure: {board_idx: {channel_name: {"on_time": str, "off_time": str, "enabled": bool, "active": bool}}}
        self.channel_schedules = {}
        # Structure: {(board_idx, channel_name, "on"/"off"): entry_widget}
        self.channel_time_entries = {}
        # Structure: {(board_idx, channel_name): BooleanVar}
        self.channel_schedule_vars = {}
        # --- End Scheduling variables ---

        self.scheduler_running = False
        # Removed scheduler_thread, using root.after now
        self.changed_boards = set()  # Track which boards changed (settings or schedule state)

        # Optimization: Add cache for last schedule check state per channel
        # Structure: {(board_idx, channel_name): {"active": bool, "last_check": timestamp}}
        self.last_schedule_state = {}

        # Optimization: Set default scheduler check interval (in milliseconds)
        self.scheduler_check_interval = self.timings['scheduler_default']
        self.adaptive_check_timer = None  # Store reference to scheduled timer

        # NEW: Add widget update batching (commented out for now, can re-enable if needed)
        # self.widget_update_queue = queue.Queue()
        # self.update_batch_timer = None
        # self.update_batch_interval = self.timings['widget_update_batch']
        # self.is_updating_widgets = False

        self.status_update_batch = []  # List to batch status updates
        self.status_update_timer = None


        # Cache fonts to avoid creating new font objects repeatedly
        self.create_font_cache()

        # Cache colors for better performance
        self.create_color_cache()

        # Cache chamber mapping
        self.chamber_mapping = {}  # {serial_number: chamber_number}
        self.reverse_chamber_mapping = {}  # {chamber_number: serial_number}

        # NEW: Add lookup to find board by serial number efficiently
        self.serial_to_board_idx = {}  # {serial_number: board_idx}

        self.load_chamber_mapping()

        # Pagination variables
        self.current_page = 0
        self.boards_per_page = self.board_layout['boards_per_page']

        # Cache command batch sizes for higher performance during bulk operations
        self.max_concurrent_commands = 5 # Limit concurrent commands per board

        # NEW: Cache calculated duty cycles for percentages
        self.duty_cycle_cache = {i: int((i / 100.0) * 4095) for i in range(101)}

        # Create and cache validation commands
        self.setup_validation_commands()

        # Create the GUI components
        self.create_gui()

        # Cache board serial detection
        self.initialize_port_cache()

        # Start the scheduler using after() instead of a thread
        self.start_scheduler() # Will start checking immediately

        # Start the widget update processor (if enabled)
        # self.process_widget_updates()

        # Start periodic queue processing
        self.process_gui_queue()

    def setup_styles(self):
        """Setup and cache TTK styles"""
        self.style = ttk.Style()
        self.style.theme_use('clam')  # Modern theme

        # Create and cache common styles
        self.style.configure('Header.TLabel', font=('Helvetica', 16, 'bold'))
        self.style.configure('Subheader.TLabel', font=('Helvetica', 12, 'bold'))
        self.style.configure('Success.TLabel', foreground='green')
        self.style.configure('Error.TLabel', foreground='red')
        self.style.configure('Warning.TLabel', foreground='orange')

        # Button styles
        self.style.configure('Primary.TButton', background='#4CAF50')
        self.style.configure('Secondary.TButton', background='#2196F3')
        self.style.configure('Danger.TButton', background='#F44336')

        # Style for schedule frames
        self.style.configure('Schedule.TFrame', background=self.cached_colors['schedule_frame_bg'])
        self.style.configure('ActiveSchedule.TFrame', background=self.cached_colors['active_bg'])
        self.style.configure('InactiveSchedule.TFrame', background=self.cached_colors['inactive_bg'])


    def create_font_cache(self):
        """Create cached font configurations"""
        self.cached_fonts = {
            'header': ('Helvetica', 16, 'bold'),
            'subheader': ('Helvetica', 12, 'bold'),
            'subheader_small': ('Helvetica', 10, 'bold'),
            'normal': ('Helvetica', 10, 'normal'),
            'small': ('Helvetica', 8, 'normal'),
            'monospace': ('Courier', 10, 'normal'),
            'button': ('Helvetica', 10, 'normal'),
            'status': ('Helvetica', 9, 'normal'),
            'schedule_label': ('Helvetica', 8, 'normal'), # Smaller font for schedule labels
            'schedule_entry': ('Helvetica', 8, 'normal')  # Smaller font for schedule entries
        }

    def create_color_cache(self):
        """Cache colors for better performance"""
        # Use the pre-defined UI_COLORS dictionary
        self.cached_colors = UI_COLORS

    def setup_validation_commands(self):
        """Set up and cache validation commands"""
        self.validation_commands = {
            'percentage': (self.root.register(self.validate_percentage), '%P'),
            'time': (self.root.register(self.validate_time_format), '%P') # Use for direct validation if needed
        }

    def initialize_port_cache(self):
        """Initialize the serial port cache for faster board detection"""
        global CACHED_PORT_INFO, BOARD_SERIAL_CACHE

        # Reset the caches
        CACHED_PORT_INFO = {}
        BOARD_SERIAL_CACHE = {}

        # Pre-cache all XIAO RP2040 boards for faster future lookups
        try:
            for port_info in list_ports.grep('VID:PID=2E8A:0005'):
                if port_info.serial_number: # Only cache if serial number is available
                    CACHED_PORT_INFO[port_info.device] = {
                        'serial_number': port_info.serial_number,
                        'description': port_info.description,
                        'hwid': port_info.hwid
                    }
                    BOARD_SERIAL_CACHE[port_info.serial_number] = port_info.device
        except Exception as e:
            self.set_status(f"Error initializing port cache: {e}", is_error=True)

    def load_chamber_mapping(self):
        """Load the chamber to serial number mapping from the text file"""
        self.chamber_mapping = {}
        self.reverse_chamber_mapping = {}

        try:
            if os.path.exists(SERIAL_MAPPING_FILE):
                with open(SERIAL_MAPPING_FILE, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith('#'): # Ignore empty lines and comments
                            continue

                        # Parse the chamber:serial format using cached regex
                        match = self.serial_mapping_pattern.match(line)
                        if match:
                            try:
                                chamber_num = int(match.group(1))
                                serial_num = match.group(2).strip()

                                # Store the mapping both ways for easy lookup
                                self.chamber_mapping[serial_num] = chamber_num
                                self.reverse_chamber_mapping[chamber_num] = serial_num
                            except ValueError:
                                print(f"Warning: Invalid chamber number format in mapping file: {line}")
                            except Exception as parse_e:
                                print(f"Warning: Error parsing mapping file line: {line} - {parse_e}")

                self.set_status(f"Loaded chamber mapping for {len(self.chamber_mapping)} chambers")
            else:
                self.set_status(f"Chamber mapping file not found: {SERIAL_MAPPING_FILE}", is_error=True)
                messagebox.showwarning("Mapping File Missing",
                                       f"The chamber mapping file was not found at:\n{SERIAL_MAPPING_FILE}\n\n"
                                       "Chamber numbers may not be assigned correctly.")
        except Exception as e:
            self.set_status(f"Error loading chamber mapping: {str(e)}", is_error=True)
            print(f"Error loading chamber mapping: {str(e)}")

    def create_gui(self):
        """Create the main GUI layout"""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # --- Top Control Frame ---
        top_control_frame = ttk.Frame(main_frame)
        top_control_frame.grid(column=0, row=0, sticky=(tk.W, tk.E), pady=(0, 10))
        top_control_frame.columnconfigure(1, weight=1) # Allow middle section to expand

        # Left side: Title and Master Lights
        left_controls = ttk.Frame(top_control_frame)
        left_controls.grid(column=0, row=0, sticky=tk.W)
        ttk.Label(left_controls, text="LED Control System", font=self.cached_fonts['header']).pack(side=tk.LEFT, padx=(0, 20))
        self.master_button_var = tk.StringVar(value="All Lights OFF")
        master_button = ttk.Button(
            left_controls,
            textvariable=self.master_button_var,
            command=self.toggle_all_lights,
            width=self.widget_sizes['button_width']
        )
        master_button.pack(side=tk.LEFT)

        # Middle: Scheduler Control
        middle_controls = ttk.Frame(top_control_frame)
        middle_controls.grid(column=1, row=0, sticky=tk.EW) # Expand horizontally
        self.scheduler_button_var = tk.StringVar(value="Start Scheduler")
        scheduler_button = ttk.Button(
            middle_controls,
            textvariable=self.scheduler_button_var,
            command=self.toggle_scheduler,
            width=self.widget_sizes['button_width']
        )
        # Center the scheduler button
        scheduler_button.pack(anchor=tk.CENTER)


        # Right side: Scan and Apply
        right_controls = ttk.Frame(top_control_frame)
        right_controls.grid(column=2, row=0, sticky=tk.E)
        ttk.Button(right_controls, text="Scan for Boards", command=self.scan_boards).pack(side=tk.LEFT, padx=5)
        ttk.Button(right_controls, text="Apply All Settings", command=self.apply_all_settings).pack(side=tk.LEFT, padx=5)


        # --- Boards Display Area ---
        boards_area_frame = ttk.Frame(main_frame)
        boards_area_frame.grid(column=0, row=1, sticky=(tk.N, tk.W, tk.E, tk.S))
        main_frame.rowconfigure(1, weight=1) # Allow boards area to expand vertically
        main_frame.columnconfigure(0, weight=1) # Allow boards area to expand horizontally

        # Navigation frame (Top of boards area)
        nav_frame = ttk.Frame(boards_area_frame)
        nav_frame.pack(fill=tk.X, pady=5)
        nav_frame.columnconfigure(1, weight=1) # Allow page label to center/expand

        self.prev_button = ttk.Button(nav_frame, text="◀ Prev Page", command=self.prev_page, width=15, state=tk.DISABLED)
        self.prev_button.grid(column=0, row=0, padx=10)

        self.page_label = ttk.Label(nav_frame, text="Chambers 1-8", font=self.cached_fonts['subheader'], anchor=tk.CENTER)
        self.page_label.grid(column=1, row=0, sticky=tk.EW) # Center label

        self.next_button = ttk.Button(nav_frame, text="Next Page ▶", command=self.next_page, width=15, state=tk.DISABLED)
        self.next_button.grid(column=2, row=0, padx=10)

        # Container frame for stacked pages
        self.page_container = ttk.Frame(boards_area_frame)
        self.page_container.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)

        # Dictionary to store page frames
        self.page_frames = {}

        # Create page frames (assuming max 16 boards for 2 pages)
        num_pages = (MAX_BOARDS + self.boards_per_page - 1) // self.boards_per_page
        for page_id in range(num_pages):
            page_frame = ttk.Frame(self.page_container)
            page_frame.place(x=0, y=0, relwidth=1, relheight=1) # Overlap frames

            # Configure grid for this page (rows/cols defined in BOARD_LAYOUT)
            for i in range(self.board_layout['cols_per_page']):
                page_frame.columnconfigure(i, weight=1, minsize=150) # Ensure minimum column width
            for i in range(self.board_layout['rows_per_page']):
                page_frame.rowconfigure(i, weight=1, minsize=200) # Ensure minimum row height

            self.page_frames[page_id] = page_frame

        # Initially raise page 0
        if 0 in self.page_frames:
            self.page_frames[0].tkraise()

        # --- Fan Control Frame ---
        fan_frame = ttk.LabelFrame(main_frame, text="Fan Controls")
        fan_frame.grid(column=0, row=2, sticky=(tk.W, tk.E), pady=10, padx=10)

        self.fan_button_var = tk.StringVar(value="Turn Fans ON")
        fan_button = ttk.Button(
            fan_frame,
            textvariable=self.fan_button_var,
            command=self.toggle_all_fans,
            width=self.widget_sizes['button_width']
        )
        fan_button.grid(column=0, row=0, padx=10, pady=5)

        ttk.Label(fan_frame, text="Fan Speed:").grid(column=1, row=0, padx=(20, 5), pady=5)
        fan_speed_entry = ttk.Entry(
            fan_frame,
            width=self.widget_sizes['entry_width'],
            textvariable=self.fan_speed_var,
            validate='key',
            validatecommand=self.validation_commands['percentage']
        )
        fan_speed_entry.grid(column=2, row=0, padx=5, pady=5)
        ttk.Label(fan_frame, text="%").grid(column=3, row=0, padx=(0, 5), pady=5)

        ttk.Button(fan_frame, text="Apply Fan Settings", command=self.apply_fan_settings).grid(column=4, row=0, padx=10, pady=5)

        # --- Bottom Frame (Import/Export) ---
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.grid(column=0, row=3, sticky=(tk.W, tk.E), pady=5, padx=10)
        bottom_frame.columnconfigure(0, weight=1) # Push buttons to sides
        bottom_frame.columnconfigure(1, weight=1)

        ttk.Button(bottom_frame, text="Export Settings", command=self.export_settings).grid(column=0, row=0, sticky=tk.W, padx=5)
        ttk.Button(bottom_frame, text="Import Settings", command=self.import_settings).grid(column=1, row=0, sticky=tk.E, padx=5)

        # --- Status Bar ---
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W, font=self.cached_fonts['status'])
        status_bar.grid(column=0, row=4, sticky=(tk.W, tk.E))

        # Add window close handler for cleanup
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # Initial scan
        self.scan_boards()

    def on_closing(self):
        """Clean up resources and close the application"""
        print("Closing application...")
        # Cancel any scheduled after() calls
        if self.adaptive_check_timer:
            print("Cancelling scheduler timer...")
            self.root.after_cancel(self.adaptive_check_timer)
            self.adaptive_check_timer = None

        # Cancel widget update batch timer (if used)
        # if self.update_batch_timer:
        #     self.root.after_cancel(self.update_batch_timer)

        # Cancel status update timer
        if self.status_update_timer:
            print("Cancelling status update timer...")
            self.root.after_cancel(self.status_update_timer)
            self.status_update_timer = None

        # Stop the scheduler explicitly
        self.scheduler_running = False
        print("Scheduler stopped flag set.")

        # Clean up board connections (includes stopping command processors)
        print(f"Cleaning up {len(self.boards)} board connections...")
        # Use a separate thread for cleanup to avoid blocking GUI? No, cleanup should be synchronous.
        cleanup_threads = []
        for i, board in enumerate(self.boards):
            print(f"Initiating cleanup for board {i} (Port: {board.port})...")
            # Run cleanup in separate threads to parallelize disconnect/shutdown
            thread = threading.Thread(target=board.cleanup, name=f"Cleanup-{board.port}")
            cleanup_threads.append(thread)
            thread.start()

        # Wait for all cleanup threads to finish
        for thread in cleanup_threads:
            thread.join(timeout=5.0) # Add timeout
            if thread.is_alive():
                 print(f"Warning: Cleanup thread {thread.name} did not finish in time.")


        print("Board cleanup process finished.")

        # Destroy the main window
        print("Destroying root window...")
        self.root.destroy()
        print("Application closed.")

    def next_page(self):
        """Navigate to the next page of chambers"""
        num_pages = (len(self.boards) + self.boards_per_page - 1) // self.boards_per_page
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
        if not self.boards: # No boards, disable navigation
             self.page_label.config(text="No Boards Found")
             self.prev_button.config(state=tk.DISABLED)
             self.next_button.config(state=tk.DISABLED)
             if 0 in self.page_frames:
                 self.page_frames[0].tkraise() # Show empty page 0
             return

        num_pages = (len(self.boards) + self.boards_per_page - 1) // self.boards_per_page
        current_page_idx = self.current_page

        # Raise the correct page frame
        if current_page_idx in self.page_frames:
            self.page_frames[current_page_idx].tkraise()
        else:
             print(f"Warning: Page frame for index {current_page_idx} not found.")
             # Optionally create the frame dynamically here if needed


        # Update page label
        start_chamber_idx = current_page_idx * self.boards_per_page
        end_chamber_idx = min(start_chamber_idx + self.boards_per_page, len(self.boards))
        # Get actual chamber numbers if available, otherwise use index+1
        try:
            start_num = self.boards[start_chamber_idx].chamber_number or (start_chamber_idx + 1)
            end_num = self.boards[end_chamber_idx - 1].chamber_number or end_chamber_idx
            self.page_label.config(text=f"Chambers {start_num}-{end_num}")
        except IndexError:
             self.page_label.config(text=f"Page {current_page_idx + 1}") # Fallback


        # Update navigation button states
        self.prev_button.config(state=tk.NORMAL if current_page_idx > 0 else tk.DISABLED)
        self.next_button.config(state=tk.NORMAL if current_page_idx < num_pages - 1 else tk.DISABLED)

        # Update status message (optional)
        # self.set_status(f"Displaying chambers {start_num}-{end_num}")

    def create_board_frames(self):
        """Create frames for each detected board, sorted by chamber number"""
        # --- Clear existing elements ---
        # Destroy old board frames
        for frame in self.board_frames:
            frame.destroy()
        self.board_frames = []

        # Clear widget dictionaries
        self.led_entries.clear()
        self.channel_time_entries.clear()
        self.channel_schedule_vars.clear()

        # Clear lookup dictionaries
        self.chamber_to_board_idx.clear()
        self.serial_to_board_idx.clear()

        # Clear schedule data (will be repopulated or loaded)
        self.channel_schedules.clear()
        self.last_schedule_state.clear()
        # --- End Clearing ---

        if not self.boards:
            print("No boards detected to create frames for.")
            self.update_page_display() # Update nav state for no boards
            return

        # Sort boards by chamber number (handle None chamber numbers)
        self.boards.sort(key=lambda b: b.chamber_number if b.chamber_number is not None else float('inf'))

        # --- Create Frames for Boards ---
        for i, board in enumerate(self.boards):
            chamber_number = board.chamber_number
            serial_number = board.serial_number

            # Add to lookup dictionaries
            if chamber_number is not None:
                self.chamber_to_board_idx[chamber_number] = i
            if serial_number is not None:
                self.serial_to_board_idx[serial_number] = i

            # Determine page and position
            page_id = i // self.boards_per_page
            if page_id not in self.page_frames:
                 print(f"Error: Page frame {page_id} does not exist for board {i}.")
                 continue # Skip board if page frame is missing

            page_frame = self.page_frames[page_id]
            row = (i % self.boards_per_page) // self.board_layout['cols_per_page']
            col = (i % self.boards_per_page) % self.board_layout['cols_per_page']

            # --- Create Main Board Frame ---
            frame_text = f"Chamber {chamber_number}" if chamber_number else f"Board {i+1} (SN: {serial_number or 'N/A'})"
            board_frame = ttk.LabelFrame(page_frame, text=frame_text, padding=self.board_layout['frame_padding'])
            board_frame.grid(row=row, column=col, padx=self.board_layout['padding'], pady=self.board_layout['padding'], sticky=(tk.N, tk.W, tk.E, tk.S))
            self.board_frames.append(board_frame) # Add frame to list

            # Configure internal grid for the board frame (e.g., 1 column)
            board_frame.columnconfigure(0, weight=1)

            # --- Create LED Control Sections (Iterate through channels) ---
            for led_row, channel_name in enumerate(LED_CHANNEL_NAMES):
                channel_idx = LED_CHANNELS[channel_name]

                # Frame for a single LED channel's controls
                channel_frame = ttk.Frame(board_frame, padding=(5, 2))
                channel_frame.grid(row=led_row, column=0, sticky=(tk.W, tk.E), pady=1)
                # Configure columns within the channel frame
                channel_frame.columnconfigure(1, weight=0) # Color
                channel_frame.columnconfigure(2, weight=0) # Name
                channel_frame.columnconfigure(3, weight=0) # Intensity Entry
                channel_frame.columnconfigure(4, weight=0) # % Label
                channel_frame.columnconfigure(5, weight=1) # Spacer
                channel_frame.columnconfigure(6, weight=0) # Schedule Frame

                # 1. Color Indicator
                color_frame = ttk.Frame(channel_frame, width=15, height=15, relief=tk.SUNKEN, borderwidth=1)
                color_frame.grid(column=1, row=0, padx=(0, 5), sticky=tk.W)
                color_label = tk.Label(color_frame, bg=LED_COLORS.get(channel_name, "#CCCCCC"), width=2, height=1)
                color_label.pack(fill=tk.BOTH, expand=True)

                # 2. Channel Name Label
                ttk.Label(channel_frame, text=f"{channel_name}:", width=8, anchor=tk.W).grid(column=2, row=0, sticky=tk.W)

                # 3. Intensity Entry
                value_var = tk.StringVar(value="0")
                entry = ttk.Entry(
                    channel_frame,
                    width=self.widget_sizes['entry_width'],
                    textvariable=value_var,
                    validate='key',
                    validatecommand=self.validation_commands['percentage'],
                    font=self.cached_fonts['normal']
                )
                entry.grid(column=3, row=0, sticky=tk.W, padx=2)
                self.led_entries[(i, channel_name)] = entry

                # 4. Percentage Label
                ttk.Label(channel_frame, text="%", font=self.cached_fonts['small']).grid(column=4, row=0, sticky=tk.W, padx=(0, 10))

                # --- 5. Scheduling Section (per channel) ---
                schedule_outer_frame = ttk.Frame(channel_frame, style='Schedule.TFrame', borderwidth=1, relief="groove")
                schedule_outer_frame.grid(column=6, row=0, sticky=tk.E, padx=(5,0))
                self.channel_schedules.setdefault(i, {})[channel_name] = {
                     "on_time": "08:00",
                     "off_time": "00:00",
                     "enabled": False,
                     "active": True # Default to active (ON) unless schedule dictates otherwise
                }


                # ON Time
                ttk.Label(schedule_outer_frame, text="On:", font=self.cached_fonts['schedule_label']).grid(column=0, row=0, padx=(5, 2), pady=1, sticky=tk.W)
                on_time_var = tk.StringVar(value=self.channel_schedules[i][channel_name]['on_time'])
                on_time_entry = ttk.Entry(
                    schedule_outer_frame,
                    width=self.widget_sizes['time_entry_width'],
                    textvariable=on_time_var,
                    font=self.cached_fonts['schedule_entry']
                    # Add validation trace later if needed, or validate on enable/apply
                )
                on_time_entry.grid(column=1, row=0, padx=(0, 5), pady=1)
                self.channel_time_entries[(i, channel_name, "on")] = on_time_entry
                # Add trace for validation feedback
                on_time_var.trace_add("write", lambda *args, b=i, c=channel_name, v=on_time_var, e=on_time_entry:
                                      self.validate_time_entry_visual(b, c, "on", v.get(), e))


                # OFF Time
                ttk.Label(schedule_outer_frame, text="Off:", font=self.cached_fonts['schedule_label']).grid(column=0, row=1, padx=(5, 2), pady=1, sticky=tk.W)
                off_time_var = tk.StringVar(value=self.channel_schedules[i][channel_name]['off_time'])
                off_time_entry = ttk.Entry(
                    schedule_outer_frame,
                    width=self.widget_sizes['time_entry_width'],
                    textvariable=off_time_var,
                    font=self.cached_fonts['schedule_entry']
                )
                off_time_entry.grid(column=1, row=1, padx=(0, 5), pady=1)
                self.channel_time_entries[(i, channel_name, "off")] = off_time_entry
                 # Add trace for validation feedback
                off_time_var.trace_add("write", lambda *args, b=i, c=channel_name, v=off_time_var, e=off_time_entry:
                                      self.validate_time_entry_visual(b, c, "off", v.get(), e))


                # Enable Checkbox
                schedule_var = tk.BooleanVar(value=self.channel_schedules[i][channel_name]['enabled'])
                schedule_check = ttk.Checkbutton(
                    schedule_outer_frame,
                    text="En", # Short text
                    variable=schedule_var,
                    # Pass board index AND channel name to the command
                    command=lambda b_idx=i, c_name=channel_name: self.update_channel_schedule(b_idx, c_name)
                )
                # Place checkbox spanning both rows, centered vertically? Or just in one row? Let's try row 0, col 2
                schedule_check.grid(column=2, row=0, rowspan=2, padx=5, pady=1, sticky=tk.W)
                self.channel_schedule_vars[(i, channel_name)] = schedule_var

            # --- Individual Apply Button (per board) ---
            apply_button = ttk.Button(
                board_frame,
                text="Apply Chamber Settings",
                command=lambda b_idx=i: self.apply_board_settings(b_idx)
            )
            # Place below all channel frames
            apply_button.grid(row=len(LED_CHANNEL_NAMES), column=0, pady=(10, 5), sticky=(tk.W, tk.E))

        # --- Finalize GUI Update ---
        # Update the display to show the first page
        self.current_page = 0
        self.update_page_display()
        print("Board frames created.")


    def toggle_all_lights(self):
        """Toggle all lights on or off on all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control.")
            return

        # Determine target state based on current master state
        target_state_on = not self.master_on # If master is ON, target is OFF, vice versa

        # Update master state immediately
        self.master_on = target_state_on

        # Update button text
        self.master_button_var.set("All Lights OFF" if self.master_on else "All Lights ON")

        # Update status
        status_msg = "Turning all lights ON..." if self.master_on else "Turning all lights OFF (preserving settings)..."
        self.set_status(status_msg)

        # If turning ON, apply current settings (which might be affected by schedules)
        if self.master_on:
            # We need to apply settings considering schedules
            self.apply_all_settings()
        else:
            # If turning OFF, save current UI values and send zeros directly
            # Save UI values first (in case they are needed later)
            self.saved_values.clear() # Clear previous saved values
            for board_idx in range(len(self.boards)):
                self.saved_values[board_idx] = {}
                for channel_name in LED_CHANNEL_NAMES:
                    key = (board_idx, channel_name)
                    if key in self.led_entries:
                        try:
                            self.saved_values[board_idx][channel_name] = self.led_entries[key].get()
                        except (ValueError, KeyError, tk.TclError): # Catch potential errors getting value
                            self.saved_values[board_idx][channel_name] = "0"

            # Send zeros to all boards
            num_boards = len(self.boards)
            completion_counter = {'count': 0}
            lock = threading.Lock()

            def off_callback(success, msg, idx=None):
                with lock:
                    completion_counter['count'] += 1
                    if completion_counter['count'] == num_boards:
                        # Use gui_queue for final status update from background thread
                        self.gui_queue.put(StatusUpdate("All lights turned OFF (settings preserved)"))

            for board_idx, board in enumerate(self.boards):
                 # Send command with all zeros directly
                 board.send_command(self.zero_duty_cycle, callback=lambda s, m, i=board_idx: off_callback(s, m, i))

    # Removed on_toggle_lights_complete as callback logic is handled differently now

    def toggle_scheduler(self):
        """Enable or disable the scheduler"""
        if self.scheduler_running:
            self.scheduler_running = False
            self.scheduler_button_var.set("Start Scheduler")
            self.set_status("Scheduler stopped")
            # Cancel any pending scheduled checks
            if self.adaptive_check_timer:
                self.root.after_cancel(self.adaptive_check_timer)
                self.adaptive_check_timer = None
            print("Scheduler stopped.")
        else:
            self.scheduler_running = True
            self.scheduler_button_var.set("Stop Scheduler")
            self.set_status("Scheduler started")
            print("Scheduler started.")
            # Start the first check immediately
            self.schedule_check()

    def start_scheduler(self):
        """Start the scheduler using Tkinter's after() method"""
        if not self.scheduler_running: # Only start if not already running
            self.toggle_scheduler()

    def schedule_check(self):
        """Periodic scheduler check using after() instead of a continuous thread"""
        if not self.scheduler_running:
            # print("Scheduler check skipped (not running).")
            return

        # print(f"Running schedule check at {datetime.now()}")

        # --- Run check logic in background thread ---
        # This prevents blocking the GUI during potentially longer checks
        threading.Thread(
            target=self._schedule_check_worker,
            daemon=True,
            name="SchedulerCheckWorker"
        ).start()

        # --- Schedule the next check ---
        # The worker thread will calculate the adaptive interval and update self.scheduler_check_interval
        # Schedule the next call using the potentially updated interval
        # Add a small buffer to ensure the worker has time to potentially update the interval
        next_check_delay = max(50, self.scheduler_check_interval) # Ensure minimum delay
        # print(f"Scheduling next check in {next_check_delay} ms")
        if self.adaptive_check_timer: # Cancel previous timer if exists
             self.root.after_cancel(self.adaptive_check_timer)
        self.adaptive_check_timer = self.root.after(next_check_delay, self.schedule_check)


    def _schedule_check_worker(self):
        """Background worker thread for schedule checking"""
        current_datetime = datetime.now()
        current_time_str = current_datetime.strftime("%H:%M")
        min_time_diff = float('inf')  # Track time to next scheduled event in minutes
        boards_needing_update = set() # Track boards where at least one channel changed state

        # Iterate through all boards and their channels with enabled schedules
        for board_idx, channels in self.channel_schedules.items():
            if board_idx >= len(self.boards): continue # Skip if board index is out of bounds

            for channel_name, schedule_info in channels.items():
                if schedule_info.get("enabled", False):
                    on_time = schedule_info.get("on_time", "")
                    off_time = schedule_info.get("off_time", "")

                    # Validate times before proceeding
                    if not self.validate_time_format(on_time) or not self.validate_time_format(off_time):
                        # Log error once per invalid schedule? Or just skip? Skip for now.
                        continue

                    # --- Calculate time differences and active state ---
                    try:
                        on_hour, on_minute = map(int, on_time.split(':'))
                        off_hour, off_minute = map(int, off_time.split(':'))

                        current_minutes = current_datetime.hour * 60 + current_datetime.minute
                        on_minutes = on_hour * 60 + on_minute
                        off_minutes = off_hour * 60 + off_minute

                        # Calculate minutes until next on/off event (handling day wraparound)
                        mins_until_on = (on_minutes - current_minutes + (24 * 60)) % (24 * 60)
                        mins_until_off = (off_minutes - current_minutes + (24 * 60)) % (24 * 60)

                        # Update minimum time difference for adaptive scheduling
                        min_time_diff = min(min_time_diff, mins_until_on, mins_until_off)

                        # Determine if channel should be active now
                        should_be_active = self.is_time_between(current_time_str, on_time, off_time)

                        # Get previous state from cache
                        cache_key = (board_idx, channel_name)
                        prev_state = self.last_schedule_state.get(cache_key, {}).get("active", None)

                        # --- Check if state changed ---
                        if prev_state is None or prev_state != should_be_active:
                            # State changed! Update cache and queue GUI update
                            if cache_key not in self.last_schedule_state:
                                self.last_schedule_state[cache_key] = {}
                            self.last_schedule_state[cache_key]["active"] = should_be_active
                            self.last_schedule_state[cache_key]["last_check"] = current_datetime

                            # Queue update for the main thread
                            self.gui_queue.put(SchedulerUpdate(board_idx, channel_name, should_be_active))
                            boards_needing_update.add(board_idx) # Mark board for potential re-apply

                            # Log the change (optional, can be verbose)
                            # chamber_num = self.boards[board_idx].chamber_number or (board_idx + 1)
                            # action = "ON" if should_be_active else "OFF"
                            # print(f"Scheduler: Chamber {chamber_num}, Channel {channel_name} -> {action}")

                    except Exception as e:
                        print(f"Error processing schedule for board {board_idx}, channel {channel_name}: {str(e)}")


        # --- Apply settings to boards where state changed ---
        # Queue the application in the main thread to avoid direct hardware calls here
        if boards_needing_update:
            # print(f"Scheduler detected changes, queuing updates for boards: {boards_needing_update}")
            # Use root.after to ensure it runs in the main thread after current event processing
            self.root.after(0, lambda boards=list(boards_needing_update): self.apply_settings_to_multiple_boards(boards))


        # --- Calculate adaptive timer interval and update in main thread ---
        adaptive_interval_ms = self.calculate_adaptive_interval(min_time_diff)
        # Update the interval variable directly (should be safe as only this thread writes)
        # Use root.after(0, ...) if strict thread safety for the variable is needed
        self.scheduler_check_interval = adaptive_interval_ms
        # print(f"Adaptive interval calculated: {adaptive_interval_ms} ms (min_diff: {min_time_diff} mins)")

    def calculate_adaptive_interval(self, min_time_diff_minutes):
        """Calculate adaptive timer interval based on time to next event"""
        # Use cached timing values for better performance
        if min_time_diff_minutes == float('inf'):
            # No active schedules, check infrequently
            return self.timings['scheduler_relaxed'] * 2 # e.g., 10 seconds

        # Check more frequently when close to a scheduled event
        if min_time_diff_minutes <= 1:  # Within 1 minute
            return self.timings['scheduler_urgent']  # e.g., 100ms
        elif min_time_diff_minutes <= 5:  # Within 5 minutes
            return self.timings['scheduler_normal'] // 2 # e.g., 500ms
        elif min_time_diff_minutes <= 15: # Within 15 minutes
             return self.timings['scheduler_normal'] # e.g., 1000ms
        else:
            # If next event is far, check less frequently
            return self.timings['scheduler_relaxed']  # e.g., 5000ms


    # Removed apply_changed_boards - replaced by direct calls via SchedulerUpdate processing
    # Removed send_zeros_to_board - logic integrated into apply_board_settings

    def scan_boards(self):
        """Detect and initialize connections to XIAO RP2040 boards"""
        # Clear previous boards and GUI elements safely
        print("Starting board scan...")
        self.set_status(self.cmd_messages['scan_start'])

        # --- Disconnect existing boards first ---
        disconnect_threads = []
        for i, board in enumerate(self.boards):
             print(f"Disconnecting existing board {i} ({board.port})...")
             # Use cleanup which handles stopping processor and closing port
             thread = threading.Thread(target=board.cleanup, name=f"Disconnect-{board.port}")
             disconnect_threads.append(thread)
             thread.start()

        # Wait for disconnections to complete
        for thread in disconnect_threads:
             thread.join(timeout=3.0) # Timeout for disconnection
             if thread.is_alive():
                  print(f"Warning: Disconnect thread {thread.name} timed out.")

        self.boards = [] # Clear the list after ensuring cleanup attempted

        # --- Clear GUI (ensure this runs in main thread if not already) ---
        # Assuming scan_boards is called from main thread or uses queue for GUI updates
        for frame in self.board_frames:
            frame.destroy()
        self.board_frames = []
        self.led_entries.clear()
        self.channel_time_entries.clear()
        self.channel_schedule_vars.clear()
        self.chamber_to_board_idx.clear()
        self.serial_to_board_idx.clear()
        self.channel_schedules.clear()
        self.last_schedule_state.clear()
        print("Cleared existing board data and GUI frames.")


        # Reset master button state (if applicable)
        self.master_on = True # Default to ON after scan
        self.master_button_var.set("All Lights OFF")
        self.saved_values = {}

        # --- Start background thread for scanning ---
        if 'scan' in self.background_operations and self.background_operations['scan']:
             print("Scan already in progress.")
             return # Avoid multiple concurrent scans

        self.background_operations['scan'] = True
        threading.Thread(
            target=self._scan_boards_worker,
            daemon=True,
            name="BoardScanWorker"
        ).start()

    def _scan_boards_worker(self):
        """Background worker thread for scanning boards"""
        detected_boards_info = []
        error_msg = None
        try:
            # Make sure chamber mapping is loaded (or reloaded)
            # This might be better done once at startup unless mapping can change
            # self.load_chamber_mapping() # Assuming load_chamber_mapping is thread-safe or called from main thread

            print("Detecting XIAO boards...")
            # Detect connected boards using cached info or fresh scan
            detected_boards_info = self.detect_xiao_boards() # Returns list of [port, serial, chamber]

            if not detected_boards_info:
                print("No boards found.")
                self.gui_queue.put(BoardsDetected([])) # Send empty list
            else:
                print(f"Found {len(detected_boards_info)} potential boards. Creating connections...")
                # Create board connections (without connecting yet)
                boards = []
                for port, serial_number, chamber_number in detected_boards_info:
                    # Basic validation
                    if not port or not serial_number:
                         print(f"Warning: Skipping board with incomplete info (Port: {port}, SN: {serial_number})")
                         continue
                    boards.append(BoardConnection(port, serial_number, chamber_number))

                print(f"Created {len(boards)} BoardConnection objects.")
                # Send result (list of BoardConnection objects) to main thread
                self.gui_queue.put(BoardsDetected(boards))

        except Exception as e:
            error_msg = f"Error during board scan: {str(e)}"
            print(error_msg)
            self.gui_queue.put(BoardsDetected([], error=error_msg)) # Send error
        finally:
            # Clear operation flag
            self.background_operations['scan'] = False
            print("Board scan worker finished.")


    def detect_xiao_boards(self):
        """Detect connected XIAO RP2040 boards and assign chamber numbers"""
        results = []
        print("Running board detection...")

        # Refresh port list
        try:
            current_ports = list_ports.grep('VID:PID=2E8A:0005')
            print(f"Found {len(current_ports)} ports matching VID/PID.")
        except Exception as e:
            print(f"Error listing serial ports: {e}")
            # Attempt to use cached info if available? Or return empty? Return empty for safety.
            self.set_status(f"Error listing ports: {e}", is_error=True)
            return [] # Return empty list on error


        # Use the latest port info
        for port_info in current_ports:
            serial_number = port_info.serial_number
            port = port_info.device

            if not serial_number:
                 print(f"Warning: Found matching device on {port} but no serial number. Skipping.")
                 continue

            # Assign chamber number from cached mapping
            chamber_number = self.chamber_mapping.get(serial_number)

            if chamber_number is None:
                print(f"Warning: Board S/N {serial_number} on {port} not found in chamber mapping.")
                # Optionally assign a temporary ID or skip? Assign temporary for now.
                # Find the next available temporary ID (e.g., starting from 1000)
                assigned_chambers = set(b.chamber_number for b in self.boards if b.chamber_number is not None)
                temp_chamber_num = 1000
                while temp_chamber_num in assigned_chambers or temp_chamber_num in [r[2] for r in results]:
                    temp_chamber_num += 1
                chamber_number = temp_chamber_num
                print(f"Assigned temporary chamber ID {chamber_number} to S/N {serial_number}")
                # Queue a status update for the main thread
                self.gui_queue.put(StatusUpdate(f"Warning: Board S/N {serial_number} not mapped, assigned Temp ID {chamber_number}", is_error=True))


            results.append([port, serial_number, chamber_number])
            print(f"Detected Board: Port={port}, SN={serial_number}, Chamber={chamber_number}")

        # Update the cache (optional, might do more harm if ports change rapidly)
        # self.initialize_port_cache() # Re-initialize cache based on current findings

        print(f"Detection finished. Returning {len(results)} boards.")
        return results


    def validate_percentage(self, value):
        """Validate that entry is a valid percentage (0-100)"""
        if value == "":
            return True # Allow empty string during typing

        try:
            val = int(value)
            is_valid = 0 <= val <= 100
            # print(f"Validating percentage '{value}': {is_valid}") # Debug print
            return is_valid
        except ValueError:
            # print(f"Validating percentage '{value}': Invalid (ValueError)") # Debug print
            return False

    def apply_all_settings(self):
        """Apply settings to all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to apply settings to.")
            return

        board_indices = list(range(len(self.boards)))
        if not board_indices:
            return

        self.set_status(self.cmd_messages['apply_start'] + f" to {len(board_indices)} boards...")
        print(f"Applying settings to all {len(board_indices)} boards.")

        # Start background thread for applying settings to all boards
        if 'apply_all' in self.background_operations and self.background_operations['apply_all']:
             print("Apply all already in progress.")
             return

        self.background_operations['apply_all'] = True
        threading.Thread(
            target=self._apply_settings_to_multiple_worker,
            args=(board_indices,), # Pass list of all board indices
            daemon=True,
            name="ApplyAllSettingsWorker"
        ).start()

    def apply_settings_to_multiple_boards(self, board_indices):
         """Helper to apply settings to a list of board indices (called from main thread)"""
         if not board_indices:
              return
         print(f"Queuing application of settings for boards: {board_indices}")
         # Start background worker
         threading.Thread(
            target=self._apply_settings_to_multiple_worker,
            args=(board_indices,),
            daemon=True,
            name=f"ApplySettingsWorker-{board_indices}"
         ).start()


    def _apply_settings_to_multiple_worker(self, board_indices):
        """Background worker thread for applying settings to a list of boards"""
        num_boards_to_process = len(board_indices)
        print(f"Worker started: Applying settings to {num_boards_to_process} boards: {board_indices}")
        processed_count = 0
        try:
            for board_idx in board_indices:
                if board_idx >= len(self.boards):
                    print(f"Skipping invalid board index {board_idx}")
                    continue

                # Apply settings for this specific board
                # This internally handles getting UI values and checking schedules
                self._apply_board_settings_worker(board_idx)

                processed_count += 1
                # Optional: Add a small delay between boards if needed
                # time.sleep(0.05)

        except Exception as e:
             print(f"Error in apply settings worker: {e}")
             self.gui_queue.put(StatusUpdate(f"Error applying settings: {e}", is_error=True))
        finally:
            # Clear operation flag if this was an 'apply_all' call
            if 'apply_all' in self.background_operations:
                 self.background_operations['apply_all'] = False

            # Add a final status update (consider if this is too noisy)
            # self.gui_queue.put(StatusUpdate(f"Finished applying settings batch ({processed_count}/{num_boards_to_process} boards)."))
            print(f"Worker finished: Processed {processed_count}/{num_boards_to_process} boards.")


    def apply_board_settings(self, board_idx):
        """Apply settings for a specific board (public method)"""
        if board_idx >= len(self.boards):
            messagebox.showerror("Error", f"Invalid board index: {board_idx}")
            return

        chamber_num = self.boards[board_idx].chamber_number or (board_idx + 1)
        self.set_status(f"Applying settings to Chamber {chamber_num}...")
        print(f"Queuing apply settings for board index {board_idx} (Chamber {chamber_num})")

        # Start background thread for applying settings to this single board
        threading.Thread(
            target=self._apply_board_settings_worker,
            args=(board_idx,),
            daemon=True,
            name=f"ApplyBoard-{board_idx}"
        ).start()

    def _apply_board_settings_worker(self, board_idx):
        """Background worker thread for applying settings to a single board"""
        if board_idx >= len(self.boards):
            print(f"Apply worker: Invalid board index {board_idx}")
            return

        board = self.boards[board_idx]
        chamber_num = board.chamber_number or (board_idx + 1)
        print(f"Apply worker: Starting for Chamber {chamber_num} (Index {board_idx})")

        final_duty_values = [0] * len(LED_CHANNELS) # Initialize with zeros
        schedule_details = [] # Store info about schedule overrides

        # --- Get UI values and check schedules (Needs main thread access) ---
        ui_values_result = {'values': {}, 'error': None, 'complete': False}

        def get_values_from_ui():
            try:
                board_ui_values = {}
                if board_idx in self.channel_schedules: # Ensure schedule data exists
                    for channel_idx, channel_name in enumerate(LED_CHANNEL_NAMES):
                        # 1. Get percentage from UI entry
                        percentage = 0
                        entry_key = (board_idx, channel_name)
                        if entry_key in self.led_entries:
                            try:
                                percentage = int(self.led_entries[entry_key].get())
                                if not (0 <= percentage <= 100):
                                    print(f"Warning: Invalid percentage {percentage} for {chamber_num}-{channel_name}. Using 0.")
                                    percentage = 0 # Clamp invalid values
                            except (ValueError, tk.TclError):
                                percentage = 0 # Default to 0 on error

                        board_ui_values[channel_name] = percentage

                    ui_values_result['values'] = board_ui_values
                else:
                     ui_values_result['error'] = "Schedule data missing"
            except Exception as e:
                ui_values_result['error'] = f"Error getting UI values: {e}"
            finally:
                ui_values_result['complete'] = True

        # Schedule UI access in main thread and wait
        self.root.after(0, get_values_from_ui)
        timeout = time.time() + 2.0 # 2 second timeout for UI access
        while not ui_values_result['complete'] and time.time() < timeout:
            time.sleep(0.01)

        if not ui_values_result['complete']:
             error_msg = f"Timeout getting UI values for Chamber {chamber_num}"
             print(error_msg)
             self.gui_queue.put(SettingsApplied(board_idx, False, error_msg))
             return
        if ui_values_result['error']:
             error_msg = f"Error for Chamber {chamber_num}: {ui_values_result['error']}"
             print(error_msg)
             self.gui_queue.put(SettingsApplied(board_idx, False, error_msg))
             return

        ui_percentages = ui_values_result['values']
        # --- End UI Value Retrieval ---


        # --- Determine final duty cycles based on schedules ---
        for channel_idx, channel_name in enumerate(LED_CHANNEL_NAMES):
            duty_cycle = 0
            percentage = ui_percentages.get(channel_name, 0) # Get UI value

            # Check schedule status for this channel
            is_scheduled_off = False
            schedule_info = self.channel_schedules.get(board_idx, {}).get(channel_name, {})
            if schedule_info.get("enabled", False):
                # Check current active state (re-check or use cached state?) Re-check for accuracy.
                current_time = datetime.now().strftime("%H:%M")
                on_time = schedule_info.get("on_time", "")
                off_time = schedule_info.get("off_time", "")

                if self.validate_time_format(on_time) and self.validate_time_format(off_time):
                    is_active = self.is_time_between(current_time, on_time, off_time)
                    if not is_active:
                        is_scheduled_off = True
                        schedule_details.append(f"{channel_name}: OFF")
                    else:
                         schedule_details.append(f"{channel_name}: ON")

                else:
                    # Invalid time format, treat as not scheduled off
                    schedule_details.append(f"{channel_name}: Invalid Time")


            # Set duty cycle: 0 if scheduled off, otherwise based on UI percentage
            if is_scheduled_off:
                duty_cycle = 0
            else:
                # Use cached lookup for performance
                duty_cycle = self.duty_cycle_lookup.get(percentage, 0) # Default to 0 if percentage invalid

            final_duty_values[channel_idx] = duty_cycle
        # --- End Duty Cycle Calculation ---

        print(f"Apply worker: Chamber {chamber_num} - Final Duty Cycles: {final_duty_values} (Schedule: {', '.join(schedule_details) or 'None'})")

        # --- Send command to board ---
        # Define callback for the send_command operation
        command_result = {'success': None, 'msg': '', 'complete': False}
        def on_command_complete(cmd_success, cmd_msg):
            command_result['success'] = cmd_success
            command_result['msg'] = cmd_msg
            command_result['complete'] = True
            print(f"Apply worker: Chamber {chamber_num} - Command complete: Success={cmd_success}, Msg='{cmd_msg}'")
            # Send result back to main thread via queue
            extra_info = f"Schedule: {', '.join(schedule_details)}" if schedule_details else None
            self.gui_queue.put(SettingsApplied(board_idx, cmd_success, cmd_msg, extra_info))

        # Send the command asynchronously
        board.send_command(final_duty_values, callback=on_command_complete)

        # Note: We don't wait here for the command to complete in the worker thread.
        # The result is handled asynchronously via the callback and GUI queue.
        print(f"Apply worker: Command sent for Chamber {chamber_num}.")


    def toggle_all_fans(self):
        """Toggle all fans on or off on all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control fans.")
            return

        # Determine target state and speed
        target_state_on = not self.fans_on
        speed = 0
        if target_state_on:
            try:
                speed = int(self.fan_speed_var.get())
                if not (0 <= speed <= 100):
                    speed = 50 # Default to 50 if invalid
                    self.fan_speed_var.set("50") # Correct UI
            except ValueError:
                speed = 50
                self.fan_speed_var.set("50") # Correct UI

        # Update internal state and UI immediately
        self.fans_on = target_state_on
        self.fan_button_var.set("Turn Fans OFF" if self.fans_on else "Turn Fans ON")

        status_msg = f"Turning all fans {'ON' if self.fans_on else 'OFF'}"
        if self.fans_on:
             status_msg += f" at {speed}%..."
        self.set_status(status_msg)
        print(status_msg)

        # --- Start background thread ---
        num_boards = len(self.boards)
        if num_boards == 0: return

        # Use a counter and lock for completion tracking
        completion_counter = {'count': 0, 'success_count': 0, 'fail_count': 0}
        lock = threading.Lock()

        def fan_toggle_callback(success, msg, idx=None):
            with lock:
                completion_counter['count'] += 1
                if success:
                    completion_counter['success_count'] += 1
                else:
                    completion_counter['fail_count'] += 1
                    # Log specific error
                    chamber_num = self.boards[idx].chamber_number if idx < len(self.boards) else idx+1
                    print(f"Fan toggle error (Chamber {chamber_num}): {msg}")

                if completion_counter['count'] == num_boards:
                    # All callbacks received, update final status via queue
                    final_msg = f"Fan toggle complete. Success: {completion_counter['success_count']}, Failed: {completion_counter['fail_count']}"
                    self.gui_queue.put(StatusUpdate(final_msg, is_error=(completion_counter['fail_count'] > 0)))
                    print(final_msg)


        # Send commands to boards
        for i, board in enumerate(self.boards):
            if self.fans_on:
                board.set_fan_speed(speed, callback=lambda s, m, idx=i: fan_toggle_callback(s, m, idx))
            else:
                board.set_fan_speed(0, callback=lambda s, m, idx=i: fan_toggle_callback(s, m, idx))


    def apply_fan_settings(self):
        """Apply the fan speed to all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control fans.")
            return

        # Get and validate speed
        try:
            speed = int(self.fan_speed_var.get())
            if not (0 <= speed <= 100):
                messagebox.showerror("Invalid Speed", "Fan speed must be between 0 and 100.")
                return
        except ValueError:
            messagebox.showerror("Invalid Speed", "Fan speed must be a number.")
            return

        # Update internal state based on speed
        self.fans_on = (speed > 0)
        self.fan_button_var.set("Turn Fans OFF" if self.fans_on else "Turn Fans ON")

        status_msg = f"Setting all fans to {speed}%..."
        self.set_status(status_msg)
        print(status_msg)

        # --- Start background thread ---
        num_boards = len(self.boards)
        if num_boards == 0: return

        completion_counter = {'count': 0, 'success_count': 0, 'fail_count': 0}
        lock = threading.Lock()

        def fan_apply_callback(success, msg, idx=None):
             with lock:
                completion_counter['count'] += 1
                if success:
                    completion_counter['success_count'] += 1
                else:
                    completion_counter['fail_count'] += 1
                    chamber_num = self.boards[idx].chamber_number if idx < len(self.boards) else idx+1
                    print(f"Fan apply error (Chamber {chamber_num}): {msg}")

                if completion_counter['count'] == num_boards:
                    final_msg = f"Fan speed setting complete. Success: {completion_counter['success_count']}, Failed: {completion_counter['fail_count']}"
                    self.gui_queue.put(StatusUpdate(final_msg, is_error=(completion_counter['fail_count'] > 0)))
                    print(final_msg)

        # Send commands
        for i, board in enumerate(self.boards):
            board.set_fan_speed(speed, callback=lambda s, m, idx=i: fan_apply_callback(s, m, idx))


    def export_settings(self):
        """Export current LED settings and schedules to a JSON file"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to export settings from.")
            return

        # --- Get File Path (Main Thread) ---
        file_path = filedialog.asksaveasfilename(
            initialdir=DEFAULT_DOCUMENTS_PATH,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
            title="Save LED Settings"
        )
        if not file_path:
            self.set_status("Export cancelled.")
            return  # User canceled

        self.set_status("Exporting settings...")

        # --- Start Background Thread for Data Collection & Saving ---
        threading.Thread(
            target=self._export_settings_worker,
            args=(file_path,),
            daemon=True,
            name="ExportSettingsWorker"
        ).start()

    def _export_settings_worker(self, file_path):
        """Background worker thread for exporting settings"""
        print(f"Export worker started for path: {file_path}")
        settings_to_export = {}
        export_error = None

        # --- Collect Settings (Needs Main Thread Access for UI) ---
        collected_data = {'data': {}, 'error': None, 'complete': False}

        def collect_from_ui():
            try:
                export_data = {}
                for board_idx, board in enumerate(self.boards):
                    # Use chamber number if available, otherwise board index as fallback key
                    chamber_num = board.chamber_number
                    board_key = f"chamber_{chamber_num}" if chamber_num is not None else f"board_{board_idx}"

                    board_settings = {"intensity": {}, "schedule": {}, "fan": {}}

                    # Get intensity settings
                    for channel_name in LED_CHANNEL_NAMES:
                        intensity = 0
                        entry_key = (board_idx, channel_name)
                        if entry_key in self.led_entries:
                            try:
                                intensity = int(self.led_entries[entry_key].get())
                            except (ValueError, tk.TclError):
                                intensity = 0 # Default on error
                        board_settings["intensity"][channel_name] = intensity

                    # Get schedule settings (per channel)
                    board_schedule_data = {}
                    if board_idx in self.channel_schedules:
                        for channel_name, schedule_info in self.channel_schedules[board_idx].items():
                             # Get times from UI entries if they exist, else from internal data
                             on_time = schedule_info.get("on_time", "08:00")
                             off_time = schedule_info.get("off_time", "00:00")
                             enabled = schedule_info.get("enabled", False)

                             on_entry_key = (board_idx, channel_name, "on")
                             if on_entry_key in self.channel_time_entries:
                                  try: on_time = self.channel_time_entries[on_entry_key].get()
                                  except tk.TclError: pass # Keep default if widget destroyed
                             off_entry_key = (board_idx, channel_name, "off")
                             if off_entry_key in self.channel_time_entries:
                                  try: off_time = self.channel_time_entries[off_entry_key].get()
                                  except tk.TclError: pass # Keep default

                             enabled_var_key = (board_idx, channel_name)
                             if enabled_var_key in self.channel_schedule_vars:
                                  try: enabled = self.channel_schedule_vars[enabled_var_key].get()
                                  except tk.TclError: pass # Keep default


                             board_schedule_data[channel_name] = {
                                 "on_time": on_time,
                                 "off_time": off_time,
                                 "enabled": enabled
                             }
                    board_settings["schedule"] = board_schedule_data


                    # Get fan settings (from BoardConnection object state)
                    board_settings["fan"] = {
                        "enabled": board.fan_enabled,
                        "speed": board.fan_speed
                    }

                    export_data[board_key] = board_settings

                collected_data['data'] = export_data
            except Exception as e:
                 collected_data['error'] = f"Error collecting UI data: {e}"
            finally:
                 collected_data['complete'] = True

        # Schedule UI access and wait
        self.root.after(0, collect_from_ui)
        timeout = time.time() + 5.0 # 5 second timeout
        while not collected_data['complete'] and time.time() < timeout:
            time.sleep(0.01)

        if not collected_data['complete']:
            export_error = "Timeout collecting settings data."
        elif collected_data['error']:
            export_error = collected_data['error']
        else:
            settings_to_export = collected_data['data']
        # --- End Data Collection ---


        # --- Save to File (If no error) ---
        if not export_error:
            try:
                print(f"Export worker: Saving data to {file_path}")
                with open(file_path, 'w') as f:
                    json.dump(settings_to_export, f, indent=4, sort_keys=True)
                print("Export worker: Save complete.")
            except Exception as e:
                export_error = f"Error writing to file: {e}"
        # --- End Save to File ---

        # --- Send Result to GUI Queue ---
        if export_error:
            print(f"Export worker error: {export_error}")
            self.gui_queue.put(FileOperationComplete('export', False, export_error))
        else:
            print("Export worker finished successfully.")
            self.gui_queue.put(FileOperationComplete('export', True, file_path))


    def import_settings(self):
        """Import LED settings and schedules from a JSON file"""
        # --- Get File Path (Main Thread) ---
        file_path = filedialog.askopenfilename(
            initialdir=DEFAULT_DOCUMENTS_PATH,
            filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
            title="Import LED Settings"
        )
        if not file_path:
            self.set_status("Import cancelled.")
            return # User canceled

        self.set_status(f"Importing settings from {os.path.basename(file_path)}...")

        # --- Start Background Thread for Reading File & Applying ---
        threading.Thread(
            target=self._import_settings_worker,
            args=(file_path,),
            daemon=True,
            name="ImportSettingsWorker"
        ).start()

    def _import_settings_worker(self, file_path):
        """Background worker thread for importing settings"""
        print(f"Import worker started for path: {file_path}")
        imported_settings = None
        import_error = None

        # --- Read Settings from File ---
        try:
            print(f"Import worker: Reading file {file_path}")
            with open(file_path, 'r') as f:
                imported_settings = json.load(f)
            print("Import worker: File read successfully.")

            # Basic validation
            if not isinstance(imported_settings, dict):
                raise ValueError("Invalid file format: Top level must be a dictionary (object).")

        except FileNotFoundError:
             import_error = "File not found."
        except json.JSONDecodeError as e:
             import_error = f"Invalid JSON format: {e}"
        except Exception as e:
             import_error = f"Error reading file: {e}"
        # --- End Read File ---

        if import_error:
             print(f"Import worker error reading file: {import_error}")
             self.gui_queue.put(FileOperationComplete('import', False, import_error))
             return

        if not self.boards:
             import_error = "No boards connected to apply settings to."
             print(import_error)
             self.gui_queue.put(FileOperationComplete('import', False, import_error))
             return

        # --- Apply Settings to UI (Needs Main Thread Access) ---
        apply_result = {'applied_count': 0, 'skipped_chambers': set(), 'error': None, 'complete': False}

        def apply_to_ui():
            applied_count = 0
            skipped_chambers = set()
            try:
                # Create reverse mapping for faster lookup during import
                chamber_num_to_idx = {info['chamber']: idx for idx, info in enumerate(self.boards) if info.chamber_number is not None}
                # Handle potential fallback keys like "board_X" if needed

                for board_key, board_settings in imported_settings.items():
                    board_idx = None
                    chamber_num = None

                    # Find board index based on key ("chamber_X" or "board_Y")
                    chamber_match = self.chamber_num_pattern.match(board_key)
                    if chamber_match:
                        try:
                            chamber_num = int(chamber_match.group(1))
                            board_idx = self.chamber_to_board_idx.get(chamber_num)
                        except ValueError:
                            pass # Invalid number in key
                    # Add fallback for "board_Y" if necessary

                    if board_idx is None:
                        skipped_chambers.add(board_key)
                        continue # Skip if board not found or key invalid

                    # --- Apply Intensity ---
                    if "intensity" in board_settings and isinstance(board_settings["intensity"], dict):
                        for channel_name, value in board_settings["intensity"].items():
                            if channel_name in LED_CHANNELS:
                                entry_key = (board_idx, channel_name)
                                if entry_key in self.led_entries:
                                    try:
                                        # Validate percentage before setting
                                        percent_val = int(value)
                                        if 0 <= percent_val <= 100:
                                            self.led_entries[entry_key].delete(0, tk.END)
                                            self.led_entries[entry_key].insert(0, str(percent_val))
                                            applied_count += 1
                                        else:
                                             print(f"Warning (Import): Invalid intensity '{value}' for {board_key}-{channel_name}. Skipping.")
                                    except (ValueError, tk.TclError):
                                        print(f"Warning (Import): Error setting intensity for {board_key}-{channel_name}.")
                                        pass # Ignore errors setting individual entries


                    # --- Apply Schedule (Per Channel) ---
                    if "schedule" in board_settings and isinstance(board_settings["schedule"], dict):
                         board_schedule_data = board_settings["schedule"]
                         if board_idx not in self.channel_schedules:
                              self.channel_schedules[board_idx] = {} # Initialize if missing

                         for channel_name, chan_schedule in board_schedule_data.items():
                              if channel_name in LED_CHANNELS and isinstance(chan_schedule, dict):
                                   # Get values safely
                                   on_time = chan_schedule.get("on_time", "08:00")
                                   off_time = chan_schedule.get("off_time", "00:00")
                                   enabled = bool(chan_schedule.get("enabled", False))

                                   # Validate times before applying
                                   if not self.validate_time_format(on_time): on_time = "08:00"
                                   if not self.validate_time_format(off_time): off_time = "00:00"

                                   # Update internal data structure
                                   if channel_name not in self.channel_schedules[board_idx]:
                                        self.channel_schedules[board_idx][channel_name] = {}
                                   self.channel_schedules[board_idx][channel_name].update({
                                        "on_time": on_time,
                                        "off_time": off_time,
                                        "enabled": enabled
                                   })

                                   # Update UI Widgets
                                   on_entry_key = (board_idx, channel_name, "on")
                                   if on_entry_key in self.channel_time_entries:
                                        try:
                                             self.channel_time_entries[on_entry_key].delete(0, tk.END)
                                             self.channel_time_entries[on_entry_key].insert(0, on_time)
                                        except tk.TclError: pass
                                   off_entry_key = (board_idx, channel_name, "off")
                                   if off_entry_key in self.channel_time_entries:
                                        try:
                                             self.channel_time_entries[off_entry_key].delete(0, tk.END)
                                             self.channel_time_entries[off_entry_key].insert(0, off_time)
                                        except tk.TclError: pass
                                   enabled_var_key = (board_idx, channel_name)
                                   if enabled_var_key in self.channel_schedule_vars:
                                        try:
                                             self.channel_schedule_vars[enabled_var_key].set(enabled)
                                        except tk.TclError: pass

                                   applied_count += 1 # Count schedule update

                    # --- Apply Fan Settings (Update UI and internal state) ---
                    # Note: Fan settings are global in UI, but per-board internally.
                    # We apply the settings from the *first* board found in the file
                    # to the global UI controls. The actual application to hardware
                    # happens via apply_fan_settings or apply_all_settings.
                    if "fan" in board_settings and isinstance(board_settings["fan"], dict) and not apply_result.get('fan_applied_to_ui', False):
                         fan_data = board_settings["fan"]
                         fan_speed = fan_data.get("speed", 50)
                         fan_enabled = fan_data.get("enabled", False)
                         try:
                              valid_speed = int(fan_speed)
                              if 0 <= valid_speed <= 100:
                                   self.fan_speed_var.set(str(valid_speed))
                                   # Update internal state and button based on the imported 'enabled' flag
                                   self.fans_on = bool(fan_enabled)
                                   self.fan_button_var.set("Turn Fans OFF" if self.fans_on else "Turn Fans ON")
                                   apply_result['fan_applied_to_ui'] = True # Mark that we've set the UI fan controls
                                   applied_count += 1
                              else:
                                   print(f"Warning (Import): Invalid fan speed '{fan_speed}' for {board_key}. Using default.")
                         except (ValueError, tk.TclError):
                              print(f"Warning (Import): Error setting fan speed UI for {board_key}.")
                              pass


                apply_result['applied_count'] = applied_count
                apply_result['skipped_chambers'] = skipped_chambers
            except Exception as e:
                apply_result['error'] = f"Error applying settings to UI: {e}"
            finally:
                apply_result['complete'] = True

        # Schedule UI update and wait
        self.root.after(0, apply_to_ui)
        timeout = time.time() + 5.0 # 5 second timeout
        while not apply_result['complete'] and time.time() < timeout:
            time.sleep(0.01)

        if not apply_result['complete']:
            import_error = "Timeout applying settings to UI."
        elif apply_result['error']:
            import_error = apply_result['error']
        # --- End Apply Settings to UI ---


        # --- Send Result to GUI Queue ---
        if import_error:
             print(f"Import worker error applying settings: {import_error}")
             self.gui_queue.put(FileOperationComplete('import', False, import_error))
        else:
             success_msg = f"Imported {apply_result['applied_count']} settings."
             if apply_result['skipped_chambers']:
                  success_msg += f" Skipped unknown chambers: {', '.join(apply_result['skipped_chambers'])}."
             print(f"Import worker finished: {success_msg}")
             # Pass back data including whether fan settings were found
             import_data = {
                  'applied_count': apply_result['applied_count'],
                  'fan_settings_found': apply_result.get('fan_applied_to_ui', False)
             }
             self.gui_queue.put(FileOperationComplete('import', True, success_msg, import_data))


    def check_channel_active_state(self, board_idx, channel_name):
        """Check if a specific channel should be active based on its schedule"""
        schedule_info = self.channel_schedules.get(board_idx, {}).get(channel_name, {})

        if not schedule_info.get("enabled", False):
            return True  # Not scheduled or schedule disabled -> active

        current_time = datetime.now().strftime("%H:%M")
        on_time = schedule_info.get("on_time", "08:00")
        off_time = schedule_info.get("off_time", "00:00")

        if not self.validate_time_format(on_time) or not self.validate_time_format(off_time):
            # Log warning maybe? Treat as active if time is invalid.
            # self.gui_queue.put(StatusUpdate(f"Board {board_idx+1}-{channel_name}: Invalid schedule time. Assuming ON."))
            return True

        is_active = self.is_time_between(current_time, on_time, off_time)

        # Update the internal active state (optional, could rely on check during apply)
        # schedule_info["active"] = is_active

        return is_active

    # Removed save_board_ui_values - saving happens during apply or master toggle

    def validate_time_format(self, time_str):
        """Validate that the time string is in HH:MM format (24-hour)"""
        if time_str is None: return False
        # Use pre-compiled pattern
        return bool(self.time_pattern.match(time_str))

    def validate_time_entry_visual(self, board_idx, channel_name, entry_type, new_value, entry_widget):
        """Validate time entry visually by changing text color"""
        # Check if widget still exists
        try:
             entry_widget.winfo_exists()
        except tk.TclError:
             return # Widget destroyed, do nothing

        if self.validate_time_format(new_value):
            entry_widget.config(foreground=self.cached_colors['normal'])
        else:
            entry_widget.config(foreground=self.cached_colors['error'])


    # --- Widget Update Batching (Commented Out) ---
    # def queue_widget_update(self, widget_id, update_type, value):
    #     """Queue a widget update to be processed in batches"""
    #     self.widget_update_queue.put((widget_id, update_type, value))
    #     if not self.update_batch_timer:
    #         self.process_widget_updates()

    # def process_widget_updates(self):
    #     """Process queued widget updates in batches"""
    #     # ... (Implementation as before, adapted for new widget keys) ...
    #     self.update_batch_timer = self.root.after(self.update_batch_interval, self.process_widget_updates)


    def set_status(self, message, is_error=False):
        """Update status bar (can optionally batch updates)"""
        # Simple direct update for now, re-enable batching if needed
        if is_error:
             self.status_var.set(f"Error: {message}")
             # Consider adding subtle error styling to status bar
        else:
             self.status_var.set(message)
        # print(f"Status: {message}") # Log status updates


    # --- Batch Status Updates (Optional, keep commented if direct update is fine) ---
    # def set_status(self, message, is_error=False):
    #     """Batch status updates to reduce status bar redraws"""
    #     if not hasattr(self, 'status_update_batch'):
    #         self.status_update_batch = []

    #     # Add the message and error status to the batch
    #     self.status_update_batch.append({'message': message, 'is_error': is_error})

    #     # If there's already a pending update, let it handle this message
    #     if self.status_update_timer:
    #         return

    #     # Schedule the status update
    #     self.status_update_timer = self.root.after(
    #         self.timings['status_update_batch'], self.process_status_updates)

    # def process_status_updates(self):
    #     """Process batched status updates"""
    #     self.status_update_timer = None # Clear timer reference

    #     if not self.status_update_batch:
    #         return

    #     # Use the most recent status message
    #     latest_update = self.status_update_batch[-1]
    #     message = latest_update['message']
    #     is_error = latest_update['is_error']

    #     if is_error:
    #          self.status_var.set(f"Error: {message}")
    #     else:
    #          self.status_var.set(message)

    #     # Clear the batch
    #     self.status_update_batch = []
    # --- End Batch Status Updates ---


    def update_channel_schedule(self, board_idx, channel_name):
        """Update the schedule state for a specific channel when checkbox is toggled"""
        if board_idx >= len(self.boards): return
        if board_idx not in self.channel_schedules or channel_name not in self.channel_schedules[board_idx]:
            print(f"Error: Schedule data not found for board {board_idx}, channel {channel_name}")
            # Initialize if missing?
            self.channel_schedules.setdefault(board_idx, {})[channel_name] = {
                 "on_time": "08:00", "off_time": "00:00", "enabled": False, "active": True
            }
            # return # Or proceed with defaults? Proceed.


        schedule_info = self.channel_schedules[board_idx][channel_name]
        schedule_var_key = (board_idx, channel_name)
        time_entry_key_on = (board_idx, channel_name, "on")
        time_entry_key_off = (board_idx, channel_name, "off")

        # Get new enabled state from checkbox
        is_enabled = False
        if schedule_var_key in self.channel_schedule_vars:
            try:
                is_enabled = self.channel_schedule_vars[schedule_var_key].get()
            except tk.TclError:
                 print(f"Warning: Checkbox variable not found for {board_idx}-{channel_name}")
                 return # Cannot proceed without checkbox state

        # Get and validate time entries BEFORE enabling
        on_time = schedule_info.get("on_time", "08:00") # Default
        off_time = schedule_info.get("off_time", "00:00") # Default
        on_time_valid = False
        off_time_valid = False

        if time_entry_key_on in self.channel_time_entries:
            try:
                on_time = self.channel_time_entries[time_entry_key_on].get()
                on_time_valid = self.validate_time_format(on_time)
            except tk.TclError: pass # Widget might be destroyed

        if time_entry_key_off in self.channel_time_entries:
             try:
                off_time = self.channel_time_entries[time_entry_key_off].get()
                off_time_valid = self.validate_time_format(off_time)
             except tk.TclError: pass


        # If enabling, check if times are valid
        if is_enabled and (not on_time_valid or not off_time_valid):
            messagebox.showerror("Invalid Time Format",
                f"Cannot enable schedule for {channel_name} with invalid time format (HH:MM).")
            # Reset checkbox state in UI (use after to ensure it runs after current event)
            if schedule_var_key in self.channel_schedule_vars:
                 self.root.after(0, lambda var=self.channel_schedule_vars[schedule_var_key]: var.set(False))
            is_enabled = False # Ensure internal state reflects disabled


        # Update internal schedule data
        schedule_info["on_time"] = on_time if on_time_valid else "08:00"
        schedule_info["off_time"] = off_time if off_time_valid else "00:00"
        schedule_info["enabled"] = is_enabled

        chamber_num = self.boards[board_idx].chamber_number or (board_idx + 1)
        action = "enabled" if is_enabled else "disabled"
        print(f"Schedule {action} for Chamber {chamber_num} - Channel {channel_name} (Times: {on_time}-{off_time})")
        self.set_status(f"Schedule {action} for {chamber_num}-{channel_name}")

        # Mark board as changed and trigger apply settings for this board
        # This will re-evaluate all channels on the board based on current schedules
        self.changed_boards.add(board_idx)
        # Use root.after to ensure apply runs after this event handler finishes
        self.root.after(10, lambda idx=board_idx: self.apply_board_settings(idx)) # Small delay


    def is_time_between(self, check_time_str, start_time_str, end_time_str):
        """Check if check_time is between start_time and end_time (inclusive) handling HH:MM format and day wraparound."""
        try:
            # Convert HH:MM strings to minutes since midnight
            ch, cm = map(int, check_time_str.split(':'))
            sh, sm = map(int, start_time_str.split(':'))
            eh, em = map(int, end_time_str.split(':'))

            check_minutes = ch * 60 + cm
            start_minutes = sh * 60 + sm
            end_minutes = eh * 60 + em

            # Handle the case where start and end times are the same (on for 24 hours or 0 hours?)
            # Assume 24 hours if start == end
            if start_minutes == end_minutes:
                return True

            # Normal case: start time is before end time (e.g., 08:00 to 18:00)
            if start_minutes < end_minutes:
                return start_minutes <= check_minutes <= end_minutes
            # Wraparound case: end time is on the next day (e.g., 20:00 to 06:00)
            else: # start_minutes > end_minutes
                return check_minutes >= start_minutes or check_minutes <= end_minutes
        except (ValueError, AttributeError):
             # Handle invalid time format strings gracefully
             print(f"Warning: Invalid time format encountered in is_time_between ({check_time_str}, {start_time_str}, {end_time_str})")
             return False # Or True depending on desired default behavior? False is safer.


    def process_gui_queue(self):
        """Process GUI action queue from worker threads"""
        try:
            # Process multiple items per call for efficiency
            for _ in range(20): # Limit items per cycle
                action = self.gui_queue.get_nowait()

                # Handle different action types
                if isinstance(action, StatusUpdate):
                    self.set_status(action.message, action.is_error)
                    if action.is_error:
                        # Optionally show critical errors in a messagebox
                        # messagebox.showerror("Background Error", action.message)
                        pass

                elif isinstance(action, BoardsDetected):
                    if action.error:
                        messagebox.showerror("Error Scanning Boards", action.error)
                        self.set_status(f"Scan Error: {action.error}", is_error=True)
                        self.boards = [] # Ensure boards list is empty on error
                    else:
                        self.boards = action.boards # Update boards list
                        print(f"Received {len(self.boards)} boards from scan worker.")

                    # Recreate frames regardless of error (clears old frames if error occurred)
                    self.create_board_frames() # This handles GUI update and status

                    # Update status after frames are created
                    if not action.error:
                         board_count = len(self.boards)
                         chambers_1_8 = sum(1 for b in self.boards if b.chamber_number and 1 <= b.chamber_number <= 8)
                         chambers_9_16 = sum(1 for b in self.boards if b.chamber_number and 9 <= b.chamber_number <= 16)
                         unknown = board_count - chambers_1_8 - chambers_9_16
                         status_msg = f"Scan complete: Found {board_count} board(s)."
                         # Add details if useful
                         # status_msg += f" (Ch 1-8: {chambers_1_8}, Ch 9-16: {chambers_9_16}, Unmapped: {unknown})"
                         self.set_status(status_msg)


                elif isinstance(action, SettingsApplied):
                    if action.board_idx >= len(self.boards): continue # Ignore if board index invalid

                    chamber_num = self.boards[action.board_idx].chamber_number or (action.board_idx + 1)
                    status_prefix = f"Chamber {chamber_num}:"
                    if action.success:
                        status_msg = f"{status_prefix} Settings applied."
                        if action.extra_info:
                             status_msg += f" ({action.extra_info})"
                        self.set_status(status_msg)
                    else:
                        error_msg = f"{status_prefix} Apply Error - {action.message}"
                        # Only show messagebox for non-connection errors? Or always? Always for now.
                        messagebox.showerror(f"Apply Error (Chamber {chamber_num})", action.message)
                        self.set_status(error_msg, is_error=True)

                # Removed ToggleComplete handler - logic integrated elsewhere

                elif isinstance(action, FileOperationComplete):
                    op_type = action.operation_type.capitalize()
                    if action.success:
                        if action.operation_type == 'import':
                             self.set_status(f"Import successful: {action.message}")
                             # Ask user if they want to apply imported settings
                             if action.data and 'applied_count' in action.data:
                                  apply_msg = f"Successfully loaded {action.data['applied_count']} settings from file.\n\n"
                                  apply_msg += "Apply these settings to the boards now?"
                                  if action.data.get('fan_settings_found'):
                                       apply_msg += "\n(Fan settings will also be applied.)"

                                  if messagebox.askyesno("Apply Imported Settings", apply_msg):
                                       self.apply_all_settings()
                                       # Apply fan settings separately if they were imported
                                       if action.data.get('fan_settings_found'):
                                            self.apply_fan_settings()
                        elif action.operation_type == 'export':
                             self.set_status(f"Settings exported to {action.message}")
                             messagebox.showinfo("Export Successful", f"Settings successfully exported to:\n{action.message}")
                    else: # Error
                        messagebox.showerror(f"{op_type} Error", f"Error during {action.operation_type}: {action.message}")
                        self.set_status(f"{op_type} error: {action.message}", is_error=True)


                elif isinstance(action, SchedulerUpdate):
                    # Update internal schedule state for the specific channel
                    if (action.board_idx in self.channel_schedules and
                        action.channel_name in self.channel_schedules[action.board_idx]):

                        # Update the 'active' state in our internal structure
                        self.channel_schedules[action.board_idx][action.channel_name]['active'] = action.active

                        # Update visual indicator (background color of schedule frame)
                        schedule_frame_widget = None # Need a way to get this widget reference
                        # TODO: Store schedule frame widgets if visual feedback is desired
                        # Example: self.channel_schedule_frames[(board_idx, channel_name)] = schedule_outer_frame
                        # if schedule_frame_widget:
                        #     style = 'ActiveSchedule.TFrame' if action.active else 'InactiveSchedule.TFrame'
                        #     schedule_frame_widget.config(style=style)

                        # Note: Applying settings is now handled by the scheduler worker itself
                        # by calling apply_settings_to_multiple_boards. We don't need to do it here.
                        # chamber_num = self.boards[action.board_idx].chamber_number or (action.board_idx+1)
                        # state = "ON" if action.active else "OFF"
                        # print(f"GUI Queue: Processed SchedulerUpdate for {chamber_num}-{action.channel_name} -> {state}")


                self.gui_queue.task_done() # Mark action as processed

        except queue.Empty:
            pass # No more actions in queue
        except Exception as e:
             print(f"Error processing GUI queue: {e}") # Log unexpected errors

        # Schedule next queue check
        self.root.after(self.queue_check_interval, self.process_gui_queue)


    def duty_cycle_from_percentage(self, percentage):
        """Convert a percentage (0-100) to duty cycle (0-4095) using cache"""
        # Clamp percentage to valid range
        percentage = max(0, min(100, int(percentage)))
        # Use pre-computed lookup table
        return self.duty_cycle_lookup.get(percentage, 0)


if __name__ == "__main__":
    root = tk.Tk()
    # Set minimum size for the window
    root.minsize(1200, 700)
    app = LEDControlGUI(root)
    root.mainloop()
