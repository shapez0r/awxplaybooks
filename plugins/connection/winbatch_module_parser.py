#!/usr/bin/python
# -*- coding: utf-8 -*-

"""
WinBatch Module Parser - Продвинутый парсер модулей Ansible для WinBatch

Этот модуль отвечает за правильное парсирование различных типов Ansible задач
и их преобразование в формат, пригодный для пакетного выполнения на Windows.
"""

import json
import re
import yaml
from typing import Dict, Any, List, Optional

class WinBatchModuleParser:
    """Парсер модулей Ansible для WinBatch плагина"""
    
    def __init__(self):
        self.supported_modules = {
            'win_shell': self._parse_win_shell,
            'win_command': self._parse_win_command,
            'win_copy': self._parse_win_copy,
            'win_file': self._parse_win_file,
            'win_template': self._parse_win_template,
            'win_service': self._parse_win_service,
            'win_feature': self._parse_win_feature,
            'win_package': self._parse_win_package,
            'win_registry': self._parse_win_registry,
            'win_user': self._parse_win_user,
            'win_group': self._parse_win_group,
            'shell': self._parse_shell,
            'command': self._parse_command,
            'copy': self._parse_copy,
            'file': self._parse_file,
            'setup': self._parse_setup,
            'debug': self._parse_debug,
        }
    
    def parse_task(self, task_data: Dict[str, Any], task_id: str) -> Dict[str, Any]:
        """
        Парсит задачу Ansible и преобразует её в формат для WinBatch
        
        Args:
            task_data: Данные задачи из Ansible
            task_id: Уникальный идентификатор задачи
            
        Returns:
            Словарь с данными задачи для WinBatch
        """
        
        # Определяем модуль
        module_name = self._detect_module(task_data)
        
        # Базовая структура задачи
        parsed_task = {
            'task_id': task_id,
            'name': task_data.get('name', f'Task {task_id}'),
            'module': module_name,
            'params': {},
            'command': '',
            'conditions': {
                'when': task_data.get('when'),
                'changed_when': task_data.get('changed_when'),
                'failed_when': task_data.get('failed_when'),
            },
            'loops': {
                'with_items': task_data.get('with_items'),
                'loop': task_data.get('loop'),
            },
            'vars': task_data.get('vars', {}),
            'tags': task_data.get('tags', []),
            'ignore_errors': task_data.get('ignore_errors', False),
            'register': task_data.get('register'),
        }
        
        # Парсим специфичные параметры модуля
        if module_name in self.supported_modules:
            module_parser = self.supported_modules[module_name]
            module_data = module_parser(task_data)
            parsed_task.update(module_data)
        else:
            # Для неизвестных модулей используем общий парсер
            parsed_task.update(self._parse_generic_module(task_data, module_name))
        
        return parsed_task
    
    def _detect_module(self, task_data: Dict[str, Any]) -> str:
        """Определяет используемый модуль в задаче"""
        
        # Ищем прямое указание модуля
        for key in task_data:
            if key in self.supported_modules:
                return key
        
        # Ищем в action
        if 'action' in task_data:
            action = task_data['action']
            if isinstance(action, str):
                return action.split()[0]
            elif isinstance(action, dict) and 'module' in action:
                return action['module']
        
        # По умолчанию используем shell
        return 'shell'
    
    def _parse_win_shell(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит модуль win_shell"""
        shell_cmd = task_data.get('win_shell', '')
        
        return {
            'command': shell_cmd,
            'params': {
                'chdir': task_data.get('args', {}).get('chdir'),
                'creates': task_data.get('args', {}).get('creates'),
                'removes': task_data.get('args', {}).get('removes'),
                'executable': task_data.get('args', {}).get('executable', 'powershell'),
            },
            'powershell_script': self._generate_powershell_wrapper(shell_cmd, task_data)
        }
    
    def _parse_win_command(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит модуль win_command"""
        cmd = task_data.get('win_command', '')
        
        return {
            'command': cmd,
            'params': {
                'chdir': task_data.get('args', {}).get('chdir'),
                'creates': task_data.get('args', {}).get('creates'),
                'removes': task_data.get('args', {}).get('removes'),
            },
            'powershell_script': self._generate_command_wrapper(cmd, task_data)
        }
    
    def _parse_win_copy(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит модуль win_copy"""
        copy_params = task_data.get('win_copy', {})
        
        if isinstance(copy_params, str):
            # Простая форма записи
            copy_params = {'dest': copy_params}
        
        return {
            'command': f"Copy file to {copy_params.get('dest', '')}",
            'params': {
                'src': copy_params.get('src'),
                'dest': copy_params.get('dest'),
                'content': copy_params.get('content'),
                'backup': copy_params.get('backup', False),
                'force': copy_params.get('force', True),
            },
            'powershell_script': self._generate_copy_script(copy_params)
        }
    
    def _parse_win_file(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит модуль win_file"""
        file_params = task_data.get('win_file', {})
        
        return {
            'command': f"Manage file/directory {file_params.get('path', '')}",
            'params': {
                'path': file_params.get('path'),
                'state': file_params.get('state', 'file'),
            },
            'powershell_script': self._generate_file_script(file_params)
        }
    
    def _parse_win_service(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит модуль win_service"""
        service_params = task_data.get('win_service', {})
        
        return {
            'command': f"Manage service {service_params.get('name', '')}",
            'params': service_params,
            'powershell_script': self._generate_service_script(service_params)
        }
    
    def _parse_win_feature(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит модуль win_feature"""
        feature_params = task_data.get('win_feature', {})
        
        return {
            'command': f"Manage Windows feature {feature_params.get('name', '')}",
            'params': feature_params,
            'powershell_script': self._generate_feature_script(feature_params)
        }
    
    def _parse_generic_module(self, task_data: Dict[str, Any], module_name: str) -> Dict[str, Any]:
        """Общий парсер для неизвестных модулей"""
        module_params = task_data.get(module_name, {})
        
        return {
            'command': f"Execute {module_name} module",
            'params': module_params,
            'powershell_script': f'# Generic module execution: {module_name}\nWrite-Host "Executing {module_name} with params: {json.dumps(module_params)}"'
        }
    
    def _generate_powershell_wrapper(self, cmd: str, task_data: Dict[str, Any]) -> str:
        """Генерирует PowerShell обертку для команды"""
        
        wrapper = []
        
        # Проверяем условия выполнения
        args = task_data.get('args', {})
        
        if args.get('chdir'):
            wrapper.append(f'Set-Location "{args["chdir"]}"')
        
        if args.get('creates'):
            wrapper.append(f'if (Test-Path "{args["creates"]}") {{ Write-Host "File already exists, skipping"; exit 0 }}')
        
        if args.get('removes'):
            wrapper.append(f'if (!(Test-Path "{args["removes"]}")) {{ Write-Host "File does not exist, skipping"; exit 0 }}')
        
        # Основная команда
        wrapper.append(f'try {{')
        wrapper.append(f'    $result = {cmd}')
        wrapper.append(f'    $result | Out-String')
        wrapper.append(f'    exit $LASTEXITCODE')
        wrapper.append(f'}} catch {{')
        wrapper.append(f'    Write-Error $_.Exception.Message')
        wrapper.append(f'    exit 1')
        wrapper.append(f'}}')
        
        return '\n'.join(wrapper)
    
    def _generate_command_wrapper(self, cmd: str, task_data: Dict[str, Any]) -> str:
        """Генерирует обертку для команды cmd"""
        
        wrapper = []
        args = task_data.get('args', {})
        
        if args.get('chdir'):
            wrapper.append(f'Set-Location "{args["chdir"]}"')
        
        if args.get('creates'):
            wrapper.append(f'if (Test-Path "{args["creates"]}") {{ Write-Host "File already exists, skipping"; exit 0 }}')
        
        if args.get('removes'):
            wrapper.append(f'if (!(Test-Path "{args["removes"]}")) {{ Write-Host "File does not exist, skipping"; exit 0 }}')
        
        # Выполняем команду через cmd
        wrapper.append(f'try {{')
        wrapper.append(f'    $result = cmd /c "{cmd}"')
        wrapper.append(f'    $result | Out-String')
        wrapper.append(f'    exit $LASTEXITCODE')
        wrapper.append(f'}} catch {{')
        wrapper.append(f'    Write-Error $_.Exception.Message')
        wrapper.append(f'    exit 1')
        wrapper.append(f'}}')
        
        return '\n'.join(wrapper)
    
    def _generate_copy_script(self, params: Dict[str, Any]) -> str:
        """Генерирует скрипт для копирования файлов"""
        
        script = []
        
        if params.get('content'):
            # Копируем контент в файл
            content = params['content'].replace('"', '`"')
            script.append(f'$content = @"')
            script.append(f'{params["content"]}')
            script.append(f'"@')
            script.append(f'$content | Set-Content -Path "{params["dest"]}" -Encoding UTF8')
        elif params.get('src'):
            # Копируем файл
            script.append(f'Copy-Item -Path "{params["src"]}" -Destination "{params["dest"]}" -Force:{params.get("force", True)}')
        
        script.append(f'Write-Host "File successfully copied to {params["dest"]}"')
        
        return '\n'.join(script)
    
    def _generate_file_script(self, params: Dict[str, Any]) -> str:
        """Генерирует скрипт для управления файлами/директориями"""
        
        script = []
        path = params.get('path', '')
        state = params.get('state', 'file')
        
        if state == 'directory':
            script.append(f'if (!(Test-Path "{path}")) {{')
            script.append(f'    New-Item -ItemType Directory -Path "{path}" -Force | Out-Null')
            script.append(f'    Write-Host "Directory created: {path}"')
            script.append(f'}} else {{')
            script.append(f'    Write-Host "Directory already exists: {path}"')
            script.append(f'}}')
        elif state == 'absent':
            script.append(f'if (Test-Path "{path}") {{')
            script.append(f'    Remove-Item -Path "{path}" -Recurse -Force')
            script.append(f'    Write-Host "Removed: {path}"')
            script.append(f'}} else {{')
            script.append(f'    Write-Host "Path does not exist: {path}"')
            script.append(f'}}')
        elif state == 'file':
            script.append(f'if (!(Test-Path "{path}")) {{')
            script.append(f'    New-Item -ItemType File -Path "{path}" -Force | Out-Null')
            script.append(f'    Write-Host "File created: {path}"')
            script.append(f'}} else {{')
            script.append(f'    Write-Host "File already exists: {path}"')
            script.append(f'}}')
        
        return '\n'.join(script)
    
    def _generate_service_script(self, params: Dict[str, Any]) -> str:
        """Генерирует скрипт для управления службами"""
        
        script = []
        name = params.get('name', '')
        state = params.get('state', 'started')
        
        script.append(f'$service = Get-Service -Name "{name}" -ErrorAction SilentlyContinue')
        script.append(f'if ($service) {{')
        
        if state == 'started':
            script.append(f'    if ($service.Status -ne "Running") {{')
            script.append(f'        Start-Service -Name "{name}"')
            script.append(f'        Write-Host "Service {name} started"')
            script.append(f'    }} else {{')
            script.append(f'        Write-Host "Service {name} already running"')
            script.append(f'    }}')
        elif state == 'stopped':
            script.append(f'    if ($service.Status -eq "Running") {{')
            script.append(f'        Stop-Service -Name "{name}"')
            script.append(f'        Write-Host "Service {name} stopped"')
            script.append(f'    }} else {{')
            script.append(f'        Write-Host "Service {name} already stopped"')
            script.append(f'    }}')
        
        script.append(f'}} else {{')
        script.append(f'    Write-Error "Service {name} not found"')
        script.append(f'    exit 1')
        script.append(f'}}')
        
        return '\n'.join(script)
    
    def _generate_feature_script(self, params: Dict[str, Any]) -> str:
        """Генерирует скрипт для управления Windows Features"""
        
        script = []
        name = params.get('name', '')
        state = params.get('state', 'present')
        
        if state == 'present':
            script.append(f'$feature = Get-WindowsFeature -Name "{name}"')
            script.append(f'if ($feature.InstallState -ne "Installed") {{')
            script.append(f'    Install-WindowsFeature -Name "{name}"')
            script.append(f'    Write-Host "Feature {name} installed"')
            script.append(f'}} else {{')
            script.append(f'    Write-Host "Feature {name} already installed"')
            script.append(f'}}')
        elif state == 'absent':
            script.append(f'$feature = Get-WindowsFeature -Name "{name}"')
            script.append(f'if ($feature.InstallState -eq "Installed") {{')
            script.append(f'    Uninstall-WindowsFeature -Name "{name}"')
            script.append(f'    Write-Host "Feature {name} uninstalled"')
            script.append(f'}} else {{')
            script.append(f'    Write-Host "Feature {name} not installed"')
            script.append(f'}}')
        
        return '\n'.join(script)
    
    # Методы для парсинга стандартных модулей (не win_*)
    def _parse_shell(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит стандартный модуль shell"""
        return self._parse_win_shell({'win_shell': task_data.get('shell', '')})
    
    def _parse_command(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит стандартный модуль command"""
        return self._parse_win_command({'win_command': task_data.get('command', '')})
    
    def _parse_copy(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит стандартный модуль copy"""
        return self._parse_win_copy({'win_copy': task_data.get('copy', {})})
    
    def _parse_file(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит стандартный модуль file"""
        return self._parse_win_file({'win_file': task_data.get('file', {})})
    
    def _parse_setup(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит модуль setup (gather_facts)"""
        return {
            'command': 'Gather system facts',
            'params': {},
            'powershell_script': '''
# Gather Windows system facts
$facts = @{}
$facts.ansible_os_family = "Windows"
$facts.ansible_system = (Get-WmiObject Win32_OperatingSystem).Caption
$facts.ansible_hostname = $env:COMPUTERNAME
$facts.ansible_fqdn = [System.Net.Dns]::GetHostByName($env:COMPUTERNAME).HostName
$facts.ansible_ip_addresses = (Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.InterfaceAlias -notlike "*Loopback*"}).IPAddress

$facts | ConvertTo-Json -Depth 3 | Write-Host
'''
        }
    
    def _parse_debug(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Парсит модуль debug"""
        debug_params = task_data.get('debug', {})
        msg = debug_params.get('msg', debug_params.get('var', 'Debug message'))
        
        return {
            'command': f'Debug: {msg}',
            'params': debug_params,
            'powershell_script': f'Write-Host "DEBUG: {msg}"'
        }