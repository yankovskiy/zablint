# zablint

Статический анализатор Zabbix-шаблонов. Проверяет шаблоны на типичные ошибки: битые макросы, неиспользуемые макросы, триггеры с `nodata()`, слишком частый дискаверинг.

## Установка

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Использование

Положить шаблоны (`.yaml`, экспортированные из Zabbix) в директорию `templates/` и запустить:

```bash
python zablint.py
```

### Коды завершения

| Код | Значение |
|-----|----------|
| `0` | Нарушений не найдено |
| `1` | Найдены нарушения |
| `2` | Ошибка конфигурации |

### Пример вывода

```
Шаблон: Template OS Linux

  Найденные отклонения:
    [UNDEFINED_MACRO] [critical] {$MEMORY_WARN} используется в item "Memory usage", но не объявлен в шаблоне  (context: item/Memory usage)
    [NODATA_TRIGGER] [warning] Триггер использует nodata(): nodata(/host/agent.ping,5m)=1  (context: Agent is unavailable)
    [UNUSED_MACRO] [info] Макрос объявлен, но не используется: {$OLD_THRESHOLD}  (context: {$OLD_THRESHOLD})

Шаблон: Template Net SNMP

  Отклонений не найдено ✓
```

Нарушения внутри каждого шаблона выводятся в порядке убывания важности: `critical` → `warning` → `info`.

## Конфигурация

Файл `config.yaml` управляет проверками:

```yaml
# Проверка макросов объявленных, но нигде не используемых
unused_macros:
  enabled: true
  severity: info  # critical / warning / info — обязательное поле

# Проверка макросов используемых, но не объявленных в шаблоне
undefined_macros:
  enabled: true
  severity: critical

# Проверка триггеров, использующих функцию nodata()
nodata_triggers:
  enabled: true
  severity: warning

# Проверка частоты discovery rules
discovery_interval:
  enabled: true
  min_interval_seconds: 1800  # минимально допустимый интервал в секундах
  severity: warning
```

Поле `severity` обязательно для каждой включённой проверки. Допустимые значения: `critical`, `warning`, `info`. Если поле отсутствует — линтер завершится с кодом `2`.

## Проверки

### UNUSED_MACRO

Макрос объявлен в блоке `macros` шаблона, но нигде не используется. Скорее всего — остаток после рефакторинга.

### UNDEFINED_MACRO

Макрос вида `{$FOO}` встречается в items/triggers/graphs/etc., но не объявлен в шаблоне. Zabbix подставит пустую строку или значение по умолчанию — поведение непредсказуемо.

### NODATA_TRIGGER

Триггер или прототип триггера использует функцию `nodata()`. Это может приводить к ложным срабатываниям при перезапуске агента или сетевых проблемах.

### FAST_DISCOVERY / INVALID_INTERVAL

Discovery rule опрашивается чаще, чем `min_interval_seconds`. Частый дискаверинг создаёт лишнюю нагрузку на Zabbix-сервер и агент.

`INVALID_INTERVAL` — отдельный код для случая, когда значение `delay` не распознаётся (например, некорректный суффикс).

Если `delay` задан через макрос (`{$DISCOVERY_INTERVAL}`), значение подставляется из объявленных макросов шаблона перед проверкой.
