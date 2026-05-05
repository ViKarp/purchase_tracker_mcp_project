from __future__ import annotations

import csv
import io
import json
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP


DEFAULT_DB_PATH = (
    Path(os.environ.get("PURCHASE_DB_PATH", "~/.purchase_tracker_mcp/purchases.sqlite3"))
    .expanduser()
    .resolve()
)

DEFAULT_CATEGORIES = [
    "Без категории",
    "Продукты",
    "Кафе и рестораны",
    "Транспорт",
    "Такси",
    "Жильё",
    "Коммунальные услуги",
    "Связь и интернет",
    "Здоровье",
    "Одежда",
    "Красота",
    "Развлечения",
    "Подарки",
    "Образование",
    "Путешествия",
    "Подписки",
    "Другое",
]

SORT_SQL: dict[str, str] = {
    "spent_at_desc": "spent_at DESC, id DESC",
    "spent_at_asc": "spent_at ASC, id ASC",
    "amount_desc": "amount DESC, spent_at DESC, id DESC",
    "amount_asc": "amount ASC, spent_at DESC, id DESC",
    "created_at_desc": "created_at DESC, id DESC",
}

GROUP_SQL: dict[str, str] = {
    "category": "category",
    "day": "substr(spent_at, 1, 10)",
    "month": "substr(spent_at, 1, 7)",
    "merchant": "coalesce(nullif(trim(merchant), ''), 'Без магазина')",
    "payment_method": "coalesce(nullif(trim(payment_method), ''), 'Без способа оплаты')",
    "currency": "currency",
}


logging.basicConfig(
    level=os.environ.get("PURCHASE_MCP_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stderr)],
)
logger = logging.getLogger("purchase-tracker-mcp")


mcp = FastMCP(
    "purchase-tracker",
    instructions=(
        "MCP-сервер для простой локальной БД покупок. "
        "Используй add_purchase для записи новой траты, list_purchases для просмотра, "
        "update_purchase/delete_purchase для правок, get_summary/monthly_budget_report "
        "для аналитики."
    ),
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_datetime(value: str | None) -> str:
    """
    Принимает None, YYYY-MM-DD или ISO datetime.
    Возвращает ISO-строку. Для даты без времени ставит 00:00:00 локального времени.
    """
    if value is None or str(value).strip() == "":
        return now_iso()

    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    try:
        if len(raw) == 10:
            parsed = datetime.fromisoformat(raw)
            return parsed.isoformat(timespec="seconds")
        parsed = datetime.fromisoformat(raw)
        return parsed.isoformat(timespec="seconds")
    except ValueError as exc:
        raise ValueError(
            "spent_at/start_date/end_date должны быть в формате YYYY-MM-DD "
            "или ISO datetime, например 2026-05-03T18:30:00"
        ) from exc


def normalize_optional_date(value: str | None, *, end_of_day: bool = False) -> str | None:
    if value is None or str(value).strip() == "":
        return None

    raw = str(value).strip()
    if len(raw) == 10 and end_of_day:
        raw = raw + "T23:59:59"
    elif len(raw) == 10:
        raw = raw + "T00:00:00"

    return normalize_datetime(raw)


def normalize_currency(currency: str | None) -> str:
    value = (currency or "RUB").strip().upper()
    if len(value) < 2 or len(value) > 8:
        raise ValueError("currency должна быть коротким кодом, например RUB, USD, EUR")
    return value


def normalize_category(category: str | None) -> str:
    value = (category or "Без категории").strip()
    if not value:
        return "Без категории"
    return value


def normalize_tags(tags: list[str] | str | None) -> list[str]:
    if tags is None:
        return []

    if isinstance(tags, str):
        raw_tags = [part.strip() for part in tags.split(",")]
    else:
        raw_tags = [str(part).strip() for part in tags]

    result: list[str] = []
    seen: set[str] = set()
    for tag in raw_tags:
        if not tag:
            continue
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)

    return result


def row_to_purchase(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    tags_json = item.pop("tags_json", "[]")
    try:
        item["tags"] = json.loads(tags_json)
    except json.JSONDecodeError:
        item["tags"] = []
    return item


def normalize_user_id(user_id: str | int | None) -> str:
    value = str(user_id or "").strip()
    if not value:
        raise ValueError("user_id обязателен и не должен быть пустым")
    if len(value) > 128:
        raise ValueError("user_id слишком длинный")
    return value




def purchase_mutation_result(
    *,
    purchase: dict[str, Any] | None,
    changed: bool | None = None,
) -> dict[str, Any]:
    if purchase is None:
        raise ValueError("purchase обязателен для формирования результата")

    result = {
        "ok": True,
        "changed": True if changed is None else changed,
        "id": purchase.get("id"),
        "user_id": purchase.get("user_id"),
        "spent_at": purchase.get("spent_at"),
        "purchase": purchase,
    }
    return result



def category_mutation_result(
    *,
    category: dict[str, Any] | None,
    changed: bool = True,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if category is None:
        raise ValueError("category обязателен для формирования результата")

    result = {
        "ok": True,
        "changed": changed,
        "user_id": category.get("user_id"),
        "category_name": category.get("name"),
        "category": category,
    }
    if extra:
        result.update(extra)
    return result



def category_deletion_result(
    *,
    user_id: str,
    deleted_name: str,
    move_purchases_to: str,
    moved_purchase_count: int,
    changed: bool = True,
) -> dict[str, Any]:
    return {
        "ok": True,
        "changed": changed,
        "user_id": user_id,
        "category_name": deleted_name,
        "deleted_category": deleted_name,
        "move_purchases_to": move_purchases_to,
        "moved_purchase_count": moved_purchase_count,
    }


def get_db_path() -> Path:
    return DEFAULT_DB_PATH


def connect() -> sqlite3.Connection:
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS categories (
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            monthly_limit REAL CHECK (monthly_limit IS NULL OR monthly_limit >= 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, name)
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            amount REAL NOT NULL CHECK (amount > 0),
            currency TEXT NOT NULL DEFAULT 'RUB',
            category TEXT NOT NULL DEFAULT 'Без категории',
            merchant TEXT,
            description TEXT,
            payment_method TEXT,
            spent_at TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_purchases_user_id ON purchases(user_id);
        CREATE INDEX IF NOT EXISTS idx_purchases_spent_at ON purchases(spent_at);
        CREATE INDEX IF NOT EXISTS idx_purchases_category ON purchases(category);
        CREATE INDEX IF NOT EXISTS idx_purchases_currency ON purchases(currency);
        CREATE INDEX IF NOT EXISTS idx_purchases_merchant ON purchases(merchant);
        CREATE INDEX IF NOT EXISTS idx_categories_user_id_name ON categories(user_id, name);
        """
    )

    purchase_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(purchases)").fetchall()
    }
    if "user_id" not in purchase_columns:
        conn.execute(
            "ALTER TABLE purchases ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_purchases_user_id ON purchases(user_id)"
        )

    category_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(categories)").fetchall()
    }
    if category_columns and "user_id" not in category_columns:
        conn.executescript(
            """
            ALTER TABLE categories RENAME TO categories_legacy;

            CREATE TABLE categories (
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                monthly_limit REAL CHECK (monthly_limit IS NULL OR monthly_limit >= 0),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (user_id, name)
            );

            CREATE INDEX idx_categories_user_id_name ON categories(user_id, name);

            INSERT INTO categories(user_id, name, monthly_limit, created_at, updated_at)
            SELECT 'default', name, monthly_limit, created_at, updated_at
            FROM categories_legacy;

            DROP TABLE categories_legacy;
            """
        )

    conn.commit()


def seed_default_categories(conn: sqlite3.Connection, user_id: str) -> None:
    normalized_user_id = normalize_user_id(user_id)
    ts = now_iso()
    conn.executemany(
        """
        INSERT OR IGNORE INTO categories(user_id, name, monthly_limit, created_at, updated_at)
        VALUES (?, ?, NULL, ?, ?)
        """,
        [(normalized_user_id, category, ts, ts) for category in DEFAULT_CATEGORIES],
    )



def ensure_category(conn: sqlite3.Connection, user_id: str, category: str) -> None:
    normalized_user_id = normalize_user_id(user_id)
    seed_default_categories(conn, normalized_user_id)
    ts = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO categories(user_id, name, monthly_limit, created_at, updated_at)
        VALUES (?, ?, NULL, ?, ?)
        """,
        (normalized_user_id, category, ts, ts),
    )


def get_purchase_or_none(
    conn: sqlite3.Connection,
    purchase_id: int,
    user_id: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM purchases WHERE id = ? AND user_id = ?",
        (purchase_id, user_id),
    ).fetchone()
    if row is None:
        return None
    return row_to_purchase(row)



def get_category_or_none(
    conn: sqlite3.Connection,
    user_id: str,
    name: str,
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM categories WHERE user_id = ? AND name = ?",
        (normalize_user_id(user_id), normalize_category(name)),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def build_purchase_where(
    user_id: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
    merchant_contains: str | None = None,
    description_contains: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    currency: str | None = None,
    payment_method: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = ["user_id = ?"]
    params: list[Any] = [normalize_user_id(user_id)]

    normalized_start = normalize_optional_date(start_date, end_of_day=False)
    normalized_end = normalize_optional_date(end_date, end_of_day=True)

    if normalized_start is not None:
        clauses.append("spent_at >= ?")
        params.append(normalized_start)

    if normalized_end is not None:
        clauses.append("spent_at <= ?")
        params.append(normalized_end)

    if category is not None and str(category).strip():
        clauses.append("category = ?")
        params.append(normalize_category(category))

    if merchant_contains is not None and str(merchant_contains).strip():
        clauses.append("merchant LIKE ?")
        params.append(f"%{merchant_contains.strip()}%")

    if description_contains is not None and str(description_contains).strip():
        clauses.append("description LIKE ?")
        params.append(f"%{description_contains.strip()}%")

    if min_amount is not None:
        if min_amount < 0:
            raise ValueError("min_amount не может быть отрицательным")
        clauses.append("amount >= ?")
        params.append(float(min_amount))

    if max_amount is not None:
        if max_amount < 0:
            raise ValueError("max_amount не может быть отрицательным")
        clauses.append("amount <= ?")
        params.append(float(max_amount))

    if currency is not None and str(currency).strip():
        clauses.append("currency = ?")
        params.append(normalize_currency(currency))

    if payment_method is not None and str(payment_method).strip():
        clauses.append("payment_method = ?")
        params.append(payment_method.strip())

    if not clauses:
        return "", params

    return "WHERE " + " AND ".join(clauses), params


def safe_limit(limit: int) -> int:
    if limit < 1:
        return 1
    if limit > 500:
        return 500
    return int(limit)


@mcp.tool()
def health() -> dict[str, Any]:
    """
    Проверить, что MCP-сервер и SQLite-БД доступны.
    """
    with connect() as conn:
        purchase_count = conn.execute("SELECT count(*) AS cnt FROM purchases").fetchone()["cnt"]
        category_count = conn.execute("SELECT count(*) AS cnt FROM categories").fetchone()["cnt"]

    return {
        "ok": True,
        "server": "purchase-tracker",
        "db_path": str(get_db_path()),
        "purchase_count": purchase_count,
        "category_count": category_count,
    }


@mcp.tool()
def add_purchase(
    user_id: str,
    amount: float,
    category: str = "Без категории",
    description: str | None = None,
    merchant: str | None = None,
    spent_at: str | None = None,
    currency: str = "RUB",
    payment_method: str | None = None,
    tags: list[str] | str | None = None,
) -> dict[str, Any]:
    """
    Добавить покупку/трату в БД.

    Args:
        user_id: Идентификатор пользователя, который агент уже подставляет из реального Telegram id.
        amount: Сумма траты. Должна быть больше 0.
        category: Категория, например "Продукты", "Кафе", "Такси".
        description: Короткое описание покупки.
        merchant: Магазин/сервис/получатель платежа.
        spent_at: Дата или дата-время траты: YYYY-MM-DD или ISO datetime.
        currency: Валюта, по умолчанию RUB.
        payment_method: Способ оплаты, например "карта", "наличные", "СБП".
        tags: Список тегов или строка с тегами через запятую.
    Success contract:
        {
            "ok": True,
            "changed": bool,
            "id": int,
            "user_id": str,
            "spent_at": str,
            "purchase": {...},
        }
    """
    if amount <= 0:
        raise ValueError("amount должен быть больше 0")

    normalized_user_id = normalize_user_id(user_id)
    normalized_category = normalize_category(category)
    normalized_currency = normalize_currency(currency)
    normalized_spent_at = normalize_datetime(spent_at)
    normalized_tags = normalize_tags(tags)
    ts = now_iso()

    with connect() as conn:
        ensure_category(conn, normalized_user_id, normalized_category)
        cursor = conn.execute(
            """
            INSERT INTO purchases(
                user_id, amount, currency, category, merchant, description,
                payment_method, spent_at, tags_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_user_id,
                float(amount),
                normalized_currency,
                normalized_category,
                merchant.strip() if merchant else None,
                description.strip() if description else None,
                payment_method.strip() if payment_method else None,
                normalized_spent_at,
                json.dumps(normalized_tags, ensure_ascii=False),
                ts,
                ts,
            ),
        )
        conn.commit()
        purchase_id = int(cursor.lastrowid)
        purchase = get_purchase_or_none(conn, purchase_id, normalized_user_id)

    return purchase_mutation_result(purchase=purchase)


@mcp.tool()
def get_purchase(user_id: str, purchase_id: int) -> dict[str, Any]:
    """
    Получить одну покупку по id.
    """
    normalized_user_id = normalize_user_id(user_id)

    with connect() as conn:
        purchase = get_purchase_or_none(conn, purchase_id, normalized_user_id)

    if purchase is None:
        return {"ok": False, "error": f"Покупка с id={purchase_id} не найдена"}

    return {"ok": True, "purchase": purchase}


@mcp.tool()
def list_purchases(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
    merchant_contains: str | None = None,
    description_contains: str | None = None,
    tag: str | None = None,
    min_amount: float | None = None,
    max_amount: float | None = None,
    currency: str | None = None,
    payment_method: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort: Literal[
        "spent_at_desc",
        "spent_at_asc",
        "amount_desc",
        "amount_asc",
        "created_at_desc",
    ] = "spent_at_desc",
) -> dict[str, Any]:
    """
    Найти покупки с фильтрами и пагинацией.

    Args:
        user_id: Идентификатор пользователя из Telegram/внешнего клиента.
        start_date: Начало периода: YYYY-MM-DD или ISO datetime.
        end_date: Конец периода: YYYY-MM-DD или ISO datetime.
        category: Точная категория.
        merchant_contains: Поиск по части названия магазина/получателя.
        description_contains: Поиск по части описания.
        tag: Фильтр по одному тегу.
        min_amount: Минимальная сумма.
        max_amount: Максимальная сумма.
        currency: Валюта.
        payment_method: Способ оплаты.
        limit: Сколько строк вернуть, максимум 500.
        offset: Смещение для пагинации.
        sort: Сортировка.
    """
    if offset < 0:
        offset = 0

    order_sql = SORT_SQL.get(sort, SORT_SQL["spent_at_desc"])
    where_sql, params = build_purchase_where(
        normalize_user_id(user_id),
        start_date=start_date,
        end_date=end_date,
        category=category,
        merchant_contains=merchant_contains,
        description_contains=description_contains,
        min_amount=min_amount,
        max_amount=max_amount,
        currency=currency,
        payment_method=payment_method,
    )

    normalized_limit = safe_limit(limit)
    tag_filter = tag.strip().casefold() if tag else None

    with connect() as conn:
        if tag_filter:
            rows = conn.execute(
                f"SELECT * FROM purchases {where_sql} ORDER BY {order_sql}",
                params,
            ).fetchall()
            all_items = [row_to_purchase(row) for row in rows]
            filtered = [
                item
                for item in all_items
                if tag_filter in {str(t).casefold() for t in item.get("tags", [])}
            ]
            items = filtered[offset : offset + normalized_limit]
            total = len(filtered)
        else:
            total = conn.execute(
                f"SELECT count(*) AS cnt FROM purchases {where_sql}",
                params,
            ).fetchone()["cnt"]
            rows = conn.execute(
                f"""
                SELECT *
                FROM purchases
                {where_sql}
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
                """,
                [*params, normalized_limit, int(offset)],
            ).fetchall()
            items = [row_to_purchase(row) for row in rows]

    return {
        "ok": True,
        "total": total,
        "limit": normalized_limit,
        "offset": int(offset),
        "items": items,
    }


@mcp.tool()
def update_purchase(
    user_id: str,
    purchase_id: int,
    amount: float | None = None,
    category: str | None = None,
    description: str | None = None,
    merchant: str | None = None,
    spent_at: str | None = None,
    currency: str | None = None,
    payment_method: str | None = None,
    tags: list[str] | str | None = None,
    clear_fields: list[
        Literal["description", "merchant", "payment_method", "tags"]
    ]
    | None = None,
) -> dict[str, Any]:
    """
    Обновить покупку по id. Поля со значением None не меняются.
    Чтобы очистить поле, передай его название в clear_fields.

    Success contract:
        {
            "ok": True,
            "changed": bool,
            "id": int,
            "user_id": str,
            "spent_at": str,
            "purchase": {...},
        }

    Args:
        user_id: Идентификатор пользователя, который агент уже подставляет из реального Telegram id.
        purchase_id: id покупки.
        amount: Новая сумма.
        category: Новая категория.
        description: Новое описание.
        merchant: Новый магазин/получатель.
        spent_at: Новая дата: YYYY-MM-DD или ISO datetime.
        currency: Новая валюта.
        payment_method: Новый способ оплаты.
        tags: Новый список тегов или строка через запятую.
        clear_fields: Поля, которые нужно очистить: description, merchant, payment_method, tags.
    """
    clear = set(clear_fields or [])
    updates: list[str] = []
    params: list[Any] = []

    if amount is not None:
        if amount <= 0:
            raise ValueError("amount должен быть больше 0")
        updates.append("amount = ?")
        params.append(float(amount))

    normalized_user_id = normalize_user_id(user_id)

    with connect() as conn:
        current = get_purchase_or_none(conn, purchase_id, normalized_user_id)
        if current is None:
            return {"ok": False, "error": f"Покупка с id={purchase_id} не найдена"}

        if category is not None:
            normalized_category = normalize_category(category)
            ensure_category(conn, normalized_user_id, normalized_category)
            updates.append("category = ?")
            params.append(normalized_category)

        if description is not None:
            updates.append("description = ?")
            params.append(description.strip() if description.strip() else None)

        if merchant is not None:
            updates.append("merchant = ?")
            params.append(merchant.strip() if merchant.strip() else None)

        if spent_at is not None:
            updates.append("spent_at = ?")
            params.append(normalize_datetime(spent_at))

        if currency is not None:
            updates.append("currency = ?")
            params.append(normalize_currency(currency))

        if payment_method is not None:
            updates.append("payment_method = ?")
            params.append(payment_method.strip() if payment_method.strip() else None)

        if tags is not None:
            updates.append("tags_json = ?")
            params.append(json.dumps(normalize_tags(tags), ensure_ascii=False))

        if "description" in clear:
            updates.append("description = NULL")

        if "merchant" in clear:
            updates.append("merchant = NULL")

        if "payment_method" in clear:
            updates.append("payment_method = NULL")

        if "tags" in clear:
            updates.append("tags_json = '[]'")

        if not updates:
            return purchase_mutation_result(purchase=current, changed=False)

        updates.append("updated_at = ?")
        params.append(now_iso())
        params.append(purchase_id)
        params.append(normalized_user_id)

        conn.execute(
            f"""
            UPDATE purchases
            SET {", ".join(updates)}
            WHERE id = ? AND user_id = ?
            """,
            params,
        )
        conn.commit()
        updated = get_purchase_or_none(conn, purchase_id, normalized_user_id)

    return purchase_mutation_result(purchase=updated, changed=True)


@mcp.tool()
def delete_purchase(user_id: str, purchase_id: int) -> dict[str, Any]:
    """
    Удалить покупку по id.

    Success contract:
        {
            "ok": True,
            "changed": bool,
            "id": int,
            "user_id": str,
            "spent_at": str,
            "purchase": {...},
        }
    """
    normalized_user_id = normalize_user_id(user_id)

    with connect() as conn:
        current = get_purchase_or_none(conn, purchase_id, normalized_user_id)
        if current is None:
            return {"ok": False, "error": f"Покупка с id={purchase_id} не найдена"}

        conn.execute(
            "DELETE FROM purchases WHERE id = ? AND user_id = ?",
            (purchase_id, normalized_user_id),
        )
        conn.commit()

    return purchase_mutation_result(purchase=current, changed=True)


@mcp.tool()
def get_category(user_id: str, name: str) -> dict[str, Any]:
    """
    Получить одну категорию пользователя по имени.
    """
    normalized_user_id = normalize_user_id(user_id)
    normalized_name = normalize_category(name)

    with connect() as conn:
        seed_default_categories(conn, normalized_user_id)
        conn.commit()
        category = get_category_or_none(conn, normalized_user_id, normalized_name)

    if category is None:
        return {"ok": False, "error": f'Категория "{normalized_name}" не найдена'}

    return {"ok": True, "category": category}


@mcp.tool()
def list_categories(user_id: str, include_usage: bool = True) -> dict[str, Any]:
    """
    Показать категории пользователя. Если include_usage=True, добавить количество и сумму покупок.
    """
    normalized_user_id = normalize_user_id(user_id)

    with connect() as conn:
        seed_default_categories(conn, normalized_user_id)
        conn.commit()

        if include_usage:
            rows = conn.execute(
                """
                SELECT
                    c.user_id,
                    c.name,
                    c.monthly_limit,
                    c.created_at,
                    c.updated_at,
                    count(p.id) AS purchase_count,
                    coalesce(sum(p.amount), 0) AS total_amount
                FROM categories c
                LEFT JOIN purchases p
                    ON p.user_id = c.user_id
                    AND p.category = c.name
                WHERE c.user_id = ?
                GROUP BY c.user_id, c.name, c.monthly_limit, c.created_at, c.updated_at

                UNION

                SELECT
                    p.user_id,
                    p.category AS name,
                    NULL AS monthly_limit,
                    NULL AS created_at,
                    NULL AS updated_at,
                    count(p.id) AS purchase_count,
                    coalesce(sum(p.amount), 0) AS total_amount
                FROM purchases p
                LEFT JOIN categories c
                    ON c.user_id = p.user_id
                    AND c.name = p.category
                WHERE p.user_id = ?
                  AND c.name IS NULL
                GROUP BY p.user_id, p.category

                ORDER BY name
                """,
                (normalized_user_id, normalized_user_id),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT
                    user_id,
                    name,
                    monthly_limit,
                    created_at,
                    updated_at,
                    NULL AS purchase_count,
                    NULL AS total_amount
                FROM categories
                WHERE user_id = ?
                ORDER BY name
                """,
                (normalized_user_id,),
            ).fetchall()

    return {"ok": True, "items": [dict(row) for row in rows]}


@mcp.tool()
def count_purchases(
    user_id: str,
    category: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """
    Посчитать количество покупок и их суммарную сумму с базовыми фильтрами.
    """
    where_sql, params = build_purchase_where(
        normalize_user_id(user_id),
        start_date=start_date,
        end_date=end_date,
        category=category,
        currency=currency,
    )

    with connect() as conn:
        row = conn.execute(
            f"""
            SELECT
                count(*) AS count,
                coalesce(round(sum(amount), 2), 0) AS total_amount
            FROM purchases
            {where_sql}
            """,
            params,
        ).fetchone()

    return {
        "ok": True,
        "count": int(row["count"]),
        "total_amount": float(row["total_amount"] or 0),
    }


@mcp.tool()
def upsert_category(
    user_id: str,
    name: str,
    monthly_limit: float | None = None,
    clear_limit: bool = False,
) -> dict[str, Any]:
    """
    Создать или обновить категорию пользователя.

    Success contract:
        {
            "ok": True,
            "changed": bool,
            "user_id": str,
            "category_name": str,
            "category": {
                "user_id": str,
                "name": str,
                "monthly_limit": float | None,
                "created_at": str,
                "updated_at": str,
            },
        }

    Args:
        user_id: Идентификатор пользователя из Telegram/внешнего клиента.
        name: Название категории.
        monthly_limit: Месячный лимит по категории.
        clear_limit: Если True, очистить месячный лимит.
    """
    normalized_user_id = normalize_user_id(user_id)
    normalized_name = normalize_category(name)
    if monthly_limit is not None and monthly_limit < 0:
        raise ValueError("monthly_limit не может быть отрицательным")

    ts = now_iso()
    with connect() as conn:
        seed_default_categories(conn, normalized_user_id)
        conn.execute(
            """
            INSERT OR IGNORE INTO categories(user_id, name, monthly_limit, created_at, updated_at)
            VALUES (?, ?, NULL, ?, ?)
            """,
            (normalized_user_id, normalized_name, ts, ts),
        )

        if clear_limit:
            conn.execute(
                """
                UPDATE categories
                SET monthly_limit = NULL, updated_at = ?
                WHERE user_id = ? AND name = ?
                """,
                (ts, normalized_user_id, normalized_name),
            )
        elif monthly_limit is not None:
            conn.execute(
                """
                UPDATE categories
                SET monthly_limit = ?, updated_at = ?
                WHERE user_id = ? AND name = ?
                """,
                (float(monthly_limit), ts, normalized_user_id, normalized_name),
            )

        conn.commit()
        category = get_category_or_none(conn, normalized_user_id, normalized_name)

    return category_mutation_result(category=category)


@mcp.tool()
def rename_category(user_id: str, old_name: str, new_name: str) -> dict[str, Any]:
    """
    Переименовать категорию пользователя и все его покупки в этой категории.

    Success contract:
        {
            "ok": True,
            "changed": bool,
            "user_id": str,
            "category_name": str,
            "category": {...},
            "old_name": str,
            "new_name": str,
            "moved_purchase_count": int,
        }
    """
    normalized_user_id = normalize_user_id(user_id)
    old_value = normalize_category(old_name)
    new_value = normalize_category(new_name)

    if old_value == "Без категории":
        return {"ok": False, "error": 'Категорию "Без категории" переименовывать нельзя'}

    if old_value == new_value:
        with connect() as conn:
            seed_default_categories(conn, normalized_user_id)
            conn.commit()
            category = get_category_or_none(conn, normalized_user_id, old_value)

        if category is None:
            return {"ok": False, "error": f'Категория "{old_value}" не найдена'}

        return category_mutation_result(
            category=category,
            changed=False,
            extra={
                "old_name": old_value,
                "new_name": new_value,
                "moved_purchase_count": 0,
            },
        )

    ts = now_iso()

    with connect() as conn:
        seed_default_categories(conn, normalized_user_id)
        old_row = get_category_or_none(conn, normalized_user_id, old_value)

        if old_row is None:
            return {"ok": False, "error": f'Категория "{old_value}" не найдена'}

        existing_new = get_category_or_none(conn, normalized_user_id, new_value)

        if existing_new is not None:
            return {"ok": False, "error": f'Категория "{new_value}" уже существует'}

        conn.execute(
            """
            INSERT INTO categories(user_id, name, monthly_limit, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                normalized_user_id,
                new_value,
                old_row["monthly_limit"],
                old_row["created_at"],
                ts,
            ),
        )
        cursor = conn.execute(
            "UPDATE purchases SET category = ? WHERE user_id = ? AND category = ?",
            (new_value, normalized_user_id, old_value),
        )
        conn.execute(
            "DELETE FROM categories WHERE user_id = ? AND name = ?",
            (normalized_user_id, old_value),
        )
        conn.commit()

        category = get_category_or_none(conn, normalized_user_id, new_value)

    return category_mutation_result(
        category=category,
        extra={
            "old_name": old_value,
            "new_name": new_value,
            "moved_purchase_count": cursor.rowcount,
        },
    )


@mcp.tool()
def delete_category(
    user_id: str,
    name: str,
    move_purchases_to: str = "Без категории",
) -> dict[str, Any]:
    """
    Удалить категорию пользователя. Покупки из неё будут перенесены в move_purchases_to.

    Success contract:
        {
            "ok": True,
            "changed": bool,
            "user_id": str,
            "category_name": str,
            "deleted_category": str,
            "move_purchases_to": str,
            "moved_purchase_count": int,
        }
    """
    normalized_user_id = normalize_user_id(user_id)
    category_name = normalize_category(name)
    target_name = normalize_category(move_purchases_to)

    if category_name == "Без категории":
        return {"ok": False, "error": 'Категорию "Без категории" удалять нельзя'}

    with connect() as conn:
        seed_default_categories(conn, normalized_user_id)
        category = get_category_or_none(conn, normalized_user_id, category_name)
        if category is None:
            return {"ok": False, "error": f'Категория "{category_name}" не найдена'}

        ensure_category(conn, normalized_user_id, target_name)
        cursor = conn.execute(
            "UPDATE purchases SET category = ? WHERE user_id = ? AND category = ?",
            (target_name, normalized_user_id, category_name),
        )
        moved_count = cursor.rowcount

        conn.execute(
            "DELETE FROM categories WHERE user_id = ? AND name = ?",
            (normalized_user_id, category_name),
        )
        conn.commit()

    return category_deletion_result(
        user_id=normalized_user_id,
        deleted_name=category_name,
        move_purchases_to=target_name,
        moved_purchase_count=moved_count,
    )


@mcp.tool()
def get_summary(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    group_by: Literal[
        "category",
        "day",
        "month",
        "merchant",
        "payment_method",
        "currency",
    ] = "category",
    currency: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """
    Получить агрегированную статистику расходов.

    Args:
        user_id: Идентификатор пользователя из Telegram/внешнего клиента.
        start_date: Начало периода: YYYY-MM-DD или ISO datetime.
        end_date: Конец периода: YYYY-MM-DD или ISO datetime.
        group_by: Разрез агрегации: category, day, month, merchant, payment_method, currency.
        currency: Фильтр по валюте.
        category: Фильтр по категории.
    """
    group_expr = GROUP_SQL.get(group_by)
    if group_expr is None:
        raise ValueError("group_by должен быть одним из: " + ", ".join(GROUP_SQL))

    where_sql, params = build_purchase_where(
        normalize_user_id(user_id),
        start_date=start_date,
        end_date=end_date,
        category=category,
        currency=currency,
    )

    with connect() as conn:
        totals_row = conn.execute(
            f"""
            SELECT
                count(*) AS purchase_count,
                coalesce(sum(amount), 0) AS total_amount,
                coalesce(avg(amount), 0) AS avg_amount,
                coalesce(min(amount), 0) AS min_amount,
                coalesce(max(amount), 0) AS max_amount
            FROM purchases
            {where_sql}
            """,
            params,
        ).fetchone()

        rows = conn.execute(
            f"""
            SELECT
                {group_expr} AS group_value,
                currency,
                count(*) AS purchase_count,
                round(sum(amount), 2) AS total_amount,
                round(avg(amount), 2) AS avg_amount,
                round(min(amount), 2) AS min_amount,
                round(max(amount), 2) AS max_amount
            FROM purchases
            {where_sql}
            GROUP BY group_value, currency
            ORDER BY total_amount DESC, purchase_count DESC
            """,
            params,
        ).fetchall()

    return {
        "ok": True,
        "filters": {
            "start_date": start_date,
            "end_date": end_date,
            "currency": currency,
            "category": category,
        },
        "group_by": group_by,
        "totals": dict(totals_row),
        "items": [dict(row) for row in rows],
    }


@mcp.tool()
def monthly_budget_report(
    user_id: str,
    year: int,
    month: int,
    currency: str = "RUB",
) -> dict[str, Any]:
    """
    Отчёт за месяц по категориям: факт расходов, лимит, остаток/перерасход.

    Args:
        user_id: Идентификатор пользователя из Telegram/внешнего клиента.
        year: Год, например 2026.
        month: Месяц от 1 до 12.
        currency: Валюта отчёта.
    """
    if month < 1 or month > 12:
        raise ValueError("month должен быть от 1 до 12")

    normalized_currency = normalize_currency(currency)
    normalized_user_id = normalize_user_id(user_id)
    period = f"{year:04d}-{month:02d}"

    with connect() as conn:
        seed_default_categories(conn, normalized_user_id)
        conn.commit()
        rows = conn.execute(
            """
            SELECT
                c.name AS category,
                c.monthly_limit,
                coalesce(sum(CASE WHEN p.currency = ? THEN p.amount ELSE 0 END), 0) AS spent,
                count(CASE WHEN p.currency = ? THEN p.id END) AS purchase_count
            FROM categories c
            LEFT JOIN purchases p
                ON p.category = c.name
                AND p.user_id = c.user_id
                AND substr(p.spent_at, 1, 7) = ?
            WHERE c.user_id = ?
            GROUP BY c.name, c.monthly_limit

            UNION

            SELECT
                p.category AS category,
                NULL AS monthly_limit,
                coalesce(sum(p.amount), 0) AS spent,
                count(p.id) AS purchase_count
            FROM purchases p
            LEFT JOIN categories c
                ON c.user_id = p.user_id
                AND c.name = p.category
            WHERE c.name IS NULL
              AND p.user_id = ?
              AND p.currency = ?
              AND substr(p.spent_at, 1, 7) = ?
            GROUP BY p.category

            ORDER BY spent DESC, category
            """,
            (
                normalized_currency,
                normalized_currency,
                period,
                normalized_user_id,
                normalized_user_id,
                normalized_currency,
                period,
            ),
        ).fetchall()

    items: list[dict[str, Any]] = []
    total_spent = 0.0
    total_limit = 0.0

    for row in rows:
        item = dict(row)
        spent = float(item["spent"] or 0)
        limit_value = item["monthly_limit"]
        total_spent += spent

        if limit_value is None:
            item["remaining"] = None
            item["usage_pct"] = None
            item["status"] = "no_limit"
        else:
            limit_float = float(limit_value)
            total_limit += limit_float
            remaining = round(limit_float - spent, 2)
            item["remaining"] = remaining
            item["usage_pct"] = round(spent / limit_float * 100, 2) if limit_float > 0 else None
            item["status"] = "over_limit" if remaining < 0 else "ok"

        items.append(item)

    return {
        "ok": True,
        "period": period,
        "currency": normalized_currency,
        "total_spent": round(total_spent, 2),
        "total_limit": round(total_limit, 2),
        "total_remaining": round(total_limit - total_spent, 2) if total_limit > 0 else None,
        "items": items,
    }


@mcp.tool()
def export_purchases_csv(
    user_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """
    Экспортировать покупки в CSV-строку.
    """
    where_sql, params = build_purchase_where(
        normalize_user_id(user_id),
        start_date=start_date,
        end_date=end_date,
        category=category,
        currency=currency,
    )

    with connect() as conn:
        rows = conn.execute(
            f"""
            SELECT *
            FROM purchases
            {where_sql}
            ORDER BY spent_at DESC, id DESC
            """,
            params,
        ).fetchall()

    output = io.StringIO()
    fieldnames = [
        "id",
        "user_id",
        "amount",
        "currency",
        "category",
        "merchant",
        "description",
        "payment_method",
        "spent_at",
        "tags",
        "created_at",
        "updated_at",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    for row in rows:
        item = row_to_purchase(row)
        item["tags"] = ",".join(item["tags"])
        writer.writerow({key: item.get(key) for key in fieldnames})

    return {
        "ok": True,
        "row_count": len(rows),
        "csv": output.getvalue(),
    }


@mcp.tool()
def import_purchases_csv(
    user_id: str,
    csv_text: str,
    default_currency: str = "RUB",
) -> dict[str, Any]:
    """
    Импортировать покупки из CSV-строки.

    Success contract:
        {
            "ok": bool,
            "changed": bool,
            "user_id": str,
            "imported": int,
            "imported_count": int,
            "imported_purchase_ids": list[int],
            "error_count": int,
            "errors": list[dict[str, Any]],
        }

    Ожидаемые колонки:
    amount, category, description, merchant, spent_at, currency, payment_method, tags.
    Колонка user_id в CSV игнорируется: импорт всегда идёт в user_id, который агент передал в вызове,
    чтобы не смешивать данные разных пользователей в одном вызове.

    Минимально обязательна только amount.
    """
    if not csv_text.strip():
        return {"ok": False, "error": "csv_text пустой"}

    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return {"ok": False, "error": "Не удалось прочитать заголовок CSV"}

    imported = 0
    imported_purchase_ids: list[int] = []
    errors: list[dict[str, Any]] = []

    normalized_default_user_id = normalize_user_id(user_id)

    with connect() as conn:
        for line_no, row in enumerate(reader, start=2):
            try:
                raw_amount = (row.get("amount") or "").strip().replace(",", ".")
                amount = float(raw_amount)
                if amount <= 0:
                    raise ValueError("amount должен быть больше 0")

                effective_user_id = normalized_default_user_id
                category = normalize_category(row.get("category"))
                currency = normalize_currency(row.get("currency") or default_currency)
                spent_at = normalize_datetime(row.get("spent_at") or None)
                tags = normalize_tags(row.get("tags") or None)
                ts = now_iso()

                ensure_category(conn, effective_user_id, category)
                cursor = conn.execute(
                    """
                    INSERT INTO purchases(
                        user_id, amount, currency, category, merchant, description,
                        payment_method, spent_at, tags_json, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        effective_user_id,
                        amount,
                        currency,
                        category,
                        (row.get("merchant") or "").strip() or None,
                        (row.get("description") or "").strip() or None,
                        (row.get("payment_method") or "").strip() or None,
                        spent_at,
                        json.dumps(tags, ensure_ascii=False),
                        ts,
                        ts,
                    ),
                )
                imported += 1
                imported_purchase_ids.append(int(cursor.lastrowid))
            except Exception as exc:
                errors.append({"line": line_no, "error": str(exc), "row": dict(row)})

        conn.commit()

    return {
        "ok": len(errors) == 0,
        "changed": imported > 0,
        "user_id": normalized_default_user_id,
        "imported": imported,
        "imported_count": imported,
        "imported_purchase_ids": imported_purchase_ids,
        "error_count": len(errors),
        "errors": errors[:50],
    }


@mcp.tool()
def backup_database(backup_path: str | None = None) -> dict[str, Any]:
    """
    Сделать копию SQLite-файла.

    Если backup_path не указан, копия будет создана рядом с БД в папке backups.
    """
    source = get_db_path()
    if not source.exists():
        with connect():
            pass

    if backup_path is None or not backup_path.strip():
        backup_dir = source.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        target = backup_dir / f"purchases_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.sqlite3"
    else:
        target = Path(backup_path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)

    shutil.copy2(source, target)
    wal_path = Path(str(source) + "-wal")
    shm_path = Path(str(source) + "-shm")

    copied_sidecars: list[str] = []
    for sidecar in [wal_path, shm_path]:
        if sidecar.exists():
            sidecar_target = Path(str(target) + sidecar.name[len(source.name):])
            shutil.copy2(sidecar, sidecar_target)
            copied_sidecars.append(str(sidecar_target))

    return {
        "ok": True,
        "source": str(source),
        "backup_path": str(target),
        "copied_sidecar_files": copied_sidecars,
    }


@mcp.tool()
def purge_all_data(confirm: str) -> dict[str, Any]:
    """
    Полностью очистить покупки и пользовательские категории.

    Success contract:
        {
            "ok": True,
            "changed": bool,
            "deleted_purchase_count": int,
        }

    Защита от случайного вызова: confirm должен быть ровно DELETE ALL PURCHASE DATA.
    """
    if confirm != "DELETE ALL PURCHASE DATA":
        return {
            "ok": False,
            "error": 'Для очистки БД передай confirm="DELETE ALL PURCHASE DATA"',
        }

    with connect() as conn:
        purchase_count = conn.execute("SELECT count(*) AS cnt FROM purchases").fetchone()["cnt"]
        conn.execute("DELETE FROM purchases")
        conn.execute("DELETE FROM categories")
        conn.commit()

    return {
        "ok": True,
        "changed": purchase_count > 0,
        "deleted_purchase_count": purchase_count,
    }


@mcp.resource("purchases://schema")
def purchases_schema() -> str:
    """
    Описание структуры БД покупок.
    """
    schema = {
        "database": str(get_db_path()),
        "tables": {
            "purchases": {
                "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
                "user_id": "TEXT, идентификатор пользователя/чата",
                "amount": "REAL, сумма траты > 0",
                "currency": "TEXT, например RUB",
                "category": "TEXT, категория покупки",
                "merchant": "TEXT, магазин/сервис/получатель",
                "description": "TEXT, описание",
                "payment_method": "TEXT, способ оплаты",
                "spent_at": "TEXT, ISO datetime",
                "tags_json": "TEXT, JSON-массив тегов",
                "created_at": "TEXT, ISO datetime",
                "updated_at": "TEXT, ISO datetime",
            },
            "categories": {
                "user_id": "TEXT, идентификатор пользователя/чата",
                "name": "TEXT, название категории",
                "monthly_limit": "REAL NULL, месячный лимит",
                "created_at": "TEXT, ISO datetime",
                "updated_at": "TEXT, ISO datetime",
                "primary_key": "(user_id, name)",
            },
        },
    }
    return json.dumps(schema, ensure_ascii=False, indent=2)


@mcp.resource("purchases://recent")
def recent_purchases() -> str:
    """
    Последние 20 покупок.
    """
    result = {"ok": True, "note": "Для recent_purchases нужен явный вызов list_purchases с user_id"}
    return json.dumps(result, ensure_ascii=False, indent=2)


@mcp.prompt()
def record_purchase_prompt(text: str) -> str:
    """
    Подсказка агенту: как превратить текст пользователя в запись о покупке.
    """
    return (
        "Разбери сообщение пользователя о покупке и вызови tool add_purchase только с данными покупки. "
        "Не передавай user_id из пользовательского текста: агент сам должен подставить реальный Telegram id. "
        "Если дата не указана, не передавай spent_at — сервер поставит текущую дату. "
        "Если категория неочевидна, выбери ближайшую бытовую категорию. "
        "Если сумма не указана, задай пользователю уточняющий вопрос.\n\n"
        f"Сообщение пользователя: {text}"
    )


def main() -> None:
    with connect():
        logger.info("SQLite DB is ready: %s", get_db_path())

    transport = os.environ.get("MCP_TRANSPORT", "stdio").strip().lower()
    if transport in {"http", "streamable-http", "streamable_http"}:
        logger.info("Starting MCP server with streamable-http transport")
        mcp.run(transport="streamable-http")
    else:
        logger.info("Starting MCP server with stdio transport")
        mcp.run()


if __name__ == "__main__":
    main()
