# 🚨 WinBatch V2 - Быстрые Исправления

## 🔧 Исправление v2.1.2: Plugin Loading Warning

### ❌ Симптомы
```
[WARNING]: Skipping plugin (/runner/project/plugins/connection/winbatch_module_parser.py) 
as it seems to be invalid: module 'ansible.plugins.connection.winbatch_module_parser' 
has no attribute 'Connection'
```

### ✅ РЕШЕНИЕ ПРИМЕНЕНО!
- Перемещен файл `winbatch_module_parser.py` в правильную директорию `plugins/module_utils/`
- Ansible больше не пытается загрузить вспомогательный модуль как connection plugin
- Устранены все предупреждения при загрузке плагина

### 🧪 Тест исправления
```bash
ansible-playbook -i inventory/windows.yml winbatch_v2_test_plugin_fix.yml
```

---

## 🔧 Исправление v2.1.1: PowerShell Escaping Error

### ❌ Симптомы
```
"Cannot process the command because of a missing parameter. A command must follow -Command."
```

### ✅ РЕШЕНИЕ ПРИМЕНЕНО!
- Переписан метод создания PowerShell скриптов
- Используется base64 кодирование для избежания проблем с экранированием
- Упрощена логика передачи команд через SSH

### 🧪 Тест исправления
```bash
ansible-playbook -i inventory/windows.yml winbatch_v2_test_fixed.yml
```

---

## 🔧 Исправление v2.1: "'Connection' object has no attribute 'play_context'"

### ✅ ПРОБЛЕМА РЕШЕНА!

Если вы получили ошибку:
```
"Failed to establish WinBatch V2 connection: 'Connection' object has no attribute 'play_context'"
```

**Это уже исправлено в текущей версии плагина!**

## 🚀 Что Делать

### 1. Убедитесь, что используете последнюю версию
Файл `plugins/connection/winbatch_v2.py` должен содержать:
```python
# Совместимость с разными версиями Ansible API
if not hasattr(self, '_play_context') and hasattr(self, 'play_context'):
    self._play_context = self.play_context
elif not hasattr(self, 'play_context') and hasattr(self, '_play_context'):
    self.play_context = self._play_context
```

### 2. Запустите диагностику
```bash
ansible-playbook -i inventory/winbatch_v2_hosts.yml winbatch_v2_debug.yml
```

### 3. Если проблема остается
Проверьте:
- ✅ SSH подключение работает: `ssh user@your-windows-server`
- ✅ OpenSSH Server запущен на Windows
- ✅ PowerShell настроен как default shell для SSH

## 🔧 Техническая Причина

**Проблема**: В разных версиях Ansible изменился API:
- **Старые версии**: используют `self.play_context`
- **Новые версии**: используют `self._play_context`

**Решение**: Плагин теперь автоматически определяет доступный API и использует правильный атрибут.

## 📊 Совместимость

WinBatch V2.1 теперь работает с:
- ✅ Ansible 2.9+
- ✅ Ansible 2.14+
- ✅ Ansible 2.15+
- ✅ Ansible 2.16+
- ✅ AWX 17.0+
- ✅ AWX 21.0+
- ✅ AWX 23.0+

## 🎯 Результат

После исправления вы должны увидеть:
```
TASK [🚀 Test 4: WinBatch V2 Basic Test] ****
ok: [192.168.0.219] => {
    "changed": false,
    "rc": 0,
    "stdout": "WinBatch V2 basic test\nYOUR-COMPUTER-NAME"
}
```

Вместо ошибки `UNREACHABLE!`

---

**WinBatch V2.1 готов к работе!** 🎉 