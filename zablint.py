#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""zablint — статический анализатор Zabbix-шаблонов."""

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class Violation:
    code: str       # FAST_DISCOVERY, UNUSED_MACRO, ...
    severity: str   # critical / warning / info
    message: str
    context: str    # имя правила/итема/триггера

# Совпадает с пользовательскими макросами Zabbix вида {$MACRO}, {$MACRO.CONTEXT}
_USER_MACRO_RE = re.compile(r'\{\$[A-Z0-9_.]+\}')
# Совпадает с числовыми интервалами вида "300", "5m", "1h" (группа 1 — число, группа 2 — суффикс)
_INTERVAL_RE = re.compile(r'^(\d+)([smhdw]?)$')
# Совпадает с вызовом функции nodata() в выражении триггера
_NODATA_RE = re.compile(r'\bnodata\s*\(')

# Коэффициенты перевода суффиксов интервалов в секунды
_SUFFIX_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400, 'w': 604800}


_CHECKS_REQUIRING_SEVERITY = ('unused_macros', 'undefined_macros', 'nodata_triggers', 'discovery_interval')


def load_config(script_dir: Path) -> dict:
    """Загружает конфигурацию линтера из config.yaml."""
    config_path = script_dir / 'config.yaml'
    if not config_path.exists():
        print(f'Ошибка: файл конфигурации не найден: {config_path}', file=sys.stderr)
        sys.exit(2)
    with config_path.open(encoding='utf-8') as f:
        config = yaml.safe_load(f)
    errors = []
    for key in _CHECKS_REQUIRING_SEVERITY:
        cfg = config.get(key) or {}
        if cfg.get('enabled', False) and 'severity' not in cfg:
            errors.append(f'  {key}: отсутствует обязательное поле "severity" (допустимые значения: critical, warning, info)')
    if errors:
        print('Ошибка конфигурации:', file=sys.stderr)
        for e in errors:
            print(e, file=sys.stderr)
        sys.exit(2)
    return config


def load_templates(templates_dir: Path) -> list:
    """Загружает все *.yaml из templates_dir. Возвращает [(filename, data), ...]."""
    results = []
    for path in sorted(templates_dir.glob('*.yaml')):
        try:
            with path.open(encoding='utf-8') as f:
                data = yaml.safe_load(f)
            results.append((path.name, data))
        except yaml.YAMLError as e:
            print(f'Предупреждение: не удалось разобрать {path.name}: {e}', file=sys.stderr)
    return results


def parse_interval(value: str):
    """Конвертирует строку интервала Zabbix в количество секунд.

    Допустимые форматы: целое число (``300``) или число с суффиксом
    ``s`` / ``m`` / ``h`` / ``d`` / ``w``. Число без суффикса трактуется
    как секунды.

    :param value: Строка интервала, например ``"5m"``, ``"1h"``, ``"300"``.
    :type value: str
    :returns: Кортеж ``(seconds, None)`` при успешном разборе или
        ``(None, error_message)`` если формат не распознан.
    :rtype: tuple[int | None, str | None]
    """
    value = str(value).strip()
    m = _INTERVAL_RE.match(value)
    if not m:
        return None, f'Некорректный формат интервала: "{value}" (допустимы суффиксы: s, m, h, d, w)'
    number = int(m.group(1))
    suffix = m.group(2) or 's'
    return number * _SUFFIX_SECONDS[suffix], None


def collect_strings(obj, skip_macros_block=False):
    """Рекурсивно собирает все строковые значения из структуры."""
    strings = []
    if isinstance(obj, str):
        strings.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if skip_macros_block and k == 'macros':
                continue
            strings.extend(collect_strings(v, skip_macros_block))
    elif isinstance(obj, list):
        for item in obj:
            strings.extend(collect_strings(item, skip_macros_block))
    return strings


def find_macros_in_strings(strings):
    """Находит все вхождения пользовательских макросов {$...} в списке строк."""
    macros = set()
    for s in strings:
        macros.update(_USER_MACRO_RE.findall(s))
    return macros


def analyze_template(template: dict, config: dict) -> list:
    """Анализирует один шаблон Zabbix и возвращает список найденных нарушений.

    Выполняет последовательно все включённые в конфиге проверки:
    ``[UNUSED_MACRO]``, ``[UNDEFINED_MACRO]``, ``[NODATA_TRIGGER]``,
    ``[FAST_DISCOVERY]``, ``[INVALID_INTERVAL]``.

    :param template: Словарь одного шаблона из блока ``zabbix_export.templates``.
    :type template: dict
    :param config: Конфигурация линтера, загруженная из ``config.yaml``.
    :type config: dict
    :returns: Список нарушений. Пустой список означает отсутствие нарушений.
    :rtype: list[Violation]
    """
    violations: list[Violation] = []

    # Объявленные макросы
    declared_macros = {}
    for m in template.get('macros', []) or []:
        declared_macros[m['macro']] = str(m.get('value', ''))

    undefined_cfg = config.get('undefined_macros') or {}
    unused_cfg = config.get('unused_macros') or {}
    undefined_enabled = undefined_cfg.get('enabled', False)
    unused_enabled = unused_cfg.get('enabled', False)

    # Все строки шаблона вне блока macros
    strings_outside_macros = collect_strings(template, skip_macros_block=True)
    used_macros = find_macros_in_strings(strings_outside_macros)

    # Неиспользуемые макросы
    if unused_enabled:
        _sev = unused_cfg['severity']
        for macro_name in declared_macros:
            if macro_name not in used_macros:
                violations.append(Violation(
                    code='UNUSED_MACRO',
                    severity=_sev,
                    message=f'Макрос объявлен, но не используется: {macro_name}',
                    context=macro_name,
                ))

    # Битые макросы (с контекстом)
    if undefined_enabled:
        _sev = undefined_cfg['severity']
        named_sections = [
            ('item', template.get('items', []) or []),
            ('discovery_rule', template.get('discovery_rules', []) or []),
            ('trigger', template.get('triggers', []) or []),
            ('graph', template.get('graphs', []) or []),
            ('dashboard', template.get('dashboards', []) or []),
            ('httptests', template.get('httptests', []) or []),
        ]
        reported = set()
        for obj_type, objects in named_sections:
            for obj in objects:
                obj_name = obj.get('name', '?')
                obj_macros = find_macros_in_strings(collect_strings(obj))
                for macro in sorted(obj_macros):
                    if macro not in declared_macros:
                        key = (macro, obj_type, obj_name)
                        if key not in reported:
                            reported.add(key)
                            violations.append(Violation(
                                code='UNDEFINED_MACRO',
                                severity=_sev,
                                message=f'{macro} используется в {obj_type} "{obj_name}", но не объявлен в шаблоне',
                                context=f'{obj_type}/{obj_name}',
                            ))

    # Триггеры с nodata()
    nodata_cfg = config.get('nodata_triggers') or {}
    if nodata_cfg.get('enabled', False):
        _sev = nodata_cfg['severity']
        # Триггеры на верхнем уровне шаблона
        top_triggers = template.get('triggers', []) or []
        # Триггеры, вложенные в items
        item_triggers = []
        for item in template.get('items', []) or []:
            item_triggers.extend(item.get('triggers', []) or [])
        # Прототипы триггеров из discovery_rules -> item_prototypes -> trigger_prototypes
        proto_triggers = []
        for rule in template.get('discovery_rules', []) or []:
            for proto in rule.get('item_prototypes', []) or []:
                proto_triggers.extend(proto.get('trigger_prototypes', []) or [])
        for trigger in top_triggers + item_triggers:
            expr = trigger.get('expression', '') or ''
            if _NODATA_RE.search(expr):
                name = trigger.get('name', '?')
                violations.append(Violation(
                    code='NODATA_TRIGGER',
                    severity=_sev,
                    message=f'Триггер использует nodata(): {expr}',
                    context=name,
                ))
        for trigger in proto_triggers:
            expr = trigger.get('expression', '') or ''
            if _NODATA_RE.search(expr):
                name = trigger.get('name', '?')
                violations.append(Violation(
                    code='NODATA_TRIGGER',
                    severity=_sev,
                    message=f'Прототип триггера использует nodata(): {expr}',
                    context=name,
                ))

    # Частый дискаверинг
    discovery_cfg = config.get('discovery_interval') or {}
    if discovery_cfg.get('enabled', False):
        min_seconds = int(discovery_cfg.get('min_interval_seconds', 600))
        _sev = discovery_cfg['severity']
        for rule in template.get('discovery_rules', []) or []:
            rule_name = rule.get('name', '?')
            rule_key = rule.get('key', '?')
            raw_delay = str(rule.get('delay', '0'))

            resolved_delay = raw_delay
            is_macro_ref = bool(_USER_MACRO_RE.fullmatch(raw_delay))
            if is_macro_ref:
                macro_name = raw_delay
                if macro_name in declared_macros:
                    resolved_delay = declared_macros[macro_name]
                else:
                    # Битый макрос — пропускаем, нарушение уже зафиксировано выше
                    continue

            seconds, err = parse_interval(resolved_delay)
            if err:
                violations.append(Violation(
                    code='INVALID_INTERVAL',
                    severity=_sev,
                    message=f'Некорректный формат интервала: "{resolved_delay}" (допустимы суффиксы: s, m, h, d, w)',
                    context=f'discovery_rule/{rule_name}',
                ))
                continue

            if seconds < min_seconds:
                detail = f'delay={raw_delay} → {resolved_delay} ({seconds}s)' if is_macro_ref else f'delay={raw_delay} ({seconds}s)'
                violations.append(Violation(
                    code='FAST_DISCOVERY',
                    severity=_sev,
                    message=f'{rule_name} ({rule_key}): {detail} — меньше минимума {min_seconds}s',
                    context=f'discovery_rule/{rule_name}',
                ))

    return violations


def load_template_file(path: Path) -> list:
    """Загружает один файл шаблона. Возвращает [(filename, data)] или []."""
    try:
        with path.open(encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return [(path.name, data)]
    except yaml.YAMLError as e:
        print(f'Предупреждение: не удалось разобрать {path.name}: {e}', file=sys.stderr)
        return []


def parse_args() -> argparse.Namespace:
    """Разбирает аргументы командной строки."""
    parser = argparse.ArgumentParser(description='zablint — статический анализатор Zabbix-шаблонов')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--dir', default='templates', help='Директория с шаблонами (по умолчанию: templates)')
    group.add_argument('--file', help='Путь к конкретному файлу шаблона')
    return parser.parse_args()


def main():
    args = parse_args()
    script_dir = Path(__file__).parent
    config = load_config(script_dir)

    if args.file:
        file_path = Path(args.file)
        if not file_path.is_file():
            print(f'Ошибка: файл не найден: {file_path}', file=sys.stderr)
            sys.exit(2)
        template_files = load_template_file(file_path)
    else:
        templates_dir = Path(args.dir)
        if not templates_dir.is_absolute():
            templates_dir = script_dir / templates_dir
        if not templates_dir.is_dir():
            print(f'Ошибка: директория не найдена: {templates_dir}', file=sys.stderr)
            sys.exit(2)
        template_files = load_templates(templates_dir)

    if not template_files:
        print('Нет шаблонов для проверки.', file=sys.stderr)
        sys.exit(2)

    any_violations = False

    for filename, data in template_files:
        if not isinstance(data, dict):
            continue
        export = data.get('zabbix_export', {}) or {}
        templates = export.get('templates', []) or []

        for template in templates:
            tpl_name = template.get('name') or template.get('template', filename)
            violations = analyze_template(template, config)

            print(f'Шаблон: {tpl_name}')
            if violations:
                any_violations = True
                print('  Найденные отклонения:')
                _severity_order = {'critical': 0, 'warning': 1, 'info': 2}
                for v in sorted(violations, key=lambda v: _severity_order.get(v.severity, 99)):
                    print(f'    [{v.code}] [{v.severity}] {v.message}  (context: {v.context})')
            else:
                print('  Отклонений не найдено ✓')
            print()

    if not any_violations:
        print('Все шаблоны прошли проверку ✓')

    sys.exit(1 if any_violations else 0)


if __name__ == '__main__':
    main()
