from __future__ import annotations

import json
import os
import uuid

import aiosqlite

from blop.config import BLOP_DB_PATH
from blop.schemas import AuthProfile, RecordedFlow, FailureCase


def _db_path() -> str:
    return os.environ.get("BLOP_DB_PATH", BLOP_DB_PATH)


async def init_db() -> None:
    path = _db_path()
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS auth_profiles (
                profile_name TEXT PRIMARY KEY,
                auth_type TEXT NOT NULL,
                config_json TEXT NOT NULL,
                storage_state_path TEXT,
                created_at TEXT DEFAULT (datetime('now')),
                refreshed_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS recorded_flows (
                flow_id TEXT PRIMARY KEY,
                flow_name TEXT NOT NULL,
                app_url TEXT NOT NULL,
                goal TEXT NOT NULL,
                steps_json TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now')),
                assertions_json TEXT,
                entry_url TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                profile_name TEXT,
                flow_ids_json TEXT,
                status TEXT DEFAULT 'pending',
                started_at TEXT,
                completed_at TEXT,
                headless INTEGER DEFAULT 1,
                artifacts_dir TEXT,
                cases_json TEXT,
                run_mode TEXT DEFAULT 'hybrid'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS run_cases (
                case_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                flow_id TEXT NOT NULL,
                status TEXT,
                severity TEXT,
                result_json TEXT,
                replay_mode TEXT,
                step_failure_index INTEGER,
                assertion_failures_json TEXT,
                FOREIGN KEY(run_id) REFERENCES runs(run_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                case_id TEXT,
                artifact_type TEXT,
                path TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS site_inventories (
                id TEXT PRIMARY KEY,
                app_url TEXT NOT NULL,
                crawled_at TEXT DEFAULT (datetime('now')),
                inventory_json TEXT NOT NULL
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS execution_plans (
                plan_id TEXT PRIMARY KEY,
                created_at TEXT DEFAULT (datetime('now')),
                intent_json TEXT NOT NULL
            )
        """)
        await db.commit()

        # Migrate existing tables to add new columns if missing
        await _migrate(db)


async def _migrate(db) -> None:
    """Add columns to existing tables that may predate schema additions."""
    migrations = [
        ("recorded_flows", "assertions_json", "TEXT"),
        ("recorded_flows", "entry_url", "TEXT"),
        ("runs", "run_mode", "TEXT DEFAULT 'hybrid'"),
        ("run_cases", "replay_mode", "TEXT"),
        ("run_cases", "step_failure_index", "INTEGER"),
        ("run_cases", "assertion_failures_json", "TEXT"),
    ]
    for table, column, col_type in migrations:
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except Exception:
            pass  # Column already exists
    await db.commit()


async def save_auth_profile(profile: AuthProfile, storage_state_path: str | None = None) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO auth_profiles
            (profile_name, auth_type, config_json, storage_state_path, refreshed_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            """,
            (
                profile.profile_name,
                profile.auth_type,
                profile.model_dump_json(),
                storage_state_path,
            ),
        )
        await db.commit()


async def get_auth_profile(profile_name: str) -> AuthProfile | None:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT config_json FROM auth_profiles WHERE profile_name = ?",
            (profile_name,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return AuthProfile.model_validate_json(row[0])
    return None


async def save_flow(flow: RecordedFlow) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO recorded_flows
            (flow_id, flow_name, app_url, goal, steps_json, created_at, assertions_json, entry_url)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                flow.flow_id,
                flow.flow_name,
                flow.app_url,
                flow.goal,
                json.dumps([s.model_dump() for s in flow.steps]),
                flow.created_at,
                json.dumps(flow.assertions_json),
                flow.entry_url,
            ),
        )
        await db.commit()


async def get_flow(flow_id: str) -> RecordedFlow | None:
    from blop.schemas import FlowStep
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            """SELECT flow_id, flow_name, app_url, goal, steps_json, created_at,
                      assertions_json, entry_url
               FROM recorded_flows WHERE flow_id = ?""",
            (flow_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                steps_data = json.loads(row[4])
                steps = []
                for s in steps_data:
                    # Handle old records that may lack new fields
                    s.setdefault("target_text", None)
                    s.setdefault("dom_fingerprint", None)
                    s.setdefault("url_before", None)
                    s.setdefault("url_after", None)
                    s.setdefault("screenshot_path", None)
                    steps.append(FlowStep(**s))

                assertions_json: list[str] = []
                if row[6]:
                    try:
                        assertions_json = json.loads(row[6])
                    except Exception:
                        pass

                return RecordedFlow(
                    flow_id=row[0],
                    flow_name=row[1],
                    app_url=row[2],
                    goal=row[3],
                    steps=steps,
                    created_at=row[5],
                    assertions_json=assertions_json,
                    entry_url=row[7],
                )
    return None


async def list_flows() -> list[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT flow_id, flow_name, app_url, goal, created_at FROM recorded_flows ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {"flow_id": r[0], "flow_name": r[1], "app_url": r[2], "goal": r[3], "created_at": r[4]}
                for r in rows
            ]


async def create_run(
    run_id: str,
    app_url: str,
    profile_name: str | None,
    flow_ids: list[str],
    headless: bool,
    artifacts_dir: str,
    run_mode: str = "hybrid",
) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO runs (run_id, app_url, profile_name, flow_ids_json, status, started_at, headless, artifacts_dir, run_mode)
            VALUES (?, ?, ?, ?, 'running', datetime('now'), ?, ?, ?)
            """,
            (
                run_id,
                app_url,
                profile_name,
                json.dumps(flow_ids),
                1 if headless else 0,
                artifacts_dir,
                run_mode,
            ),
        )
        await db.commit()


async def update_run(run_id: str, status: str, cases: list[FailureCase], completed_at: str | None = None) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            UPDATE runs SET status = ?, cases_json = ?, completed_at = ?
            WHERE run_id = ?
            """,
            (
                status,
                json.dumps([c.model_dump() for c in cases]),
                completed_at,
                run_id,
            ),
        )
        await db.commit()


async def get_run(run_id: str) -> dict | None:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            """SELECT run_id, app_url, profile_name, flow_ids_json, status,
                      started_at, completed_at, headless, artifacts_dir, cases_json, run_mode
               FROM runs WHERE run_id = ?""",
            (run_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "run_id": row[0],
                    "app_url": row[1],
                    "profile_name": row[2],
                    "flow_ids": json.loads(row[3]) if row[3] else [],
                    "status": row[4],
                    "started_at": row[5],
                    "completed_at": row[6],
                    "headless": bool(row[7]),
                    "artifacts_dir": row[8] or "",
                    "cases": json.loads(row[9]) if row[9] else [],
                    "run_mode": row[10] or "hybrid",
                }
    return None


async def save_case(case: FailureCase) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO run_cases
            (case_id, run_id, flow_id, status, severity, result_json,
             replay_mode, step_failure_index, assertion_failures_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                case.case_id,
                case.run_id,
                case.flow_id,
                case.status,
                case.severity,
                case.model_dump_json(),
                case.replay_mode,
                case.step_failure_index,
                json.dumps(case.assertion_failures),
            ),
        )
        await db.commit()


async def list_cases_for_run(run_id: str) -> list[FailureCase]:
    async with aiosqlite.connect(_db_path()) as db:
        async with db.execute(
            "SELECT result_json FROM run_cases WHERE run_id = ?",
            (run_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            cases = []
            for row in rows:
                try:
                    cases.append(FailureCase.model_validate_json(row[0]))
                except Exception:
                    pass
            return cases


async def save_artifact(run_id: str, case_id: str | None, artifact_type: str, path: str) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO artifacts (artifact_id, run_id, case_id, artifact_type, path)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), run_id, case_id, artifact_type, path),
        )
        await db.commit()


async def save_site_inventory(app_url: str, inventory_dict: dict) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT INTO site_inventories (id, app_url, inventory_json)
            VALUES (?, ?, ?)
            """,
            (str(uuid.uuid4()), app_url, json.dumps(inventory_dict)),
        )
        await db.commit()


async def save_execution_plan(plan_id: str, intent_dict: dict) -> None:
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO execution_plans (plan_id, intent_json)
            VALUES (?, ?)
            """,
            (plan_id, json.dumps(intent_dict)),
        )
        await db.commit()
