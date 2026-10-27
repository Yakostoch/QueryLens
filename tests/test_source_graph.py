import unittest

from app.analyzers.diagnostic_router import route_candidates
from app.analyzers.sql_features import extract_sql_features


class SourceGraphTests(unittest.TestCase):
    def check(self, sql, status):
        syntax = extract_sql_features(sql)
        self.assertEqual(syntax["source_connection_graph"]["scopes"][0]["status"], status)
        f = next(f for f in route_candidates(syntax)["findings"] if f["rule_id"] == "AP12")
        self.assertEqual(
            f["status"],
            dict(
                connected="no_syntax_candidate",
                disconnected="candidate",
                unknown="insufficient_data",
            )[status],
        )

    def test_connected_on_where_using_and_selfjoin(self):
        for sql in (
            "SELECT a.x FROM a JOIN b ON a.x=b.x",
            "SELECT a.x FROM a,b WHERE a.x=b.x",
            "SELECT a.x FROM a CROSS JOIN b WHERE a.x=b.x",
            "SELECT a.x FROM a JOIN b USING(x)",
            "SELECT a.x FROM a JOIN a b ON a.x=b.x",
        ):
            with self.subTest(sql=sql):
                self.check(sql, "connected")

    def test_independent_cross_and_one_sided_condition(self):
        for sql in (
            "SELECT a.x FROM a CROSS JOIN b",
            "SELECT a.x FROM a,b",
            "SELECT a.x FROM a JOIN b ON TRUE",
            "SELECT a.x FROM a JOIN b ON a.x=1",
        ):
            with self.subTest(sql=sql):
                self.check(sql, "disconnected")

    def test_third_independent_source_is_retained(self):
        self.check("SELECT a.x FROM a JOIN b ON a.x=b.x CROSS JOIN c", "disconnected")

    def test_or_does_not_invent_a_connection(self):
        self.check("SELECT a.x FROM a JOIN b ON a.x=b.x OR a.x=1", "unknown")
        self.check("SELECT a.x FROM a JOIN b ON a.x=b.x OR a.y=b.y", "connected")

    def test_and_keeps_proven_connection(self):
        self.check("SELECT a.x FROM a,b WHERE a.x=b.x AND (a.y=1 OR b.y=2)", "connected")

    def test_unknown_lineage_and_outer_semantics(self):
        for sql in (
            "SELECT a.x FROM a LEFT JOIN b ON FALSE",
            "SELECT a.x FROM a NATURAL JOIN b",
            "SELECT a.x FROM a JOIN b ON x=y",
            "SELECT a.x FROM a CROSS JOIN LATERAL (SELECT a.x) b",
            "SELECT a.x FROM a,b WHERE EXISTS(SELECT 1 FROM c WHERE c.x=a.x AND c.x=b.x)",
        ):
            with self.subTest(sql=sql):
                self.check(sql, "unknown")


if __name__ == "__main__":
    unittest.main()
