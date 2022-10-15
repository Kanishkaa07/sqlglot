from __future__ import annotations

import typing as t
from copy import copy

import sqlglot
from sqlglot import expressions as exp
from sqlglot.schema import MutableSchema

if t.TYPE_CHECKING:
    from sqlglot.dataframe.sql.dataframe import DataFrame
    from sqlglot.dataframe.sql.session import SparkSession


class DataFrameReader:
    def __init__(self, spark: SparkSession):
        self.spark = spark

    def table(self, tableName: str) -> DataFrame:
        from sqlglot.dataframe.sql.dataframe import DataFrame

        assert isinstance(sqlglot.schema, MutableSchema)
        sqlglot.schema.add_table(tableName)
        return DataFrame(self.spark, exp.Select().from_(tableName).select(*sqlglot.schema.column_names(tableName)))


class DataFrameWriter:
    def __init__(
        self, df: DataFrame, spark: t.Optional[SparkSession] = None, mode: t.Optional[str] = None, by_name: bool = False
    ):
        self._df = df
        self._spark = spark or df.spark
        self._mode = mode
        self._by_name = by_name

    def copy(self, **kwargs) -> DataFrameWriter:
        kwargs = {**{k: copy(v) for k, v in vars(self).copy().items()}, **kwargs}
        return DataFrameWriter(**{k[1:] if k.startswith("_") else k: v for k, v in kwargs.items()})

    def sql(self, **kwargs) -> t.List[str]:
        return self._df.sql(**kwargs)

    def mode(self, saveMode: t.Optional[str]) -> DataFrameWriter:
        return self.copy(_mode=saveMode)

    @property
    def byName(self):
        return self.copy(by_name=True)

    def insertInto(self, tableName: str, overwrite: t.Optional[bool] = None) -> DataFrameWriter:
        output_expression_container = exp.Insert(
            **{
                "this": exp.to_table(tableName),
                "overwrite": overwrite,
            }
        )
        df = self._df.copy(output_expression_container=output_expression_container)
        if self._by_name:
            assert isinstance(sqlglot.schema, MutableSchema)
            columns = sqlglot.schema.column_names(tableName, only_visible=True)
            df = df._convert_leaf_to_cte().select(*columns)

        return self.copy(_df=df)

    def saveAsTable(self, name: str, format: t.Optional[str] = None, mode: t.Optional[str] = None):
        if format is not None:
            raise NotImplementedError("Providing Format in the save as table is not supported")
        exists, replace, mode = None, None, mode or str(self._mode)
        if mode == "append":
            return self.insertInto(name)
        if mode == "ignore":
            exists = True
        if mode == "overwrite":
            replace = True
        output_expression_container = exp.Create(
            this=exp.to_table(name),
            kind="TABLE",
            exists=exists,
            replace=replace,
        )
        return self.copy(_df=self._df.copy(output_expression_container=output_expression_container))
