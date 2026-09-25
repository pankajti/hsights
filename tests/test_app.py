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
                         'open-setup.n_clicks': 0, 'close-setup.n_clicks': 0,
                         'close-result.n_clicks': 0, 'result-restart.n_clicks': 0,
                         'result-new.n_clicks': 0, 'primary.n_clicks': 0}
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

    def test_home_and_play_routes_both_serve_the_app(self):
        for path in ('/', '/play', '/play/portfolio-challenge'):
            self.assertEqual(self.client.get(path).status_code, 200, path)

    def test_router_swaps_pages(self):
        from hsights.games.portfolio_challenge.app import is_play_path
        for pathname in ('/play', '/play/', '/play/portfolio-challenge'):
            self.assertTrue(is_play_path(pathname), pathname)
        for pathname in ('/', '', None, '/about', '/played'):
            self.assertFalse(is_play_path(pathname), pathname)

        route = next(callback for callback in self.app.callback_map.values()
                     if any(output.component_id == 'page-home'
                            for output in (callback['output']
                                           if isinstance(callback['output'], list)
                                           else [callback['output']])))['callback'].__wrapped__
        home, game, clock = route('/', 'test-session')
        self.assertEqual((home, game), ('page is-active', 'page'))
        self.assertTrue(clock, 'the market must not tick while the board is hidden')
        home, game, clock = route('/play', 'test-session')
        self.assertEqual((home, game), ('page', 'page is-active'))
        self.assertTrue(clock, 'no round yet, so the clock stays off')

    def test_router_resumes_the_clock_for_a_running_round(self):
        self.request('new.n_clicks')
        self.controls['allocate.n_clicks'] = 1
        self.request('allocate.n_clicks')
        self.controls['toggle.n_clicks'] = 1
        self.assertFalse(self.request('toggle.n_clicks')['clock']['disabled'])

        route = next(callback for callback in self.app.callback_map.values()
                     if any(output.component_id == 'page-home'
                            for output in (callback['output']
                                           if isinstance(callback['output'], list)
                                           else [callback['output']])))['callback'].__wrapped__
        self.assertTrue(route('/', 'test-session')[2], 'leaving the board freezes it')
        self.assertFalse(route('/play', 'test-session')[2], 'returning resumes it')

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

    def play_to_deadline(self, duration=2, target=10):
        """Flat prices: a 0% return wins against a 0% target and loses against 10%."""
        self.values['duration.value'] = duration
        self.values['target.value'] = target
        self.request('new.n_clicks')
        self.controls['allocate.n_clicks'] = 1
        opening = self.request('allocate.n_clicks')
        self.assertEqual(opening['result']['className'], 'overlay result-overlay',
                         'modal must stay shut while the round is live')
        for click in range(1, duration + 1):
            self.controls['step.n_clicks'] = click
            outcome = self.request('step.n_clicks')
        return outcome

    def test_result_modal_on_loss(self):
        result = self.play_to_deadline(target=10)
        self.assertEqual(result['status-pill']['children'], 'LOST')
        self.assertEqual(result['result']['className'], 'overlay result-overlay is-open')
        body = str(result['result-body'])
        self.assertIn('result-inner lost', body)
        self.assertIn('ROUND OVER', body)
        self.assertIn('Sorry, not this time', body)
        self.assertIn('Deadline reached below the return target.', body)
        self.assertIn('MAX DRAWDOWN', body)
        self.assertNotIn('prices', body)

    def test_result_modal_on_win(self):
        result = self.play_to_deadline(target=0)
        self.assertEqual(result['status-pill']['children'], 'WON')
        self.assertEqual(result['result']['className'], 'overlay result-overlay is-open')
        body = str(result['result-body'])
        self.assertIn('result-inner won', body)
        self.assertIn('CHALLENGE COMPLETE', body)
        self.assertIn('Congratulations', body)

    def test_result_modal_dismisses_and_stays_dismissed(self):
        self.play_to_deadline(target=10)
        self.controls['close-result.n_clicks'] = 1
        self.assertEqual(self.request('close-result.n_clicks')['result']['className'],
                         'overlay result-overlay')
        # Opening settings must not resurrect an already-dismissed result.
        self.controls['open-setup.n_clicks'] = 1
        reopened = self.request('open-setup.n_clicks')
        self.assertEqual(reopened['setup']['className'], 'overlay is-open')
        self.assertEqual(reopened['result']['className'], 'overlay result-overlay')

    def test_result_play_again_restarts_and_clears_the_modal(self):
        self.play_to_deadline(target=10)
        self.controls['result-restart.n_clicks'] = 1
        restarted = self.request('result-restart.n_clicks')
        self.assertEqual(restarted['status-pill']['children'], 'SETUP')
        self.assertEqual(restarted['assisted']['children'], 'ASSISTED')
        self.assertEqual(restarted['result']['className'], 'overlay result-overlay')

    def test_result_new_round_opens_settings(self):
        self.play_to_deadline(target=10)
        self.controls['result-new.n_clicks'] = 1
        opened = self.request('result-new.n_clicks')
        self.assertEqual(opened['setup']['className'], 'overlay is-open')
        self.assertEqual(opened['result']['className'], 'overlay result-overlay')

    def test_primary_action_dispatches_by_status(self):
        self.values['duration.value'] = 2
        result = self.request('new.n_clicks')
        self.assertIn('CONFIRM ALLOCATION', str(result['primary']['children']))
        self.assertEqual(result['primary']['className'], 'primary-action go')

        self.controls['primary.n_clicks'] = 1
        result = self.request('primary.n_clicks')          # commits the allocation
        self.assertEqual(result['status-pill']['children'], 'PAUSED')
        self.assertIn('PLAY', str(result['primary']['children']))
        self.assertEqual(result['primary']['className'], 'primary-action go')

        self.controls['primary.n_clicks'] = 2
        result = self.request('primary.n_clicks')          # starts the clock
        self.assertEqual(result['status-pill']['children'], 'RUNNING')
        self.assertIn('PAUSE', str(result['primary']['children']))
        self.assertEqual(result['primary']['className'], 'primary-action hold')

        self.controls['primary.n_clicks'] = 3
        result = self.request('primary.n_clicks')          # freezes it again
        self.assertEqual(result['status-pill']['children'], 'PAUSED')

        for click in (1, 2):
            self.controls['step.n_clicks'] = click
            result = self.request('step.n_clicks')
        self.assertEqual(result['status-pill']['children'], 'LOST')
        self.assertIn('PLAY AGAIN', str(result['primary']['children']))
        self.assertEqual(result['primary']['className'], 'primary-action again')

        self.controls['primary.n_clicks'] = 4
        result = self.request('primary.n_clicks')          # replays the round
        self.assertEqual(result['status-pill']['children'], 'SETUP')
        self.assertEqual(result['assisted']['children'], 'ASSISTED')
        self.assertIn('CONFIRM ALLOCATION', str(result['primary']['children']))

    def test_primary_opens_settings_when_there_is_no_round(self):
        self.values['session.data'] = 'session-with-no-round'
        self.controls['primary.n_clicks'] = 1
        result = self.request('primary.n_clicks')
        self.assertEqual(result['setup']['className'], 'overlay is-open')
        self.assertIn('DRAW A ROUND', str(result['primary']['children']))

    def test_primary_disabled_state_has_one_owner(self):
        def owners(component):
            return [callback for callback in self.app.callback_map.values()
                    if any(output.component_id == component
                           and output.component_property == 'disabled'
                           for output in (callback['output']
                                          if isinstance(callback['output'], list)
                                          else [callback['output']]))]
        self.assertEqual(len(owners('primary')), 1)
        self.assertIs(owners('primary')[0], owners('allocate')[0])

        self.request('new.n_clicks')
        callback = owners('primary')[0]['callback'].__wrapped__
        # Setup: unbalanced sliders must block Confirm and the header button alike.
        self.assertFalse(callback(100, 0, 0, 'r1', 'test-session')[6])
        self.assertTrue(callback(90, 0, 0, 'r1', 'test-session')[6])
        # Running: the header button means PAUSE, so sliders must not lock it.
        self.request('allocate.n_clicks')
        self.request('toggle.n_clicks')
        self.assertTrue(callback(90, 0, 0, 'r2', 'test-session')[5])
        self.assertFalse(callback(90, 0, 0, 'r2', 'test-session')[6])

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
