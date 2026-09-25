import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from hsights.games.portfolio_challenge.data import create_round
from hsights.games.portfolio_challenge.engine import Rules


class DataTests(unittest.TestCase):
    def test_seed_and_starting_feasibility(self):
        frame = pd.DataFrame(100.,index=pd.bdate_range('2020-01-01',periods=12),
                             columns=['A','B','C','D'])
        rules = Rules(lookback=3,horizon=3)
        with patch('hsights.games.portfolio_challenge.data.load_prices',return_value=frame):
            a = create_round(str(frame.index[4].date()),42,rules)
            b = create_round(str(frame.index[4].date()),42,rules)
        self.assertEqual(list(a.prices),list(b.prices))
        self.assertEqual(len(a.prices.columns),3)

    def test_missing_future_is_not_silently_filled(self):
        frame = pd.DataFrame(100.,index=pd.bdate_range('2020-01-01',periods=12),
                             columns=['A','B','C'])
        frame.iloc[6,0] = np.nan
        with patch('hsights.games.portfolio_challenge.data.load_prices',return_value=frame):
            with self.assertRaisesRegex(ValueError,'missing prices'):
                create_round(str(frame.index[4].date()),42,Rules(lookback=3,horizon=3))

    def test_end_date_resolves_weekend_and_changes_horizon(self):
        frame = pd.DataFrame(100.,index=pd.bdate_range('2020-01-01',periods=15),
                             columns=['A','B','C'])
        with patch('hsights.games.portfolio_challenge.data.load_prices',return_value=frame):
            game = create_round('2020-01-06',rules=Rules(lookback=3,horizon=100),
                                end_date='2020-01-12')
        self.assertEqual(game.rules.horizon,4)
        self.assertEqual(str(game.prices.index[-1].date()),'2020-01-10')

    def test_invalid_end_date(self):
        with self.assertRaises(ValueError):
            create_round('2020-01-06',end_date='2020-01-01')

    def test_sp500_random_batch_and_exclusion(self):
        universe = tuple(f'S{i}' for i in range(500))
        captured = {}
        def download(start,rules,refresh,**kwargs):
            captured.update(kwargs)
            return pd.DataFrame(100.,index=pd.bdate_range('2020-01-01',periods=15),
                                columns=kwargs['tickers'])
        with patch('hsights.games.portfolio_challenge.data.available_symbols',return_value=universe), \
             patch('hsights.games.portfolio_challenge.data.load_prices',side_effect=download):
            game = create_round('2020-01-06',rules=Rules(lookback=3,horizon=3),
                                universe='sp500',exclude=('S0','S1','S2'))
        self.assertEqual(len(captured['tickers']),30)
        self.assertFalse(set(game.prices.columns) & {'S0','S1','S2'})
