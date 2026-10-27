"""Replay recorded CSV through the same sanity/aggregation/rules/storage pipeline."""
import argparse
import csv
import json
from pathlib import Path
from .core import Tick,Sanity,Minutes,Store

def replay(source,destination):
    store = Store(destination)
    sanity = Sanity()
    minutes = Minutes()
    count = 0
    with Path(source).open(encoding='utf-8',newline='') as f:
        for row in csv.DictReader(f):
            tick = sanity.check(Tick(row['ts'],row['code'],json.loads(row['values_json']),json.loads(row.get('confidence_json') or '{}')))
            c = minutes.add(tick)
            if c and store.conclusion(c):
                count += 1
    # The final minute may be incomplete, matching live monitoring behavior.
    return count

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('source');p.add_argument('destination')
    a = p.parse_args()
    print(f'{replay(a.source,a.destination)} completed minutes written')
