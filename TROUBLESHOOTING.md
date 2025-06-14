# 🔧 WinBatch V2 - Устранение Неполадок

## ❌ Проблема: "Could not find a version that satisfies the requirement threading-timer"

### 🎯 Решение
Эта ошибка возникала в ранней версии плагина из-за несуществующей зависимости. **Проблема ИСПРАВЛЕНА!**

**WinBatch V2** теперь **НЕ ТРЕБУЕТ** внешних зависимостей и использует только:
- ✅ Стандартные библиотеки Python
- ✅ Системные SSH инструменты
- ✅ Встроенные модули Ansible

### 🚀 Что Изменилось
```python
# СТАРАЯ версия (с ошибкой)
required_packages = [
    'paramiko>=2.10.0',
    'threading-timer>=1.0.0',  # ❌ Этот пакет не существует!
]

# НОВАЯ версия (исправлена)
# Использует только системные инструменты SSH
# БЕЗ внешних зависимостей!
```

## ✅ Проверка Работоспособности

### 1. Быстрый Тест
```bash
# Запустите простой тест
ansible-playbook -i inventory/winbatch_v2_hosts.yml winbatch_v2_test.yml
```

### 2. Полная Демонстрация
```bash
# Запустите полную демонстрацию
ansible-playbook -i inventory/winbatch_v2_hosts.yml winbatch_v2_demo.yml
```

## 🛠️ Другие Возможные Проблемы

### Проблема: SSH соединение не устанавливается
**Причина**: OpenSSH Server не настроен на Windows
**Решение**:
```powershell
# На Windows сервере выполните:
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Start-Service sshd
Set-Service -Name sshd -StartupType 'Automatic'

# Настройте PowerShell как shell по умолчанию
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
```

### Проблема: "Connection plugin 'winbatch_v2' not found"
**Причина**: Неправильный путь к плагину в ansible.cfg
**Решение**:
```ini
# В ansible.cfg убедитесь, что указан правильный путь:
[defaults]
connection_plugins = plugins/connection
```

### Проблема: Медленное выполнение
**Причина**: Неоптимальные настройки batch_size
**Решение**:
```yaml
# Для мощных серверов
ansible_winbatch_batch_size: 30

# Для медленных серверов
ansible_winbatch_batch_size: 5
```

### Проблема: Таймауты выполнения
**Причина**: Слишком короткие таймауты
**Решение**:
```yaml
# Увеличьте таймауты в инвентаре
ansible_winbatch_execution_timeout: 7200  # 2 часа
ansible_winbatch_ssh_timeout: 120         # 2 минуты
```

## 🔍 Диагностика

### Проверка SSH Доступности
```yaml
- name: "Test SSH connectivity"
  shell: ssh -V
  delegate_to: localhost
```

### Проверка Windows OpenSSH
```yaml
- name: "Test Windows SSH"
  win_shell: Get-Service sshd
  connection: winrm  # Используйте WinRM для первоначальной проверки
```

### Проверка Плагина
```yaml
- name: "Test WinBatch V2 Plugin"
  debug:
    msg: "WinBatch V2 plugin loaded successfully"
  connection: winbatch_v2
```

## 📊 Мониторинг Производительности

### Включение Подробного Логирования
```ini
# В ansible.cfg
[defaults]
log_path = /tmp/ansible.log
verbosity = 2

[winbatch]
enable_performance_logging = true
```

### Анализ Времени Выполнения
```yaml
- name: "Performance test"
  win_shell: |
    $start = Get-Date
    # Ваши команды здесь
    $end = Get-Date
    Write-Host "Execution time: $(($end - $start).TotalSeconds) seconds"
  connection: winbatch_v2
```

## 🎯 Рекомендации

### Для Продакшена
1. **Используйте SSH ключи** вместо паролей
2. **Настройте оптимальный batch_size** (15-25 для большинства случаев)
3. **Мониторьте производительность** через логи
4. **Тестируйте на dev окружении** перед продакшеном

### Для Разработки
1. **Используйте winbatch_v2_test.yml** для быстрой проверки
2. **Включите подробное логирование** (verbosity = 3)
3. **Уменьшите batch_size** (5-10) для отладки
4. **Используйте короткие таймауты** для быстрого feedback

## 🆘 Получение Помощи

Если проблема не решена:

1. **Проверьте логи** AWX Job execution
2. **Запустите тестовый playbook** winbatch_v2_test.yml
3. **Убедитесь в правильности настроек** инвентаря
4. **Проверьте доступность SSH** на Windows сервере

## ✅ Контрольный Список

- [ ] OpenSSH Server установлен и запущен на Windows
- [ ] SSH подключение работает (тест через обычный ssh)
- [ ] ansible.cfg содержит правильный путь к плагину
- [ ] Инвентарь настроен с ansible_connection: winbatch_v2
- [ ] Тестовый playbook выполняется успешно
- [ ] Производительность соответствует ожиданиям (3-5x улучшение)

---

**Помните**: WinBatch V2 теперь полностью самодостаточен и НЕ требует внешних зависимостей! 🚀 