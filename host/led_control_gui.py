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
        ttk.Button(bottom_frame, text="Import Settings", command=self.import_settings).pack(side=tk.RIGHT, padx=5)
        
        # Status bar
        status_bar = ttk.Label(main_frame, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.grid(column=0, row=4, columnspan=2, sticky=(tk.W, tk.E))
        
        # Add window close handler for cleanup
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.scan_boards()
    
    def on_closing(self):
        """Clean up resources and close the application"""
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
        
        # Update page label and navigation buttons
        if self.current_page == 0:
            self.page_label.config(text="Chambers 1-8")
            self.prev_button.config(state=tk.DISABLED)
            self.next_button.config(state=tk.NORMAL if len(self.boards) > 8 else tk.DISABLED)
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
            frame = ttk.LabelFrame(self.boards_container, text=f"Chamber {chamber_number} (S/N: {board.serial_number})")
            self.board_frames.append(frame)
            
            # LED control section
            led_control_frame = ttk.LabelFrame(frame, text="LED Controls")
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
                entry = ttk.Spinbox(
                    led_control_frame, 
                    from_=0, 
                    to=100, 
                    width=5, 
                    textvariable=value_var,
                    validate='key',
                    validatecommand=(self.root.register(self.validate_percentage), '%P')
                )
                entry.grid(column=2, row=row, sticky=tk.W, padx=5)
                ttk.Label(led_control_frame, text="%").grid(column=3, row=row, sticky=tk.W)
                self.led_entries[(i, channel_name)] = entry
            
            # Scheduling section - one per board
            schedule_frame = ttk.LabelFrame(frame, text="Board Schedule")
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
            self.status_var.set("Scheduler stopped")
        else:
            self.scheduler_running = True
            self.scheduler_button_var.set("Stop Scheduler")
            self.status_var.set("Scheduler started")
            if not self.scheduler_thread or not self.scheduler_thread.is_alive():
                self.start_scheduler()
    
    def start_scheduler(self):
        """Start the scheduler thread"""
        self.scheduler_running = True
        self.scheduler_button_var.set("Stop Scheduler")
        self.scheduler_thread = threading.Thread(target=self.schedule_checker, daemon=True)
        self.scheduler_thread.start()
    
    def is_time_between(self, current_time, start_time, end_time):
        """Check if current time is between start and end times, handling overnight periods"""
        try:
            # Convert all times to datetime for comparison
            current = datetime.strptime(current_time, "%H:%M")
            start = datetime.strptime(start_time, "%H:%M")
            end = datetime.strptime(end_time, "%H:%M")
            
            if start <= end:
                # Simple case: start time is before end time (e.g., 08:00 to 20:00)
                return start <= current <= end
            else:
                # Wrap-around case: end time is before start time (e.g., 20:00 to 08:00 next day)
                return current >= start or current <= end
        except ValueError:
            # If any time format is invalid, default to True (safer)
            return True
    
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
            self.status_var.set(f"Board {board_idx+1}: Applying zeros to hardware (schedule OFF time, UI settings preserved)")
            # Send zeros to board, but don't change UI
            board.send_command([0, 0, 0, 0, 0, 0], 
                              callback=lambda success, msg: self.on_board_command_complete(
                                  board_idx, success, msg, "during scheduled OFF time"))
        else:
            # Board should be on - apply actual values from UI
            status_msg = "Applying settings..."
            if scheduling_enabled and schedule_active:
                status_msg = "Applying settings (during scheduled ON time)..."
            self.status_var.set(f"Board {board_idx+1}: {status_msg}")
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
                self.status_var.set(f"Chamber {chamber_number}: Settings applied ({extra_info})")
            else:
                self.status_var.set(f"Chamber {chamber_number}: Settings applied successfully")
        else:
            # Use after() to ensure messagebox runs in the main thread
            self.root.after(0, lambda: messagebox.showerror(f"Error - Chamber {chamber_number}", message))
            self.status_var.set(f"Chamber {chamber_number}: Error - {message}")
    
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
            # Reset the checkbox
            self.board_schedule_vars[board_idx].set(False)
            is_enabled = False
        
        self.board_schedules[board_idx]["enabled"] = is_enabled
        # If scheduling was just enabled, check if we need to update active state
        if is_enabled:
            # Check current active state
            is_active = self.check_board_active_state(board_idx)
            # If we're outside active hours and just enabled scheduling, turn off lights
            if not is_active:
                # Outside of ON period - send zeros but keep UI values
                self.status_var.set(f"Board {board_idx+1}: Schedule enabled, outside ON hours - turning lights off (settings preserved)")
                # Send direct command to turn off LEDs without changing UI
                self.send_zeros_to_board(board_idx)
        elif was_enabled and not is_enabled:
            # Scheduling was just disabled, ensure active state is reset
            self.board_schedules[board_idx]["active"] = True
            # Re-apply the current UI settings to restore the board state
            self.status_var.set(f"Board {board_idx+1}: Schedule disabled - applying current settings")
            self.apply_board_settings(board_idx)
    
    def send_zeros_to_board(self, board_idx):
        """Send zeros to the board without changing UI values"""
        if board_idx >= len(self.boards):
            return False
            
        board = self.boards[board_idx]
        # Send command with all zeros directly (non-blocking now)
        board.send_command([0, 0, 0, 0, 0, 0])
        return True
    
    def schedule_checker(self):
        """Background thread to check and apply scheduled settings"""
        # Track the last activation time for each board
        last_activation = {}  # {board_idx: {"on": datetime, "off": datetime}}
        
        while True:
            if not self.scheduler_running:
                time.sleep(1)
                continue
            
            current_datetime = datetime.now()
            changes_made = False
            
            for board_idx, schedule_info in self.board_schedules.items():
                if not schedule_info.get("enabled", False):
                    continue
                    
                # Initialize tracking for this board if needed
                if board_idx not in last_activation:
                    last_activation[board_idx] = {"on": None, "off": None}
                
                # Parse on_time and off_time
                on_time = schedule_info.get("on_time", "")
                off_time = schedule_info.get("off_time", "")
                
                try:
                    on_hour, on_minute = map(int, on_time.split(':'))
                    off_hour, off_minute = map(int, off_time.split(':'))
                    
                    # Create datetime objects for today's scheduled times
                    today = current_datetime.date()
                    on_datetime = datetime(today.year, today.month, today.day, on_hour, on_minute)
                    off_datetime = datetime(today.year, today.month, today.day, off_hour, off_minute)
                    
                    # Calculate time differences in minutes
                    on_diff_minutes = abs((current_datetime - on_datetime).total_seconds()) / 60
                    off_diff_minutes = abs((current_datetime - off_datetime).total_seconds()) / 60
                    
                    # Check if we're within the activation window for ON time (1 minute) and not recently activated
                    if on_diff_minutes <= 1 and (last_activation[board_idx]["on"] is None or 
                                              (current_datetime - last_activation[board_idx]["on"]).total_seconds() > 120):
                        if not schedule_info.get("active", True):  # Only change if currently inactive
                            # Time to turn on LEDs - just set active flag and apply
                            schedule_info["active"] = True
                            changes_made = True
                            self.status_var.set(f"Board {board_idx+1}: Schedule activated - turning ON")
                            last_activation[board_idx]["on"] = current_datetime
                    
                    # Check if we're within the activation window for OFF time (1 minute) and not recently activated
                    if off_diff_minutes <= 1 and (last_activation[board_idx]["off"] is None or 
                                               (current_datetime - last_activation[board_idx]["off"]).total_seconds() > 120):
                        if schedule_info.get("active", True):  # Only change if currently active
                            # Time to turn off LEDs - just set active flag, UI values are preserved
                            schedule_info["active"] = False
                            changes_made = True
                            self.status_var.set(f"Board {board_idx+1}: Schedule activated - turning OFF (settings preserved)")
                            last_activation[board_idx]["off"] = current_datetime
                except (ValueError, TypeError):
                    # Skip if time format is invalid
                    continue
            
            # Apply changes if any were made
            if changes_made:
                self.root.after(0, self.apply_all_settings)
            
            time.sleep(10)  # Check every 10 seconds
    
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
        
        # Detect all XIAO RP2040 boards by VID:PID
        for port_info in list_ports.grep('VID:PID=2E8A:0005'):
            serial_number = port_info.serial_number
            # Assign chamber number
            chamber_number = self.chamber_mapping.get(serial_number)
            # If no chamber number found, assign a high number (ensuring it comes after known chambers)
            if chamber_number is None:
                chamber_number = 100  # High number to appear at end when sorted
                self.status_var.set(f"Warning: Board with S/N {serial_number} not found in chamber mapping")
            results.append([port_info.device, serial_number, chamber_number])
        
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
            
        # Check format with regex
        import re
        if not re.match(r'^([0-1][0-9]|2[0-3]):([0-5][0-9])$', time_str):
            return False
        
        # Format is correct
        return True
    
    def validate_time_entry(self, board_idx, entry_type, new_value):
        """Validate time entry and provide feedback"""
        if self.validate_time_format(new_value):
            # Valid format - reset any previous error styling
            key = (board_idx, entry_type)
            if key in self.board_time_entries:
                self.board_time_entries[key].config(foreground="black")
            return True
        else:
            # Invalid format - set error styling
            key = (board_idx, entry_type)
            if key in self.board_time_entries:
                self.board_time_entries[key].config(foreground="red")
            return False


if __name__ == "__main__":
    root = tk.Tk()
    app = LEDControlGUI(root)
    root.mainloop()