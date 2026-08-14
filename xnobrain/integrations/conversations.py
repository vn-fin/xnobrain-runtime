"""Conversations methods for the Hermes runtime adapter."""

from .hermes_support import (
    AgentAPIError,
    Any,
    DEFAULT_CONVERSATION_TITLE,
    DEFAULT_CONVERSATION_TITLE_RE,
    Mapping,
    Path,
    _new_conversation_id,
    asyncio,
    json,
    re,
    route_nine_router_model,
    sqlite3,
    time,
)


class ConversationsMixin:
    def list_conversations(self, raw_name: Any, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        body = body or {}
        page = max(1, int(body.get("page") or 1))
        limit = max(1, min(int(body.get("limit") or 50), 1000))
        rows = self._sessions(profile_dir, limit=limit + 1, offset=(page - 1) * limit)
        return {
            "object": "hermes.agent_conversations",
            "agent": name,
            "conversations": rows[:limit],
            "pagination": {"page": page, "limit": limit, "has_more": len(rows) > limit},
        }


    def create_conversation(self, raw_name: Any, body: Mapping[str, Any]) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_conversation_request")
        session_id = self._session_id(
            body.get("id")
            or body.get("session_id")
            or body.get("conversation_id")
            or _new_conversation_id()
        )
        title = self._conversation_title(body)
        model = self._conversation_model(profile_dir, body)
        default_title = title is None or title.casefold() == DEFAULT_CONVERSATION_TITLE.casefold()
        with self._conversation_lock:
            if self._session(profile_dir, session_id) is not None:
                raise AgentAPIError(
                    "conversation already exists",
                    code="conversation_exists",
                    status=409,
                )
            for _attempt in range(100):
                if default_title:
                    title = self._next_default_conversation_title(profile_dir)
                try:
                    self._create_session(profile_dir, session_id, model=model, title=title)
                    break
                except AgentAPIError as exc:
                    # Another process can claim the next numbered title between
                    # our read and insert. Re-read and continue the sequence;
                    # the insert is atomic, so no empty session is left behind.
                    if default_title and exc.code == "conversation_name_exists":
                        continue
                    raise
            else:
                raise AgentAPIError(
                    "could not allocate a conversation name",
                    code="conversation_name_conflict",
                    status=409,
                )
        return self.get_conversation(name, session_id)


    def update_conversation(
        self,
        raw_name: Any,
        raw_session_id: Any,
        body: Mapping[str, Any],
    ) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        if not isinstance(body, Mapping):
            raise AgentAPIError("request body must be an object", code="invalid_conversation_request")
        session_id = self._session_id(raw_session_id)
        if self._session(profile_dir, session_id) is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        title = self._conversation_title(body)
        if title is None:
            raise AgentAPIError("name is required", code="invalid_conversation_request")
        self._update_session_title(profile_dir, session_id, title)
        return self.get_conversation(name, session_id)


    def delete_conversation(self, raw_name: Any, raw_session_id: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        session_id = self._session_id(raw_session_id)
        if self._session(profile_dir, session_id) is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        self._delete_session(profile_dir, session_id)
        return {
            "object": "hermes.agent_conversation_delete",
            "agent": name,
            "id": session_id,
            "deleted": True,
        }


    def get_conversation(self, raw_name: Any, raw_session_id: Any) -> dict[str, Any]:
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        session_id = self._session_id(raw_session_id)
        session = self._session(profile_dir, session_id)
        if session is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )
        return {
            "object": "hermes.agent_conversation",
            "agent": name,
            "conversation": session,
            "messages": self._messages(profile_dir, session_id),
        }


    async def compact_conversation(
        self,
        raw_name: Any,
        raw_session_id: Any,
        *,
        focus: str = "",
    ) -> dict[str, Any]:
        """Compact one persisted session in place without adding a chat message."""
        name = self._agent_name(raw_name)
        profile_dir = self._require_profile(name)
        session_id = self._session_id(raw_session_id)
        session = self._session(profile_dir, session_id)
        if session is None:
            raise AgentAPIError(
                f"Conversation not found: {session_id}",
                code="conversation_not_found",
                status=404,
            )

        compact_key = f"{name}:{session_id}"
        with self._registry_lock:
            if compact_key in self._compacting_sessions:
                raise AgentAPIError(
                    "conversation context is already being compacted",
                    code="conversation_compacting",
                    status=409,
                )
            if any(
                state.get("agent") == name
                and state.get("conversation_id") == session_id
                for state in self._active_runs.values()
            ):
                raise AgentAPIError(
                    "wait for the active response to finish before compacting context",
                    code="conversation_running",
                    status=409,
                )
            self._compacting_sessions.add(compact_key)

        try:
            result = await asyncio.to_thread(
                self._compact_conversation_sync,
                name,
                profile_dir,
                session_id,
                str(session.get("model") or ""),
                focus[:500],
            )
            self._persist_conversation_context(
                profile_dir,
                session_id,
                {
                    "used": result["after_tokens"],
                    "limit": result["context_limit"],
                    "threshold": result["context_threshold"],
                    "auto_compaction": result["auto_compaction"],
                    "model": result["model"],
                },
            )
            return result
        finally:
            with self._registry_lock:
                self._compacting_sessions.discard(compact_key)


    def _compact_conversation_sync(
        self,
        name: str,
        profile_dir: Path,
        session_id: str,
        selected_model: str,
        focus: str,
    ) -> dict[str, Any]:
        from agent.model_metadata import estimate_request_tokens_rough
        from gateway.config import PlatformConfig
        from gateway.platforms.api_server import APIServerAdapter, _api_request_profile
        from gateway.run import _profile_runtime_scope
        from gateway.session_context import clear_session_vars

        session_db = self._session_db(profile_dir)
        adapter = APIServerAdapter(PlatformConfig(enabled=True))
        adapter._session_db = session_db
        adapter._profile_scope = lambda _profile: _profile_runtime_scope(profile_dir)
        profile_token = _api_request_profile.set(name)
        session_tokens = adapter._bind_api_server_session(
            chat_id=session_id,
            session_key=session_id,
            session_id=session_id,
        )
        agent = None
        try:
            history = session_db.get_messages_as_conversation(session_id)
            if len(history) < 4:
                raise AgentAPIError(
                    "there is not enough conversation history to compact",
                    code="compact_not_enough_history",
                    status=422,
                )
            route = {"model": selected_model} if selected_model else None
            with _profile_runtime_scope(profile_dir):
                agent = adapter._create_agent(
                    session_id=session_id,
                    gateway_session_key=session_id,
                    route=route,
                )
                agent._end_session_on_close = False
                # Browser sessions retain one durable route and ID for life.
                agent.compression_in_place = True
                agent._print_fn = lambda *args, **kwargs: None
                compressor = getattr(agent, "context_compressor", None)
                if compressor is None or not compressor.has_content_to_compress(history):
                    raise AgentAPIError(
                        "there is not enough compressible context yet",
                        code="compact_nothing_to_do",
                        status=422,
                    )
                system_prompt = getattr(agent, "_cached_system_prompt", "") or ""
                tools = getattr(agent, "tools", None) or None
                before_tokens = estimate_request_tokens_rough(
                    history,
                    system_prompt=system_prompt,
                    tools=tools,
                )
                compressed, _ = agent._compress_context(
                    history,
                    system_prompt,
                    approx_tokens=before_tokens,
                    focus_topic=focus or None,
                    task_id=session_id,
                    force=True,
                )
                if getattr(agent, "_compression_skipped_due_to_lock", None):
                    raise AgentAPIError(
                        "conversation context is already being compacted",
                        code="conversation_compacting",
                        status=409,
                    )
                if bool(getattr(compressor, "_last_compress_aborted", False)):
                    raise AgentAPIError(
                        "the context summary could not be generated; no messages were changed",
                        code="compact_summary_failed",
                        status=502,
                    )
                if not bool(getattr(agent, "_last_compaction_in_place", False)):
                    raise AgentAPIError(
                        "context compaction made no changes",
                        code="compact_nothing_to_do",
                        status=422,
                    )
                after_tokens = estimate_request_tokens_rough(
                    compressed,
                    system_prompt=getattr(agent, "_cached_system_prompt", "") or system_prompt,
                    tools=getattr(agent, "tools", None) or tools,
                )
                return {
                    "conversation_id": session_id,
                    "before_tokens": max(0, int(before_tokens)),
                    "after_tokens": max(0, int(after_tokens)),
                    "messages_before": len(history),
                    "messages_after": len(compressed),
                    "focus": focus,
                    "in_place": True,
                    "model": str(getattr(agent, "model", "") or selected_model),
                    "context_limit": max(0, int(getattr(compressor, "context_length", 0) or 0)),
                    "context_threshold": max(0, int(getattr(compressor, "threshold_tokens", 0) or 0)),
                    "auto_compaction": bool(getattr(agent, "compression_enabled", False)),
                }
        except AgentAPIError:
            raise
        except Exception as exc:
            raise AgentAPIError(
                "context compaction failed; no chat message was added",
                code="compact_failed",
                status=502,
            ) from exc
        finally:
            if agent is not None:
                try:
                    agent.close()
                except Exception:
                    pass
            clear_session_vars(session_tokens)
            _api_request_profile.reset(profile_token)
            close = getattr(session_db, "close", None)
            if callable(close):
                close()


    def _conversation_title(self, body: Mapping[str, Any]) -> str | None:
        if "name" in body:
            return self._nullable_text(body["name"], field="name", max_chars=256)
        if "title" in body:
            return self._nullable_text(body["title"], field="title", max_chars=256)
        return None


    def _title_from_first_message(self, message: Any) -> str:
        """Build a short, stable fallback title from the first message."""
        candidates = []
        for line in str(message or "").splitlines():
            text = re.sub(r"^\s*(?:[-*+#>]|[0-9]+[.)])\s*", "", line).strip()
            if not text or re.fullmatch(r"`[^`]+`", text):
                continue
            text = re.sub(r"`([^`]+)`", r"\1", text)
            text = re.sub(r"\s+", " ", text).strip(" \t\r\n\"'")
            if text:
                candidates.append(text)
        title = candidates[0] if candidates else "Conversation"
        if len(title) > 48:
            shortened = title[:45].rsplit(" ", 1)[0].rstrip(".,:;- ")
            title = (shortened or title[:45]).rstrip() + "…"
        return title[0].upper() + title[1:] if title else "Conversation"


    def _conversation_has_default_title(self, profile_dir: Path, session_id: str) -> bool:
        with self._conversation_lock:
            session = self._session(profile_dir, session_id)
            current = str((session or {}).get("title") or "").strip()
            return bool(DEFAULT_CONVERSATION_TITLE_RE.fullmatch(current))


    def _clean_generated_title(self, value: Any) -> str:
        title = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n\"'`*_#")
        title = re.sub(r"^(?:title|conversation title)\s*:\s*", "", title, flags=re.I)
        title = title.strip(" \t\r\n\"'`*_#")
        title = title.rstrip(".!?:;, -")
        words = title.split()
        if len(words) > 7:
            title = " ".join(words[:7])
        if len(title) > 48:
            title = title[:48].rsplit(" ", 1)[0].rstrip(".,:;- ")
        return title


    async def _summarize_conversation_title(self, message: Any, model: str) -> str:
        try:
            generated = await asyncio.wait_for(
                self.nine_router.generate_conversation_title(str(message or ""), model),
                timeout=20,
            )
            return self._clean_generated_title(generated) or self._title_from_first_message(message)
        except Exception:
            return self._title_from_first_message(message)


    def _auto_title_conversation(
        self,
        profile_dir: Path,
        session_id: str,
        message: Any,
        *,
        suggested_title: str | None = None,
    ) -> str | None:
        """Rename a default session after its first successful chat turn."""
        with self._conversation_lock:
            session = self._session(profile_dir, session_id)
            current = str((session or {}).get("title") or "").strip()
            if not DEFAULT_CONVERSATION_TITLE_RE.fullmatch(current):
                return current or None
            base = self._clean_generated_title(suggested_title) or self._title_from_first_message(message)
            for index in range(1, 101):
                suffix = "" if index == 1 else f" {index}"
                candidate = base[: max(1, 256 - len(suffix))].rstrip() + suffix
                try:
                    self._update_session_title(profile_dir, session_id, candidate)
                    return candidate
                except AgentAPIError as exc:
                    if exc.code != "conversation_name_exists":
                        return None
            return None


    def _next_default_conversation_title(self, profile_dir: Path) -> str:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return DEFAULT_CONVERSATION_TITLE
        conn = self._open_readonly_db(db_path)
        try:
            titles = [
                str(row["title"] or "").strip()
                for row in conn.execute(
                    "SELECT title FROM sessions WHERE title IS NOT NULL"
                ).fetchall()
            ]
        except sqlite3.Error:
            titles = []
        finally:
            conn.close()

        highest = 0
        for title in titles:
            match = DEFAULT_CONVERSATION_TITLE_RE.fullmatch(title)
            if match is None:
                continue
            highest = max(highest, int(match.group(1) or 1))
        return (
            DEFAULT_CONVERSATION_TITLE
            if highest == 0
            else f"{DEFAULT_CONVERSATION_TITLE} {highest + 1}"
        )


    def _conversation_model(self, profile_dir: Path, body: Mapping[str, Any]) -> str:
        if body.get("model"):
            return route_nine_router_model(
                self._nonempty_string(body["model"], "model")
            )
        config = self._read_config(profile_dir)
        model = self._get_nested(config, ("model", "default"), None)
        if not model:
            model = self._get_nested(config, ("model", "model"), "")
        return str(model or "")


    def _session_db(self, profile_dir: Path):
        from hermes_state import SessionDB

        return SessionDB(db_path=profile_dir / "state.db")


    def _create_session(
        self,
        profile_dir: Path,
        session_id: str,
        *,
        model: str,
        title: str | None,
    ) -> None:
        # SessionDB creates the row and assigns its title in two separate
        # transactions. A title conflict therefore used to leave an untitled
        # session behind while the API returned 409. Initialize the native
        # schema first, then insert the row and title together atomically.
        try:
            db = self._session_db(profile_dir)
            close = getattr(db, "close", None)
            if callable(close):
                close()
        except Exception:
            # The SQLite fallback also initializes the small compatible schema
            # used by tests and degraded local installations.
            pass
        self._create_session_sqlite(profile_dir, session_id, model=model, title=title)


    def _create_session_sqlite(
        self,
        profile_dir: Path,
        session_id: str,
        *,
        model: str,
        title: str | None,
    ) -> None:
        db_path = profile_dir / "state.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=1.0)
        try:
            self._ensure_session_schema(conn)
            now = time.time()
            conn.execute(
                "INSERT INTO sessions (id, source, model, title, started_at) VALUES (?, ?, ?, ?, ?)",
                (session_id, "api", model, title, now),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            if "title" in str(exc).lower():
                raise AgentAPIError(
                    "conversation name already exists",
                    code="conversation_name_exists",
                    status=409,
                ) from exc
            raise AgentAPIError(
                "conversation already exists",
                code="conversation_exists",
                status=409,
            ) from exc
        finally:
            conn.close()


    def _update_session_title(self, profile_dir: Path, session_id: str, title: str) -> None:
        try:
            db = self._session_db(profile_dir)
            db.set_session_title(session_id, title)
            return
        except Exception as exc:
            if "UNIQUE" in str(exc).upper() or "already" in str(exc).lower():
                raise AgentAPIError("conversation name already exists", code="conversation_name_exists", status=409) from exc
            self._update_session_title_sqlite(profile_dir, session_id, title)


    def _update_session_title_sqlite(self, profile_dir: Path, session_id: str, title: str) -> None:
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            cursor = conn.execute("UPDATE sessions SET title = ? WHERE id = ?", (title, session_id))
            if cursor.rowcount == 0:
                raise AgentAPIError("conversation not found", code="conversation_not_found", status=404)
            conn.commit()
        except sqlite3.IntegrityError as exc:
            raise AgentAPIError("conversation name already exists", code="conversation_name_exists", status=409) from exc
        finally:
            conn.close()


    def _delete_session(self, profile_dir: Path, session_id: str) -> None:
        try:
            db = self._session_db(profile_dir)
            db.delete_session(session_id)
            return
        except Exception:
            self._delete_session_sqlite(profile_dir, session_id)


    def _delete_session_sqlite(self, profile_dir: Path, session_id: str) -> None:
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            cursor = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            if cursor.rowcount == 0:
                raise AgentAPIError("conversation not found", code="conversation_not_found", status=404)
            conn.commit()
        finally:
            conn.close()


    def _ensure_session_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                user_id TEXT,
                model TEXT,
                model_config TEXT,
                system_prompt TEXT,
                parent_session_id TEXT,
                started_at REAL NOT NULL,
                ended_at REAL,
                end_reason TEXT,
                message_count INTEGER DEFAULT 0,
                tool_call_count INTEGER DEFAULT 0,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                cache_read_tokens INTEGER DEFAULT 0,
                cache_write_tokens INTEGER DEFAULT 0,
                reasoning_tokens INTEGER DEFAULT 0,
                billing_provider TEXT,
                billing_base_url TEXT,
                billing_mode TEXT,
                estimated_cost_usd REAL,
                actual_cost_usd REAL,
                cost_status TEXT,
                cost_source TEXT,
                pricing_version TEXT,
                title TEXT,
                api_call_count INTEGER DEFAULT 0,
                FOREIGN KEY (parent_session_id) REFERENCES sessions(id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES sessions(id),
                role TEXT NOT NULL,
                content TEXT,
                tool_call_id TEXT,
                tool_calls TEXT,
                tool_name TEXT,
                timestamp REAL NOT NULL,
                token_count INTEGER,
                finish_reason TEXT,
                reasoning TEXT,
                reasoning_content TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_title_unique ON sessions(title) WHERE title IS NOT NULL")


    def _latest_session_ids(self, profile_dir: Path) -> dict[str, float]:
        return {
            item["id"]: self._session_sort_value(item)
            for item in self._sessions(profile_dir, limit=500)
            if item.get("id")
        }


    def _detect_changed_session(self, before: Mapping[str, float], after: Mapping[str, float]) -> str | None:
        new_ids = [session_id for session_id in after if session_id not in before]
        if new_ids:
            return max(new_ids, key=lambda session_id: after[session_id])
        changed = [
            session_id
            for session_id, started_at in after.items()
            if before.get(session_id) != started_at
        ]
        if changed:
            return max(changed, key=lambda session_id: after[session_id])
        return max(after, key=lambda session_id: after[session_id]) if after else None


    def _sessions(self, profile_dir: Path, *, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return []
        conn = self._open_readonly_db(db_path)
        try:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
            }
            activity_columns = [
                column
                for column in ("last_active_at", "updated_at", "ended_at", "started_at", "created_at")
                if column in columns
            ]
            sql = "SELECT * FROM sessions"
            if activity_columns:
                activity_values = [f"COALESCE({column}, 0)" for column in activity_columns]
                activity = activity_values[0] if len(activity_values) == 1 else f"MAX({', '.join(activity_values)})"
                sql += f" ORDER BY {activity} DESC, id DESC"
            else:
                sql += " ORDER BY id DESC"
            sql += " LIMIT ? OFFSET ?"
            rows = conn.execute(sql, (limit, max(0, offset))).fetchall()
            return [self._row_dict(row) for row in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()


    def _session(self, profile_dir: Path, session_id: str) -> dict[str, Any] | None:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return None
        conn = self._open_readonly_db(db_path)
        try:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return self._row_dict(row) if row else None
        except sqlite3.Error:
            return None
        finally:
            conn.close()


    def _messages(self, profile_dir: Path, session_id: str) -> list[dict[str, Any]]:
        db_path = profile_dir / "state.db"
        if not db_path.is_file():
            return []
        conn = self._open_readonly_db(db_path)
        try:
            order_by = self._preferred_order_column(conn, "messages", descending=False)
            sql = "SELECT * FROM messages WHERE session_id = ?"
            if order_by:
                sql += f" ORDER BY {order_by} ASC"
            rows = conn.execute(sql, (session_id,)).fetchall()
            return [self._row_dict(row) for row in rows]
        except sqlite3.Error:
            return []
        finally:
            conn.close()


    def _persist_conversation_context(
        self,
        profile_dir: Path,
        session_id: str,
        context: Mapping[str, Any],
    ) -> None:
        """Persist the last real prompt occupancy without changing Hermes' schema."""
        if not int(context.get("used") or 0) and not int(context.get("limit") or 0):
            return
        conn = sqlite3.connect(profile_dir / "state.db", timeout=1.0)
        try:
            row = conn.execute(
                "SELECT model_config FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return
            try:
                model_config = json.loads(row[0]) if row[0] else {}
            except (TypeError, ValueError, json.JSONDecodeError):
                model_config = {}
            if not isinstance(model_config, dict):
                model_config = {}
            stored_context: dict[str, Any] = {
                "used": max(0, int(context.get("used") or 0)),
                "limit": max(0, int(context.get("limit") or 0)),
                "model": str(context.get("model") or ""),
            }
            if context.get("route"):
                stored_context["route"] = str(context["route"])
                stored_context["route_tier"] = str(context.get("route_tier") or "")
                stored_context["reasoning"] = str(context.get("reasoning") or "")
            threshold = max(0, int(context.get("threshold") or 0))
            if threshold:
                stored_context["threshold"] = threshold
                stored_context["auto_compaction"] = bool(context.get("auto_compaction"))
            model_config["xnobrain_context"] = stored_context
            conn.execute(
                "UPDATE sessions SET model_config = ? WHERE id = ?",
                (json.dumps(model_config, separators=(",", ":")), session_id),
            )
            conn.commit()
        except sqlite3.Error:
            # Context telemetry must never make a successful chat turn fail.
            pass
        finally:
            conn.close()


    def _open_readonly_db(self, path: Path) -> sqlite3.Connection:
        uri = f"file:{path.resolve()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.0)
        conn.row_factory = sqlite3.Row
        return conn


    def _preferred_order_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        *,
        descending: bool = True,
    ) -> str | None:
        try:
            columns = {
                row["name"]
                for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
            }
        except sqlite3.Error:
            return None
        candidates = (
            ("started_at", "updated_at", "created_at", "timestamp", "id")
            if descending
            else ("timestamp", "created_at", "id")
        )
        for column in candidates:
            if column in columns:
                return column
        return None


    def _row_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        result = {}
        for key in row.keys():
            value = row[key]
            if key.endswith("_config") and isinstance(value, str) and value:
                try:
                    result[key] = json.loads(value)
                    continue
                except Exception:
                    pass
            result[key] = value
        return result
