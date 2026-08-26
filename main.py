"""Meta-Team CLI entry point — load agent pool, run tasks, manage sessions."""

from __future__ import annotations

import os
import sys
import json
import shutil
import signal
import asyncio
import yaml
from pathlib import Path
from typing import NamedTuple

from core.tool_registry import ToolRegistry
from core.agent import Agent
from core.session import Session
from core.run_manager import RunManager
from core.runner import Runner
from core.output_contract import output_contract_from_settings


class PoolComponents(NamedTuple):
    """Container for loaded pool: registry, agents, chairman, constitution."""
    registry: ToolRegistry
    agents: dict               # name → Agent
    chairman_name: str
    constitution: str
    settings: dict


BASE_DIR = Path(__file__).parent

_active_session: Session | None = None
TOOLS_DIR = str(BASE_DIR / "tools")
AGENTS_DIR = BASE_DIR / "agents"
DEFAULT_TEAM = "pool_SWE_Pro"


def is_pool_dir(path: Path) -> bool:
    return path.is_dir() and (path / "pool.yaml").exists()


# ---------------------------------------------------------------------------

def load_all_agents(tool_registry: ToolRegistry, team_dir: Path) -> dict[str, Agent]:
    agents = {}
    for config_path in sorted(team_dir.rglob("config.yaml")):
        agent_dir = config_path.parent
        if agent_dir == team_dir:
            continue
        agent = Agent(str(agent_dir), tool_registry)
        agents[agent.config.name] = agent
    return agents


# ---------------------------------------------------------------------------

def load_constitution(team_dir: Path) -> str:
    const_path = team_dir / "constitution.md"
    if not const_path.exists():
        return ""
    return const_path.read_text(encoding="utf-8").strip()


# ---------------------------------------------------------------------------

def load_pool_config(pool_dir: Path) -> dict:
    pool_path = pool_dir / "pool.yaml"
    if not pool_path.exists():
        return {}
    return yaml.safe_load(pool_path.read_text(encoding="utf-8")) or {}


def load_pool(pool_dir: Path) -> PoolComponents:
    """Load a complete agent pool from directory: agents, tools, constitution."""
    registry = ToolRegistry(TOOLS_DIR)
    agents = load_all_agents(registry, pool_dir)

    pool_config = load_pool_config(pool_dir)
    chairman_name = pool_config.get("chairman", "")
    if not chairman_name and agents:
        chairman_name = list(agents.keys())[0]

    constitution = load_constitution(pool_dir)
    settings = pool_config.get("settings", {})

    return PoolComponents(
        registry=registry,
        agents=agents,
        chairman_name=chairman_name,
        constitution=constitution,
        settings=settings,
    )


# ---------------------------------------------------------------------------

def copy_team_to_session(session_dir: Path, source_team_dir: Path) -> Path:
    """Copy the selected team version into a session's disposable runtime copy."""
    dest = session_dir / "team"
    # An API-infrastructure retry reuses the same case directory. Its prior
    # runtime team copy is derived data, not evolution state; replace it so
    # retrying the case can load the same immutable vNNN source cleanly.
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(
        str(source_team_dir),
        str(dest),
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    return dest


def print_pool_info(pool: PoolComponents, pool_name: str):
    print(f"  pool: {pool_name}")
    print(f"  chairman: {pool.chairman_name}")
    print(f"  agents: {len(pool.agents)} total")
    for name, agent in pool.agents.items():
        suffix = " [chairman]" if name == pool.chairman_name else ""
        desc = agent.config.description
        desc_str = f" — {desc}" if desc else ""
        print(f"    - {name}{suffix}{desc_str}")


def _save_trace(session_id: str, session_dir: str) -> None:
    import io
    import re
    import contextlib

    d = Path(session_dir)

    # --- 1. trace (full verbose) ---
    try:
        from core.trace import render_trace
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            render_trace(session_id, verbose=True, session_dir=session_dir)
        raw = buf.getvalue()

        (d / "trace.ans").write_text(raw, encoding="utf-8")

        clean = re.sub(r'\033\[[0-9;]*m', '', raw)
        (d / "trace.txt").write_text(clean, encoding="utf-8")

        html = _ansi_to_html(raw, session_id)
        (d / "trace.html").write_text(html, encoding="utf-8")
    except Exception as e:
        print(f"[warn] failed to save trace: {e}")

    try:
        from core.trace import render_overview
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            render_overview(session_id, session_dir=session_dir)
        raw = buf.getvalue()
        clean = re.sub(r'\033\[[0-9;]*m', '', raw)
        (d / "overview.txt").write_text(clean, encoding="utf-8")
    except Exception as e:
        print(f"[warn] failed to save overview: {e}")

    # --- 3. audit (summary.txt + report.html) ---
    try:
        from core.audit import audit
        audit(session_id, quiet=True, session_dir=session_dir)
    except Exception as e:
        print(f"[warn] failed to save audit: {e}")


def _ansi_to_html(text: str, title: str = "Trace") -> str:
    import re
    from html import escape

    ansi_map = {
        "1": "font-weight:bold",
        "2": "opacity:0.6",
        "31": "color:#e06c75",
        "32": "color:#98c379",
        "33": "color:#e5c07b",
        "34": "color:#61afef",
        "35": "color:#c678dd",
        "36": "color:#56b6c2",
        "37": "color:#abb2bf",
    }

    ansi_re = re.compile(r'\033\[[0-9;]*m')
    parts: list[str] = []
    depth = 0
    last_end = 0

    for m in ansi_re.finditer(text):
        if m.start() > last_end:
            parts.append(escape(text[last_end:m.start()]))
        last_end = m.end()

        codes = m.group()[2:-1].split(";")
        if codes == ["0"] or codes == [""]:
            parts.append("</span>" * depth)
            depth = 0
        else:
            styles = [ansi_map[c] for c in codes if c in ansi_map]
            if styles:
                parts.append(f'<span style="{";".join(styles)}">')
                depth += 1

    if last_end < len(text):
        parts.append(escape(text[last_end:]))
    if depth > 0:
        parts.append("</span>" * depth)

    body = "".join(parts)
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f'<title>Trace — {escape(title)}</title>'
        "<style>"
        "body{background:#282c34;color:#abb2bf;"
        "font-family:'JetBrains Mono','Fira Code',Consolas,monospace;"
        "font-size:13px;line-height:1.5;padding:20px;margin:0}"
        "pre{white-space:pre-wrap;word-wrap:break-word;margin:0}"
        "</style></head><body>"
        f"<pre>{body}</pre>"
        "</body></html>"
    )


async def run_task(task: str, team_name: str = DEFAULT_TEAM, template: str | None = None,
                   evolve: bool = False):
    """Run a single task with the loaded team and return results."""
    global _active_session
    print("initializing...")

    source_team_dir = AGENTS_DIR / team_name
    if not source_team_dir.exists():
        raise FileNotFoundError(f"Team '{team_name}' not found: {source_team_dir}")

    run_mgr = None
    if evolve:
        run_mgr = RunManager.create_run(
            source_team_dir=source_team_dir,
            team_name=team_name,
            config={"mode": "run_task", "evolve": True},
        )
        _, actual_source = run_mgr.get_latest_team_version()
        print(f"  run: {run_mgr.run_dir}")
    else:
        actual_source = source_team_dir

    pool = load_pool(actual_source)
    team_info = {
        "team_name": team_name,
        "structure": "pool",
        "chairman": pool.chairman_name,
        "total_agents": len(pool.agents),
        "agents": list(pool.agents.keys()),
    }

    if run_mgr:
        case_dir = run_mgr.create_case_dir(0, "task")
        session = Session.create(task=task, team_info=team_info, template=template,
                                 session_dir=case_dir)
    else:
        session = Session.create(task=task, team_info=team_info, template=template)
    _active_session = session

    runtime_team_dir = copy_team_to_session(Path(session.dir), actual_source)

    pool = load_pool(runtime_team_dir)
    team_selection = None
    if pool.settings.get("team_selection_enabled", False):
        from benchmarks.adapter import _load_team_selection

        team_selection, _ = await _load_team_selection(
            runtime_team_dir, pool, task
        )
        session.team_info["team_selection"] = team_selection.to_dict()
        session._write_meta("running")
    print(f"  tools: {pool.registry.all_names()}")
    print_pool_info(pool, team_name)

    print(f"\n  session: {session.id}")
    print(f"  workspace: {session.workspace}")
    print(f"  team_dir: {runtime_team_dir}")
    if template:
        print(f"  template: {template}")
    if evolve:
        print(f"  reflection: enabled")
    print()

    print(f"task: {task}")
    print("-" * 60)

    try:
        effective_task_seconds = float(pool.settings.get(
            "effective_task_seconds", pool.settings.get("max_seconds", 600)
        ))
        wall_clock_seconds = float(pool.settings.get(
            "wall_clock_seconds", effective_task_seconds
        ))
        runner = Runner(
            pool_dir=runtime_team_dir,
            chairman_name=pool.chairman_name,
            constitution=pool.constitution,
            tool_registry=pool.registry,
            max_seconds=effective_task_seconds,
            wall_clock_seconds=wall_clock_seconds,
            max_messages=int(pool.settings.get("max_messages", 200)),
            enable_reflection=evolve,
            reflection_phase_timeout=float(pool.settings.get("reflection_phase_timeout", 0)),
            allowed_agent_ids=(
                set(team_selection.allowed_agent_ids)
                if team_selection is not None
                else None
            ),
            team_selection_metadata=(
                team_selection.to_dict() if team_selection is not None else {}
            ),
            output_contract=output_contract_from_settings(
                pool.settings, pool.settings.get("output_contract")
            ),
        )
        result = await runner.run(task, session=session)

        print("-" * 60)
        print(f"result ({len(result.agents)} agents, {result.cost_seconds:.1f}s):")
        print("=" * 60)
        print(result.output)
        print("=" * 60)

        if evolve and run_mgr:
            new_ver, modified = run_mgr.persist_team_version(
                session_team_dir=runtime_team_dir,
                include_l3=result.reflection_applied,
            )
            if modified:
                run_mgr.write_changelog(
                    case_index=0, task_id="task",
                    from_version="v000", to_version=new_ver,
                    modified_files=modified,
                    reflection_applied=result.reflection_applied,
                )

        session.close("completed", summary={
            "agents_used": list(result.agents.keys()),
            "total_events": session.event_log.count,
            "cost_seconds": round(result.cost_seconds, 2),
        })
    except KeyboardInterrupt:
        print("\n[interrupted] saving session...")
        session.close("interrupted", summary={"error": "KeyboardInterrupt"})
    except asyncio.CancelledError:
        if _active_session is None:
            return
        print("\n[cancelled] saving session...")
        session.close("interrupted", summary={"error": "asyncio.CancelledError"})
    except (SystemExit, GeneratorExit):
        if _active_session is None:
            raise
        session.close("interrupted", summary={"error": "SystemExit"})
        _save_trace(session.id, session.dir)
        _active_session = None
        raise
    except Exception as e:
        session.close("failed", summary={"error": str(e)})
        _save_trace(session.id, session.dir)
        _active_session = None
        raise

    _save_trace(session.id, session.dir)
    _active_session = None
    if run_mgr:
        run_mgr.save_summary({"mode": "run_task", "team_name": team_name})
        print(f"\nrun saved: {run_mgr.run_dir}")
    else:
        print(f"\nsession saved: {session.dir}")


async def _run_interactive_task(execute_coro, session: Session,
                                evolve: bool = False,
                                runtime_team_dir: Path | None = None,
                                run_mgr: "RunManager | None" = None,
                                case_index: int = 0) -> None:
    global _active_session
    _active_session = session
    try:
        result = await execute_coro
        summary = {
            "total_events": session.event_log.count,
            "cost_seconds": round(result.cost_seconds, 2),
        }
        session.close("completed", summary=summary)
        print("=" * 60)
        print(result.output)
        print("=" * 60)

        if evolve and run_mgr and runtime_team_dir:
            include_l3 = getattr(result, "reflection_applied", False)
            team_ver_before, _ = run_mgr.get_latest_team_version()
            new_ver, modified = run_mgr.persist_team_version(
                session_team_dir=runtime_team_dir,
                include_l3=include_l3,
            )
            if modified:
                run_mgr.write_changelog(
                    case_index=case_index,
                    task_id=f"interactive_{case_index}",
                    from_version=team_ver_before,
                    to_version=new_ver,
                    modified_files=modified,
                    reflection_applied=include_l3,
                )
    except KeyboardInterrupt:
        print("\n[interrupted] saving session...")
        session.close("interrupted", summary={"error": "KeyboardInterrupt"})
    except asyncio.CancelledError:
        print("\n[cancelled] saving session...")
        session.close("interrupted", summary={"error": "asyncio.CancelledError"})
    except Exception as e:
        session.close("failed", summary={"error": str(e)})
        import traceback
        traceback.print_exc()
    _save_trace(session.id, session.dir)
    _active_session = None


async def interactive(team_name: str = DEFAULT_TEAM, template: str | None = None,
                      evolve: bool = False):
    source_team_dir = AGENTS_DIR / team_name
    if not source_team_dir.exists():
        raise FileNotFoundError(f"Team '{team_name}' not found: {source_team_dir}")

    run_mgr = None
    if evolve:
        run_mgr = RunManager.create_run(
            source_team_dir=source_team_dir,
            team_name=team_name,
            config={"mode": "interactive", "evolve": True},
        )
        _, actual_source = run_mgr.get_latest_team_version()
    else:
        actual_source = source_team_dir

    print("initializing...")
    pool = load_pool(actual_source)
    print(f"  tools: {pool.registry.all_names()}")
    print_pool_info(pool, team_name)
    pre_team_info = {
        "team_name": team_name,
        "structure": "pool",
        "chairman": pool.chairman_name,
        "total_agents": len(pool.agents),
        "agents": list(pool.agents.keys()),
    }

    print()
    print("agent-teams interactive mode. type 'quit' to exit.")
    if template:
        print(f"  template: {template}")
    if evolve:
        print(f"  reflection: enabled")
        print(f"  run: {run_mgr.run_dir}")
    print()

    case_index = 0
    while True:
        try:
            task = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break
        if not task:
            continue
        if task.lower() in ("quit", "exit", "q"):
            print("bye.")
            break

        if run_mgr:
            _, actual_source = run_mgr.get_latest_team_version()
            case_dir = run_mgr.create_case_dir(case_index, f"interactive_{case_index}")
            session = Session.create(task=task, team_info=pre_team_info,
                                     template=template, session_dir=case_dir)
        else:
            session = Session.create(task=task, team_info=pre_team_info, template=template)

        runtime_team_dir = copy_team_to_session(Path(session.dir), actual_source)

        print(f"[session: {session.id}]")
        print("-" * 60)

        pool = load_pool(runtime_team_dir)
        effective_task_seconds = float(pool.settings.get(
            "effective_task_seconds", pool.settings.get("max_seconds", 600)
        ))
        wall_clock_seconds = float(pool.settings.get(
            "wall_clock_seconds", effective_task_seconds
        ))
        runner = Runner(
            pool_dir=runtime_team_dir,
            chairman_name=pool.chairman_name,
            constitution=pool.constitution,
            tool_registry=pool.registry,
            max_seconds=effective_task_seconds,
            wall_clock_seconds=wall_clock_seconds,
            max_messages=int(pool.settings.get("max_messages", 200)),
            enable_reflection=evolve,
            reflection_phase_timeout=float(pool.settings.get("reflection_phase_timeout", 0)),
            output_contract=output_contract_from_settings(
                pool.settings, pool.settings.get("output_contract")
            ),
        )
        await _run_interactive_task(
            runner.run(task, session=session), session,
            evolve=evolve,
            runtime_team_dir=runtime_team_dir,
            run_mgr=run_mgr,
            case_index=case_index,
        )
        case_index += 1
        print()

    if run_mgr:
        run_mgr.save_summary({"mode": "interactive", "team_name": team_name,
                              "total_tasks": case_index})
        print(f"\nrun saved: {run_mgr.run_dir}")


def show_history():
    sessions = Session.list_sessions()
    if not sessions:
        print("no sessions found.")
        return
    print(f"{'ID':<20} {'Status':<12} {'Task':<50}")
    print("-" * 82)
    for s in sessions:
        task = s.get("task", "")[:50]
        print(f"{s['id']:<20} {s['status']:<12} {task}")


def replay_session(session_id: str):
    events = Session.load_events(session_id)
    if not events:
        print(f"no events found for session {session_id}")
        return
    print(f"session: {session_id} ({len(events)} events)")
    print("=" * 80)
    for e in events:
        ts = e.get("ts", "")
        seq = e.get("seq", "")
        etype = e.get("type", "")
        agent = e.get("agent", "")
        data = e.get("data", {})

        prefix = f"[{seq:>3}] {ts} {etype}"
        if agent:
            prefix += f" ({agent})"

        print(prefix)

        if etype == "tool.call":
            print(f"      tool={data.get('tool')} args={json.dumps(data.get('args', {}), ensure_ascii=False)[:200]}")
        elif etype == "tool.result":
            output = data.get("output", "")[:200]
            print(f"      tool={data.get('tool')} output={output}")
        elif etype == "llm.call":
            print(f"      model={data.get('model')} in={data.get('input_tokens')} out={data.get('output_tokens')}")
        elif etype == "agent.finish":
            output = data.get("output", "")[:200]
            print(f"      steps={data.get('steps')} output={output}")
        elif etype == "agent.plan":
            for st in data.get("subtasks", []):
                print(f"      → {st.get('assigned_to')}: {st.get('description', '')[:100]}")
        elif etype == "committee.dispatch":
            for d in data.get("dispatches", []):
                print(f"      → {d.get('team')}: {d.get('task', '')[:100]}")
        elif etype == "committee.plan_update":
            print(f"      plan: {data.get('plan_summary', '')[:200]}")
        elif etype == "committee.finalize":
            print(f"      output: {data.get('output', '')[:200]}")
        elif etype == "runner.start":
            print(f"      chairman={data.get('chairman')} pool={data.get('pool_dir', '')}"
                  f" reflection={'on' if data.get('enable_reflection') else 'off'}")
        elif etype == "runner.end":
            print(f"      output={data.get('output', '')[:200]}")
            print(f"      cost={data.get('cost_seconds', 0):.1f}s agents={data.get('agents_used', [])}")
        elif etype == "runner.timeout":
            print(f"      max_seconds={data.get('max_seconds')} elapsed={data.get('elapsed')}s")
        elif etype in ("runner.phase_change", "committee.phase_change"):
            print(f"      {data.get('from', '')} → {data.get('to', '')}")
        elif etype in ("runner.restart_agents_for_phase", "committee.restart_agents"):
            print(f"      restarted={data.get('restarted', [])} refreshed={data.get('refreshed', [])}")
        elif etype == "runner.guard":
            print(f"      reason={data.get('reason', '')} count={data.get('count', '')}")
        elif etype.startswith("committee."):
            print(f"      {json.dumps(data, ensure_ascii=False)[:200]}")
        elif etype.startswith("runner."):
            print(f"      {json.dumps(data, ensure_ascii=False)[:200]}")
    print("=" * 80)


def _watch_trace(session_id: str, full: bool = False, verbose: bool = False,
                 agent_filter: str | None = None, interval: float = 3.0):
    import os
    import time as _time
    from core.trace import render_trace, render_overview
    from core.session import SESSIONS_DIR

    events_path = SESSIONS_DIR / session_id / "events.jsonl"
    if not events_path.exists():
        print(f"Waiting for session {session_id} to start...")

    last_size = -1
    try:
        while True:
            cur_size = events_path.stat().st_size if events_path.exists() else 0
            if cur_size != last_size:
                last_size = cur_size
                os.system("clear" if os.name != "nt" else "cls")
                try:
                    if agent_filter:
                        render_trace(session_id, verbose=verbose, agent_filter=agent_filter)
                    elif full:
                        render_trace(session_id, verbose=verbose)
                    else:
                        render_overview(session_id)
                except Exception as e:
                    print(f"[render error] {e}")
                print(f"\n  🔄 Live watch (refresh {interval}s) — Ctrl+C to stop")
                meta_path = SESSIONS_DIR / session_id / "session.json"
                if meta_path.exists():
                    import json
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    status = meta.get("status", "")
                    if status in ("completed", "failed", "interrupted"):
                        print(f"  ■ Session ended: {status}")
                        break
            _time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  Stopped watching.")


def _handle_sigterm(signum, frame):
    global _active_session
    session = _active_session
    if session:
        try:
            print(f"\n[SIGTERM] saving session {session.id}...")
            session.close("interrupted", summary={"error": "SIGTERM"})
            _save_trace(session.id, session.dir)
            print(f"session saved: {session.dir}")
        except Exception as e:
            print(f"[SIGTERM] failed to save session: {e}")
        _active_session = None
    sys.exit(128 + signal.SIGTERM)


def main():
    signal.signal(signal.SIGTERM, _handle_sigterm)

    args = sys.argv[1:]

    if not args:
        asyncio.run(interactive())
        return

    if args[0] in ("--help", "-h"):
        print("Usage: python main.py [--team POOL] [--model MODEL] [--evolve] TASK")
        print("       python main.py --history | --replay [SESSION] | --audit [SESSION]")
        return

    if args[0] == "--history":
        show_history()
        return

    if args[0] == "--replay":
        sid = args[1] if len(args) > 1 else None
        if not sid or sid == "latest":
            sid = Session.get_latest_session_id()
            if not sid:
                print("No sessions found.")
                return
            print(f"  → latest session: {sid}")
        replay_session(sid)
        return

    if args[0] == "--audit":
        from core.audit import audit
        sid = args[1] if len(args) > 1 else None
        if not sid or sid == "latest":
            sid = Session.get_latest_session_id()
            if not sid:
                print("No sessions found.")
                return
            print(f"  → latest session: {sid}")
        result = audit(sid)
        if result:
            print(f"\nOpen in browser: {result[1]}")
        return

    if args[0] == "--trace":
        from core.trace import render_trace, render_overview
        from core.session import Session as _Ses
        rest = args[1:]
        sid = None
        if rest and not rest[0].startswith("--"):
            candidate = rest.pop(0)
            if candidate != "latest":
                sid = candidate
        if sid is None:
            sid = _Ses.get_latest_session_id()
            if sid is None:
                print("No sessions found.")
                return
            print(f"  → latest session: {sid}")

        verbose = "--verbose" in rest or "-v" in rest
        full = "--full" in rest
        watch = "--watch" in rest or "-w" in rest
        # --agent <name>
        agent_filter = None
        if "--agent" in rest:
            idx = rest.index("--agent")
            if idx + 1 < len(rest):
                agent_filter = rest[idx + 1]

        if watch:
            _watch_trace(sid, full=full, verbose=verbose, agent_filter=agent_filter)
        elif agent_filter:
            render_trace(sid, verbose=verbose, agent_filter=agent_filter)
        elif full:
            render_trace(sid, verbose=verbose)
        else:
            render_overview(sid)
        return

    team_name = DEFAULT_TEAM
    template = None
    evolve = False

    while args and args[0].startswith("--"):
        if args[0] in ("--team", "--pool") and len(args) > 1:
            team_name = args[1]
            args = args[2:]
        elif args[0] == "--template" and len(args) > 1:
            template = args[1]
            args = args[2:]
        elif args[0] == "--evolve":
            evolve = True
            args = args[1:]
        elif args[0] == "--model" and len(args) > 1:
            os.environ["META_TEAM_MODEL"] = args[1]
            args = args[2:]
        else:
            break

    if not args:
        asyncio.run(interactive(team_name, template, evolve=evolve))
    else:
        task = " ".join(args)
        asyncio.run(run_task(task, team_name, template, evolve=evolve))


if __name__ == "__main__":
    main()
