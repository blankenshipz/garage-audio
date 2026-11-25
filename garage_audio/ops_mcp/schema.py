"""Strict validator for the JSON Schema subset used by the tool input schemas (stdlib only)."""
import math
import re

from .errors import OpsError


def _fail(path, message):
    raise OpsError('invalid_argument', f"{path or 'arguments'}: {message}")


def validate(schema, value, path=''):
    kind = schema.get('type')
    if 'enum' in schema and value not in schema['enum']:
        _fail(path, f"must be one of {schema['enum']}")
    if kind == 'object':
        if not isinstance(value, dict):
            _fail(path, 'must be an object')
        props = schema.get('properties', {})
        for key in schema.get('required', []):
            if key not in value:
                _fail(f'{path}.{key}'.lstrip('.'), 'is required')
        if schema.get('additionalProperties', True) is False:
            extra = sorted(set(value) - set(props))
            if extra:
                _fail(path, f'unknown field(s) {extra}')
        if schema.get('minProperties') and len(value) < schema['minProperties']:
            _fail(path, f"needs at least {schema['minProperties']} field(s)")
        for key, item in value.items():
            if key in props:
                validate(props[key], item, f'{path}.{key}'.lstrip('.'))
    elif kind == 'array':
        if not isinstance(value, list):
            _fail(path, 'must be an array')
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', 10 ** 6):
            _fail(path, f"must have {schema.get('minItems', 0)}-{schema.get('maxItems')} items")
        if schema.get('uniqueItems') and len(set(map(repr, value))) != len(value):
            _fail(path, 'items must be unique')
        for i, item in enumerate(value):
            validate(schema.get('items', {}), item, f'{path}[{i}]')
    elif kind == 'string':
        if not isinstance(value, str):
            _fail(path, 'must be a string')
        if len(value) < schema.get('minLength', 0) or len(value) > schema.get('maxLength', 10 ** 7):
            _fail(path, f"length must be {schema.get('minLength', 0)}-{schema.get('maxLength')}")
        if 'pattern' in schema and not re.fullmatch(schema['pattern'], value):
            _fail(path, 'has an invalid format')
        if '\x00' in value:
            _fail(path, 'must not contain NUL')
    elif kind in ('number', 'integer'):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == 'integer' and not isinstance(value, int)):
            _fail(path, f'must be a{"n integer" if kind == "integer" else " number"}')
        if not math.isfinite(value):
            _fail(path, 'must be finite')
        if 'minimum' in schema and value < schema['minimum']:
            _fail(path, f"must be >= {schema['minimum']}")
        if 'maximum' in schema and value > schema['maximum']:
            _fail(path, f"must be <= {schema['maximum']}")
    elif kind == 'boolean':
        if not isinstance(value, bool):
            _fail(path, 'must be true or false')
    return value
