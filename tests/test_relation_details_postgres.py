"""Проверка метаданных в отдельной базе из QUERYLENS_TEST_DATABASE.

Используется временная схема и локальное соединение."""

import os
import unittest
from uuid import uuid4

from app.collectors.metadata import collect_metadata
from app.collectors.relation_details import collect_relation_details


@unittest.skipUnless(
    os.environ.get("QUERYLENS_TEST_DATABASE"), "No dedicated PostgreSQL test database configured"
)
class PostgresRelationDetailsTests(unittest.TestCase):
    def test_catalog_queries_and_query_scope_against_postgresql(self):
        import psycopg
        from psycopg import sql

        database = os.environ["QUERYLENS_TEST_DATABASE"]
        if not database.startswith("querylens_test"):
            self.fail("Integration tests require a dedicated querylens_test* database")
        schema = "ql_test_" + uuid4().hex
        identifier = sql.Identifier(schema)
        with psycopg.connect(
            dbname=database, host="127.0.0.1", autocommit=True, options="-c statement_timeout=5000"
        ) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(identifier))
            try:
                statements = [
                    "CREATE TABLE {}.parent (tenant_id int, id int, PRIMARY KEY(tenant_id,id))",
                    "CREATE TABLE {}.users (id int PRIMARY KEY, tenant_id int NOT NULL, parent_id int, "
                    "email text, active boolean, created_at timestamp, "
                    "FOREIGN KEY(tenant_id,parent_id) REFERENCES {}.parent(tenant_id,id), CHECK(id>0))",
                    "CREATE INDEX expr_partial_include ON {}.users(lower(email), created_at DESC NULLS LAST) "
                    "INCLUDE(id) WHERE active",
                    "CREATE TABLE {}.empty (id int)",
                    "INSERT INTO {}.parent VALUES(1,1)",
                    "INSERT INTO {}.users SELECT g,1,1,'user'||g,true,now() FROM generate_series(1,20) g",
                    "ANALYZE {}.users",
                ]
                for statement in statements:
                    connection.execute(sql.SQL(statement).format(identifier, identifier))
                query = (
                    sql.SQL("SELECT email FROM {}.users").format(identifier).as_string(connection)
                )
                report = collect_metadata(connection, scope="query", sql=query)
                self.assertEqual(len(report["tables"]), 1)
                table = report["tables"][0]
                self.assertEqual([f["name"] for f in table["fields"]], ["email"])
                self.assertEqual(len(table["all_fields"]), 6)
                sources = table["diagnostics"]["sources"]
                self.assertTrue(all(x["status"] == "ok" for x in sources.values()), sources)
                index = next(
                    x for x in sources["indexes"]["data"] if x["name"] == "expr_partial_include"
                )
                self.assertEqual(index["keys"][0]["expression"], "lower(email)")
                self.assertEqual(index["keys"][1]["direction"], "DESC")
                self.assertFalse(index["keys"][1]["nulls_first"])
                self.assertEqual(index["included_columns"], ["id"])
                self.assertEqual(index["predicate"], "active")
                foreign_key = next(x for x in sources["constraints"]["data"] if x["kind"] == "f")
                self.assertEqual(foreign_key["columns"], ["tenant_id", "parent_id"])
                self.assertEqual(foreign_key["referenced_columns"], ["tenant_id", "id"])
                self.assertEqual(sources["relation"]["data"]["estimated_rows"], 20)
                oid = connection.execute(
                    sql.SQL("SELECT {}::regclass::oid").format(sql.Literal(f"{schema}.empty"))
                ).fetchone()[0]
                empty = collect_relation_details(
                    connection, {"oid": oid, "schema": schema, "name": "empty"}
                )
                self.assertEqual(empty["sources"]["indexes"]["status"], "ok")
                self.assertEqual(empty["sources"]["indexes"]["data"], [])
                self.assertEqual(empty["sources"]["column_statistics"]["status"], "unavailable")
            finally:
                connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(identifier))


if __name__ == "__main__":
    unittest.main()
