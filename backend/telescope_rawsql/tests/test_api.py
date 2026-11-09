"""P5.1 raw SQL tiles: permission, request and dashboard checks that need no ClickHouse."""

import copy
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group, User

from telescope.models import Connection, Dashboard
from telescope.rbac import permissions
from telescope.rbac.manager import RBACManager
from telescope.rbac.roles import ROLES
from telescope_rawsql import query as query_module
from telescope_rawsql.macros import expand_macros, MacroError
from telescope_rawsql.query import RawSqlError, ast_error, build_query
from telescope_rawsql.serializers import BUILDER_MAX_BYTES, RawSqlChartSerializer
from telescope_rawsql.tests.conftest import T0, client_for, grant, query, source_on
from tests.data import get_dashboard_data

RAW_SQL = permissions.Connection.RAW_SQL.value
SQL_TILE = {
    "id": "s1",
    "x": 0,
    "y": 8,
    "w": 12,
    "h": 8,
    "title": "SQL",
    "chart": {"display": "sql", "connection": 0, "sql": "SELECT 1"},
}


@pytest.fixture
def connection(db):
    return Connection.objects.create(
        kind="clickhouse",
        name="ch",
        data={"host": "127.0.0.1", "port": 1, "user": "u", "password": ""},
    )


def test_raw_sql_is_only_in_the_new_connection_role():
    holders = [
        (kind, role)
        for kind, roles in ROLES.items()
        for role, perms in roles.items()
        if RAW_SQL in perms
    ]
    assert holders == [("connection", "raw_sql_user")]
    assert RAW_SQL not in permissions.GLOBAL_TO_CONNECTION.values()


@pytest.mark.django_db
@pytest.mark.parametrize("role", [None, "owner", "editor", "viewer", "user"])
def test_query_without_raw_sql_is_403(connection, sql_user, role):
    if role:
        grant(connection, sql_user, role)
    response = query(client_for(sql_user), connection.pk, "SELECT 1")
    assert response.status_code == 403
    assert response.json()["data"] == {"code": "no_access"}


@pytest.mark.django_db
def test_superuser_needs_an_explicit_grant(connection, root_user):
    assert query(client_for(root_user), connection.pk, "SELECT 1").status_code == 403


@pytest.mark.django_db
def test_missing_and_non_clickhouse_connections_are_403(
    sql_user, docker_connection, connection
):
    grant(docker_connection, sql_user)
    client = client_for(sql_user)
    assert query(client, docker_connection.pk, "SELECT 1").status_code == 403
    assert query(client, connection.pk + 999, "SELECT 1").status_code == 403


@pytest.mark.django_db
def test_group_grant_and_revoke(connection, sql_user):
    group = Group.objects.create(name="sql")
    sql_user.groups.add(group)
    RBACManager().grant_connection_role(connection, "raw_sql_user", group=group)
    client = client_for(sql_user)
    response = client.get("/ui/v1/rawsql/connections")
    assert response.json()["data"] == [{"id": connection.pk, "name": "ch"}]
    # Granted, so the request gets past the permission check to validation.
    response = query(client, connection.pk, "SHOW TABLES")
    assert response.status_code == 200
    assert response.json()["validation"]["fields"]["sql"] == [
        "only SELECT queries are allowed"
    ]
    RBACManager().revoke_connection_role(connection, "raw_sql_user", group=group)
    assert query(client, connection.pk, "SELECT 1").status_code == 403
    assert client.get("/ui/v1/rawsql/connections").json()["data"] == []


@pytest.mark.django_db
def test_anonymous_is_refused(connection):
    from rest_framework.test import APIClient

    response = query(APIClient(), connection.pk, "SELECT 1")
    assert response.status_code in (302, 403)


@pytest.mark.django_db
@pytest.mark.parametrize(
    "sql,error",
    [
        ("", "This field may not be blank."),
        ("SHOW TABLES", "only SELECT queries are allowed"),
        ("/* x */ INSERT INTO t VALUES (1)", "only SELECT queries are allowed"),
        ("DROP TABLE logs.app", "only SELECT queries are allowed"),
        ("SELECT 1; DROP TABLE logs.app", "multi-statement queries are not allowed"),
        (
            "SELECT $__timeFilter()",
            "macro $__timeFilter expects 1 argument(s), but got 0",
        ),
        ("SELECT $__timeFilter(a", "unbalanced parentheses in macro arguments"),
    ],
)
def test_refused_before_running(connection, sql_user, sql, error):
    grant(connection, sql_user)
    response = query(client_for(sql_user), connection.pk, sql)
    assert response.status_code == 200
    assert response.json()["validation"]["fields"]["sql"] == [error]


@pytest.mark.django_db
def test_bad_variable_names_and_values(connection, sql_user):
    grant(connection, sql_user)
    client = client_for(sql_user)
    fields = query(client, connection.pk, "SELECT 1", params={"a b": "x"}).json()[
        "validation"
    ]["fields"]
    assert fields["params"] == {"a b": ["invalid variable name"]}
    fields = query(client, connection.pk, "SELECT 1", params={"a": {"k": 1}}).json()[
        "validation"
    ]["fields"]
    assert "params" in fields


def test_variables_travel_only_as_parameters():
    sql = "SELECT * FROM t WHERE s = {service:String} AND $__timeFilter(ts)"
    hostile = "x' OR 1=1 --"
    query_text, params = build_query(
        sql,
        T0,
        T0 + 3600000,
        "auto",
        {"service": hostile, "startDateMilliseconds": 0, "rawSqlLimit": 10**9},
    )
    assert hostile not in query_text
    assert params["service"] == hostile
    # Reserved names cannot override the time range or the row limit.
    assert params["startDateMilliseconds"] == T0
    assert params["endDateMilliseconds"] == T0 + 3600000
    assert params["rawSqlLimit"] == 1001
    assert params["intervalSeconds"] == 60
    assert query_text.endswith("\n) LIMIT {rawSqlLimit:UInt32}")


def test_macros_match_hyperdx():
    assert expand_macros("$__fromTime") == (
        "toDateTime(fromUnixTimestamp64Milli({startDateMilliseconds:Int64}))"
    )
    assert expand_macros("$__toTime") == (
        "toDateTime(fromUnixTimestamp64Milli({endDateMilliseconds:Int64}))"
    )
    assert expand_macros("$__interval_s") == "{intervalSeconds:Int64}"
    assert expand_macros("$__timeFilter(ts)") == (
        "ts >= toDateTime(fromUnixTimestamp64Milli({startDateMilliseconds:Int64}))"
        " AND ts <= toDateTime(fromUnixTimestamp64Milli({endDateMilliseconds:Int64}))"
    )
    # A ')' or ',' inside a literal argument does not end it; unknown names stay.
    assert expand_macros("$__timeFilter(if(a = ')', b, c))").startswith(
        "if(a = ')', b, c) >= "
    )
    assert expand_macros("$__other(1)") == "$__other(1)"
    with pytest.raises(MacroError):
        expand_macros("$__fromTime(1)")


@pytest.mark.parametrize(
    "lines,error",
    [
        (["SelectQuery", " Set"], "SETTINGS clauses are not allowed"),
        (
            [" TableExpression (children 1)", "  Function url (children 1)"],
            "table function url is not allowed",
        ),
        (
            [" TableExpression (children 1)", "  Function numbers (alias n)"],
            None,
        ),
        ([" ExpressionList", "  Function url (children 1)"], None),
    ],
)
def test_ast_error(lines, error):
    top = ["SelectWithUnionQuery (children 1)", " ExpressionList (children 1)"]
    assert ast_error(top + lines) == error
    assert ast_error(
        ["SelectWithUnionQuery (children 1)", " ExpressionList (children 2)"]
    )


def test_select_and_with_pass_the_first_word_check():
    for sql in ["select 1", "WITH x AS (SELECT 1) SELECT * FROM x", "(SELECT 1)"]:
        build_query(sql, T0, T0 + 1000, "auto", {})
    with pytest.raises(RawSqlError):
        build_query("EXPLAIN SELECT 1", T0, T0 + 1000, "auto", {})


def save(client, data, dashboard=None):
    body = {"name": "D", "description": "", "shared": True, "data": data}
    if dashboard is None:
        return client.post("/ui/v1/dashboards", body, format="json").json()
    body["version"] = dashboard["version"]
    return client.patch(
        f"/ui/v1/dashboards/{dashboard['slug']}", body, format="json"
    ).json()


def with_sql_tile(connection, sql="SELECT 1"):
    data = get_dashboard_data()
    data["tiles"] = data["tiles"][1:]
    tile = copy.deepcopy(SQL_TILE)
    tile["chart"].update(connection=connection.pk, sql=sql)
    data["tiles"].append(tile)
    return data


@pytest.mark.django_db
def test_saving_a_sql_tile_needs_raw_sql(connection, sql_user, test_user):
    response = save(client_for(test_user), with_sql_tile(connection))
    assert response["validation"]["fields"]["data"]["tiles"] == {
        "1": {"chart": {"connection": ["connection not found or no raw SQL access"]}}
    }
    grant(connection, sql_user)
    response = save(client_for(sql_user), with_sql_tile(connection))
    assert response["validation"]["result"], response
    assert response["data"]["data"]["tiles"][1]["chart"] == {
        "display": "sql",
        "connection": connection.pk,
        "sql": "SELECT 1",
        "granularity": "auto",
    }


@pytest.mark.django_db
def test_unknown_chart_keys_are_rejected(connection, sql_user):
    grant(connection, sql_user)
    data = with_sql_tile(connection)
    data["tiles"][1]["chart"]["source"] = "clickhouse"
    fields = save(client_for(sql_user), data)["validation"]["fields"]
    assert fields["data"]["tiles"]["1"]["chart"] == {"source": ["unknown key"]}


@pytest.mark.django_db
def test_an_editor_without_raw_sql_keeps_but_cannot_change_sql(
    connection, sql_user, test_user
):
    grant(connection, sql_user)
    created = save(client_for(sql_user), with_sql_tile(connection))["data"]
    # Ownership moves to a user who has no RAW_SQL; unchanged tiles still save.
    Dashboard.objects.filter(slug=created["slug"]).update(owner=test_user)
    client = client_for(test_user)
    data = created["data"]
    data["tiles"][1]["title"] = "renamed"
    updated = save(client, data, created)
    assert updated["validation"]["result"], updated
    data["tiles"][1]["chart"]["sql"] = "SELECT 2"
    updated = save(client, data, updated["data"])
    assert not updated["validation"]["result"]
    assert updated["validation"]["fields"]["data"]["tiles"]["1"]["chart"] == {
        "connection": ["connection not found or no raw SQL access"]
    }


@pytest.mark.django_db
def test_readers_without_raw_sql_do_not_see_the_sql(connection, sql_user):
    grant(connection, sql_user)
    created = save(client_for(sql_user), with_sql_tile(connection, "SELECT 'secret'"))
    reader = User.objects.create(username="reader")
    data = client_for(reader).get(f"/ui/v1/dashboards/{created['data']['slug']}")
    chart = data.json()["data"]["data"]["tiles"][1]["chart"]
    assert chart["display"] == "text" and "secret" not in str(chart)
    grant(connection, reader)
    data = client_for(reader).get(f"/ui/v1/dashboards/{created['data']['slug']}")
    assert data.json()["data"]["data"]["tiles"][1]["chart"]["sql"] == "SELECT 'secret'"


def schema(client, pk):
    return client.get(f"/ui/v1/rawsql/connections/{pk}/schema")


@pytest.mark.django_db
@pytest.mark.parametrize("role", [None, "owner", "viewer", "user"])
def test_schema_without_raw_sql_is_403(connection, sql_user, role):
    if role:
        grant(connection, sql_user, role)
    response = schema(client_for(sql_user), connection.pk)
    assert response.status_code == 403
    assert response.json()["data"] == {"code": "no_access"}
    assert schema(client_for(sql_user), connection.pk + 999).status_code == 403


@pytest.fixture
def fake_clickhouse(monkeypatch):
    calls = []

    class Connect:
        client = None

        def __init__(self, data):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def client_query(client, sql, **kw):
        calls.append({"sql": sql, **kw})
        rows = [("logs", "app", "ts", "DateTime"), ("logs", "app", "msg", "String")]
        return SimpleNamespace(result_rows=rows)

    monkeypatch.setattr(query_module, "ClickhouseConnect", Connect)
    monkeypatch.setattr(query_module, "_client_query", client_query)
    return calls


@pytest.mark.django_db
def test_schema_lists_the_columns_of_usable_tables(
    connection, sql_user, fake_clickhouse
):
    grant(connection, sql_user)
    source_on(connection, sql_user, "qb-app", "logs", "app")
    source_on(connection, sql_user, "qb-secret", "logs", "secret", role="viewer")
    response = schema(client_for(sql_user), connection.pk)
    assert response.status_code == 200
    assert response.json()["data"] == [
        {
            "database": "logs",
            "table": "app",
            "columns": [
                {"name": "ts", "type": "DateTime"},
                {"name": "msg", "type": "String"},
            ],
        }
    ]
    # Table names travel as a bound parameter; system.one is left out.
    (call,) = fake_clickhouse
    assert call["parameters"] == {"tables": [("logs", "app")]}
    assert "{tables:Array(Tuple(String, String))}" in call["sql"]
    assert call["settings"]["readonly"] == 2


@pytest.mark.django_db
def test_schema_without_usable_tables_does_not_query(
    connection, sql_user, fake_clickhouse
):
    grant(connection, sql_user)
    response = schema(client_for(sql_user), connection.pk)
    assert response.json()["data"] == []
    assert fake_clickhouse == []


BUILDER = {
    "table": "logs.app",
    "mode": "aggregate",
    "aggregates": [
        {"fn": "count", "alias": "n"},
        {"fn": "quantile", "column": "duration", "level": 0.95},
    ],
    "group_by": ["service"],
    "time_bucket": "ts",
    "filters": [{"column": "level", "op": "IN", "value": "error, warn"}],
    "time_filter": "ts",
    "order_by": [{"column": "n", "dir": "DESC"}],
    "limit": 100,
}


def chart_errors(builder):
    chart = {"display": "sql", "connection": 1, "sql": "SELECT 1", "builder": builder}
    serializer = RawSqlChartSerializer(data=chart)
    return None if serializer.is_valid() else serializer.errors


def test_builder_state_is_accepted():
    serializer = RawSqlChartSerializer(
        data={"display": "sql", "connection": 1, "sql": "SELECT 1", "builder": BUILDER}
    )
    assert serializer.is_valid(), serializer.errors
    builder = serializer.validated_data["builder"]
    assert builder["filters"][0]["op"] == "IN"
    assert builder["columns"] == [] and builder["limit"] == 100
    # The builder is optional.
    assert RawSqlChartSerializer(
        data={"display": "sql", "connection": 1, "sql": "SELECT 1"}
    ).is_valid()


@pytest.mark.parametrize(
    "changes",
    [
        {"extra": 1},
        {"mode": "graph"},
        {"limit": 1001},
        {"limit": 0},
        {"columns": "a, b"},
        {"columns": ["a"] * 101},
        {"aggregates": [{"fn": "median", "column": "a"}]},
        {"aggregates": [{"fn": "quantile", "column": "a", "level": 2}]},
        {"aggregates": [{"fn": "count", "sql": "1"}]},
        {"filters": [{"column": "a", "op": "; DROP", "value": "1"}]},
        {"filters": [{"column": "a", "op": "=", "value": "x" * 1001}]},
        {"filters": [{"column": "a", "op": "="}] * 21},
        {"order_by": [{"column": "a", "dir": "UP"}]},
        {"group_by": ["g"] * 11},
    ],
)
def test_bad_builder_shapes_are_rejected(changes):
    assert "builder" in chart_errors({**BUILDER, **changes})


def test_builder_is_an_object():
    for builder in ["SELECT 1", [BUILDER], 1]:
        assert "builder" in chart_errors(builder)


def test_oversize_builder_is_rejected():
    # Each field is within its own limits; together they are too big.
    columns = ["c" * 255] * 100
    errors = chart_errors({**BUILDER, "mode": "rows", "columns": columns})
    assert f"builder state is over {BUILDER_MAX_BYTES} bytes" in str(errors["builder"])


@pytest.mark.django_db
def test_builder_state_is_saved_with_the_tile(connection, sql_user):
    grant(connection, sql_user)
    data = with_sql_tile(connection)
    data["tiles"][1]["chart"]["builder"] = BUILDER
    response = save(client_for(sql_user), data)
    assert response["validation"]["result"], response
    chart = response["data"]["data"]["tiles"][1]["chart"]
    assert chart["sql"] == "SELECT 1"
    assert chart["builder"]["aggregates"][1] == {
        "fn": "quantile",
        "column": "duration",
        "level": 0.95,
        "alias": "",
    }
