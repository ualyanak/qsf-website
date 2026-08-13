import json
import math
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


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
        self.portal = (REPO / "assets/js/portal-demo.js").read_text()
        self.pdf = (REPO / "assets/js/demo-pdf.js").read_text()
        history = json.loads((REPO / "data/demo-portfolio-history.json").read_text())
        self.points = history["accounts"]["ahub"]["points"]

    def test_current_history_uses_stable_tick_counts(self):
        self.assertEqual(tick_indexes(len(self.points), 7), [0, 5, 9, 14, 18, 23, 27])
        self.assertEqual(tick_indexes(len(self.points), 6), [0, 5, 11, 16, 22, 27])
        self.assertEqual(tick_indexes(2, 7), [0, 1])

    def test_axis_contract_is_shared_by_dashboard_and_pdf(self):
        for source in (self.portal, self.pdf):
            self.assertRegex(source, r"const intervalCount = 5;")
            self.assertRegex(source, r"dataMin >= 9000 \? 9000")
            self.assertRegex(source, r"niceChartStep\(\(targetMax - targetMin\) / intervalCount")
            self.assertRegex(source, r"chartTickIndexes\(points\.length, (?:mobile \? 6 : 7|7)\)")

    def test_chart_uses_published_nightly_history_without_intraday_append(self):
        self.assertNotIn('historyByDate.set(today, { date: today, value: nav', self.portal)
        self.assertNotIn('kind: "live_delayed_marks"', self.portal)
        self.assertIn('point.date <= today', self.portal)

    def test_series_keeps_all_points_and_only_one_latest_marker(self):
        self.assertIn('points.map(function (point, index)', self.portal)
        self.assertEqual(self.portal.count('data-chart-latest'), 1)
        self.assertNotRegex(self.pdf, r"points\.forEach\(function \(point, index\)")
        self.assertEqual(self.pdf.count("page.drawCircle({"), 1)


if __name__ == "__main__":
    unittest.main()
