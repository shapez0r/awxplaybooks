#!/usr/bin/python
# -*- coding: utf-8 -*-

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import os
import json
import time
import threading
import queue
import subprocess
import base64
import re
from ansible.plugins.connection import ConnectionBase
from ansible.module_utils._text import to_bytes, to_text
from ansible.errors import AnsibleConnectionFailure
from ansible.utils.display import Display

display = Display()

# Разделитель для вывода между командами
WINBATCH_V3_MARKER = '---WINBATCH_V3_COMMAND_DONE---'

class Connection(ConnectionBase):
    """WinBatch V3 connection plugin for persistent SSH connection."""

    transport = 'winbatch_v3'
    has_pipelining = True
    become_methods = []
    allow_executable = False

    def _get_var(self, name, default):
        # 1. Из play_context (vars/inventory)
        val = getattr(self._play_context, name, None)
        if val is not None:
            try:
                return int(val)
            except Exception:
                return default
        return default

    def __init__(self, play_context, new_stdin, *args, **kwargs):
        super(Connection, self).__init__(play_context, new_stdin, *args, **kwargs)
        self._connected = False
        self._ssh_process = None
        self._command_queue = queue.Queue()
        self._result_queue = queue.Queue()
        self._status_thread = None
        self._last_status_time = time.time()
        self._status_interval = 10  # seconds
        self._playbook_start_time = time.time()
        
        # Получаем таймауты из vars/play_context
        self._playbook_timeout = self._get_var('playbook_timeout', 45)
        self._command_timeout = self._get_var('command_timeout', 30)
        self._queue_timeout = self._get_var('queue_timeout', 1)
        self._ps_prompt_regex = re.compile(r'PS [A-Z]:\\.*>')

    def _connect(self):
        """Establish SSH connection and start command processing thread."""
        if self._connected:
            return

        try:
            # Формируем команду SSH
            ssh_cmd = [
                'ssh',
                '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                '-o', 'ControlMaster=auto',
                '-o', 'ControlPersist=60s',
                f'{self._play_context.remote_user}@{self._play_context.remote_addr}',
                'powershell -NoProfile -NonInteractive'
            ]

            # Запускаем SSH процесс
            self._ssh_process = subprocess.Popen(
                ssh_cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0
            )

            # Запускаем поток обработки команд
            self._command_thread = threading.Thread(target=self._process_commands)
            self._command_thread.daemon = True
            self._command_thread.start()

            # Запускаем поток отправки статуса
            self._status_thread = threading.Thread(target=self._send_status)
            self._status_thread.daemon = True
            self._status_thread.start()

            self._connected = True
            display.vvv("SSH connection established successfully", host=self._play_context.remote_addr)

        except Exception as e:
            raise AnsibleConnectionFailure(f"Failed to establish SSH connection: {str(e)}")

    def _check_timeout(self):
        """Check if we've exceeded the playbook timeout."""
        if time.time() - self._playbook_start_time > self._playbook_timeout:
            raise AnsibleConnectionFailure(f"Playbook execution timeout exceeded ({self._playbook_timeout} seconds)")

    def _process_commands(self):
        """Process commands from the queue and execute them on the remote host."""
        while True:
            try:
                self._check_timeout()
                command = self._command_queue.get(timeout=self._queue_timeout)
                if command is None:
                    break
                display.vvv(f"[winbatch_v3] EXEC: {command}", host=self._play_context.remote_addr)
                
                # Формируем команду как одну строку для REPL
                # Заменяем переводы строк на точку с запятой для многострочных команд
                single_line_command = command.replace('\n', '; ').replace('\r', '')
                wrapped_command = f'$exitCode = 0; try {{ {single_line_command} }} catch {{ Write-Error $_.Exception.Message; $exitCode = 1 }}; Write-Output "{WINBATCH_V3_MARKER}:$exitCode"'
                
                # Отправляем команду на удалённый хост
                self._ssh_process.stdin.write(to_bytes(wrapped_command + "\n"))
                self._ssh_process.stdin.flush()
                
                stdout = []
                stderr = []
                exit_code = 0
                
                # Читаем stdout до маркера
                while True:
                    self._check_timeout()
                    line = self._ssh_process.stdout.readline()
                    if not line:
                        display.vvv(f"[winbatch_v3] STDOUT EOF", host=self._play_context.remote_addr)
                        break
                    line_text = to_text(line.strip())
                    display.vvv(f"[winbatch_v3] STDOUT: {line_text}", host=self._play_context.remote_addr)
                    
                    # Пропускаем приглашения PS и эхо-команды
                    if (line_text.startswith('PS ') or 
                        line_text.startswith('>>') or 
                        line_text == '' or
                        'try {' in line_text or
                        'catch {' in line_text or
                        'Write-Output' in line_text):
                        continue
                        
                    if line_text.startswith(WINBATCH_V3_MARKER):
                        try:
                            exit_code = int(line_text.split(":")[1])
                        except Exception:
                            exit_code = 1
                        break
                    stdout.append(line_text)
                
                # Читаем stderr (только если есть данные, не блокируем)
                # Не читаем stderr блокирующим способом, чтобы не задерживать результат
                
                display.vvv(f"[winbatch_v3] RESULT: rc={exit_code}, stdout={stdout}, stderr={stderr}", host=self._play_context.remote_addr)
                self._result_queue.put({
                    'command': command,
                    'stdout': stdout,
                    'stderr': stderr,
                    'rc': exit_code
                })
            except queue.Empty:
                continue
            except Exception as e:
                display.vvv(f"[winbatch_v3] EXCEPTION: {str(e)}", host=self._play_context.remote_addr)
                self._result_queue.put({
                    'command': command,
                    'stdout': [],
                    'stderr': [str(e)],
                    'rc': 1
                })

    def _send_status(self):
        """Send status updates back to AWX every status_interval seconds."""
        while True:
            try:
                time.sleep(1)  # Check more frequently
                if time.time() - self._last_status_time >= self._status_interval:
                    self._check_timeout()
                    status = {
                        'timestamp': time.time(),
                        'queue_size': self._command_queue.qsize(),
                        'connection_active': self._connected,
                        'elapsed_time': time.time() - self._playbook_start_time,
                        'timeout': self._playbook_timeout
                    }
                    display.vvv(f"Status update: {json.dumps(status)}", host=self._play_context.remote_addr)
                    self._last_status_time = time.time()
            except Exception as e:
                display.vvv(f"Failed to send status update: {str(e)}", host=self._play_context.remote_addr)

    def exec_command(self, cmd, in_data=None, sudoable=True):
        """Execute a command on the remote host."""
        if not self._connected:
            self._connect()
        try:
            self._check_timeout()
            display.vvv(f"[winbatch_v3] exec_command: {cmd}", host=self._play_context.remote_addr)
            self._command_queue.put(cmd)
            display.vvv(f"[winbatch_v3] waiting for result with timeout {self._command_timeout}", host=self._play_context.remote_addr)
            result = self._result_queue.get(timeout=self._command_timeout)
            display.vvv(f"[winbatch_v3] got result: {result}", host=self._play_context.remote_addr)
            stdout = '\n'.join(result.get('stdout', []))
            stderr = '\n'.join(result.get('stderr', []))
            display.vvv(f"[winbatch_v3] exec_command result: rc={result['rc']}, stdout={stdout}, stderr={stderr}", host=self._play_context.remote_addr)
            return result['rc'], stdout, stderr
        except queue.Empty as e:
            display.vvv(f"[winbatch_v3] exec_command timeout: {str(e)}", host=self._play_context.remote_addr)
            raise AnsibleConnectionFailure(f"Command execution timeout after {self._command_timeout} seconds: {str(e)}")
        except Exception as e:
            display.vvv(f"[winbatch_v3] exec_command exception: {str(e)}", host=self._play_context.remote_addr)
            import traceback
            display.vvv(f"[winbatch_v3] exec_command traceback: {traceback.format_exc()}", host=self._play_context.remote_addr)
            raise AnsibleConnectionFailure(f"Failed to execute command: {str(e)}")

    def put_file(self, in_path, out_path):
        """Transfer a file from local to remote."""
        if not self._connected:
            self._connect()

        try:
            self._check_timeout()
            with open(in_path, 'rb') as f:
                content = f.read()

            # Создаем PowerShell команду для записи файла
            ps_cmd = f"""
            $content = [System.Convert]::FromBase64String('{base64.b64encode(content).decode()}')
            [System.IO.File]::WriteAllBytes('{out_path}', $content)
            """
            
            self._command_queue.put(ps_cmd)
            result = self._result_queue.get(timeout=self._command_timeout)
            
            if result['rc'] != 0:
                raise AnsibleConnectionFailure(f"Failed to transfer file: {result.get('stderr', [''])[0]}")

        except Exception as e:
            raise AnsibleConnectionFailure(f"Failed to transfer file: {str(e)}")

    def fetch_file(self, in_path, out_path):
        """Transfer a file from remote to local."""
        if not self._connected:
            self._connect()

        try:
            self._check_timeout()
            # Создаем PowerShell команду для чтения файла
            ps_cmd = f"""
            $content = [System.IO.File]::ReadAllBytes('{in_path}')
            [System.Convert]::ToBase64String($content)
            """
            
            self._command_queue.put(ps_cmd)
            result = self._result_queue.get(timeout=self._command_timeout)
            
            if result['rc'] != 0:
                raise AnsibleConnectionFailure(f"Failed to fetch file: {result.get('stderr', [''])[0]}")

            # Записываем содержимое в локальный файл
            with open(out_path, 'wb') as f:
                f.write(base64.b64decode(result['stdout'][0]))

        except Exception as e:
            raise AnsibleConnectionFailure(f"Failed to fetch file: {str(e)}")

    def close(self):
        """Close the connection."""
        if self._connected:
            try:
                # Отправляем сигнал завершения в поток обработки команд
                self._command_queue.put(None)
                
                # Закрываем SSH процесс
                if self._ssh_process:
                    self._ssh_process.terminate()
                    self._ssh_process.wait()
                
                self._connected = False
                display.vvv("SSH connection closed", host=self._play_context.remote_addr)
                
            except Exception as e:
                display.vvv(f"Error while closing connection: {str(e)}", host=self._play_context.remote_addr) 