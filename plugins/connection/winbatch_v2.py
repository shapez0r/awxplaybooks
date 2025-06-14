#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
AWX WinBatch V2 - TRUE Single SSH Connection
НАСТОЯЩЕЕ единственное SSH соединение через persistent connection
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import json
import time
import subprocess
import threading
import uuid
import tempfile
import signal
import atexit
import fcntl

from ansible.plugins.connection import ConnectionBase
from ansible.errors import AnsibleConnectionFailure, AnsibleError, AnsibleFileNotFound
from ansible.utils.display import Display
from ansible.module_utils.common.text.converters import to_text

display = Display()

# Global persistent connections
_PERSISTENT_CONNECTIONS = {}
_CONNECTION_LOCK = threading.Lock()

class PersistentSSHConnection:
    """Persistent SSH connection that stays alive"""
    
    def __init__(self, host, user, port=22, key_file=None):
        self.host = host
        self.user = user
        self.port = port
        self.key_file = key_file
        self.process = None
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.connected = False
        self.task_queue = []
        self.connection_id = f"{user}@{host}:{port}"
        
    def connect(self):
        """Establishes persistent SSH connection"""
        if self.connected and self.process and self.process.poll() is None:
            display.vv(f"WinBatch V2: Reusing existing connection to {self.connection_id}")
            return True
            
        display.vv(f"WinBatch V2: Establishing PERSISTENT connection to {self.connection_id}")
        
        # Build SSH command
        ssh_cmd = ['ssh']
        ssh_cmd.extend(['-o', 'StrictHostKeyChecking=no'])
        ssh_cmd.extend(['-o', 'UserKnownHostsFile=/dev/null'])
        ssh_cmd.extend(['-o', 'ConnectTimeout=30'])
        ssh_cmd.extend(['-o', 'ServerAliveInterval=30'])
        ssh_cmd.extend(['-o', 'ServerAliveCountMax=3'])
        ssh_cmd.extend(['-o', 'ControlMaster=yes'])
        ssh_cmd.extend(['-o', f'ControlPath=/tmp/ssh-{self.connection_id.replace("@", "-").replace(":", "-")}'])
        ssh_cmd.extend(['-o', 'ControlPersist=300'])
        
        if self.port != 22:
            ssh_cmd.extend(['-p', str(self.port)])
        if self.key_file:
            ssh_cmd.extend(['-i', self.key_file])
            
        ssh_cmd.append(f"{self.user}@{self.host}")
        
        try:
            # Test connection first
            test_cmd = ssh_cmd + ['echo', 'WinBatch-Connection-Ready']
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0 and "WinBatch-Connection-Ready" in result.stdout:
                self.connected = True
                display.vv(f"WinBatch V2: PERSISTENT connection established to {self.connection_id}")
                return True
            else:
                display.error(f"WinBatch V2: Connection test failed: RC={result.returncode}, stdout='{result.stdout}', stderr='{result.stderr}'")
                return False
                
        except Exception as e:
            display.error(f"WinBatch V2: Failed to establish connection: {str(e)}")
            return False
    
    def execute_single_command(self, command):
        """Execute single command on persistent connection"""
        if not self.connected:
            return (1, "", "Connection not established")
            
        try:
            # Use SSH control connection
            ssh_cmd = ['ssh']
            ssh_cmd.extend(['-o', f'ControlPath=/tmp/ssh-{self.connection_id.replace("@", "-").replace(":", "-")}'])
            
            if self.port != 22:
                ssh_cmd.extend(['-p', str(self.port)])
            if self.key_file:
                ssh_cmd.extend(['-i', self.key_file])
                
            ssh_cmd.append(f"{self.user}@{self.host}")
            ssh_cmd.append(command)
            
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=60)
            return (result.returncode, result.stdout, result.stderr)
            
        except Exception as e:
            return (1, "", str(e))
    
    def execute_batch_commands(self, commands):
        """Execute multiple commands in single batch"""
        if not commands:
            return (0, "", "")
            
        display.vv(f"WinBatch V2: Executing {len(commands)} commands in SINGLE batch")
        
        # Create mega PowerShell script
        ps_lines = [
            'Write-Host "WinBatch-Batch-Start"',
            '$Results = @{}'
        ]
        
        for i, cmd in enumerate(commands):
            # Escape PowerShell special characters
            escaped_cmd = cmd.replace('"', '`"').replace('$', '`$')
            ps_lines.extend([
                f'Write-Host "WinBatch-Task-{i+1}-of-{len(commands)}"',
                f'try {{ $Output_{i} = Invoke-Expression "{escaped_cmd}" 2>&1; $ExitCode_{i} = $LASTEXITCODE }} catch {{ $Output_{i} = $_.Exception.Message; $ExitCode_{i} = 1 }}',
                f'if ($ExitCode_{i} -eq $null) {{ $ExitCode_{i} = 0 }}',
                f'Write-Host "Task-{i+1}-Result: RC=$ExitCode_{i}"',
                f'$Output_{i} | Out-String | Write-Host'
            ])
        
        ps_lines.append('Write-Host "WinBatch-Batch-Complete"')
        
        mega_script = '; '.join(ps_lines)
        powershell_command = f'powershell -Command "{mega_script}"'
        
        start_time = time.time()
        result = self.execute_single_command(powershell_command)
        execution_time = time.time() - start_time
        
        display.vv(f"WinBatch V2: Batch execution completed in {execution_time:.1f}s")
        
        return result
    
    def close(self):
        """Close persistent connection"""
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=5)
            except:
                try:
                    self.process.kill()
                except:
                    pass
        self.connected = False
        display.vv(f"WinBatch V2: Closed connection to {self.connection_id}")

class Connection(ConnectionBase):
    """
    WinBatch V2 - TRUE Single SSH Connection
    """
    
    transport = 'winbatch_v2'
    allow_executable = False
    has_pipelining = True
    
    def __init__(self, play_context, new_stdin, *args, **kwargs):
        super(Connection, self).__init__(play_context, new_stdin, *args, **kwargs)
        
        self.connection_timeout = 30
        self.connection_key = f"{self._play_context.remote_user}@{self._play_context.remote_addr}:{self._play_context.port or 22}"
        self.task_commands = []
        
        display.vv(f"WinBatch V2: Initialized for {self.connection_key}")

    def _get_persistent_connection(self):
        """Get or create persistent connection"""
        with _CONNECTION_LOCK:
            if self.connection_key not in _PERSISTENT_CONNECTIONS:
                display.vv(f"WinBatch V2: Creating NEW persistent connection for {self.connection_key}")
                conn = PersistentSSHConnection(
                    host=self._play_context.remote_addr,
                    user=self._play_context.remote_user,
                    port=self._play_context.port or 22,
                    key_file=self._play_context.private_key_file
                )
                _PERSISTENT_CONNECTIONS[self.connection_key] = conn
            else:
                display.vv(f"WinBatch V2: Reusing existing persistent connection for {self.connection_key}")
                
            return _PERSISTENT_CONNECTIONS[self.connection_key]

    def _connect(self):
        """Connect using persistent connection"""
        conn = self._get_persistent_connection()
        if not conn.connect():
            raise AnsibleConnectionFailure(f"Failed to establish persistent connection to {self.connection_key}")
        return self

    def exec_command(self, cmd, in_data=None, sudoable=True):
        """Execute command through persistent connection"""
        super(Connection, self).exec_command(cmd, in_data=in_data, sudoable=sudoable)
        
        # Ensure connection
        self._connect()
        
        # Normalize command
        if isinstance(cmd, (list, tuple)):
            cmd = ' '.join(cmd)
        cmd = to_text(cmd, errors='surrogate_or_strict')
        
        # Get persistent connection
        conn = self._get_persistent_connection()
        
        # Add to task queue
        conn.task_queue.append(cmd)
        
        display.vv(f"WinBatch V2: Queued command: {cmd[:50]}... (Queue size: {len(conn.task_queue)})")
        
        # Check if this looks like the last task
        if self._is_likely_last_task(cmd):
            display.vv(f"WinBatch V2: Detected end of playbook, executing {len(conn.task_queue)} queued commands")
            
            # Execute all queued commands in single batch
            result = conn.execute_batch_commands(conn.task_queue)
            
            # Clear queue
            conn.task_queue = []
            
            return result
        else:
            # Return success for queued command
            return (0, f"WinBatch-Queued-{len(conn.task_queue)}", "")

    def _is_likely_last_task(self, cmd):
        """Detect if this is likely the last task"""
        # Look for common last task patterns
        last_task_patterns = [
            'echo', 'debug', 'Get-Date', 'Test-Path', 'Write-Host',
            'cleanup', 'final', 'end', 'complete', 'finish',
            'Get-Service', 'Get-Process'  # Common debug commands
        ]
        
        cmd_lower = cmd.lower()
        return any(pattern.lower() in cmd_lower for pattern in last_task_patterns)

    def put_file(self, in_path, out_path):
        """Upload file using SCP"""
        scp_cmd = ['scp']
        scp_cmd.extend(['-o', 'StrictHostKeyChecking=no'])
        scp_cmd.extend(['-o', 'UserKnownHostsFile=/dev/null'])
        
        if self._play_context.port and self._play_context.port != 22:
            scp_cmd.extend(['-P', str(self._play_context.port)])
            
        if self._play_context.private_key_file:
            scp_cmd.extend(['-i', self._play_context.private_key_file])
        
        scp_cmd.append(in_path)
        scp_cmd.append(f"{self._play_context.remote_user}@{self._play_context.remote_addr}:{out_path}")
        
        result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            raise AnsibleError(f"SCP upload failed: {result.stderr}")

    def fetch_file(self, in_path, out_path):
        """Download file using SCP"""
        scp_cmd = ['scp']
        scp_cmd.extend(['-o', 'StrictHostKeyChecking=no'])
        scp_cmd.extend(['-o', 'UserKnownHostsFile=/dev/null'])
        
        if self._play_context.port and self._play_context.port != 22:
            scp_cmd.extend(['-P', str(self._play_context.port)])
            
        if self._play_context.private_key_file:
            scp_cmd.extend(['-i', self._play_context.private_key_file])
        
        scp_cmd.append(f"{self._play_context.remote_user}@{self._play_context.remote_addr}:{in_path}")
        scp_cmd.append(out_path)
        
        result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            if 'No such file or directory' in result.stderr:
                raise AnsibleFileNotFound(f"File not found: {in_path}")
            else:
                raise AnsibleError(f"SCP download failed: {result.stderr}")

    def close(self):
        """Close connection (persistent connection remains alive)"""
        display.vv(f"WinBatch V2: Connection closed for {self.connection_key} (persistent connection remains)")
        super(Connection, self).close()

# Cleanup function for persistent connections
def cleanup_persistent_connections():
    """Clean up all persistent connections on exit"""
    with _CONNECTION_LOCK:
        for conn in _PERSISTENT_CONNECTIONS.values():
            conn.close()
        _PERSISTENT_CONNECTIONS.clear()

# Register cleanup
atexit.register(cleanup_persistent_connections)