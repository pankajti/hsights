import unittest

import numpy as np

from hsights.games.noise_hunt import (Breakout, HuntSession, MeanReversion,
                                      MovingAverageCross, expected_maximum_sharpe,
                                      generate_series, run_backtest, sharpe_ratio)
from hsights.games.noise_hunt.rules import positions


def grid():
    """A small search space, the kind a player would sweep through."""
    rules = [MovingAverageCross(fast, slow, short)
             for fast in (5, 10, 20) for slow in (30, 50, 100) for short in (True, False)]
    rules += [Breakout(lookback, short)
              for lookback in (10, 20, 40, 60) for short in (True, False)]
    rules += [MeanReversion(lookback, threshold)
              for lookback in (10, 20, 40) for threshold in (0.5, 1.0, 1.5)]
    return rules


def holdout_sharpes(seeds, **session_kwargs):
    """Best-rule out-of-sample Sharpe for each seed, after the same search."""
    results = []
    for seed in seeds:
        session = HuntSession(seed=seed, length=1500, holdout_fraction=0.4,
                              cost_bps=0, **session_kwargs)
        session.search(grid())
        results.append(session.reveal().holdout_sharpe)
    return np.array(results)


class SeriesTests(unittest.TestCase):
    def test_default_series_has_no_drift_or_memory(self):
        series = generate_series(seed=3, length=4000)
        self.assertFalse(series.has_signal)
        returns = series.returns()
        # Mean return within a couple of standard errors of zero.
        standard_error = returns.std(ddof=1) / np.sqrt(len(returns))
        self.assertLess(abs(returns.mean()), 3 * standard_error)
        # No first-order autocorrelation worth the name.
        self.assertLess(abs(np.corrcoef(returns[:-1], returns[1:])[0, 1]), .06)

    def test_same_seed_same_path(self):
        self.assertTrue(np.array_equal(generate_series(seed=11).prices,
                                       generate_series(seed=11).prices))
        self.assertFalse(np.array_equal(generate_series(seed=11).prices,
                                        generate_series(seed=12).prices))

    def test_generator_rejects_nonsense(self):
        for kwargs in ({'length': 10}, {'daily_vol': 0}, {'autocorrelation': 2.0}):
            with self.assertRaises(ValueError):
                generate_series(**kwargs)


class RuleTests(unittest.TestCase):
    def test_positions_are_lagged_by_one_day(self):
        prices = np.array([1., 2., 3., 4., 5., 6., 7., 8., 9., 10.])

        class AlwaysLong:
            label = 'always long'

            def signal(self, values):
                return np.ones(len(values))

        held = positions(AlwaysLong(), prices)
        self.assertEqual(held[0], 0.0, 'day one must be flat: nothing is known yet')
        self.assertTrue(np.all(held[1:] == 1.0))

    def test_a_rule_cannot_see_the_day_it_trades(self):
        """Changing only the final price must not change any earlier position."""
        prices = generate_series(seed=5, length=300).prices
        tampered = prices.copy()
        tampered[-1] *= 1.5
        for rule in (MovingAverageCross(5, 20), Breakout(10), MeanReversion(10, 1.0)):
            before = positions(rule, prices)
            after = positions(rule, tampered)
            np.testing.assert_allclose(before[:-1], after[:-1], err_msg=rule.label)

    def test_rules_validate_their_parameters(self):
        for bad in (lambda: MovingAverageCross(50, 10), lambda: Breakout(1),
                    lambda: MeanReversion(2, 1.0), lambda: MeanReversion(10, 0)):
            with self.assertRaises(ValueError):
                bad()


class BacktestTests(unittest.TestCase):
    def test_costs_only_reduce_returns(self):
        series = generate_series(seed=9, length=500)
        rule = MeanReversion(10, 0.5)
        free = run_backtest(rule, series, cost_bps=0)
        charged = run_backtest(rule, series, cost_bps=20)
        self.assertGreater(charged.trades, 0)
        self.assertLess(charged.total_return, free.total_return)

    def test_flat_rule_earns_nothing(self):
        class Flat:
            label = 'flat'

            def signal(self, values):
                return np.zeros(len(values))

        result = run_backtest(Flat(), generate_series(seed=1, length=300))
        self.assertAlmostEqual(result.total_return, 0.0)
        self.assertEqual(result.sharpe, 0.0)
        self.assertEqual(result.trades, 0)


class StatsTests(unittest.TestCase):
    def test_expected_maximum_sharpe_grows_with_attempts(self):
        values = [expected_maximum_sharpe(n, 1.0) for n in (2, 10, 100, 1000)]
        self.assertEqual(values, sorted(values))
        self.assertGreater(values[-1], values[0])

    def test_degenerate_inputs_return_zero(self):
        self.assertEqual(expected_maximum_sharpe(1, 1.0), 0.0)
        self.assertEqual(expected_maximum_sharpe(100, 0.0), 0.0)
        self.assertEqual(sharpe_ratio([]), 0.0)
        self.assertEqual(sharpe_ratio([0.01, 0.01, 0.01]), 0.0)


class SessionTests(unittest.TestCase):
    def test_holdout_is_never_exposed_before_the_reveal(self):
        session = HuntSession(seed=4, length=600, holdout_fraction=0.4)
        visible = session.visible_prices
        self.assertEqual(len(visible), 360)
        self.assertTrue(np.array_equal(visible, session.series.prices[:360]))
        session.try_rule(Breakout(20))
        # Nothing on the public surface leaks a post-split price.
        surface = str(session.trials) + str(session.visible_prices.tolist())
        for price in session.series.prices[360:]:
            self.assertNotIn(f'{price:.10f}', surface)

    def test_searching_noise_produces_a_flattering_best_that_does_not_survive(self):
        session = HuntSession(seed=21, length=900, holdout_fraction=0.4, cost_bps=2)
        session.search(grid())
        result = session.reveal()
        self.assertEqual(result.trials, len(grid()))
        self.assertFalse(result.series_had_signal)
        # The search always finds something that looks positive in sample.
        self.assertGreater(result.in_sample_sharpe, 0.0)
        # And chance alone explains it.
        self.assertGreater(result.expected_best_under_null, 0.0)
        self.assertIn(result.verdict, ('fooled', 'lucky'))
        self.assertIn(str(result.trials), result.headline)

    def test_the_reveal_is_not_rigged_against_real_signal(self):
        """Across seeds, a genuine edge must survive the holdout and noise must not.

        A single seed proves nothing here: even with real drift the winning rule
        can be a short-capable one that gets whipsawed. The claim worth testing
        is that the search separates the two populations.
        """
        seeds = range(30, 42)
        with_signal = holdout_sharpes(seeds, drift=0.0015, daily_vol=0.008)
        without = holdout_sharpes(seeds)

        self.assertGreater(with_signal.mean(), 1.0,
                           'a real edge should show up out of sample')
        self.assertLess(abs(without.mean()), 0.5,
                        'searching noise should average out to nothing')
        self.assertGreater(with_signal.mean(), without.mean() + 1.0)
        self.assertGreaterEqual((with_signal > 0).mean(), 0.75)

    def test_reveal_requires_a_trial_and_closes_the_session(self):
        session = HuntSession(seed=6, length=400)
        with self.assertRaises(ValueError):
            session.reveal()
        session.try_rule(Breakout(15))
        session.reveal()
        self.assertTrue(session.revealed)
        with self.assertRaises(ValueError):
            session.try_rule(Breakout(20))

    def test_best_tracks_the_highest_in_sample_sharpe(self):
        session = HuntSession(seed=8, length=700)
        trials = session.search(grid())
        self.assertEqual(session.best.sharpe, max(trial.sharpe for trial in trials))


if __name__ == '__main__':
    unittest.main()


class PuzzleTests(unittest.TestCase):
    """The game rounds: a hidden coin, calibrated edges and a scored call."""

    def test_regimes_add_signal_without_changing_the_noise_underneath(self):
        from hsights.games.noise_hunt import generate_series
        plain = generate_series(seed=4)
        trended = generate_series(seed=4, regime_drift=.003, regime_length=60)
        self.assertTrue(trended.has_signal)
        self.assertEqual(trended.edge_kind, 'trend')
        difference = np.diff(np.log(trended.prices)) - np.diff(np.log(plain.prices))
        np.testing.assert_allclose(np.abs(difference), .003)

    def test_puzzles_are_reproducible_and_sometimes_hide_an_edge(self):
        from hsights.games.noise_hunt import puzzle
        self.assertTrue(np.array_equal(puzzle(12).series.prices, puzzle(12).series.prices))
        kinds = [puzzle(seed).series.edge_kind for seed in range(200)]
        share = sum(kind is not None for kind in kinds) / len(kinds)
        self.assertTrue(.25 < share < .55, share)
        self.assertEqual({kind for kind in kinds if kind}, {'trend', 'reversal'})

    def test_planted_edges_are_findable_and_noise_is_not_flagged(self):
        """The reveal is not rigged either way. Calibrated in session.puzzle."""
        from hsights.games.noise_hunt import puzzle
        from hsights.ui.noise_hunt.app import SWEEP
        with_edge, without = [], []
        seed = 0
        while len(with_edge) < 16 or len(without) < 16:
            session = puzzle(seed)
            seed += 1
            bucket = with_edge if session.series.has_signal else without
            if len(bucket) >= 16:
                continue
            session.search(SWEEP)
            bucket.append(session.reveal().verdict)
        found = with_edge.count('signal_found') / len(with_edge)
        flagged = without.count('lucky') / len(without)
        self.assertGreater(found, .45, with_edge)
        self.assertLess(flagged, .15, without)

    def test_the_call_is_scored_with_a_brier_rule(self):
        from hsights.games.noise_hunt import HuntSession, MovingAverageCross

        def scored(belief, **edge):
            session = HuntSession(seed=3, **edge)
            session.try_rule(MovingAverageCross(10, 50))
            return session.reveal(belief=belief)

        confident_right = scored(0.1)                       # pure noise
        shrug = scored(0.5)
        confident_wrong = scored(0.9)
        self.assertEqual((confident_right.belief_points, confident_right.called_it), (99, True))
        self.assertEqual((shrug.belief_points, shrug.called_it), (75, None))
        self.assertEqual((confident_wrong.belief_points, confident_wrong.called_it), (19, False))
        self.assertTrue(scored(0.9, autocorrelation=-.3).called_it)
        with self.assertRaises(ValueError):
            scored(1.5)
