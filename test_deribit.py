"""Quick smoke-test for Deribit testnet access. Run with: python test_deribit.py"""
from access import DeribitClient

c = DeribitClient(paper=True)
c.authenticate()
print("Auth OK — token:", c._token[:20], "...")
print("Open orders:", len(c.get_open_orders()))
