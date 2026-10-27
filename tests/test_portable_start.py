import unittest
from importlib import metadata
from unittest.mock import patch

from test_analysis_presentation import bundle

from app.services.analysis_presentation import summarize
from run_querylens import child_environment, dependency_issues


class ShortTimeTests(unittest.TestCase):
    def brief(self, value):
        payload = bundle()
        payload["diagnostic_tree"].update(measurement_status="measured", execution_time_ms=value)
        return summarize(payload)["brief"]

    def test_small_measured_times_do_not_round_to_zero(self):
        for value in (0, 0.001, 0.049, 0.099):
            with self.subTest(value=value):
                self.assertIn("менее 0,1 мс", self.brief(value))
                self.assertNotIn("0.0 мс", self.brief(value))

    def test_regular_time_keeps_existing_format(self):
        self.assertIn("0.1 мс", self.brief(0.1))
        self.assertIn("123.4 мс", self.brief(123.4))

    def test_invalid_measurements_do_not_become_time_facts(self):
        for value in (-1, True, None, float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertIn("не измерялось", self.brief(value))


class LauncherTests(unittest.TestCase):
    def test_environment_cleanup_is_only_for_child_process(self):
        original = dict(
            QT_PLUGIN_PATH="conda/plugins", QT_QPA_PLATFORM="other", PATH="keep", PGPASSFILE="keep"
        )
        clean = child_environment(original)
        self.assertNotIn("QT_PLUGIN_PATH", clean)
        self.assertNotIn("QT_QPA_PLATFORM", clean)
        self.assertEqual(clean["PATH"], "keep")
        self.assertEqual(clean["PGPASSFILE"], "keep")
        self.assertIn("QT_PLUGIN_PATH", original)

    def test_missing_dependency_is_reported(self):
        with patch("run_querylens.metadata.version", side_effect=metadata.PackageNotFoundError):
            self.assertEqual(len(dependency_issues()), 4)

    def test_supported_and_out_of_range_dependencies(self):
        versions = {"PySide6": "6.8.3", "psycopg": "3.2.1", "psutil": "7.2.2", "sqlglot": "30.25.0"}
        with patch("run_querylens.metadata.version", side_effect=versions.get):
            self.assertEqual(dependency_issues(), [])
            versions["sqlglot"] = "31.0.0"
            self.assertIn("sqlglot", dependency_issues()[0])
