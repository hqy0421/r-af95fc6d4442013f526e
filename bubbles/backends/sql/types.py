# -*- coding: utf-8 -*-
"""SQL storage type for exact decimals.

SQLite does not have a native decimal type.  Storing decimals as ``NUMERIC``
and letting the DBAPI convert them through binary floating point loses
digits.  Therefore decimals are stored as canonical text in a column with a
dedicated declared type ``DECIMAL_TEXT(precision, scale)``.  The declared type
carries the parameters, so the field description can be fully reconstructed
after re-opening the store.

Comparison, ordering and summation of text values are not numeric, so the
store registers SQLite functions that do the comparisons and aggregates on
:class:`decimal.Decimal` values.

On databases that support exact decimals natively the type is compiled as
``NUMERIC(precision, scale)`` and values are passed as
:class:`decimal.Decimal` objects.
"""

import decimal

import sqlalchemy
from sqlalchemy.types import TypeEngine
from sqlalchemy.ext.compiler import compiles

from ...numeric import (coerce_decimal, decimal_text, decimal_sort_key,
                        decimal_parameters)

try:
    from sqlalchemy.dialects import sqlite as _sqlite_dialect
except ImportError:  # pragma: no cover - sqlalchemy always provides it
    _sqlite_dialect = None

__all__ = (
    "SQLDecimal",
    "DECIMAL_SQL_TYPE_NAME",
    "decimal_sort_key_expression",
    "is_decimal_type",
)

# Name of the SQLite declared type.  The parameters are appended in
# parentheses and reflected back by SQLite's type name parser.
DECIMAL_SQL_TYPE_NAME = "DECIMAL_TEXT"


def is_decimal_type(type_):
    """Returns `True` if `type_` is the exact decimal SQL type."""
    return isinstance(type_, SQLDecimal)


class SQLDecimal(TypeEngine):
    """Exact decimal SQL type with optional `precision` and `scale`.

    The value is a :class:`decimal.Decimal` or ``None``.  Invalid,
    non-finite, over-precise and rounding-requiring values raise
    :class:`bubbles.errors.DecimalError` during binding.
    """

    __visit_name__ = "decimal_exact"

    def __init__(self, precision=None, scale=None):
        precision, scale = decimal_parameters(precision, scale)
        self.precision = precision
        self.scale = scale
        super(SQLDecimal, self).__init__()

    def __repr__(self):
        return "%s(%s, %s)" % (self.__class__.__name__,
                               self.precision, self.scale)

    def copy(self, **kwargs):
        return SQLDecimal(self.precision, self.scale)

    @property
    def python_type(self):
        return decimal.Decimal

    def bind_processor(self, dialect):
        if dialect.name == "sqlite":
            precision, scale = self.precision, self.scale

            def process(value):
                return decimal_text(value, precision, scale)

            return process

        precision, scale = self.precision, self.scale

        def process(value):
            return coerce_decimal(value, precision, scale)

        return process

    def result_processor(self, dialect, coltype):
        if dialect.name == "sqlite":
            def process(value):
                if value is None or isinstance(value, decimal.Decimal):
                    return value
                return decimal.Decimal(str(value))

            return process

        def process(value):
            if value is None:
                return None
            if isinstance(value, decimal.Decimal):
                return value

            return decimal.Decimal(str(value))

        return process

    def literal_processor(self, dialect):
        precision, scale = self.precision, self.scale

        def process(value):
            text = decimal_text(value, precision, scale)
            if text is None:
                return "NULL"
            return "'%s'" % text

        return process


@compiles(SQLDecimal, "sqlite")
def _compile_sqlite_decimal(element, compiler, **kw):
    if element.precision is None:
        return DECIMAL_SQL_TYPE_NAME

    return "%s(%d, %d)" % (DECIMAL_SQL_TYPE_NAME, element.precision,
                            element.scale or 0)


@compiles(SQLDecimal)
def _compile_decimal(element, compiler, **kw):
    if element.precision is None:
        return "NUMERIC"

    return "NUMERIC(%d, %d)" % (element.precision, element.scale or 0)


# Make SQLite reflection recognize the declared type and its parameters.
if _sqlite_dialect is not None:
    try:
        _sqlite_dialect.base.ischema_names[DECIMAL_SQL_TYPE_NAME] = SQLDecimal
    except AttributeError:  # pragma: no cover
        pass


def decimal_sort_key_expression(column, field):
    """Returns an SQL expression of the sortable text key for a decimal
    `column` that belongs to `field`."""

    from ...numeric import decimal_key_parameters

    integer_digits, scale = decimal_key_parameters(field)
    return sqlalchemy.func.bubbles_decimal_sortkey(column,
                                                   integer_digits, scale)


def decimal_sort_key_value(value, field):
    """Returns the sort key (a string) for a decimal `value` of `field`."""
    from ...numeric import decimal_key_parameters

    integer_digits, scale = decimal_key_parameters(field)
    return decimal_sort_key(value, integer_digits, scale)
