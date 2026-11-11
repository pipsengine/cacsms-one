"""Result linking: real spellings from the stored feeds, and the guards that keep wrong scores out."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

import result_linker as rl


def fx(home, away, starts='2026-11-07T17:30:00+00:00'):
    return {'id': 'f', 'home': home, 'away': away, 'starts_at': starts}


def res(home, away, hs=1, as_=0, starts='2026-11-07T17:30:00+00:00', id_='r'):
    return {'id': id_, 'home': home, 'away': away, 'starts_at': starts, 'home_score': hs, 'away_score': as_}


class NameTests(unittest.TestCase):
    def test_short_forms_and_spellings(self):
        self.assertTrue(rl.same_team('Man City', 'Manchester City'))
        self.assertTrue(rl.same_team('Sheffield United FC', 'Sheffield Utd'))
        self.assertTrue(rl.same_team('Wolverhampton Wanderers FC', 'Wolves'))
        self.assertTrue(rl.same_team('Stade Brestois 29', 'Brest'))
        self.assertTrue(rl.same_team('1 FC Nuremberg', '1. FC Nürnberg'))
        self.assertTrue(rl.same_team('Lillestroem SK', 'Lillestrøm'))
        self.assertTrue(rl.same_team('Borussia M´gladbach', 'Borussia Mönchengladbach'))

    def test_different_clubs_never_match(self):
        self.assertFalse(rl.same_team('Man City', 'Manchester United'))
        self.assertFalse(rl.same_team('Real Madrid', 'Atletico Madrid'))

    def test_youth_women_reserve_and_virtual_sides_never_match_the_senior_side(self):
        self.assertFalse(rl.same_team('Holstein Kiel', 'Holstein Kiel U19'))
        self.assertFalse(rl.same_team('Aston Villa', 'Aston Villa W'))
        self.assertFalse(rl.same_team('Benfica', 'SL Benfica B'))
        self.assertFalse(rl.same_team('Sunderland AFC Srl', 'Sunderland'))
        self.assertTrue(rl.same_team('Bayern Munich W', 'Bayern München Women'))


class MatchTests(unittest.TestCase):
    def test_two_sources_agreeing_on_the_score_are_one_match(self):
        found, how = rl.find_match(fx('Nottingham Forest', 'Man City'), [
            res('Nottingham Forest FC', 'Manchester City FC', 3, 1, id_='a'), res('Nottingham', 'Manchester City', 3, 1, id_='b')])
        self.assertEqual((how, found['home_score']), ('names', 3))

    def test_sources_disagreeing_on_the_score_link_nothing(self):
        self.assertEqual(rl.find_match(fx('Nottingham Forest', 'Man City'), [
            res('Nottingham Forest FC', 'Manchester City FC', 3, 1), res('Nottingham', 'Manchester City', 1, 1)])[1], 'ambiguous')

    def test_kickoff_pass_needs_same_time_and_a_distinctive_word_for_both_teams(self):
        found, how = rl.find_match(fx('Hertha BSC', 'VfL Wolfsburg', '2026-11-07T19:30:00+00:00'),
                                   [res('Hertha Berlin', 'Wolfsburg', 2, 1, '2026-11-07T19:30:00+00:00')])
        self.assertEqual(how, 'kickoff')
        # Two hours apart: not the same game.
        self.assertEqual(rl.find_match(fx('Hertha BSC', 'VfL Wolfsburg', '2026-11-07T19:30:00+00:00'),
                                       [res('Hertha Berlin', 'Wolfsburg', 2, 1, '2026-11-07T21:30:00+00:00')])[1], 'unmatched')
        # Only a generic word in common ("Real"): never enough.
        self.assertEqual(rl.find_match(fx('Real Sociedad', 'Getafe CF'), [res('Real Madrid', 'Getafe')])[1], 'unmatched')

    def test_swapped_home_and_away_is_ambiguous(self):
        self.assertEqual(rl.find_match(fx('Porto', 'Napoli'), [res('FC Porto', 'Napoli'), res('Napoli', 'Porto')])[1], 'ambiguous')


class StaleSeasonTests(unittest.TestCase):
    def test_old_season_redated_into_the_future_is_history(self):
        kickoff = datetime(2026, 11, 23, tzinfo=timezone.utc)
        self.assertTrue(rl.stale_season('UEFA Champions League 2021/22', kickoff))
        self.assertFalse(rl.stale_season('Fußball-Bundesliga 2026/2027', kickoff))
        self.assertFalse(rl.stale_season('England Premier League', kickoff))
        self.assertFalse(rl.stale_season('Brasileirao 2026', kickoff))


if __name__ == '__main__':
    unittest.main()
