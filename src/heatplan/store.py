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


class SqliteStore:
    """Same interface on a local SQLite file: the no-AWS-account ("Build It") mode."""

    def __init__(self, path: str):
        import sqlite3
        import threading

        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS items (pk TEXT, sk TEXT, data TEXT, PRIMARY KEY (pk, sk))")
        self.db.commit()

    def put(self, pk, sk, data, if_absent=False) -> bool:
        blob = json.dumps(data, ensure_ascii=False)
        with self._lock:
            verb = "INSERT OR IGNORE" if if_absent else "INSERT OR REPLACE"
            cur = self.db.execute(f"{verb} INTO items (pk, sk, data) VALUES (?, ?, ?)", (pk, sk, blob))
            self.db.commit()
            return cur.rowcount == 1

    def get(self, pk, sk):
        with self._lock:
            row = self.db.execute("SELECT data FROM items WHERE pk = ? AND sk = ?", (pk, sk)).fetchone()
        return json.loads(row[0]) if row else None

    def query(self, pk, newest_first=False, limit=None):
        sql = f"SELECT sk, data FROM items WHERE pk = ? ORDER BY sk {'DESC' if newest_first else 'ASC'}"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self.db.execute(sql, (pk,)).fetchall()
        return [{"sk": sk, **json.loads(data)} for sk, data in rows]

    def delete(self, pk, sk):
        with self._lock:
            self.db.execute("DELETE FROM items WHERE pk = ? AND sk = ?", (pk, sk))
            self.db.commit()


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
        table, local_db = os.environ.get("TABLE_NAME"), os.environ.get("LOCAL_DB")
        _store = DynamoStore(table) if table else SqliteStore(local_db) if local_db else MemoryStore()
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
