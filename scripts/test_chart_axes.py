import json
import math
import re
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
BENCHMARK_IDS = ["spy", "gold-gld", "btc-usd"]


def tick_indexes(length, maximum):
    count = min(length, maximum)
    if count <= 0:
        return []
    if count == 1:
        return [0]
    indexes = []
    for tick in range(count):
        index = math.floor(tick * (length - 1) / (count - 1) + 0.5)
        if not indexes or indexes[-1] != index:
            indexes.append(index)
    return indexes


class ChartAxesTests(unittest.TestCase):
    def setUp(self):
        self.portal = (REPO / "assets/js/portal-demo.js").read_text(encoding="utf-8")
        history = json.loads((REPO / "data/demo-portfolio-history.json").read_text(encoding="utf-8"))
        self.account = history["accounts"]["ahub"]
        self.points = self.account["points"]
        self.comparisons = self.account.get("comparisons", [])

    def test_tick_counts_adapt_without_depending_on_history_length(self):
        for maximum in (7, 6):
            indexes = tick_indexes(len(self.points), maximum)
            self.assertEqual(len(indexes), min(len(self.points), maximum))
            self.assertEqual(indexes[0], 0)
            self.assertEqual(indexes[-1], len(self.points) - 1)
            self.assertEqual(indexes, sorted(set(indexes)))
        self.assertEqual(tick_indexes(2, 7), [0, 1])

    def test_history_exposes_three_aligned_normalized_benchmarks(self):
        self.assertEqual([series["id"] for series in self.comparisons], BENCHMARK_IDS)
        qsf_dates = [point["date"] for point in self.points]
        opening_value = float(self.account["opening_nav"])
        self.assertEqual(opening_value, 9900.0)
        for series in self.comparisons:
            with self.subTest(series=series["id"]):
                benchmark_points = series["points"]
                self.assertEqual([point["date"] for point in benchmark_points], qsf_dates)
                self.assertAlmostEqual(float(benchmark_points[0]["value"]), opening_value, places=2)
                self.assertTrue(all(math.isfinite(float(point["value"])) for point in benchmark_points))

    def test_dashboard_builds_qsf_plus_benchmarks_on_one_axis(self):
        self.assertIn("comparisonSeries: comparisonSeries", self.portal)
        self.assertIn("renderChart(view.comparisonSeries, view.currency, view.openingNav)", self.portal)
        self.assertIn("const axisValues = [];", self.portal)
        self.assertRegex(
            self.portal,
            r"chartSeries\.forEach\(function \(series\) \{\s*series\.points\.forEach\(function \(point\) \{ axisValues\.push\(point\.value\); \}\);",
        )
        self.assertIn("const axis = chartAxis(axisValues);", self.portal)
        self.assertIn("const intervalCount = 5;", self.portal)
        self.assertIn("dataMin >= 9000 ? 9000", self.portal)
        self.assertIn("chartTickIndexes(points.length, mobile ? 6 : 7)", self.portal)

    def test_renderer_has_four_distinct_series_and_latest_markers_without_area_fill(self):
        for series_id in ["qsf", *BENCHMARK_IDS]:
            self.assertIn(f'"{series_id}"', self.portal)
        self.assertIn('"btc-usd": { label: "Bitcoin"', self.portal)
        self.assertIn('dash: "7 5"', self.portal)
        self.assertNotIn("areaPath", self.portal)
        self.assertNotIn("rgba(201, 162, 79, .17)", self.portal)
        self.assertEqual(self.portal.count('"data-chart-latest"'), 1)
        self.assertRegex(
            self.portal,
            re.compile(
                r"drawOrder\.forEach\(function \(series\) \{.*?const marker = svgNode\(\"circle\".*?svg\.append\(marker\);",
                re.DOTALL,
            ),
        )

    def test_chart_remains_completed_nightly_only(self):
        self.assertNotIn('historyByDate.set(today, { date: today, value: nav', self.portal)
        self.assertNotIn('kind: "live_delayed_marks"', self.portal)
        self.assertIn("point.date <= today", self.portal)
        self.assertIn("Latest night ", self.portal)


if __name__ == "__main__":
    unittest.main()
