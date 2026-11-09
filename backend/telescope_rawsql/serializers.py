import json

from rest_framework import serializers

from telescope.fetchers.chart import GRANULARITIES, VARIABLE_NAME_RE
from telescope.models import Connection
from telescope_otel.metrics_sql import metric_tables
from telescope.rbac import permissions
from telescope.rbac.manager import RBACManager
from telescope.utils import parse_time
from telescope_rawsql.query import (
    RAW_SQL_DISPLAY,
    RAW_SQL_MAX_LENGTH,
    RAW_SQL_MAX_ROWS,
)

rbac_manager = RBACManager()

NO_ACCESS = "connection not found or no raw SQL access"


def raw_sql_connection(user, pk):
    """The ClickHouse connection `user` may run raw SQL on, or None."""
    try:
        connection = rbac_manager.get_connection(
            user, pk, [permissions.Connection.RAW_SQL.value]
        )
    except Connection.DoesNotExist:
        return None
    return connection if connection.kind == "clickhouse" else None


def raw_sql_tables(user, connection) -> set:
    """The "db.table" names raw SQL may read: the tables of the sources `user` may use on `connection`."""
    tables = {"system.one"}
    for source in rbac_manager.get_sources(
        user,
        source_filter={"conn_id": connection.pk},
        required_permissions=[permissions.Source.USE.value],
    ):
        if source.kind != "clickhouse":
            continue
        database = source.data.get("database")
        names = [source.data.get("table"), *metric_tables(source).values()]
        tables.update(f"{database}.{name}" for name in names if name)
    return tables


class _StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if isinstance(data, dict):
            unknown = sorted(set(data) - set(self.fields))
            if unknown:
                raise serializers.ValidationError(
                    {key: ["unknown key"] for key in unknown}
                )
        return super().to_internal_value(data)


BUILDER_MAX_BYTES = 20000
BUILDER_AGGS = ["count", "uniq", "sum", "avg", "min", "max", "quantile"]
BUILDER_OPS = [
    "=",
    "!=",
    "<",
    "<=",
    ">",
    ">=",
    "LIKE",
    "NOT LIKE",
    "IN",
    "NOT IN",
    "IS NULL",
    "IS NOT NULL",
]


def _name(**kw):
    return serializers.CharField(max_length=255, **kw)


def _names(max_length):
    return serializers.ListField(
        child=_name(), required=False, default=list, max_length=max_length
    )


class BuilderAggregateSerializer(_StrictSerializer):
    fn = serializers.ChoiceField(choices=BUILDER_AGGS)
    column = _name(required=False, allow_blank=True, default="")
    level = serializers.FloatField(required=False, min_value=0, max_value=1)
    alias = _name(required=False, allow_blank=True, default="")


class BuilderFilterSerializer(_StrictSerializer):
    column = _name(allow_blank=True)
    op = serializers.ChoiceField(choices=BUILDER_OPS)
    value = serializers.CharField(
        required=False, allow_blank=True, default="", max_length=1000
    )


class BuilderOrderSerializer(_StrictSerializer):
    column = _name(allow_blank=True)
    dir = serializers.ChoiceField(choices=["ASC", "DESC"])


class RawSqlBuilderSerializer(_StrictSerializer):
    """The query builder's form; it only reopens the builder, the tile runs `sql`."""

    table = serializers.CharField(max_length=512, allow_blank=True)
    mode = serializers.ChoiceField(choices=["rows", "aggregate"])
    columns = _names(100)
    aggregates = serializers.ListField(
        child=BuilderAggregateSerializer(), required=False, default=list, max_length=20
    )
    group_by = _names(10)
    time_bucket = _name(required=False, allow_blank=True, default="")
    filters = serializers.ListField(
        child=BuilderFilterSerializer(), required=False, default=list, max_length=20
    )
    time_filter = _name(required=False, allow_blank=True, default="")
    order_by = serializers.ListField(
        child=BuilderOrderSerializer(), required=False, default=list, max_length=5
    )
    limit = serializers.IntegerField(min_value=1, max_value=RAW_SQL_MAX_ROWS)

    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("Expected an object")
        if len(json.dumps(data)) > BUILDER_MAX_BYTES:
            raise serializers.ValidationError(
                f"builder state is over {BUILDER_MAX_BYTES} bytes"
            )
        return super().to_internal_value(data)


class RawSqlChartSerializer(_StrictSerializer):
    display = serializers.ChoiceField(choices=[RAW_SQL_DISPLAY])
    connection = serializers.IntegerField()
    sql = serializers.CharField(max_length=RAW_SQL_MAX_LENGTH)
    granularity = serializers.ChoiceField(
        choices=GRANULARITIES, required=False, default="auto"
    )
    builder = RawSqlBuilderSerializer(required=False)


def raw_sql_tile_error(user, tile, old: set):
    """Saving a new or changed raw SQL tile needs RAW_SQL on its connection."""
    chart = tile["chart"]
    if raw_sql_key(tile) in old or raw_sql_connection(user, chart["connection"]):
        return None
    return {"connection": [NO_ACCESS]}


def raw_sql_key(tile):
    chart = tile.get("chart") or {}
    return ("sql", tile.get("id"), chart.get("connection"), chart.get("sql"))


class RawSqlRequestSerializer(serializers.Serializer):
    _from = serializers.CharField()
    to = serializers.CharField()
    sql = serializers.CharField(max_length=RAW_SQL_MAX_LENGTH)
    granularity = serializers.ChoiceField(
        choices=GRANULARITIES, required=False, default="auto"
    )
    params = serializers.DictField(required=False, default=dict)

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = fields.pop("_from")
        return fields

    def validate_from(self, value):
        value, error = parse_time(value)
        if error:
            raise serializers.ValidationError(error)
        return value

    def validate_to(self, value):
        value, error = parse_time(value)
        if error:
            raise serializers.ValidationError(error)
        return value

    def validate_params(self, value):
        # Lazy import: telescope.serializers.dashboard imports this module.
        from telescope.serializers.dashboard import ParamValueField

        field = ParamValueField()
        result = {}
        for name, item in value.items():
            if not VARIABLE_NAME_RE.match(name):
                raise serializers.ValidationError({name: ["invalid variable name"]})
            result[name] = field.to_internal_value(item)
        return result

    def validate(self, attrs):
        if attrs["from"] >= attrs["to"]:
            raise serializers.ValidationError({"to": ["must be after from"]})
        return attrs
