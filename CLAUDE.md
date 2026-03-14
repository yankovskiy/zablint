# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Установка зависимостей
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Запуск линтера
python zablint.py
```

Линтер завершается с кодом `0` если нарушений нет, `1` если найдены нарушения, `2` при ошибке конфигурации.

## Архитектура

Весь код — единственный файл `zablint.py`. Точка входа: `main()`.

**Поток данных:**
1. `load_config()` читает `config.yaml` — управляет включением/отключением каждой проверки
2. `load_templates()` загружает все `*.yaml` из `templates/`
3. Для каждого шаблона вызывается `analyze_template(template, config)` — возвращает `list[Violation]`
4. Результаты выводятся в stdout

**Проверки в `analyze_template()`** (выполняются последовательно):
- `[UNUSED_MACRO]` — макрос объявлен в `template.macros`, но не встречается нигде в шаблоне
- `[UNDEFINED_MACRO]` — макрос используется в items/triggers/graphs/etc., но не объявлен
- `[NODATA_TRIGGER]` — триггер или прототип триггера содержит вызов `nodata()` в выражении
- `[FAST_DISCOVERY]` / `[INVALID_INTERVAL]` — discovery rule с интервалом ниже `min_interval_seconds`

**Где искать триггеры** (важно для проверки nodata):
- `template.triggers[]` — триггеры верхнего уровня
- `template.items[].triggers[]` — триггеры, привязанные к конкретному item
- `template.discovery_rules[].item_prototypes[].trigger_prototypes[]` — прототипы триггеров

**Разрешение макросов в интервалах:** если `delay` discovery rule — это ссылка вида `{$MACRO}`, значение подставляется из `declared_macros` перед проверкой интервала. Битые макросы в delay пропускаются (нарушение уже зафиксировано в `[UNDEFINED_MACRO]`).

## Добавление новой проверки

1. Добавить параметр в `config.yaml` с полем `enabled: true/false`
2. Прочитать конфиг в `analyze_template()`: `cfg = config.get('key') or {}`
3. Добавить логику и `violations.append(Violation(code='КОД', severity='warning/critical/info', message='...', context='...'))`
