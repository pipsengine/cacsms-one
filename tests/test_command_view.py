import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from command_view import ticket_confidence, ticket_probability


class TicketProbabilityTests(unittest.TestCase):
    def test_independent_joint_probability_is_reported(self):
        self.assertEqual(ticket_probability('independent', 0.425), 42.5)

    def test_correlated_outcomes_do_not_receive_a_probability(self):
        self.assertIsNone(ticket_probability('unverified', 0.425))
        self.assertIsNone(ticket_probability('unverified', None))
        self.assertIsNone(ticket_probability('independent', None))

    def test_confidence_uses_only_a_complete_stored_set(self):
        self.assertEqual(ticket_confidence([0.91, 0.74], 2), 74.0)
        self.assertIsNone(ticket_confidence([0.91, None], 2))
        self.assertIsNone(ticket_confidence([0.91], 2))


if __name__ == '__main__':
    unittest.main()
