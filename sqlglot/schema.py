import abc
from copy import copy

from sqlglot import expressions as exp
from sqlglot.errors import OptimizeError
from sqlglot.helper import csv_reader, ensure_table


class Schema(abc.ABC):
    """Abstract base class for database schemas"""

    @abc.abstractmethod
    def column_names(self, table, only_visible=False):
        """
        Get the column names for a table.
        Args:
            table (sqlglot.expressions.Table): Table expression instance
            only_visible (bool): Whether to include invisible columns
        Returns:
            list[str]: list of column names
        """

    @abc.abstractmethod
    def get_column_type(self, table, column):
        """
        Get the exp.DataType type of a column in the schema.

        Args:
            table (sqlglot.expressions.Table): The source table.
            column (sqlglot.expressions.Column): The target column.
        Returns:
            sqlglot.expressions.DataType.Type: The resulting column type.
        """


class MutableSchema(Schema):
    """
    Based on MappingSchema but mutable so you can add tables over time.

    Args:
        schema (dict): Mapping in one of the following forms:
            1. {table: {col: type}}
            2. {db: {table: {col: type}}}
            3. {catalog: {db: {table: {col: type}}}}
            4. None - Tables will be added later
        visible (dict): Optional mapping of which columns in the schema are visible. If not provided, all columns
            are assumed to be visible. The nesting should mirror that of the schema:
            1. {table: set(*cols)}}
            2. {db: {table: set(*cols)}}}
            3. {catalog: {db: {table: set(*cols)}}}}
        dialect (str): The dialect to be used for custom type mappings.
    """

    def __init__(self, schema=None, visible=None, dialect=None):
        self.schema = schema or {}
        self.visible = visible
        self.dialect = dialect
        self._type_mapping_cache = {}
        self.supported_table_args = []
        self.forbidden_args = set()
        if self.schema:
            self._initialize_supported_args()

    @classmethod
    def from_mapping_schema(cls, mapping_schema):
        return MutableSchema(
            schema=mapping_schema.schema, visible=mapping_schema.visible, dialect=mapping_schema.dialect
        )

    def copy(self, **kwargs):
        kwargs = {**{"schema": copy(self.schema)}, **kwargs}
        return MutableSchema(**kwargs)

    def add_table(self, table, column_mapping=None):
        """
        Registers the table to be a known schema to be accessed later.

        Args:
            table (sqlglot.expressions.Table|str): Table expression instance or string representing the table
            column_mapping (dict|str|sqlglot.dataframe.sql.types.StructType|list): A column mapping that describes the structure of the table
        """
        if column_mapping is None:
            return
        table = ensure_table(table)
        self._validate_table(table)
        column_mapping = ensure_column_mapping(column_mapping)
        _nested_set(
            self.schema,
            [table.text(p) for p in self.supported_table_args or self._get_table_args_from_table(table)],
            column_mapping,
        )
        self._initialize_supported_args()

    def _get_table_args_from_table(self, table):
        if table.args.get("catalog") is not None:
            return "catalog", "db", "this"
        if table.args.get("db") is not None:
            return "db", "this"
        return ("this",)

    def _validate_table(self, table):
        if not self.supported_table_args and isinstance(table, exp.Table):
            return
        for forbidden in self.forbidden_args:
            if table.text(forbidden):
                raise ValueError(f"Schema doesn't support {forbidden}. Received: {table.sql()}")
        for expected in self.supported_table_args:
            if not table.text(expected):
                raise ValueError(f"Table is expected to have {expected}. Received: {table.sql()} ")

    def column_names(self, table, only_visible=False):
        table = ensure_table(table)
        if not isinstance(table.this, exp.Identifier):
            return fs_get(table)

        args = tuple(table.text(p) for p in self.supported_table_args)

        for forbidden in self.forbidden_args:
            if table.text(forbidden):
                raise ValueError(f"Schema doesn't support {forbidden}. Received: {table.sql()}")

        columns = list(_nested_get(self.schema, *zip(self.supported_table_args, args)))
        if not only_visible or not self.visible:
            return columns

        visible = _nested_get(self.visible, *zip(self.supported_table_args, args))
        return [col for col in columns if col in visible]

    def get_column_type(self, table, column):
        try:
            schema_type = self.schema.get(table.name, {}).get(column.name).upper()
            return self._convert_type(schema_type)
        except:
            raise OptimizeError(f"Failed to get type for column {column.sql()}")

    def _convert_type(self, schema_type):
        """
        Convert a type represented as a string to the corresponding exp.DataType.Type object.
        Args:
            schema_type (str): The type we want to convert.
        Returns:
            sqlglot.expressions.DataType.Type: The resulting expression type.
        """
        if schema_type not in self._type_mapping_cache:
            try:
                self._type_mapping_cache[schema_type] = exp.maybe_parse(
                    schema_type, into=exp.DataType, dialect=self.dialect
                ).this
            except AttributeError:
                raise OptimizeError(f"Failed to convert type {schema_type}")

        return self._type_mapping_cache[schema_type]

    def _initialize_supported_args(self):
        if not self.supported_table_args:
            depth = _dict_depth(self.schema)

            if not depth or depth == 1:  # {}
                self.supported_table_args = []
            elif depth == 2:  # {table: {col: type}}
                self.supported_table_args = ("this",)
            elif depth == 3:  # {db: {table: {col: type}}}
                self.supported_table_args = ("db", "this")
            elif depth == 4:  # {catalog: {db: {table: {col: type}}}}
                self.supported_table_args = ("catalog", "db", "this")
            else:
                raise OptimizeError(f"Invalid schema shape. Depth: {depth}")

            self.forbidden_args = {"catalog", "db", "this"} - set(self.supported_table_args)


class MappingSchema(MutableSchema):
    """
    Schema based on a nested mapping.

    Args:
        schema (dict): Mapping in one of the following forms:
            1. {table: {col: type}}
            2. {db: {table: {col: type}}}
            3. {catalog: {db: {table: {col: type}}}}
        visible (dict): Optional mapping of which columns in the schema are visible. If not provided, all columns
            are assumed to be visible. The nesting should mirror that of the schema:
            1. {table: set(*cols)}}
            2. {db: {table: set(*cols)}}}
            3. {catalog: {db: {table: set(*cols)}}}}
        dialect (str): The dialect to be used for custom type mappings.
    """

    def add_table(self, table, column_mapping=None):
        raise NotImplementedError("Use `MutableSchema` if you want a to be able to add tables")


def ensure_schema(schema, schema_klass):
    if isinstance(schema, Schema):
        return schema

    return schema_klass(schema)


def ensure_column_mapping(mapping):
    if isinstance(mapping, dict):
        return mapping
    elif isinstance(mapping, str):
        col_name_type_strs = [x.strip() for x in mapping.split(",")]
        return {
            name_type_str.split(":")[0].strip(): name_type_str.split(":")[1].strip()
            for name_type_str in col_name_type_strs
        }
    # Check if mapping looks like a DataFrame StructType
    elif hasattr(mapping, "simpleString"):
        return {struct_field.name: struct_field.dataType.simpleString() for struct_field in mapping}
    elif isinstance(mapping, list):
        return {x.strip(): None for x in mapping}
    raise ValueError(f"Invalid mapping provided: {type(mapping)}")


def fs_get(table):
    name = table.this.name

    if name.upper() == "READ_CSV":
        with csv_reader(table) as reader:
            return next(reader)

    raise ValueError(f"Cannot read schema for {table}")


def _nested_get(d, *path):
    """
    Get a value for a nested dictionary.

    Args:
        d (dict): dictionary
        *path (tuple[str, str]): tuples of (name, key)
            `key` is the key in the dictionary to get.
            `name` is a string to use in the error if `key` isn't found.
    """
    for name, key in path:
        d = d.get(key)
        if d is None:
            name = "table" if name == "this" else name
            raise ValueError(f"Unknown {name}")
    return d


def _nested_set(d, keys, value):
    """
    In-place set a value for a nested dictionary

    Ex:
        >>> _nested_set({}, ["top_key", "second_key"], "value")
        {'top_key': {'second_key': 'value'}}
        >>> _nested_set({"top_key": {"third_key": "third_value"}}, ["top_key", "second_key"], "value")
        {'top_key': {'third_key': 'third_value', 'second_key': 'value'}}

    d (dict): dictionary
    keys (Iterable[str]): ordered iterable of keys that makeup path to value
    value (Any): The value to set in the dictionary for the given key path
    """
    if not keys:
        return
    if len(keys) == 1:
        d[keys[0]] = value
        return
    subd = d
    for key in keys[:-1]:
        if key not in subd:
            subd = subd.setdefault(key, {})
        else:
            subd = subd[key]
    subd[keys[-1]] = value
    return d


def _dict_depth(d):
    """
    Get the nesting depth of a dictionary.

    For example:
        >>> _dict_depth(None)
        0
        >>> _dict_depth({})
        1
        >>> _dict_depth({"a": "b"})
        1
        >>> _dict_depth({"a": {}})
        2
        >>> _dict_depth({"a": {"b": {}}})
        3

    Args:
        d (dict): dictionary
    Returns:
        int: depth
    """
    try:
        return 1 + _dict_depth(next(iter(d.values())))
    except AttributeError:
        # d doesn't have attribute "values"
        return 0
    except StopIteration:
        # d.values() returns an empty sequence
        return 1
