"""UI tests for Noise Hunt.

These exercise the presentation layer only. Everything about series, rules and
scoring is tested in test_noise_hunt.py against the domain package directly —
if a test here needs to know how a Sharpe is computed, it is in the wrong file.
"""
import importlib.util
import unittest


@unittest.skipUnless(importlib.util.find_spec('dash'), 'Install Dash for UI tests')
class NoiseHuntUITests(unittest.TestCase):
    def setUp(self):
        from hsights.ui.noise_hunt.app import create_app

        self.app = create_app()
        self.client = self.app.server.test_client()
        self.controls = {'new.n_clicks': 0, 'test.n_clicks': 0,
                         'sweep.n_clicks': 0, 'reveal.n_clicks': 0,
                         'mode.value': 'practice', 'belief.value': None}
        self.values = {'sid.data': 'ui-test', 'kind.value': 'ma',
                       'param-a.value': 10, 'param-b.value': 50,
                       'short.value': ['on']}

    def main_callback(self):
        return next(callback for key, callback in self.app.callback_map.items()
                    if 'tiles.children' in key)

    def request(self, trigger):
        callback = self.main_callback()
        key = next(key for key in self.app.callback_map if 'tiles.children' in key)
        response = self.client.post('/_dash-update-component', json={
            'output': key,
            'outputs': [{'id': item.component_id, 'property': item.component_property}
                        for item in callback['output']],
            'inputs': [dict(item, value=self.controls[f"{item['id']}.{item['property']}"])
                       for item in callback['inputs']],
            'state': [dict(item, value=self.values[f"{item['id']}.{item['property']}"])
                      for item in callback['state']],
            'changedPropIds': [trigger]})
        self.assertEqual(response.status_code, 200, response.data[:800])
        return response.get_json()['response']

    @staticmethod
    def tile_values(output):
        """Read the <strong> out of each rendered tile."""
        return [tile['props']['children'][1]['props']['children']
                for tile in output['children']]

    def test_routes_and_assets(self):
        for path in ('/', '/_dash-layout', '/_dash-dependencies', '/assets/style.css'):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_reveal_is_blocked_until_a_rule_is_tested(self):
        self.controls['new.n_clicks'] = 1
        result = self.request('new.n_clicks')
        self.assertTrue(result['reveal']['disabled'])
        self.assertEqual(self.tile_values(result['tiles'])[0], '0')

    def test_testing_one_rule_enables_the_reveal(self):
        self.controls['new.n_clicks'] = 1
        self.request('new.n_clicks')
        self.controls['test.n_clicks'] = 1
        result = self.request('test.n_clicks')
        self.assertEqual(self.tile_values(result['tiles'])[0], '1')
        self.assertTrue(result['reveal']['disabled'], 'no call placed yet')
        self.controls['belief.value'] = 0.3
        result = self.request('belief.value')
        self.assertFalse(result['reveal']['disabled'])

    def test_reveal_without_a_call_is_refused(self):
        self.controls['test.n_clicks'] = 1
        self.request('test.n_clicks')
        self.controls['reveal.n_clicks'] = 1
        result = self.request('reveal.n_clicks')
        self.assertIn('Make your call', result['error']['children'])
        self.assertFalse(self.app.sessions['ui-test']['session'].revealed)

    def test_new_series_clears_the_call(self):
        self.controls['belief.value'] = 0.7
        self.controls['new.n_clicks'] = 1
        result = self.request('new.n_clicks')
        self.assertIsNone(result['belief']['value'])
        self.assertEqual(result['share']['style'], {'display': 'none'})

    def test_sweep_then_reveal_produces_a_verdict(self):
        self.controls['new.n_clicks'] = 1
        self.request('new.n_clicks')
        self.controls['sweep.n_clicks'] = 1
        swept = self.request('sweep.n_clicks')
        self.assertGreater(int(self.tile_values(swept['tiles'])[0]), 20)

        self.controls['belief.value'] = 0.1
        self.controls['reveal.n_clicks'] = 1
        revealed = self.request('reveal.n_clicks')
        verdict = str(revealed['verdict'])
        self.assertIn('EXPECTED FROM CHANCE', verdict)
        self.assertIn('/100', verdict, 'the call is scored')
        self.assertEqual(revealed['share']['style'], {'display': 'block'})
        self.assertIn('Noise Hunt', revealed['share-text']['children'])
        self.assertTrue(revealed['share-x']['href'].startswith('/share/x?game=noise-hunt'))
        self.assertIn('OUT OF SAMPLE', verdict)
        self.assertEqual(revealed['reveal-chart']['style'], {'display': 'block'})
        self.assertTrue(revealed['reveal']['disabled'],
                        'the hunt is over once revealed')

    def test_the_browser_never_receives_holdout_prices(self):
        self.controls['new.n_clicks'] = 1
        self.request('new.n_clicks')
        self.controls['sweep.n_clicks'] = 1
        payload = str(self.request('sweep.n_clicks'))

        hunt = self.app.sessions['ui-test']['session']
        split = len(hunt.visible_prices)
        sealed = hunt.series.prices[split:]
        self.assertGreater(len(sealed), 100, 'there should be a real holdout')
        for price in sealed:
            self.assertNotIn(f'{price:.8f}', payload)
        # Nor does anything hint at the hidden coin.
        for word in ('regime', 'autocorrelation', 'reversal', 'has_signal', 'edge_kind'):
            self.assertNotIn(word, payload)

    def test_rule_labels_follow_the_selected_type(self):
        key = next(key for key in self.app.callback_map if 'label-a.children' in key)
        relabel = self.app.callback_map[key]['callback'].__wrapped__
        self.assertEqual(relabel('ma')[:2], ('Fast window', 'Slow window'))
        self.assertEqual(relabel('breakout')[0], 'Lookback days')
        self.assertEqual(relabel('breakout')[2], {'display': 'none'},
                         'breakout has no second parameter, so hide it')
        self.assertEqual(relabel('reversion')[1], 'Z-score threshold')


if __name__ == '__main__':
    unittest.main()
