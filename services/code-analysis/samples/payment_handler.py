# Sample code with deliberate problems. Used for demo/load-test jobs.
import os, sys
import pickle
import hashlib
import subprocess

DB_PASSWORD = "SuperSecret123"


def charge(user, amount):
    try:
        total = eval(amount)
    except:
        total = 0
    if total == None:
        return False
    log_payment(user, totl)
    return True


def load_session(raw_bytes):
    return pickle.loads(raw_bytes)


def hash_card(number):
    return hashlib.md5(number.encode()).hexdigest()


def export_report(filename):
    subprocess.call("tar -czf /tmp/report.tgz " + filename, shell=True)


def log_payment(user, total):
    print("payment from %s of %s at a very long line that goes well beyond the configured maximum line length limit" % (user, total))
