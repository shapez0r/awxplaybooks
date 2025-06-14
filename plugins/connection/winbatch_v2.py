#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
AWX WinBatch V2 - TRUE BATCH MODE
1 SSH соединение + все задачи за раз + выполнение на Windows
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
import fcntl
import atexit

from ansible.plugins.connection import ConnectionBase
from ansible.errors import AnsibleConnectionFailure, AnsibleError, AnsibleFileNotFound
from ansible.utils.display import Display
from ansible.module_utils.common.text.converters import to_text

display = Display()

# Global state for TRUE BATCH MODE
_BATCH_STATE_FILE = "/tmp/winbatch_batch_state.json"
_BATCH_LOCK_FILE = "/tmp/winbatch_batch.lock"
_BATCH_LOCK = threading.Lock()

class Connection(ConnectionBase):
    """
    WinBatch V2 - TRUE BATCH MODE
    Собирает ВСЕ задачи, передает за 1 раз, 1 SSH соединение
    """
    
    transport = 'winbatch_v2'
    allow_executable = False
    has_pipelining = True
    
    def __init__(self, play_context, new_stdin, *args, **kwargs):
        super(Connection, self).__init__(play_context, new_stdin, *args, **kwargs)
        
        self.connection_timeout = 30
        self.batch_id = f"batch_{self._play_context.remote_addr}_{self._play_context.remote_user}_{int(time.time())}"
        self.host_key = f"{self._play_context.remote_user}@{self._play_context.remote_addr}:{self._play_context.port or 22}"
        
        display.vv(f"WinBatch V2 BATCH: Initialized {self.batch_id}")

    def _get_batch_state(self):
        """Получает состояние batch"""
        try:
            if os.path.exists(_BATCH_STATE_FILE):
                with open(_BATCH_STATE_FILE, 'r') as f:
                    return json.load(f)
        except:
            pass
        return {}

    def _save_batch_state(self, state):
        """Сохраняет состояние batch"""
        try:
            with open(_BATCH_STATE_FILE, 'w') as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            display.warning(f"WinBatch V2: Failed to save batch state: {e}")

    def _add_task_to_batch(self, command):
        """Добавляет задачу в глобальный batch"""
        with _BATCH_LOCK:
            state = self._get_batch_state()
            
            # Инициализируем batch для этого хоста если нужно
            if self.host_key not in state:
                state[self.host_key] = {
                    'tasks': [],
                    'batch_started': False,
                    'batch_completed': False,
                    'start_time': time.time(),
                    'connection_info': {
                        'host': self._play_context.remote_addr,
                        'user': self._play_context.remote_user,
                        'port': self._play_context.port or 22,
                        'key_file': self._play_context.private_key_file
                    }
                }
            
            # Добавляем задачу
            task = {
                'id': len(state[self.host_key]['tasks']) + 1,
                'command': command,
                'timestamp': time.time()
            }
            
            state[self.host_key]['tasks'].append(task)
            self._save_batch_state(state)
            
            display.vv(f"WinBatch V2 BATCH: Added task {task['id']}: {command[:50]}... (Total: {len(state[self.host_key]['tasks'])})")
            
            return len(state[self.host_key]['tasks'])

    def _is_batch_ready(self, command):
        """Определяет готов ли batch к выполнению"""
        # Эвристики для определения последней задачи
        last_task_patterns = [
            'echo', 'debug', 'Get-Date', 'Test-Path', 'Write-Host',
            'cleanup', 'final', 'end', 'complete', 'finish',
            'Get-Service', 'Get-Process', 'whoami', 'hostname'
        ]
        
        cmd_lower = command.lower()
        is_likely_last = any(pattern.lower() in cmd_lower for pattern in last_task_patterns)
        
        if is_likely_last:
            display.vv(f"WinBatch V2 BATCH: Detected likely last task: {command[:50]}...")
            return True
            
        # Также проверяем таймаут (если задачи не добавлялись 5 секунд)
        state = self._get_batch_state()
        if self.host_key in state and state[self.host_key]['tasks']:
            last_task_time = max(task['timestamp'] for task in state[self.host_key]['tasks'])
            if time.time() - last_task_time > 5:
                display.vv(f"WinBatch V2 BATCH: Timeout reached, executing batch")
                return True
        
        return False

    def _execute_batch(self):
        """Выполняет весь batch за ОДНО SSH соединение"""
        with _BATCH_LOCK:
            state = self._get_batch_state()
            
            if self.host_key not in state or state[self.host_key]['batch_completed']:
                return (0, "Batch already completed", "")
            
            if state[self.host_key]['batch_started']:
                # Ждем завершения другого процесса
                return self._wait_for_batch_completion()
            
            # Помечаем что начали выполнение
            state[self.host_key]['batch_started'] = True
            self._save_batch_state(state)
            
            tasks = state[self.host_key]['tasks']
            conn_info = state[self.host_key]['connection_info']
            
            display.vv(f"WinBatch V2 BATCH: Executing {len(tasks)} tasks in SINGLE SSH connection")
            
            try:
                # Создаем МЕГА PowerShell скрипт
                mega_script = self._create_mega_script(tasks)
                
                # Выполняем через ЕДИНСТВЕННОЕ SSH соединение
                result = self._execute_single_ssh_batch(conn_info, mega_script)
                
                # Помечаем как завершенный
                state[self.host_key]['batch_completed'] = True
                state[self.host_key]['result'] = result
                state[self.host_key]['end_time'] = time.time()
                self._save_batch_state(state)
                
                execution_time = state[self.host_key]['end_time'] - state[self.host_key]['start_time']
                display.vv(f"WinBatch V2 BATCH: Completed {len(tasks)} tasks in {execution_time:.1f}s")
                
                return result
                
            except Exception as e:
                display.error(f"WinBatch V2 BATCH: Execution failed: {str(e)}")
                state[self.host_key]['batch_completed'] = True
                state[self.host_key]['result'] = (1, "", str(e))
                self._save_batch_state(state)
                return (1, "", str(e))

    def _create_mega_script(self, tasks):
        """Создает МЕГА PowerShell скрипт для всех задач"""
        script_lines = [
            'Write-Host "WinBatch-MEGA-BATCH-START"',
            f'Write-Host "Total tasks: {len(tasks)}"',
            '$Global:BatchResults = @{}'
        ]
        
        for i, task in enumerate(tasks):
            cmd = task['command']
            
            # Конвертируем bash команды в PowerShell
            if cmd.startswith('echo '):
                echo_text = cmd[5:].strip().strip('"').strip("'")
                ps_cmd = f'Write-Host "{echo_text}"'
            elif cmd.startswith('whoami'):
                ps_cmd = '$env:USERNAME'
            elif cmd.startswith('hostname'):
                ps_cmd = '$env:COMPUTERNAME'
            elif cmd.startswith('pwd'):
                ps_cmd = 'Get-Location'
            else:
                ps_cmd = cmd.replace('"', '`"').replace('$', '`$')
            
            script_lines.extend([
                f'Write-Host "=== TASK {i+1}/{len(tasks)}: {cmd[:30]}... ==="',
                f'$TaskStart_{i} = Get-Date',
                f'$ExitCode_{i} = 0; $Output_{i} = ""',
                f'try {{',
                f'    $Output_{i} = {ps_cmd}',
                f'    if ($Output_{i}) {{ Write-Host $Output_{i} }}',
                f'}} catch {{',
                f'    $Output_{i} = $_.Exception.Message',
                f'    $ExitCode_{i} = 1',
                f'    Write-Host "ERROR: $Output_{i}"',
                f'}}',
                f'$TaskEnd_{i} = Get-Date',
                f'$TaskTime_{i} = ($TaskEnd_{i} - $TaskStart_{i}).TotalSeconds',
                f'Write-Host "Task {i+1} completed: RC=$ExitCode_{i}, Time=$TaskTime_{i}s"',
                f'$Global:BatchResults[{i}] = @{{ ExitCode=$ExitCode_{i}; Output=$Output_{i}; Time=$TaskTime_{i} }}',
                ''
            ])
        
        script_lines.extend([
            'Write-Host "WinBatch-MEGA-BATCH-COMPLETE"',
            f'Write-Host "All {len(tasks)} tasks completed"'
        ])
        
        return '; '.join(script_lines)

    def _execute_single_ssh_batch(self, conn_info, mega_script):
        """Выполняет мега-скрипт через ЕДИНСТВЕННОЕ SSH соединение"""
        ssh_cmd = ['ssh']
        ssh_cmd.extend(['-o', 'StrictHostKeyChecking=no'])
        ssh_cmd.extend(['-o', 'UserKnownHostsFile=/dev/null'])
        ssh_cmd.extend(['-o', 'ConnectTimeout=30'])
        ssh_cmd.extend(['-o', 'ServerAliveInterval=30'])
        ssh_cmd.extend(['-o', 'ServerAliveCountMax=3'])
        
        if conn_info['port'] != 22:
            ssh_cmd.extend(['-p', str(conn_info['port'])])
        if conn_info['key_file']:
            ssh_cmd.extend(['-i', conn_info['key_file']])
            
        ssh_cmd.append(f"{conn_info['user']}@{conn_info['host']}")
        # Экранируем кавычки для PowerShell
        escaped_script = mega_script.replace('"', '\\"')
        ssh_cmd.append(f'powershell -Command "{escaped_script}"')
        
        display.vv(f"WinBatch V2 BATCH: Executing MEGA script via single SSH connection")
        
        start_time = time.time()
        
        try:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=300)
            execution_time = time.time() - start_time
            
            display.vv(f"WinBatch V2 BATCH: SSH execution completed in {execution_time:.1f}s")
            
            # Фильтруем SSH warnings из stderr
            filtered_stderr = self._filter_ssh_warnings(result.stderr)
            
            return (result.returncode, result.stdout, filtered_stderr)
            
        except subprocess.TimeoutExpired:
            return (1, "", "Batch execution timeout (300s)")
        except Exception as e:
            return (1, "", f"SSH execution failed: {str(e)}")

    def _filter_ssh_warnings(self, stderr):
        """Фильтрует SSH warnings из stderr"""
        if not stderr:
            return stderr
            
        # SSH warnings которые нужно убрать
        ssh_warnings = [
            "Warning: Permanently added",
            "Warning: Identity file",
            "Warning: the ECDSA host key",
            "Warning: the RSA host key",
            "Warning: the ED25519 host key",
            "Pseudo-terminal will not be allocated",
            "stdin: is not a tty"
        ]
        
        filtered_lines = []
        for line in stderr.split('\n'):
            is_warning = any(warning in line for warning in ssh_warnings)
            if not is_warning and line.strip():
                filtered_lines.append(line)
        
        filtered_stderr = '\n'.join(filtered_lines)
        
        if filtered_stderr != stderr:
            display.vv(f"WinBatch V2 BATCH: Filtered SSH warnings from stderr")
            
        return filtered_stderr

    def _wait_for_batch_completion(self):
        """Ждет завершения batch другим процессом"""
        display.vv("WinBatch V2 BATCH: Waiting for batch completion by another process...")
        
        timeout = 300  # 5 минут
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            state = self._get_batch_state()
            
            if (self.host_key in state and 
                state[self.host_key].get('batch_completed', False) and
                'result' in state[self.host_key]):
                
                result = state[self.host_key]['result']
                display.vv("WinBatch V2 BATCH: Batch completed by another process")
                return tuple(result)
            
            time.sleep(1)
        
        return (1, "", "Timeout waiting for batch completion")

    def _connect(self):
        """Подключение не нужно - все делается в batch"""
        return self

    def exec_command(self, cmd, in_data=None, sudoable=True):
        """Добавляет команду в batch или выполняет batch"""
        super(Connection, self).exec_command(cmd, in_data=in_data, sudoable=sudoable)
        
        # Нормализуем команду
        if isinstance(cmd, (list, tuple)):
            cmd = ' '.join(cmd)
        cmd = to_text(cmd, errors='surrogate_or_strict')
        
        # Добавляем в batch
        task_count = self._add_task_to_batch(cmd)
        
        # Проверяем готов ли batch к выполнению
        if self._is_batch_ready(cmd):
            display.vv(f"WinBatch V2 BATCH: Executing batch with {task_count} tasks")
            return self._execute_batch()
        else:
            # Возвращаем успех для промежуточных задач
            return (0, f"WinBatch-Queued-Task-{task_count}", "")

    def put_file(self, in_path, out_path):
        """Загружает файл через SCP"""
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
        """Скачивает файл через SCP"""
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
        """Закрывает соединение"""
        display.vv(f"WinBatch V2 BATCH: Connection closed for {self.host_key}")
        super(Connection, self).close()

# Cleanup function
def cleanup_batch_state():
    """Очищает состояние batch при выходе"""
    try:
        if os.path.exists(_BATCH_STATE_FILE):
            os.remove(_BATCH_STATE_FILE)
        if os.path.exists(_BATCH_LOCK_FILE):
            os.remove(_BATCH_LOCK_FILE)
    except:
        pass

# Register cleanup
atexit.register(cleanup_batch_state)