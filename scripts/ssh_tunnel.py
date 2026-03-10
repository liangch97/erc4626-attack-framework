# SSH tunnel setup script with password authentication using paramiko
# Improved version with better connection handling and larger buffers

import subprocess
import sys
import time
import socket
import threading
import select

def forward_tunnel(local_port, remote_host, remote_port, ssh_host, ssh_port, username, password):
    """Create SSH tunnel using paramiko with improved reliability"""
    print(f"Setting up tunnel: localhost:{local_port} -> {remote_host}:{remote_port} via {ssh_host}")
    
    try:
        import paramiko
    except ImportError:
        print("Installing paramiko...")
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'paramiko'])
        import paramiko
    
    # Create SSH client
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    print(f"Connecting to {ssh_host}:{ssh_port} as {username}...")
    client.connect(ssh_host, port=ssh_port, username=username, password=password, timeout=30)
    print("SSH connection established!")
    
    # Get transport and enable keepalive
    transport = client.get_transport()
    transport.set_keepalive(30)  # Send keepalive every 30 seconds
    
    # Create local socket
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', local_port))
    server.listen(100)  # Increased backlog for more concurrent connections
    print(f"Listening on 127.0.0.1:{local_port}")
    
    active_connections = 0
    lock = threading.Lock()
    
    def handle_client(client_socket, addr):
        nonlocal active_connections
        with lock:
            active_connections += 1
        
        channel = None
        try:
            channel = transport.open_channel(
                'direct-tcpip', 
                (remote_host, remote_port), 
                client_socket.getpeername(),
                timeout=30
            )
            if channel is None:
                print(f"[{addr}] Channel open failed")
                client_socket.close()
                return
            
            # Set channel timeout
            channel.settimeout(60)
            
            while True:
                r, w, x = select.select([client_socket, channel], [], [], 5.0)
                if client_socket in r:
                    data = client_socket.recv(65536)  # 64KB buffer
                    if len(data) == 0:
                        break
                    channel.sendall(data)
                if channel in r:
                    data = channel.recv(65536)  # 64KB buffer
                    if len(data) == 0:
                        break
                    client_socket.sendall(data)
        except Exception as e:
            print(f"[{addr}] Tunnel error: {e}")
        finally:
            try:
                if channel:
                    channel.close()
            except:
                pass
            try:
                client_socket.close()
            except:
                pass
            with lock:
                active_connections -= 1
    
    print("SSH tunnel ready! Waiting for connections...")
    try:
        while True:
            client_socket, addr = server.accept()
            client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            t = threading.Thread(target=handle_client, args=(client_socket, addr))
            t.daemon = True
            t.start()
    except KeyboardInterrupt:
        print("Shutting down tunnel...")
    finally:
        server.close()
        client.close()

if __name__ == '__main__':
    forward_tunnel(
        local_port=18545,
        remote_host='127.0.0.1',
        remote_port=18545,
        ssh_host='172.16.108.231',
        ssh_port=22,
        username='flash_swap_test',
        password='flash_swap'
    )
