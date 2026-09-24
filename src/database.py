from __future__ import annotations

import sqlite3
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    accepted_terms_version TEXT,
                    accepted_terms_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    details TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS wallets (
                    telegram_user_id INTEGER PRIMARY KEY,
                    balance INTEGER NOT NULL DEFAULT 0 CHECK(balance >= 0),
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
                );

                CREATE TABLE IF NOT EXISTS deposit_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    reference_code TEXT NOT NULL UNIQUE,
                    expected_amount INTEGER NOT NULL CHECK(expected_amount > 0),
                    status TEXT NOT NULL DEFAULT 'pending',
                    expires_at TEXT NOT NULL,
                    paid_at TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
                );

                CREATE TABLE IF NOT EXISTS bank_transactions (
                    provider_transaction_id TEXT PRIMARY KEY,
                    bank_reference TEXT,
                    description TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    received_at TEXT NOT NULL,
                    raw_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    entry_type TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    reference TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
                );

                CREATE TABLE IF NOT EXISTS purchases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL,
                    provider_order_id TEXT UNIQUE,
                    service_id TEXT,
                    service_name TEXT,
                    phone_number TEXT,
                    price INTEGER NOT NULL CHECK(price >= 0),
                    status TEXT NOT NULL,
                    otp TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(telegram_user_id) REFERENCES users(telegram_user_id)
                );

                CREATE TABLE IF NOT EXISTS app_settings (
                    setting_key TEXT PRIMARY KEY,
                    setting_value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(purchases)")}
            for name, sql_type in {
                "otp_id": "INTEGER",
                "sim_id": "INTEGER",
                "network_id": "INTEGER",
                "content": "TEXT",
                "audio_url": "TEXT",
                "provider": "TEXT DEFAULT 'codesim'",
            }.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE purchases ADD COLUMN {name} {sql_type}")

    def reserve_purchase(self, user_id: int, service_id: str | int, service_name: str, price: int, network_id: int | None, provider: str = "codesim"):
        now = datetime.now(timezone.utc).isoformat()
        local_ref = f"rent:{uuid.uuid4().hex}"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            wallet = db.execute("SELECT balance FROM wallets WHERE telegram_user_id=?", (user_id,)).fetchone()
            if not wallet or int(wallet["balance"]) < price:
                return None
            db.execute("UPDATE wallets SET balance=balance-?,updated_at=? WHERE telegram_user_id=?", (price, now, user_id))
            cursor = db.execute(
                "INSERT INTO purchases (telegram_user_id,provider_order_id,service_id,service_name,price,status,network_id,provider,created_at,updated_at) VALUES (?,?,?,?,?,'renting',?,?,?,?)",
                (user_id, local_ref, str(service_id), service_name, price, network_id, provider, now, now),
            )
            purchase_id = int(cursor.lastrowid)
            db.execute("INSERT INTO ledger (telegram_user_id,entry_type,amount,reference,created_at) VALUES (?,'number_rental',?,?,?)", (user_id, -price, local_ref, now))
            balance = db.execute("SELECT balance FROM wallets WHERE telegram_user_id=?", (user_id,)).fetchone()["balance"]
            return {"id": purchase_id, "balance": int(balance), "reference": local_ref}

    def rental_started(self, purchase_id: int, otp_id: int | str, sim_id: int | str, phone: str, provider_price: int, network_id: int | None = None, provider: str = "codesim") -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "UPDATE purchases SET provider_order_id=?,otp_id=?,sim_id=?,phone_number=?,network_id=?,provider=?,status='waiting_otp',updated_at=? WHERE id=?",
                (f"provider:{otp_id}", otp_id, sim_id, phone, network_id, provider, now, purchase_id),
            )

    def fail_and_refund_purchase(self, purchase_id: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM purchases WHERE id=?", (purchase_id,)).fetchone()
            if not row or row["status"] != "renting":
                return
            price = int(row["price"])
            db.execute("UPDATE wallets SET balance=balance+?,updated_at=? WHERE telegram_user_id=?", (price, now, row["telegram_user_id"]))
            db.execute("UPDATE purchases SET status='failed_refunded',updated_at=? WHERE id=?", (now, purchase_id))
            db.execute("INSERT OR IGNORE INTO ledger (telegram_user_id,entry_type,amount,reference,created_at) VALUES (?,'rental_refund',?,?,?)", (row["telegram_user_id"], price, f"rent-refund:{purchase_id}", now))

    def set_purchase_otp(self, purchase_id: int, code: str, content: str = "", audio_url: str = "") -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE purchases SET otp=?,content=?,audio_url=?,status='success',updated_at=? WHERE id=? AND status='waiting_otp'",
                (code, content, audio_url, now, purchase_id),
            )
            return cursor.rowcount == 1

    def set_purchase_poll_data(self, purchase_id: int, content: str = "", audio_url: str = "") -> None:
        with self.connect() as db:
            db.execute("UPDATE purchases SET content=?,audio_url=?,updated_at=? WHERE id=?", (content, audio_url, datetime.now(timezone.utc).isoformat(), purchase_id))

    def set_purchase_voice_received(self, purchase_id: int, content: str, audio_url: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            cursor = db.execute(
                "UPDATE purchases SET content=?,audio_url=?,status='voice_received',updated_at=? "
                "WHERE id=? AND status='waiting_otp'",
                (content, audio_url, now, purchase_id),
            )
            return cursor.rowcount == 1

    def purchase_for_user(self, purchase_id: int, user_id: int):
        with self.connect() as db:
            return db.execute("SELECT * FROM purchases WHERE id=? AND telegram_user_id=?", (purchase_id, user_id)).fetchone()

    def active_purchase_by_phone(self, user_id: int, phone: str):
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM purchases WHERE telegram_user_id=? AND phone_number=? "
                "AND status IN ('waiting_otp','otp_timeout') ORDER BY id DESC LIMIT 1",
                (user_id, phone),
            ).fetchone()

    def update_purchase_status(self, purchase_id: int, status: str) -> None:
        with self.connect() as db:
            db.execute("UPDATE purchases SET status=?,updated_at=? WHERE id=?", (status, datetime.now(timezone.utc).isoformat(), purchase_id))

    def cancel_and_refund_purchase(self, purchase_id: int, user_id: int):
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM purchases WHERE id=? AND telegram_user_id=?",
                (purchase_id, user_id),
            ).fetchone()
            if not row or row["status"] not in {"waiting_otp", "otp_timeout"} or row["otp"]:
                return None
            price = int(row["price"])
            db.execute("UPDATE purchases SET status='cancelled_refunded',updated_at=? WHERE id=?", (now, purchase_id))
            db.execute("UPDATE wallets SET balance=balance+?,updated_at=? WHERE telegram_user_id=?", (price, now, user_id))
            db.execute(
                "INSERT OR IGNORE INTO ledger (telegram_user_id,entry_type,amount,reference,created_at) VALUES (?,'rental_cancel_refund',?,?,?)",
                (user_id, price, f"cancel-refund:{purchase_id}", now),
            )
            balance = db.execute("SELECT balance FROM wallets WHERE telegram_user_id=?", (user_id,)).fetchone()["balance"]
            return {"amount": price, "balance": int(balance)}

    def user_purchases(self, user_id: int, limit: int = 10):
        with self.connect() as db:
            return db.execute("SELECT * FROM purchases WHERE telegram_user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()

    def pending_purchases(self):
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM purchases WHERE status='waiting_otp' AND otp_id IS NOT NULL"
            ).fetchall()

    def set_setting(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT INTO app_settings(setting_key,setting_value,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_at=excluded.updated_at",
                (key, value, now),
            )

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT setting_value FROM app_settings WHERE setting_key=?", (key,)
            ).fetchone()
            return row["setting_value"] if row else default

    def delete_setting(self, key: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM app_settings WHERE setting_key=?", (key,))

    def register_user(self, user) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            is_new = db.execute(
                "SELECT 1 FROM users WHERE telegram_user_id=?", (user.id,)
            ).fetchone() is None
            db.execute(
                """
                INSERT INTO users (
                    telegram_user_id, username, first_name, last_name,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    updated_at=excluded.updated_at
                """,
                (user.id, user.username, user.first_name, user.last_name, now, now),
            )
            db.execute(
                "INSERT OR IGNORE INTO wallets (telegram_user_id, balance, updated_at) VALUES (?, 0, ?)",
                (user.id, now),
            )
            return is_new

    def user_summary(self, user_id: int):
        with self.connect() as db:
            return db.execute(
                """
                SELECT u.telegram_user_id, u.username, u.first_name, u.status,
                       u.accepted_terms_at, COALESCE(w.balance, 0) AS balance
                FROM users u LEFT JOIN wallets w USING (telegram_user_id)
                WHERE u.telegram_user_id=?
                """,
                (user_id,),
            ).fetchone()

    def create_deposit(self, user_id: int, amount: int, reference: str, expires_at: str):
        now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT INTO deposit_requests (telegram_user_id,reference_code,expected_amount,expires_at,created_at) VALUES (?,?,?,?,?)",
                (user_id,reference,amount,expires_at,now),
            )
            return db.execute("SELECT * FROM deposit_requests WHERE reference_code=?",(reference,)).fetchone()

    def deposit_status(self, user_id: int, reference: str):
        with self.connect() as db:
            return db.execute("SELECT * FROM deposit_requests WHERE telegram_user_id=? AND reference_code=?",(user_id,reference)).fetchone()

    def credit_bank_transaction(self, transaction: dict, raw_json: str, provider: str = "bank"):
        import re
        txid=str(transaction.get("id", ""))
        description=str(transaction.get("description", ""))
        amount=int(transaction.get("amount",0) or 0)
        if not txid or amount<=0:return None
        match=re.search(r"(?i)(?<![A-Z0-9])(ZK[A-Z0-9]{8})(?![A-Z0-9])",description)
        if not match:return None
        reference=match.group(1).upper();now=datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM bank_transactions WHERE provider_transaction_id=?",(txid,)).fetchone():return None
            request=db.execute("SELECT * FROM deposit_requests WHERE reference_code=? AND status='pending'",(reference,)).fetchone()
            if not request or int(request["expected_amount"])!=amount:return None
            if datetime.fromisoformat(request["expires_at"]) < datetime.now(timezone.utc):
                db.execute("UPDATE deposit_requests SET status='expired' WHERE id=?",(request["id"],));return None
            db.execute("INSERT INTO bank_transactions VALUES (?,?,?,?,?,?)",(txid,str(transaction.get("reference","")),description,amount,now,raw_json))
            db.execute("UPDATE wallets SET balance=balance+?,updated_at=? WHERE telegram_user_id=?",(amount,now,request["telegram_user_id"]))
            db.execute("INSERT INTO ledger (telegram_user_id,entry_type,amount,reference,created_at) VALUES (?,'bank_deposit',?,?,?)",(request["telegram_user_id"],amount,f"{provider}:{txid}",now))
            db.execute("UPDATE deposit_requests SET status='paid',paid_at=? WHERE id=?",(now,request["id"]))
            return {"telegram_user_id":request["telegram_user_id"],"amount":amount,"reference":reference}

    def admin_credit(self, admin_id: int, target_user_id: int, amount: int, note: str):
        now = datetime.now(timezone.utc).isoformat()
        reference = f"admin:{admin_id}:{uuid.uuid4().hex}"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target = db.execute(
                "SELECT telegram_user_id FROM users WHERE telegram_user_id=? AND status='active'",
                (target_user_id,),
            ).fetchone()
            if not target:
                return None
            db.execute(
                "UPDATE wallets SET balance=balance+?, updated_at=? WHERE telegram_user_id=?",
                (amount, now, target_user_id),
            )
            db.execute(
                "INSERT INTO ledger (telegram_user_id,entry_type,amount,reference,created_at) "
                "VALUES (?,'admin_credit',?,?,?)",
                (target_user_id, amount, reference, now),
            )
            db.execute(
                "INSERT INTO audit_logs (telegram_user_id,action,details,created_at) VALUES (?,?,?,?)",
                (
                    admin_id,
                    "admin_credit",
                    json.dumps(
                        {"target_user_id": target_user_id, "amount": amount, "note": note},
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            balance = db.execute(
                "SELECT balance FROM wallets WHERE telegram_user_id=?", (target_user_id,)
            ).fetchone()["balance"]
            return {"target_user_id": target_user_id, "amount": amount, "balance": balance}

    def admin_debit(self, admin_id: int, target_user_id: int, amount: int, note: str):
        now = datetime.now(timezone.utc).isoformat()
        reference = f"admin-debit:{admin_id}:{uuid.uuid4().hex}"
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            target = db.execute(
                "SELECT u.telegram_user_id,COALESCE(w.balance,0) AS balance "
                "FROM users u LEFT JOIN wallets w USING (telegram_user_id) "
                "WHERE u.telegram_user_id=? AND u.status='active'",
                (target_user_id,),
            ).fetchone()
            if not target:
                return {"error": "not_found"}
            if int(target["balance"]) < amount:
                return {"error": "insufficient", "balance": int(target["balance"])}
            db.execute(
                "UPDATE wallets SET balance=balance-?,updated_at=? WHERE telegram_user_id=?",
                (amount, now, target_user_id),
            )
            db.execute(
                "INSERT INTO ledger (telegram_user_id,entry_type,amount,reference,created_at) "
                "VALUES (?,'admin_debit',?,?,?)",
                (target_user_id, -amount, reference, now),
            )
            db.execute(
                "INSERT INTO audit_logs (telegram_user_id,action,details,created_at) VALUES (?,?,?,?)",
                (
                    admin_id,
                    "admin_debit",
                    json.dumps(
                        {"target_user_id": target_user_id, "amount": amount, "note": note},
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            balance = db.execute(
                "SELECT balance FROM wallets WHERE telegram_user_id=?", (target_user_id,)
            ).fetchone()["balance"]
            return {"target_user_id": target_user_id, "amount": amount, "balance": int(balance)}

    def admin_stats(self):
        with self.connect() as db:
            return db.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM users) AS total_users,
                    (SELECT COUNT(*) FROM users WHERE accepted_terms_at IS NOT NULL) AS accepted_users,
                    (SELECT COALESCE(SUM(balance),0) FROM wallets) AS wallet_total,
                    (SELECT COUNT(*) FROM deposit_requests WHERE status='paid') AS paid_deposits,
                    (SELECT COALESCE(SUM(expected_amount),0) FROM deposit_requests WHERE status='paid') AS deposited_total,
                    (SELECT COUNT(*) FROM purchases) AS purchase_count,
                    (SELECT COALESCE(SUM(price),0) FROM purchases WHERE status NOT IN ('cancelled','refunded')) AS purchase_total
                """
            ).fetchone()

    def recent_deposits(self, limit: int = 10):
        with self.connect() as db:
            return db.execute(
                """
                SELECT d.telegram_user_id,d.reference_code,d.expected_amount,d.status,
                       d.created_at,d.paid_at,u.username,u.first_name
                FROM deposit_requests d JOIN users u USING (telegram_user_id)
                ORDER BY d.id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def recent_users(self, limit: int = 10):
        with self.connect() as db:
            return db.execute(
                """
                SELECT u.telegram_user_id,u.username,u.first_name,u.created_at,
                       COALESCE(w.balance,0) AS balance
                FROM users u LEFT JOIN wallets w USING (telegram_user_id)
                ORDER BY u.created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

    def customer_count(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"])

    def all_customers(self):
        with self.connect() as db:
            return db.execute(
                """
                SELECT
                    u.telegram_user_id,
                    COALESCE(NULLIF(TRIM(COALESCE(u.first_name,'') || ' ' || COALESCE(u.last_name,'')), ''),
                             NULLIF(u.username,''), 'Không tên') AS display_name,
                    u.created_at,
                    COALESCE(w.balance,0) AS balance,
                    (
                        SELECT MAX(activity_at) FROM (
                            SELECT l.created_at AS activity_at FROM ledger l
                            WHERE l.telegram_user_id=u.telegram_user_id
                            UNION ALL
                            SELECT p.updated_at FROM purchases p
                            WHERE p.telegram_user_id=u.telegram_user_id
                            UNION ALL
                            SELECT COALESCE(d.paid_at,d.created_at) FROM deposit_requests d
                            WHERE d.telegram_user_id=u.telegram_user_id AND d.status='paid'
                        )
                    ) AS last_transaction_at
                FROM users u
                LEFT JOIN wallets w USING (telegram_user_id)
                ORDER BY u.created_at DESC
                """
            ).fetchall()

    def admin_user_detail(self, user_id: int):
        with self.connect() as db:
            user = db.execute(
                """
                SELECT u.*,COALESCE(w.balance,0) AS balance
                FROM users u LEFT JOIN wallets w USING (telegram_user_id)
                WHERE u.telegram_user_id=?
                """,
                (user_id,),
            ).fetchone()
            if not user:
                return None
            ledger = db.execute(
                "SELECT entry_type,amount,reference,created_at FROM ledger "
                "WHERE telegram_user_id=? ORDER BY id DESC LIMIT 10",
                (user_id,),
            ).fetchall()
            purchases = db.execute(
                "SELECT service_name,phone_number,price,status,created_at FROM purchases "
                "WHERE telegram_user_id=? ORDER BY id DESC LIMIT 10",
                (user_id,),
            ).fetchall()
            return {"user": user, "ledger": ledger, "purchases": purchases}

    def has_accepted_terms(self, user_id: int, version: str) -> bool:
        with self.connect() as db:
            row = db.execute(
                """
                SELECT 1 FROM users
                WHERE telegram_user_id=?
                  AND status='active'
                  AND accepted_terms_version=?
                  AND accepted_terms_at IS NOT NULL
                """,
                (user_id, version),
            ).fetchone()
        return row is not None

    def accept_terms(self, user_id: int, version: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                UPDATE users SET accepted_terms_version=?, accepted_terms_at=?, updated_at=?
                WHERE telegram_user_id=? AND status='active'
                """,
                (version, now, now, user_id),
            )
            db.execute(
                "INSERT INTO audit_logs (telegram_user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
                (user_id, "terms_accepted", version, now),
            )

    def log_decline(self, user_id: int, version: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute(
                "INSERT INTO audit_logs (telegram_user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
                (user_id, "terms_declined", version, now),
            )

    def get_all_active_user_ids(self) -> list[int]:
        with self.connect() as db:
            rows = db.execute("SELECT telegram_user_id FROM users WHERE status='active'").fetchall()
            return [int(row["telegram_user_id"]) for row in rows]
