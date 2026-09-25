import unittest
from dataclasses import replace
import numpy as np
import pandas as pd

from hsights.games.portfolio_challenge.engine import Game, Rules


def prices(future=None):
    daily = np.zeros((8,3))
    if future is not None:
        daily[4:4+len(future)] = future
    return pd.DataFrame(100*np.cumprod(1+daily,axis=0),
                        index=pd.bdate_range('2020-01-01',periods=8),columns=['A','B','C'])


class EngineTests(unittest.TestCase):
    rules = Rules(lookback=3,horizon=3,risk_cap=10,cost_bps=0,target=.01)

    def game(self, frame=None, **changes):
        frame = prices() if frame is None else frame
        return Game(frame,frame.index[3],replace(self.rules,**changes))

    def test_drift_and_value(self):
        game = self.game(prices([[.1,0,0]]))
        game.allocate([.5,.25,.25])
        game.toggle()
        game.step()
        self.assertAlmostEqual(game.value,105000)
        np.testing.assert_allclose(game.weights,[55000/105000,25000/105000,25000/105000])

    def test_purchase_and_rotation_cost(self):
        game = self.game(cost_bps=10)
        game.allocate([1,0,0])
        self.assertAlmostEqual(game.value,100000/1.001)
        before = game.value
        game.allocate([0,1,0])
        self.assertAlmostEqual(game.value,before*.999/1.001)
        self.assertAlmostEqual(game.value+game.fees,100000)
        before = game.value
        game.allocate([0,1,0])
        self.assertAlmostEqual(before,game.value)

    def test_future_cannot_change_initial_statistics(self):
        a = self.game(prices([[.1,0,0]]))
        b = self.game(prices([[-.5,.9,.1]]))
        np.testing.assert_allclose(a.covariance(),b.covariance())
        self.assertEqual(a.snapshot(),b.snapshot())
        self.assertNotIn('prices',a.snapshot())

    def test_floor_and_final_score(self):
        game = self.game(prices([[-.09,-.09,-.09]]))
        game.allocate([1,0,0]); game.toggle(); game.step()
        self.assertEqual(game.status,'lost')
        self.assertAlmostEqual(game.snapshot()['total_return'],-.09)
        before = game.value
        game.step()
        self.assertEqual(before,game.value)
        with self.assertRaises(ValueError):
            game.allocate([0,1,0])

    def test_risk_breach(self):
        game = self.game(prices([[.03,.03,.03]]),risk_cap=.05)
        game.allocate([1,0,0]); game.toggle(); game.step()
        self.assertEqual(game.status,'lost')
        self.assertIn('volatility',game.reason)

    def test_win_and_missed_target(self):
        for future, expected in [([[.005]*3]*3,'won'),(None,'lost')]:
            game = self.game(prices(future))
            game.allocate([1,0,0]); game.toggle()
            for _ in range(3):
                game.step()
            self.assertEqual(game.status,expected)
            self.assertEqual(game.position-game.start,3)

    def test_pause_and_bad_allocations(self):
        game = self.game()
        for weights in ([.2,.2,.2],[-1,1,1],[float('nan'),0,1]):
            with self.assertRaises(ValueError):
                game.allocate(weights)
        game.allocate([1,0,0]); game.step()
        self.assertEqual(game.position,game.start)
        game.toggle()
        with self.assertRaises(ValueError):
            game.allocate([0,1,0])

    def test_rebalance_risk_rejection_is_atomic(self):
        frame = prices()
        frame.iloc[1,0] = 110
        game = self.game(frame,risk_cap=.05)
        game.allocate([0,1,0])
        before = game.snapshot()
        with self.assertRaises(ValueError):
            game.allocate([1,0,0])
        self.assertEqual(before,game.snapshot())

    def test_rewind_restores_value_history_and_fees(self):
        game = self.game(prices([[.02, 0, 0], [.02, 0, 0], [.02, 0, 0]]), cost_bps=10)
        game.allocate([1, 0, 0])
        opening_value, opening_fees = game.value, game.fees
        game.toggle()
        for _ in range(3):
            game.step()
        self.assertEqual(game.position - game.start, 3)
        self.assertEqual(len(game.history), 4)
        game.rewind(2)
        self.assertEqual(game.position - game.start, 1)
        self.assertEqual(len(game.history), 2)
        self.assertEqual(game.history[-1]['day'], 1)
        self.assertEqual(game.status, 'paused')
        self.assertTrue(game.assisted)
        self.assertAlmostEqual(game.value, opening_value * 1.02)
        self.assertAlmostEqual(game.fees, opening_fees)
        # Replaying forward from the restored state reproduces the same value.
        game.toggle()
        game.step()
        game.step()
        self.assertAlmostEqual(game.value, opening_value * 1.02 ** 3)

    def test_rewind_undoes_a_loss_and_is_disclosed(self):
        game = self.game(prices([[.02, 0, 0], [-.11, 0, 0]]))
        game.allocate([1, 0, 0])
        game.toggle()
        game.step()
        game.step()
        self.assertEqual(game.status, 'lost')
        self.assertFalse(game.snapshot()['assisted'])
        game.rewind(1)
        self.assertEqual(game.status, 'paused')
        self.assertTrue(game.snapshot()['assisted'])
        self.assertAlmostEqual(game.snapshot()['total_return'], .02, places=6)

    def test_rewind_guards(self):
        game = self.game(prices([[.01, 0, 0]]))
        with self.assertRaises(ValueError):
            game.rewind(1)            # nothing confirmed yet
        game.allocate([1, 0, 0])
        self.assertFalse(game.can_rewind)
        with self.assertRaises(ValueError):
            game.rewind(1)            # still on day zero
        for days in (0, -3, 1.5):
            with self.assertRaises(ValueError):
                game.rewind(days)
        game.toggle()
        game.step()
        self.assertTrue(game.can_rewind)
        game.rewind(99)               # clamps to the first recorded day
        self.assertEqual(game.position, game.start)

    def test_restart_clears_state_and_flags_played_rounds(self):
        game = self.game(prices([[.02, 0, 0]]), cost_bps=10)
        game.restart()
        self.assertFalse(game.assisted)   # nothing was played yet
        game.allocate([1, 0, 0])
        game.toggle()
        game.step()
        game.restart()
        self.assertTrue(game.assisted)
        self.assertEqual(game.status, 'setup')
        self.assertEqual(game.position, game.start)
        self.assertEqual(game.value, self.rules.capital)
        self.assertEqual(game.fees, 0)
        self.assertEqual(game.history, [])
        self.assertEqual(game.trades, [])
        self.assertFalse(game.can_rewind)
        with self.assertRaises(ValueError):
            game.toggle()                 # must reallocate before playing again

    def test_missing_data_and_coverage(self):
        frame = prices(); frame.iloc[4,0] = np.nan
        with self.assertRaises(ValueError):
            self.game(frame)
        with self.assertRaises(ValueError):
            self.game(horizon=100)


if __name__ == '__main__':
    unittest.main()
