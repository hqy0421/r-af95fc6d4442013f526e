# -*- coding: utf-8 -*-
"""Exact decimal support.

This module contains helpers used by the metadata layer, the row/CSV
backends and the SQL backend to represent decimal numbers without ever
converting them to a binary floating point value.

A decimal value is described by two optional parameters:

* ``precision`` – maximum number of significant decimal digits (integer and
  fractional digits together, leading zeros are not counted).  If it is
  ``None`` the value is not limited in the number of digits.
* ``scale`` – number of digits after the decimal point.  If it is ``None``
  the value is not rounded, however when ``precision`` is specified the
  value has to be an integer (SQL ``NUMERIC(p)`` semantics).

Values are validated on input.  Invalid, non-finite (``NaN``/``Infinity``),
over-precise or values that would require rounding raise
:class:`bubbles.errors.DecimalError`.  ``None`` is always preserved as a
missing value.
"""

import math

from decimal import Decimal, InvalidOperation, localcontext

from .errors import DecimalError

__all__ = (
    "DECIMAL_STORAGE_TYPE",
    "decimal_parameters",
    "parse_decimal_storage_type",
    "coerce_decimal",
    "decimal_text",
    "decimal_sort_key",
    "decimal_key_parameters",
    "DecimalSum",
    "DecimalMin",
    "DecimalMax",
    "register_sqlite_decimal_functions",
)

DECIMAL_STORAGE_TYPE = "decimal"

# Precision used for internal arithmetic.  It is intentionally much larger
# than any realistic precision so that addition/subtraction of very large
# numbers can not lose digits.  Stored values are text, therefore the sum is
# not limited by any machine integer.
CALCULATION_PRECISION = 10000

# Number of extra integer digits a summed decimal field is widened by.
DECIMAL_SUM_PRECISION_INCREMENT = 10

# Width of the integer part of the sort key when the precision is unknown.
DEFAULT_SORT_KEY_INTEGER_DIGITS = 100

# Scale used for sorting when the scale is unknown.
DEFAULT_SORT_KEY_SCALE = 100


def decimal_parameters(precision=None, scale=None):
    """Return effective ``(precision, scale)`` of a decimal description.

    When a precision is specified but a scale is not, the scale is ``0``
    (integer-only decimal).
    """

    if precision is not None:
        precision = _as_integer(precision, "precision")
        if precision < 1:
            raise DecimalError("Decimal precision has to be a positive "
                               "integer, got %r" % (precision, ))

    if scale is not None:
        scale = _as_integer(scale, "scale")
        if scale < 0:
            raise DecimalError("Decimal scale has to be a non-negative "
                               "integer, got %r" % (scale, ))

    if precision is not None and scale is None:
        scale = 0

    if precision is not None and scale is not None and scale > precision:
        raise DecimalError("Decimal scale %d is larger than precision %d"
                           % (scale, precision))

    return precision, scale


def _as_integer(value, label):
    if isinstance(value, bool):
        raise DecimalError("Decimal %s has to be an integer, got %r"
                           % (label, value))
    if isinstance(value, int):
        return value

    try:
        integer = int(value)
    except (TypeError, ValueError):
        raise DecimalError("Decimal %s has to be an integer, got %r"
                           % (label, value))

    if integer != value:
        raise DecimalError("Decimal %s has to be an integer, got %r"
                           % (label, value))

    return integer


def parse_decimal_storage_type(storage_type):
    """Parse a storage type that carries decimal parameters.

    Accepts either a plain storage type (returned unchanged) or a storage
    type with parameters in form ``decimal(precision, scale)``,
    ``decimal(precision)`` or ``decimal``.

    Returns a tuple ``(storage_type, precision, scale)``.  For all non
    decimal types the parameters are ``(None, None)``.
    """

    if not isinstance(storage_type, str):
        return storage_type, None, None

    text = storage_type.strip()
    lowered = text.lower()

    if lowered.startswith(DECIMAL_STORAGE_TYPE):
        name = lowered[len(DECIMAL_STORAGE_TYPE):].strip()

        if not name:
            return DECIMAL_STORAGE_TYPE, None, None

        if not (name.startswith("(") and name.endswith(")")):
            return storage_type, None, None

        arguments = name[1:-1].strip()
        if not arguments:
            return DECIMAL_STORAGE_TYPE, None, None

        parts = [part.strip() for part in arguments.split(",")]
        if len(parts) > 2:
            raise DecimalError("Invalid decimal storage type %r: too many "
                               "parameters" % (storage_type, ))

        try:
            precision = int(parts[0]) if parts[0] else None
            scale = int(parts[1]) if len(parts) > 1 and parts[1] else None
        except ValueError:
            raise DecimalError("Invalid decimal storage type %r: parameters "
                               "have to be integers" % (storage_type, ))

        return DECIMAL_STORAGE_TYPE, precision, scale

    return storage_type, None, None


def _decimal_from_value(value):
    """Convert a Python value into a :class:`decimal.Decimal` without going
    through binary floating point when avoidable."""

    if isinstance(value, Decimal):
        return value

    if isinstance(value, bool):
        raise DecimalError("Boolean value %r can not be used as a decimal"
                           % (value, ))

    if isinstance(value, int):
        return Decimal(value)

    if isinstance(value, float):
        if not math.isfinite(value):
            raise DecimalError("Non-finite value %r can not be used as a "
                               "decimal" % (value, ))
        # repr() of a float is the shortest string that round-trips, it
        # describes the decimal value the user meant.
        return Decimal(repr(value))

    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise DecimalError("Empty string can not be used as a decimal")

        try:
            return Decimal(text)
        except (InvalidOperation, ValueError):
            raise DecimalError("Invalid decimal value %r" % (value, ))

    raise DecimalError("Value of type %s can not be used as a decimal"
                       % (type(value).__name__, ))


def coerce_decimal(value, precision=None, scale=None):
    """Validate and normalize ``value`` as a decimal with ``precision`` and
    ``scale``.  Returns a :class:`decimal.Decimal` or ``None``.

    Raises :class:`bubbles.errors.DecimalError` when the value is not a
    finite decimal number, when it has more digits than allowed by
    ``precision`` or when it would have to be rounded to ``scale``.
    """

    if value is None:
        return None

    precision, scale = decimal_parameters(precision, scale)

    number = _decimal_from_value(value)

    if not number.is_finite():
        raise DecimalError("Non-finite value %r can not be used as a decimal"
                           % (value, ))

    if scale is not None:
        quantum = Decimal(1).scaleb(-scale)
        sign, digits, exponent = number.as_tuple()

        with localcontext() as context:
            context.prec = len(digits) + abs(exponent) + scale + 10
            try:
                quantized = number.quantize(quantum)
            except InvalidOperation:
                raise DecimalError("Value %s can not be represented with "
                                   "scale %d" % (number, scale))

        if quantized != number:
            raise DecimalError("Value %s would require rounding to scale %d"
                               % (number, scale))

        number = quantized

    if precision is not None:
        integer_digits = _integer_digit_count(number)
        allowed = precision - (scale or 0)
        if integer_digits > allowed:
            raise DecimalError("Decimal value exceeds precision %d "
                               "(at most %d integer digits are allowed with "
                               "scale %d)" % (precision, allowed, scale or 0))

    return number


def _integer_digit_count(number):
    if number == 0:
        return 0

    return number.adjusted() + 1


def decimal_text(value, precision=None, scale=None):
    """Return a canonical text representation of a decimal value.

    Canonical text is used for storage in backends that do not have native
    decimal support.  Values with a fixed scale always have exactly ``scale``
    fractional digits.  Values without a scale are normalized by removing
    trailing fractional zeros.
    """

    number = coerce_decimal(value, precision, scale)

    if number is None:
        return None

    # Negative zero and zero are the same value, to keep the stored text
    # canonical (and equality comparisons correct) they share a form.
    if number == 0:
        number = abs(number)

    _, effective_scale = decimal_parameters(precision, scale)

    if effective_scale is not None:
        return format(number, ".%df" % effective_scale)

    return _canonical_unscaled_text(number)


def _canonical_unscaled_text(number):
    text = format(number, "f")

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    if text in ("-0", ""):
        text = "0"

    return text


def _text_to_decimal(value):
    if isinstance(value, Decimal):
        return value

    if value is None:
        return None

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise DecimalError("Invalid decimal value %r" % (value, ))


def decimal_key_parameters(field):
    """Return ``(integer_digits, scale)`` used to build sort keys for a
    decimal ``field``."""

    precision = getattr(field, "precision", None)
    scale = getattr(field, "scale", None)

    if precision is None:
        integer_digits = DEFAULT_SORT_KEY_INTEGER_DIGITS
    else:
        integer_digits = max(1, precision - (scale or 0))

    if scale is None:
        scale = DEFAULT_SORT_KEY_SCALE

    return integer_digits, scale


def decimal_sort_key(value, integer_digits=None, scale=None):
    """Return a string that sorts in the same order as the decimal value.

    It can be used as an SQLite user defined function so that ordering and
    range comparison of text-stored decimals is numerically correct.
    """

    if value is None:
        return None

    number = _text_to_decimal(value)

    if number is None:
        return None

    if not number.is_finite():
        raise DecimalError("Non-finite value %r can not be sorted as a "
                           "decimal" % (value, ))

    if integer_digits is None:
        integer_digits = DEFAULT_SORT_KEY_INTEGER_DIGITS
    if scale is None:
        scale = DEFAULT_SORT_KEY_SCALE

    integer_digits = _as_integer(integer_digits, "integer digits")
    scale = _as_integer(scale, "scale")

    magnitude = format(abs(number), ".%df" % scale)
    integer, _, fraction = magnitude.partition(".")

    integer = integer.zfill(integer_digits)
    key = integer + fraction

    if number < 0:
        digits = "".join(str(9 - int(c)) for c in key)
        return "0" + digits

    if number == 0:
        return "1" + key

    return "2" + key


def _sqlite_sort_key(value, integer_digits, scale):
    return decimal_sort_key(value, integer_digits, scale)


class _DecimalAggregate(object):
    """Shared implementation of the SQLite decimal aggregates."""

    def __init__(self):
        self.value = None

    def step(self, value):
        if value is None:
            return

        number = _text_to_decimal(value)

        with localcontext() as context:
            context.prec = CALCULATION_PRECISION
            self.value = self._aggregate(self.value, number)

    def _aggregate(self, current, number):
        raise NotImplementedError

    def finalize(self):
        if self.value is None:
            return None

        return format(self.value, "f")


class DecimalSum(_DecimalAggregate):
    """SQLite aggregate that sums decimals exactly (ignoring NULLs)."""

    def _aggregate(self, current, number):
        if current is None:
            return number

        return current + number


class DecimalMin(_DecimalAggregate):
    """SQLite aggregate that returns the smallest decimal (ignoring NULLs)."""

    def _aggregate(self, current, number):
        if current is None or number < current:
            return number

        return current


class DecimalMax(_DecimalAggregate):
    """SQLite aggregate that returns the largest decimal (ignoring NULLs)."""

    def _aggregate(self, current, number):
        if current is None or number > current:
            return number

        return current


def register_sqlite_decimal_functions(connection):
    """Register decimal helper functions on a DBAPI connection.

    ``connection`` is a raw DBAPI (``sqlite3.Connection``) connection.
    """

    if connection is None:
        return

    create_function = getattr(connection, "create_function", None)
    create_aggregate = getattr(connection, "create_aggregate", None)

    if not create_function or not create_aggregate:
        return

    try:
        create_function("bubbles_decimal_sortkey", 3, _sqlite_sort_key,
                        deterministic=True)
    except TypeError:
        # Older Python/SQLite combinations do not know the `deterministic`
        # argument.
        create_function("bubbles_decimal_sortkey", 3, _sqlite_sort_key)

    create_aggregate("bubbles_decimal_sum", 1, DecimalSum)
    create_aggregate("bubbles_decimal_min", 1, DecimalMin)
    create_aggregate("bubbles_decimal_max", 1, DecimalMax)
