import importlib.util
import unittest
import numpy as np
import pandas as pd

from hsights.games.portfolio_challenge.engine import Game


@unittest.skipUnless(importlib.util.find_spec('dash'), 'Install Dash for browser integration tests')
class AppTests(unittest.TestCase):
    def setUp(self):
        from hsights.games.portfolio_challenge.app import create_app

        def factory(start, seed, rules, **kwargs):
            frame = pd.DataFrame(np.full((510, 3), 100.),
                                 index=pd.bdate_range('2020-01-01', periods=510),
                                 columns=['AAA', 'BBB', 'CCC'])
            return Game(frame, frame.index[252], rules)

        self.app = create_app(factory)
        self.client = self.app.server.test_client()
        self.controls = {'new.n_clicks': 1, 'randomize.n_clicks': 0, 'allocate.n_clicks': 0,
                         'toggle.n_clicks': 0, 'step.n_clicks': 0, 'clock.n_intervals': 0,
                         'rewind.n_clicks': 0, 'restart.submit_n_clicks': 0,
                         'open-setup.n_clicks': 0, 'close-setup.n_clicks': 0}
        self.values = {'session.data': 'test-session', 'start.value': '2022-01-03',
                       'seed.value': 42, 'universe.value': 'curated',
                       'period-mode.value': 'duration', 'duration.value': 252,
                       'end-date.value': '2023-01-03', 'target.value': 10,
                       'floor.value': -8, 'cap.value': 20, 'rewind-days.value': 5,
                       'w0.value': 100, 'w1.value': 0, 'w2.value': 0}

    def request(self, trigger):
        key, callback = next(iter(self.app.callback_map.items()))
        response = self.client.post('/_dash-update-component', json={
            'output': key,
            'outputs': [{'id': item.component_id, 'property': item.component_property}
                        for item in callback['output']],
            'inputs': [dict(item, value=self.controls[f"{item['id']}.{item['property']}"])
                       for item in callback['inputs']],
            'state': [dict(item, value=self.values[f"{item['id']}.{item['property']}"])
                      for item in callback['state']],
            'changedPropIds': [trigger]})
        self.assertEqual(response.status_code, 200, response.data)
        return response.get_json()['response']

    def test_static_routes(self):
        for path in ('/', '/_dash-layout', '/_dash-dependencies',
                     '/assets/style.css', '/assets/game.js'):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_http_controls_and_hidden_future(self):
        result = self.request('new.n_clicks')
        self.assertEqual(result['status-pill']['children'], 'SETUP')
        self.assertEqual(result['setup']['className'], 'overlay')
        self.assertNotIn('prices', str(result))
        self.controls['allocate.n_clicks'] = 1
        result = self.request('allocate.n_clicks')
        self.assertEqual(result['status-pill']['children'], 'PAUSED')
        self.controls['step.n_clicks'] = 1
        result = self.request('step.n_clicks')
        self.assertIn('DAY 1 / 252', result['clock-label']['children'])
        self.assertTrue(result['clock']['disabled'])

    def test_rewind_and_restart_controls(self):
        self.request('new.n_clicks')
        self.controls['allocate.n_clicks'] = 1
        result = self.request('allocate.n_clicks')
        self.assertTrue(result['rewind']['disabled'])        # nothing to rewind to yet
        self.assertFalse(result['restart-button']['disabled'])
        self.controls['step.n_clicks'] = 1
        result = self.request('step.n_clicks')
        self.assertFalse(result['rewind']['disabled'])
        self.assertEqual(result['assisted']['children'], '')

        self.controls['rewind.n_clicks'] = 1
        result = self.request('rewind.n_clicks')
        self.assertIn('DAY 0 / 252', result['clock-label']['children'])
        self.assertEqual(result['assisted']['children'], 'ASSISTED')
        self.assertEqual(result['status-pill']['children'], 'PAUSED')

        self.controls['restart.submit_n_clicks'] = 1
        result = self.request('restart.submit_n_clicks')
        self.assertEqual(result['status-pill']['children'], 'SETUP')
        self.assertEqual(result['assisted']['children'], 'ASSISTED')
        self.assertTrue(result['toggle']['disabled'])
        self.assertNotIn('prices', str(result))

    def test_step_strip_tracks_stage(self):
        result = self.request('new.n_clicks')
        stages = [item['props']['className'] for item in result['steps']['children']]
        self.assertEqual(stages, ['step is-done', 'step is-active', 'step'])
        self.controls['allocate.n_clicks'] = 1
        result = self.request('allocate.n_clicks')
        stages = [item['props']['className'] for item in result['steps']['children']]
        self.assertEqual(stages, ['step is-done', 'step is-done', 'step is-active'])

    def test_setup_overlay_toggles(self):
        self.controls['open-setup.n_clicks'] = 1
        self.assertEqual(self.request('open-setup.n_clicks')['setup']['className'],
                         'overlay is-open')
        self.controls['close-setup.n_clicks'] = 1
        self.assertEqual(self.request('close-setup.n_clicks')['setup']['className'],
                         'overlay')

    def test_settings_pauses_and_does_not_resume_on_close(self):
        self.request('new.n_clicks')
        self.request('allocate.n_clicks')
        self.request('toggle.n_clicks')
        result = self.request('open-setup.n_clicks')
        self.assertEqual(result['status-pill']['children'], 'PAUSED')
        self.assertTrue(result['clock']['disabled'])
        result = self.request('close-setup.n_clicks')
        self.assertTrue(result['clock']['disabled'])

    def test_preview_refresh_dependency_and_single_button_owner(self):
        owners = [callback for callback in self.app.callback_map.values()
                  if any(output.component_id == 'allocate' and output.component_property == 'disabled'
                         for output in (callback['output'] if isinstance(callback['output'], list)
                                        else [callback['output']]))]
        self.assertEqual(len(owners), 1)
        self.assertIn({'id': 'game-revision', 'property': 'data'}, owners[0]['inputs'])
        self.request('new.n_clicks')
        callback = owners[0]['callback'].__wrapped__
        result = callback(100, 0, 0, 'revision', 'test-session')
        self.assertFalse(result[5])
        result = callback(90, 0, 0, 'revision', 'test-session')
        self.assertTrue(result[5])
        self.request('allocate.n_clicks')
        self.request('toggle.n_clicks')
        result = callback(100, 0, 0, 'next-revision', 'test-session')
        self.assertTrue(result[5])
