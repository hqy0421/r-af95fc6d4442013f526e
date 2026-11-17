# -*- coding: utf-8 -*-
"""Tests for exact decimal storage and operations."""

import os
import tempfile
import unittest
from decimal import Decimal

from bubbles import Field, FieldList, RowListDataObject, IterableDataSource
from bubbles.errors import DecimalError
from bubbles.execution.context import default_context
from bubbles.numeric import (coerce_decimal, decimal_text,
                              parse_decimal_storage_type)
from bubbles.backends.text.objects import CSVSource, CSVTarget
from bubbles.backends.sql.objects import SQLDataStore


def sqlite_store():
    path = tempfile.mktemp(suffix=".db")
    return SQLDataStore("sqlite:///" + path), path


def amount_fields():
    return FieldList(Field("id", "integer"),
                     Field("amount", "decimal", precision=18, scale=2))


class DecimalMetadataTestCase(unittest.TestCase):
    def test_field_parameters(self):
        field = Field("amount", "decimal", precision=18, scale=2)
        self.assertTrue(field.is_decimal())
        self.assertEqual((18, 2), field.decimal_parameters())
        self.assertEqual("decimal(18, 2)", field.storage_type_string())

        field = Field("amount", "decimal(10, 3)")
        self.assertEqual((10, 3), field.decimal_parameters())

        # precision without scale means integer decimal
        field = Field("amount", "decimal", precision=10)
        self.assertEqual((10, 0), field.decimal_parameters())

    def test_field_clone_and_dict(self):
        field = Field("amount", "decimal", precision=18, scale=2)
        clone = field.clone(name="total")
        self.assertEqual((18, 2), clone.decimal_parameters())

        restored = Field(**dict(field.to_dict()))
        self.assertEqual((18, 2), restored.decimal_parameters())
        self.assertEqual(field, restored)

        field = Field("amount", "string", precision=18, scale=2)
        self.assertEqual((None, None), field.decimal_parameters())

    def test_parse_storage_type(self):
        self.assertEqual(("decimal", None, None),
                         parse_decimal_storage_type("decimal"))
        self.assertEqual(("decimal", 18, None),
                         parse_decimal_storage_type("decimal(18)"))
        self.assertEqual(("decimal", 18, 2),
                         parse_decimal_storage_type("decimal(18, 2)"))
        self.assertEqual(("string", None, None),
                         parse_decimal_storage_type("string"))

    def test_invalid_field_parameters(self):
        self.assertRaises(DecimalError, Field, "a", "decimal", precision=0)
        self.assertRaises(DecimalError, Field, "a", "decimal", scale=-1)
        self.assertRaises(DecimalError, Field, "a", "decimal", precision=2,
                          scale=3)

    def test_aggregated_field_widens_sum(self):
        fields = amount_fields()
        fields.append("name")
        summed = fields.aggregated_fields([("amount", "sum")])
        self.assertEqual((28, 2), summed.field("amount_sum").decimal_parameters())

        minimal = fields.aggregated_fields([("amount", "min")])
        self.assertEqual((18, 2), minimal.field("amount_min").decimal_parameters())


class DecimalValueTestCase(unittest.TestCase):
    def test_coerce(self):
        self.assertEqual(Decimal("12.30"), coerce_decimal("12.30", 10, 2))
        self.assertEqual(Decimal("12.00"), coerce_decimal(12, 10, 2))
        self.assertIsNone(coerce_decimal(None, 10, 2))

    def test_invalid_values(self):
        for value in ("abc", "", "NaN", "Infinity", "-Infinity"):
            self.assertRaises(DecimalError, coerce_decimal, value, 10, 2)

        self.assertRaises(DecimalError, coerce_decimal, True, 10, 2)
        # requires rounding
        self.assertRaises(DecimalError, coerce_decimal, "1.005", 10, 2)
        # too many integer digits
        self.assertRaises(DecimalError, coerce_decimal, "123456789.00", 10, 2)

    def test_text(self):
        self.assertEqual("12.30", decimal_text(Decimal("12.3"), 10, 2))
        self.assertEqual("12.3", decimal_text(Decimal("12.30"), None, None))
        self.assertIsNone(decimal_text(None, 10, 2))

    def test_large_values(self):
        value = Decimal("123456789012345678901234.56")
        self.assertEqual(value, coerce_decimal(value, None, None))
        self.assertEqual("123456789012345678901234.56",
                         decimal_text(value, None, None))


class DecimalRowsTestCase(unittest.TestCase):
    def setUp(self):
        self.ctx = default_context

    def test_rows_and_records(self):
        fields = FieldList(Field("id", "integer"), Field("amount", "string"))
        obj = RowListDataObject([[1, "12.30"], [2, None]], fields)
        retyped = self.ctx.op.retype(obj, {"amount": "decimal(10, 2)"})

        self.assertEqual((10, 2),
                         retyped.fields.field("amount").decimal_parameters())
        self.assertEqual([[1, Decimal("12.30")], [2, None]],
                         list(retyped.rows()))

        obj = RowListDataObject([[1, "12.30"], [2, None]], fields)
        retyped = self.ctx.op.retype(obj, {"amount": "decimal(10, 2)"})
        self.assertEqual([{"id": 1, "amount": Decimal("12.30")},
                          {"id": 2, "amount": None}],
                         list(retyped.records()))

    def test_retype_rejects_invalid(self):
        fields = FieldList(Field("amount", "string"))
        obj = RowListDataObject([["1.005"]], fields)
        retyped = self.ctx.op.retype(obj, {"amount": "decimal(10, 2)"})
        self.assertRaises(DecimalError, list, retyped.rows())

    def test_records_are_kept_exact(self):
        fields = amount_fields()
        rows = [[1, Decimal("1.10")], [2, None]]
        obj = RowListDataObject(rows, fields)
        source = IterableDataSource(iter(obj.rows()), obj.fields)
        self.assertEqual(rows, [list(record.values())
                                for record in source.records()])

    def test_aggregate(self):
        fields = FieldList(Field("g", "string"),
                           Field("amount", "decimal", precision=10, scale=2))
        rows = [["a", Decimal("1.10")], ["a", None], ["a", Decimal("2.20")],
                ["b", None], ["b", None]]
        obj = RowListDataObject(rows, fields)

        out = self.ctx.op.aggregate(obj, "g", [("amount", "sum")],
                                    include_count=False)
        self.assertEqual((20, 2),
                         out.fields.field("amount_sum").decimal_parameters())
        self.assertEqual([["a", Decimal("3.30")], ["b", None]],
                         sorted(out.rows()))

    def test_sort_and_range(self):
        fields = FieldList(Field("id", "integer"),
                           Field("amount", "decimal", precision=10, scale=2))
        rows = [[1, Decimal("9.99")], [2, None], [3, Decimal("-1.00")],
                [4, Decimal("10.00")]]
        obj = RowListDataObject(rows, fields)

        out = self.ctx.op.sort(obj, [("amount", "ascending")])
        self.assertEqual([None, Decimal("-1.00"), Decimal("9.99"),
                          Decimal("10.00")], [row[1] for row in out.rows()])

        out = self.ctx.op.filter_by_range(obj, "amount", 0, None)
        self.assertEqual([Decimal("9.99"), Decimal("10.00")],
                         [row[1] for row in out.rows()])


class DecimalCSVTestCase(unittest.TestCase):
    def test_round_trip(self):
        path = tempfile.mktemp(suffix=".csv")
        fields = amount_fields()

        try:
            target = CSVTarget(path, fields=fields)
            target.append([1, Decimal("12.30")])
            target.append([2, None])
            target.append([3, Decimal("-4.50")])
            target.finalize()

            source = CSVSource(path, fields=fields)
            rows = list(source.rows())
            self.assertEqual([[Decimal("12.30")], [None],
                              [Decimal("-4.50")]],
                             [row[1:] for row in rows])

            source = CSVSource(path, fields=fields)
            records = list(source.records())
            self.assertEqual(Decimal("-4.50"), records[2]["amount"])
            self.assertIsNone(records[1]["amount"])
        finally:
            os.unlink(path)

    def test_invalid_value(self):
        path = tempfile.mktemp(suffix=".csv")
        with open(path, "w") as handle:
            handle.write("id,amount\n1,1.005\n")

        try:
            source = CSVSource(path, fields=amount_fields())
            self.assertRaises(DecimalError, list, source.rows())
        finally:
            os.unlink(path)


class DecimalSQLTestCase(unittest.TestCase):
    def setUp(self):
        self.context = default_context
        self.store, self.path = sqlite_store()

    def tearDown(self):
        self.store.close()
        if os.path.exists(self.path):
            os.unlink(self.path)

    def _table(self, rows):
        table = self.store.create("amounts", amount_fields())
        for row in rows:
            table.append(row)
        table.flush()
        return table

    def test_round_trip(self):
        table = self._table([[1, Decimal("12.30")], [2, None],
                             [3, Decimal("-4.50")]])
        self.assertEqual([(1, Decimal("12.30")), (2, None),
                          (3, Decimal("-4.50"))], list(table.rows()))

    def test_invalid_value_on_append(self):
        table = self._table([])
        self.assertRaises(DecimalError, table.append, [1, Decimal("1.005")])
        self.assertRaises(DecimalError, table.append, [1, "abc"])

    def test_reflect_after_reopen(self):
        table = self._table([[1, Decimal("1234567890123456.78")], [2, None]])
        self.store.close()

        self.store = SQLDataStore("sqlite:///" + self.path)
        table = self.store.get_object("amounts")
        field = table.fields.field("amount")
        self.assertEqual("decimal", field.storage_type)
        self.assertEqual((18, 2), field.decimal_parameters())
        self.assertEqual(Decimal("1234567890123456.78"),
                         list(table.rows())[0][1])

    def test_operations_keep_decimals(self):
        table = self._table([[1, Decimal("12.30")], [2, None],
                             [3, Decimal("-4.50")]])

        renamed = self.context.op.keep_fields(table, ["id", "amount"])
        renamed = self.context.op.rename_fields(renamed, {"amount": "total"})
        self.assertEqual((18, 2),
                         renamed.fields.field("total").decimal_parameters())

        filtered = self.context.op.filter_by_range(renamed, "total", 0, 100)
        self.assertEqual([(1, Decimal("12.30"))], list(filtered.rows()))

        sorted_ = self.context.op.sort(filtered, [("total", "ascending")])
        self.assertEqual([(1, Decimal("12.30"))], list(sorted_.rows()))

    def test_aggregate(self):
        fields = FieldList(Field("id", "integer"), Field("g", "string"),
                           Field("amount", "decimal",
                                 precision=10, scale=2))
        table = self.store.create("aggregated", fields)
        for row in [[1, "a", Decimal("1.10")], [2, "a", None],
                    [3, "a", Decimal("2.20")], [4, "b", None]]:
            table.append(row)
        table.flush()

        result = self.context.op.aggregate(table, "g", [("amount", "sum")],
                                           include_count=False)
        self.assertEqual((20, 2),
                         result.fields.field("amount_sum").decimal_parameters())
        self.assertEqual([("a", Decimal("3.30")), ("b", None)],
                         list(result.rows()))

    def test_sum_cancellation_and_widening(self):
        fields = FieldList(Field("g", "string"),
                           Field("amount", "decimal", precision=30, scale=2))
        table = self.store.create("precise", fields)
        table.append(["a", Decimal("123456789012345678901234.56")])
        table.append(["a", Decimal("-123456789012345678901234.55")])
        table.flush()

        result = self.context.op.aggregate(table, "g", [("amount", "sum")],
                                           include_count=False)
        self.assertEqual([("a", Decimal("0.01"))], list(result.rows()))

        loaded = self.store.create("sums", result.fields)
        loaded.append_from(result)
        loaded.flush()
        self.assertEqual([("a", Decimal("0.01"))], list(loaded.rows()))
        self.assertEqual((40, 2),
                         loaded.fields.field("amount_sum").decimal_parameters())

    def test_non_decimal_columns_unaffected(self):
        table = self.store.create("plain",
                                  FieldList(Field("id", "integer"),
                                            Field("name", "string")))
        table.append([1, "text"])
        table.flush()

        field = self.store.get_object("plain").fields.field("name")
        self.assertEqual("string", field.storage_type)
        self.assertIsNone(field.precision)
        self.assertIsNone(field.scale)
        self.assertFalse(field.is_decimal())


if __name__ == "__main__":
    unittest.main()
