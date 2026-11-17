#!/usr/bin/env python3
"""Fake retention job: records that it ran and exits at once."""
import json
import os
import sys
with open(os.path.join(os.environ["MC_FAKE_DIR"], "calls.log"), "a") as f:
    f.write("retention\t" + json.dumps(sys.argv[1:]) + "\n")
print("0 item(s) to process")
