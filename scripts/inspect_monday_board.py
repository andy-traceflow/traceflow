"""Discover Monday.com board column IDs by display name.

Useful during onboarding when wiring up a new client's Monday adapter:
inspect the target board, see what columns exist, then seed
client_field_mappings rows that point each canonical field at the
right column.

Usage:
    python scripts/inspect_monday_board.py <MONDAY_API_KEY> <BOARD_ID>

Output is a two-section table (parent columns + subitem columns).
"""

from __future__ import annotations

import json
import sys

import httpx

MONDAY_API_URL = "https://api.monday.com/v2"


def inspect(api_key: str, board_id: str) -> None:
    headers = {"Authorization": api_key, "Content-Type": "application/json"}

    parent_query = """
    query ($boardId: [ID!]) {
        boards(ids: $boardId) {
            name
            columns { id title type settings_str }
        }
    }
    """
    resp = httpx.post(
        MONDAY_API_URL,
        headers=headers,
        json={"query": parent_query, "variables": {"boardId": [board_id]}},
        timeout=30,
    )
    data = resp.json()
    if "errors" in data:
        print(f"API Error: {data['errors']}", file=sys.stderr)
        sys.exit(1)

    boards = data.get("data", {}).get("boards", [])
    if not boards:
        print(f"Board {board_id} not found.", file=sys.stderr)
        sys.exit(1)

    board = boards[0]
    print(f"\nBoard: {board['name']} (ID: {board_id})")
    print(_section("PARENT COLUMNS", board["columns"]))

    # Find the subitem board ID
    subitem_board_id: str | None = None
    for col in board["columns"]:
        if col["type"] == "subtasks":
            try:
                settings = json.loads(col["settings_str"])
                ids = settings.get("boardIds") or []
                if ids:
                    subitem_board_id = str(ids[0])
            except (json.JSONDecodeError, KeyError):
                pass

    if not subitem_board_id:
        print("\nNo subitem board found.")
        return

    sub_resp = httpx.post(
        MONDAY_API_URL,
        headers=headers,
        json={"query": parent_query, "variables": {"boardId": [subitem_board_id]}},
        timeout=30,
    )
    sub_data = sub_resp.json()
    sub_boards = sub_data.get("data", {}).get("boards", [])
    if sub_boards:
        print(_section(f"SUBITEM COLUMNS (board {subitem_board_id})", sub_boards[0]["columns"]))


def _section(title: str, columns: list[dict]) -> str:
    header = f"\n{'='*60}\n{title:^60}\n{'='*60}"
    rows = [f"{'Title':<30} {'ID':<20} {'Type':<15}", "-" * 30 + " " + "-" * 20 + " " + "-" * 15]
    for col in columns:
        rows.append(f"{col['title']:<30} {col['id']:<20} {col['type']:<15}")
    return header + "\n" + "\n".join(rows)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python scripts/inspect_monday_board.py <MONDAY_API_KEY> <BOARD_ID>", file=sys.stderr)
        sys.exit(1)
    inspect(sys.argv[1], sys.argv[2])
