import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'backend'))
from engine import football_probabilities,basketball_probabilities,build_tickets
class EngineTests(unittest.TestCase):
 def test_football(self):
  p=football_probabilities(1.5,1.2);self.assertAlmostEqual(p['HOME']+p['DRAW']+p['AWAY'],1,5)
 def test_basketball(self):
  p=basketball_probabilities(110,105);self.assertAlmostEqual(p['HOME']+p['AWAY'],1,6)
 def test_distinct_fixtures(self):
  p=[{'event_id':'x','odds':2,'probability':.6,'edge':.2,'market':'HOME'},{'event_id':'x','odds':2,'probability':.6,'edge':.2,'market':'AWAY'}]
  self.assertTrue(all(len(t['legs'])==1 for g in build_tickets(p).values() for t in g))
