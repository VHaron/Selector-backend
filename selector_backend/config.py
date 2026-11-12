import logging
import os
import urllib3

UPSTREAM_TLS_VERIFY = os.getenv("UPSTREAM_TLS_VERIFY", "true").strip().lower() not in {"0", "false", "no"}
if not UPSTREAM_TLS_VERIFY:
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("mvp_selector")

SITE_BASES = {"globusgo": "https://globusgo.pro", "globusmvp": "https://globusmvp.co"}
ROOMS = {
    '888Poker': '244', 'Chico': '245', 'GGNetwork': '246',
    'iPoker': '247', 'PartyPoker': '248', 'PokerStars': '249',
    'PokerStars(FR-ES-PT)': '250', 'Winamax.fr': '251', 'WPN': '252',
    'Pokerdom': '264', 'CoinPoker': '265', 'ClubGG': '267', 'WPT': '268'
}
