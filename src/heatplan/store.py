"""Single-table storage. Every item is (pk, sk, data-as-JSON) so floats never meet Decimal.

Key layout
  SNAP#{city}#{mode}        / {date}               zone-risk snapshot for a day
  ACT#{city}#{mode}#{date}  / {zone}#{action}      a plan action and its status
  REP#{city}#{date}         / {ts}#{chat}          a worker report
  USER                      / {chat_id}            a worker or officer profile
  ALERT#{mode}#{date}       / {chat}#{zone}        highest band already alerted
  CACHE                     / {key}                cached weather / backtests
"""
from __future__ import annotations

import json
import os
import time


class MemoryStore:
    """In-memory store with the same interface, for tests and local runs."""

    def __init__(self):
        self.items: dict[tuple[str, str], dict] = {}

    def put(self, pk, sk, data, if_absent=False) -> bool:
        if if_absent and (pk, sk) in self.items:
            return False
        self.items[(pk, sk)] = json.loads(json.dumps(data))
        return True

    def get(self, pk, sk):
        v = self.items.get((pk, sk))
        return json.loads(json.dumps(v)) if v is not None else None

    def query(self, pk, newest_first=False, limit=None):
        rows = sorted(((sk, v) for (p, sk), v in self.items.items() if p == pk), reverse=newest_first)
        if limit:
            rows = rows[:limit]
        return [{"sk": sk, **json.loads(json.dumps(v))} for sk, v in rows]

    def delete(self, pk, sk):
        self.items.pop((pk, sk), None)


class DynamoStore:
    def __init__(self, table_name: str):
        import boto3
        from boto3.dynamodb.conditions import Key

        self._key = Key
        self.table = boto3.resource("dynamodb").Table(table_name)

    def put(self, pk, sk, data, if_absent=False) -> bool:
        item = {"pk": pk, "sk": sk, "data": json.dumps(data, ensure_ascii=False)}
        if not if_absent:
            self.table.put_item(Item=item)
            return True
        try:
            self.table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
            return True
        except self.table.meta.client.exceptions.ConditionalCheckFailedException:
            return False

    def get(self, pk, sk):
        res = self.table.get_item(Key={"pk": pk, "sk": sk})
        item = res.get("Item")
        return json.loads(item["data"]) if item else None

    def query(self, pk, newest_first=False, limit=None):
        kwargs = {"KeyConditionExpression": self._key("pk").eq(pk), "ScanIndexForward": not newest_first}
        if limit:
            kwargs["Limit"] = limit
        out = []
        while True:
            res = self.table.query(**kwargs)
            out += [{"sk": i["sk"], **json.loads(i["data"])} for i in res["Items"]]
            if limit and len(out) >= limit:
                return out[:limit]
            if "LastEvaluatedKey" not in res:
                return out
            kwargs["ExclusiveStartKey"] = res["LastEvaluatedKey"]

    def delete(self, pk, sk):
        self.table.delete_item(Key={"pk": pk, "sk": sk})


_store = None


def get_store():
    global _store
    if _store is None:
        table = os.environ.get("TABLE_NAME")
        _store = DynamoStore(table) if table else MemoryStore()
    return _store


# ---- typed helpers -------------------------------------------------------

def snap_pk(city, mode):
    return f"SNAP#{city}#{mode}"


def act_pk(city, mode, date):
    return f"ACT#{city}#{mode}#{date}"


def save_snapshot(st, snap, mode):
    st.put(snap_pk(snap["city"], mode), snap["date"], snap)


def load_snapshot(st, city, mode, date=None):
    if date:
        return st.get(snap_pk(city, mode), date)
    rows = st.query(snap_pk(city, mode), newest_first=True, limit=1)
    return rows[0] if rows else None


def list_actions(st, city, mode, date):
    return st.query(act_pk(city, mode, date))


def list_reports(st, city, date):
    return st.query(f"REP#{city}#{date}", newest_first=True)


def get_user(st, chat_id):
    return st.get("USER", str(chat_id))


def save_user(st, chat_id, profile):
    st.put("USER", str(chat_id), profile)


def list_users(st):
    return st.query("USER")


def now_ts() -> int:
    return int(time.time())
