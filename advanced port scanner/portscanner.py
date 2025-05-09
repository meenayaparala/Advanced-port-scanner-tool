import re
import tkinter as tk
from tkinter import ttk
import threading
import socket
import queue
import ipaddress
import csv
import os
import urllib.request
import paramiko  # SSH login
import ftplib  # FTP login
import requests
from requests.auth import HTTPBasicAuth  # HTTP login (for basic auth)
from ftplib import FTP
from tkinter import messagebox
from tkinter import filedialog

# Function to download and parse the IANA Service Port database
def download_and_parse_iana_data():
    url = "https://www.iana.org/assignments/service-names-port-numbers/service-names-port-numbers.csv"
    local_file = "service-names-port-numbers.csv"

    # Download the file if it doesn't exist locally
    if not os.path.exists(local_file):
        print("Downloading IANA Service Name and Port Number data...")
        urllib.request.urlretrieve(url, local_file)
        print("Download complete!")

    # Parse the CSV file into a dictionary
    service_mapping = {}
    with open(local_file, newline='', encoding="utf-8") as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            try:
                port = int(row['Port Number'])
                service_mapping[port] = row['Service Name']
            except (ValueError, KeyError):
                continue  # Skip rows without valid port numbers

    return service_mapping

# Load the mapping globally
PORT_SERVICE_MAPPING = download_and_parse_iana_data()

# Function to handle the input values and scanning
def scan_target():
    target = entry_target.get()
    resolved_ip = resolve_ip()
    if not resolved_ip:
        return

    protocol = selected_protocol.get()
    ports = entry_ports.get()
    start_port = entry_start_port.get()
    end_port = entry_end_port.get()

    # Clear previous results
    for row in tree.get_children():
        tree.delete(row)

    try:
        ipaddress.ip_address(resolved_ip)
    except ValueError:
        label_resolved_ip_value.config(text="Invalid IP", fg="red")
        return

    port_list = []
    if scan_all_ports_var.get():
        port_list = range(1, 65536)
    elif ports:
        try:
            port_list = [int(port.strip()) for port in ports.split(",") if port.strip().isdigit()]
        except ValueError:
            label_resolved_ip_value.config(text="Invalid port input", fg="red")
            return
    else:
        try:
            start_port, end_port = int(start_port), int(end_port)
            port_list = range(start_port, end_port + 1)
        except ValueError:
            label_resolved_ip_value.config(text="Invalid port range", fg="red")
            return

    result_queue = queue.Queue()
    stree = ttk.Treeview(root, columns=("Port", "Service", "Protocol", "Status", "Banner"), show="headings", style="Custom.Treeview")
    tree.heading("Port", text="Port")
    tree.heading("Service", text="Service")
    tree.heading("Protocol", text="Protocol")
    tree.heading("Status", text="Status")
    tree.heading("Banner", text="Banner")
    tree.tag_configure("open_port", background="green")
    tree.tag_configure("closed_port", background="red")
    tree.bind("<Double-1>", on_port_double_click)


    def scan_port(target, port, selected_protocol):
        for proto in (["TCP", "UDP"] if selected_protocol == "Both" else [selected_protocol]):
            sock_type = socket.SOCK_DGRAM if proto == "UDP" else socket.SOCK_STREAM
            with socket.socket(socket.AF_INET, sock_type) as s:
                s.settimeout(1)
                try:
                    result = s.connect_ex((target, port)) if proto != "UDP" else 0
                    service_name = PORT_SERVICE_MAPPING.get(port, "Unknown")
                    banner = get_banner(s, proto, port) if result == 0 else ""
                    status = "Open" if result == 0 else "Closed"
                    result_queue.put((port, service_name, proto, status, banner))
                except Exception as e:
                    result_queue.put((port, "Unknown", proto, "Closed", ""))
                    print(f"Error scanning port {port} ({proto}): {e}")
    
    def get_banner(sock, protocol, port):
        banner = ""
        try:
            if protocol == "TCP":
                sock.sendall(b"HEAD / HTTP/1.0\r\n\r\n")
                banner = sock.recv(1024).decode(errors="ignore").strip()
        except Exception as e:
            banner = "Error fetching banner"
            print(f"Error fetching banner: {e}")

        return banner

    threads = []
    for port in port_list:
        t = threading.Thread(target=scan_port, args=(resolved_ip, port, protocol))
        threads.append(t)
        t.start()

    def update_tree():
        while not result_queue.empty():
            result = result_queue.get()
            tree.insert("", "end", values=result, tags=("open_port" if result[3] == "Open" else "closed_port",))
        if all(not t.is_alive() for t in threads):
            if tree.get_children():
                tree.grid(row=9, column=0, columnspan=2, padx=10, pady=10, sticky="nsew")
                scrollbar.grid(row=9, column=2, padx=5, pady=5, sticky="ns")
            else:
                label_resolved_ip_value.config(text="No open ports found", fg="red")
        else:
            root.after(100, update_tree)

    update_tree()

def resolve_ip():
    target = entry_target.get()
    try:
        resolved_ip = socket.gethostbyname(target)
        label_resolved_ip_value.config(text=resolved_ip, fg="green")
        return resolved_ip
    except socket.gaierror:
        label_resolved_ip_value.config(text="Unable to resolve", fg="red")
        return None
    
# Functions to handle service-specific connections
def http_connect(target, port):
    try:
        url = f"http://{target}:{port}"
        response = requests.get(url, timeout=5)
        label_connect_status.config(
            text=f"HTTP Connected: {url} (Status: {response.status_code})", fg="green"
        )
    except Exception as e:
        label_connect_status.config(text=f"HTTP connection failed: {e}", fg="red")

def ftp_connect(target, port):
    try:
        ftp = ftplib.FTP()
        ftp.connect(target, port, timeout=5)
        ftp.login()
        label_connect_status.config(
            text=f"FTP Connected: {target}:{port} (Welcome: {ftp.getwelcome()})",
            fg="green",
        )
        ftp.quit()
    except Exception as e:
        label_connect_status.config(text=f"FTP connection failed: {e}", fg="red")

def ssl_connect(target, port):
    try:
        context = ssl.create_default_context()
        with socket.create_connection((target, port)) as sock:
            with context.wrap_socket(sock, server_hostname=target) as ssock:
                label_connect_status.config(
                    text=f"SSL/TLS Connected: {target}:{port} (Cipher: {ssock.cipher()})",
                    fg="green",
                )
    except Exception as e:
        label_connect_status.config(text=f"SSL/TLS connection failed: {e}", fg="red")

def generic_tcp_connect(target, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect((target, port))
            label_connect_status.config(
                text=f"Connected to {target} on TCP port {port}.", fg="green"
            )
    except Exception as e:
        label_connect_status.config(text=f"TCP connection failed: {e}", fg="red")

def generic_udp_connect(target, port):
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(5)
            s.sendto(b"Test UDP Connection", (target, port))
            label_connect_status.config(
                text=f"Sent data to {target} on UDP port {port}.", fg="green"
            )
    except Exception as e:
        label_connect_status.config(text=f"UDP connection failed: {e}", fg="red")



# Function to connect to a port when a user double-clicks an open port in the treeview
def on_port_double_click(event):
    selected_item = tree.selection()
    if not selected_item:
        return
    port_details = tree.item(selected_item, "values")
    port = port_details[0]
    protocol = port_details[2]
    status = port_details[3]

    if status != "Open":
        return  # Only attempt to connect to open ports

    target = entry_target.get()
    connection_type = selected_connection_type.get()  # Assume there's a dropdown or radio button to select connection type

    # Perform vulnerability check based on selected connection type
    if connection_type == "SSH":
        vulnerability_result = check_ssh_vulnerability(target, port)
    elif connection_type == "HTTP":
        vulnerability_result = check_http_vulnerability(target, port)
    elif connection_type == "FTP":
        vulnerability_result = check_ftp_vulnerability(target, port)
    else:
        vulnerability_result = "Vulnerability check not implemented for this protocol."

    # Display the vulnerability result in a popup message
    messagebox.showinfo("Vulnerability Check", f"Port {port} vulnerability: {vulnerability_result}")
    print(f"Vulnerability Check for Port {port}: {vulnerability_result}")


# Initialize GUI
root = tk.Tk()
root.title("The Advanced Port Scanner Tool")
root.geometry("900x900")
root.configure(bg="black")


heading = tk.Label(
    root, text="The Advanced Port Scanner Tool", fg="green", bg="black", font=("Helvetica", 40, "bold")
)
heading.grid(row=0, column=0,columnspan=2, pady=1,sticky="e")  # Use `sticky="w"` here

label_target = tk.Label( root,text="Target (IP or Domain):", fg="red", bg="black", font=("Helvetica", 12))
label_target.grid(row=1, column=0, padx=1, pady=1,sticky="w")

entry_target = tk.Entry(
    root, fg="green", bg="black", insertbackground="green", font=("Helvetica", 12)
)
entry_target.grid(row=1, column=1, padx=1,pady=1,sticky="w")  # Apply sticky here to align left
entry_target.bind("<FocusOut>", lambda event: resolve_ip())

label_resolved_ip = tk.Label(root, text="Resolved IP: ", fg="red", bg="black", font=("Helvetica", 12))
label_resolved_ip.grid(row=2, column=0, padx=1,pady=1,sticky="w")  # Apply sticky here to align left

label_resolved_ip_value = tk.Label(root, text="", fg="green", bg="black", font=("Helvetica", 12))
label_resolved_ip_value.grid(row=2, column=1, padx=1,pady=1,sticky="w")  # Apply sticky here to align left

label_protocol = tk.Label(root, text="Select Protocol:", fg="red", bg="black", font=("Helvetica", 12))
label_protocol.grid(row=3, column=0, padx=1, pady=1,sticky="w")

selected_protocol = tk.StringVar(value="Both")
protocol_options = ttk.Combobox(root, textvariable=selected_protocol, values=["TCP", "UDP", "Both"], state="readonly", font=("Helvetica", 12))
protocol_options.grid(row=3, column=1, padx=1, pady=1,sticky="w")

label_ports = tk.Label(root, text="Ports (comma separated):", fg="red", bg="black", font=("Helvetica", 12))
label_ports.grid(row=4, column=0, padx=1, pady=1,sticky="w")

entry_ports = tk.Entry(root, fg="green", bg="black", insertbackground="green", font=("Helvetica", 12))
entry_ports.grid(row=4, column=1, padx=1, pady=1,sticky="w")

label_start_port = tk.Label(root, text="Start Port:", fg="red", bg="black", font=("Helvetica", 12))
label_start_port.grid(row=5, column=0, padx=1, pady=1,sticky="w")

entry_start_port = tk.Entry(root, fg="green", bg="black", insertbackground="green", font=("Helvetica", 12))
entry_start_port.grid(row=5, column=1, padx=1, pady=1,sticky="w")

label_end_port = tk.Label(root, text="End Port:", fg="red", bg="black", font=("Helvetica", 12))
label_end_port.grid(row=6, column=0, padx=1, pady=1,sticky="w")

entry_end_port = tk.Entry(root,fg="green", bg="black", insertbackground="green", font=("Helvetica", 12))
entry_end_port.grid(row=6, column=1, padx=1, pady=1,sticky="w")

scan_all_ports_var = tk.BooleanVar()
scan_all_ports_check = tk.Checkbutton(root, text="Scan All Ports (1-65535)", variable=scan_all_ports_var, fg="red", bg="black", font=("Helvetica", 12), selectcolor="black")
scan_all_ports_check.grid(row=7, column=0, columnspan=1, pady=5,sticky="w")

button_scan = tk.Button(root,text="Scan", command=scan_target, fg="black", bg="green", font=("Helvetica", 12, "bold"))
button_scan.grid(row=8, column=0, columnspan=1, pady=1,sticky="e")

tree = ttk.Treeview(columns=("Port", "Service", "Protocol", "Status", "Banner"), show="headings", style="Custom.Treeview")
tree.heading("Port", text="Port")
tree.heading("Service", text="Service")
tree.heading("Protocol", text="Protocol")
tree.heading("Status", text="Status")
tree.heading("Banner", text="Banner")
tree.tag_configure("open_port", background="green")
tree.tag_configure("closed_port", background="red")
tree.bind("<Double-1>", on_port_double_click)
tree.column("Port", width=50, anchor="center")
tree.column("Service",width=50,anchor="center")
tree.column("Protocol",width=50,anchor="center")
tree.column("Status",width=50,anchor="center")
tree.column("Banner",width=50,anchor="center")

scrollbar = ttk.Scrollbar(root, orient="vertical", command=tree.yview)
tree.configure(yscrollcommand=scrollbar.set)


tree.grid(row=9, column=0, columnspan=3, pady=5, sticky="nsew")
scrollbar.grid(row=9, column=4, sticky="ns")  # Adjust scrollbar position


tree.configure(yscrollcommand=scrollbar.set)

# Label for Connection Type
label_connection_type = tk.Label(
    root, text="Select Connection Type:", fg="red", bg="black", font=("Helvetica", 12)
)
label_connection_type.grid(row=12, column=0, padx=5, pady=5,sticky="w")

# Dropdown menu for connection type
selected_connection_type = tk.StringVar(value="None")
connection_types = ["HTTP", "FTP", "SSL/TLS", "Generic TCP", "Generic UDP"]
connection_type_dropdown = ttk.Combobox(
    root, textvariable=selected_connection_type, values=connection_types, state="readonly", font=("Helvetica", 12)
)
connection_type_dropdown.grid(row=12, column=1, padx=5, pady=5,sticky="w")

def connect_to_selected_port():
    selected_item = tree.selection()
    if not selected_item:
        label_connect_status.config(text="No port selected.", fg="red")
        return

    port_details = tree.item(selected_item, "values")
    port = int(port_details[0])
    protocol = port_details[2]
    status = port_details[3]

    if status != "Open":
        label_connect_status.config(text="Cannot connect to closed ports.", fg="red")
        return

    target = entry_target.get()
    connection_type = selected_connection_type.get()

    # Now perform the connection and vulnerability check
    if connection_type == "SSH":
        generic_tcp_connect(target, port)
        vulnerability_result = check_ssh_vulnerability(target, port)
    elif connection_type == "HTTP":
        http_connect(target, port)
        vulnerability_result = check_http_vulnerability(target, port)
    elif connection_type == "FTP":
        ftp_connect(target, port)
        vulnerability_result = check_ftp_vulnerability(target, port)
    elif connection_type == "SSL/TLS":
        ssl_connect(target, port)
        vulnerability_result = "SSL/TLS vulnerability check not implemented."
    elif connection_type == "Generic TCP":
        generic_tcp_connect(target, port)
        vulnerability_result = "No known vulnerability checks for Generic TCP."
    elif connection_type == "Generic UDP":
        generic_udp_connect(target, port)
        vulnerability_result = "No known vulnerability checks for Generic UDP."
    else:
        label_connect_status.config(text="Invalid connection type selected.", fg="red")
        return

    # Show the result of the vulnerability check
    label_connect_status.config(
        text=f"Connection successful. {vulnerability_result}", fg="green"
    )
    # Show an alert if the port is vulnerable
    if "Vulnerable" in vulnerability_result:
        messagebox.showwarning(
            "Vulnerability Detected", f"Port {port} is vulnerable: {vulnerability_result}"
        )


def on_port_single_click(event):
    selected_item = tree.selection()
    print(f"Single-clicked Item: {selected_item}")
    if selected_item:
        print(f"Selected Item Details: {tree.item(selected_item, 'values')}")

# Connect button to initiate connection attempt
button_connect = tk.Button(
    root,
    text="Connect",
    command=connect_to_selected_port,
    fg="black",
    bg="green",
    font=("Helvetica", 12, "bold")
)
button_connect.grid(row=13, column=0,  pady=5,sticky="e")




# Status label for connection attempts
label_connect_status = tk.Label(
    root, text="", fg="red", bg="black", font=("Helvetica", 12)
)
label_connect_status.grid(row=14, column=0, columnspan=1,sticky="e", pady=5)

# Default credentials for vulnerability testing
DEFAULT_CREDENTIALS = {
    'HTTP': [
        ('admin', 'admin'),
        ('admin', 'password'),
        ('admin', '1234'),
        ('admin', '12345'),
        ('admin', '123456'),
        ('admin', 'default'),
        ('admin', 'changeme'),
        ('admin', 'welcome'),
        ('admin', 'letmein'),
        ('root', 'root'),
        ('root', 'password'),
        ('root', '1234'),
        ('root', '12345'),
        ('root', '123456'),
        ('user', 'user'),
        ('user', 'password'),
        ('guest', 'guest'),
        ('test', 'test'),
        ('superuser', 'password'),
        ('cisco', 'cisco'),
        ('support', 'support'),
        ('system', 'manager'),
        ('administrator', 'administrator'),
    ],
    'FTP': [
        ('anonymous', 'anonymous'),
        ('ftp', 'ftp'),
        ('ftp', 'password'),
        ('ftp', '1234'),
        ('ftp', '12345'),
        ('ftp', '123456'),
        ('ftpuser', 'ftpuser'),
        ('admin', 'admin'),
        ('admin', 'password'),
        ('admin', '1234'),
        ('admin', '12345'),
        ('admin', 'changeme'),
        ('user', 'password'),
        ('guest', 'guest'),
        ('test', 'test'),
        ('root', 'root'),
        ('root', 'password'),
        ('root', '1234'),
    ],
    'SSH': [
        ('root', 'root'),
        ('root', 'password'),
        ('root', '1234'),
        ('root', '12345'),
        ('root', '123456'),
        ('root', 'toor'),
        ('admin', 'admin'),
        ('admin', 'password'),
        ('admin', '1234'),
        ('admin', '12345'),
        ('admin', 'changeme'),
        ('pi', 'raspberry'),
        ('ubuntu', 'ubuntu'),
        ('test', 'test'),
        ('guest', 'guest'),
        ('oracle', 'oracle'),
    ],
    'Generic TCP': [
        ('admin', 'admin'),
        ('admin', 'password'),
        ('admin', '1234'),
        ('admin', '12345'),
        ('admin', 'changeme'),
        ('root', 'root'),
        ('root', 'password'),
        ('root', '1234'),
        ('root', '12345'),
        ('root', '123456'),
        ('user', 'user'),
        ('user', 'password'),
        ('guest', 'guest'),
        ('test', 'test'),
        ('default', 'default'),
        ('supervisor', 'supervisor'),
        ('manager', 'manager'),
    ],
    'Generic UDP': [
        ('admin', 'admin'),
        ('admin', 'password'),
        ('admin', '1234'),
        ('admin', '12345'),
        ('root', 'root'),
        ('root', 'password'),
        ('root', '1234'),
        ('user', 'user'),
        ('guest', 'guest'),
        ('test', 'test'),
    ]
}



def check_http_vulnerability(target, port):
    """Attempt to login using common HTTP credentials."""
    for username, password in DEFAULT_CREDENTIALS['HTTP']:
        try:
            url = f"http://{target}:{port}"
            response = requests.get(url, auth=HTTPBasicAuth(username, password), timeout=5)
            if response.status_code == 200:
                return f"Vulnerable with credentials: {username}/{password}"  # Return if login is successful
        except requests.RequestException as e:
            print(f"HTTP Connection Error: {e}")
    return "No known vulnerabilities found"  # Return if no default credentials worked

def check_ftp_vulnerability(target, port):
    """Attempt to login using common FTP credentials."""
    for username, password in DEFAULT_CREDENTIALS['FTP']:
        try:
            # Create FTP object and attempt connection
            ftp = ftplib.FTP()
            ftp.connect(target, port, timeout=5)
            ftp.login(username, password)  # Try login with default credentials
            ftp.quit()  # Close the connection if login is successful
            return f"Vulnerable with credentials: {username}/{password}"  # If login succeeds, return vulnerable message
        except ftplib.all_errors as e:
            # If FTP connection or login fails, continue trying the next set of credentials
            print(f"FTP Connection Error: {e}")
    return "No known vulnerabilities found"  # Return if no default credentials worked

def check_ssh_vulnerability(target, port):
    """Attempt to login using common SSH credentials."""
    for username, password in DEFAULT_CREDENTIALS['SSH']:
        try:
            # Initialize the SSH client
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # Automatically add host key
            # Attempt SSH login with default credentials
            ssh.connect(target, port=port, username=username, password=password, timeout=5)
            ssh.close()  # Close the connection if login is successful
            return f"Vulnerable with credentials: {username}/{password}"  # Return vulnerable message on success
        except paramiko.AuthenticationException:
            # If authentication fails, continue to the next set of credentials
            continue
        except Exception as e:
            # Handle other SSH connection errors (timeouts, etc.)
            print(f"SSH Connection Error: {e}")
    return "No known vulnerabilities found"  # Return if no default credentials worked


# Function to get open ports using nmap
def get_open_ports(target_ip):
    """Run nmap to get open ports for the target IP."""
    try:
        result = subprocess.run(['nmap', target_ip], capture_output=True, text=True)
        open_ports = []
        for line in result.stdout.splitlines():
            if "open" in line:
                port = line.split()[0]
                open_ports.append(port)
        return open_ports
    except Exception as e:
        return f"Error running nmap: {e}"


# Function to save work to a file
def save_work_to_file():
    try:
        # Open a file dialog to specify the location to save
        file_path = tk.filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")]
        )
        if not file_path:
            return  # User cancelled the save dialog

        # Prepare data to save
        data = []
        data.append(f"Target: {entry_target.get()}")
        data.append(f"Resolved IP: {label_resolved_ip_value.cget('text')}")
        data.append(f"Selected Protocol: {selected_protocol.get()}")
        data.append(f"Ports: {entry_ports.get()}")
        data.append(f"Start Port: {entry_start_port.get()}")
        data.append(f"End Port: {entry_end_port.get()}")
        data.append(f"Scan All Ports: {scan_all_ports_var.get()}")
        data.append("\nResults:\n")
        for child in tree.get_children():
            port, service, protocol, status, banner = tree.item(child)["values"]
            data.append(f"Port: {port}, Service: {service}, Protocol: {protocol}, Status: {status}, Banner: {banner}")

        # Write data to the file
        with open(file_path, "w") as file:
            file.write("\n".join(data))
        
        # Notify the user that the file has been saved
        tk.messagebox.showinfo("Success", f"Work saved to {file_path}")
    except Exception as e:
        tk.messagebox.showerror("Error", f"Failed to save work: {e}")

# Add a Save button to the GUI
button_save_work = tk.Button(root, text="Save Work", command=save_work_to_file, fg="black", bg="green", font=("Helvetica", 12, "bold"))
button_save_work.grid(row=15, column=0, columnspan=1,sticky="w", pady=1)

def show_project_info():
    project_info = """
    Advanced Port Scanner Tool
This tool is designed to:

Perform port scanning for specified IP addresses or domains.
Identify open ports and retrieve banners.
Attempt login using custom credentials for SSH, FTP, and HTTP.
Provide a user-friendly GUI with scrollable output.
Save results to a file for later analysis.
Developed by: Y. Meena Kumari
Student ID: SUP/2024/ST#IS#6905
Version: 1.0

This project was developed as part of a Cyber Security Internship under 
the guidance of Supraja Technologies, an MSME and ISO 9001:2015 Certified Company.
The internship program provided intensive training and mentorship on cybersecurity principles,
hands-on project work, and exposure to real-world tools and practices.
The project aligns with industry standards and reflects a commitment to innovation and 
practical applications in cybersecurity.
    """
    tk.messagebox.showinfo("Project Info", project_info)

button_project_info = tk.Button(
    root, 
    text="Project Info", 
    command=show_project_info, 
    fg="black", 
    bg="green", 
    font=("Helvetica", 12, "bold")
)
button_project_info.grid(row=15, column=1, columnspan=2,sticky="w", pady=1)

def show_local_ip():
    try:
        # Fetch the local IP address
        local_ip = socket.gethostbyname(socket.gethostname())
        # Display the IP in a message box
        tk.messagebox.showinfo("Local IP Address", f"Your Local IP Address is: {local_ip}")
    except Exception as e:
        # Handle errors (e.g., network issues)
        tk.messagebox.showerror("Error", f"Could not fetch local IP: {e}")

button_local_ip = tk.Button(
    root, 
    text="Local IP", 
    command=show_local_ip, 
    fg="black", 
    bg="green", 
    font=("Helvetica", 12, "bold")
)
button_local_ip.grid(row=16, column=0, columnspan=1,sticky="w", pady=5,padx=5)

def show_target_ip():
    try:
        # Get the target value from the entry field
        target = entry_target.get().strip()
        if not target:
            raise ValueError("No target specified. Please enter a target.")

        # Resolve the target to an IP address
        target_ip = socket.gethostbyname(target)
        # Display the resolved IP in a message box
        tk.messagebox.showinfo("Target IP Address", f"The Target IP Address is: {target_ip}")
    except socket.gaierror:
        tk.messagebox.showerror("Error", "Could not resolve the target to an IP address. Please check the target.")
    except ValueError as e:
        tk.messagebox.showerror("Error", str(e))
    except Exception as e:
        tk.messagebox.showerror("Error", f"An unexpected error occurred: {e}")

button_target_ip = tk.Button(
    root, 
    text="Target IP", 
    command=show_target_ip, 
    fg="black", 
    bg="green", 
    font=("Helvetica", 12, "bold")
)
button_target_ip.grid(row=16, column=1, columnspan=2,sticky="w", pady=5,padx=5)
root.mainloop()
