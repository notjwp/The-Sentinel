"""Deliberately insecure sample, used to verify Sentinel end to end.

This file exists only on a throwaway verification branch and is never merged.
Enrichment runs only for HIGH/CRITICAL security findings, so a pull request
with nothing wrong in it cannot tell us whether the LLM path works.
"""

import os


def connect(uid):
    password = "hunter2"
    query = f"SELECT * FROM users WHERE id = {uid}"
    os.system("echo " + uid)
    return password, query
