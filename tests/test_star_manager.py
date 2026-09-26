"""The Star Manager: domain rules, then the HTTP surface."""
import importlib.util
import os
import unittest

import numpy as np

from hsights.games.star_manager import ACTIVE_FEE, MANAGERS, StarManager


class StarManagerTests(unittest.TestCase):
    def test_screen_shows_only_years_one_to_three(self):
        game = StarManager(seed=3)
        rows = game.screen()
        self.assertEqual(len(rows), MANAGERS)
        self.assertEqual(sorted(row['rank'] for row in rows), list(range(1, MANAGERS + 1)))
        self.assertEqual(set(rows[0]), {'id', 'name', 'ann_return', 'volatility', 'sharpe',
                                        'max_drawdown', 'vs_index', 'rank'})
        index, paths = game.growth([0, 1])
        self.assertEqual(len(index), 36)
        self.assertTrue(all(len(path) == 36 for path in paths.values()))

    def test_names_are_unique(self):
        self.assertEqual(len(set(StarManager(seed=1).names)), MANAGERS)

    def test_hire_limits_and_single_use(self):
        game = StarManager(seed=4)
        with self.assertRaises(ValueError):
            game.hire([0, 1, 2, 3])
        with self.assertRaises(ValueError):
            game.hire([MANAGERS])
        outcome = game.hire([0, 1])
        self.assertEqual(outcome.hired, (0, 1))
        with self.assertRaises(ValueError):
            game.hire([2])

    def test_buying_the_index_matches_the_index(self):
        outcome = StarManager(seed=5).hire([])
        self.assertEqual(outcome.portfolio_return, outcome.index_return)
        self.assertFalse(outcome.beat_index)

    def test_same_seed_same_universe(self):
        self.assertEqual(StarManager(seed=8).screen(), StarManager(seed=8).screen())

    def test_the_lessons_hold_on_average(self):
        top_beats, stayed, beating = [], [], []
        for seed in range(60):
            game = StarManager(seed=seed)
            best = sorted(game.screen(), key=lambda row: -row['ann_return'])[:3]
            outcome = game.hire([row['id'] for row in best])
            top_beats.append(outcome.beat_index)
            stayed.append(outcome.top10_stayed)
            beating.append(outcome.share_beating_index)
        self.assertLess(np.mean(top_beats), .55, 'past winners should not reliably win')
        self.assertLess(np.mean(beating), .5, f'the {ACTIVE_FEE:.0%} fee should bite')
        self.assertLess(np.mean(stayed), 3)


@unittest.skipUnless(importlib.util.find_spec('dash'), 'Install Dash for UI tests')
class StarManagerUITests(unittest.TestCase):
    def setUp(self):
        os.environ['HSIGHTS_ANALYTICS'] = '0'
        from hsights.ui.star_manager.app import create_app
        self.app = create_app()
        self.client = self.app.server.test_client()

    def post(self, marker, inputs, trigger, state):
        key = next(key for key in self.app.callback_map if marker in key)
        callback = self.app.callback_map[key]
        response = self.client.post('/_dash-update-component', json={
            'output': key,
            'outputs': [{'id': item.component_id, 'property': item.component_property}
                        for item in callback['output']],
            'inputs': [dict(item, value=inputs[f"{item['id']}.{item['property']}"])
                       for item in callback['inputs']],
            'state': [dict(item, value=state[f"{item['id']}.{item['property']}"])
                      for item in callback['state']],
            'changedPropIds': [trigger]})
        self.assertEqual(response.status_code, 200, response.data[:800])
        return response.get_json()['response']

    def test_routes(self):
        for path in ('/', '/_dash-layout', '/_dash-dependencies', '/assets/style.css'):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_screen_then_hire(self):
        inputs = {'new.n_clicks': 0, 'mode.value': 'daily', 'hire.n_clicks': 0,
                  'index.n_clicks': 0}
        state = {'table.selected_row_ids': [], 'sid.data': 'sm-test'}
        screened = self.post('table.data', inputs, 'mode.value', state)
        rows = screened['table']['data']
        self.assertEqual(len(rows), MANAGERS)
        self.assertNotIn('skilled', str(screened), 'skill stays sealed')
        self.assertNotIn('return_after', str(screened))

        picks = [row['id'] for row in sorted(rows, key=lambda row: row['rank'])[:3]]
        shortlist = self.post('growth.figure', {'table.selected_row_ids': picks,
                                                'table.data': rows, 'verdict.children': None},
                              'table.selected_row_ids', {'sid.data': 'sm-test'})
        self.assertFalse(shortlist['hire']['disabled'])

        inputs['hire.n_clicks'] = 1
        state['table.selected_row_ids'] = picks
        hired = self.post('table.data', inputs, 'hire.n_clicks', state)
        self.assertIn('YEARS 4-6', str(hired['verdict']))
        self.assertEqual(hired['share']['style'], {'display': 'block'})
        self.assertIn('The Star Manager', hired['share-text']['children'])
        self.assertFalse(hired['table']['row_selectable'])


if __name__ == '__main__':
    unittest.main()
