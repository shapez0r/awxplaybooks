#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
AWX WinBatch V2 - Persistent Batch Architecture
Революционный SSH connection plugin для Windows
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import json
import time
import subprocess
import threading
import uuid

from ansible.plugins.connection import ConnectionBase
from ansible.errors import AnsibleConnectionFailure, AnsibleError, AnsibleFileNotFound
from ansible.utils.display import Display
from ansible.module_utils.common.text.converters import to_text

display = Display()

class Connection(ConnectionBase):
    """
    WinBatch V2 Persistent SSH Connection Plugin
    ОДНО соединение + батчинг команд + прогресс-репорты
    """
    
    transport = 'winbatch_v2'
    allow_executable = False
    has_pipelining = True
    
    def __init__(self, play_context, new_stdin, *args, **kwargs):
        super(Connection, self).__init__(play_context, new_stdin, *args, **kwargs)
        
        # Persistent connection state
        self._connected = False
        self._connection_lock = threading.Lock()
        
        # Batch execution state
        self._batch_id = str(uuid.uuid4())[:8]
        self._command_queue = []
        self._batch_executing = False
        
        # Configuration
        self.batch_size = 10  # Smaller batches for testing
        self.connection_timeout = 30
        
        # SSH command template
        self._ssh_cmd = self._build_ssh_command()
        
        display.vv(f"WinBatch V2 initialized: batch_id={self._batch_id}")

    def _build_ssh_command(self):
        """Строит SSH команду с persistent connection"""
        ssh_cmd = ['ssh']
        
        # SSH options for persistent connection
        ssh_cmd.extend([
            '-o', 'ControlMaster=auto',
            '-o', f'ControlPath=/tmp/winbatch-{self._batch_id}-%h-%p-%r',
            '-o', 'ControlPersist=300s',
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', f'ConnectTimeout={self.connection_timeout}'
        ])
        
        # Port
        if self._play_context.port:
            ssh_cmd.extend(['-p', str(self._play_context.port)])
        
        # Private key
        if self._play_context.private_key_file:
            ssh_cmd.extend(['-i', self._play_context.private_key_file])
        
        # Target
        if self._play_context.remote_user:
            ssh_cmd.append(f"{self._play_context.remote_user}@{self._play_context.remote_addr}")
        else:
            ssh_cmd.append(self._play_context.remote_addr)
        
        return ssh_cmd

    def _connect(self):
        """Устанавливает ОДНО persistent SSH соединение"""
        if self._connected:
            return self
            
        display.vv(f"WinBatch V2: Establishing PERSISTENT connection to {self._play_context.remote_addr}")
        
        try:
            with self._connection_lock:
                if self._connected:
                    return self
                
                # Test master connection
                test_cmd = self._ssh_cmd + ['echo', f'WinBatch-{self._batch_id}-Ready']
                result = subprocess.run(test_cmd, 
                                      capture_output=True, 
                                      text=True, 
                                      timeout=self.connection_timeout)
                
                if result.returncode != 0:
                    raise AnsibleConnectionFailure(f"SSH connection failed: {result.stderr}")
                
                display.vv(f"WinBatch V2: PERSISTENT connection established - {result.stdout.strip()}")
                self._connected = True
                
        except Exception as e:
            raise AnsibleConnectionFailure(f"Failed to establish persistent connection: {str(e)}")
        
        return self

    def exec_command(self, cmd, in_data=None, sudoable=True):
        """
        Добавляет команду в batch queue
        """
        super(Connection, self).exec_command(cmd, in_data=in_data, sudoable=sudoable)
        
        self._ensure_connected()
        
        # Normalize command
        if isinstance(cmd, (list, tuple)):
            cmd = ' '.join(cmd)
        cmd = to_text(cmd, errors='surrogate_or_strict')
        
        display.vvv(f"WinBatch V2: QUEUING command: {cmd[:50]}...")
        
        # Add to batch queue
        command_id = len(self._command_queue)
        self._command_queue.append({
            'id': command_id,
            'command': cmd,
            'timestamp': time.time()
        })
        
        # Execute batch if queue is full
        if len(self._command_queue) >= self.batch_size:
            display.vv(f"WinBatch V2: Batch size reached, executing {len(self._command_queue)} commands")
            return self._execute_batch()
        else:
            # Return placeholder for queued command
            display.vvv(f"WinBatch V2: Command queued ({len(self._command_queue)}/{self.batch_size})")
            return (0, f"WinBatch-Queued-{command_id}", "")

    def _execute_batch(self):
        """Выполняет batch команд на Windows машине"""
        if not self._command_queue or self._batch_executing:
            return (0, "", "")
            
        display.vv(f"WinBatch V2: EXECUTING BATCH of {len(self._command_queue)} commands")
        
        self._batch_executing = True
        
        try:
            # Create simple batch script
            commands = [cmd['command'] for cmd in self._command_queue]
            batch_script = self._create_simple_batch_script(commands)
            
            # Execute batch
            batch_cmd = self._ssh_cmd + ['powershell', '-Command', batch_script]
            
            result = subprocess.run(
                batch_cmd,
                capture_output=True,
                text=True,
                timeout=300
            )
            
            display.vv(f"WinBatch V2: Batch completed with RC: {result.returncode}")
            
            return (result.returncode, result.stdout or "", result.stderr or "")
            
        except Exception as e:
            error_msg = f"Batch execution failed: {str(e)}"
            display.error(f"WinBatch V2: {error_msg}")
            return (1, "", error_msg)
            
        finally:
            self._batch_executing = False
            self._command_queue = []

    def _create_simple_batch_script(self, commands):
        """Создает простой PowerShell скрипт для batch выполнения"""
        script_lines = [
            f'Write-Host "WinBatch-{self._batch_id}: Starting batch of {len(commands)} commands"',
            '$Results = @()'
        ]
        
        for i, cmd in enumerate(commands):
            script_lines.extend([
                f'Write-Host "WinBatch-{self._batch_id}: Executing command {i+1}/{len(commands)}"',
                f'try {{ {cmd} }} catch {{ Write-Error $_.Exception.Message }}'
            ])
        
        script_lines.append(f'Write-Host "WinBatch-{self._batch_id}: Batch completed"')
        
        return '; '.join(script_lines)

    def put_file(self, in_path, out_path):
        """Загружает файл через persistent SCP"""
        self._ensure_connected()
        
        scp_cmd = ['scp', '-o', f'ControlPath=/tmp/winbatch-{self._batch_id}-%h-%p-%r']
        
        if self._play_context.port:
            scp_cmd.extend(['-P', str(self._play_context.port)])
            
        if self._play_context.private_key_file:
            scp_cmd.extend(['-i', self._play_context.private_key_file])
        
        scp_cmd.append(in_path)
        
        if self._play_context.remote_user:
            scp_cmd.append(f"{self._play_context.remote_user}@{self._play_context.remote_addr}:{out_path}")
        else:
            scp_cmd.append(f"{self._play_context.remote_addr}:{out_path}")
        
        result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            raise AnsibleError(f"SCP upload failed: {result.stderr}")

    def fetch_file(self, in_path, out_path):
        """Скачивает файл через persistent SCP"""
        self._ensure_connected()
        
        scp_cmd = ['scp', '-o', f'ControlPath=/tmp/winbatch-{self._batch_id}-%h-%p-%r']
        
        if self._play_context.port:
            scp_cmd.extend(['-P', str(self._play_context.port)])
            
        if self._play_context.private_key_file:
            scp_cmd.extend(['-i', self._play_context.private_key_file])
        
        if self._play_context.remote_user:
            scp_cmd.append(f"{self._play_context.remote_user}@{self._play_context.remote_addr}:{in_path}")
        else:
            scp_cmd.append(f"{self._play_context.remote_addr}:{in_path}")
            
        scp_cmd.append(out_path)
        
        result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
        
        if result.returncode != 0:
            if 'No such file or directory' in result.stderr:
                raise AnsibleFileNotFound(f"File not found: {in_path}")
            else:
                raise AnsibleError(f"SCP download failed: {result.stderr}")

    def _ensure_connected(self):
        """Обеспечивает активное соединение"""
        if not self._connected:
            self._connect()

    def close(self):
        """Закрывает persistent соединение"""
        display.vv("WinBatch V2: Closing persistent connection")
        
        with self._connection_lock:
            if self._connected:
                # Execute remaining commands
                if self._command_queue and not self._batch_executing:
                    try:
                        display.vv(f"WinBatch V2: Executing final batch of {len(self._command_queue)} commands")
                        self._execute_batch()
                    except:
                        pass
                
                # Close SSH master connection
                close_cmd = ['ssh', '-O', 'exit', '-o', f'ControlPath=/tmp/winbatch-{self._batch_id}-%h-%p-%r']
                if self._play_context.remote_user:
                    close_cmd.append(f"{self._play_context.remote_user}@{self._play_context.remote_addr}")
                else:
                    close_cmd.append(self._play_context.remote_addr)
                
                try:
                    subprocess.run(close_cmd, capture_output=True, timeout=10)
                    display.vv("WinBatch V2: SSH master connection closed")
                except:
                    pass
                
                self._connected = False
        
        super(Connection, self).close() 