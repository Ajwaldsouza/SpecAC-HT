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

# Path to the microcontroller serial mapping file
SERIAL_MAPPING_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                  "microcontroller", "microcontroller_serial.txt")

# Cache commonly used regular expression patterns
TIME_PATTERN = re.compile(r'^([0-1][0-9]|2[0-3]):([0-5][0-9])$')

# Cache serial port details for faster board detection (will be initialized at runtime)
CACHED_PORT_INFO = {}

# Cache board serial numbers to avoid repeated lookups
BOARD_SERIAL_CACHE = {}


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
                    baudrate=115200,
                    timeout=2,  # Increased timeout
                    write_timeout=2  # Added write timeout
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
        
        self.style = ttk.Style()
        self.style.theme_use('clam')  # Modern theme
        
        # Initialize status variable early so it's available for all methods
        self.status_var = tk.StringVar(value="Ready")
        
        # Cache these once instead of creating multiple regex objects
        self.time_pattern = TIME_PATTERN
        
        # Pre-calculate common conversion factors
        self.duty_cycle_factor = 4095 / 100.0  # For converting percentages to duty cycles
        
        self.boards = []
        self.board_frames = []
        self.led_entries = {}  # {(board_idx, channel): entry_widget}
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
        self.scheduler_check_interval = 1000  # 1 second default
        self.adaptive_check_timer = None  # Store reference to scheduled timer
        
        # NEW: Add widget update batching
        self.widget_update_queue = queue.Queue()
        self.update_batch_timer = None
        self.update_batch_interval = 250  # ms between widget update batches
        self.status_update_batch = []  # List to batch status updates
        self.status_update_timer = None
        self.is_updating_widgets = False
        
        # Cache fonts to avoid creating new font objects repeatedly
        self.cached_fonts = {
            'header': ('Helvetica', 16, 'bold'),
            'subheader': ('Helvetica', 10, 'bold'),
            'normal': ('Helvetica', 10, 'normal')
        }
        
        # Cache colors for better performance
        self.cached_colors = {
            'error': 'red',
            'normal': 'black',
            'success': 'green'
        }
        
        # Cache chamber mapping
        self.chamber_mapping = {}  # {serial_number: chamber_number}
        self.reverse_chamber_mapping = {}  # {chamber_number: serial_number}
        self.load_chamber_mapping()
        
        # Pagination variables
        self.current_page = 0
        self.boards_per_page = 8
        
        # Cache command batch sizes for higher performance during bulk operations
        self.max_concurrent_commands = 5
        
        # Pre-cache some command strings
        self.zero_duty_cycle = [0, 0, 0, 0, 0, 0]
        
        self.create_gui()
        
        # Cache board serial detection
        self.initialize_port_cache()
        
        # Start the scheduler using after() instead of a thread
        self.start_scheduler()
        
        # Start the widget update processor
        self.process_widget_updates()
    
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
                            
                        # Parse the chamber:serial format
                        match = re.match(r'^(\d+):(.+)$', line)
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
            validatecommand=(self.root.register(self.validate_percentage), '%P')
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
        
        # Count boards with chamber numbers in each range
        boards_1_8 = sum(1 for board in self.boards if 1 <= board.chamber_number <= 8)
        boards_9_16 = sum(1 for board in self.boards if 9 <= board.chamber_number <= 16)
        
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
        
        # Display boards based on chamber number
        displayed_count = 0
        for i, board in enumerate(self.boards):
            chamber_number = board.chamber_number
            
            if first_chamber <= chamber_number <= last_chamber:
                # Calculate position within the page (2 rows x 4 columns)
                relative_position = chamber_number - first_chamber
                row = relative_position // 4
                col = relative_position % 4
                
                if i < len(self.board_frames):
                    self.board_frames[i].grid(row=row, column=col, padx=5, pady=5, sticky=(tk.N, tk.W, tk.E, tk.S))
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
        
        # Sort boards by chamber number
        self.boards.sort(key=lambda b: b.chamber_number)
        
        for i, board in enumerate(self.boards):
            chamber_number = board.chamber_number
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
                    validatecommand=(self.root.register(self.validate_percentage), '%P')
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
            off_time_var = tk.StringVar(value="20:00")
            
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
                "off_time": "20:00",
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
        
        if self.master_on:
            # Turn OFF all lights - keep UI values but send zeros to boards
            self.master_on = False
            self.master_button_var.set("All Lights ON")
            
            # Save current values but don't change the UI
            self.saved_values = {}
            for board_idx in range(len(self.boards)):
                for channel_name in LED_CHANNELS:
                    key = (board_idx, channel_name)
                    if key in self.led_entries:
                        try:
                            self.saved_values[key] = self.led_entries[key].get()
                        except (ValueError, KeyError):
                            pass
            
            # Send zeros to all boards without changing UI
            success_count = 0
            active_boards = len(self.boards)
            self.pending_operations = active_boards
            
            # Update status for processing
            self.status_var.set("Turning all lights OFF...")
            
            for board_idx, board in enumerate(self.boards):
                board.send_command([0, 0, 0, 0, 0, 0], 
                                  callback=lambda success, msg, idx=board_idx: 
                                      self.on_toggle_lights_complete(success, idx))
        else:
            # Turn ON all lights - apply the values already in the UI
            self.master_on = True
            self.master_button_var.set("All Lights OFF")
            
            # Update status
            self.status_var.set("Restoring all light settings...")
            
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
            self.scheduler_button_var.set("Start Scheduler")
            self.set_status("Scheduler stopped")
            # Cancel any pending scheduled checks
            if self.adaptive_check_timer:
                self.root.after_cancel(self.adaptive_check_timer)
        else:
            self.scheduler_running = True
            self.scheduler_button_var.set("Stop Scheduler")
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
            
        current_datetime = datetime.now()
        current_hour, current_minute = current_datetime.hour, current_datetime.minute
        changes_made = False
        min_time_diff = float('inf')  # Track time to next scheduled event
        updated_boards = set()  # Track which boards actually need updates
        
        # Process each board with a schedule
        for board_idx, schedule_info in list(self.board_schedules.items()):
            if not schedule_info.get("enabled", False):
                continue
                
            # Get on_time and off_time
            on_time = schedule_info.get("on_time", "")
            off_time = schedule_info.get("off_time", "")
            
            # Extract hours and minutes
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
                    is_active = self.is_time_between(f"{current_hour:02d}:{current_minute:02d}", on_time, off_time)
                    
                    # Get previous state from cache, defaulting to None for first check
                    prev_state = self.last_schedule_state.get(board_idx, {}).get("active", None)
                    
                    # Only process if state has changed or this is the first check
                    if prev_state is None or prev_state != is_active:
                        # Update our tracking
                        schedule_info["active"] = is_active
                        changes_made = True
                        
                        # Update the schedule state cache
                        if board_idx not in self.last_schedule_state:
                            self.last_schedule_state[board_idx] = {}
                        self.last_schedule_state[board_idx]["active"] = is_active
                        self.last_schedule_state[board_idx]["last_check"] = current_datetime
                        
                        # Add to the set of boards that need settings applied
                        updated_boards.add(board_idx)
                        
                        # Log the change - use batched status updates
                        chamber_number = self.boards[board_idx].chamber_number if board_idx < len(self.boards) else board_idx+1
                        action = "ON" if is_active else "OFF"
                        self.set_status(f"Chamber {chamber_number}: Schedule activated - turning {action}")
            except Exception as e:
                # Log error but continue processing other boards
                print(f"Error processing schedule for board {board_idx}: {str(e)}")
        
        # Only update changed_boards if we actually have changes to apply
        if updated_boards:
            self.changed_boards.update(updated_boards)
            # Apply changes but only if we haven't done so recently
            self.apply_changed_boards(force=False)
        
        # Calculate adaptive timer interval
        adaptive_interval = self.calculate_adaptive_interval(min_time_diff)
        
        # Schedule the next check using adaptive interval
        self.adaptive_check_timer = self.root.after(adaptive_interval, self.schedule_check)
    
    def calculate_adaptive_interval(self, min_time_diff):
        """Calculate adaptive timer interval based on time to next event"""
        # Convert from minutes to milliseconds
        base_interval = 10000  # 10 seconds default
        
        if min_time_diff != float('inf'):
            # Check more frequently when close to a scheduled event
            if min_time_diff <= 1:  # Within 1 minute
                return 1000  # Check every second
            elif min_time_diff <= 5:  # Within 5 minutes
                return 5000  # Check every 5 seconds
            elif min_time_diff <= 15:  # Within 15 minutes
                return 30000  # Check every 30 seconds
            else:
                # If next event is far, check less frequently
                return 60000  # Check every minute
        
        return base_interval  # Default interval
    
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
                return
                
        # Apply settings only to boards that changed
        boards_to_update = list(self.changed_boards)
        
        # Update at most 3 boards at once to avoid GUI freezing
        max_updates = 3
        if len(boards_to_update) > max_updates:
            # Process some boards now, defer the rest
            current_batch = boards_to_update[:max_updates]
            deferred_batch = boards_to_update[max_updates:]
            
            # Process current batch
            for board_idx in current_batch:
                if board_idx < len(self.boards):
                    self.apply_board_settings(board_idx)
                self.changed_boards.remove(board_idx)
                
            # Schedule deferred batch with a small delay
            self.root.after(100, self.apply_changed_boards, True)
        else:
            # Process all boards at once
            for board_idx in boards_to_update:
                if board_idx < len(self.boards):
                    self.apply_board_settings(board_idx)
            
            # Clear the set of changed boards
            self.changed_boards = set()
        
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
        
        # Make sure chamber mapping is loaded
        self.load_chamber_mapping()
        
        # Detect connected boards
        try:
            detected_boards = self.detect_xiao_boards()
            
            if not detected_boards:
                messagebox.showwarning("No Boards Found", "No XIAO RP2040 boards were detected.")
                self.status_var.set("No boards found")
                return
            
            # Create board connections
            for port, serial_number, chamber_number in detected_boards:
                self.boards.append(BoardConnection(port, serial_number, chamber_number))
            
            # Create GUI elements for boards
            self.create_board_frames()
            
            # Count boards by chamber ranges
            chambers_1_8 = sum(1 for board in self.boards if 1 <= board.chamber_number <= 8)
            chambers_9_16 = sum(1 for board in self.boards if 9 <= board.chamber_number <= 16)
            
            self.status_var.set(f"Found {len(self.boards)} board(s): {chambers_1_8} in chambers 1-8, {chambers_9_16} in chambers 9-16")
        except Exception as e:
            messagebox.showerror("Error Scanning Boards", str(e))
            self.status_var.set(f"Error: {str(e)}")
    
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
                self.status_var.set(f"Warning: Board with S/N {serial_number} not found in chamber mapping")
            
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
        success_count = 0
        error_count = 0
        # Track completion status
        active_boards = len(self.boards)
        self.pending_operations = active_boards
        
        # Update status
        self.status_var.set("Applying all settings...")
        
        for i in range(active_boards):
            self.apply_board_settings(i)
    
    def toggle_all_fans(self):
        """Toggle all fans on or off on all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control fans.")
            return
        
        if self.fans_on:
            # Turn off all fans
            self.fans_on = False
            self.fan_button_var.set("Turn Fans ON")
            
            # Update status
            self.status_var.set("Turning all fans OFF...")
            
            # Track completion status
            active_boards = len(self.boards)
            self.pending_fan_operations = active_boards
            
            for i, board in enumerate(self.boards):
                board.turn_fan_off(callback=lambda success, msg, idx=i: 
                                  self.on_toggle_fan_complete(success, msg, idx, False))
        else:
            # Turn on all fans
            self.fans_on = True
            self.fan_button_var.set("Turn Fans OFF")
            
            # Get the speed from the entry
            try:
                speed = int(self.fan_speed_var.get())
            except ValueError:
                speed = 50  # Default to 50% if invalid
                self.fan_speed_var.set("50")
            
            # Update status
            self.status_var.set(f"Turning all fans ON at {speed}%...")
            
            # Track completion status
            active_boards = len(self.boards)
            self.pending_fan_operations = active_boards
            
            for i, board in enumerate(self.boards):
                board.set_fan_speed(speed, callback=lambda success, msg, idx=i: 
                                   self.on_toggle_fan_complete(success, msg, idx, True))
    
    def on_toggle_fan_complete(self, success, message, board_idx, is_on):
        """Callback when a toggle fan operation completes"""
        self.pending_fan_operations -= 1
        
        if not success:
            # Use after() to ensure messagebox runs in the main thread
            self.root.after(0, lambda: messagebox.showerror(f"Error - Board {board_idx+1}", message))
        
        if self.pending_fan_operations == 0:
            action = "ON" if is_on else "OFF"
            speed_info = f" at {self.fan_speed_var.get()}%" if is_on else ""
            self.status_var.set(f"All fans turned {action}{speed_info}")
    
    def apply_fan_settings(self):
        """Apply the fan speed to all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control fans.")
            return
        
        try:
            speed = int(self.fan_speed_var.get())
        except ValueError:
            messagebox.showerror("Invalid Value", "Please enter a valid fan speed (0-100%).")
            return
        
        # Update status
        self.status_var.set(f"Setting all fans to {speed}%...")
        
        # Track completion status
        active_boards = len(self.boards)
        self.pending_fan_operations = active_boards
        
        for i, board in enumerate(self.boards):
            board.set_fan_speed(speed, callback=lambda success, msg, idx=i: 
                               self.on_fan_setting_complete(success, msg, idx, speed))
    
    def on_fan_setting_complete(self, success, message, board_idx, speed):
        """Callback when a fan setting operation completes"""
        self.pending_fan_operations -= 1
        
        if success:
            # Update the fans_on flag if needed
            if speed > 0 and not self.fans_on:
                self.fans_on = True
                self.fan_button_var.set("Turn Fans OFF")
            elif speed == 0 and self.fans_on:
                self.fans_on = False
                self.fan_button_var.set("Turn Fans ON")
        else:
            # Use after() to ensure messagebox runs in the main thread
            self.root.after(0, lambda: messagebox.showerror(f"Error - Board {board_idx+1}", message))
        
        if self.pending_fan_operations == 0:
            self.status_var.set(f"Fan speed set to {speed}% on all boards")
    
    def export_settings(self):
        """Export current LED settings and schedules to a text file"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to export settings from.")
            return
        
        try:
            # Collect all settings
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
                        "off_time": self.board_schedules[board_idx].get("off_time", "20:00"),
                        "enabled": self.board_schedules[board_idx].get("enabled", False)
                    }
                
                # Add fan settings
                board_settings["fan"] = {
                    "enabled": board.fan_enabled,
                    "speed": board.fan_speed
                }
                
                # Use chamber number as the key instead of board index
                settings[f"chamber_{chamber_number}"] = board_settings
            
            # Get file path from user
            file_path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
                title="Save LED Settings"
            )
            if not file_path:
                return  # User canceled
                
            # Save to file
            with open(file_path, 'w') as f:
                json.dump(settings, f, indent=4)
            
            self.status_var.set(f"Settings exported to {file_path}")
            messagebox.showinfo("Export Successful", f"Settings successfully exported to {file_path}")
            
        except Exception as e:
            messagebox.showerror("Export Error", f"Error exporting settings: {str(e)}")
            self.status_var.set(f"Export error: {str(e)}")
    
    def import_settings(self):
        """Import LED settings and schedules from a text file and apply them"""
        try:
            # Get file path from user
            file_path = filedialog.askopenfilename(
                filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
                title="Import LED Settings"
            )
            if not file_path:
                return  # User canceled
            
            # Read settings from file
            with open(file_path, 'r') as f:
                settings = json.load(f)
            
            # Validate imported data format
            if not isinstance(settings, dict):
                raise ValueError("Invalid settings file format")
                
            # Make sure we have boards to apply settings to
            if not self.boards:
                messagebox.showwarning("No Boards", "No boards connected to apply settings to.")
                return
                
            # Apply settings to GUI entries
            applied_count = 0
            fan_settings_found = False
            
            for board_key, board_settings in settings.items():
                try:
                    # Extract chamber number (format: "chamber_X")
                    chamber_number = int(board_key.split("_")[1])
                    # Find the board index for this chamber number
                    board_idx = next((i for i, b in enumerate(self.boards) if b.chamber_number == chamber_number), None)
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
                                "off_time": schedule.get("off_time", "20:00"),
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
            
            self.status_var.set(f"Imported settings from {file_path}")
            
            if messagebox.askyesno("Apply Settings", 
                                 f"Successfully loaded {applied_count} settings from file.\n\nDo you want to apply these settings to the boards now?"):
                self.apply_all_settings()
                
                # Also apply fan settings if they were found
                if fan_settings_found:
                    self.apply_fan_settings()
                
        except Exception as e:
            messagebox.showerror("Import Error", f"Error importing settings: {str(e)}")
            self.status_var.set(f"Import error: {str(e)}")
    
    def check_board_active_state(self, board_idx):
        """Check if a board should be active based on current schedule"""
        if board_idx not in self.board_schedules:
            return True  # Default to active if no schedule
        
        schedule_info = self.board_schedules[board_idx]
        if not schedule_info.get("enabled", False):
            return True  # Not using scheduling, so always active
        
        # Get time values
        current_time = datetime.now().strftime("%H:%M")
        on_time = schedule_info.get("on_time", "08:00")
        off_time = schedule_info.get("off_time", "20:00")
        
        # Validate time formats
        if not self.validate_time_format(on_time) or not self.validate_time_format(off_time):
            # If time format is invalid, default to active (safer)
            self.status_var.set(f"Board {board_idx+1}: WARNING - Invalid schedule time format. Using default ON state.")
            return True
        
        is_active = self.is_time_between(current_time, on_time, off_time)
        # Update the active state in our tracking
        schedule_info["active"] = is_active
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
                    # Update foreground color
                    widget = self.board_time_entries.get(widget_id)
                    if widget:
                        widget.config(foreground=value)
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
            except Exception as e:
                print(f"Error updating widget {widget_id}: {str(e)}")
        
        self.is_updating_widgets = False
        
        # Schedule the next batch processing
        self.update_batch_timer = self.root.after(
            self.update_batch_interval, self.process_widget_updates)
    
    # NEW: Add batch status update method
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
        self.status_update_timer = self.root.after(100, self.process_status_updates)
    
    def process_status_updates(self):
        """Process batched status updates"""
        self.status_update_timer = None
        
        if not self.status_update_batch:
            return
            
        # Use the most recent status message
        latest_message = self.status_update_batch[-1]
        self.status_var.set(latest_message)
        
        # Clear the batch
        self.status_update_batch = []
    
    def apply_board_settings(self, board_idx):
        """Apply settings for a specific board"""
        if board_idx >= len(self.boards):
            messagebox.showerror("Error", "Invalid board index")
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
                percentage = int(self.led_entries[(board_idx, channel)].get())
                duty = int((percentage / 100.0) * 4095)
                duty_values.append(duty)
            except ValueError:
                duty_values.append(0)
        
        # If scheduling is active, we may need to override the duty values
        if scheduling_enabled and not schedule_active:
            # Board should be off according to schedule
            self.set_status(f"Board {board_idx+1}: Applying zeros to hardware (schedule OFF time, UI settings preserved)")
            # Send zeros to board, but don't change UI
            board.send_command([0, 0, 0, 0, 0, 0], 
                              callback=lambda success, msg: self.on_board_command_complete(
                                  board_idx, success, msg, "during scheduled OFF time"))
        else:
            # Board should be on - apply actual values from UI
            status_msg = "Applying settings..."
            if scheduling_enabled and schedule_active:
                status_msg = "Applying settings (during scheduled ON time)..."
            self.set_status(f"Board {board_idx+1}: {status_msg}")
            board.send_command(duty_values, 
                              callback=lambda success, msg: self.on_board_command_complete(
                                  board_idx, success, msg, 
                                  "during scheduled ON time" if scheduling_enabled and schedule_active else None))
    
    def on_board_command_complete(self, board_idx, success, message, extra_info=None):
        """Callback when a board command completes"""
        if board_idx >= len(self.boards):
            return
        
        chamber_number = self.boards[board_idx].chamber_number
        if success:
            if extra_info:
                self.set_status(f"Chamber {chamber_number}: Settings applied ({extra_info})")
            else:
                self.set_status(f"Chamber {chamber_number}: Settings applied successfully")
        else:
            # Use after() to ensure messagebox runs in the main thread
            self.root.after(0, lambda: messagebox.showerror(f"Error - Chamber {chamber_number}", message))
            self.set_status(f"Chamber {chamber_number}: Error - {message}")
    
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
    
    # Replace all status_var.set() calls with this method
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
        self.status_update_timer = self.root.after(100, self.process_status_updates)
    
    # ...rest of the class implementation remains the same...


if __name__ == "__main__":
    root = tk.Tk()
    app = LEDControlGUI(root)
    root.mainloop()