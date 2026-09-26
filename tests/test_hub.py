"""The combined server: hub, mounted games, shared assets."""
import importlib.util
import os
import re
import unittest


@unittest.skipUnless(importlib.util.find_spec('dash'), 'Install Dash for server tests')
class HubTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['HSIGHTS_ANALYTICS'] = '0'
        from hsights.server import create_server
        cls.client = create_server().test_client()

    def test_every_game_is_served(self):
        for path in ('/', '/play', '/noise-hunt/', '/real-or-random/', '/star-manager/',
                     '/noise-hunt/_dash-layout', '/real-or-random/_dash-layout',
                     '/star-manager/_dash-layout', '/common/common.css', '/healthz'):
            self.assertIn(self.client.get(path).status_code, (200, 503), path)

    def test_paths_without_a_slash_redirect_and_keep_the_query(self):
        response = self.client.get('/star-manager?src=hub&pos=1')
        self.assertEqual(response.status_code, 308)
        self.assertEqual(response.headers['Location'], '/star-manager/?src=hub&pos=1')

    def test_hub_shows_four_tiles_in_shuffled_positions(self):
        from hsights.home import GAMES, home_page
        orders = set()
        for _ in range(40):
            text = str(home_page())
            cards = re.findall(r"id='card-([a-z-]+)'", text)
            self.assertEqual(sorted(cards), sorted(game[0] for game in GAMES))
            orders.add(tuple(cards))
            for position in range(1, len(GAMES) + 1):
                self.assertIn(f'pos={position}', text)
        self.assertGreater(len(orders), 5, 'tile order must vary between visits')


if __name__ == '__main__':
    unittest.main()
