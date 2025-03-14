import tkinter as tk
from tkinter import messagebox, filedialog
import serial
import serial.tools.list_ports
import json
import time
import threading

# Function to wait for a response with timeout
def read_with_timeout(ser, timeout=2):
    start_time = time.time()
    response = ''
    while time.time() - start_time < timeout:
        if ser.in_waiting > 0:
            new_data = ser.readline().strip().decode('utf-8', errors='ignore')
            response += new_data
            if new_data.endswith('\n') or new_data.endswith('\r'):
                break
        time.sleep(0.1)
    return response.strip()

def main():
    # Detect all serial ports for more flexibility
    available_ports = list(serial.tools.list_ports.comports())
    
    # First look for XIAO RP2040 boards
    xiao_ports = list(serial.tools.list_ports.grep('VID:PID=2E8A:0005'))
    
    # If no XIAO boards found, ask user if they want to try all ports
    if not xiao_ports and available_ports:
        use_all_ports = messagebox.askyesno("No XIAO RP2040 boards detected", 
                                            "No XIAO RP2040 boards were detected. Would you like to try connecting to all available serial ports?")
        if use_all_ports:
            ports_to_try = available_ports
        else:
            messagebox.showerror("Error", "No XIAO RP2040 boards detected.")
            return
    else:
        ports_to_try = xiao_ports[:16]  # Limit to 16 boards
    
    boards = []
    
    # Establish serial connections
    for port in ports_to_try:
        try:
            # Try different common baud rates if the default doesn't work
            baud_rates = [115200, 9600, 57600, 38400]
            connected = False
            
            for baud in baud_rates:
                try:
                    ser = serial.Serial(port.device, baud, timeout=1)
                    # Flush any pending data
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                    
                    # Wait a moment for device to initialize
                    time.sleep(0.5)
                    
                    # Look for initial "READY" message
                    initial_response = read_with_timeout(ser, 1)
                    if initial_response and "READY" in initial_response:
                        print(f"Device at {port.device} is ready at {baud} baud")
                        connected = True
                        break
                    
                    # Test communication with different line endings
                    for ending in [b"PING\n", b"PING\r\n", b"PING\r"]:
                        ser.reset_input_buffer()
                        ser.write(ending)
                        response = read_with_timeout(ser, 1)
                        
                        if response and "OK" in response:
                            print(f"Device at {port.device} responded correctly at {baud} baud")
                            connected = True
                            break
                    
                    if connected:
                        break
                    
                    ser.close()
                
                except Exception as e:
                    print(f"Failed with baud rate {baud}: {e}")
            
            if connected:
                serial_number = getattr(port, 'serial_number', f"Port_{port.device.split('/')[-1]}")
                boards.append({
                    'serial_number': serial_number, 
                    'ser': ser, 
                    'status': 'connected',
                    'port': port.device,
                    'baud': baud
                })
                print(f"Connected to board at {port.device} - Serial: {serial_number}")
            else:
                print(f"Could not establish communication with device at {port.device}")
        
        except serial.SerialException as e:
            print(f"Failed to open {port.device}: {e}")
            messagebox.showerror("Error", f"Serial connection failed for {port.device}: {e}")
    
    # Check the number of connected boards
    num_boards = len(boards)
    if num_boards == 0:
        messagebox.showerror("Error", "No boards could be connected. Please check your connections and make sure the microcontroller code is uploaded correctly.")
        return
    else:
        messagebox.showinfo("Connection Status", f"Successfully connected to {num_boards} board(s).")
        
    # Initialize the Tkinter GUI
    root = tk.Tk()
    root.title("LED Brightness Control")
    
    # Create a frame for the canvas and scrollbar
    canvas_frame = tk.Frame(root)
    canvas_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
    
    # Create a scrollable canvas for the board columns
    canvas = tk.Canvas(canvas_frame)
    canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
    
    scrollbar = tk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=canvas.xview)
    scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
    canvas.configure(xscrollcommand=scrollbar.set)
    
    # Frame to hold all board columns
    boards_frame = tk.Frame(canvas)
    canvas.create_window((0, 0), window=boards_frame, anchor='nw')
    
    # Update scroll region when the frame size changes
    boards_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    
    # Dictionary to store widgets for each board
    entries = {}
    status_labels = {}
    channel_names = ['UV', 'FAR_RED', 'RED', 'WHITE', 'GREEN', 'BLUE']
    
    # Create GUI elements for each detected board
    for i, board in enumerate(boards):
        serial_number = board['serial_number']
        port_info = board['port']
        
        # Use LabelFrame with serial number and port as the title
        board_frame = tk.LabelFrame(boards_frame, text=f"{serial_number} - {port_info}")
        board_frame.grid(row=0, column=i, padx=10, pady=10)
        
        # Add status indicator
        status_label = tk.Label(board_frame, text="Connected", fg="green")
        status_label.pack(pady=(0, 10))
        status_labels[serial_number] = status_label
        
        board_entries = []
        # Add labels and entry fields for each LED channel
        for j, channel in enumerate(channel_names):
            frame = tk.Frame(board_frame)
            frame.pack(fill=tk.X, padx=5, pady=2)
            tk.Label(frame, text=channel, width=8, anchor='w').pack(side=tk.LEFT)
            entry = tk.Entry(frame, width=5)
            entry.pack(side=tk.LEFT)
            entry.insert(0, "0")  # Default value of 0%
            board_entries.append(entry)
        
        entries[serial_number] = board_entries
    
    # Function to send a command and get a response
    def send_command(ser, command, timeout=2):
        try:
            ser.reset_input_buffer()
            
            # Try different line endings if needed
            command_sent = False
            for ending in ["\n", "\r\n", "\r"]:
                try:
                    ser.write((command + ending).encode())
                    command_sent = True
                    break
                except:
                    continue
                    
            if not command_sent:
                return None
                
            return read_with_timeout(ser, timeout)
        except serial.SerialException as e:
            print(f"Serial error: {e}")
            return None
    
    # Function to apply settings to all boards
    def apply_settings():
        for board in boards:
            serial_number = board['serial_number']
            ser = board['ser']
            board_entries = entries[serial_number]
            status_label = status_labels[serial_number]
            
            try:
                percentages = []
                # Validate and collect input for each channel
                for channel, entry in zip(channel_names, board_entries):
                    value = entry.get().strip()
                    if not value:  # Allow empty fields as 0%
                        p = 0.0
                    else:
                        p = float(value)
                        if not 0 <= p <= 100:
                            raise ValueError(f"Value {value} for {channel} is out of range (0-100).")
                    percentages.append(p)
                
                # Convert percentages to PWM duty cycles (0-4095)
                duties = [int((p / 100.0) * 4095) for p in percentages]
                command = "SETALL " + " ".join(map(str, duties))
                
                status_label.config(text="Sending command...", fg="orange")
                root.update_idletasks()  # Force GUI update
                
                response = send_command(ser, command)
                
                if response and "OK" in response:
                    status_label.config(text="Settings applied", fg="green")
                else:
                    status_label.config(text="Command failed", fg="red")
                    print(f"Board {serial_number} response: {response}")
            
            except ValueError as e:
                status_label.config(text="Input error", fg="red")
                messagebox.showerror("Error", f"Input error for board {serial_number}: {e}")
            except serial.SerialException as e:
                status_label.config(text="Connection lost", fg="red")
                board['status'] = 'disconnected'
                messagebox.showerror("Error", f"Serial error for board {serial_number}: {e}")
    
    # Function to check board connections periodically
    def check_connections():
        while True:
            for board in boards:
                if board['status'] == 'disconnected':
                    continue
                    
                try:
                    response = send_command(board['ser'], "PING")
                    if response and response.startswith("OK"):
                        status_labels[board['serial_number']].config(text="Connected", fg="green")
                    else:
                        status_labels[board['serial_number']].config(text="Not responding", fg="orange")
                except:
                    status_labels[board['serial_number']].config(text="Connection lost", fg="red")
                    board['status'] = 'disconnected'
            time.sleep(5)  # Check every 5 seconds
    
    # Start connection check in a separate thread
    connection_thread = threading.Thread(target=check_connections, daemon=True)
    connection_thread.start()
    
    # Function to save settings to a file
    def save_settings():
        settings = {}
        for serial_number, board_entries in entries.items():
            settings[serial_number] = [entry.get().strip() for entry in board_entries]
        
        file_path = filedialog.asksaveasfilename(defaultextension=".json", 
                                             filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if file_path:
            with open(file_path, 'w') as f:
                json.dump(settings, f)
            messagebox.showinfo("Success", "Settings saved successfully")
    
    # Function to load settings from a file
    def load_settings():
        file_path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if file_path:
            with open(file_path, 'r') as f:
                settings = json.load(f)
            
            for serial_number, values in settings.items():
                if serial_number in entries:
                    for entry, value in zip(entries[serial_number], values):
                        entry.delete(0, tk.END)
                        entry.insert(0, value)
            messagebox.showinfo("Success", "Settings loaded successfully")
    
    # Create a button frame at the bottom
    button_frame = tk.Frame(root)
    button_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=10)
    
    # Add a reconnect button
    reconnect_button = tk.Button(button_frame, text="Reconnect All", command=lambda: reconnect_boards())
    reconnect_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
    
    # Add all buttons with proper spacing
    save_button = tk.Button(button_frame, text="Save Settings", command=save_settings)
    save_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
    
    load_button = tk.Button(button_frame, text="Load Settings", command=load_settings)
    load_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
    
    apply_button = tk.Button(button_frame, text="Apply Settings", command=apply_settings, bg="#a0d6b4")
    apply_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
    
    # Function to reconnect to boards
    def reconnect_boards():
        for board in boards:
            if board['status'] == 'disconnected':
                try:
                    ser = serial.Serial(board['port'], board['baud'], timeout=1)
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()
                    
                    response = send_command(ser, "PING")
                    if response and "OK" in response:
                        board['ser'] = ser
                        board['status'] = 'connected'
                        status_labels[board['serial_number']].config(text="Reconnected", fg="green")
                    else:
                        status_labels[board['serial_number']].config(text="Failed to reconnect", fg="red")
                except Exception as e:
                    print(f"Failed to reconnect to {board['port']}: {e}")
    
    # Start the GUI event loop
    root.mainloop()
    
    # Clean up serial connections on exit
    for board in boards:
        if board['ser'].is_open:
            board['ser'].close()

if __name__ == "__main__":
    main()