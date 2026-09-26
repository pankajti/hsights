"""Real or Random?: domain rules, then the HTTP surface."""
import importlib.util
import os
import unittest

import numpy as np
import pandas as pd

from hsights.games.real_or_random import ROUNDS, RealOrRandom, coin_probability


def fake_panel(symbols=8, days=900, seed=3):
    rng = np.random.default_rng(seed)
    returns = rng.standard_t(4, size=(days, symbols)) * .012
    prices = 50 * np.exp(np.cumsum(returns, axis=0))
    return pd.DataFrame(prices, index=pd.bdate_range('2015-01-01', periods=days),
                        columns=[f'S{i:02d}' for i in range(symbols)])


class RealOrRandomTests(unittest.TestCase):
    def setUp(self):
        self.panel = fake_panel()

    def test_current_round_never_says_which_side_is_real(self):
        game = RealOrRandom(self.panel, seed=1)
        visible = game.current()
        self.assertEqual(set(visible), {'round', 'rounds', 'days', 'left', 'right'})
        self.assertEqual(visible['left'][0], 100.0)
        self.assertAlmostEqual(visible['right'][0], 100.0)

    def test_real_side_is_a_rescaled_real_window(self):
        game = RealOrRandom(self.panel, seed=4)
        game.guess('left')
        pair, result = game.last_pair(), game.results[-1]
        real = pair.left if pair.real_side == 'left' else pair.right
        window = self.panel.loc[result['start']:result['end'], result['symbol']].to_numpy()
        np.testing.assert_allclose(real, 100 * window / window[0])

    def test_random_twin_matches_mean_and_volatility(self):
        game = RealOrRandom(self.panel, seed=9)
        for _ in range(ROUNDS):
            game.guess('left')
            pair = game.last_pair()
            real, fake = ((pair.left, pair.right) if pair.real_side == 'left'
                          else (pair.right, pair.left))
            real_vol, fake_vol = np.diff(np.log(real)).std(), np.diff(np.log(fake)).std()
            self.assertLess(abs(fake_vol / real_vol - 1), .35)
            if not game.finished:
                game.advance()

    def test_full_game_and_honest_score(self):
        game = RealOrRandom(self.panel, seed=2)
        while not game.finished:
            answer = game._pairs[game.index].real_side         # an oracle
            game.guess(answer)
            if not game.finished:
                game.advance()
        score = game.score()
        self.assertEqual((score['correct'], score['verdict']), (ROUNDS, 'skill'))
        self.assertAlmostEqual(score['coin_probability'], .5 ** ROUNDS)
        with self.assertRaises(ValueError):
            game.guess('left')

    def test_guard_rails(self):
        game = RealOrRandom(self.panel, seed=2)
        with self.assertRaises(ValueError):
            game.advance()
        with self.assertRaises(ValueError):
            game.guess('middle')
        game.guess('left')
        with self.assertRaises(ValueError):
            game.guess('left')

    def test_same_seed_same_rounds(self):
        first, second = RealOrRandom(self.panel, seed=5), RealOrRandom(self.panel, seed=5)
        np.testing.assert_array_equal(first.current()['left'], second.current()['left'])

    def test_coin_probability(self):
        self.assertEqual(coin_probability(0, 10), 1.0)
        self.assertAlmostEqual(coin_probability(5, 10), 0.623046875)
        self.assertAlmostEqual(coin_probability(9, 10), 11 / 1024)


@unittest.skipUnless(importlib.util.find_spec('dash'), 'Install Dash for UI tests')
class RealOrRandomUITests(unittest.TestCase):
    def setUp(self):
        os.environ['HSIGHTS_ANALYTICS'] = '0'
        from hsights.ui.real_or_random.app import create_app
        self.panel = fake_panel()
        self.app = create_app(panel_loader=lambda: self.panel)
        self.client = self.app.server.test_client()
        self.controls = {'new.n_clicks': 0, 'mode.value': 'practice',
                         'pick-left.n_clicks': 0, 'pick-right.n_clicks': 0,
                         'next.n_clicks': 0}

    def request(self, trigger):
        key = next(key for key in self.app.callback_map if 'dots.children' in key)
        callback = self.app.callback_map[key]
        response = self.client.post('/_dash-update-component', json={
            'output': key,
            'outputs': [{'id': item.component_id, 'property': item.component_property}
                        for item in callback['output']],
            'inputs': [dict(item, value=self.controls[f"{item['id']}.{item['property']}"])
                       for item in callback['inputs']],
            'state': [dict(item, value='ror-test') for item in callback['state']],
            'changedPropIds': [trigger]})
        self.assertEqual(response.status_code, 200, response.data[:800])
        return response.get_json()['response']

    def test_routes(self):
        for path in ('/', '/_dash-layout', '/_dash-dependencies', '/assets/style.css',
                     '/common/common.css'):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_play_through_to_a_shareable_score(self):
        first = self.request('mode.value')
        game = self.app.sessions['ror-test']['game']
        symbol = game._pairs[0].symbol
        self.assertNotIn(symbol, str(first), 'the stock is named only after the guess')
        self.assertNotIn('real_side', str(first))
        for round_number in range(ROUNDS):
            self.controls['pick-left.n_clicks'] += 1
            answered = self.request('pick-left.n_clicks')
            self.assertTrue(answered['pick-left']['disabled'])
            self.assertIn(game.results[-1]['symbol'], str(answered['feedback']))
            if round_number < ROUNDS - 1:
                self.controls['next.n_clicks'] += 1
                self.request('next.n_clicks')
        self.assertTrue(game.finished)
        self.assertEqual(answered['share']['style'], {'display': 'block'})
        self.assertIn(f"{game.correct}/{ROUNDS}", answered['share-text']['children'])
        self.assertEqual(answered['next']['style'], {'display': 'none'})


if __name__ == '__main__':
    unittest.main()
