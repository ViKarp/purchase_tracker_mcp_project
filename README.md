# purchase-tracker-mcp

Локальный MCP-сервер на Python для простого учёта покупок в SQLite.

## Что умеет

- записывать покупки;
- читать, искать, обновлять и удалять покупки;
- управлять категориями;
- задавать месячные лимиты по категориям;
- строить сводки по категориям, дням, месяцам, магазинам, способам оплаты и валютам;
- делать CSV-экспорт/импорт;
- делать резервную копию SQLite-БД.

## Установка

```bash
cd purchase_tracker_mcp_project
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

На Windows PowerShell:

```powershell
cd purchase_tracker_mcp_project
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

## Проверка запуска

```bash
purchase-tracker-mcp
```

Для MCP stdio-сервера это нормально, что терминал "завис": он ждёт JSON-RPC сообщения от MCP-клиента.

## Где лежит БД

По умолчанию:

```text
~/.purchase_tracker_mcp/purchases.sqlite3
```

Можно переопределить путь переменной окружения:

```bash
export PURCHASE_DB_PATH="$HOME/.purchase_tracker_mcp/my_purchases.sqlite3"
```

## Подключение к MCP-клиенту

### Claude Desktop / похожие клиенты с JSON-конфигом

```json
{
  "mcpServers": {
    "purchase-tracker": {
      "command": "purchase-tracker-mcp",
      "args": []
    }
  }
}
```

### Codex CLI / TOML-стиль

```toml
[mcp_servers.purchase-tracker]
command = "purchase-tracker-mcp"
args = []
```

## Примеры фраз для агента

```text
Запиши: я потратила 850 рублей в Перекрёстке на продукты.
```

```text
Покажи мои траты за май по категориям.
```

```text
Поставь лимит на категорию Кафе и рестораны 15000 рублей в месяц.
```

```text
Удали покупку с id 17.
```

## Главные tools

- `health`
- `add_purchase`
- `get_purchase`
- `list_purchases`
- `update_purchase`
- `delete_purchase`
- `list_categories`
- `upsert_category`
- `rename_category`
- `delete_category`
- `get_summary`
- `monthly_budget_report`
- `export_purchases_csv`
- `import_purchases_csv`
- `backup_database`
- `purge_all_data`

## Важно про безопасность

Это локальный сервис. Не выставляй его наружу в интернет без авторизации.
Для обычной работы с Claude/Codex/RooCode лучше использовать stdio-транспорт.
