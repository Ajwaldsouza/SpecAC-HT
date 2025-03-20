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


class BoardConnection:
    """Manages the connection to a single XIAO RP2040 board"""
    
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
    
    def send_command(self, duty_values, callback=None):
        """Send command to update LED brightness asynchronously"""
        future = self.executor.submit(self._send_command_impl, duty_values)
        if callback:
            future.add_done_callback(lambda f: callback(*f.result()))
        self.pending_tasks.append(future)
        return future
    
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
                    command = "SETALL"
                    for val in duty_values:
                        command += f" {val}"
                    command += "\n"
                    
                    # Clear any pending data
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()
                    
                    self.serial_conn.write(command.encode('utf-8'))
                    
                    # Wait for response with timeout
                    start_time = time.time()
                    while time.time() - start_time < 1.0:  # 1 second timeout
                        if self.serial_conn.in_waiting > 0:
                            response = self.serial_conn.readline().decode('utf-8').strip()
                            if response == "OK":
                                return True, "Success"
                            elif response.startswith("ERR:"):
                                return False, f"Error: {response}"
                            else:
                                return False, f"Unexpected response: {response}"
                        time.sleep(0.1)
                    
                    retry_count += 1
                    if retry_count < self.max_retries:
                        time.sleep(0.5)  # Wait before retry
                    else:
                        return False, "Timeout waiting for response"
                        
                except Exception as e:
                    self.is_connected = False
                    return False, str(e)
            
            return False, "Max retries exceeded"
    
    def set_fan_speed(self, percentage, callback=None):
        """Set the fan speed as a percentage asynchronously"""
        future = self.executor.submit(self._set_fan_speed_impl, percentage)
        if callback:
            future.add_done_callback(lambda f: callback(*f.result()))
        self.pending_tasks.append(future)
        return future
    
    def _set_fan_speed_impl(self, percentage):
        """Internal implementation of set_fan_speed operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl():
                    return False, self.last_error
                    
            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    command = f"FAN_SET {percentage}\n"
                    
                    # Clear any pending data
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()
                    
                    self.serial_conn.write(command.encode('utf-8'))
                    
                    # Wait for response with timeout
                    start_time = time.time()
                    while time.time() - start_time < 1.0:  # 1 second timeout
                        if self.serial_conn.in_waiting > 0:
                            response = self.serial_conn.readline().decode('utf-8').strip()
                            if response == "OK":
                                self.fan_speed = percentage
                                self.fan_enabled = percentage > 0
                                return True, "Success"
                            elif response.startswith("ERR:"):
                                return False, f"Error: {response}"
                            else:
                                return False, f"Unexpected response: {response}"
                        time.sleep(0.1)
                    
                    retry_count += 1
                    if retry_count < self.max_retries:
                        time.sleep(0.5)  # Wait before retry
                    else:
                        return False, "Timeout waiting for response"
                        
                except Exception as e:
                    self.is_connected = False
                    return False, str(e)
            
            return False, "Max retries exceeded"
            
    def turn_fan_on(self, callback=None):
        """Turn the fan on asynchronously"""
        future = self.executor.submit(self._turn_fan_on_impl)
        if callback:
            future.add_done_callback(lambda f: callback(*f.result()))
        self.pending_tasks.append(future)
        return future
    
    def _turn_fan_on_impl(self):
        """Internal implementation of turn_fan_on operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl():
                    return False, self.last_error
            
            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    command = "FAN_ON\n"
                    
                    # Clear any pending data
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()
                    
                    self.serial_conn.write(command.encode('utf-8'))
                    
                    # Wait for response with timeout
                    start_time = time.time()
                    while time.time() - start_time < 1.0:  # 1 second timeout
                        if self.serial_conn.in_waiting > 0:
                            response = self.serial_conn.readline().decode('utf-8').strip()
                            if response == "OK":
                                self.fan_enabled = True
                                return True, "Success"
                            elif response.startswith("ERR:"):
                                return False, f"Error: {response}"
                            else:
                                return False, f"Unexpected response: {response}"
                        time.sleep(0.1)
                    
                    retry_count += 1
                    if retry_count < self.max_retries:
                        time.sleep(0.5)  # Wait before retry
                    else:
                        return False, "Timeout waiting for response"
                        
                except Exception as e:
                    self.is_connected = False
                    return False, str(e)
            
            return False, "Max retries exceeded"
            
    def turn_fan_off(self, callback=None):
        """Turn the fan off asynchronously"""
        future = self.executor.submit(self._turn_fan_off_impl)
        if callback:
            future.add_done_callback(lambda f: callback(*f.result()))
        self.pending_tasks.append(future)
        return future
    
    def _turn_fan_off_impl(self):
        """Internal implementation of turn_fan_off operation"""
        with self.lock:
            if not self.is_connected:
                if not self._connect_impl():
                    return False, self.last_error
                    
            retry_count = 0
            while retry_count < self.max_retries:
                try:
                    command = "FAN_OFF\n"
                    
                    # Clear any pending data
                    if self.serial_conn.in_waiting > 0:
                        self.serial_conn.reset_input_buffer()
                    
                    self.serial_conn.write(command.encode('utf-8'))
                    
                    # Wait for response with timeout
                    start_time = time.time()
                    while time.time() - start_time < 1.0:  # 1 second timeout
                        if self.serial_conn.in_waiting > 0:
                            response = self.serial_conn.readline().decode('utf-8').strip()
                            if response == "OK":
                                self.fan_enabled = False
                                self.fan_speed = 0
                                return True, "Success"
                            elif response.startswith("ERR:"):
                                return False, f"Error: {response}"
                            else:
                                return False, f"Unexpected response: {response}"
                        time.sleep(0.1)
                    
                    retry_count += 1
                    if retry_count < self.max_retries:
                        time.sleep(0.5)  # Wait before retry
                    else:
                        return False, "Timeout waiting for response"
                        
                except Exception as e:
                    self.is_connected = False
                    return False, str(e)
            
            return False, "Max retries exceeded"
    
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
        
        # Chamber mapping variables
        self.chamber_mapping = {}  # {serial_number: chamber_number}
        self.load_chamber_mapping()
        
        # Pagination variables
        self.current_page = 0
        self.boards_per_page = 8
        
        self.create_gui()
        self.start_scheduler()
    
    def load_chamber_mapping(self):
        """Load the chamber to serial number mapping from the text file"""
        self.chamber_mapping = {}
        
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
        
        ttk.Label(header_frame, text="LED Control System", font=('Helvetica', 16, 'bold')).pack(side=tk.LEFT)
        
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
        nav_label = ttk.Label(nav_frame, text="Chamber Navigation:", font=('Helvetica', 10, 'bold'))
        nav_label.pack(side=tk.LEFT, padx=10)
        
        self.prev_button = ttk.Button(nav_frame, text="◀ Chambers 1-8", command=self.prev_page, width=15)
        self.prev_button.pack(side=tk.LEFT, padx=10)
        
        self.page_label = ttk.Label(nav_frame, text="Chambers 1-8", font=('Helvetica', 10, 'bold'))
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
        fan_speed_entry = ttk.Spinbox(
            fan_frame,
            from_=0,
            to=100,
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
        ttk.Button(bottom_frame, text="Import Settings", command=self.import_settings).pack(side=tk.RIGHT, padx=5)le=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        
        # Status bar
        self.status_var = tk.StringVar(value="Ready")self.scan_boards()
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(column=0, row=4, columnspan=2, sticky=(tk.W, tk.E))# Add window close handler for cleanup
         self.on_closing)
        self.scan_boards()
        def on_closing(self):
        # Add window close handler for cleanuprces and close the application"""
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    ng = False
    def on_closing(self):elf.scheduler_thread.is_alive():
        """Clean up resources and close the application"""
        # Stop the scheduler
        self.scheduler_running = Falseean up board connections
        if self.scheduler_thread and self.scheduler_thread.is_alive():
            self.scheduler_thread.join(timeout=1.0)
            
        # Clean up board connectionsstroy the main window
        for board in self.boards:
            board.cleanup()
            def next_page(self):
        # Destroy the main windowhambers 9-16 (page 2)"""
        self.root.destroy()
    
    def next_page(self):lay()
        """Navigate to chambers 9-16 (page 2)"""
        if self.current_page == 0:def prev_page(self):
            self.current_page = 1hambers 1-8 (page 1)"""
            self.update_page_display()
    
    def prev_page(self):lay()
        """Navigate to chambers 1-8 (page 1)"""
        if self.current_page == 1:def update_page_display(self):
            self.current_page = 0how chambers 1-8 or 9-16 based on current page"""
            self.update_page_display()
    s:
    def update_page_display(self):
        """Update the display to show chambers 1-8 or 9-16 based on current page"""
        # Hide all board frames first# Update page label and navigation buttons
        for frame in self.board_frames:
            frame.grid_remove()(text="Chambers 1-8")
        
        # Update page label and navigation buttons len(self.boards) > 8 else tk.DISABLED)
        if self.current_page == 0:
            self.page_label.config(text="Chambers 1-8")
            self.prev_button.config(state=tk.DISABLED)
            self.next_button.config(state=tk.NORMAL if len(self.boards) > 8 else tk.DISABLED)abel.config(text="Chambers 9-16")
            first_chamber = 1
            last_chamber = 8D)
        else:  # page 1
            self.page_label.config(text="Chambers 9-16")
            self.prev_button.config(state=tk.NORMAL)
            self.next_button.config(state=tk.DISABLED)# Display boards based on chamber number
            first_chamber = 9
            last_chamber = 16merate(self.boards):
        er
        # Display boards based on chamber number
        displayed_count = 0if first_chamber <= chamber_number <= last_chamber:
        for i, board in enumerate(self.boards):4 columns)
            chamber_number = board.chamber_number
            
            if first_chamber <= chamber_number <= last_chamber:
                # Calculate position within the page (2 rows x 4 columns)
                relative_position = chamber_number - first_chamberif i < len(self.board_frames):
                row = relative_position // 4row=row, column=col, padx=5, pady=5, sticky=(tk.N, tk.W, tk.E, tk.S))
                col = relative_position % 4
                
                if i < len(self.board_frames):# Update status message to show how many chambers are displayed
                    self.board_frames[i].grid(row=row, column=col, padx=5, pady=5, sticky=(tk.N, tk.W, tk.E, tk.S))ambers {first_chamber}-{last_chamber})")
                    displayed_count += 1
        def create_board_frames(self):
        # Update status message to show how many chambers are displayeddetected board, sorted by chamber number"""
        self.status_var.set(f"Displaying {displayed_count} chambers (Chambers {first_chamber}-{last_chamber})")
    oard_frames:
    def create_board_frames(self):
        """Create frames for each detected board, sorted by chamber number""" []
        # Remove old frames
        for frame in self.board_frames:# Sort boards by chamber number
            frame.destroy()b.chamber_number)
        self.board_frames = []
        for i, board in enumerate(self.boards):
        # Sort boards by chamber numberer
        self.boards.sort(key=lambda b: b.chamber_number)ntainer, text=f"Chamber {chamber_number} (S/N: {board.serial_number})")
        
        for i, board in enumerate(self.boards):
            chamber_number = board.chamber_number# LED control section
            frame = ttk.LabelFrame(self.boards_container, text=f"Chamber {chamber_number} (S/N: {board.serial_number})")tk.LabelFrame(frame, text="LED Controls")
            self.board_frames.append(frame)=(tk.W, tk.E))
            
            # LED control section# Add header row for LED controls
            led_control_frame = ttk.LabelFrame(frame, text="LED Controls")="LED Channel").grid(column=1, row=0, sticky=tk.W, padx=5)
            led_control_frame.grid(column=0, row=0, padx=5, pady=5, sticky=(tk.W, tk.E))5)
            
            # Add header row for LED controls# Add LED controls for each channel
            ttk.Label(led_control_frame, text="LED Channel").grid(column=1, row=0, sticky=tk.W, padx=5)) in enumerate(LED_CHANNELS.items(), start=1):
            ttk.Label(led_control_frame, text="Intensity (%)").grid(column=2, row=0, sticky=tk.W, padx=5)
            
            # Add LED controls for each channelhannel_name], width=2)
            for row, (channel_name, channel_idx) in enumerate(LED_CHANNELS.items(), start=1):
                color_frame = ttk.Frame(led_control_frame, width=20, height=20)
                color_frame.grid(column=0, row=row, padx=5, pady=2)ttk.Label(led_control_frame, text=channel_name).grid(column=1, row=row, sticky=tk.W, padx=5)
                color_label = tk.Label(color_frame, bg=LED_COLORS[channel_name], width=2)
                color_label.pack(fill=tk.BOTH, expand=True)value_var = tk.StringVar(value="0")
                
                ttk.Label(led_control_frame, text=channel_name).grid(column=1, row=row, sticky=tk.W, padx=5)e, 
                
                value_var = tk.StringVar(value="0")
                entry = ttk.Spinbox( 
                    led_control_frame, ble=value_var,
                    from_=0, 
                    to=100, =(self.root.register(self.validate_percentage), '%P')
                    width=5, 
                    textvariable=value_var,ntry.grid(column=2, row=row, sticky=tk.W, padx=5)
                    validate='key',=3, row=row, sticky=tk.W)
                    validatecommand=(self.root.register(self.validate_percentage), '%P')
                )self.led_entries[(i, channel_name)] = entry
                entry.grid(column=2, row=row, sticky=tk.W, padx=5)
                ttk.Label(led_control_frame, text="%").grid(column=3, row=row, sticky=tk.W)# Scheduling section - one per board
                e, text="Board Schedule")
                self.led_entries[(i, channel_name)] = entrytk.W, tk.E))
            
            # Scheduling section - one per board# Create schedule controls
            schedule_frame = ttk.LabelFrame(frame, text="Board Schedule")text="ON Time:").grid(column=0, row=0, padx=5, pady=5, sticky=tk.W)
            schedule_frame.grid(column=0, row=1, padx=5, pady=5, sticky=(tk.W, tk.E))
            
            # Create schedule controls# Add validation callback to variable
            ttk.Label(schedule_frame, text="ON Time:").grid(column=0, row=0, padx=5, pady=5, sticky=tk.W) name, index, mode, b_idx=i, var=on_time_var: 
            on_time_var = tk.StringVar(value="08:00")
            
            # Add validation callback to variable(schedule_frame, width=7, textvariable=on_time_var)
            on_time_var.trace_add("write", lambda name, index, mode, b_idx=i, var=on_time_var: 
                               self.validate_time_entry(b_idx, "on", var.get()))
                               
            on_time = ttk.Entry(schedule_frame, width=7, textvariable=on_time_var)ttk.Label(schedule_frame, text="OFF Time:").grid(column=2, row=0, padx=5, pady=5, sticky=tk.W)
            on_time.grid(column=1, row=0, padx=5, pady=5)
            self.board_time_entries[(i, "on")] = on_time
            # Add validation callback to variable
            ttk.Label(schedule_frame, text="OFF Time:").grid(column=2, row=0, padx=5, pady=5, sticky=tk.W)a name, index, mode, b_idx=i, var=off_time_var: 
            off_time_var = tk.StringVar(value="20:00")
            
            # Add validation callback to variable(schedule_frame, width=7, textvariable=off_time_var)
            off_time_var.trace_add("write", lambda name, index, mode, b_idx=i, var=off_time_var: 
                                self.validate_time_entry(b_idx, "off", var.get()))
                                
            off_time = ttk.Entry(schedule_frame, width=7, textvariable=off_time_var)# Schedule enable checkbox
            off_time.grid(column=3, row=0, padx=5, pady=5)ar(value=False)
            self.board_time_entries[(i, "off")] = off_time
            
            # Schedule enable checkboxeduling",
            schedule_var = tk.BooleanVar(value=False)
            schedule_check = ttk.Checkbutton(: self.update_board_schedule(b_idx)
                schedule_frame, 
                text="Enable Scheduling",chedule_check.grid(column=4, row=0, padx=10, pady=5, sticky=tk.W)
                variable=schedule_var,
                command=lambda b_idx=i: self.update_board_schedule(b_idx)
            )# Initialize the schedule data for this board
            schedule_check.grid(column=4, row=0, padx=10, pady=5, sticky=tk.W)
            self.board_schedule_vars[i] = schedule_var
            ,
            # Initialize the schedule data for this board
            self.board_schedules[i] = {},
                "on_time": "08:00",dd active flag, default to True
                "off_time": "20:00",
                "enabled": False,   
                "saved_values": {},dividual apply button
                "active": True  # Add active flag, default to True
            }
                pply", 
            # Individual apply button b_idx=i: self.apply_board_settings(b_idx)
            ttk.Button(
                frame, 
                text="Apply", # Update the display to show chambers 1-8 by default
                command=lambda b_idx=i: self.apply_board_settings(b_idx)
            ).grid(column=0, row=2, pady=10, sticky=(tk.W, tk.E))lay()
        
        # Update the display to show chambers 1-8 by defaultdef toggle_all_lights(self):
        self.current_page = 0or off on all boards"""
        self.update_page_display()
    warning("No Boards", "No boards available to control.")
    def toggle_all_lights(self):
        """Toggle all lights on or off on all boards"""
        if not self.boards:if self.master_on:
            messagebox.showwarning("No Boards", "No boards available to control.") lights - keep UI values but send zeros to boards
            return
        .set("All Lights ON")
        if self.master_on:
            # Turn OFF all lights - keep UI values but send zeros to boards# Save current values but don't change the UI
            self.master_on = False
            self.master_button_var.set("All Lights ON")(len(self.boards)):
            
            # Save current values but don't change the UIe)
            self.saved_values = {}
            for board_idx in range(len(self.boards)):
                for channel_name in LED_CHANNELS:self.saved_values[key] = self.led_entries[key].get()
                    key = (board_idx, channel_name)
                    if key in self.led_entries:
                        try:
                            self.saved_values[key] = self.led_entries[key].get()# Send zeros to all boards without changing UI
                        except (ValueError, KeyError):
                            passen(self.boards)
            
            # Send zeros to all boards without changing UI# Update status for processing
            success_count = 0ll lights OFF...")
            active_boards = len(self.boards)
            # Track completion status
            # Update status for processing active_boards
            self.status_var.set("Turning all lights OFF...")
            for board_idx, board in enumerate(self.boards):
            # Track completion status
            self.pending_operations = active_boardsss, msg, idx=board_idx: 
            dx))
            for board_idx, board in enumerate(self.boards):
                board.send_command([0, 0, 0, 0, 0, 0],  Turn ON all lights - apply the values already in the UI
                                  callback=lambda success, msg, idx=board_idx: 
                                      self.on_toggle_lights_complete(success, idx))r.set("All Lights OFF")
        else:
            # Turn ON all lights - apply the values already in the UI# Update status
            self.master_on = True.set("Restoring all light settings...")
            self.master_button_var.set("All Lights OFF")
            # Apply the values that are already in the UI
            # Update status
            self.status_var.set("Restoring all light settings...")
            def on_toggle_lights_complete(self, success, board_idx):
            # Apply the values that are already in the UI"""
            self.apply_all_settings()
    
    def on_toggle_lights_complete(self, success, board_idx):if self.pending_operations == 0:
        """Callback when a toggle lights operation completes"""hts turned OFF (settings preserved)")
        self.pending_operations -= 1
        def toggle_scheduler(self):
        if self.pending_operations == 0:e scheduler"""
            self.status_var.set("All lights turned OFF (settings preserved)")
     = False
    def toggle_scheduler(self):"Start Scheduler")
        """Enable or disable the scheduler"""
        if self.scheduler_running:
            self.scheduler_running = Falseelf.scheduler_running = True
            self.scheduler_button_var.set("Start Scheduler")("Stop Scheduler")
            self.status_var.set("Scheduler stopped")
        else:.scheduler_thread.is_alive():
            self.scheduler_running = True
            self.scheduler_button_var.set("Stop Scheduler")
            self.status_var.set("Scheduler started")def start_scheduler(self):
            if not self.scheduler_thread or not self.scheduler_thread.is_alive(): thread"""
                self.start_scheduler()
    ("Stop Scheduler")
    def start_scheduler(self):=self.schedule_checker, daemon=True)
        """Start the scheduler thread"""
        self.scheduler_running = True
        self.scheduler_button_var.set("Stop Scheduler")def is_time_between(self, current_time, start_time, end_time):
        self.scheduler_thread = threading.Thread(target=self.schedule_checker, daemon=True)andling overnight periods"""
        self.scheduler_thread.start()
    # Convert all times to datetime for comparison
    def is_time_between(self, current_time, start_time, end_time):%M")
        """Check if current time is between start and end times, handling overnight periods"""
        try:
            # Convert all times to datetime for comparison
            current = datetime.strptime(current_time, "%H:%M")if start <= end:
            start = datetime.strptime(start_time, "%H:%M")e: start time is before end time (e.g., 08:00 to 20:00)
            end = datetime.strptime(end_time, "%H:%M")
            
            if start <= end: Wrap-around case: end time is before start time (e.g., 20:00 to 08:00 next day)
                # Simple case: start time is before end time (e.g., 08:00 to 20:00)
                return start <= current <= end
            else:format is invalid, default to True (safer)
                # Wrap-around case: end time is before start time (e.g., 20:00 to 08:00 next day)
                return current >= start or current <= end
        except ValueError:def apply_board_settings(self, board_idx):
            # If any time format is invalid, default to True (safer)"""
            return True
     "Invalid board index")
    def apply_board_settings(self, board_idx):
        """Apply settings for a specific board"""
        if board_idx >= len(self.boards):d = self.boards[board_idx]
            messagebox.showerror("Error", "Invalid board index")
            return
            # Check if scheduling is enabled and get active state
        board = self.boards[board_idx]
        duty_values = []
        oard_schedules:
        # Check if scheduling is enabled and get active statechedules[board_idx].get("enabled", False)
        scheduling_enabled = False
        schedule_active = True
        if board_idx in self.board_schedules:self.check_board_active_state(board_idx)
            scheduling_enabled = self.board_schedules[board_idx].get("enabled", False)
            # If scheduling is enabled, check current active state# Always save the current UI values to ensure they're preserved
            if scheduling_enabled:
                schedule_active = self.check_board_active_state(board_idx)_values(board_idx)
        
        # Always save the current UI values to ensure they're preserved# Get duty cycle values from UI for each channel
        if scheduling_enabled:
            self.save_board_ui_values(board_idx)
        percentage = int(self.led_entries[(board_idx, channel)].get())
        # Get duty cycle values from UI for each channel
        for channel in LED_CHANNELS:
            try:
                percentage = int(self.led_entries[(board_idx, channel)].get())pend(0)
                duty = int((percentage / 100.0) * 4095)
                duty_values.append(duty)# If scheduling is active, we may need to override the duty values
            except ValueError:
                duty_values.append(0)e
        Applying zeros to hardware (schedule OFF time, UI settings preserved)")
        # If scheduling is active, we may need to override the duty values
        if scheduling_enabled and not schedule_active:
            # Board should be off according to scheduless, msg: self.on_board_command_complete(
            self.status_var.set(f"Board {board_idx+1}: Applying zeros to hardware (schedule OFF time, UI settings preserved)")
            # Send zeros to board, but don't change UI
            board.send_command([0, 0, 0, 0, 0, 0],  Board should be on - apply actual values from UI
                              callback=lambda success, msg: self.on_board_command_complete(
                                  board_idx, success, msg, "during scheduled OFF time"))active:
        else:g scheduled ON time)..."
            # Board should be on - apply actual values from UI
            status_msg = "Applying settings..."self.status_var.set(f"Board {board_idx+1}: {status_msg}")
            if scheduling_enabled and schedule_active:
                status_msg = "Applying settings (during scheduled ON time)..."board.send_command(duty_values, 
            a success, msg: self.on_board_command_complete(
            self.status_var.set(f"Board {board_idx+1}: {status_msg}")
            " if scheduling_enabled and schedule_active else None))
            board.send_command(duty_values, 
                              callback=lambda success, msg: self.on_board_command_complete(def on_board_command_complete(self, board_idx, success, message, extra_info=None):
                                  board_idx, success, msg, 
                                  "during scheduled ON time" if scheduling_enabled and schedule_active else None))
    
    def on_board_command_complete(self, board_idx, success, message, extra_info=None):
        """Callback when a board command completes"""ber_number = self.boards[board_idx].chamber_number
        if board_idx >= len(self.boards):
            returnif success:
            a_info:
        chamber_number = self.boards[board_idx].chamber_numbers_var.set(f"Chamber {chamber_number}: Settings applied ({extra_info})")
        
        if success:elf.status_var.set(f"Chamber {chamber_number}: Settings applied successfully")
            if extra_info:
                self.status_var.set(f"Chamber {chamber_number}: Settings applied ({extra_info})") Use after() to ensure messagebox runs in the main thread
            else:Chamber {chamber_number}", message))
                self.status_var.set(f"Chamber {chamber_number}: Settings applied successfully")
        else:
            # Use after() to ensure messagebox runs in the main threaddef update_board_schedule(self, board_idx):
            self.root.after(0, lambda: messagebox.showerror(f"Error - Chamber {chamber_number}", message))oard"""
            self.status_var.set(f"Chamber {chamber_number}: Error - {message}")
    nabled": False, "active": True, "saved_values": {}}
    def update_board_schedule(self, board_idx):
        """Update the schedule for a specific board"""t current values from widgets
        if board_idx not in self.board_schedules:s[board_idx].get("enabled", False)
            self.board_schedules[board_idx] = {"enabled": False, "active": True, "saved_values": {}}
            
        # Get current values from widgets# Validate time entries before applying
        was_enabled = self.board_schedules[board_idx].get("enabled", False)
        is_enabled = self.board_schedule_vars[board_idx].get()e
        
        # Validate time entries before applyingif (board_idx, "on") in self.board_time_entries:
        on_time_valid = False, "on")].get()
        off_time_valid = False
        
        if (board_idx, "on") in self.board_time_entries:hedules[board_idx]["on_time"] = on_time
            on_time = self.board_time_entries[(board_idx, "on")].get()
            on_time_valid = self.validate_time_format(on_time)if (board_idx, "off") in self.board_time_entries:
            if on_time_valid:, "off")].get()
                self.board_schedules[board_idx]["on_time"] = on_time
        
        if (board_idx, "off") in self.board_time_entries:edules[board_idx]["off_time"] = off_time
            off_time = self.board_time_entries[(board_idx, "off")].get()
            off_time_valid = self.validate_time_format(off_time)# If times are invalid, don't enable scheduling
            if off_time_valid:_time_valid):
                self.board_schedules[board_idx]["off_time"] = off_time
        id time format. Please use HH:MM (24-hour) format.")
        # If times are invalid, don't enable scheduling
        if is_enabled and (not on_time_valid or not off_time_valid):vars[board_idx].set(False)
            messagebox.showerror("Invalid Time Format", 
                "Scheduling cannot be enabled with invalid time format. Please use HH:MM (24-hour) format.")
            # Reset the checkboxself.board_schedules[board_idx]["enabled"] = is_enabled
            self.board_schedule_vars[board_idx].set(False)
            is_enabled = False# Always save current UI values
        _idx)
        self.board_schedules[board_idx]["enabled"] = is_enabled
        # If scheduling was just enabled, check if we need to update active state
        # Always save current UI values
        self.save_board_ui_values(board_idx)rrent active state
        _active_state(board_idx)
        # If scheduling was just enabled, check if we need to update active state
        if is_enabled:# If we're outside active hours and just enabled scheduling, turn off lights
            # Check current active state
            is_active = self.check_board_active_state(board_idx)ON period - send zeros but keep UI values
            abled, outside ON hours - turning lights off (settings preserved)")
            # If we're outside active hours and just enabled scheduling, turn off lights
            if not is_active:# Send direct command to turn off LEDs without changing UI
                # Outside of ON period - send zeros but keep UI values
                self.status_var.set(f"Board {board_idx+1}: Schedule enabled, outside ON hours - turning lights off (settings preserved)")
                ensure active state is reset
                # Send direct command to turn off LEDs without changing UI
                self.send_zeros_to_board(board_idx)e board state
        elif was_enabled and not is_enabled:- applying current settings")
            # Scheduling was just disabled, ensure active state is reset
            self.board_schedules[board_idx]["active"] = True
            # Re-apply the current UI settings to restore the board statedef send_zeros_to_board(self, board_idx):
            self.status_var.set(f"Board {board_idx+1}: Schedule disabled - applying current settings")anging UI values"""
            self.apply_board_settings(board_idx)
    
    def send_zeros_to_board(self, board_idx):
        """Send zeros to the board without changing UI values"""d = self.boards[board_idx]
        if board_idx >= len(self.boards):directly (non-blocking now)
            return False
            
        board = self.boards[board_idx]
        # Send command with all zeros directly (non-blocking now)    def schedule_checker(self):
        board.send_command([0, 0, 0, 0, 0, 0]) check and apply scheduled settings"""
        return True
time, "off": datetime}}
    def schedule_checker(self):
        """Background thread to check and apply scheduled settings"""while True:
        # Track the last activation time for each boardself.scheduler_running:
        last_activation = {}  # {board_idx: {"on": datetime, "off": datetime}}
        
        while True:
            if not self.scheduler_running:ent_datetime = datetime.now()
                time.sleep(1)
                continue
                for board_idx, schedule_info in self.board_schedules.items():
            current_datetime = datetime.now()
            changes_made = False
            
            for board_idx, schedule_info in self.board_schedules.items():ime = schedule_info.get("on_time", "")
                if not schedule_info.get("enabled", False):")
                    continue
                    # Initialize tracking for this board if needed
                on_time = schedule_info.get("on_time", "")
                off_time = schedule_info.get("off_time", "")n": None, "off": None}
                
                # Initialize tracking for this board if needed# Parse on_time and off_time
                if board_idx not in last_activation:
                    last_activation[board_idx] = {"on": None, "off": None}on_hour, on_minute = map(int, on_time.split(':'))
                '))
                # Parse on_time and off_time
                try:# Create datetime objects for today's scheduled times
                    on_hour, on_minute = map(int, on_time.split(':'))
                    off_hour, off_minute = map(int, off_time.split(':'))ar, today.month, today.day, on_hour, on_minute)
                    te)
                    # Create datetime objects for today's scheduled times
                    today = current_datetime.date()# Calculate time differences in minutes
                    on_datetime = datetime(today.year, today.month, today.day, on_hour, on_minute) - on_datetime).total_seconds()) / 60
                    off_datetime = datetime(today.year, today.month, today.day, off_hour, off_minute)60
                    
                    # Calculate time differences in minutes# Check if we're within the activation window for ON time (1 minute) and not recently activated
                    on_diff_minutes = abs((current_datetime - on_datetime).total_seconds()) / 60
                    off_diff_minutes = abs((current_datetime - off_datetime).total_seconds()) / 60"on"]).total_seconds() > 120):
                    
                    # Check if we're within the activation window for ON time (1 minute) and not recently activated
                    if on_diff_minutes <= 1 and (last_activation[board_idx]["on"] is None or 
                                              (current_datetime - last_activation[board_idx]["on"]).total_seconds() > 120):
                        if not schedule_info.get("active", True):  # Only change if currently inactive(f"Board {board_idx+1}: Schedule activated - turning ON")
                            # Time to turn on LEDs - just set active flag and apply
                            schedule_info["active"] = True
                            changes_made = True# Check if we're within the activation window for OFF time (1 minute) and not recently activated
                            self.status_var.set(f"Board {board_idx+1}: Schedule activated - turning ON")
                            last_activation[board_idx]["on"] = current_datetimeoff"]).total_seconds() > 120):
                    
                    # Check if we're within the activation window for OFF time (1 minute) and not recently activatedved
                    if off_diff_minutes <= 1 and (last_activation[board_idx]["off"] is None or 
                                               (current_datetime - last_activation[board_idx]["off"]).total_seconds() > 120):
                        if schedule_info.get("active", True):  # Only change if currently active(f"Board {board_idx+1}: Schedule activated - turning OFF (settings preserved)")
                            # Time to turn off LEDs - just set active flag, UI values are preserved
                            schedule_info["active"] = False
                            changes_made = Truevalid
                            self.status_var.set(f"Board {board_idx+1}: Schedule activated - turning OFF (settings preserved)")
                            last_activation[board_idx]["off"] = current_datetime
                except (ValueError, TypeError):# Apply changes if any were made
                    # Skip if time format is invalid
                    continueter(0, self.apply_all_settings)
            
            # Apply changes if any were made.sleep(10)  # Check every 10 seconds
            if changes_made:
                self.root.after(0, self.apply_all_settings)def scan_boards(self):
                ialize connections to XIAO RP2040 boards"""
            time.sleep(10)  # Check every 10 seconds
    
    def scan_boards(self):
        """Detect and initialize connections to XIAO RP2040 boards"""
        # Clear previous boards and GUI elements
        for board in self.boards:for frame in self.board_frames:
            board.disconnect()
        self.boards = [] []
        
        for frame in self.board_frames:
            frame.destroy()# Reset master button state
        self.board_frames = []
        self.led_entries = {}r.set("All Lights OFF")
        
        # Reset master button state
        self.master_on = True# Make sure chamber mapping is loaded
        self.master_button_var.set("All Lights OFF")
        self.saved_values = {}
        # Detect connected boards
        # Make sure chamber mapping is loaded
        self.load_chamber_mapping()detected_boards = self.detect_xiao_boards()
        
        # Detect connected boardsif not detected_boards:
        try:ing("No Boards Found", "No XIAO RP2040 boards were detected.")
            detected_boards = self.detect_xiao_boards()
            
            if not detected_boards:
                messagebox.showwarning("No Boards Found", "No XIAO RP2040 boards were detected.")eate board connections
                self.status_var.set("No boards found")hamber_number in detected_boards:
                return chamber_number))
                
            # Create board connections# Create GUI elements for boards
            for port, serial_number, chamber_number in detected_boards:
                self.boards.append(BoardConnection(port, serial_number, chamber_number))
            # Count boards by chamber ranges
            # Create GUI elements for boardsn self.boards if 1 <= board.chamber_number <= 8)
            self.create_board_frames()6)
            
            # Count boards by chamber rangesself.status_var.set(f"Found {len(self.boards)} board(s): {chambers_1_8} in chambers 1-8, {chambers_9_16} in chambers 9-16")
            chambers_1_8 = sum(1 for board in self.boards if 1 <= board.chamber_number <= 8)
            chambers_9_16 = sum(1 for board in self.boards if 9 <= board.chamber_number <= 16)or("Error Scanning Boards", str(e))
            
            self.status_var.set(f"Found {len(self.boards)} board(s): {chambers_1_8} in chambers 1-8, {chambers_9_16} in chambers 9-16")
        except Exception as e:def detect_xiao_boards(self):
            messagebox.showerror("Error Scanning Boards", str(e))RP2040 boards and assign chamber numbers"""
            self.status_var.set(f"Error: {str(e)}")
    
    def detect_xiao_boards(self):# Detect all XIAO RP2040 boards by VID:PID
        """Detect connected XIAO RP2040 boards and assign chamber numbers"""2E8A:0005'):
        results = []
        
        # Detect all XIAO RP2040 boards by VID:PID# Assign chamber number
        for port_info in list_ports.grep('VID:PID=2E8A:0005'):hamber_mapping.get(serial_number)
            serial_number = port_info.serial_number
            # If no chamber number found, assign a high number (ensuring it comes after known chambers)
            # Assign chamber number
            chamber_number = self.chamber_mapping.get(serial_number)# High number to appear at end when sorted
            t found in chamber mapping")
            # If no chamber number found, assign a high number (ensuring it comes after known chambers)
            if chamber_number is None:results.append([port_info.device, serial_number, chamber_number])
                chamber_number = 100  # High number to appear at end when sorted
                self.status_var.set(f"Warning: Board with S/N {serial_number} not found in chamber mapping")return results
            
            results.append([port_info.device, serial_number, chamber_number])def validate_percentage(self, value):
         percentage (0-100)"""
        return results
    
    def validate_percentage(self, value):
        """Validate that entry is a valid percentage (0-100)"""val = int(value)
        if value == "":<= 100
            return True
        try:
            val = int(value)
            return 0 <= val <= 100def apply_all_settings(self):
        except ValueError:boards"""
            return False
    
    def apply_all_settings(self):
        """Apply settings to all boards"""# Update status
        success_count = 0.set("Applying all settings...")
        error_count = 0
        # Track completion status
        # Update statusboards)
        self.status_var.set("Applying all settings...")_boards
        
        # Track completion statusfor i in range(active_boards):
        active_boards = len(self.boards)i)
        self.pending_operations = active_boards
        sult will be updated by individual board callbacks
        for i in range(active_boards):
            self.apply_board_settings(i)def toggle_all_fans(self):
            or off on all boards"""
        # Result will be updated by individual board callbacks
    warning("No Boards", "No boards available to control fans.")
    def toggle_all_fans(self):
        """Toggle all fans on or off on all boards"""
        if not self.boards:elf.fans_on:
            messagebox.showwarning("No Boards", "No boards available to control fans.")ll fans
            returne
            set("Turn Fans ON")
        if self.fans_on:
            # Turn off all fans# Update status
            self.fans_on = False.set("Turning all fans OFF...")
            self.fan_button_var.set("Turn Fans ON")
            # Track completion status
            # Update statusboards)
            self.status_var.set("Turning all fans OFF...")tive_boards
            
            # Track completion statusfor i, board in enumerate(self.boards):
            active_boards = len(self.boards)success, msg, idx=i: 
            self.pending_fan_operations = active_boardssg, idx, False))
            
            for i, board in enumerate(self.boards):
                board.turn_fan_off(callback=lambda success, msg, idx=i:  Turn on all fans
                                  self.on_toggle_fan_complete(success, msg, idx, False))e
                .set("Turn Fans OFF")
        else:
            # Turn on all fans# Get the speed from the entry
            self.fans_on = True
            self.fan_button_var.set("Turn Fans OFF")speed = int(self.fan_speed_var.get())
            
            # Get the speed from the entryDefault to 50% if invalid
            try:
                speed = int(self.fan_speed_var.get())
            except ValueError:# Update status
                speed = 50  # Default to 50% if invalid.set(f"Turning all fans ON at {speed}%...")
                self.fan_speed_var.set("50")
            # Track completion status
            # Update statusboards)
            self.status_var.set(f"Turning all fans ON at {speed}%...")tive_boards
            
            # Track completion statusfor i, board in enumerate(self.boards):
            active_boards = len(self.boards)=lambda success, msg, idx=i: 
            self.pending_fan_operations = active_boards, True))
            
            for i, board in enumerate(self.boards):def on_toggle_fan_complete(self, success, message, board_idx, is_on):
                board.set_fan_speed(speed, callback=lambda success, msg, idx=i: 
                                   self.on_toggle_fan_complete(success, msg, idx, True))
    
    def on_toggle_fan_complete(self, success, message, board_idx, is_on):if not success:
        """Callback when a toggle fan operation completes"""() to ensure messagebox runs in the main thread
        self.pending_fan_operations -= 1Board {board_idx+1}", message))
        
        if not success:if self.pending_fan_operations == 0:
            # Use after() to ensure messagebox runs in the main thread"
            self.root.after(0, lambda: messagebox.showerror(f"Error - Board {board_idx+1}", message))d_var.get()}%" if is_on else ""
        
        if self.pending_fan_operations == 0:
            action = "ON" if is_on else "OFF"def apply_fan_settings(self):
            speed_info = f" at {self.fan_speed_var.get()}%" if is_on else "" all boards"""
            self.status_var.set(f"All fans turned {action}{speed_info}")
    warning("No Boards", "No boards available to control fans.")
    def apply_fan_settings(self):
        """Apply the fan speed to all boards"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to control fans.")speed = int(self.fan_speed_var.get())
            return
            werror("Invalid Value", "Please enter a valid fan speed (0-100%).")
        try:
            speed = int(self.fan_speed_var.get())
        except ValueError:date status
            messagebox.showerror("Invalid Value", "Please enter a valid fan speed (0-100%).").set(f"Setting all fans to {speed}%...")
            return
            # Track completion status
        # Update statusboards)
        self.status_var.set(f"Setting all fans to {speed}%...")tive_boards
        
        # Track completion statusfor i, board in enumerate(self.boards):
        active_boards = len(self.boards)=lambda success, msg, idx=i: 
        self.pending_fan_operations = active_boardsx, speed))
        
        for i, board in enumerate(self.boards):def on_fan_setting_complete(self, success, message, board_idx, speed):
            board.set_fan_speed(speed, callback=lambda success, msg, idx=i: 
                               self.on_fan_setting_complete(success, msg, idx, speed))
    
    def on_fan_setting_complete(self, success, message, board_idx, speed):if success:
        """Callback when a fan setting operation completes"""e the fans_on flag if needed
        self.pending_fan_operations -= 1
        
        if success:.set("Turn Fans OFF")
            # Update the fans_on flag if needed
            if speed > 0 and not self.fans_on:
                self.fans_on = Trueset("Turn Fans ON")
                self.fan_button_var.set("Turn Fans OFF")
            elif speed == 0 and self.fans_on: Use after() to ensure messagebox runs in the main thread
                self.fans_on = FalseBoard {board_idx+1}", message))
                self.fan_button_var.set("Turn Fans ON")
        else:if self.pending_fan_operations == 0:
            # Use after() to ensure messagebox runs in the main threadset to {speed}% on all boards")
            self.root.after(0, lambda: messagebox.showerror(f"Error - Board {board_idx+1}", message))
        def export_settings(self):
        if self.pending_fan_operations == 0:settings and schedules to a text file"""
            self.status_var.set(f"Fan speed set to {speed}% on all boards")
    warning("No Boards", "No boards available to export settings from.")
    def export_settings(self):
        """Export current LED settings and schedules to a text file"""
        if not self.boards:
            messagebox.showwarning("No Boards", "No boards available to export settings from.")# Collect all settings
            return
            , board in enumerate(self.boards):
        try:
            # Collect all settingschedule": {}, "fan": {}}
            settings = {}
            for board_idx, board in enumerate(self.boards):# Get intensity settings
                chamber_number = board.chamber_numberCHANNELS:
                board_settings = {"intensity": {}, "schedule": {}, "fan": {}}
                value = int(self.led_entries[(board_idx, channel_name)].get())
                # Get intensity settings
                for channel_name in LED_CHANNELS:
                    try:][channel_name] = 0
                        value = int(self.led_entries[(board_idx, channel_name)].get())
                        board_settings["intensity"][channel_name] = value# Get board-level schedule settings
                    except (ValueError, KeyError):s:
                        board_settings["intensity"][channel_name] = 0
                dules[board_idx].get("on_time", "08:00"),
                # Get board-level schedule settings),
                if board_idx in self.board_schedules:
                    board_settings["schedule"] = {
                        "on_time": self.board_schedules[board_idx].get("on_time", "08:00"),
                        "off_time": self.board_schedules[board_idx].get("off_time", "20:00"),# Add fan settings
                        "enabled": self.board_schedules[board_idx].get("enabled", False)n"] = {
                    }enabled,
                
                # Add fan settings
                board_settings["fan"] = {
                    "enabled": board.fan_enabled,# Use chamber number as the key instead of board index
                    "speed": board.fan_speed
                }
                # Get file path from user
                # Use chamber number as the key instead of board indexksaveasfilename(
                settings[f"chamber_{chamber_number}"] = board_settings
             "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],
            # Get file path from user
            file_path = filedialog.asksaveasfilename(
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],if not file_path:
                title="Save LED Settings"r canceled
            )
            ve to file
            if not file_path:_path, 'w') as f:
                return  # User cancelednt=4)
                
            # Save to file.status_var.set(f"Settings exported to {file_path}")
            with open(file_path, 'w') as f:essfully exported to {file_path}")
                json.dump(settings, f, indent=4)
                pt Exception as e:
            self.status_var.set(f"Settings exported to {file_path}")or("Export Error", f"Error exporting settings: {str(e)}")
            messagebox.showinfo("Export Successful", f"Settings successfully exported to {file_path}")
            
        except Exception as e:def import_settings(self):
            messagebox.showerror("Export Error", f"Error exporting settings: {str(e)}") and schedules from a text file and apply them"""
            self.status_var.set(f"Export error: {str(e)}")
    # Get file path from user
    def import_settings(self):kopenfilename(
        """Import LED settings and schedules from a text file and apply them""", ("Text files", "*.txt"), ("All files", "*.*")],
        try:
            # Get file path from user
            file_path = filedialog.askopenfilename(
                filetypes=[("JSON files", "*.json"), ("Text files", "*.txt"), ("All files", "*.*")],if not file_path:
                title="Import LED Settings"r canceled
            )
            ad settings from file
            if not file_path: as f:
                return  # User canceled
                
            # Read settings from file# Validate imported data format
            with open(file_path, 'r') as f:t):
                settings = json.load(f)ings file format")
            
            # Validate imported data formatke sure we have boards to apply settings to
            if not isinstance(settings, dict):
                raise ValueError("Invalid settings file format")warning("No Boards", "No boards connected to apply settings to.")
                
            # Make sure we have boards to apply settings to
            if not self.boards:ply settings to GUI entries
                messagebox.showwarning("No Boards", "No boards connected to apply settings to.")
                returnd = False
                
            # Apply settings to GUI entriesfor board_key, board_settings in settings.items():
            applied_count = 0
            fan_settings_found = False# Extract chamber number (format: "chamber_X")
            
            for board_key, board_settings in settings.items():
                try:# Find the board index for this chamber number
                    # Extract chamber number (format: "chamber_X").boards) if b.chamber_number == chamber_number), None)
                    chamber_number = int(board_key.split("_")[1])
                    if board_idx is None:
                    # Find the board index for this chamber numberif chamber number is not found
                    board_idx = next((i for i, b in enumerate(self.boards) if b.chamber_number == chamber_number), None)
                    # Apply intensity settings
                    if board_idx is None:ttings:
                        continue  # Skip if chamber number is not foundard_settings["intensity"].items():
                    e) in self.led_entries:
                    # Apply intensity settings
                    if "intensity" in board_settings:ue))
                        for channel_name, value in board_settings["intensity"].items():
                            if channel_name in LED_CHANNELS and (board_idx, channel_name) in self.led_entries:
                                self.led_entries[(board_idx, channel_name)].delete(0, tk.END)# Apply schedule settings
                                self.led_entries[(board_idx, channel_name)].insert(0, str(value))ttings:
                                applied_count += 1chedule"]
                    
                    # Apply schedule settings# Update time entries
                    if "schedule" in board_settings:ule and (board_idx, "on") in self.board_time_entries:
                        schedule = board_settings["schedule"]
                        e["on_time"])
                        # Update time entries
                        if "on_time" in schedule and (board_idx, "on") in self.board_time_entries:if "off_time" in schedule and (board_idx, "off") in self.board_time_entries:
                            self.board_time_entries[(board_idx, "on")].delete(0, tk.END)
                            self.board_time_entries[(board_idx, "on")].insert(0, schedule["on_time"])e["off_time"])
                        
                        if "off_time" in schedule and (board_idx, "off") in self.board_time_entries:# Update checkbox
                            self.board_time_entries[(board_idx, "off")].delete(0, tk.END)chedule and board_idx in self.board_schedule_vars:
                            self.board_time_entries[(board_idx, "off")].insert(0, schedule["off_time"])
                        
                        # Update checkbox# Update internal schedule data
                        if "enabled" in schedule and board_idx in self.board_schedule_vars:dules:
                            self.board_schedule_vars[board_idx].set(schedule["enabled"])pdate({
                        08:00"),
                        # Update internal schedule data),
                        if board_idx in self.board_schedules:
                            self.board_schedules[board_idx].update({
                                "on_time": schedule.get("on_time", "08:00"),
                                "off_time": schedule.get("off_time", "20:00"),applied_count += 1
                                "enabled": schedule.get("enabled", False)
                            })# Apply fan settings if present
                        
                        applied_count += 1an"]
                    
                    # Apply fan settings if present
                    if "fan" in board_settings:# Only set the fan speed in the UI for the first board with settings
                        fan = board_settings["fan"]
                        fan_settings_found = Truepeed", 50)))
                        
                        # Only set the fan speed in the UI for the first board with settings# Update fan button state
                        if fan_settings_found and board_idx == 0:se):
                            self.fan_speed_var.set(str(fan.get("speed", 50)))
                            .set("Turn Fans OFF")
                            # Update fan button state
                            if fan.get("enabled", False):elf.fans_on = False
                                self.fans_on = Trueset("Turn Fans ON")
                                self.fan_button_var.set("Turn Fans OFF")
                            else:applied_count += 1
                                self.fans_on = False
                                self.fan_button_var.set("Turn Fans ON")eError, IndexError, KeyError):
                        
                        applied_count += 1
                            self.status_var.set(f"Imported settings from {file_path}")
                except (ValueError, IndexError, KeyError):
                    continue  # Skip invalid entriesif messagebox.askyesno("Apply Settings", 
            d {applied_count} settings from file.\n\nDo you want to apply these settings to the boards now?"):
            self.status_var.set(f"Imported settings from {file_path}")
            
            if messagebox.askyesno("Apply Settings", # Also apply fan settings if they were found
                                 f"Successfully loaded {applied_count} settings from file.\n\nDo you want to apply these settings to the boards now?"):
                self.apply_all_settings()tings()
                
                # Also apply fan settings if they were foundxception as e:
                if fan_settings_found:or("Import Error", f"Error importing settings: {str(e)}")
                    self.apply_fan_settings()
                
        except Exception as e:def check_board_active_state(self, board_idx):
            messagebox.showerror("Import Error", f"Error importing settings: {str(e)}") on current schedule"""
            self.status_var.set(f"Import error: {str(e)}")
    o schedule
    def check_board_active_state(self, board_idx):
        """Check if a board should be active based on current schedule"""dule_info = self.board_schedules[board_idx]
        if board_idx not in self.board_schedules:
            return True  # Default to active if no schedule always active
            
        schedule_info = self.board_schedules[board_idx]# Get time values
        if not schedule_info.get("enabled", False):tetime.now().strftime("%H:%M")
            return True  # Not using scheduling, so always active
        ")
        # Get time values
        current_time = datetime.now().strftime("%H:%M")# Validate time formats
        on_time = schedule_info.get("on_time", "08:00")me_format(on_time) or not self.validate_time_format(off_time):
        off_time = schedule_info.get("off_time", "20:00")
        nvalid schedule time format. Using default ON state.")
        # Validate time formats
        if not self.validate_time_format(on_time) or not self.validate_time_format(off_time):
            # If time format is invalid, default to active (safer)is_active = self.is_time_between(current_time, on_time, off_time)
            self.status_var.set(f"Board {board_idx+1}: WARNING - Invalid schedule time format. Using default ON state.")
            return True# Update the active state in our tracking
        
        is_active = self.is_time_between(current_time, on_time, off_time)
        return is_active
        # Update the active state in our tracking
        schedule_info["active"] = is_activedef save_board_ui_values(self, board_idx):
        without changing them"""
        return is_active
    nabled": False, "active": True, "saved_values": {}}
    def save_board_ui_values(self, board_idx):
        """Save current UI values for a board without changing them"""saved_values = {}
        if board_idx not in self.board_schedules:in LED_CHANNELS:
            self.board_schedules[board_idx] = {"enabled": False, "active": True, "saved_values": {}}e)
        
        saved_values = {}
        for channel_name in LED_CHANNELS:saved_values[channel_name] = self.led_entries[key].get()
            key = (board_idx, channel_name)
            if key in self.led_entries:ed_values[channel_name] = "0"
                try:
                    saved_values[channel_name] = self.led_entries[key].get()self.board_schedules[board_idx]["saved_values"] = saved_values
                except:
                    saved_values[channel_name] = "0"
        # Add time validation method
        self.board_schedules[board_idx]["saved_values"] = saved_valuesf, time_str):
        return saved_valuesn HH:MM format (24-hour)"""
    
    # Add time validation method Empty is okay during typing
    def validate_time_format(self, time_str):
        """Validate that the time string is in HH:MM format (24-hour)"""eck format with regex
        if time_str == "":
            return True  # Empty is okay during typing.match(r'^([0-1][0-9]|2[0-3]):([0-5][0-9])$', time_str):
            
        # Check format with regex
        import rermat is correct
        if not re.match(r'^([0-1][0-9]|2[0-3]):([0-5][0-9])$', time_str):
            return False
            # Add method to validate and provide feedback for time entries
        # Format is correct):
        return True
    
    # Add method to validate and provide feedback for time entrieserror styling
    def validate_time_entry(self, board_idx, entry_type, new_value):
        """Validate time entry and provide feedback"""ries:
        if self.validate_time_format(new_value):onfig(foreground="black")
            # Valid format - reset any previous error styling
            key = (board_idx, entry_type)
            if key in self.board_time_entries: Invalid format - set error styling
                self.board_time_entries[key].config(foreground="black")
            return Trueries:
        else:onfig(foreground="red")
            # Invalid format - set error styling
            key = (board_idx, entry_type)
            if key in self.board_time_entries:if __name__ == "__main__":
                self.board_time_entries[key].config(foreground="red")
            return FalseolGUI(root)
if __name__ == "__main__":
    root = tk.Tk()
    app = LEDControlGUI(root)
    root.mainloop()