import unittest

from scripts.tests.simulate_ai_review_disposition import scenarios


class ScenarioTests(unittest.TestCase):
  def test_simulated_bot_rounds_and_second_human_permissions(self):
    for name, actual, expected in scenarios():
      with self.subTest(scenario=name):
        self.assertEqual(actual, expected)
