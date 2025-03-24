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
LED_COLORS = {
    'UV': "#9400D3",
    'FAR_RED': "#8B0000",
    'RED': "#FF0000",
    'WHITE': "#FFFFFF",
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
    'active_bg': '#D0FFD0',
    'inactive_bg': '#FFD0D0',
    'button_bg': '#F0F0F0',
    'entry_bg': '#FFFFFF',
    'disabled_bg': '#E0E0E0'
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
    'label_width': 12
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
        self.extra_info = extra_info

class SchedulerUpdate(GUIAction):
    """Message to update scheduler state"""
    def __init__(self, board_idx, active):
        self.board_idx = board_idx
        self.active = active

class ToggleComplete(GUIAction):
    """Message indicating toggle operation completed"""
    def __init__(self, operation_type, success, message=None, board_idx=None, state=None):
        self.operation_type = operation_type  # 'lights' or 'fans'
        self.success = success
        self.message = message
        self.board_idx = board_idx
        self.state = state

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
                except:
                    pass
                finally:
                    self.is_connected = False
            return True
    
    def _send_command_impl(self, duty_values):
        """Internal implementation of send_command operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl():
                    return False, self.last_error
            
            retry_count = 0
            while retry_count < self.max_retries:        
                try:
                    # Format: "SETALL d0 d1 d2 d3 d4 d5\n"
                    # Use pre-cached command type
                    command = self.CMD_SETALL
                    for val in duty_values:
                        command += f" {val}"
                    command += "\n"
                    
                    # Clear any pending data only once before sending - reduce I/O operations
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()
                    
                    # Send command in a single write operation
                    self.serial_conn.write(command.encode('utf-8'))
                    
                    # More efficient response reading with timeout
                    start_time = time.time()
                    response = ""
                    while time.time() - start_time < 1.0:  # 1 second timeout
                        if self.serial_conn.in_waiting > 0:
                            # Read all available data at once rather than byte-by-byte
                            data = self.serial_conn.read(self.serial_conn.in_waiting)
                            response += data.decode('utf-8')
                            # Use cached response strings for comparison
                            if self.RESP_OK in response:
                                return True, "Success"
                            elif self.RESP_ERR_PREFIX in response:
                                return False, f"Error: {response}"
                            
                        # Shorter sleep interval for faster response
                        time.sleep(0.05)
                    
                    retry_count += 1
                    if retry_count < self.max_retries:
                        time.sleep(0.5)  # Wait before retry
                    else:
                        return False, "Timeout waiting for response"
                        
                except Exception as e:
                    self.is_connected = False
                    return False, str(e)
            
            return False, "Max retries exceeded"
    
    def _set_fan_speed_impl(self, percentage):
        """Internal implementation of set_fan_speed operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl():
                    return False, self.last_error
            
            retry_count = 0
            while retry_count < self.max_retries:        
                try:
                    # Format: "FAN_SET percentage\n"
                    # Use pre-cached command type
                    command = f"{self.CMD_FAN_SET} {percentage}\n"
                    
                    # Clear any pending data
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()
                    
                    # Send command
                    self.serial_conn.write(command.encode('utf-8'))
                    
                    # Read response with timeout
                    start_time = time.time()
                    response = ""
                    while time.time() - start_time < 1.0:  # 1 second timeout
                        if self.serial_conn.in_waiting > 0:
                            data = self.serial_conn.read(self.serial_conn.in_waiting)
                            response += data.decode('utf-8')
                            # Use cached response strings for comparison
                            if self.RESP_OK in response:
                                self.fan_speed = percentage
                                self.fan_enabled = percentage > 0
                                return True, "Success"
                            elif self.RESP_ERR_PREFIX in response:
                                return False, f"Error: {response}"
                            
                        time.sleep(0.05)
                    
                    retry_count += 1
                    if retry_count < self.max_retries:
                        time.sleep(0.5)  # Wait before retry
                    else:
                        return False, "Timeout waiting for response"
                        
                except Exception as e:
                    self.is_connected = False
                    return False, str(e)
            
            return False, "Max retries exceeded"
    
    def _turn_fan_on_impl(self):
        """Internal implementation of turn_fan_on operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl():
                    return False, self.last_error
            
            retry_count = 0
            while retry_count < self.max_retries:        
                try:
                    # Format: "FAN_ON\n"
                    # Use pre-cached command type
                    command = f"{self.CMD_FAN_ON}\n"
                    
                    # Clear any pending data
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()
                    
                    # Send command
                    self.serial_conn.write(command.encode('utf-8'))
                    
                    # Read response with timeout
                    start_time = time.time()
                    response = ""
                    while time.time() - start_time < 1.0:  # 1 second timeout
                        if self.serial_conn.in_waiting > 0:
                            data = self.serial_conn.read(self.serial_conn.in_waiting)
                            response += data.decode('utf-8')
                            # Use cached response strings for comparison
                            if self.RESP_OK in response:
                                self.fan_enabled = True
                                return True, "Success"
                            elif self.RESP_ERR_PREFIX in response:
                                return False, f"Error: {response}"
                            
                        time.sleep(0.05)
                    
                    retry_count += 1
                    if retry_count < self.max_retries:
                        time.sleep(0.5)  # Wait before retry
                    else:
                        return False, "Timeout waiting for response"
                        
                except Exception as e:
                    self.is_connected = False
                    return False, str(e)
            
            return False, "Max retries exceeded"
    
    def _turn_fan_off_impl(self):
        """Internal implementation of turn_fan_off operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl():
                    return False, self.last_error
            
            retry_count = 0
            while retry_count < self.max_retries:        
                try:
                    # Format: "FAN_OFF\n"
                    # Use pre-cached command type
                    command = f"{self.CMD_FAN_OFF}\n"
                    
                    # Clear any pending data
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()
                    
                    # Send command
                    self.serial_conn.write(command.encode('utf-8'))
                    
                    # Read response with timeout
                    start_time = time.time()
                    response = ""
                    while time.time() - start_time < 1.0:  # 1 second timeout
                        if self.serial_conn.in_waiting > 0:
                            data = self.serial_conn.read(self.serial_conn.in_waiting)
                            response += data.decode('utf-8')
                            # Use cached response strings for comparison
                            if self.RESP_OK in response:
                                self.fan_enabled = False
                                self.fan_speed = 0
                                return True, "Success"
                            elif self.RESP_ERR_PREFIX in response:
                                return False, f"Error: {response}"
                            
                        time.sleep(0.05)
                    
                    retry_count += 1
                    if retry_count < self.max_retries:
                        time.sleep(0.5)  # Wait before retry
                    else:
                        return False, "Timeout waiting for response"
                        
                except Exception as e:
                    self.is_connected = False
                    return False, str(e)
            
            return False, "Max retries exceeded"
    
    def start_command_processor(self):
        """Start the background command processor thread"""
        if not self.command_processor_running:
            self.command_processor_running = True
            self.command_processor_thread = threading.Thread(
                target=self._process_command_queue,
                daemon=True
            )
            self.command_processor_thread.start()
    
    def _process_command_queue(self):
        """Process and batch commands in background thread"""
        while self.command_processor_running:
            try:
                # Wait for a command or timeout
                try:
                    cmd, args, callback = self.command_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                
                # Check if we should wait for more commands to batch
                batch_commands = []
                batch_callbacks = []
                
                # Add the first command
                batch_commands.append((cmd, args))
                batch_callbacks.append(callback)
                
                # Try to batch multiple commands of the same type
                start_time = time.time()
                while time.time() - start_time < self.command_batch_timeout:
                    try:
                        next_cmd, next_args, next_callback = self.command_queue.get_nowait()
                        # Only batch same command types
                        if next_cmd == cmd:
                            # For most recent LED command, only keep the latest
                            if cmd == "SETALL" and batch_commands:
                                batch_commands.pop()
                                batch_callbacks.pop()
                            batch_commands.append((next_cmd, next_args))
                            batch_callbacks.append(next_callback)
                        else:
                            # Put back different command types for next batch
                            self.command_queue.put((next_cmd, next_args, next_callback))
                            break
                    except queue.Empty:
                        break
                
                # Process the batched commands
                for i, (cmd, args) in enumerate(batch_commands):
                    if cmd == "SETALL":
                        success, message = self._send_command_impl(args)
                    elif cmd == "FAN_SET":
                        success, message = self._set_fan_speed_impl(args)
                    elif cmd == "FAN_ON":
                        success, message = self._turn_fan_on_impl()
                    elif cmd == "FAN_OFF":
                        success, message = self._turn_fan_off_impl()
                    
                    # Call the callback if provided
                    callback = batch_callbacks[i]
                    if callback:
                        callback(success, message)
                    
                    # Mark task as done
                    self.command_queue.task_done()
            
            except Exception as e:
                # Log error but continue processing
                print(f"Command processor error: {str(e)}")
                time.sleep(0.1)
    
    def send_command(self, duty_values, callback=None):
        """Send command to update LED brightness asynchronously"""
        # Cache zero duty cycle check for better performance
        if duty_values == self.ZERO_DUTY_CYCLES:
            # Optimization: Use cached zero array for faster comparison
            pass
            
        # Ensure command processor is running
        if not self.command_processor_running:
            self.start_command_processor()
        
        # Queue the command instead of executing directly
        self.command_queue.put(("SETALL", duty_values, callback))
        return True
    
    def set_fan_speed(self, percentage, callback=None):
        """Set the fan speed as a percentage asynchronously"""
        # Ensure command processor is running
        if not self.command_processor_running:
            self.start_command_processor()
        
        # Queue the command instead of executing directly
        self.command_queue.put(("FAN_SET", percentage, callback))
        return True
    
    def turn_fan_on(self, callback=None):
        """Turn the fan on asynchronously"""
        # Ensure command processor is running
        if not self.command_processor_running:
            self.start_command_processor()
        
        # Queue the command instead of executing directly
        self.command_queue.put(("FAN_ON", None, callback))
        return True
    
    def turn_fan_off(self, callback=None):
        """Turn the fan off asynchronously"""
        # Ensure command processor is running
        if not self.command_processor_running:
            self.start_command_processor()
        
        # Queue the command instead of executing directly
        self.command_queue.put(("FAN_OFF", None, callback))
        return True
    
    def cleanup(self):
        """Clean up resources when shutting down"""
        # Cancel any pending futures if possible
        for task in self.pending_tasks:
            if not task.done():
                task.cancel()
        
        # Shutdown the executor
        self.executor.shutdown(wait=False)
        
        # Ensure serial connection is closed
        if self.serial_conn and self.is_connected:
            try:
                self.serial_conn.close()
            except:
                pass


class LEDControlGUI:
    """Main GUI application for controlling LED brightness"""
    
    def __init__(self, root):
        self.root = root
        self.root.title("SpecAC-HT Control System")
        self.root.geometry("1200x800")
        
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
        self.zero_duty_cycle = [0, 0, 0, 0, 0, 0]
        
        self.boards = []
        self.board_frames = []
        self.led_entries = {}  # {(board_idx, channel): entry_widget}
        
        # NEW: Add direct chamber-to-board mapping for O(1) lookups
        self.chamber_to_board_idx = {}  # {chamber_number: board_idx}
        
        # Track master light state
        self.master_on = True
        self.saved_values = {}  # To store values when turning off
        
        # Track master fan state
        self.fans_on = False
        self.fan_speed_var = tk.StringVar(value="50")
        
        # Scheduling related variables - now at board level instead of channel level
        self.board_schedules = {}  # {board_idx: {"on_time": time, "off_time": time, "enabled": bool, "active": bool}}
        self.board_time_entries = {}   # {board_idx, "on"/"off"): entry_widget}
        self.board_schedule_vars = {}  # {board_idx: BooleanVar}
        self.scheduler_running = False
        self.scheduler_thread = None
        self.changed_boards = set()  # Track which boards changed
        
        # Optimization: Add cache for last schedule check state to avoid unnecessary updates
        self.last_schedule_state = {}  # {board_idx: {"active": bool, "last_check": timestamp}}
        
        # Optimization: Set default scheduler check interval (in milliseconds)
        self.scheduler_check_interval = self.timings['scheduler_default']
        self.adaptive_check_timer = None  # Store reference to scheduled timer
        
        # NEW: Add widget update batching
        self.widget_update_queue = queue.Queue()
        self.update_batch_timer = None
        self.update_batch_interval = self.timings['widget_update_batch']
        self.status_update_batch = []  # List to batch status updates
        self.status_update_timer = None
        self.is_updating_widgets = False
        
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
        self.max_concurrent_commands = 5
        
        # NEW: Cache calculated duty cycles for percentages
        self.duty_cycle_cache = {i: int((i / 100.0) * 4095) for i in range(101)}
        
        # Create and cache validation commands
        self.setup_validation_commands()
        
        # Create the GUI components
        self.create_gui()
        
        # Cache board serial detection
        self.initialize_port_cache()
        
        # Start the scheduler using after() instead of a thread
        self.start_scheduler()
        
        # Start the widget update processor
        self.process_widget_updates()
        
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
            'status': ('Helvetica', 9, 'normal')
        }
    
    def create_color_cache(self):
        """Cache colors for better performance"""
        # Use the pre-defined UI_COLORS dictionary
        self.cached_colors = UI_COLORS
    
    def setup_validation_commands(self):
        """Set up and cache validation commands"""
        self.validation_commands = {
            'percentage': (self.root.register(self.validate_percentage), '%P'),
            'time': (self.root.register(self.validate_time_format), '%P')
        }
    
    def initialize_port_cache(self):
        """Initialize the serial port cache for faster board detection"""
        global CACHED_PORT_INFO, BOARD_SERIAL_CACHE
        
        # Reset the caches
        CACHED_PORT_INFO = {}
        BOARD_SERIAL_CACHE = {}
        
        # Pre-cache all XIAO RP2040 boards for faster future lookups
        for port_info in list_ports.grep('VID:PID=2E8A:0005'):
            CACHED_PORT_INFO[port_info.device] = {
                'serial_number': port_info.serial_number,
                'description': port_info.description,
                'hwid': port_info.hwid
            }
            BOARD_SERIAL_CACHE[port_info.serial_number] = port_info.device
    
    def load_chamber_mapping(self):
        """Load the chamber to serial number mapping from the text file"""
        self.chamber_mapping = {}
        self.reverse_chamber_mapping = {}
        
        try:
            if os.path.exists(SERIAL_MAPPING_FILE):
                with open(SERIAL_MAPPING_FILE, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                            
                        # Parse the chamber:serial format using cached regex
                        match = self.serial_mapping_pattern.match(line)
                        if match:
                            chamber_num = int(match.group(1))
                            serial_num = match.group(2).strip()
                            
                            # Store the mapping both ways for easy lookup
                            self.chamber_mapping[serial_num] = chamber_num
                            self.reverse_chamber_mapping[chamber_num] = serial_num
                        
                self.status_var.set(f"Loaded chamber mapping for {len(self.chamber_mapping)} chambers")
            else:
                self.status_var.set(f"Chamber mapping file not found at {SERIAL_MAPPING_FILE}")
        except Exception as e:
            self.status_var.set(f"Error loading chamber mapping: {str(e)}")
            print(f"Error loading chamber mapping: {str(e)}")
        
    def create_gui(self):
        """Create the main GUI layout"""
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(column=0, row=0, sticky=(tk.N, tk.W, tk.E, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Header
        header_frame = ttk.Frame(main_frame)
        header_frame.grid(column=0, row=0, columnspan=2, sticky=(tk.W, tk.E))
        
        ttk.Label(header_frame, text="LED Control System", font=self.cached_fonts['header']).pack(side=tk.LEFT)
        
        # Master lights control button
        self.master_button_var = tk.StringVar(value="All Lights OFF")
        master_button = ttk.Button(
            header_frame,
            textvariable=self.master_button_var,
            command=self.toggle_all_lights,
            width=15
        )
        master_button.pack(side=tk.LEFT, padx=20)
        
        # Add scheduler control button
        self.scheduler_button_var = tk.StringVar(value="Start Scheduler")
        scheduler_button = ttk.Button(
            header_frame,
            textvariable=self.scheduler_button_var,
            command=self.toggle_scheduler,
            width=15
        )
        scheduler_button.pack(side=tk.LEFT, padx=10)
        
        # Scan and Apply buttons
        btn_frame = ttk.Frame(header_frame)
        btn_frame.pack(side=tk.RIGHT)
        
        ttk.Button(btn_frame, text="Scan for Boards", command=self.scan_boards).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Apply All Settings", command=self.apply_all_settings).pack(side=tk.LEFT, padx=5)
        
        # Boards display area (2 rows x 4 columns grid)
        boards_frame = ttk.Frame(main_frame)
        boards_frame.grid(column=0, row=1, sticky=(tk.N, tk.W, tk.E, tk.S))
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(1, weight=1)
        
        # Navigation frame for page buttons - MOVED TO TOP of boards_frame for better visibility
        nav_frame = ttk.Frame(boards_frame)
        nav_frame.pack(fill=tk.X, pady=5)
        
        # Page navigation buttons - Enhanced with more prominent styling
        nav_label = ttk.Label(nav_frame, text="Chamber Navigation:", font=self.cached_fonts['subheader'])
        nav_label.pack(side=tk.LEFT, padx=10)
        
        self.prev_button = ttk.Button(nav_frame, text="◀ Chambers 1-8", command=self.prev_page, width=15)
        self.prev_button.pack(side=tk.LEFT, padx=10)
        
        self.page_label = ttk.Label(nav_frame, text="Chambers 1-8", font=self.cached_fonts['subheader'])
        self.page_label.pack(side=tk.LEFT, padx=10)
        
        self.next_button = ttk.Button(nav_frame, text="Chambers 9-16 ▶", command=self.next_page, width=15)
        self.next_button.pack(side=tk.LEFT, padx=10)
        
        # Container frame for board frames
        self.boards_container = ttk.Frame(boards_frame)
        self.boards_container.pack(expand=True, fill=tk.BOTH, padx=10, pady=10)
        
        # Configure grid for boards_container - ensure columns and rows can expand
        for i in range(4):  # 4 columns
            self.boards_container.columnconfigure(i, weight=1, uniform="column")
        for i in range(2):  # 2 rows
            self.boards_container.rowconfigure(i, weight=1, uniform="row")
        
        # Fan control frame (new)
        fan_frame = ttk.LabelFrame(main_frame, text="Fan Controls")
        fan_frame.grid(column=0, row=2, columnspan=2, sticky=(tk.W, tk.E), pady=10)
        
        # Fan toggle button
        self.fan_button_var = tk.StringVar(value="Turn Fans ON")
        fan_button = ttk.Button(
            fan_frame,
            textvariable=self.fan_button_var,
            command=self.toggle_all_fans,
            width=15
        )
        fan_button.grid(column=0, row=0, padx=10, pady=5)
        
        # Fan speed control
        ttk.Label(fan_frame, text="Fan Speed:").grid(column=1, row=0, padx=(20, 5), pady=5)
        # Replace Spinbox with Entry for better performance
        fan_speed_entry = ttk.Entry(
            fan_frame,
            width=5,
            textvariable=self.fan_speed_var,
            validate='key',
            validatecommand=self.validation_commands['percentage']
        )
        fan_speed_entry.grid(column=2, row=0, padx=5, pady=5)
        ttk.Label(fan_frame, text="%").grid(column=3, row=0, padx=(0, 5), pady=5)
        
        # Apply fan settings button
        ttk.Button(fan_frame, text="Apply Fan Settings", command=self.apply_fan_settings).grid(column=4, row=0, padx=10, pady=5)
        
        # Bottom frame for Import/Export buttons
        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.grid(column=0, row=3, columnspan=2, sticky=(tk.W, tk.E), pady=5)
        
        ttk.Button(bottom_frame, text="Export Settings", command=self.export_settings).pack(side=tk.LEFT, padx=5)
        ttk.Button(bottom_frame, text="Import Settings", command=self.import_settings).pack(side=tk.RIGHT, padx=5)
        
        # Status bar
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(column=0, row=4, columnspan=2, sticky=(tk.W, tk.E))
        
        # Add window close handler for cleanup
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.scan_boards()
    
    def on_closing(self):
        """Clean up resources and close the application"""
        # Cancel any scheduled after() calls
        if self.adaptive_check_timer:
            self.root.after_cancel(self.adaptive_check_timer)
        
        # Cancel widget update batch timer
        if self.update_batch_timer:
            self.root.after_cancel(self.update_batch_timer)
            
        # Cancel status update timer
        if self.status_update_timer:
            self.root.after_cancel(self.status_update_timer)
        
        # Stop the scheduler
        self.scheduler_running = False
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=1.0)
        
        # Clean up board connections
        for board in self.boards:
            board.cleanup()
        
        # Destroy the main window
        self.root.destroy()
    
    def next_page(self):
        """Navigate to chambers 9-16 (page 2)"""
        if self.current_page == 0:
            self.current_page = 1
            self.update_page_display()
    
    def prev_page(self):
        """Navigate to chambers 1-8 (page 1)"""
        if self.current_page == 1:
            self.current_page = 0
            self.update_page_display()
    
    def update_page_display(self):
        """Update the display to show chambers 1-8 or 9-16 based on current page"""
        # Hide all board frames first
        for frame in self.board_frames:
            frame.grid_remove()
        
        # NEW: More efficient counting using dictionary comprehension and sum
        boards_1_8 = sum(1 for chamber in self.chamber_to_board_idx if 1 <= chamber <= 8)
        boards_9_16 = sum(1 for chamber in self.chamber_to_board_idx if 9 <= chamber <= 16)
        
        # Update page label and navigation buttons
        if self.current_page == 0:
            self.page_label.config(text="Chambers 1-8")
            self.prev_button.config(state=tk.DISABLED)
            self.next_button.config(state=tk.NORMAL if boards_9_16 > 0 else tk.DISABLED)
            first_chamber = 1
            last_chamber = 8
        else:  # page 1
            self.page_label.config(text="Chambers 9-16")
            self.prev_button.config(state=tk.NORMAL)
            self.next_button.config(state=tk.DISABLED)
            first_chamber = 9
            last_chamber = 16
        
        # NEW: More efficient display of boards using chamber-to-board mapping
        displayed_count = 0
        for chamber_number in range(first_chamber, last_chamber + 1):
            if chamber_number in self.chamber_to_board_idx:
                board_idx = self.chamber_to_board_idx[chamber_number]
                
                # Calculate position within the page (2 rows x 4 columns)
                relative_position = chamber_number - first_chamber
                row = relative_position // 4
                col = relative_position % 4
                
                if board_idx < len(self.board_frames):
                    self.board_frames[board_idx].grid(row=row, column=col, padx=5, pady=5, sticky=(tk.N, tk.W, tk.E, tk.S))
                    displayed_count += 1
        
        # Update status message to show how many chambers are displayed
        self.status_var.set(f"Displaying {displayed_count} chambers (Chambers {first_chamber}-{last_chamber})")
    
    def create_board_frames(self):
        """Create frames for each detected board, sorted by chamber number"""
        # Remove old frames
        for frame in self.board_frames:
            frame.destroy()
        self.board_frames = []
        self.led_entries = {}
        
        # NEW: Reset board lookup dictionaries
        self.chamber_to_board_idx = {}
        self.serial_to_board_idx = {}
        
        # Sort boards by chamber number
        self.boards.sort(key=lambda b: b.chamber_number)
        
        for i, board in enumerate(self.boards):
            chamber_number = board.chamber_number
            serial_number = board.serial_number
            
            # NEW: Add chamber and serial number to lookup dictionaries
            self.chamber_to_board_idx[chamber_number] = i
            self.serial_to_board_idx[serial_number] = i
            
            frame = ttk.LabelFrame(self.boards_container, text=f"Chamber {chamber_number}")
            self.board_frames.append(frame)
            
            # LED control section
            led_control_frame = ttk.Frame(frame)
            led_control_frame.grid(column=0, row=0, padx=5, pady=5, sticky=(tk.W, tk.E))
            
            # Add header row for LED controls
            ttk.Label(led_control_frame, text="LED Channel").grid(column=1, row=0, sticky=tk.W, padx=5)
            ttk.Label(led_control_frame, text="Intensity (%)").grid(column=2, row=0, sticky=tk.W, padx=5)
            
            # Add LED controls for each channel
            for row, (channel_name, channel_idx) in enumerate(LED_CHANNELS.items(), start=1):
                color_frame = ttk.Frame(led_control_frame, width=20, height=20)
                color_frame.grid(column=0, row=row, padx=5, pady=2)
                color_label = tk.Label(color_frame, bg=LED_COLORS[channel_name], width=2)
                color_label.pack(fill=tk.BOTH, expand=True)
                
                ttk.Label(led_control_frame, text=channel_name).grid(column=1, row=row, sticky=tk.W, padx=5)
                
                value_var = tk.StringVar(value="0")
                # Replace Spinbox with Entry for better performance
                entry = ttk.Entry(
                    led_control_frame,
                    width=5,
                    textvariable=value_var,
                    validate='key',
                    validatecommand=self.validation_commands['percentage']
                )
                entry.grid(column=2, row=row, sticky=tk.W, padx=5)
                ttk.Label(led_control_frame, text="%").grid(column=3, row=row, sticky=tk.W)
                self.led_entries[(i, channel_name)] = entry
            
            # Scheduling section - one per board
            schedule_frame = ttk.Frame(frame)
            schedule_frame.grid(column=0, row=1, padx=5, pady=5, sticky=(tk.W, tk.E))
            
            # Create schedule controls
            ttk.Label(schedule_frame, text="ON Time:").grid(column=0, row=0, padx=5, pady=5, sticky=tk.W)
            on_time_var = tk.StringVar(value="08:00")
            
            # Add validation callback to variable
            on_time_var.trace_add("write", lambda name, index, mode, b_idx=i, var=on_time_var: 
                               self.validate_time_entry(b_idx, "on", var.get()))
                               
            on_time = ttk.Entry(schedule_frame, width=7, textvariable=on_time_var)
            on_time.grid(column=1, row=0, padx=5, pady=5)
            self.board_time_entries[(i, "on")] = on_time
            
            ttk.Label(schedule_frame, text="OFF Time:").grid(column=2, row=0, padx=5, pady=5, sticky=tk.W)
            off_time_var = tk.StringVar(value="00:00")
            
            # Add validation callback to variable
            off_time_var.trace_add("write", lambda name, index, mode, b_idx=i, var=off_time_var: 
                                self.validate_time_entry(b_idx, "off", var.get()))
                                
            off_time = ttk.Entry(schedule_frame, width=7, textvariable=off_time_var)
            off_time.grid(column=3, row=0, padx=5, pady=5)
            self.board_time_entries[(i, "off")] = off_time
            
            # Schedule enable checkbox
            schedule_var = tk.BooleanVar(value=False)
            schedule_check = ttk.Checkbutton(
                schedule_frame,
                text="Enable Scheduling",
                variable=schedule_var,
                command=lambda b_idx=i: self.update_board_schedule(b_idx)
            )
            schedule_check.grid(column=4, row=0, padx=10, pady=5, sticky=tk.W)
            self.board_schedule_vars[i] = schedule_var
            
            # Initialize the schedule data for this board
            self.board_schedules[i] = {
                "on_time": "08:00",
                "off_time": "00:00",
                "enabled": False,
                "saved_values": {},
                "active": True  # Add active flag, default to True
            }
            
            # Individual apply button
            ttk.Button(
                frame,
                text="Apply", 
                command=lambda b_idx=i: self.apply_board_settings(b_idx)
            ).grid(column=0, row=2, pady=10, sticky=(tk.W, tk.E))
        
        # Update the display to show chambers 1-8 by default
        self.current_page = 0
        self.update_page_display()
    
    def toggle_all_lights(self):
        """Toggle all lights on or off on all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control.")
            return
        
        # Start background thread for toggling lights
        threading.Thread(
            target=self._toggle_all_lights_worker,
            daemon=True
        ).start()
    
    def _toggle_all_lights_worker(self):
        """Background worker thread for toggling all lights"""
        if self.master_on:
            # Turn OFF all lights - keep UI values but send zeros to boards
            self.master_on = False
            
            # Update button text in main thread
            self.root.after(0, lambda: self.master_button_var.set("All Lights ON"))
            
            # Save current values but don't change the UI
            self.saved_values = {}
            
            # Get values from UI in main thread
            save_results = {}
            def save_values():
                for board_idx in range(len(self.boards)):
                    for channel_name in LED_CHANNELS:
                        key = (board_idx, channel_name)
                        if key in self.led_entries:
                            try:
                                save_results[key] = self.led_entries[key].get()
                            except (ValueError, KeyError):
                                pass
            
            self.root.after(0, save_values)
            
            # Give time for the main thread to process the request
            time.sleep(0.2)
            
            # Store the saved values
            self.saved_values = save_results
            
            # Send zeros to all boards without changing UI
            active_boards = len(self.boards)
            self.pending_operations = active_boards
            
            # Update status for processing
            self.gui_queue.put(StatusUpdate("Turning all lights OFF..."))
            
            for board_idx, board in enumerate(self.boards):
                board.send_command([0, 0, 0, 0, 0, 0], 
                                  callback=lambda success, msg, idx=board_idx: 
                                      self.gui_queue.put(ToggleComplete('lights', success, msg, idx)))
        else:
            # Turn ON all lights - apply the values already in the UI
            self.master_on = True
            
            # Update button text in main thread
            self.root.after(0, lambda: self.master_button_var.set("All Lights OFF"))
            
            # Update status
            self.gui_queue.put(StatusUpdate("Restoring all light settings..."))
            
            # Apply the values that are already in the UI
            self.apply_all_settings()
    
    def on_toggle_lights_complete(self, success, board_idx):
        """Callback when a toggle lights operation completes"""
        self.pending_operations -= 1
        
        if self.pending_operations == 0:
            self.status_var.set("All lights turned OFF (settings preserved)")
    
    def toggle_scheduler(self):
        """Enable or disable the scheduler"""
        if self.scheduler_running:
            self.scheduler_running = False
            self.scheduler_button_var.set("Stop Scheduler")
            self.set_status("Scheduler stopped")
            # Cancel any pending scheduled checks
            if self.adaptive_check_timer:
                self.root.after_cancel(self.adaptive_check_timer)
        else:
            self.scheduler_running = True
            self.scheduler_button_var.set("Start Scheduler")
            self.set_status("Scheduler started")
            # Start the scheduler using after() method
            self.schedule_check()
    
    def start_scheduler(self):
        """Start the scheduler using Tkinter's after() method"""
        self.scheduler_running = True
        self.scheduler_button_var.set("Stop Scheduler")
        # Directly call schedule_check instead of starting a thread
        self.schedule_check()
    
    def schedule_check(self):
        """Periodic scheduler check using after() instead of a continuous thread"""
        if not self.scheduler_running:
            return
        
        # Start background thread for schedule checking
        threading.Thread(
            target=self._schedule_check_worker,
            daemon=True
        ).start()
            
        # Schedule the next check using adaptive interval
        self.adaptive_check_timer = self.root.after(self.scheduler_check_interval, self.schedule_check)
    
    def _schedule_check_worker(self):
        """Background worker thread for schedule checking"""
        current_datetime = datetime.now()
        current_time_str = current_datetime.strftime("%H:%M")
        current_hour, current_minute = current_datetime.hour, current_datetime.minute
        changes_made = False
        min_time_diff = float('inf')  # Track time to next scheduled event
        
        # Pre-fetch all needed schedule information for a batch check
        active_schedule_boards = {
            board_idx: schedule_info 
            for board_idx, schedule_info in self.board_schedules.items() 
            if schedule_info.get("enabled", False)
        }
        
        # Process each board with a schedule
        for board_idx, schedule_info in active_schedule_boards.items():
            # Get on_time and off_time
            on_time = schedule_info.get("on_time", "")
            off_time = schedule_info.get("off_time", "")
            
            # Extract hours and minutes - use cached pattern
            try:
                on_match = self.time_pattern.match(on_time)
                off_match = self.time_pattern.match(off_time)
                
                if on_match and off_match:
                    on_hour, on_minute = int(on_match.group(1)), int(on_match.group(2))
                    off_hour, off_minute = int(off_match.group(1)), int(off_match.group(2))
                    
                    # Calculate minutes since midnight for easy comparison
                    current_minutes = current_hour * 60 + current_minute
                    on_minutes = on_hour * 60 + on_minute
                    off_minutes = off_hour * 60 + off_minute
                    
                    # Calculate minutes until next on/off event (handling day wraparound)
                    mins_until_on = (on_minutes - current_minutes) % (24 * 60)
                    mins_until_off = (off_minutes - current_minutes) % (24 * 60)
                    
                    # Update minimum time difference for adaptive scheduling
                    min_time_diff = min(min_time_diff, mins_until_on, mins_until_off)
                    
                    # Determine if board should be active now
                    is_active = self.is_time_between(current_time_str, on_time, off_time)
                    
                    # Get previous state from cache, defaulting to None for first check
                    prev_state = self.last_schedule_state.get(board_idx, {}).get("active", None)
                    
                    # Only process if state has changed or this is the first check
                    if prev_state is None or prev_state != is_active:
                        # Update our tracking in the main thread via the queue
                        self.gui_queue.put(SchedulerUpdate(board_idx, is_active))
                        changes_made = True
                        
                        # Update the schedule state cache
                        if board_idx not in self.last_schedule_state:
                            self.last_schedule_state[board_idx] = {}
                        self.last_schedule_state[board_idx]["active"] = is_active
                        self.last_schedule_state[board_idx]["last_check"] = current_datetime
                        
                        # Log the change - use batched status updates
                        chamber_number = self.boards[board_idx].chamber_number if board_idx < len(self.boards) else board_idx+1
                        action = "ON" if is_active else "OFF"
                        self.gui_queue.put(StatusUpdate(f"Chamber {chamber_number}: Schedule activated - turning {action}"))
            except Exception as e:
                # Log error but continue processing other boards
                print(f"Error processing schedule for board {board_idx}: {str(e)}")
        
        # Calculate adaptive timer interval and update in main thread
        adaptive_interval = self.calculate_adaptive_interval(min_time_diff)
        self.root.after(0, lambda interval=adaptive_interval: setattr(self, 'scheduler_check_interval', interval))
    
    def calculate_adaptive_interval(self, min_time_diff):
        """Calculate adaptive timer interval based on time to next event"""
        # Use cached timing values for better performance
        if min_time_diff != float('inf'):
            # Check more frequently when close to a scheduled event
            if min_time_diff <= 1:  # Within 1 minute
                return self.timings['scheduler_urgent']  # Check very frequently
            elif min_time_diff <= 5:  # Within 5 minutes
                return self.timings['scheduler_normal']  # Check normally
            else:
                # If next event is far, check less frequently
                return self.timings['scheduler_relaxed']  # Check less frequently
        
        return self.timings['scheduler_default']  # Default interval
    
    def apply_changed_boards(self, force=True):
        """Apply settings only to boards that have changed states"""
        if not hasattr(self, 'changed_boards') or not self.changed_boards:
            return
        
        # If not forcing an update, check if we've updated recently
        if not force:
            # Check if we have a "last_batch_update" timestamp
            last_update = getattr(self, 'last_batch_update', 0)
            current_time = time.time()
            
            # If we've updated within the last second, defer this update
            if current_time - last_update < 1.0:
                self.root.after(1000, lambda: self.apply_changed_boards(True))
                return
                
        # NEW: Convert set to list once to avoid copying during iteration
        boards_to_update = list(self.changed_boards)
        
        # Update at most 3 boards at once to avoid GUI freezing
        max_updates = 3
        
        if len(boards_to_update) > max_updates:
            # Process some boards now, defer the rest
            current_batch = boards_to_update[:max_updates]
            
            # Process current batch
            for board_idx in current_batch:
                if board_idx < len(self.boards):
                    self.apply_board_settings(board_idx)
                    # Only remove AFTER successful processing 
                    if board_idx in self.changed_boards:
                        self.changed_boards.remove(board_idx)
                
            # Schedule deferred batch with a small delay - ALWAYS FORCE=TRUE for deferred batch
            self.root.after(100, lambda: self.apply_changed_boards(True))
        else:
            # Process all boards at once
            for board_idx in boards_to_update:
                if board_idx < len(self.boards):
                    self.apply_board_settings(board_idx)
            
            # Clear the set of changed boards
            self.changed_boards.clear()  # More efficient than creating a new empty set
        
        # Update the last batch update timestamp
        self.last_batch_update = time.time()
    
    def send_zeros_to_board(self, board_idx):
        """Send zeros to the board without changing UI values"""
        if board_idx >= len(self.boards):
            return False
        
        board = self.boards[board_idx]
        # Send command with all zeros directly (non-blocking now)
        board.send_command([0, 0, 0, 0, 0, 0])
        return True
    
    def scan_boards(self):
        """Detect and initialize connections to XIAO RP2040 boards"""
        # Clear previous boards and GUI elements
        for board in self.boards:
            board.disconnect()
        self.boards = []
        
        for frame in self.board_frames:
            frame.destroy()
        self.board_frames = []
        self.led_entries = {}
        
        # Reset master button state
        self.master_on = True
        self.master_button_var.set("All Lights OFF")
        self.saved_values = {}
        
        # Set status
        self.status_var.set(self.cmd_messages['scan_start'])
        
        # Start background thread for scanning
        self.background_operations['scan'] = True
        threading.Thread(
            target=self._scan_boards_worker,
            daemon=True
        ).start()
    
    def _scan_boards_worker(self):
        """Background worker thread for scanning boards"""
        try:
            # Make sure chamber mapping is loaded
            self.load_chamber_mapping()
            
            # Detect connected boards
            detected_boards = self.detect_xiao_boards()
            
            if not detected_boards:
                self.gui_queue.put(StatusUpdate("No boards found"))
                self.gui_queue.put(BoardsDetected([]))
                return
            
            # Create board connections
            boards = []
            for port, serial_number, chamber_number in detected_boards:
                boards.append(BoardConnection(port, serial_number, chamber_number))
            
            # Send result to main thread
            self.gui_queue.put(BoardsDetected(boards))
            
        except Exception as e:
            # Send error to main thread
            self.gui_queue.put(BoardsDetected([], str(e)))
        finally:
            # Clear operation flag
            self.background_operations['scan'] = False
    
    def detect_xiao_boards(self):
        """Detect connected XIAO RP2040 boards and assign chamber numbers"""
        results = []
        
        # Use the cached port info if available
        global CACHED_PORT_INFO
        
        # If cache is empty or needs refresh, initialize it
        if not CACHED_PORT_INFO:
            self.initialize_port_cache()
        
        # Use the cache for faster detection
        for port, info in CACHED_PORT_INFO.items():
            serial_number = info['serial_number']
            
            # Assign chamber number from cached mapping
            chamber_number = self.chamber_mapping.get(serial_number)
            
            # If no chamber number found, assign a high number (ensuring it comes after known chambers)
            if chamber_number is None:
                chamber_number = 100  # High number to appear at end when sorted
                self.set_status(f"Warning: Board with S/N {serial_number} not found in chamber mapping")
            
            results.append([port, serial_number, chamber_number])
        
        return results
    
    def validate_percentage(self, value):
        """Validate that entry is a valid percentage (0-100)"""
        if value == "":
            return True
        
        try:
            val = int(value)
            return 0 <= val <= 100
        except ValueError:
            return False
    
    def apply_all_settings(self):
        """Apply settings to all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to apply settings to.")
            return
            
        # Track completion status
        active_boards = len(self.boards)
        self.pending_operations = active_boards
        
        # Update status
        self.status_var.set(self.cmd_messages['apply_start'])
        
        # Start background thread for applying settings
        self.background_operations['apply_all'] = True
        threading.Thread(
            target=self._apply_all_settings_worker,
            daemon=True
        ).start()
    
    def _apply_all_settings_worker(self):
        """Background worker thread for applying settings to all boards"""
        try:
            # First, check all schedules to provide a combined status update
            scheduled_boards = 0
            active_schedules = 0
            inactive_schedules = 0
            
            for i in range(len(self.boards)):
                if i in self.board_schedules and self.board_schedules[i].get("enabled", False):
                    scheduled_boards += 1
                    is_active = self.check_board_active_state(i)
                    if is_active:
                        active_schedules += 1
                    else:
                        inactive_schedules += 1
            
            # Show summary of schedule states if any schedules are enabled
            if scheduled_boards > 0:
                status_message = f"Applying settings to {len(self.boards)} boards - " + \
                                f"{scheduled_boards} with schedules: {active_schedules} in ON period, " + \
                                f"{inactive_schedules} in OFF period"
                self.gui_queue.put(StatusUpdate(status_message))
            
            # Apply settings to each board based on its schedule
            for i in range(len(self.boards)):
                # Apply settings - the schedule state is checked inside _apply_board_settings_worker
                self._apply_board_settings_worker(i)
                # Small delay between boards to avoid overwhelming the system
                time.sleep(0.1)
                
        finally:
            # Clear operation flag
            self.background_operations['apply_all'] = False
            
            # Add a final status update to confirm completion
            if scheduled_boards > 0:
                self.gui_queue.put(StatusUpdate(f"Settings applied - boards with scheduled OFF time have lights turned off"))
            else:
                self.gui_queue.put(StatusUpdate("All settings applied successfully"))
    
    def apply_board_settings(self, board_idx):
        """Apply settings for a specific board"""
        if board_idx >= len(self.boards):
            messagebox.showerror("Error", "Invalid board index")
            return
        
        # Start background thread for applying settings to this board
        threading.Thread(
            target=self._apply_board_settings_worker,
            args=(board_idx,),
            daemon=True
        ).start()
    
    def _apply_board_settings_worker(self, board_idx):
        """Background worker thread for applying settings to a single board"""
        if board_idx >= len(self.boards):
            return
        
        board = self.boards[board_idx]
        duty_values = []
        
        # Check if scheduling is enabled and get active state
        scheduling_enabled = False
        schedule_active = True
        if board_idx in self.board_schedules:
            scheduling_enabled = self.board_schedules[board_idx].get("enabled", False)
            # If scheduling is enabled, check current active state
            if scheduling_enabled:
                schedule_active = self.check_board_active_state(board_idx)
        
        # Always save the current UI values to ensure they're preserved
        self.save_board_ui_values(board_idx)
        
        # Get duty cycle values from UI for each channel
        for channel in LED_CHANNELS:
            try:
                # Thread-safe access to tkinter variables requires special care
                # Access the entries in a thread-safe way
                entry_key = (board_idx, channel)
                percentage = 0
                
                # Schedule a task on the main thread to get the value and wait for the result
                percentage_result = {}
                def get_percentage():
                    try:
                        if entry_key in self.led_entries:
                            percentage_result['value'] = int(self.led_entries[entry_key].get())
                    except ValueError:
                        percentage_result['value'] = 0
                
                self.root.after(0, get_percentage)
                
                # Wait for the main thread to process the request (with timeout)
                timeout = time.time() + 1.0  # 1 second timeout
                while 'value' not in percentage_result and time.time() < timeout:
                    time.sleep(0.01)
                
                percentage = percentage_result.get('value', 0)
                duty = self.duty_cycle_from_percentage(percentage)
                duty_values.append(duty)
            except Exception:
                duty_values.append(0)
        
        # If scheduling is active, we may need to override the duty values
        if scheduling_enabled and not schedule_active:
            # Board should be off according to schedule
            status_msg = f"Board {board_idx+1}: Applying zeros to hardware (schedule OFF time, UI settings preserved)"
            self.gui_queue.put(StatusUpdate(status_msg))
            
            # Send zeros to board, but don't change UI
            success, msg = False, "Command not sent"
            
            def on_command_complete(cmd_success, cmd_msg):
                nonlocal success, msg
                success, msg = cmd_success, cmd_msg
                # Send result to main thread
                self.gui_queue.put(SettingsApplied(
                    board_idx, cmd_success, cmd_msg, "during scheduled OFF time"))
            
            # Send command with all zeros directly
            board.send_command([0, 0, 0, 0, 0, 0], callback=on_command_complete)
            
        else:
            # Board should be on - apply actual values from UI
            status_msg = "Applying settings..."
            if scheduling_enabled and schedule_active:
                status_msg = "Applying settings (during scheduled ON time)..."
            self.gui_queue.put(StatusUpdate(f"Board {board_idx+1}: {status_msg}"))
            
            success, msg = False, "Command not sent"
            
            def on_command_complete(cmd_success, cmd_msg):
                nonlocal success, msg
                success, msg = cmd_success, cmd_msg
                extra_info = "during scheduled ON time" if scheduling_enabled and schedule_active else None
                # Send result to main thread
                self.gui_queue.put(SettingsApplied(board_idx, cmd_success, cmd_msg, extra_info))
            
            # Send command with duty values
            board.send_command(duty_values, callback=on_command_complete)
    
    def toggle_all_fans(self):
        """Toggle all fans on or off on all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control fans.")
            return
        
        # Start background thread for toggling fans
        threading.Thread(
            target=self._toggle_all_fans_worker,
            daemon=True
        ).start()
    
    def _toggle_all_fans_worker(self):
        """Background worker thread for toggling all fans"""
        if self.fans_on:
            # Turn off all fans
            self.fans_on = False
            
            # Update button text in main thread
            self.root.after(0, lambda: self.fan_button_var.set("Turn Fans ON"))
            
            # Update status
            self.gui_queue.put(StatusUpdate("Turning all fans OFF..."))
            
            # Track completion status
            active_boards = len(self.boards)
            self.pending_fan_operations = active_boards
            
            for i, board in enumerate(self.boards):
                board.turn_fan_off(callback=lambda success, msg, idx=i: 
                                  self.gui_queue.put(ToggleComplete('fans', success, msg, idx, False)))
        else:
            # Turn on all fans
            self.fans_on = True
            
            # Update button text in main thread
            self.root.after(0, lambda: self.fan_button_var.set("Turn Fans OFF"))
            
            # Get the speed from the entry
            speed_result = {'value': 50}  # Default
            
            def get_speed():
                try:
                    speed_result['value'] = int(self.fan_speed_var.get())
                except ValueError:
                    speed_result['value'] = 50  # Default
                    self.fan_speed_var.set("50")
            
            self.root.after(0, get_speed)
            
            # Wait briefly for the main thread to process
            time.sleep(0.1)
            
            speed = speed_result['value']
            
            # Update status
            self.gui_queue.put(StatusUpdate(f"Turning all fans ON at {speed}%..."))
            
            # Track completion status
            active_boards = len(self.boards)
            self.pending_fan_operations = active_boards
            
            for i, board in enumerate(self.boards):
                board.set_fan_speed(speed, callback=lambda success, msg, idx=i: 
                                   self.gui_queue.put(ToggleComplete('fans', success, msg, idx, True)))
    
    def apply_fan_settings(self):
        """Apply the fan speed to all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control fans.")
            return
        
        # Start background thread for applying fan settings
        threading.Thread(
            target=self._apply_fan_settings_worker,
            daemon=True
        ).start()
    
    def _apply_fan_settings_worker(self):
        """Background worker thread for applying fan settings"""
        # Get speed value from UI in main thread
        speed_result = {'value': None, 'error': False}
        
        def get_speed():
            try:
                speed_result['value'] = int(self.fan_speed_var.get())
            except ValueError:
                speed_result['error'] = True
                speed_result['value'] = 0
        
        self.root.after(0, get_speed)
        
        # Wait briefly for the main thread to process
        time.sleep(0.1)
        
        if speed_result['error']:
            self.gui_queue.put(StatusUpdate("Invalid fan speed value. Please enter a number between 0-100.", True))
            return
        
        speed = speed_result['value']
        
        # Update status
        self.gui_queue.put(StatusUpdate(f"Setting all fans to {speed}%..."))
        
        # Track completion status
        active_boards = len(self.boards)
        self.pending_fan_operations = active_boards
        
        # Update fan button state based on speed
        if speed > 0 and not self.fans_on:
            self.fans_on = True
            self.root.after(0, lambda: self.fan_button_var.set("Turn Fans OFF"))
        elif speed == 0 and self.fans_on:
            self.fans_on = False
            self.root.after(0, lambda: self.fan_button_var.set("Turn Fans ON"))
        
        for i, board in enumerate(self.boards):
            board.set_fan_speed(speed, callback=lambda success, msg, idx=i: 
                               self.gui_queue.put(ToggleComplete('fans', success, msg, idx, speed > 0)))
    
    def export_settings(self):
        """Export current LED settings and schedules to a text file"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to export settings from.")
            return
            
        # Start background thread for exporting settings
        threading.Thread(
            target=self._export_settings_worker,
            daemon=True
        ).start()
    
    def _export_settings_worker(self):
        """Background worker thread for exporting settings"""
        try:
            # Get file path from user in main thread
            file_path_result = {'path': None, 'selected': False}
            
            def get_file_path():
                path = filedialog.asksaveasfilename(
                    initialdir=DEFAULT_DOCUMENTS_PATH,
                    defaultextension=".json",
                    filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
                    title="Save LED Settings"
                )
                file_path_result['path'] = path
                file_path_result['selected'] = True
            
            self.root.after(0, get_file_path)
            
            # Wait for the file dialog to complete
            timeout = time.time() + 60  # 60 second timeout
            while not file_path_result['selected'] and time.time() < timeout:
                time.sleep(0.1)
            
            file_path = file_path_result['path']
            if not file_path:
                return  # User canceled
            
            # Collect settings from UI in main thread
            settings_result = {'data': None, 'collected': False}
            
            def collect_settings():
                settings = {}
                
                for board_idx, board in enumerate(self.boards):
                    chamber_number = board.chamber_number
                    board_settings = {"intensity": {}, "schedule": {}, "fan": {}}
                    
                    # Get intensity settings
                    for channel_name in LED_CHANNELS:
                        try:
                            value = int(self.led_entries[(board_idx, channel_name)].get())
                            board_settings["intensity"][channel_name] = value
                        except (ValueError, KeyError):
                            board_settings["intensity"][channel_name] = 0
                    
                    # Get board-level schedule settings
                    if board_idx in self.board_schedules:
                        board_settings["schedule"] = {
                            "on_time": self.board_schedules[board_idx].get("on_time", "08:00"),
                            "off_time": self.board_schedules[board_idx].get("off_time", "00:00"),
                            "enabled": self.board_schedules[board_idx].get("enabled", False)
                        }
                    
                    # Add fan settings
                    board_settings["fan"] = {
                        "enabled": board.fan_enabled,
                        "speed": board.fan_speed
                    }
                    
                    # Use chamber number as the key instead of board index
                    settings[f"chamber_{chamber_number}"] = board_settings
                
                settings_result['data'] = settings
                settings_result['collected'] = True
            
            self.root.after(0, collect_settings)
            
            # Wait for settings collection to complete
            timeout = time.time() + 10  # 10 second timeout
            while not settings_result['collected'] and time.time() < timeout:
                time.sleep(0.1)
            
            settings = settings_result['data']
            if not settings:
                self.gui_queue.put(FileOperationComplete('export', False, "Failed to collect settings data"))
                return
            
            # Save to file (this is the actual blocking I/O operation)
            with open(file_path, 'w') as f:
                json.dump(settings, f, indent=4)
            
            # Send success message
            self.gui_queue.put(FileOperationComplete('export', True, file_path))
            
        except Exception as e:
            # Send error message
            self.gui_queue.put(FileOperationComplete('export', False, str(e)))
    
    def import_settings(self):
        """Import LED settings and schedules from a text file and apply them"""
        # Start background thread for importing settings
        threading.Thread(
            target=self._import_settings_worker,
            daemon=True
        ).start()
    
    def _import_settings_worker(self):
        """Background worker thread for importing settings"""
        try:
            # Get file path from user in main thread
            file_path_result = {'path': None, 'selected': False}
            
            def get_file_path():
                path = filedialog.askopenfilename(
                    initialdir=DEFAULT_DOCUMENTS_PATH,
                    filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
                    title="Import LED Settings"
                )
                file_path_result['path'] = path
                file_path_result['selected'] = True
            
            self.root.after(0, get_file_path)
            
            # Wait for the file dialog to complete
            timeout = time.time() + 60  # 60 second timeout
            while not file_path_result['selected'] and time.time() < timeout:
                time.sleep(0.1)
            
            file_path = file_path_result['path']
            if not file_path:
                return  # User canceled
            
            # Read settings from file (blocking I/O operation)
            with open(file_path, 'r') as f:
                settings = json.load(f)
            
            # Validate imported data format
            if not isinstance(settings, dict):
                self.gui_queue.put(FileOperationComplete('import', False, "Invalid settings file format"))
                return
                
            # Make sure we have boards to apply settings to
            if not self.boards:
                self.gui_queue.put(FileOperationComplete('import', False, "No boards connected to apply settings to."))
                return
                
            # Apply settings to GUI entries in main thread
            apply_result = {'applied_count': 0, 'fan_settings_found': False, 'complete': False}
            
            def apply_settings_to_ui():
                applied_count = 0
                fan_settings_found = False
                
                for board_key, board_settings in settings.items():
                    try:
                        # Extract chamber number (format: "chamber_X")
                        match = self.chamber_num_pattern.match(board_key)
                        if not match:
                            continue
                            
                        chamber_number = int(match.group(1))
                        
                        # Use chamber-to-board mapping for O(1) lookup
                        board_idx = self.chamber_to_board_idx.get(chamber_number)
                        if board_idx is None:
                            continue  # Skip if chamber number is not found
                        
                        # Apply intensity settings
                        if "intensity" in board_settings:
                            for channel_name, value in board_settings["intensity"].items():
                                if channel_name in LED_CHANNELS and (board_idx, channel_name) in self.led_entries:
                                    self.led_entries[(board_idx, channel_name)].delete(0, tk.END)
                                    self.led_entries[(board_idx, channel_name)].insert(0, str(value))
                                    applied_count += 1
                        
                        # Apply schedule settings
                        if "schedule" in board_settings:
                            schedule = board_settings["schedule"]
                            # Update time entries
                            if "on_time" in schedule and (board_idx, "on") in self.board_time_entries:
                                self.board_time_entries[(board_idx, "on")].delete(0, tk.END)
                                self.board_time_entries[(board_idx, "on")].insert(0, schedule["on_time"])
                            
                            if "off_time" in schedule and (board_idx, "off") in self.board_time_entries:
                                self.board_time_entries[(board_idx, "off")].delete(0, tk.END)
                                self.board_time_entries[(board_idx, "off")].insert(0, schedule["off_time"])
                            
                            # Update checkbox
                            if "enabled" in schedule and board_idx in self.board_schedule_vars:
                                self.board_schedule_vars[board_idx].set(schedule["enabled"])
                            
                            # Update internal schedule data
                            if board_idx in self.board_schedules:
                                self.board_schedules[board_idx].update({
                                    "on_time": schedule.get("on_time", "08:00"),
                                    "off_time": schedule.get("off_time", "00:00"),
                                    "enabled": schedule.get("enabled", False)
                                })
                            
                            applied_count += 1
                        
                        # Apply fan settings if present
                        if "fan" in board_settings:
                            fan = board_settings["fan"]
                            fan_settings_found = True
                            
                            # Only set the fan speed in the UI for the first board with settings
                            if fan_settings_found and board_idx == 0:
                                self.fan_speed_var.set(str(fan.get("speed", 50)))
                                # Update fan button state
                                if fan.get("enabled", False):
                                    self.fans_on = True
                                    self.fan_button_var.set("Turn Fans OFF")
                                else:
                                    self.fans_on = False
                                    self.fan_button_var.set("Turn Fans ON")
                            
                            applied_count += 1
                    except (ValueError, IndexError, KeyError):
                        continue  # Skip invalid entries
                
                apply_result['applied_count'] = applied_count
                apply_result['fan_settings_found'] = fan_settings_found
                apply_result['complete'] = True
            
            self.root.after(0, apply_settings_to_ui)
            
            # Wait for settings application to complete
            timeout = time.time() + 10  # 10 second timeout
            while not apply_result['complete'] and time.time() < timeout:
                time.sleep(0.1)
            
            # Send success message with data for further action
            self.gui_queue.put(FileOperationComplete(
                'import',
                True,
                f"Imported settings from {file_path}",
                {'applied_count': apply_result['applied_count'], 
                 'fan_settings_found': apply_result['fan_settings_found']}
            ))
            
        except Exception as e:
            # Send error message
            self.gui_queue.put(FileOperationComplete('import', False, str(e)))
    
    def check_board_active_state(self, board_idx):
        """Check if a board should be active based on current schedule"""
        if board_idx not in self.board_schedules:
            return True  # Default to active if no schedule
        
        schedule_info = self.board_schedules[board_idx]
        if not schedule_info.get("enabled", False):
            return True  # Not using scheduling, so always active
        
        # Get time values - cache the current time to ensure consistent checking
        current_time = datetime.now().strftime("%H:%M")
        on_time = schedule_info.get("on_time", "08:00")
        off_time = schedule_info.get("off_time", "00:00")
        
        # Validate time formats
        if not self.validate_time_format(on_time) or not self.validate_time_format(off_time):
            # If time format is invalid, default to active (safer)
            self.gui_queue.put(StatusUpdate(f"Board {board_idx+1}: WARNING - Invalid schedule time format. Using default ON state."))
            return True
        
        is_active = self.is_time_between(current_time, on_time, off_time)
        
        # Update the active state in our tracking
        schedule_info["active"] = is_active
        
        # Log the time check result for debugging
        chamber_number = self.boards[board_idx].chamber_number if board_idx < len(self.boards) else board_idx+1
        status = "ON" if is_active else "OFF"
        self.gui_queue.put(StatusUpdate(f"Chamber {chamber_number}: Schedule check - current time {current_time} is {status} ({on_time} to {off_time})"))
        
        return is_active
    
    def save_board_ui_values(self, board_idx):
        """Save current UI values for a board without changing them"""
        if board_idx not in self.board_schedules:
            self.board_schedules[board_idx] = {"enabled": False, "active": True, "saved_values": {}}
        
        saved_values = {}
        for channel_name in LED_CHANNELS:
            key = (board_idx, channel_name)
            if key in self.led_entries:
                try:
                    saved_values[channel_name] = self.led_entries[key].get()
                except:
                    saved_values[channel_name] = "0"
        self.board_schedules[board_idx]["saved_values"] = saved_values
        return saved_values
    
    def validate_time_format(self, time_str):
        """Validate that the time string is in HH:MM format (24-hour)"""
        if time_str == "":
            return True  # Empty is okay during typing
            
        # Use pre-compiled pattern
        return bool(self.time_pattern.match(time_str))
    
    def validate_time_entry(self, board_idx, entry_type, new_value):
        """Validate time entry and provide feedback"""
        if self.validate_time_format(new_value):
            # Valid format - reset any previous error styling
            key = (board_idx, entry_type)
            if key in self.board_time_entries:
                self.board_time_entries[key].config(foreground=self.cached_colors['normal'])
            return True
        else:
            # Invalid format - set error styling
            key = (board_idx, entry_type)
            if key in self.board_time_entries:
                self.board_time_entries[key].config(foreground=self.cached_colors['error'])
            return False
    
    # NEW: Add methods for batch widget updates
    def queue_widget_update(self, widget_id, update_type, value):
        """Queue a widget update to be processed in batches"""
        self.widget_update_queue.put((widget_id, update_type, value))
        
        # Ensure the processor is running
        if not self.update_batch_timer:
            self.process_widget_updates()
    
    def process_widget_updates(self):
        """Process queued widget updates in batches"""
        if self.is_updating_widgets:
            # Already processing updates, just reschedule
            self.update_batch_timer = self.root.after(
                self.update_batch_interval, self.process_widget_updates)
            return
            
        self.is_updating_widgets = True
        
        # Create a dictionary to store only the latest update for each widget
        updates_by_widget = {}
        update_count = 0
        
        # Process all queued updates
        try:
            while not self.widget_update_queue.empty() and update_count < 50:  # Limit updates per batch
                widget_id, update_type, value = self.widget_update_queue.get_nowait()
                # Only keep the most recent update for each widget
                updates_by_widget[(widget_id, update_type)] = value
                update_count += 1
        except queue.Empty:
            pass
        
        # Apply the batched updates
        for (widget_id, update_type), value in updates_by_widget.items():
            try:
                if update_type == "text":
                    # Update text in an entry
                    widget = self.led_entries.get(widget_id)
                    if widget:
                        current = widget.get()
                        if current != value:
                            widget.delete(0, tk.END)
                            widget.insert(0, value)
                elif update_type == "color":
                    # Update foreground color - use cached colors
                    widget = self.board_time_entries.get(widget_id)
                    if widget:
                        widget.config(foreground=self.cached_colors.get(value, value))
                elif update_type == "check":
                    # Update checkbox state
                    var = self.board_schedule_vars.get(widget_id)
                    if var and var.get() != value:
                        var.set(value)
                elif update_type == "enable":
                    # Enable/disable a widget
                    widget = self.board_frames[widget_id] if widget_id < len(self.board_frames) else None
                    if widget:
                        widget.config(state=value)
                elif update_type == "background":
                    # Update background color
                    widget = None
                    if isinstance(widget_id, tuple) and widget_id[0] == "board":
                        board_idx = widget_id[1]
                        if board_idx < len(self.board_frames):
                            widget = self.board_frames[board_idx]
                    if widget:
                        widget.config(background=self.cached_colors.get(value, value))
            except Exception as e:
                print(f"Error updating widget {widget_id}: {str(e)}")
        
        self.is_updating_widgets = False
        
        # Schedule the next batch processing
        self.update_batch_timer = self.root.after(
            self.update_batch_interval, self.process_widget_updates)
    
    def set_status(self, message):
        """Batch status updates to reduce status bar redraws"""
        if not hasattr(self, 'status_update_batch'):
            self.status_update_batch = []
            
        # Add the message to the batch
        self.status_update_batch.append(message)
        
        # If there's already a pending update, let it handle this message
        if self.status_update_timer:
            return
            
        # Schedule the status update
        self.status_update_timer = self.root.after(
            self.timings['status_update_batch'], self.process_status_updates)
    
    def process_status_updates(self):
        """Process batched status updates"""
        self.status_update_timer = None
        
        if not self.status_update_batch:
            return
            
        # Use the most recent status message for efficiency
        latest_message = self.status_update_batch[-1]
        self.status_var.set(latest_message)
        
        # Clear the batch
        self.status_update_batch = []
    
    def apply_board_settings(self, board_idx):
        """Apply settings for a specific board"""
        if board_idx >= len(self.boards):
            messagebox.showerror("Error", "Invalid board index")
            return
        
        # Start background thread for applying settings to this board
        threading.Thread(
            target=self._apply_board_settings_worker,
            args=(board_idx,),
            daemon=True
        ).start()
    
    def _apply_board_settings_worker(self, board_idx):
        """Background worker thread for applying settings to a single board"""
        if board_idx >= len(self.boards):
            return
        
        board = self.boards[board_idx]
        duty_values = []
        
        # Check if scheduling is enabled and get active state
        scheduling_enabled = False
        schedule_active = True
        if board_idx in self.board_schedules:
            scheduling_enabled = self.board_schedules[board_idx].get("enabled", False)
            # If scheduling is enabled, check current active state
            if scheduling_enabled:
                schedule_active = self.check_board_active_state(board_idx)
        
        # Always save the current UI values to ensure they're preserved
        self.save_board_ui_values(board_idx)
        
        # Get duty cycle values from UI for each channel
        for channel in LED_CHANNELS:
            try:
                # Thread-safe access to tkinter variables requires special care
                # Access the entries in a thread-safe way
                entry_key = (board_idx, channel)
                percentage = 0
                
                # Schedule a task on the main thread to get the value and wait for the result
                percentage_result = {}
                def get_percentage():
                    try:
                        if entry_key in self.led_entries:
                            percentage_result['value'] = int(self.led_entries[entry_key].get())
                    except ValueError:
                        percentage_result['value'] = 0
                
                self.root.after(0, get_percentage)
                
                # Wait for the main thread to process the request (with timeout)
                timeout = time.time() + 1.0  # 1 second timeout
                while 'value' not in percentage_result and time.time() < timeout:
                    time.sleep(0.01)
                
                percentage = percentage_result.get('value', 0)
                duty = self.duty_cycle_from_percentage(percentage)
                duty_values.append(duty)
            except Exception:
                duty_values.append(0)
        
        # If scheduling is active, we may need to override the duty values
        if scheduling_enabled and not schedule_active:
            # Board should be off according to schedule
            status_msg = f"Board {board_idx+1}: Applying zeros to hardware (schedule OFF time, UI settings preserved)"
            self.gui_queue.put(StatusUpdate(status_msg))
            
            # Send zeros to board, but don't change UI
            success, msg = False, "Command not sent"
            
            def on_command_complete(cmd_success, cmd_msg):
                nonlocal success, msg
                success, msg = cmd_success, cmd_msg
                # Send result to main thread
                self.gui_queue.put(SettingsApplied(
                    board_idx, cmd_success, cmd_msg, "during scheduled OFF time"))
            
            # Send command with all zeros directly
            board.send_command([0, 0, 0, 0, 0, 0], callback=on_command_complete)
            
        else:
            # Board should be on - apply actual values from UI
            status_msg = "Applying settings..."
            if scheduling_enabled and schedule_active:
                status_msg = "Applying settings (during scheduled ON time)..."
            self.gui_queue.put(StatusUpdate(f"Board {board_idx+1}: {status_msg}"))
            
            success, msg = False, "Command not sent"
            
            def on_command_complete(cmd_success, cmd_msg):
                nonlocal success, msg
                success, msg = cmd_success, cmd_msg
                extra_info = "during scheduled ON time" if scheduling_enabled and schedule_active else None
                # Send result to main thread
                self.gui_queue.put(SettingsApplied(board_idx, cmd_success, cmd_msg, extra_info))
            
            # Send command with duty values
            board.send_command(duty_values, callback=on_command_complete)
    
    def update_board_schedule(self, board_idx):
        """Update the schedule for a specific board"""
        if board_idx not in self.board_schedules:
            self.board_schedules[board_idx] = {"enabled": False, "active": True, "saved_values": {}}
            
        # Get current values from widgets
        was_enabled = self.board_schedules[board_idx].get("enabled", False)
        is_enabled = self.board_schedule_vars[board_idx].get()
        
        # Validate time entries before applying
        on_time_valid = False
        off_time_valid = False
        
        if (board_idx, "on") in self.board_time_entries:
            on_time = self.board_time_entries[(board_idx, "on")].get()
            on_time_valid = self.validate_time_format(on_time)
            if on_time_valid:
                self.board_schedules[board_idx]["on_time"] = on_time
        
        if (board_idx, "off") in self.board_time_entries:
            off_time = self.board_time_entries[(board_idx, "off")].get()
            off_time_valid = self.validate_time_format(off_time)
            if off_time_valid:
                self.board_schedules[board_idx]["off_time"] = off_time
        
        # If times are invalid, don't enable scheduling
        if is_enabled and (not on_time_valid or not off_time_valid):
            messagebox.showerror("Invalid Time Format", 
                "Scheduling cannot be enabled with invalid time format. Please use HH:MM (24-hour) format.")
            # Reset the checkbox - use queued update
            self.queue_widget_update(board_idx, "check", False)
            is_enabled = False
        
        self.board_schedules[board_idx]["enabled"] = is_enabled
        # If scheduling was just enabled, check if we need to update active state
        if is_enabled:
            # Check current active state
            is_active = self.check_board_active_state(board_idx)
            # If we're outside active hours and just enabled scheduling, turn off lights
            if not is_active:
                # Outside of ON period - send zeros but keep UI values
                self.set_status(f"Board {board_idx+1}: Schedule enabled, outside ON hours - turning lights off (settings preserved)")
                # Send direct command to turn off LEDs without changing UI
                self.send_zeros_to_board(board_idx)
        elif was_enabled and not is_enabled:
            # Scheduling was just disabled, ensure active state is reset
            self.board_schedules[board_idx]["active"] = True
            # Re-apply the current UI settings to restore the board state
            self.set_status(f"Board {board_idx+1}: Schedule disabled - applying current settings")
            self.apply_board_settings(board_idx)
    
    def is_time_between(self, check_time, start_time, end_time):
        """Check if a time is between start and end times (handling day wraparound)"""
        # Use cached function for better performance
        # Extract hour and minute from time strings using cached regex pattern
        check_match = self.time_pattern.match(check_time)
        start_match = self.time_pattern.match(start_time)
        end_match = self.time_pattern.match(end_time)
        
        if not (check_match and start_match and end_match):
            return False
            
        check_hour, check_minute = int(check_match.group(1)), int(check_match.group(2))
        start_hour, start_minute = int(start_match.group(1)), int(start_match.group(2))
        end_hour, end_minute = int(end_match.group(1)), int(end_match.group(2))
        
        # Convert to minutes since midnight for easier comparison
        check_minutes = check_hour * 60 + check_minute
        start_minutes = start_hour * 60 + start_minute
        end_minutes = end_hour * 60 + end_minute
        
        if start_minutes <= end_minutes:
            # Normal case: start time is before end time
            return start_minutes <= check_minutes <= end_minutes
        else:
            # Wraparound case: end time is on the next day
            return check_minutes >= start_minutes or check_minutes <= end_minutes
    
    def process_gui_queue(self):
        """Process GUI action queue from worker threads"""
        try:
            # Process all available actions (up to a reasonable limit)
            for _ in range(10):
                action = self.gui_queue.get_nowait()
                
                # Handle different action types
                if isinstance(action, StatusUpdate):
                    self.status_var.set(action.message)
                    if action.is_error:
                        # Show error dialog for critical errors
                        messagebox.showerror("Error", action.message)
                
                elif isinstance(action, BoardsDetected):
                    if action.error:
                        messagebox.showerror("Error Scanning Boards", action.error)
                        self.status_var.set(f"Error: {action.error}")
                    else:
                        self.boards = action.boards
                        self.create_board_frames()
                        
                        # Count boards by chamber ranges
                        chambers_1_8 = sum(1 for board in self.boards if 1 <= board.chamber_number <= 8)
                        chambers_9_16 = sum(1 for board in self.boards if 9 <= board.chamber_number <= 16)
                        
                        self.status_var.set(f"Found {len(self.boards)} board(s): {chambers_1_8} in chambers 1-8, {chambers_9_16} in chambers 9-16")
                
                elif isinstance(action, SettingsApplied):
                    if action.board_idx >= len(self.boards):
                        continue
                        
                    chamber_number = self.boards[action.board_idx].chamber_number
                    if action.success:
                        if action.extra_info:
                            self.set_status(f"Chamber {chamber_number}: Settings applied ({action.extra_info})")
                        else:
                            self.set_status(f"Chamber {chamber_number}: Settings applied successfully")
                    else:
                        messagebox.showerror(f"Error - Chamber {chamber_number}", action.message)
                        self.set_status(f"Chamber {chamber_number}: Error - {action.message}")
                
                elif isinstance(action, ToggleComplete):
                    if action.operation_type == 'lights':
                        self.pending_operations -= 1
                        if self.pending_operations == 0:
                            state_msg = "OFF" if not self.master_on else "ON"
                            self.status_var.set(f"All lights turned {state_msg} (settings preserved)")
                    
                    elif action.operation_type == 'fans':
                        self.pending_fan_operations -= 1
                        if not action.success and action.board_idx is not None:
                            messagebox.showerror(f"Error - Board {action.board_idx+1}", action.message)
                        
                        if self.pending_fan_operations == 0:
                            if action.state is not None:
                                action_text = "ON" if action.state else "OFF"
                                speed_info = f" at {self.fan_speed_var.get()}%" if action.state else ""
                                self.status_var.set(f"All fans turned {action_text}{speed_info}")
                
                elif isinstance(action, FileOperationComplete):
                    if action.operation_type == 'import':
                        if action.success:
                            self.status_var.set(f"Imported settings: {action.message}")
                            if action.data and 'applied_count' in action.data:
                                if messagebox.askyesno("Apply Settings", 
                                                     f"Successfully loaded {action.data['applied_count']} settings from file.\n\nDo you want to apply these settings to the boards now?"):
                                    self.apply_all_settings()
                                    if action.data.get('fan_settings_found', False):
                                        self.apply_fan_settings()
                        else:
                            messagebox.showerror("Import Error", f"Error importing settings: {action.message}")
                            self.status_var.set(f"Import error: {action.message}")
                    
                    elif action.operation_type == 'export':
                        if action.success:
                            self.status_var.set(f"Settings exported to {action.message}")
                            messagebox.showinfo("Export Successful", f"Settings successfully exported to {action.message}")
                        else:
                            messagebox.showerror("Export Error", f"Error exporting settings: {action.message}")
                            self.status_var.set(f"Export error: {action.message}")
                
                elif isinstance(action, SchedulerUpdate):
                    # Update board schedule state - FORCE TO TRUE FOR SCHEDULE CHANGES
                    if action.board_idx in self.board_schedules: # Changed from False to True
                        self.board_schedules[action.board_idx]["active"] = action.active
                        # Add to changed boards set for applying settings
                        self.changed_boards.add(action.board_idx)
                        # Apply changes if needed
                        self.apply_changed_boards(force=False)
                
                self.gui_queue.task_done()
                
        except queue.Empty:
            pass
        
        # Schedule next queue check
        self.root.after(self.queue_check_interval, self.process_gui_queue)
    
    def duty_cycle_from_percentage(self, percentage):
        """Convert a percentage (0-100) to duty cycle (0-4095)"""
        # If percentage is in valid range and in cache, use cached value
        if 0 <= percentage <= 100 and percentage in self.duty_cycle_lookup:
            return self.duty_cycle_lookup[percentage]
        
        # Otherwise calculate (should not happen with valid input)
        return int((percentage / 100.0) * 4095)


if __name__ == "__main__":
    root = tk.Tk()
    app = LEDControlGUI(root)
    root.mainloop()