import json
import re
import sqlite3
import time
from pathlib import Path

import gradio as gr


def read_text_file(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def strip_sql_blocks(sql: str) -> str:
    sql = re.sub(r"DO\s+\$\$.*?\$\$;", "", sql, flags=re.S | re.I)
    sql = re.sub(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION.*?\$\$\s+LANGUAGE\s+plpgsql;",
        "",
        sql,
        flags=re.S | re.I,
    )
    sql = re.sub(r"DROP\s+TRIGGER\s+IF\s+EXISTS.*?;", "", sql, flags=re.S | re.I)
    sql = re.sub(r"CREATE\s+TRIGGER.*?;", "", sql, flags=re.S | re.I)
    sql = re.sub(r"CREATE\s+TYPE\s+.*?;", "", sql, flags=re.S | re.I)
    return sql


def translate_migrate_sql_to_sqlite(sql: str) -> str:
    sql = strip_sql_blocks(sql)
    replacements = [
        (r"\bBIGSERIAL\s+PRIMARY\s+KEY\b", "INTEGER PRIMARY KEY"),
        (r"\bBIGSERIAL\b", "INTEGER"),
        (r"\bBIGINT\b", "INTEGER"),
        (r"\bTIMESTAMPTZ\b", "TEXT"),
        (r"\bBOOLEAN\b", "INTEGER"),
        (r"\bJSONB\b", "TEXT"),
        (r"\bNUMERIC\s*\(\s*3\s*,\s*1\s*\)", "REAL"),
        (r"\bseason_type\b", "TEXT"),
        (r"DEFAULT\s+now\(\)", "DEFAULT CURRENT_TIMESTAMP"),
        (r"\bnow\(\)", "CURRENT_TIMESTAMP"),
    ]
    for pattern, replacement in replacements:
        sql = re.sub(pattern, replacement, sql, flags=re.I)
    return sql


def create_sqlite_db(migrate_sql_path: str, sqlite_path: str) -> str:
    migrate_path = Path(migrate_sql_path).expanduser().resolve()
    db_path = Path(sqlite_path).expanduser().resolve()
    if not migrate_path.exists():
        return f"migrate.sql not found: {migrate_path}"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql_raw = read_text_file(migrate_path)
    sqlite_sql = translate_migrate_sql_to_sqlite(sql_raw)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(sqlite_sql)
    return f"SQLite DB ready: {db_path}"


def get_or_create_author(cur, name: str, dynasty: str | None) -> int:
    if dynasty is None:
        cur.execute("SELECT id FROM authors WHERE name = ? AND dynasty IS NULL", (name,))
    else:
        cur.execute("SELECT id FROM authors WHERE name = ? AND dynasty = ?", (name, dynasty))
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur.execute(
        "INSERT INTO authors (name, dynasty) VALUES (?, ?)",
        (name, dynasty),
    )
    return int(cur.lastrowid)


def get_or_create_tag(cur, table: str, name: str, cache: dict[tuple[str, str], int]) -> int:
    key = (table, name)
    if key in cache:
        return cache[key]
    cur.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        cache[key] = int(row[0])
        return cache[key]
    cur.execute(f"INSERT INTO {table} (name) VALUES (?)", (name,))
    cache[key] = int(cur.lastrowid)
    return cache[key]


def ensure_unique_collection_code(cur, base_code: str) -> str:
    candidate = base_code
    suffix = 1
    while True:
        cur.execute("SELECT 1 FROM collections WHERE code = ?", (candidate,))
        if not cur.fetchone():
            return candidate
        candidate = f"{base_code}_{suffix}"
        suffix += 1


def ensure_primary_collection(
    cur,
    primary_collection_id: int | None,
    collection_code: str | None,
    collection_name: str | None,
    default_label: str,
) -> tuple[int, bool]:
    if primary_collection_id is not None:
        cur.execute("SELECT id FROM collections WHERE id = ?", (primary_collection_id,))
        if cur.fetchone():
            return primary_collection_id, False
        base_code = collection_code or f"collection_{primary_collection_id}"
        code = ensure_unique_collection_code(cur, base_code)
        name = collection_name or code
        cur.execute(
            "INSERT INTO collections (id, code, name) VALUES (?, ?, ?)",
            (primary_collection_id, code, name),
        )
        return primary_collection_id, True

    base_code = collection_code or f"collection_{default_label}_{int(time.time())}"
    code = ensure_unique_collection_code(cur, base_code)
    name = collection_name or code
    cur.execute("INSERT INTO collections (code, name) VALUES (?, ?)", (code, name))
    return int(cur.lastrowid), True


def load_json_entries(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def folder_from_selection(selection) -> str:
    def extract_path(item) -> str | None:
        if item is None:
            return None
        if isinstance(item, (str, Path)):
            return str(item)
        if isinstance(item, dict):
            if "path" in item:
                return str(item["path"])
            if "name" in item:
                return str(item["name"])
        if hasattr(item, "name"):
            return str(item.name)
        return None

    if not selection:
        return ""
    if isinstance(selection, list):
        paths = [extract_path(item) for item in selection]
        paths = [Path(p) for p in paths if p]
    else:
        path = extract_path(selection)
        paths = [Path(path)] if path else []
    if not paths:
        return ""
    if len(paths) == 1 and paths[0].is_dir():
        return str(paths[0])
    try:
        common = Path(Path.commonpath([str(p) for p in paths]))
    except ValueError:
        common = paths[0].parent
    if common.is_dir():
        return str(common)
    return str(common.parent)


def coerce_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"Invalid primary_collection_id: {value!r}")


def import_json_folder(
    sqlite_path: str,
    folder_path: str,
    filename_pattern: str,
    primary_collection_id_text: str,
    collection_code: str,
    collection_name: str,
) -> str:
    db_path = Path(sqlite_path).expanduser().resolve()
    if not db_path.exists():
        return f"SQLite DB not found: {db_path}"
    data_dir = Path(folder_path).expanduser().resolve()
    if not data_dir.exists():
        return f"Folder not found: {data_dir}"

    pattern = filename_pattern.strip() or "*.json"
    files = sorted(data_dir.glob(pattern))
    if not files:
        return f"No files matched: {data_dir} / {pattern}"

    primary_collection_id = coerce_int(primary_collection_id_text)
    default_label = data_dir.name or "collection"
    total_files = 0
    total_works = 0
    created_collection = None

    tag_cache: dict[tuple[str, str], int] = {}
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.cursor()
        primary_id, created = ensure_primary_collection(
            cur,
            primary_collection_id,
            collection_code.strip() or None,
            collection_name.strip() or None,
            default_label,
        )
        created_collection = primary_id if created else None
        conn.commit()

        for file_path in files:
            entries = load_json_entries(file_path)
            total_files += 1
            for item in entries:
                title = (item.get("title") or "").strip()
                author = (item.get("author") or "").strip()
                if not title or not author:
                    continue
                dynasty = item.get("dynasty")
                author_id = get_or_create_author(cur, author, dynasty)

                technique = item.get("technique_analysis") or {}
                structural_logic = technique.get("structural_logic")
                translation = item.get("translation")
                analysis = item.get("analysis")
                score = item.get("score")
                source_json = json.dumps(item, ensure_ascii=False)

                cur.execute(
                    """
                    INSERT INTO works (
                        title,
                        author_id,
                        primary_collection_id,
                        dynasty,
                        score,
                        translation,
                        analysis,
                        structural_logic,
                        source_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        title,
                        author_id,
                        primary_id,
                        dynasty,
                        score,
                        translation,
                        analysis,
                        structural_logic,
                        source_json,
                    ),
                )
                work_id = int(cur.lastrowid)
                total_works += 1

                cur.execute(
                    """
                    INSERT OR IGNORE INTO work_collections (work_id, collection_id, is_primary)
                    VALUES (?, ?, 1)
                    """,
                    (work_id, primary_id),
                )

                paragraphs = item.get("paragraphs") or []
                simplified = item.get("paragraphs_simplified") or []
                sentence_types = item.get("sentence_types") or []
                line_count = max(len(paragraphs), len(simplified), len(sentence_types))
                for idx in range(line_count):
                    text_trad = ""
                    if idx < len(paragraphs):
                        text_trad = paragraphs[idx]
                    elif idx < len(simplified):
                        text_trad = simplified[idx]
                    text_simp = simplified[idx] if idx < len(simplified) else None
                    sentence_type = sentence_types[idx] if idx < len(sentence_types) else None
                    cur.execute(
                        """
                        INSERT INTO work_lines (work_id, line_no, text_traditional, text_simplified, sentence_type)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (work_id, idx + 1, text_trad, text_simp, sentence_type),
                    )

                best_quote = item.get("best_quote") or {}
                if isinstance(best_quote, dict) and "index" in best_quote:
                    line_no = int(best_quote.get("index", 0)) + 1
                    if line_count > 0 and 1 <= line_no <= line_count:
                        cur.execute(
                            """
                            INSERT INTO best_quotes (work_id, line_no, reason)
                            VALUES (?, ?, ?)
                            """,
                            (work_id, line_no, best_quote.get("reason")),
                        )

                time_season = item.get("time_season") or {}
                if isinstance(time_season, dict):
                    season = (time_season.get("season") or "").strip() or "unknown"
                    specific_time = time_season.get("specific_time")
                    cur.execute(
                        """
                        INSERT INTO work_time_season (work_id, season, specific_time)
                        VALUES (?, ?, ?)
                        """,
                        (work_id, season, specific_time),
                    )

                allusions = item.get("allusions") or []
                for entry in allusions:
                    if not isinstance(entry, dict):
                        continue
                    phrase = entry.get("phrase")
                    explanation = entry.get("explanation")
                    if not phrase or not explanation:
                        continue
                    cur.execute(
                        """
                        INSERT INTO allusions (work_id, phrase, explanation)
                        VALUES (?, ?, ?)
                        """,
                        (work_id, phrase, explanation),
                    )

                poetry_styles = item.get("poetry_styles") or []
                for style_entry in poetry_styles:
                    if not isinstance(style_entry, dict):
                        continue
                    style_name = style_entry.get("style")
                    if not style_name:
                        continue
                    style_tag_id = get_or_create_tag(cur, "style_tags", style_name, tag_cache)
                    cur.execute(
                        """
                        INSERT INTO poetry_styles (work_id, style_tag_id, imagery_analysis, realm, reason)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            work_id,
                            style_tag_id,
                            style_entry.get("imagery_analysis"),
                            style_entry.get("realm"),
                            style_entry.get("reason"),
                        ),
                    )

                evidence_list = technique.get("evidence") or []
                for ev in evidence_list:
                    if not isinstance(ev, dict):
                        continue
                    tag_name = ev.get("tag")
                    explanation = ev.get("explanation")
                    if not tag_name or not explanation:
                        continue
                    tag_id = get_or_create_tag(cur, "evidence_tags", tag_name, tag_cache)
                    cur.execute(
                        """
                        INSERT INTO technique_evidence (work_id, evidence_tag_id, explanation)
                        VALUES (?, ?, ?)
                        """,
                        (work_id, tag_id, explanation),
                    )

                subject_tags = item.get("subject") or []
                for tag in subject_tags:
                    tag_id = get_or_create_tag(cur, "subject_tags", tag, tag_cache)
                    cur.execute(
                        "INSERT OR IGNORE INTO work_subject_tags (work_id, subject_tag_id) VALUES (?, ?)",
                        (work_id, tag_id),
                    )

                emotion_tags = item.get("emotion") or []
                for tag in emotion_tags:
                    tag_id = get_or_create_tag(cur, "emotion_tags", tag, tag_cache)
                    cur.execute(
                        "INSERT OR IGNORE INTO work_emotion_tags (work_id, emotion_tag_id) VALUES (?, ?)",
                        (work_id, tag_id),
                    )

                imagery_tags = item.get("imagery") or []
                for tag in imagery_tags:
                    tag_id = get_or_create_tag(cur, "imagery_tags", tag, tag_cache)
                    cur.execute(
                        "INSERT OR IGNORE INTO work_imagery_tags (work_id, imagery_tag_id) VALUES (?, ?)",
                        (work_id, tag_id),
                    )

                expression_tags = technique.get("expression_tags") or []
                for tag in expression_tags:
                    tag_id = get_or_create_tag(cur, "expression_tags", tag, tag_cache)
                    cur.execute(
                        "INSERT OR IGNORE INTO work_expression_tags (work_id, expression_tag_id) VALUES (?, ?)",
                        (work_id, tag_id),
                    )

                rhetoric_tags = technique.get("rhetoric_tags") or []
                for tag in rhetoric_tags:
                    tag_id = get_or_create_tag(cur, "rhetoric_tags", tag, tag_cache)
                    cur.execute(
                        "INSERT OR IGNORE INTO work_rhetoric_tags (work_id, rhetoric_tag_id) VALUES (?, ?)",
                        (work_id, tag_id),
                    )

                presentation_tags = technique.get("presentation_tags") or []
                for tag in presentation_tags:
                    tag_id = get_or_create_tag(cur, "presentation_tags", tag, tag_cache)
                    cur.execute(
                        "INSERT OR IGNORE INTO work_presentation_tags (work_id, presentation_tag_id) VALUES (?, ?)",
                        (work_id, tag_id),
                    )

                structure_tags = technique.get("structure_tags") or []
                for tag in structure_tags:
                    tag_id = get_or_create_tag(cur, "structure_tags", tag, tag_cache)
                    cur.execute(
                        "INSERT OR IGNORE INTO work_structure_tags (work_id, structure_tag_id) VALUES (?, ?)",
                        (work_id, tag_id),
                    )

            conn.commit()

    details = [
        f"DB: {db_path}",
        f"Folder: {data_dir}",
        f"Pattern: {pattern}",
        f"Files processed: {total_files}",
        f"Works inserted: {total_works}",
        f"Primary collection id: {primary_id}",
    ]
    if created_collection is not None:
        details.append(f"Primary collection created: {created_collection}")
    return "\n".join(details)


def build_ui() -> gr.Blocks:
    with gr.Blocks() as demo:
        gr.Markdown("SQLite importer from migrate.sql + JSON batch loader")
        with gr.Row():
            migrate_sql_path = gr.Textbox(
                label="migrate.sql path",
                value=str(Path("handler/migrate.sql")),
            )
            sqlite_db_path = gr.Textbox(
                label="SQLite DB path",
                value=str(Path("poetry.sqlite3")),
            )
        create_btn = gr.Button("Create SQLite DB")
        with gr.Row():
            json_folder = gr.Textbox(label="JSON folder path", value=str(Path(".")))
            filename_pattern = gr.Textbox(label="Filename pattern", value="*.json")
        folder_selector = gr.File(
            label="JSON folder picker",
            file_count="directory",
            type="filepath",
        )
        with gr.Row():
            primary_collection_id = gr.Textbox(label="Primary collection id (optional)")
            collection_code = gr.Textbox(label="Collection code (optional)")
            collection_name = gr.Textbox(label="Collection name (optional)")
        import_btn = gr.Button("Import JSON files")
        output = gr.Textbox(label="Log", lines=12)

        create_btn.click(
            fn=create_sqlite_db,
            inputs=[migrate_sql_path, sqlite_db_path],
            outputs=output,
        )
        folder_selector.change(
            fn=folder_from_selection,
            inputs=folder_selector,
            outputs=json_folder,
        )
        import_btn.click(
            fn=import_json_folder,
            inputs=[
                sqlite_db_path,
                json_folder,
                filename_pattern,
                primary_collection_id,
                collection_code,
                collection_name,
            ],
            outputs=output,
        )
    return demo


if __name__ == "__main__":
    ui = build_ui()
    ui.launch()
