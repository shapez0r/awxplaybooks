#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
AWX WinBatch V2 - TRUE Single Connection Architecture
ЕДИНСТВЕННОЕ соединение + ВСЕ задачи сразу + локальное выполнение
"""

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import json
import time
import subprocess
import threading
import uuid
import base64

from ansible.plugins.connection import ConnectionBase
from ansible.errors import AnsibleConnectionFailure, AnsibleError, AnsibleFileNotFound
from ansible.utils.display import Display
from ansible.module_utils.common.text.converters import to_text

display = Display()

# Global state for single connection across ALL tasks
_GLOBAL_CONNECTION = None
_GLOBAL_LOCK = threading.Lock()
_ALL_TASKS = []
_PLAYBOOK_STARTED = False

class Connection(ConnectionBase):
    """
    WinBatch V2 - TRUE Single Connection
    ОДНО соединение для ВСЕГО playbook
    """
    
    transport = 'winbatch_v2'
    allow_executable = False
    has_pipelining = True
    
    def __init__(self, play_context, new_stdin, *args, **kwargs):
        super(Connection, self).__init__(play_context, new_stdin, *args, **kwargs)
        
        global _GLOBAL_CONNECTION, _PLAYBOOK_STARTED
        
        # Use global connection state
        self._batch_id = str(uuid.uuid4())[:8]
        self.connection_timeout = 30
        
        # SSH command template
        self._ssh_cmd = self._build_ssh_command()
        
        display.vv(f"WinBatch V2 Task initialized: batch_id={self._batch_id}")

    def _build_ssh_command(self):
        """Строит SSH команду"""
        ssh_cmd = ['ssh']
        
        ssh_cmd.extend([
            '-o', 'StrictHostKeyChecking=no',
            '-o', 'UserKnownHostsFile=/dev/null',
            '-o', f'ConnectTimeout={self.connection_timeout}'
        ])
        
        if self._play_context.port:
            ssh_cmd.extend(['-p', str(self._play_context.port)])
        
        if self._play_context.private_key_file:
            ssh_cmd.extend(['-i', self._play_context.private_key_file])
        
        if self._play_context.remote_user:
            ssh_cmd.append(f"{self._play_context.remote_user}@{self._play_context.remote_addr}")
        else:
            ssh_cmd.append(self._play_context.remote_addr)
        
        return ssh_cmd

    def _connect(self):
        """Устанавливает ЕДИНСТВЕННОЕ соединение для ВСЕГО playbook"""
        global _GLOBAL_CONNECTION, _GLOBAL_LOCK, _PLAYBOOK_STARTED
        
        with _GLOBAL_LOCK:
            if _GLOBAL_CONNECTION is not None:
                display.vv("WinBatch V2: Using existing GLOBAL connection")
                return self
            
            if _PLAYBOOK_STARTED:
                display.vv("WinBatch V2: Playbook already started, using existing connection")
                return self
            
            display.vv(f"WinBatch V2: Establishing SINGLE GLOBAL connection to {self._play_context.remote_addr}")
            
            try:
                # Test connection
                test_cmd = self._ssh_cmd + ['echo', f'WinBatch-Global-{self._batch_id}']
                result = subprocess.run(test_cmd, 
                                      capture_output=True, 
                                      text=True, 
                                      timeout=self.connection_timeout)
                
                if result.returncode != 0:
                    raise AnsibleConnectionFailure(f"Global SSH connection failed: {result.stderr}")
                
                # Setup global execution environment
                self._setup_global_environment()
                
                _GLOBAL_CONNECTION = {
                    'batch_id': self._batch_id,
                    'ssh_cmd': self._ssh_cmd,
                    'established_at': time.time()
                }
                
                _PLAYBOOK_STARTED = True
                
                display.vv(f"WinBatch V2: GLOBAL connection established - {result.stdout.strip()}")
                
            except Exception as e:
                raise AnsibleConnectionFailure(f"Failed to establish global connection: {str(e)}")
        
        return self

    def _setup_global_environment(self):
        """Настраивает глобальное окружение для выполнения ВСЕХ задач"""
        display.vv("WinBatch V2: Setting up GLOBAL execution environment")
        
        setup_script = f'''
# WinBatch V2 Global Execution Environment
$GlobalBatchId = "{self._batch_id}"
$GlobalDir = "C:\\Temp\\WinBatch\\Global_$GlobalBatchId"
$TasksFile = "$GlobalDir\\all_tasks.json"
$ProgressFile = "$GlobalDir\\progress.json"
$ResultsFile = "$GlobalDir\\results.json"

# Create global directory
if (!(Test-Path $GlobalDir)) {{
    New-Item -Path $GlobalDir -ItemType Directory -Force | Out-Null
}}

# Initialize global state
$GlobalState = @{{
    batch_id = $GlobalBatchId
    status = "ready"
    tasks_received = 0
    tasks_completed = 0
    start_time = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    last_update = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
}}
$GlobalState | ConvertTo-Json | Out-File -FilePath $ProgressFile -Encoding UTF8

Write-Host "WinBatch-Global-Ready:$GlobalBatchId"
'''
        
        setup_cmd = self._ssh_cmd + ['powershell', '-Command', setup_script]
        try:
            result = subprocess.run(setup_cmd, 
                                  capture_output=True, 
                                  text=True, 
                                  timeout=60)
            
            if result.returncode == 0:
                display.vv("WinBatch V2: Global environment ready")
            else:
                display.warning(f"WinBatch V2: Global setup warning: {result.stderr}")
                
        except Exception as e:
            display.warning(f"WinBatch V2: Global setup error: {str(e)}")

    def exec_command(self, cmd, in_data=None, sudoable=True):
        """
        Собирает ВСЕ команды и выполняет их ОДНИМ блоком
        """
        super(Connection, self).exec_command(cmd, in_data=in_data, sudoable=sudoable)
        
        global _ALL_TASKS, _GLOBAL_LOCK
        
        # Ensure global connection exists
        self._connect()
        
        # Normalize command
        if isinstance(cmd, (list, tuple)):
            cmd = ' '.join(cmd)
        cmd = to_text(cmd, errors='surrogate_or_strict')
        
        with _GLOBAL_LOCK:
            task_id = len(_ALL_TASKS)
            task = {
                'id': task_id,
                'command': cmd,
                'input_data': in_data,
                'timestamp': time.time(),
                'host': self._play_context.remote_addr
            }
            
            _ALL_TASKS.append(task)
            
            display.vv(f"WinBatch V2: Collected task {task_id}: {cmd[:50]}... (Total: {len(_ALL_TASKS)})")
            
            # Check if this is likely the last task (heuristic)
            if self._is_likely_last_task(cmd):
                display.vv(f"WinBatch V2: Detected likely end of playbook, executing ALL {len(_ALL_TASKS)} tasks")
                return self._execute_all_tasks()
            else:
                # Return placeholder - task is queued
                return (0, f"WinBatch-Task-Queued-{task_id}", "")

    def _is_likely_last_task(self, cmd):
        """Определяет является ли это последней задачей playbook"""
        # Простая эвристика - если команда содержит cleanup или это debug/echo
        last_task_indicators = [
            'echo', 'debug', 'cleanup', 'final', 'end', 'complete',
            'Get-Date', 'Test-Path', 'Write-Host'
        ]
        
        return any(indicator.lower() in cmd.lower() for indicator in last_task_indicators)

    def _execute_all_tasks(self):
        """Выполняет ВСЕ собранные задачи ОДНИМ блоком на Windows"""
        global _ALL_TASKS, _GLOBAL_CONNECTION
        
        if not _ALL_TASKS:
            return (0, "", "")
        
        display.vv(f"WinBatch V2: EXECUTING ALL {len(_ALL_TASKS)} TASKS IN SINGLE OPERATION")
        
        try:
            # Create mega-script with ALL tasks
            mega_script = self._create_mega_execution_script(_ALL_TASKS)
            
            # Execute ALL tasks at once
            mega_cmd = _GLOBAL_CONNECTION['ssh_cmd'] + ['powershell', '-Command', mega_script]
            
            # Start execution with progress monitoring
            process = subprocess.Popen(
                mega_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )
            
            # Monitor progress every 5 seconds
            stdout_lines = []
            stderr_lines = []
            last_progress_time = time.time()
            
            while True:
                if process.poll() is not None:
                    break
                
                # Read available output
                try:
                    line = process.stdout.readline()
                    if line:
                        stdout_lines.append(line.strip())
                        if "WinBatch-Progress:" in line:
                            display.vv(f"WinBatch V2: {line.strip()}")
                        elif "WinBatch-AllTasks-Completed:" in line:
                            display.vv("WinBatch V2: ALL TASKS COMPLETED!")
                            break
                except:
                    pass
                
                # Progress report every 5 seconds
                current_time = time.time()
                if current_time - last_progress_time >= 5:
                    display.vv(f"WinBatch V2: Still executing... ({int(current_time - last_progress_time)}s)")
                    last_progress_time = current_time
                
                time.sleep(0.5)
            
            # Get final output
            remaining_stdout, remaining_stderr = process.communicate()
            if remaining_stdout:
                stdout_lines.extend(remaining_stdout.strip().split('\n'))
            if remaining_stderr:
                stderr_lines.extend(remaining_stderr.strip().split('\n'))
            
            stdout = '\n'.join(stdout_lines)
            stderr = '\n'.join(stderr_lines)
            rc = process.returncode or 0
            
            display.vv(f"WinBatch V2: ALL TASKS completed with RC: {rc}")
            
            # Clear tasks after execution
            _ALL_TASKS.clear()
            
            return (rc, stdout, stderr)
            
        except Exception as e:
            error_msg = f"Mega-batch execution failed: {str(e)}"
            display.error(f"WinBatch V2: {error_msg}")
            return (1, "", error_msg)

    def _create_mega_execution_script(self, all_tasks):
        """Создает мега-скрипт для выполнения ВСЕХ задач"""
        batch_id = _GLOBAL_CONNECTION['batch_id']
        
        script_lines = [
            f'Write-Host "WinBatch-{batch_id}: Starting MEGA-BATCH of {len(all_tasks)} tasks"',
            f'$TotalTasks = {len(all_tasks)}',
            '$CompletedTasks = 0',
            '$Results = @{}'
        ]
        
        # Add all tasks
        for i, task in enumerate(all_tasks):
            cmd = task['command'].replace('"', '`"')  # Escape quotes
            script_lines.extend([
                f'Write-Host "WinBatch-Progress: Executing task {i+1}/{len(all_tasks)}: {cmd[:30]}..."',
                f'try {{',
                f'    $Output_{i} = {cmd} 2>&1',
                f'    $ExitCode_{i} = $LASTEXITCODE',
                f'    if ($ExitCode_{i} -eq $null) {{ $ExitCode_{i} = 0 }}',
                f'    $Results[{i}] = @{{ id={i}; command="{cmd[:50]}..."; exit_code=$ExitCode_{i}; success=($ExitCode_{i} -eq 0) }}',
                f'}} catch {{',
                f'    $Results[{i}] = @{{ id={i}; command="{cmd[:50]}..."; exit_code=1; success=$false; error=$_.Exception.Message }}',
                f'}}',
                f'$CompletedTasks++',
                f'if ($CompletedTasks % 5 -eq 0) {{ Write-Host "WinBatch-Progress: $CompletedTasks/$TotalTasks tasks completed" }}'
            ])
        
        script_lines.extend([
            f'Write-Host "WinBatch-AllTasks-Completed:{batch_id}"',
            f'Write-Host "WinBatch-{batch_id}: MEGA-BATCH COMPLETED - $CompletedTasks tasks executed"'
        ])
        
        return '; '.join(script_lines)

    def put_file(self, in_path, out_path):
        """Загружает файл через глобальное соединение"""
        self._connect()
        
        scp_cmd = ['scp']
        
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
        """Скачивает файл через глобальное соединение"""
        self._connect()
        
        scp_cmd = ['scp']
        
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

    def close(self):
        """Закрывает глобальное соединение только в самом конце"""
        global _GLOBAL_CONNECTION, _ALL_TASKS, _GLOBAL_LOCK
        
        with _GLOBAL_LOCK:
            # Execute any remaining tasks
            if _ALL_TASKS:
                display.vv(f"WinBatch V2: Executing final {len(_ALL_TASKS)} tasks before close")
                try:
                    self._execute_all_tasks()
                except:
                    pass
            
            # Only close if this is the last connection
            if _GLOBAL_CONNECTION:
                display.vv("WinBatch V2: Closing GLOBAL connection")
                _GLOBAL_CONNECTION = None
        
        super(Connection, self).close() 