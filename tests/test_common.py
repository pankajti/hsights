"""Shared plumbing: sessions, daily seeds and traction analytics."""
from datetime import date
import importlib.util
import os
from pathlib import Path
import tempfile
import time
import unittest

from hsights.common.daily import daily_seed
from hsights.common.sessions import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_put_get_and_replace_keep_one_lock(self):
        store = SessionStore(60, 5)
        self.assertIsNone(store.get('a'))
        slot = store.put('a', {'n': 1})
        again = store.put('a', {'n': 2})
        self.assertIs(slot, again, 'replacing state must keep the same lock')
        self.assertEqual(store['a'], {'n': 2})

    def test_limit_evicts_least_recent_and_spares_live_games(self):
        store = SessionStore(60, 3, keep_first=lambda value: value == 'live')
        store.put('old-live', 'live')
        time.sleep(.002)
        store.put('finished', 'done')
        time.sleep(.002)
        store.put('recent', 'done')
        store.put('newcomer', 'done')
        self.assertIn('old-live', store, 'live games are evicted last')
        self.assertNotIn('finished', store)
        self.assertEqual(len(store), 3)

    def test_expired_sessions_are_purged(self):
        store = SessionStore(60, 5)
        slot = store.put('a', 1)
        slot.touched -= 61
        self.assertIsNone(store.get('a'))

    def test_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            SessionStore(0, 5)


class DailySeedTests(unittest.TestCase):
    def test_stable_per_game_and_day(self):
        day = date(2026, 9, 26)
        self.assertEqual(daily_seed('noise-hunt', day), daily_seed('noise-hunt', day))
        self.assertNotEqual(daily_seed('noise-hunt', day), daily_seed('star-manager', day))
        self.assertNotEqual(daily_seed('noise-hunt', day),
                            daily_seed('noise-hunt', date(2026, 9, 27)))
        self.assertLess(daily_seed('noise-hunt', day), 2 ** 31)


@unittest.skipUnless(importlib.util.find_spec('flask'), 'Flask needed')
class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        from hsights.common import analytics
        self.analytics = analytics
        self.directory = tempfile.TemporaryDirectory()
        self.environment = dict(os.environ)
        os.environ['HSIGHTS_EVENTS_DB'] = str(Path(self.directory.name) / 'events.db')
        os.environ['HSIGHTS_ANALYTICS'] = '1'
        os.environ['HSIGHTS_ADMIN_TOKEN'] = 'secret-token'
        self.server = Flask('analytics-test')

        @self.server.get('/noise-hunt/')
        def page():
            return 'ok'

        @self.server.get('/record/<game>/<event>')
        def recording(game, event):
            return str(analytics.record(game, event, mode='daily'))

        analytics.install(self.server)
        self.client = self.server.test_client()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.environment)
        self.directory.cleanup()

    def test_first_visit_sets_an_anonymous_cookie_and_counts_the_view(self):
        response = self.client.get('/noise-hunt/?src=hub&pos=3')
        cookie = response.headers.get('Set-Cookie', '')
        self.assertIn('hs_vid=', cookie)
        self.assertIn('HttpOnly', cookie)
        report = self.analytics.summary(7)
        row = next(row for row in report['games'] if row['game'] == 'noise-hunt')
        self.assertEqual((row['visitors'], row['hub_clicks']), (1, 1))

    def test_funnel_counts_unique_visitors(self):
        self.client.get('/noise-hunt/')            # sets the cookie
        for event in ('start', 'complete', 'complete', 'share'):
            self.assertEqual(self.client.get(f'/record/noise-hunt/{event}').data, b'True')
        row = next(row for row in self.analytics.summary(7)['games']
                   if row['game'] == 'noise-hunt')
        self.assertEqual((row['started'], row['completed'], row['sharers']), (1, 1, 1))
        self.assertEqual(row['plays_per_player'], 2.0)
        self.assertEqual(row['completion_rate'], 1.0)
        self.assertEqual(row['daily_players'], 1)

    def test_bots_and_unknown_events_are_ignored(self):
        self.client.get('/record/noise-hunt/start', headers={'User-Agent': 'Googlebot/2.1'})
        self.assertEqual(self.client.get('/record/noise-hunt/hack').data, b'False')
        row = next(row for row in self.analytics.summary(7)['games']
                   if row['game'] == 'noise-hunt')
        self.assertEqual(row['started'], 0)

    def test_report_is_hidden_without_the_token(self):
        self.assertEqual(self.client.get('/admin/traction').status_code, 404)
        self.assertEqual(self.client.get('/admin/traction?token=wrong').status_code, 404)
        page = self.client.get('/admin/traction?token=secret-token')
        self.assertEqual(page.status_code, 200)
        self.assertIn(b'Noise Hunt', page.data)
        data = self.client.get('/admin/traction?token=secret-token&format=json').get_json()
        self.assertEqual(len(data['games']), 4)

    def test_share_redirect_counts_and_leaves_for_x_only(self):
        response = self.client.get('/share/x?game=noise-hunt&text=hello')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers['Location'].startswith('https://x.com/intent/post?'))

    def test_recording_off_records_nothing(self):
        os.environ['HSIGHTS_ANALYTICS'] = '0'
        self.assertEqual(self.client.get('/record/noise-hunt/start').data, b'False')


if __name__ == '__main__':
    unittest.main()
