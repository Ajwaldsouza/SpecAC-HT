import tkinter as tk
from tkinter import messagebox, filedialog
import serial
import serial.tools.list_ports
import json
import time
import threading

def main():
    # Detect connected XIAO RP2040 boards
    ports = list(serial.tools.list_ports.grep('VID:PID=2E8A:0005'))
    boards = []
    
    # Establish serial connections for up to 16 boards
    for port in ports[:16]:
        try:
            ser = serial.Serial(port.device, 115200, timeout=1)
            # Flush any pending data
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            
            # Test communication with a simple command
            ser.write(b"PING\n")
            response = ser.readline().strip().decode('utf-8', errors='ignore')
            if not response.startswith("OK"):
                messagebox.showwarning("Warning", f"Board at {port.device} did not respond correctly. It may not be programmed with the correct firmware.")
                
            boards.append({'serial_number': port.serial_number, 'ser': ser, 'status': 'connected'})
            print(f"Connected to board {port.serial_number} at {port.device}")
        except serial.SerialException as e:
            print(f"Failed to open {port.device}: {e}")
            messagebox.showerror("Error", f"Serial connection failed for {port.device}: {e}")
    
    # Check the number of connected boards and show a warning if not exactly 16
    num_boards = len(boards)
    if num_boards == 0:
        messagebox.showerror("Error", "No XIAO RP2040 boards detected.")
        return
    elif num_boards < 16:
        messagebox.showwarning("Warning", f"Only {num_boards} board(s) detected out of 16.")

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
        
        # Use LabelFrame with serial number as the title
        board_frame = tk.LabelFrame(boards_frame, text=serial_number)
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
    def send_command(ser, command):
        try:
            ser.reset_input_buffer()
            ser.write((command + "\n").encode())
            response = ser.readline().strip().decode('utf-8', errors='ignore')
            return response
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
                
                response = send_command(ser, command)
                
                if response and response.startswith("OK"):
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
    
    # Add all buttons with proper spacing
    save_button = tk.Button(button_frame, text="Save Settings", command=save_settings)
    save_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
    
    load_button = tk.Button(button_frame, text="Load Settings", command=load_settings)
    load_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
    
    apply_button = tk.Button(button_frame, text="Apply Settings", command=apply_settings, bg="#a0d6b4")
    apply_button.pack(side=tk.LEFT, padx=5, fill=tk.X, expand=True)
    
    # Start the GUI event loop
    root.mainloop()
    
    # Clean up serial connections on exit
    for board in boards:
        if board['ser'].is_open:
            board['ser'].close()

if __name__ == "__main__":
    main()