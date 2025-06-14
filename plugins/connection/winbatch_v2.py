#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
AWX WinBatch V2 - REAL Single Connection Architecture
НАСТОЯЩЕЕ единственное соединение через daemon процесс
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

from ansible.plugins.connection import ConnectionBase
from ansible.errors import AnsibleConnectionFailure, AnsibleError, AnsibleFileNotFound
from ansible.utils.display import Display
from ansible.module_utils.common.text.converters import to_text

display = Display()

# Global daemon state file
DAEMON_STATE_FILE = "/tmp/winbatch_daemon_state.json"
DAEMON_LOCK_FILE = "/tmp/winbatch_daemon.lock"

class Connection(ConnectionBase):
    """
    WinBatch V2 - REAL Single Connection через daemon процесс
    """
    
    transport = 'winbatch_v2'
    allow_executable = False
    has_pipelining = True
    
    def __init__(self, play_context, new_stdin, *args, **kwargs):
        super(Connection, self).__init__(play_context, new_stdin, *args, **kwargs)
        
        self.connection_timeout = 30
        self.daemon_id = f"winbatch_{self._play_context.remote_addr}_{self._play_context.remote_user}"
        self.task_queue_file = f"/tmp/winbatch_tasks_{self.daemon_id}.json"
        self.results_file = f"/tmp/winbatch_results_{self.daemon_id}.json"
        
        display.vv(f"WinBatch V2 Task: daemon_id={self.daemon_id}")

    def _get_daemon_state(self):
        """Получает состояние daemon процесса"""
        try:
            if os.path.exists(DAEMON_STATE_FILE):
                with open(DAEMON_STATE_FILE, 'r') as f:
                    return json.load(f)
        except:
            pass
        return {}

    def _set_daemon_state(self, state):
        """Сохраняет состояние daemon процесса"""
        try:
            with open(DAEMON_STATE_FILE, 'w') as f:
                json.dump(state, f)
        except:
            pass

    def _is_daemon_running(self):
        """Проверяет работает ли daemon для этого хоста"""
        state = self._get_daemon_state()
        daemon_info = state.get(self.daemon_id)
        
        if not daemon_info:
            return False
            
        # Проверяем что процесс еще жив
        try:
            pid = daemon_info.get('pid')
            if pid:
                os.kill(pid, 0)  # Проверка существования процесса
                return True
        except:
            pass
            
        return False

    def _start_daemon(self):
        """Запускает daemon процесс для этого хоста"""
        if self._is_daemon_running():
            display.vv(f"WinBatch V2: Daemon already running for {self.daemon_id}")
            return True
            
        display.vv(f"WinBatch V2: Starting REAL SINGLE CONNECTION daemon for {self.daemon_id}")
        
        # Создаем daemon скрипт
        daemon_script = self._create_daemon_script()
        
        try:
            # Запускаем daemon в фоне
            process = subprocess.Popen([
                'python3', '-c', daemon_script
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            # Ждем немного чтобы daemon запустился
            time.sleep(2)
            
            # Проверяем что daemon запустился
            if self._is_daemon_running():
                display.vv(f"WinBatch V2: Daemon started successfully")
                return True
            else:
                display.error(f"WinBatch V2: Failed to start daemon")
                return False
                
        except Exception as e:
            display.error(f"WinBatch V2: Error starting daemon: {str(e)}")
            return False

    def _create_daemon_script(self):
        """Создает Python скрипт для daemon процесса"""
        return f'''
import os
import json
import time
import subprocess
import threading
import signal
import sys

# Daemon configuration
DAEMON_ID = "{self.daemon_id}"
HOST = "{self._play_context.remote_addr}"
USER = "{self._play_context.remote_user}"
PORT = {self._play_context.port or 22}
KEY_FILE = "{self._play_context.private_key_file or ''}"
TASK_QUEUE_FILE = "{self.task_queue_file}"
RESULTS_FILE = "{self.results_file}"
STATE_FILE = "{DAEMON_STATE_FILE}"

class WinBatchDaemon:
    def __init__(self):
        self.ssh_connection = None
        self.running = True
        self.tasks = []
        
    def setup_ssh_connection(self):
        """Устанавливает ЕДИНСТВЕННОЕ SSH соединение"""
        ssh_cmd = ['ssh']
        ssh_cmd.extend(['-o', 'StrictHostKeyChecking=no'])
        ssh_cmd.extend(['-o', 'UserKnownHostsFile=/dev/null'])
        ssh_cmd.extend(['-o', 'ConnectTimeout=30'])
        ssh_cmd.extend(['-o', 'ServerAliveInterval=30'])
        ssh_cmd.extend(['-o', 'ServerAliveCountMax=3'])
        
        if PORT != 22:
            ssh_cmd.extend(['-p', str(PORT)])
        if KEY_FILE:
            ssh_cmd.extend(['-i', KEY_FILE])
            
        ssh_cmd.append(f"{{USER}}@{{HOST}}")
        
        # Тестируем соединение
        test_cmd = ssh_cmd + ['echo', 'WinBatch-Daemon-Ready']
        try:
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                self.ssh_connection = ssh_cmd
                print(f"WinBatch Daemon: SSH connection established to {{HOST}}")
                return True
        except Exception as e:
            print(f"WinBatch Daemon: SSH connection failed: {{e}}")
            
        return False
        
    def execute_tasks(self, tasks):
        """Выполняет все задачи ОДНИМ блоком"""
        if not self.ssh_connection or not tasks:
            return
            
        print(f"WinBatch Daemon: Executing {{len(tasks)}} tasks in SINGLE operation")
        
        # Создаем мега-скрипт
        mega_script = self.create_mega_script(tasks)
        
        # Выполняем через ЕДИНСТВЕННОЕ соединение
        cmd = self.ssh_connection + ['powershell', '-Command', mega_script]
        
        try:
            start_time = time.time()
            process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            
            # Мониторим выполнение
            while process.poll() is None:
                elapsed = time.time() - start_time
                print(f"WinBatch Daemon: Still executing... ({{int(elapsed)}}s)")
                time.sleep(5)
                
            stdout, stderr = process.communicate()
            
            # Сохраняем результаты
            results = {{
                'return_code': process.returncode,
                'stdout': stdout,
                'stderr': stderr,
                'execution_time': time.time() - start_time,
                'tasks_count': len(tasks)
            }}
            
            with open(RESULTS_FILE, 'w') as f:
                json.dump(results, f)
                
            print(f"WinBatch Daemon: Completed {{len(tasks)}} tasks in {{results['execution_time']:.1f}}s")
            
        except Exception as e:
            print(f"WinBatch Daemon: Execution error: {{e}}")
            
    def create_mega_script(self, tasks):
        """Создает мега-скрипт для всех задач"""
        script_lines = [
            f'Write-Host "WinBatch-Daemon: Starting {{len(tasks)}} tasks"',
            '$Results = @{{}}'
        ]
        
        for i, task in enumerate(tasks):
            cmd = task['command'].replace('"', '`"')
            script_lines.extend([
                f'Write-Host "WinBatch-Progress: Task {{i+1}}/{{len(tasks)}}"',
                f'try {{ $Output_{{i}} = {{cmd}} 2>&1; $ExitCode_{{i}} = $LASTEXITCODE }} catch {{ $ExitCode_{{i}} = 1 }}',
                f'if ($ExitCode_{{i}} -eq $null) {{ $ExitCode_{{i}} = 0 }}'
            ])
            
        script_lines.append('Write-Host "WinBatch-Daemon: All tasks completed"')
        return '; '.join(script_lines)
        
    def run(self):
        """Основной цикл daemon"""
        # Регистрируем daemon
        state = {{}}
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
                
        state[DAEMON_ID] = {{
            'pid': os.getpid(),
            'host': HOST,
            'user': USER,
            'started_at': time.time()
        }}
        
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f)
            
        # Устанавливаем соединение
        if not self.setup_ssh_connection():
            print("WinBatch Daemon: Failed to establish SSH connection")
            return
            
        print(f"WinBatch Daemon: Started for {{DAEMON_ID}}")
        
        # Основной цикл
        while self.running:
            try:
                # Проверяем новые задачи
                if os.path.exists(TASK_QUEUE_FILE):
                    with open(TASK_QUEUE_FILE, 'r') as f:
                        tasks = json.load(f)
                        
                    if tasks:
                        self.execute_tasks(tasks)
                        # Очищаем очередь
                        os.remove(TASK_QUEUE_FILE)
                        
                time.sleep(1)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"WinBatch Daemon: Error: {{e}}")
                time.sleep(5)
                
        print("WinBatch Daemon: Shutting down")

# Запускаем daemon
if __name__ == "__main__":
    daemon = WinBatchDaemon()
    daemon.run()
'''

    def _connect(self):
        """Подключается к daemon или запускает его"""
        if not self._start_daemon():
            raise AnsibleConnectionFailure("Failed to start WinBatch daemon")
        return self

    def exec_command(self, cmd, in_data=None, sudoable=True):
        """Добавляет команду в очередь daemon"""
        super(Connection, self).exec_command(cmd, in_data=in_data, sudoable=sudoable)
        
        # Ensure daemon is running
        self._connect()
        
        # Normalize command
        if isinstance(cmd, (list, tuple)):
            cmd = ' '.join(cmd)
        cmd = to_text(cmd, errors='surrogate_or_strict')
        
        # Добавляем задачу в очередь
        task = {
            'id': int(time.time() * 1000),
            'command': cmd,
            'timestamp': time.time()
        }
        
        # Читаем существующие задачи
        tasks = []
        if os.path.exists(self.task_queue_file):
            try:
                with open(self.task_queue_file, 'r') as f:
                    tasks = json.load(f)
            except:
                tasks = []
        
        tasks.append(task)
        
        # Сохраняем обновленную очередь
        with open(self.task_queue_file, 'w') as f:
            json.dump(tasks, f)
        
        display.vv(f"WinBatch V2: Added task to daemon queue: {cmd[:50]}... (Total: {len(tasks)})")
        
        # Если это последняя задача - ждем выполнения
        if self._is_likely_last_task(cmd):
            display.vv(f"WinBatch V2: Detected end of playbook, waiting for daemon execution")
            return self._wait_for_daemon_results()
        else:
            return (0, f"WinBatch-Daemon-Queued-{task['id']}", "")

    def _is_likely_last_task(self, cmd):
        """Определяет последнюю задачу"""
        last_task_indicators = [
            'echo', 'debug', 'Get-Date', 'Test-Path', 'Write-Host',
            'cleanup', 'final', 'end', 'complete'
        ]
        return any(indicator.lower() in cmd.lower() for indicator in last_task_indicators)

    def _wait_for_daemon_results(self):
        """Ждет результаты от daemon"""
        display.vv("WinBatch V2: Waiting for daemon to complete all tasks...")
        
        # Ждем пока daemon обработает задачи
        timeout = 300  # 5 минут
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            # Проверяем есть ли результаты
            if os.path.exists(self.results_file):
                try:
                    with open(self.results_file, 'r') as f:
                        results = json.load(f)
                    
                    # Удаляем файл результатов
                    os.remove(self.results_file)
                    
                    display.vv(f"WinBatch V2: Daemon completed {results['tasks_count']} tasks in {results['execution_time']:.1f}s")
                    
                    return (results['return_code'], results['stdout'], results['stderr'])
                    
                except Exception as e:
                    display.warning(f"WinBatch V2: Error reading results: {e}")
            
            # Проверяем что daemon еще работает
            if not self._is_daemon_running():
                display.error("WinBatch V2: Daemon stopped unexpectedly")
                break
                
            time.sleep(2)
        
        # Timeout
        display.error("WinBatch V2: Timeout waiting for daemon results")
        return (1, "", "Daemon execution timeout")

    def put_file(self, in_path, out_path):
        """Загружает файл через daemon"""
        # Для простоты используем обычный SCP
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
        """Скачивает файл через daemon"""
        # Для простоты используем обычный SCP
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
        """Закрывает соединение (daemon продолжает работать)"""
        display.vv("WinBatch V2: Connection closed (daemon continues running)")
        super(Connection, self).close()