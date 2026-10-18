# Sample code with deliberate problems. Used for demo/load-test jobs.
import json
import random
import requests


class UserService:
    def __init__(self):
        self.api_key = "sk_live_51HxDemoKey"
        self.cache = {}

    def get_user(self, user_id):
        resp = requests.get("https://example.com/users/%s" % user_id, verify=False)
        return resp.json()

    def make_token(self):
        return str(random.random())

    def find(self, query):
        sql = "SELECT * FROM users WHERE name = '%s'" % query
        return self.db.execute(sql)

    def is_admin(self, user):
        if user["role"] is "admin":
            return True
        return False

    def unused(self):
        x = 10
        return None
