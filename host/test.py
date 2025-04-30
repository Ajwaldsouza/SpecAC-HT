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
    'WHITE': "#E0E0E0", # Use light gray for better visibility on white background
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
# Assumes the script is in a 'gui' folder and 'microcontroller' is a sibling folder
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
SERIAL_MAPPING_FILE = os.path.join(PROJECT_ROOT, "microcontroller", "microcontroller_serial.txt")


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
    'scheduler_relaxed': 5000,    # relaxed scheduler check interval (ms)
    'command_callback_timeout': 3.0 # Seconds to wait for command callback
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
    'import_start': "Importing settings...",
    'import_success': "Settings imported successfully",
    'export_start': "Exporting settings...",
    'export_success': "Settings exported successfully",
    'error_prefix': "Error: "
}

# --- GUI Action Classes for Queue Communication ---
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
        self.boards = boards # List of BoardConnection objects
        self.error = error

class CommandComplete(GUIAction):
    """Message indicating a command sent to a board has completed"""
    def __init__(self, board_idx, command_type, success, message, extra_info=None):
        self.board_idx = board_idx
        self.command_type = command_type # e.g., "SETALL", "FAN_SET"
        self.success = success
        self.message = message
        self.extra_info = extra_info # Optional data (e.g., schedule status)

class SchedulerUpdate(GUIAction):
    """Message to update scheduler state for a specific channel"""
    def __init__(self, board_idx, channel_name, active):
        self.board_idx = board_idx
        self.channel_name = channel_name
        self.active = active # True if channel should be ON, False if OFF

class FileOperationComplete(GUIAction):
    """Message indicating file operation completion"""
    def __init__(self, operation_type, success, message, data=None):
        self.operation_type = operation_type  # 'import' or 'export'
        self.success = success
        self.message = message # File path on success, error message on failure
        self.data = data # Optional data (e.g., import results)
# --- End GUI Action Classes ---


class BoardConnection:
    """
    Manages the serial connection and command queue for a single XIAO RP2040 board.
    Uses a background thread to process commands and report results via a GUI queue.
    """
    # Static properties to cache command types
    CMD_SETALL = "SETALL"
    CMD_FAN_SET = "FAN_SET"
    # FAN_ON and FAN_OFF are handled via FAN_SET

    # Pre-computed responses for faster comparison
    RESP_OK = "OK"
    RESP_ERR_PREFIX = "ERR:"

    # Pre-computed zero duty cycle array for turning off all LEDs
    ZERO_DUTY_CYCLES = [0] * len(LED_CHANNELS)

    def __init__(self, port, serial_number, gui_queue, chamber_number=None):
        """
        Initializes the BoardConnection.

        Args:
            port (str): The serial port name (e.g., 'COM3' or '/dev/ttyACM0').
            serial_number (str): The unique serial number of the board.
            gui_queue (queue.Queue): The thread-safe queue for sending results to the GUI.
            chamber_number (int, optional): The assigned chamber number. Defaults to None.
        """
        self.port = port
        self.serial_number = serial_number
        self.chamber_number = chamber_number
        self.gui_queue = gui_queue # Queue for sending results to GUI
        self.serial_conn = None
        self.is_connected = False
        self.last_error = ""
        self.fan_speed = 0 # Store last known fan speed percentage
        self.fan_enabled = False # Store last known fan enabled state
        self.max_retries = 3
        self.lock = threading.RLock() # Reentrant lock for thread safety
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"ConnExec-{self.port}")
        self.pending_futures = [] # Track futures for connect/disconnect
        self.command_queue = queue.Queue()
        self.command_processor_running = False
        self.command_processor_thread = None
        self.stop_event = threading.Event() # Event to signal thread termination

        # Cache connection parameters
        self.conn_params = {
            'baudrate': 115200,
            'timeout': 1.0, # Read timeout
            'write_timeout': 1.0 # Write timeout
        }

    def connect_async(self):
        """Establish serial connection asynchronously."""
        with self.lock:
            if self.is_connected or 'connect' in [f.info for f in self.pending_futures if not f.done()]:
                return # Already connected or connection attempt in progress
            print(f"[{self.port}] Submitting connection task...")
            future = self.executor.submit(self._connect_impl)
            future.info = 'connect' # Add info for tracking
            self.pending_futures.append(future)
        return future

    def _connect_impl(self):
        """Internal implementation of connect operation (runs in executor thread)."""
        with self.lock:
            if self.is_connected: return True, "Already connected"
            print(f"[{self.port}] Attempting connection...")
            try:
                # Close existing connection if any (shouldn't happen with check above, but safety)
                if self.serial_conn:
                    try: self.serial_conn.close()
                    except: pass
                self.serial_conn = serial.Serial(port=self.port, **self.conn_params)
                # RP2040 might reset on connection, wait
                time.sleep(2.0)
                # Clear any boot messages
                if self.serial_conn.in_waiting > 0:
                    self.serial_conn.reset_input_buffer()
                self.is_connected = True
                self.last_error = ""
                print(f"[{self.port}] Connection successful.")
                return True, "Connected"
            except serial.SerialException as e:
                self.last_error = f"Serial Error: {str(e)}"
                self.is_connected = False
                self.serial_conn = None
                print(f"[{self.port}] Connection failed: {self.last_error}")
                return False, self.last_error
            except Exception as e:
                self.last_error = f"Unexpected Error: {str(e)}"
                self.is_connected = False
                self.serial_conn = None
                print(f"[{self.port}] Connection failed unexpectedly: {self.last_error}")
                return False, self.last_error

    def disconnect_async(self):
        """Close serial connection asynchronously."""
        with self.lock:
            if not self.is_connected or 'disconnect' in [f.info for f in self.pending_futures if not f.done()]:
                return # Already disconnected or disconnection attempt in progress
            print(f"[{self.port}] Submitting disconnection task...")
            future = self.executor.submit(self._disconnect_impl)
            future.info = 'disconnect'
            self.pending_futures.append(future)
        return future

    def _disconnect_impl(self):
        """Internal implementation of disconnect operation (runs in executor thread)."""
        with self.lock:
            if self.serial_conn:
                print(f"[{self.port}] Closing serial connection...")
                try:
                    self.serial_conn.close()
                except Exception as e:
                    print(f"[{self.port}] Error closing serial port: {e}")
            self.is_connected = False
            self.serial_conn = None
            print(f"[{self.port}] Disconnected.")
            return True, "Disconnected"

    def _send_receive(self, command):
        """Sends a command and waits for an 'OK' or 'ERR:' response."""
        if not self.is_connected:
            return False, "Not connected"

        retries = 0
        while retries < self.max_retries:
            try:
                # Ensure input buffer is clear before sending
                if self.serial_conn.in_waiting > 0:
                    self.serial_conn.reset_input_buffer()
                    print(f"[{self.port}] Cleared {self.serial_conn.in_waiting} bytes from input buffer before sending.")

                # Send command
                # print(f"[{self.port}] Sending: {command.strip()}")
                self.serial_conn.write(command.encode('utf-8'))
                self.serial_conn.flush()

                # Read response line by line with timeout
                response_line = ""
                start_time = time.monotonic()
                # Read until newline or timeout
                while time.monotonic() - start_time < self.conn_params['timeout'] + 0.5: # Generous timeout
                    if self.serial_conn.in_waiting > 0:
                        try:
                            byte = self.serial_conn.read(1)
                            if not byte: break # Should not happen if in_waiting > 0
                            char = byte.decode('utf-8', errors='ignore')
                            if char == '\n':
                                break # End of line found
                            response_line += char
                        except serial.SerialException as read_e:
                             print(f"[{self.port}] Read error: {read_e}")
                             # Treat read error as a communication failure, trigger retry
                             raise ConnectionError(f"Read error: {read_e}") # Raise specific error
                    # time.sleep(0.01) # Small sleep to prevent busy-waiting

                response_line = response_line.strip()
                # print(f"[{self.port}] Received: '{response_line}'")

                if response_line == self.RESP_OK:
                    return True, "Success"
                elif response_line.startswith(self.RESP_ERR_PREFIX):
                    error_msg = response_line.split(self.RESP_ERR_PREFIX, 1)[-1].strip()
                    return False, f"Board Error: {error_msg}"
                else:
                    # No valid response or timeout
                    raise TimeoutError("Timeout or invalid response") # Raise specific error

            except (serial.SerialTimeoutException, TimeoutError, ConnectionError) as e:
                retries += 1
                self.last_error = f"Comm Error (Retry {retries}/{self.max_retries}): {e}"
                print(f"[{self.port}] {self.last_error}")
                if retries >= self.max_retries:
                    print(f"[{self.port}] Max retries exceeded. Marking as disconnected.")
                    self._disconnect_impl() # Disconnect after max retries
                    return False, self.last_error
                time.sleep(0.5 * retries) # Wait longer after each retry
                # Attempt to reconnect before next retry
                print(f"[{self.port}] Attempting to reconnect...")
                connected, msg = self._connect_impl()
                if not connected:
                     print(f"[{self.port}] Reconnect failed: {msg}. Aborting command.")
                     return False, f"Reconnect failed: {msg}" # Abort if reconnect fails

            except serial.SerialException as e:
                self.last_error = f"Serial Error: {str(e)}"
                print(f"[{self.port}] {self.last_error}. Disconnecting.")
                self._disconnect_impl() # Disconnect on serial error
                return False, self.last_error
            except Exception as e:
                self.last_error = f"Unexpected Send/Receive Error: {str(e)}"
                print(f"[{self.port}] {self.last_error}. Disconnecting.")
                self._disconnect_impl() # Disconnect on unexpected error
                return False, self.last_error

        return False, "Send/Receive failed after retries" # Should not be reached


    def _send_command_impl(self, duty_values, board_idx):
        """Internal implementation of send_command operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl()[0]: # Check only success boolean
                    return False, self.last_error

            command = self.CMD_SETALL
            for val in duty_values:
                command += f" {val}"
            command += "\n"
            success, message = self._send_receive(command)
            # Update internal state if needed (e.g., last sent values) - not strictly necessary here
            return success, message

    def _set_fan_speed_impl(self, percentage, board_idx):
        """Internal implementation of set_fan_speed operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl()[0]:
                    return False, self.last_error

            command = f"{self.CMD_FAN_SET} {percentage}\n"
            success, message = self._send_receive(command)
            if success:
                self.fan_speed = percentage
                self.fan_enabled = percentage > 0
            return success, message

    def start_command_processor(self):
        """Start the background command processor thread"""
        with self.lock:
            if not self.command_processor_running:
                self.stop_event.clear() # Ensure stop event is clear
                self.command_processor_running = True
                self.command_processor_thread = threading.Thread(
                    target=self._process_command_queue,
                    daemon=True,
                    name=f"CmdProc-{self.port}"
                )
                self.command_processor_thread.start()
                print(f"[{self.port}] Command processor started.")

    def stop_command_processor(self):
        """Stop the background command processor thread gracefully"""
        with self.lock:
            if self.command_processor_running:
                print(f"[{self.port}] Stopping command processor...")
                self.stop_event.set() # Signal thread to stop
                # Put a dummy item to unblock queue.get if waiting
                self.command_queue.put((None, None, None))
                if self.command_processor_thread and self.command_processor_thread.is_alive():
                    self.command_processor_thread.join(timeout=2.0) # Wait for thread to finish
                    if self.command_processor_thread.is_alive():
                         print(f"[{self.port}] Warning: Command processor thread did not stop gracefully.")
                self.command_processor_running = False
                self.command_processor_thread = None
                print(f"[{self.port}] Command processor stopped.")

    def _process_command_queue(self):
        """Target function for the command processor thread."""
        while not self.stop_event.is_set():
            try:
                # Wait for a command with a timeout to allow checking stop_event
                try:
                    # board_idx is now passed with the command tuple
                    command_type, args, board_idx = self.command_queue.get(timeout=0.2)
                except queue.Empty:
                    continue # Check stop_event again

                # Check for sentinel value used during stop
                if command_type is None:
                    break

                success = False
                message = "Command not processed"
                extra_info = None # For schedule status, etc.

                # --- Execute Command ---
                if command_type == self.CMD_SETALL:
                    # args should be duty_values list
                    # extra_info might be passed in args tuple if needed, or determined here
                    success, message = self._send_command_impl(args, board_idx)
                elif command_type == self.CMD_FAN_SET:
                    # args should be the percentage
                    success, message = self._set_fan_speed_impl(args, board_idx)

                # --- Report Result via GUI Queue ---
                # Create a CommandComplete action and put it on the GUI queue
                result_action = CommandComplete(
                    board_idx=board_idx,
                    command_type=command_type,
                    success=success,
                    message=message,
                    extra_info=extra_info # Pass any relevant extra info
                )
                self.gui_queue.put(result_action)

                # Mark task as done in the command queue
                self.command_queue.task_done()

            except Exception as e:
                print(f"[{self.port}] Error in command processor loop: {e}")
                # Consider putting an error status on the GUI queue
                self.gui_queue.put(StatusUpdate(f"Cmd Proc Error ({self.port}): {e}", is_error=True))
                time.sleep(0.5) # Avoid rapid looping on persistent error

        print(f"[{self.port}] Command processor thread exiting.")

    def queue_command(self, command_type, args, board_idx):
        """Adds a command to the processing queue."""
        if not self.command_processor_running:
            self.start_command_processor()
        # Pass board_idx along with command and args
        self.command_queue.put((command_type, args, board_idx))


    def send_led_command(self, duty_values, board_idx):
        """Queue command to update LED brightness asynchronously."""
        self.queue_command(self.CMD_SETALL, list(duty_values), board_idx) # Ensure list copy

    def set_fan_speed_command(self, percentage, board_idx):
        """Queue command to set the fan speed asynchronously."""
        self.queue_command(self.CMD_FAN_SET, int(percentage), board_idx)

    def turn_fan_on_command(self, board_idx):
        """Queue command to turn the fan on asynchronously."""
        # Determine speed to turn on (use current if > 0, else default 50)
        speed_to_set = self.fan_speed if self.fan_speed > 0 else 50
        self.set_fan_speed_command(speed_to_set, board_idx)

    def turn_fan_off_command(self, board_idx):
        """Queue command to turn the fan off asynchronously."""
        self.set_fan_speed_command(0, board_idx)

    def cleanup(self):
        """Clean up resources: stop processor, shutdown executor, close port."""
        print(f"[{self.port}] Initiating cleanup...")
        self.stop_command_processor() # Stop thread first

        # Cancel any pending connect/disconnect futures
        # Copy list as cancel might modify it indirectly
        futures_to_cancel = [f for f in self.pending_futures if not f.done()]
        if futures_to_cancel:
             print(f"[{self.port}] Cancelling {len(futures_to_cancel)} pending connection tasks...")
             for future in futures_to_cancel:
                  future.cancel()

        # Shutdown the executor, wait for tasks to finish/cancel
        print(f"[{self.port}] Shutting down executor...")
        self.executor.shutdown(wait=True)
        print(f"[{self.port}] Executor shut down.")

        # Ensure serial connection is closed (redundant if disconnect was called, but safe)
        self._disconnect_impl()
        print(f"[{self.port}] Cleanup complete.")


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

        # Flag to track active background operations (e.g., scan, apply_all)
        self.background_operations = {} # {operation_name: True/False}

        # Cache style configurations
        self.setup_styles()

        # Initialize status variable early so it's available for all methods
        self.status_var = tk.StringVar(value="Initializing...")

        # Pre-cache these patterns once
        self.time_pattern = TIME_PATTERN
        self.serial_mapping_pattern = SERIAL_MAPPING_PATTERN
        self.chamber_num_pattern = CHAMBER_NUM_PATTERN

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

        self.boards = [] # List of BoardConnection objects
        self.board_frames = [] # List of board GUI frames
        self.led_entries = {}  # {(board_idx, channel_name): entry_widget}

        # Direct chamber-to-board mapping for O(1) lookups
        self.chamber_to_board_idx = {}  # {chamber_number: board_idx}
        self.serial_to_board_idx = {}  # {serial_number: board_idx}

        # Track master light state
        self.master_on = True
        self.saved_values = {}  # To store UI values when master toggles OFF

        # Track master fan state
        self.fans_on = False # Assume fans start OFF until set otherwise
        self.fan_speed_var = tk.StringVar(value="50")

        # --- Scheduling related variables ---
        # Structure: {board_idx: {channel_name: {"on_time": str, "off_time": str, "enabled": bool, "active": bool}}}
        self.channel_schedules = {}
        # Structure: {(board_idx, channel_name, "on"/"off"): entry_widget}
        self.channel_time_entries = {}
        # Structure: {(board_idx, channel_name): BooleanVar}
        self.channel_schedule_vars = {}
        # Structure: {(board_idx, channel_name): schedule_frame_widget} - For visual feedback
        self.channel_schedule_frames = {}
        # --- End Scheduling variables ---

        self.scheduler_running = False
        self.adaptive_check_timer = None  # Store reference to scheduled timer

        # Optimization: Cache for last schedule check state per channel
        # Structure: {(board_idx, channel_name): {"active": bool, "last_check": timestamp}}
        self.last_schedule_state = {}

        # Optimization: Set default scheduler check interval (in milliseconds)
        self.scheduler_check_interval = self.timings['scheduler_default']

        # Batching status updates to reduce flickering
        self.status_update_batch = []
        self.status_update_timer = None

        # Cache fonts to avoid creating new font objects repeatedly
        self.create_font_cache()

        # Cache colors for better performance
        self.create_color_cache()

        # Cache chamber mapping
        self.chamber_mapping = {}  # {serial_number: chamber_number}
        self.reverse_chamber_mapping = {}  # {chamber_number: serial_number}
        self.load_chamber_mapping() # Load mapping early

        # Pagination variables
        self.current_page = 0
        self.boards_per_page = self.board_layout['boards_per_page']

        # Create and cache validation commands
        self.setup_validation_commands()

        # --- Initialize GUI ---
        self.create_gui()

        # Initialize serial port cache (can be done lazily or at start)
        self.initialize_port_cache()

        # Start the scheduler (if desired on startup)
        # self.start_scheduler() # Optional: Start scheduler automatically

        # Start periodic queue processing
        self.process_gui_queue()

        # Initial scan for boards
        self.scan_boards() # Start scan after GUI is built

        self.set_status("Ready.") # Final status after init


    def setup_styles(self):
        """Setup and cache TTK styles"""
        self.style = ttk.Style()
        try:
            # Try a theme known to work well on multiple platforms
            self.style.theme_use('clam')
        except tk.TclError:
            # Fallback to default if 'clam' is not available
            print("Warning: 'clam' theme not available, using default.")
            pass # Use default theme

        # Create and cache common styles
        self.style.configure('Header.TLabel', font=self.cached_fonts['header'])
        self.style.configure('Subheader.TLabel', font=self.cached_fonts['subheader'])
        self.style.configure('Success.TLabel', foreground=self.cached_colors['success'])
        self.style.configure('Error.TLabel', foreground=self.cached_colors['error'])
        self.style.configure('Warning.TLabel', foreground=self.cached_colors['warning'])

        # Button styles (consider platform consistency)
        # self.style.configure('Primary.TButton', background='#4CAF50') # May not work reliably everywhere
        # self.style.configure('Secondary.TButton', background='#2196F3')
        # self.style.configure('Danger.TButton', background='#F44336')

        # Style for schedule frames - used to change background based on state
        self.style.configure('ScheduleBase.TFrame', background=self.cached_colors['schedule_frame_bg'], borderwidth=1, relief="groove")
        self.style.configure('ActiveSchedule.TFrame', background=self.cached_colors['active_bg'])
        self.style.configure('InactiveSchedule.TFrame', background=self.cached_colors['inactive_bg'])


    def create_font_cache(self):
        """Create cached font configurations"""
        # Use common cross-platform fonts if possible
        base_font = "Helvetica" # Or "Arial", "Segoe UI"
        self.cached_fonts = {
            'header': (base_font, 16, 'bold'),
            'subheader': (base_font, 12, 'bold'),
            'subheader_small': (base_font, 10, 'bold'),
            'normal': (base_font, 10, 'normal'),
            'small': (base_font, 8, 'normal'),
            'monospace': ('Courier', 10, 'normal'),
            'button': (base_font, 10, 'normal'),
            'status': (base_font, 9, 'normal'),
            'schedule_label': (base_font, 8, 'normal'), # Smaller font for schedule labels
            'schedule_entry': (base_font, 8, 'normal')  # Smaller font for schedule entries
        }

    def create_color_cache(self):
        """Cache colors for better performance"""
        # Use the pre-defined UI_COLORS dictionary
        self.cached_colors = UI_COLORS

    def setup_validation_commands(self):
        """Set up and cache validation commands"""
        # Register validation functions with the Tkinter interpreter
        vcmd_percentage = (self.root.register(self.validate_percentage), '%P') # %P passes the proposed value
        # vcmd_time = (self.root.register(self.validate_time_format), '%P') # Use for direct validation if needed

        self.validation_commands = {
            'percentage': vcmd_percentage,
            # 'time': vcmd_time # Using visual validation with trace instead
        }

    def initialize_port_cache(self):
        """Initialize the serial port cache for faster board detection"""
        global CACHED_PORT_INFO, BOARD_SERIAL_CACHE
        print("Initializing port cache...")
        CACHED_PORT_INFO = {}
        BOARD_SERIAL_CACHE = {}
        try:
            # Use list_ports.comports() for potentially more reliable info
            for port_info in list_ports.comports():
                # Check for XIAO RP2040 VID/PID
                if port_info.vid == 0x2E8A and port_info.pid == 0x0005:
                    if port_info.serial_number: # Only cache if serial number is available
                        CACHED_PORT_INFO[port_info.device] = {
                            'serial_number': port_info.serial_number,
                            'description': port_info.description,
                            'hwid': port_info.hwid,
                            'device': port_info.device
                        }
                        BOARD_SERIAL_CACHE[port_info.serial_number] = port_info.device
                        print(f"Cached port: {port_info.device}, SN: {port_info.serial_number}")
            print(f"Port cache initialized with {len(CACHED_PORT_INFO)} entries.")
        except Exception as e:
            self.set_status(f"Error initializing port cache: {e}", is_error=True)
            print(f"Error initializing port cache: {e}")


    def load_chamber_mapping(self):
        """Load the chamber to serial number mapping from the text file"""
        self.chamber_mapping = {}
        self.reverse_chamber_mapping = {}
        print(f"Attempting to load chamber mapping from: {SERIAL_MAPPING_FILE}")
        try:
            if os.path.exists(SERIAL_MAPPING_FILE):
                with open(SERIAL_MAPPING_FILE, 'r') as f:
                    line_num = 0
                    for line in f:
                        line_num += 1
                        line = line.strip()
                        if not line or line.startswith('#'): # Ignore empty lines and comments
                            continue

                        # Parse the chamber:serial format using cached regex
                        match = self.serial_mapping_pattern.match(line)
                        if match:
                            try:
                                chamber_num = int(match.group(1))
                                serial_num = match.group(2).strip()
                                if not serial_num:
                                     raise ValueError("Serial number cannot be empty")

                                # Store the mapping both ways for easy lookup
                                self.chamber_mapping[serial_num] = chamber_num
                                self.reverse_chamber_mapping[chamber_num] = serial_num
                            except ValueError as ve:
                                print(f"Warning: Invalid format in mapping file line {line_num}: '{line}' - {ve}")
                            except Exception as parse_e:
                                print(f"Warning: Error parsing mapping file line {line_num}: '{line}' - {parse_e}")
                        else:
                             print(f"Warning: Skipping malformed line {line_num} in mapping file: '{line}'")


                print(f"Loaded chamber mapping for {len(self.chamber_mapping)} chambers.")
                self.set_status(f"Loaded mapping for {len(self.chamber_mapping)} chambers.")
            else:
                warning_msg = f"Chamber mapping file not found: {SERIAL_MAPPING_FILE}"
                print(warning_msg)
                self.set_status(warning_msg, is_error=True)
                messagebox.showwarning("Mapping File Missing",
                                       f"The chamber mapping file was not found at:\n{SERIAL_MAPPING_FILE}\n\n"
                                       "Chamber numbers may not be assigned correctly.")
        except Exception as e:
            error_msg = f"Error loading chamber mapping: {str(e)}"
            self.set_status(error_msg, is_error=True)
            print(error_msg)

    def create_gui(self):
        """Create the main GUI layout"""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1) # Allow main content area to expand

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
        scheduler_button.pack(anchor=tk.CENTER) # Center the scheduler button


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
            # Place frames on top of each other
            page_frame.place(x=0, y=0, relwidth=1, relheight=1)

            # Configure grid for this page (rows/cols defined in BOARD_LAYOUT)
            for c in range(self.board_layout['cols_per_page']):
                # Give equal weight and a minimum size to prevent collapsing
                page_frame.columnconfigure(c, weight=1, minsize=180)
            for r in range(self.board_layout['rows_per_page']):
                # Give equal weight and a minimum size
                page_frame.rowconfigure(r, weight=1, minsize=250)

            self.page_frames[page_id] = page_frame

        # Initially raise page 0
        if 0 in self.page_frames:
            self.page_frames[0].tkraise()
        else:
             print("Warning: Page frame 0 not created.")


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


    def on_closing(self):
        """Clean up resources and close the application"""
        print("Closing application...")
        if messagebox.askokcancel("Quit", "Do you want to quit? This will stop all schedules and turn off lights/fans."):
            # Cancel any scheduled after() calls
            if self.adaptive_check_timer:
                print("Cancelling scheduler timer...")
                self.root.after_cancel(self.adaptive_check_timer)
                self.adaptive_check_timer = None

            if self.status_update_timer:
                print("Cancelling status update timer...")
                self.root.after_cancel(self.status_update_timer)
                self.status_update_timer = None

            # Stop the scheduler explicitly
            self.scheduler_running = False
            print("Scheduler stopped flag set.")

            # Turn off all lights and fans before closing connections
            print("Turning off all lights and fans...")
            off_threads = []
            num_boards = len(self.boards)
            completion_counter = {'count': 0}
            lock = threading.Lock()

            def off_callback(success, msg, idx=None):
                 with lock:
                      completion_counter['count'] += 1
                      # Optional: Log success/failure of individual turn-off commands
                      if not success:
                           print(f"Warning: Failed to turn off board {idx}: {msg}")
                      if completion_counter['count'] == num_boards * 2: # Expect 2 commands per board (lights off, fans off)
                           print("Finished sending OFF commands.")
                           # Proceed with cleanup after commands sent (don't wait for callbacks strictly)

            for i, board in enumerate(self.boards):
                 # Queue commands directly to board's queue
                 board.queue_command(board.CMD_SETALL, board.ZERO_DUTY_CYCLES, i)
                 board.queue_command(board.CMD_FAN_SET, 0, i)

            # Give commands a moment to be sent (adjust time if needed)
            time.sleep(1.0)

            # Clean up board connections (includes stopping command processors)
            print(f"Cleaning up {len(self.boards)} board connections...")
            cleanup_threads = []
            for i, board in enumerate(self.boards):
                print(f"Initiating cleanup for board {i} (Port: {board.port})...")
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
        else:
             print("Quit cancelled.")


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
        num_boards_total = len(self.boards)
        if num_boards_total == 0: # No boards, disable navigation
             self.page_label.config(text="No Boards Found")
             self.prev_button.config(state=tk.DISABLED)
             self.next_button.config(state=tk.DISABLED)
             if 0 in self.page_frames:
                 self.page_frames[0].tkraise() # Show empty page 0
             return

        num_pages = (num_boards_total + self.boards_per_page - 1) // self.boards_per_page
        current_page_idx = self.current_page

        # Ensure current page index is valid
        if not (0 <= current_page_idx < num_pages):
             print(f"Warning: Invalid current page index {current_page_idx}. Resetting to 0.")
             self.current_page = 0
             current_page_idx = 0

        # Raise the correct page frame
        if current_page_idx in self.page_frames:
            self.page_frames[current_page_idx].tkraise()
        else:
             print(f"Error: Page frame for index {current_page_idx} not found.")
             # Fallback: raise page 0 if it exists
             if 0 in self.page_frames: self.page_frames[0].tkraise()


        # Update page label
        start_board_idx = current_page_idx * self.boards_per_page
        end_board_idx = min(start_board_idx + self.boards_per_page, num_boards_total)

        # Get actual chamber numbers if available, otherwise use index+1
        try:
            # Ensure indices are valid before accessing self.boards
            if 0 <= start_board_idx < num_boards_total and 0 <= end_board_idx -1 < num_boards_total:
                 start_num = self.boards[start_board_idx].chamber_number or (start_board_idx + 1)
                 end_num = self.boards[end_board_idx - 1].chamber_number or end_board_idx
                 self.page_label.config(text=f"Chambers {start_num}-{end_num} (Page {current_page_idx + 1}/{num_pages})")
            else:
                 # Handle edge case where indices might be invalid after calculation
                 self.page_label.config(text=f"Page {current_page_idx + 1}/{num_pages}")
        except IndexError:
             # Fallback if accessing self.boards fails
             self.page_label.config(text=f"Page {current_page_idx + 1}/{num_pages}")


        # Update navigation button states
        self.prev_button.config(state=tk.NORMAL if current_page_idx > 0 else tk.DISABLED)
        self.next_button.config(state=tk.NORMAL if current_page_idx < num_pages - 1 else tk.DISABLED)

        # Update status message (optional)
        # self.set_status(f"Displaying chambers {start_num}-{end_num}")

    def create_board_frames(self):
        """Create frames for each detected board, sorted by chamber number"""
        print("Creating board frames...")
        # --- Clear existing elements ---
        for frame in self.board_frames:
            try: frame.destroy()
            except tk.TclError: pass # Ignore if already destroyed
        self.board_frames = []

        # Clear widget dictionaries safely
        self.led_entries.clear()
        self.channel_time_entries.clear()
        self.channel_schedule_vars.clear()
        self.channel_schedule_frames.clear() # Clear schedule frame references

        # Clear lookup dictionaries
        self.chamber_to_board_idx.clear()
        self.serial_to_board_idx.clear()

        # Clear schedule data (will be repopulated or loaded)
        # Keep existing schedule data if reloading? No, clear for consistency after scan.
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
                 print(f"Error: Page frame {page_id} does not exist for board {i} (SN: {serial_number}). Skipping frame creation.")
                 continue # Skip board if page frame is missing

            page_frame = self.page_frames[page_id]
            # Calculate grid position within the page frame
            row_in_page = (i % self.boards_per_page) // self.board_layout['cols_per_page']
            col_in_page = (i % self.boards_per_page) % self.board_layout['cols_per_page']

            # --- Create Main Board Frame ---
            frame_text = f"Chamber {chamber_number}" if chamber_number else f"Board {i+1} (SN: {serial_number or 'N/A'})"
            board_frame = ttk.LabelFrame(page_frame, text=frame_text, padding=self.board_layout['frame_padding'])
            board_frame.grid(row=row_in_page, column=col_in_page, padx=self.board_layout['padding'], pady=self.board_layout['padding'], sticky=(tk.N, tk.W, tk.E, tk.S))
            self.board_frames.append(board_frame) # Add frame to list

            # Configure internal grid for the board frame
            board_frame.columnconfigure(0, weight=1) # Allow content to expand horizontally

            # --- Initialize schedule data structure for this board ---
            self.channel_schedules[i] = {}


            # --- Create LED Control Sections (Iterate through channels) ---
            for led_row, channel_name in enumerate(LED_CHANNEL_NAMES):
                channel_idx = LED_CHANNELS[channel_name]

                # Frame for a single LED channel's controls
                channel_frame = ttk.Frame(board_frame, padding=(5, 2))
                # Grid within the board_frame
                channel_frame.grid(row=led_row, column=0, sticky=(tk.W, tk.E), pady=1)

                # Configure columns within the channel frame for layout
                channel_frame.columnconfigure(0, weight=0) # Color Indicator
                channel_frame.columnconfigure(1, weight=0) # Channel Name Label
                channel_frame.columnconfigure(2, weight=0) # Intensity Entry
                channel_frame.columnconfigure(3, weight=0) # % Label
                channel_frame.columnconfigure(4, weight=1) # Spacer (pushes schedule right)
                channel_frame.columnconfigure(5, weight=0) # Schedule Frame

                # 1. Color Indicator
                color_frame = ttk.Frame(channel_frame, width=15, height=15, relief=tk.SUNKEN, borderwidth=1)
                color_frame.grid(column=0, row=0, padx=(0, 5), sticky=tk.W)
                # Use a standard Label, easier to control background
                color_label = tk.Label(color_frame, bg=LED_COLORS.get(channel_name, "#CCCCCC"), width=2, height=1)
                color_label.pack(fill=tk.BOTH, expand=True)

                # 2. Channel Name Label
                ttk.Label(channel_frame, text=f"{channel_name}:", width=8, anchor=tk.W).grid(column=1, row=0, sticky=tk.W)

                # 3. Intensity Entry
                value_var = tk.StringVar(value="0") # Default to 0
                entry = ttk.Entry(
                    channel_frame,
                    width=self.widget_sizes['entry_width'],
                    textvariable=value_var,
                    validate='key', # Validate on key press
                    validatecommand=self.validation_commands['percentage'],
                    font=self.cached_fonts['normal']
                )
                entry.grid(column=2, row=0, sticky=tk.W, padx=2)
                self.led_entries[(i, channel_name)] = entry

                # 4. Percentage Label
                ttk.Label(channel_frame, text="%", font=self.cached_fonts['small']).grid(column=3, row=0, sticky=tk.W, padx=(0, 10))

                # --- 5. Scheduling Section (per channel) ---
                # Initialize internal data structure first
                self.channel_schedules[i][channel_name] = {
                     "on_time": "08:00",
                     "off_time": "00:00",
                     "enabled": False,
                     "active": True # Default to active (ON) unless schedule dictates otherwise
                }

                # Create the schedule frame widget
                # Use 'ScheduleBase.TFrame' initially, will be updated by scheduler
                schedule_outer_frame = ttk.Frame(channel_frame, style='ScheduleBase.TFrame')
                schedule_outer_frame.grid(column=5, row=0, sticky=tk.E, padx=(5,0))
                # Store reference to the frame for visual updates
                self.channel_schedule_frames[(i, channel_name)] = schedule_outer_frame


                # ON Time
                ttk.Label(schedule_outer_frame, text="On:", font=self.cached_fonts['schedule_label']).grid(column=0, row=0, padx=(5, 2), pady=1, sticky=tk.W)
                on_time_var = tk.StringVar(value=self.channel_schedules[i][channel_name]['on_time'])
                on_time_entry = ttk.Entry(
                    schedule_outer_frame,
                    width=self.widget_sizes['time_entry_width'],
                    textvariable=on_time_var,
                    font=self.cached_fonts['schedule_entry']
                )
                on_time_entry.grid(column=1, row=0, padx=(0, 5), pady=1)
                self.channel_time_entries[(i, channel_name, "on")] = on_time_entry
                # Add trace for visual validation feedback
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
                # Add trace for visual validation feedback
                off_time_var.trace_add("write", lambda *args, b=i, c=channel_name, v=off_time_var, e=off_time_entry:
                                      self.validate_time_entry_visual(b, c, "off", v.get(), e))


                # Enable Checkbox
                schedule_var = tk.BooleanVar(value=self.channel_schedules[i][channel_name]['enabled'])
                schedule_check = ttk.Checkbutton(
                    schedule_outer_frame,
                    text="En", # Short text for space
                    variable=schedule_var,
                    # Pass board index AND channel name to the command
                    command=lambda b_idx=i, c_name=channel_name: self.update_channel_schedule(b_idx, c_name)
                )
                # Place checkbox next to time entries
                schedule_check.grid(column=2, row=0, rowspan=2, padx=(0, 5), pady=1, sticky=tk.W)
                self.channel_schedule_vars[(i, channel_name)] = schedule_var

            # --- Individual Apply Button (per board) ---
            apply_button = ttk.Button(
                board_frame,
                text="Apply Chamber Settings",
                command=lambda b_idx=i: self.apply_board_settings(b_idx)
            )
            # Place below all channel frames using grid
            apply_button.grid(row=len(LED_CHANNEL_NAMES), column=0, pady=(10, 5), sticky=(tk.W, tk.E))

        # --- Finalize GUI Update ---
        self.current_page = 0 # Reset to first page
        self.update_page_display() # Update navigation buttons and label
        print(f"Board frames created for {len(self.boards)} boards.")


    def toggle_all_lights(self):
        """Toggle all lights on or off on all boards using master control."""
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
        status_msg = self.cmd_messages['lights_on'] if self.master_on else self.cmd_messages['lights_off']
        self.set_status(status_msg + "...")

        if self.master_on:
            # --- Turning ON ---
            # Re-apply settings considering schedules.
            # apply_all_settings handles status updates and background work.
            print("Master Toggle: Turning ON - Applying all settings...")
            self.apply_all_settings()
        else:
            # --- Turning OFF ---
            print("Master Toggle: Turning OFF - Saving UI values and sending zeros...")
            # Save current UI values first (in case they are needed later)
            self.saved_values.clear() # Clear previous saved values
            for board_idx in range(len(self.boards)):
                self.saved_values[board_idx] = {}
                for channel_name in LED_CHANNEL_NAMES:
                    key = (board_idx, channel_name)
                    entry_widget = self.led_entries.get(key)
                    if entry_widget:
                        try:
                            # Check if widget exists before getting value
                            if entry_widget.winfo_exists():
                                self.saved_values[board_idx][channel_name] = entry_widget.get()
                            else:
                                self.saved_values[board_idx][channel_name] = "0" # Default if widget destroyed
                        except (ValueError, KeyError, tk.TclError):
                            self.saved_values[board_idx][channel_name] = "0" # Default on error
                    else:
                         self.saved_values[board_idx][channel_name] = "0" # Default if no entry

            # Send zeros to all boards using their command queues
            num_boards = len(self.boards)
            print(f"Master Toggle: Queuing OFF command for {num_boards} boards.")
            for board_idx, board in enumerate(self.boards):
                 # Queue the SETALL command with zeros
                 board.send_led_command(self.zero_duty_cycle, board_idx)
            # Status update will happen when CommandComplete messages are processed


    def toggle_scheduler(self):
        """Enable or disable the scheduler globally."""
        if self.scheduler_running:
            self.scheduler_running = False
            self.scheduler_button_var.set("Start Scheduler")
            self.set_status(self.cmd_messages['scheduler_stop'])
            # Cancel any pending scheduled checks
            if self.adaptive_check_timer:
                self.root.after_cancel(self.adaptive_check_timer)
                self.adaptive_check_timer = None
            print("Scheduler stopped.")
        else:
            self.scheduler_running = True
            self.scheduler_button_var.set("Stop Scheduler")
            self.set_status(self.cmd_messages['scheduler_start'])
            print("Scheduler started.")
            # Start the first check immediately
            self.schedule_check() # Will schedule subsequent checks via root.after

    def start_scheduler(self):
        """Start the scheduler using Tkinter's after() method if not already running."""
        if not self.scheduler_running:
            self.toggle_scheduler()

    def schedule_check(self):
        """Periodic scheduler check using after() instead of a continuous thread."""
        # Cancel previous timer if it exists (prevents duplicates if called manually)
        if self.adaptive_check_timer:
            self.root.after_cancel(self.adaptive_check_timer)
            self.adaptive_check_timer = None

        if not self.scheduler_running:
            # print("Scheduler check skipped (not running).")
            return

        # print(f"Running schedule check at {datetime.now()}") # DEBUG

        # --- Run check logic in background thread ---
        threading.Thread(
            target=self._schedule_check_worker,
            daemon=True,
            name="SchedulerCheckWorker"
        ).start()

        # --- Schedule the next check ---
        # The worker thread calculates the adaptive interval and updates self.scheduler_check_interval.
        # Schedule the next call using the potentially updated interval.
        # Use max to ensure a minimum delay, preventing overly rapid checks.
        next_check_delay = max(50, self.scheduler_check_interval)
        # print(f"Scheduling next check in {next_check_delay} ms") # DEBUG
        self.adaptive_check_timer = self.root.after(next_check_delay, self.schedule_check)


    def _schedule_check_worker(self):
        """Background worker thread for schedule checking."""
        current_datetime = datetime.now()
        current_time_str = current_datetime.strftime("%H:%M")
        min_time_diff = float('inf')  # Track time to next scheduled event in minutes
        boards_needing_update = set() # Track boards where at least one channel changed state

        # Iterate through a copy of keys to avoid issues if dict changes during iteration (unlikely here)
        board_indices = list(self.channel_schedules.keys())

        for board_idx in board_indices:
            # Check if board index is still valid (boards list might change)
            if board_idx >= len(self.boards): continue

            channels = self.channel_schedules.get(board_idx, {})
            channel_names = list(channels.keys())

            for channel_name in channel_names:
                schedule_info = channels.get(channel_name)
                if not schedule_info or not schedule_info.get("enabled", False):
                    continue # Skip if no info or not enabled

                on_time = schedule_info.get("on_time", "")
                off_time = schedule_info.get("off_time", "")

                # Validate times before proceeding
                if not self.validate_time_format(on_time) or not self.validate_time_format(off_time):
                    continue # Skip channels with invalid time formats

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
                    last_state_info = self.last_schedule_state.get(cache_key, {})
                    prev_state = last_state_info.get("active", None)

                    # --- Check if state changed ---
                    if prev_state is None or prev_state != should_be_active:
                        # State changed! Update cache and queue GUI update
                        self.last_schedule_state[cache_key] = {
                            "active": should_be_active,
                            "last_check": current_datetime
                        }

                        # Queue update for the main thread to handle internal state and visuals
                        self.gui_queue.put(SchedulerUpdate(board_idx, channel_name, should_be_active))
                        boards_needing_update.add(board_idx) # Mark board for re-applying settings

                        # Log the change (optional, can be verbose)
                        # chamber_num = self.boards[board_idx].chamber_number or (board_idx + 1)
                        # action = "ON" if should_be_active else "OFF"
                        # print(f"Scheduler Update: Chamber {chamber_num}, Channel {channel_name} -> {action}")

                except Exception as e:
                    # Log error but continue processing other channels/boards
                    print(f"Error processing schedule for board {board_idx}, channel {channel_name}: {str(e)}")


        # --- Apply settings to boards where state changed ---
        # Queue the application in the main thread to avoid direct hardware calls here
        if boards_needing_update:
            # print(f"Scheduler detected changes, queuing updates for boards: {boards_needing_update}") # DEBUG
            # Use root.after to ensure it runs in the main thread after current event processing
            # Pass a copy of the set as a list
            self.root.after(0, lambda boards=list(boards_needing_update): self.apply_settings_to_multiple_boards(boards))


        # --- Calculate adaptive timer interval ---
        # This update happens directly in the worker thread, which is generally safe
        # as only this worker modifies self.scheduler_check_interval.
        self.scheduler_check_interval = self.calculate_adaptive_interval(min_time_diff)
        # print(f"Adaptive interval calculated: {self.scheduler_check_interval} ms (min_diff: {min_time_diff} mins)") # DEBUG

    def calculate_adaptive_interval(self, min_time_diff_minutes):
        """Calculate adaptive timer interval based on time to next event."""
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


    def scan_boards(self):
        """Detect and initialize connections to XIAO RP2040 boards."""
        # Avoid concurrent scans
        if self.background_operations.get('scan', False):
             print("Scan already in progress.")
             self.set_status("Scan already in progress...")
             return

        print("Starting board scan...")
        self.set_status(self.cmd_messages['scan_start'])
        self.background_operations['scan'] = True # Set flag

        # --- Disconnect existing boards first (in background) ---
        # This prevents blocking the GUI while waiting for cleanup
        threading.Thread(target=self._disconnect_all_boards_async, daemon=True).start()
        # Note: The scan worker will start after this thread finishes or times out.

    def _disconnect_all_boards_async(self):
        """Helper to disconnect all current boards in the background."""
        print("Disconnecting existing boards...")
        disconnect_threads = []
        boards_to_disconnect = list(self.boards) # Copy list
        self.boards = [] # Clear main list immediately

        for i, board in enumerate(boards_to_disconnect):
             print(f"Initiating cleanup for old board {i} ({board.port})...")
             thread = threading.Thread(target=board.cleanup, name=f"Disconnect-{board.port}")
             disconnect_threads.append(thread)
             thread.start()

        # Wait for disconnections to complete
        all_disconnected = True
        for thread in disconnect_threads:
             thread.join(timeout=3.0) # Timeout for disconnection
             if thread.is_alive():
                  print(f"Warning: Disconnect thread {thread.name} timed out.")
                  all_disconnected = False

        print(f"Finished disconnecting old boards (Success: {all_disconnected}).")

        # --- Clear GUI (must run in main thread) ---
        self.root.after(0, self._clear_gui_elements)

        # --- Start the actual scan worker ---
        print("Starting board scan worker...")
        threading.Thread(
            target=self._scan_boards_worker,
            daemon=True,
            name="BoardScanWorker"
        ).start()


    def _clear_gui_elements(self):
        """Clears GUI elements related to boards (runs in main thread)."""
        print("Clearing GUI elements...")
        for frame in self.board_frames:
            try: frame.destroy()
            except tk.TclError: pass
        self.board_frames = []
        self.led_entries.clear()
        self.channel_time_entries.clear()
        self.channel_schedule_vars.clear()
        self.channel_schedule_frames.clear()
        self.chamber_to_board_idx.clear()
        self.serial_to_board_idx.clear()
        self.channel_schedules.clear()
        self.last_schedule_state.clear()

        # Reset master button state (if applicable)
        self.master_on = True # Default to ON after scan
        self.master_button_var.set("All Lights OFF")
        self.saved_values = {}
        print("GUI elements cleared.")
        # Update display in case no boards are found later
        self.update_page_display()


    def _scan_boards_worker(self):
        """Background worker thread for scanning boards."""
        detected_boards_info = []
        error_msg = None
        boards_created = []
        try:
            # Reload chamber mapping in case it changed
            # self.load_chamber_mapping() # Requires main thread access or careful locking if done here

            print("Detecting XIAO boards...")
            # Detect connected boards using cached info or fresh scan
            detected_boards_info = self.detect_xiao_boards() # Returns list of [port, serial, chamber]

            if not detected_boards_info:
                print("No boards found.")
                # Send empty list via queue
                self.gui_queue.put(BoardsDetected([]))
            else:
                print(f"Found {len(detected_boards_info)} potential boards. Creating connections...")
                # Create board connections (pass gui_queue)
                for port, serial_number, chamber_number in detected_boards_info:
                    if not port or not serial_number:
                         print(f"Warning: Skipping board with incomplete info (Port: {port}, SN: {serial_number})")
                         continue
                    # Pass the GUI queue to the BoardConnection instance
                    boards_created.append(BoardConnection(port, serial_number, self.gui_queue, chamber_number))

                print(f"Created {len(boards_created)} BoardConnection objects.")
                # Send result (list of BoardConnection objects) to main thread
                self.gui_queue.put(BoardsDetected(boards_created))

        except Exception as e:
            error_msg = f"Error during board scan: {str(e)}"
            print(error_msg)
            # Send error via queue
            self.gui_queue.put(BoardsDetected([], error=error_msg))
        finally:
            # Clear operation flag
            self.background_operations['scan'] = False
            print("Board scan worker finished.")


    def detect_xiao_boards(self):
        """Detect connected XIAO RP2040 boards and assign chamber numbers."""
        results = []
        print("Running board detection using list_ports...")

        # Refresh port list using list_ports.comports()
        try:
            current_ports_info = list_ports.comports()
            print(f"Found {len(current_ports_info)} serial ports total.")
        except Exception as e:
            print(f"Error listing serial ports: {e}")
            # Queue status update for main thread
            self.gui_queue.put(StatusUpdate(f"Error listing ports: {e}", is_error=True))
            return [] # Return empty list on error

        # Filter for XIAO RP2040 VID/PID
        xiao_ports = [p for p in current_ports_info if p.vid == 0x2E8A and p.pid == 0x0005]
        print(f"Found {len(xiao_ports)} ports matching XIAO VID/PID.")

        # Process detected XIAO ports
        assigned_temp_ids = set() # Track temp IDs assigned in this scan
        for port_info in xiao_ports:
            serial_number = port_info.serial_number
            port = port_info.device

            if not serial_number:
                 print(f"Warning: Found XIAO on {port} but no serial number. Skipping.")
                 continue

            # Assign chamber number from cached mapping
            chamber_number = self.chamber_mapping.get(serial_number)

            if chamber_number is None:
                warning_msg = f"Warning: Board S/N {serial_number} ({port}) not in mapping."
                print(warning_msg)
                # Assign a temporary ID if no mapping found
                temp_chamber_num = 1000
                # Ensure temp ID is unique within this scan and avoids real chamber numbers
                existing_real_chambers = set(self.chamber_mapping.values())
                while temp_chamber_num in assigned_temp_ids or temp_chamber_num in existing_real_chambers:
                    temp_chamber_num += 1
                chamber_number = temp_chamber_num
                assigned_temp_ids.add(chamber_number)
                warning_msg += f" Assigned Temp ID {chamber_number}"
                print(f"Assigned temporary chamber ID {chamber_number} to S/N {serial_number}")
                # Queue a status update for the main thread
                self.gui_queue.put(StatusUpdate(warning_msg, is_error=True))


            results.append([port, serial_number, chamber_number])
            print(f"Detected Board: Port={port}, SN={serial_number}, Chamber={chamber_number}")


        print(f"Board detection finished. Returning {len(results)} boards.")
        return results


    def validate_percentage(self, P):
        """Validation command for percentage entries (0-100)."""
        # P is the proposed value of the entry if the change is allowed.
        if P == "":
            return True # Allow empty string

        try:
            val = int(P)
            if 0 <= val <= 100:
                return True # Value is valid
            else:
                self.root.bell() # Audible feedback for invalid input
                return False # Value out of range
        except ValueError:
            self.root.bell()
            return False # Not an integer


    def apply_all_settings(self):
        """Apply current UI settings to all connected boards."""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to apply settings to.")
            return

        board_indices = list(range(len(self.boards)))
        if not board_indices:
            return # Should not happen if self.boards is not empty

        # Avoid concurrent "apply all" operations
        if self.background_operations.get('apply_all', False):
             print("Apply all operation already in progress.")
             self.set_status("Apply all operation already in progress...")
             return

        self.set_status(self.cmd_messages['apply_start'] + f" to {len(board_indices)} boards...")
        print(f"Applying settings to all {len(board_indices)} boards.")
        self.background_operations['apply_all'] = True # Set flag

        # Start background thread for applying settings to all boards
        threading.Thread(
            target=self._apply_settings_to_multiple_worker,
            args=(board_indices,), # Pass list of all board indices
            daemon=True,
            name="ApplyAllSettingsWorker"
        ).start()

    def apply_settings_to_multiple_boards(self, board_indices):
         """Helper to apply settings to a list of board indices (called from main thread)."""
         if not board_indices:
              return
         valid_indices = [idx for idx in board_indices if 0 <= idx < len(self.boards)]
         if not valid_indices:
              print(f"Warning: No valid board indices in list: {board_indices}")
              return

         print(f"Queuing application of settings for boards: {valid_indices}")
         self.set_status(f"Applying settings to {len(valid_indices)} boards...") # Update status

         # Start background worker
         threading.Thread(
            target=self._apply_settings_to_multiple_worker,
            args=(valid_indices,), # Pass only valid indices
            daemon=True,
            name=f"ApplySettingsWorker-{valid_indices}"
         ).start()


    def _apply_settings_to_multiple_worker(self, board_indices):
        """Background worker thread for applying settings to a list of boards."""
        num_boards_to_process = len(board_indices)
        print(f"Apply Worker: Starting batch for {num_boards_to_process} boards: {board_indices}")
        processed_count = 0
        # --- Collect UI Data for all relevant boards FIRST (in main thread) ---
        all_ui_data = {} # {board_idx: {channel_name: percentage}}
        collect_result = {'data': {}, 'error': None, 'complete': False}

        def collect_batch_ui_data():
            batch_data = {}
            try:
                for idx in board_indices:
                    if idx >= len(self.boards): continue # Should not happen if list is pre-filtered
                    board_data = {}
                    for channel_name in LED_CHANNEL_NAMES:
                        percentage = 0
                        entry_key = (idx, channel_name)
                        entry_widget = self.led_entries.get(entry_key)
                        if entry_widget:
                             try:
                                  if entry_widget.winfo_exists():
                                       percentage = int(entry_widget.get())
                                       if not (0 <= percentage <= 100): percentage = 0
                                  else: percentage = 0 # Widget destroyed
                             except (ValueError, tk.TclError): percentage = 0
                        board_data[channel_name] = percentage
                    batch_data[idx] = board_data
                collect_result['data'] = batch_data
            except Exception as e:
                 collect_result['error'] = f"Error collecting batch UI data: {e}"
            finally:
                 collect_result['complete'] = True

        self.root.after(0, collect_batch_ui_data)
        timeout = time.time() + 5.0 # 5 sec timeout for collecting all data
        while not collect_result['complete'] and time.time() < timeout:
             time.sleep(0.01)

        if not collect_result['complete'] or collect_result['error']:
             error_msg = collect_result['error'] or "Timeout collecting UI data for batch apply."
             print(f"Apply Worker Error: {error_msg}")
             self.gui_queue.put(StatusUpdate(error_msg, is_error=True))
             # Clear apply_all flag if it was set
             if 'apply_all' in self.background_operations: self.background_operations['apply_all'] = False
             return
        all_ui_data = collect_result['data']
        # --- End UI Data Collection ---


        # --- Process each board ---
        try:
            for board_idx in board_indices:
                if board_idx not in all_ui_data: # Skip if data wasn't collected
                     print(f"Apply Worker: Skipping board {board_idx}, no UI data found.")
                     continue

                board = self.boards[board_idx]
                ui_percentages = all_ui_data[board_idx]
                final_duty_values = [0] * len(LED_CHANNELS)
                schedule_details = []

                # Determine final duty cycles based on schedules
                for channel_idx, channel_name in enumerate(LED_CHANNEL_NAMES):
                    percentage = ui_percentages.get(channel_name, 0)
                    is_scheduled_off = False
                    schedule_info = self.channel_schedules.get(board_idx, {}).get(channel_name, {})

                    if schedule_info.get("enabled", False):
                        current_time = datetime.now().strftime("%H:%M") # Check time for each channel
                        on_time = schedule_info.get("on_time", "")
                        off_time = schedule_info.get("off_time", "")
                        if self.validate_time_format(on_time) and self.validate_time_format(off_time):
                            if not self.is_time_between(current_time, on_time, off_time):
                                is_scheduled_off = True
                                schedule_details.append(f"{channel_name}: OFF")
                            else:
                                schedule_details.append(f"{channel_name}: ON")
                        else:
                            schedule_details.append(f"{channel_name}: Invalid Time")

                    # Set duty cycle
                    if is_scheduled_off:
                        final_duty_values[channel_idx] = 0
                    else:
                        final_duty_values[channel_idx] = self.duty_cycle_lookup.get(percentage, 0)

                # Queue the command for the board
                print(f"Apply Worker: Queuing SETALL for board {board_idx} - Duties: {final_duty_values}")
                # Pass schedule details via the CommandComplete message? No, handled by GUI thread.
                board.send_led_command(final_duty_values, board_idx)
                processed_count += 1
                # Optional delay between queuing commands
                # time.sleep(0.02)

        except Exception as e:
             print(f"Error in apply settings worker loop: {e}")
             self.gui_queue.put(StatusUpdate(f"Error applying settings batch: {e}", is_error=True))
        finally:
            # Clear operation flag ONLY if this was triggered by "Apply All" button
            # How to know? Check the name? No, use the flag.
            if self.background_operations.get('apply_all', False):
                 # Check if all boards in the initial request were processed (or attempted)
                 # This logic might be complex if operations can overlap.
                 # Simple approach: clear flag after worker finishes.
                 self.background_operations['apply_all'] = False
                 # Add a final status update after all commands queued
                 self.gui_queue.put(StatusUpdate(f"Finished queuing settings for {processed_count}/{num_boards_to_process} boards."))

            print(f"Apply Worker: Finished batch processing ({processed_count}/{num_boards_to_process} boards queued).")


    def apply_board_settings(self, board_idx):
        """Apply settings for a specific board (public method called by button)."""
        if board_idx >= len(self.boards):
            messagebox.showerror("Error", f"Invalid board index: {board_idx}")
            return

        chamber_num = self.boards[board_idx].chamber_number or (board_idx + 1)
        self.set_status(f"Applying settings to Chamber {chamber_num}...")
        print(f"Apply button clicked for board index {board_idx} (Chamber {chamber_num})")

        # Apply settings to just this single board
        self.apply_settings_to_multiple_boards([board_idx])


    # _apply_board_settings_worker is now replaced by _apply_settings_to_multiple_worker


    def toggle_all_fans(self):
        """Toggle all fans on or off on all boards."""
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

        # --- Queue commands for all boards ---
        num_boards = len(self.boards)
        if num_boards == 0: return

        print(f"Queuing fan commands for {num_boards} boards (State: {'ON' if self.fans_on else 'OFF'}, Speed: {speed if self.fans_on else 0})")
        for i, board in enumerate(self.boards):
            board.set_fan_speed_command(speed if self.fans_on else 0, i)
        # Status update will happen when CommandComplete messages are processed


    def apply_fan_settings(self):
        """Apply the fan speed from the UI to all boards."""
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

        # --- Queue commands for all boards ---
        num_boards = len(self.boards)
        if num_boards == 0: return

        print(f"Queuing fan speed ({speed}%) commands for {num_boards} boards.")
        for i, board in enumerate(self.boards):
            board.set_fan_speed_command(speed, i)
        # Status update will happen when CommandComplete messages are processed


    def export_settings(self):
        """Export current LED settings and schedules to a JSON file"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to export settings from.")
            return

        # --- Get File Path (Main Thread) ---
        file_path = filedialog.asksaveasfilename(
            initialdir=DEFAULT_DOCUMENTS_PATH,
            defaultextension=".json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Save LED Settings"
        )
        if not file_path:
            self.set_status("Export cancelled.")
            return  # User canceled

        self.set_status(self.cmd_messages['export_start'])

        # --- Collect Data (Main Thread) ---
        settings_to_export = {}
        collection_error = None
        try:
            print("Export: Collecting data from UI...")
            for board_idx, board in enumerate(self.boards):
                chamber_num = board.chamber_number
                board_key = f"chamber_{chamber_num}" if chamber_num is not None else f"board_{board_idx}"
                board_settings = {"intensity": {}, "schedule": {}, "fan": {}}

                # Get intensity settings
                for channel_name in LED_CHANNEL_NAMES:
                    intensity = 0
                    entry_widget = self.led_entries.get((board_idx, channel_name))
                    if entry_widget:
                        try:
                            if entry_widget.winfo_exists(): intensity = int(entry_widget.get())
                        except (ValueError, tk.TclError): intensity = 0
                    board_settings["intensity"][channel_name] = max(0, min(100, intensity)) # Clamp

                # Get schedule settings (per channel)
                board_schedule_data = {}
                if board_idx in self.channel_schedules:
                    for channel_name, schedule_info in self.channel_schedules[board_idx].items():
                        on_time = schedule_info.get("on_time", "08:00")
                        off_time = schedule_info.get("off_time", "00:00")
                        enabled = schedule_info.get("enabled", False)
                        # Get current values from widgets if they exist
                        on_entry = self.channel_time_entries.get((board_idx, channel_name, "on"))
                        off_entry = self.channel_time_entries.get((board_idx, channel_name, "off"))
                        enabled_var = self.channel_schedule_vars.get((board_idx, channel_name))
                        try:
                            if on_entry and on_entry.winfo_exists(): on_time = on_entry.get()
                            if off_entry and off_entry.winfo_exists(): off_time = off_entry.get()
                            if enabled_var: enabled = enabled_var.get()
                        except tk.TclError: pass # Ignore if widget destroyed

                        board_schedule_data[channel_name] = {
                            "on_time": on_time if self.validate_time_format(on_time) else "08:00",
                            "off_time": off_time if self.validate_time_format(off_time) else "00:00",
                            "enabled": bool(enabled)
                        }
                board_settings["schedule"] = board_schedule_data

                # Get fan settings (from BoardConnection object state)
                board_settings["fan"] = { "enabled": board.fan_enabled, "speed": board.fan_speed }
                settings_to_export[board_key] = board_settings
            print("Export: Data collection complete.")
        except Exception as e:
            collection_error = f"Error collecting settings data: {e}"
            print(f"Export Error: {collection_error}")
            self.gui_queue.put(FileOperationComplete('export', False, collection_error))
            return
        # --- End Data Collection ---

        # --- Start Background Thread for Saving ---
        threading.Thread(
            target=self._export_settings_worker,
            args=(file_path, settings_to_export), # Pass collected data
            daemon=True,
            name="ExportSettingsWorker"
        ).start()

    def _export_settings_worker(self, file_path, settings_data):
        """Background worker thread for saving exported settings to file."""
        print(f"Export worker started for path: {file_path}")
        export_error = None
        try:
            print(f"Export worker: Saving data to {file_path}")
            with open(file_path, 'w') as f:
                json.dump(settings_data, f, indent=4, sort_keys=True)
            print("Export worker: Save complete.")
        except Exception as e:
            export_error = f"Error writing to file: {e}"

        # --- Send Result to GUI Queue ---
        if export_error:
            print(f"Export worker error: {export_error}")
            self.gui_queue.put(FileOperationComplete('export', False, export_error))
        else:
            print("Export worker finished successfully.")
            self.gui_queue.put(FileOperationComplete('export', True, file_path)) # Pass file path on success


    def import_settings(self):
        """Import LED settings and schedules from a JSON file"""
        # --- Get File Path (Main Thread) ---
        file_path = filedialog.askopenfilename(
            initialdir=DEFAULT_DOCUMENTS_PATH,
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            title="Import LED Settings"
        )
        if not file_path:
            self.set_status("Import cancelled.")
            return # User canceled

        self.set_status(self.cmd_messages['import_start'])

        # --- Start Background Thread for Reading File ---
        threading.Thread(
            target=self._import_settings_reader_worker,
            args=(file_path,),
            daemon=True,
            name="ImportSettingsReader"
        ).start()

    def _import_settings_reader_worker(self, file_path):
         """Background worker thread for reading the import file."""
         print(f"Import reader worker started for path: {file_path}")
         imported_settings = None
         import_error = None

         # --- Read Settings from File ---
         try:
             print(f"Import reader: Reading file {file_path}")
             with open(file_path, 'r') as f:
                 imported_settings = json.load(f)
             print("Import reader: File read successfully.")

             # Basic validation
             if not isinstance(imported_settings, dict):
                 raise ValueError("Invalid file format: Top level must be a dictionary.")

         except FileNotFoundError:
              import_error = "File not found."
         except json.JSONDecodeError as e:
              import_error = f"Invalid JSON format: {e}"
         except Exception as e:
              import_error = f"Error reading file: {e}"
         # --- End Read File ---

         if import_error:
              print(f"Import reader error: {import_error}")
              self.gui_queue.put(FileOperationComplete('import', False, import_error))
         else:
              # Schedule the UI update in the main thread, passing the loaded data
              print("Import reader: Scheduling UI update.")
              self.root.after(0, lambda data=imported_settings, path=file_path: self._apply_imported_settings_to_ui(data, path))


    def _apply_imported_settings_to_ui(self, imported_settings, file_path):
        """Applies the loaded settings to the UI elements (runs in main thread)."""
        print("Applying imported settings to UI...")
        if not self.boards:
             error_msg = "No boards connected to apply settings to."
             print(error_msg)
             self.gui_queue.put(FileOperationComplete('import', False, error_msg))
             return

        applied_count = 0
        skipped_chambers = set()
        fan_settings_found = False
        apply_error = None

        try:
            # Process each board key in the imported data
            for board_key, board_settings in imported_settings.items():
                board_idx = None
                chamber_num = None

                # Find board index based on key ("chamber_X")
                chamber_match = self.chamber_num_pattern.match(board_key)
                if chamber_match:
                    try:
                        chamber_num = int(chamber_match.group(1))
                        board_idx = self.chamber_to_board_idx.get(chamber_num)
                    except ValueError: pass # Invalid number in key
                # Add fallback for "board_Y" if necessary

                if board_idx is None or board_idx >= len(self.boards):
                    skipped_chambers.add(board_key)
                    continue # Skip if board not found or index out of range

                board = self.boards[board_idx] # Get the BoardConnection object

                # --- Apply Intensity ---
                if "intensity" in board_settings and isinstance(board_settings["intensity"], dict):
                    for channel_name, value in board_settings["intensity"].items():
                        if channel_name in LED_CHANNELS:
                            entry_widget = self.led_entries.get((board_idx, channel_name))
                            if entry_widget:
                                try:
                                    percent_val = int(value)
                                    if 0 <= percent_val <= 100:
                                        if entry_widget.winfo_exists():
                                             entry_widget.delete(0, tk.END)
                                             entry_widget.insert(0, str(percent_val))
                                             applied_count += 1
                                    else:
                                         print(f"Warning (Import): Invalid intensity '{value}' for {board_key}-{channel_name}. Skipping.")
                                except (ValueError, tk.TclError): pass # Ignore errors setting individual entries

                # --- Apply Schedule (Per Channel) ---
                if "schedule" in board_settings and isinstance(board_settings["schedule"], dict):
                     board_schedule_data = board_settings["schedule"]
                     if board_idx not in self.channel_schedules: self.channel_schedules[board_idx] = {}

                     for channel_name, chan_schedule in board_schedule_data.items():
                          if channel_name in LED_CHANNELS and isinstance(chan_schedule, dict):
                               on_time = chan_schedule.get("on_time", "08:00")
                               off_time = chan_schedule.get("off_time", "00:00")
                               enabled = bool(chan_schedule.get("enabled", False))
                               # Validate times
                               if not self.validate_time_format(on_time): on_time = "08:00"
                               if not self.validate_time_format(off_time): off_time = "00:00"

                               # Update internal data structure
                               self.channel_schedules.setdefault(board_idx, {}).setdefault(channel_name, {}).update({
                                    "on_time": on_time, "off_time": off_time, "enabled": enabled
                               })

                               # Update UI Widgets safely
                               on_entry = self.channel_time_entries.get((board_idx, channel_name, "on"))
                               off_entry = self.channel_time_entries.get((board_idx, channel_name, "off"))
                               enabled_var = self.channel_schedule_vars.get((board_idx, channel_name))
                               try:
                                    if on_entry and on_entry.winfo_exists():
                                         on_entry.delete(0, tk.END); on_entry.insert(0, on_time)
                                    if off_entry and off_entry.winfo_exists():
                                         off_entry.delete(0, tk.END); off_entry.insert(0, off_time)
                                    if enabled_var: enabled_var.set(enabled)
                                    applied_count += 1 # Count schedule update
                               except tk.TclError: pass # Ignore if widget destroyed

                # --- Apply Fan Settings (Update UI and internal Board state) ---
                if "fan" in board_settings and isinstance(board_settings["fan"], dict):
                     fan_data = board_settings["fan"]
                     fan_speed = fan_data.get("speed", 50)
                     fan_enabled = bool(fan_data.get("enabled", False))
                     try:
                          valid_speed = int(fan_speed)
                          if 0 <= valid_speed <= 100:
                               # Update the internal state of the specific board object
                               board.fan_speed = valid_speed
                               board.fan_enabled = fan_enabled
                               # Update global UI controls only ONCE based on the first board with fan settings
                               if not fan_settings_found:
                                    self.fan_speed_var.set(str(valid_speed))
                                    self.fans_on = fan_enabled # Update global fan state tracker
                                    self.fan_button_var.set("Turn Fans OFF" if self.fans_on else "Turn Fans ON")
                                    fan_settings_found = True
                                    applied_count += 1 # Count global fan UI update once
                          else: print(f"Warning (Import): Invalid fan speed '{fan_speed}' for {board_key}.")
                     except (ValueError, tk.TclError): pass # Ignore errors

        except Exception as e:
            apply_error = f"Error applying settings to UI: {e}"
            print(f"Import Apply Error: {apply_error}")

        # --- Send Result to GUI Queue ---
        if apply_error:
             self.gui_queue.put(FileOperationComplete('import', False, apply_error))
        else:
             success_msg = f"Applied {applied_count} settings from {os.path.basename(file_path)}."
             if skipped_chambers:
                  success_msg += f" Skipped unknown keys: {', '.join(skipped_chambers)}."
             print(f"Import Apply Finished: {success_msg}")
             import_data = { 'applied_count': applied_count, 'fan_settings_found': fan_settings_found }
             self.gui_queue.put(FileOperationComplete('import', True, success_msg, import_data))


    # check_channel_active_state is removed as the check is done within _apply_settings_to_multiple_worker


    def validate_time_format(self, time_str):
        """Validate that the time string is in HH:MM format (24-hour)."""
        if not isinstance(time_str, str): return False
        # Use pre-compiled pattern
        return bool(self.time_pattern.match(time_str))

    def validate_time_entry_visual(self, board_idx, channel_name, entry_type, new_value, entry_widget):
        """Validate time entry visually by changing text color (runs in main thread via trace)."""
        try:
            # Check if widget still exists before configuring
            if entry_widget.winfo_exists():
                if self.validate_time_format(new_value):
                    entry_widget.config(foreground=self.cached_colors['normal'])
                else:
                    entry_widget.config(foreground=self.cached_colors['error'])
        except tk.TclError:
            pass # Widget has been destroyed, ignore


    def set_status(self, message, is_error=False):
        """Update status bar using batched updates."""
        # Add the message and error status to the batch
        self.status_update_batch.append({'message': message, 'is_error': is_error})

        # If there's already a pending update timer, do nothing (it will process this message)
        if self.status_update_timer:
            return

        # Schedule the status update processing
        self.status_update_timer = self.root.after(
            self.timings['status_update_batch'], self.process_status_updates)

    def process_status_updates(self):
        """Process batched status updates (runs in main thread)."""
        self.status_update_timer = None # Clear timer reference

        if not self.status_update_batch:
            return

        # Use the most recent status message from the batch
        latest_update = self.status_update_batch[-1]
        message = latest_update['message']
        is_error = latest_update['is_error']

        try:
             if is_error:
                  self.status_var.set(f"Error: {message}")
                  # Optionally change status bar style for errors
                  # self.status_bar_widget.config(foreground='red', background='pink')
             else:
                  self.status_var.set(message)
                  # Reset style if needed
                  # self.status_bar_widget.config(foreground=default_fg, background=default_bg)
        except tk.TclError:
             print("Warning: Status bar variable/widget no longer exists.")

        # Clear the batch
        self.status_update_batch = []


    def update_channel_schedule(self, board_idx, channel_name):
        """Update the schedule state for a specific channel when checkbox is toggled."""
        if board_idx >= len(self.boards): return # Safety check

        # Ensure data structures exist
        if board_idx not in self.channel_schedules: self.channel_schedules[board_idx] = {}
        if channel_name not in self.channel_schedules[board_idx]:
             self.channel_schedules[board_idx][channel_name] = {
                  "on_time": "08:00", "off_time": "00:00", "enabled": False, "active": True
             }

        schedule_info = self.channel_schedules[board_idx][channel_name]
        schedule_var = self.channel_schedule_vars.get((board_idx, channel_name))
        on_time_entry = self.channel_time_entries.get((board_idx, channel_name, "on"))
        off_time_entry = self.channel_time_entries.get((board_idx, channel_name, "off"))

        if not schedule_var:
             print(f"Error: Checkbox variable not found for {board_idx}-{channel_name}")
             return

        try:
            is_enabled = schedule_var.get()

            # Get and validate time entries BEFORE enabling
            on_time = on_time_entry.get() if on_time_entry and on_time_entry.winfo_exists() else schedule_info["on_time"]
            off_time = off_time_entry.get() if off_time_entry and off_time_entry.winfo_exists() else schedule_info["off_time"]
            on_time_valid = self.validate_time_format(on_time)
            off_time_valid = self.validate_time_format(off_time)

            # If enabling, check if times are valid
            if is_enabled and (not on_time_valid or not off_time_valid):
                messagebox.showerror("Invalid Time Format",
                    f"Cannot enable schedule for {channel_name} with invalid time format (HH:MM).")
                # Reset checkbox state in UI
                schedule_var.set(False)
                is_enabled = False # Ensure internal state reflects disabled

            # Update internal schedule data
            schedule_info["on_time"] = on_time if on_time_valid else "08:00" # Store valid time or default
            schedule_info["off_time"] = off_time if off_time_valid else "00:00"
            schedule_info["enabled"] = is_enabled

            chamber_num = self.boards[board_idx].chamber_number or (board_idx + 1)
            action_str = "enabled" if is_enabled else "disabled"
            print(f"Schedule {action_str} for Chamber {chamber_num} - Channel {channel_name} (Times: {schedule_info['on_time']}-{schedule_info['off_time']})")
            self.set_status(f"Schedule {action_str} for {chamber_num}-{channel_name}")

            # Trigger apply settings for this board to reflect the change
            # Use root.after to ensure apply runs after this event handler finishes
            self.root.after(10, lambda idx=board_idx: self.apply_board_settings(idx))

        except tk.TclError:
             print(f"Warning: Error accessing schedule widgets for {board_idx}-{channel_name} (likely destroyed).")
        except Exception as e:
             print(f"Error updating channel schedule for {board_idx}-{channel_name}: {e}")
             messagebox.showerror("Error", f"An unexpected error occurred while updating the schedule for {channel_name}.")


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

            # Handle the case where start and end times are the same
            # If start == end, assume it means ON for the full 24 hours.
            if start_minutes == end_minutes:
                return True

            # Normal case: start time is before end time (e.g., 08:00 to 18:00)
            if start_minutes < end_minutes:
                # Check is within the interval [start, end]
                return start_minutes <= check_minutes <= end_minutes
            # Wraparound case: end time is on the next day (e.g., 20:00 to 06:00)
            else: # start_minutes > end_minutes
                # Check is after start OR before end
                return check_minutes >= start_minutes or check_minutes <= end_minutes
        except (ValueError, AttributeError, TypeError):
             # Handle invalid time format strings gracefully
             print(f"Warning: Invalid time format encountered in is_time_between ({check_time_str}, {start_time_str}, {end_time_str})")
             return False # Default to False (not between) if times are invalid


    def process_gui_queue(self):
        """Process GUI action queue from worker threads."""
        processed_count = 0
        try:
            # Process multiple items per call for efficiency, up to a limit
            for _ in range(50): # Limit items per cycle to prevent blocking GUI
                action = self.gui_queue.get_nowait()
                processed_count += 1

                # --- Handle different action types ---
                if isinstance(action, StatusUpdate):
                    self.set_status(action.message, action.is_error)
                    # Optionally show critical errors in a messagebox
                    # if action.is_error: messagebox.showerror("Background Error", action.message)

                elif isinstance(action, BoardsDetected):
                    print(f"GUI Queue: Processing BoardsDetected (Found: {len(action.boards)}, Error: {action.error})")
                    if action.error:
                        messagebox.showerror("Error Scanning Boards", action.error)
                        self.set_status(f"Scan Error: {action.error}", is_error=True)
                        self.boards = [] # Ensure boards list is empty on error
                    else:
                        self.boards = action.boards # Update boards list

                    # Recreate frames regardless of error (clears old frames if error occurred)
                    self.create_board_frames() # This handles GUI update and initial status

                    # Update final status after frames are created
                    if not action.error:
                         board_count = len(self.boards)
                         status_msg = f"Scan complete: Found {board_count} board(s)."
                         self.set_status(status_msg)
                    # Ensure scan flag is cleared here after processing
                    self.background_operations['scan'] = False


                elif isinstance(action, CommandComplete):
                    # Handle completion of commands sent via BoardConnection queue
                    board_idx = action.board_idx
                    if board_idx >= len(self.boards): continue # Ignore if board index invalid

                    chamber_num = self.boards[board_idx].chamber_number or (board_idx + 1)
                    cmd_type = action.command_type
                    success = action.success
                    message = action.message
                    extra_info = action.extra_info

                    status_prefix = f"Chamber {chamber_num}:"
                    if success:
                        status_msg = f"{status_prefix} "
                        if cmd_type == BoardConnection.CMD_SETALL: status_msg += "LEDs updated."
                        elif cmd_type == BoardConnection.CMD_FAN_SET: status_msg += "Fan updated."
                        else: status_msg += f"{cmd_type} OK."
                        if extra_info: status_msg += f" ({extra_info})"
                        self.set_status(status_msg)
                    else:
                        error_msg = f"{status_prefix} {cmd_type} Error - {message}"
                        # Show messagebox for command errors
                        messagebox.showerror(f"Command Error (Chamber {chamber_num})", f"Command: {cmd_type}\nError: {message}")
                        self.set_status(error_msg, is_error=True)


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
                    else: # Error during file operation
                        messagebox.showerror(f"{op_type} Error", f"Error during {action.operation_type}: {action.message}")
                        self.set_status(f"{op_type} error: {action.message}", is_error=True)


                elif isinstance(action, SchedulerUpdate):
                    # Update internal schedule state and visual indicator for the specific channel
                    board_idx, channel_name, is_active = action.board_idx, action.channel_name, action.active
                    if (board_idx in self.channel_schedules and
                        channel_name in self.channel_schedules[board_idx]):

                        # Update the 'active' state in our internal structure
                        self.channel_schedules[board_idx][channel_name]['active'] = is_active

                        # Update visual indicator (background color of schedule frame)
                        schedule_frame_widget = self.channel_schedule_frames.get((board_idx, channel_name))
                        if schedule_frame_widget:
                             try:
                                  if schedule_frame_widget.winfo_exists():
                                       # Choose style based on active state
                                       style_name = 'ActiveSchedule.TFrame' if is_active else 'InactiveSchedule.TFrame'
                                       schedule_frame_widget.config(style=style_name)
                             except tk.TclError: pass # Widget might be destroyed

                        # Applying settings is now handled by the scheduler worker itself
                        # No need to trigger apply_board_settings here.
                        # chamber_num = self.boards[board_idx].chamber_number or (board_idx+1)
                        # state = "ON" if is_active else "OFF"
                        # print(f"GUI Queue: Processed SchedulerUpdate for {chamber_num}-{channel_name} -> {state}")


                self.gui_queue.task_done() # Mark action as processed

        except queue.Empty:
            pass # No more actions in queue this cycle
        except Exception as e:
             # Log unexpected errors during queue processing
             print(f"FATAL Error processing GUI queue: {e}")
             import traceback
             traceback.print_exc()
             self.set_status(f"GUI Error: {e}", is_error=True)

        # Schedule next queue check regardless of exceptions
        self.root.after(self.queue_check_interval, self.process_gui_queue)


    def duty_cycle_from_percentage(self, percentage):
        """Convert a percentage (0-100) to duty cycle (0-4095) using cache."""
        try:
             # Ensure percentage is an integer and clamp to valid range
             percentage = max(0, min(100, int(percentage)))
             # Use pre-computed lookup table
             return self.duty_cycle_lookup.get(percentage, 0) # Default to 0 if not found (shouldn't happen)
        except (ValueError, TypeError):
             return 0 # Return 0 if conversion fails


if __name__ == "__main__":
    root = tk.Tk()
    # Set minimum size for the window to better accommodate controls
    root.minsize(1300, 750)
    app = LEDControlGUI(root)
    root.mainloop()
