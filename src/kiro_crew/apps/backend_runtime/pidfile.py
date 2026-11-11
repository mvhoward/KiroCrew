"""The persisted identity of every spawned backend: ``app_backends.pids.json``.

App backends run in their OWN session (``start_new_session=True``) and are NOT in the
gateway's process group, so when the liveness probe SIGKILLs a wedged gateway (no
``on_cleanup`` runs) they orphan, reparent to PID 1, and accumulate across restarts.
Each spawned backend's ``(pid, start_time, port, spawn_instance)`` is therefore
persisted here and the next clean start reaps the survivors of a PRIOR generation
(:mod:`~kiro_crew.apps.backend_runtime.stale_reap`). ``start_time`` is the PID-reuse
guard: a recorded pid whose live start time does not match names another process now.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from kiro_crew import platform_compat
from kiro_crew.apps.backend_runtime import _FACADE
from kiro_crew.apps.backend_runtime.tracking import _lock, _processes
from kiro_crew.atomic_write import atomic_write
from kiro_crew.config.loader import config_dir

logger = logging.getLogger(_FACADE)


# Serializes the pidfile read-modify-write. _record_app_pid runs on the
# to_thread worker that spawns a backend (both the runtime app-enable path and
# the startup reconcile offload start_app_backend via asyncio.to_thread) while
# _forget_app_pid runs on the to_thread worker that stops one — distinct OS
# threads, so without this lock their non-atomic read-modify-writes of the
# whole JSON dict lose each other's entries.
_pidfile_lock = threading.Lock()


def _pidfile_path() -> Path:
    return config_dir() / "app_backends.pids.json"


def _proc_start_time(pid: int) -> str | None:
    """Stable per-process start time, or None if unavailable.

    PID-reuse guard: a recorded pid whose live start_time does not match has
    been recycled to an unrelated process and MUST NOT be killed. The value must
    be stable across gateway restarts (the reap compares a string recorded by a
    prior generation against one read now), so it cannot use ``hash()`` — that
    is salted per interpreter by ``PYTHONHASHSEED``.

    Per-platform sources live in ``platform_compat.process_start_time``: Linux
    reads ``/proc/<pid>/stat`` field 22, Windows the process creation FILETIME
    through a query-only handle, and other POSIX ``ps -o lstart=``. Resolving it
    there is what keeps the guard alive on Windows — a ``/proc``-or-``ps`` probe
    answers None for every pid there, and a recorded None makes the reap decline
    to confirm ANY backend, so nothing is ever reaped and the entries accumulate.
    """
    return platform_compat.process_start_time(pid)


def _read_pidfile() -> dict[str, dict[str, Any]]:
    try:
        with open(_pidfile_path()) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        # A corrupt/half-written pidfile (e.g. a SIGKILL mid-write before atomic
        # writes landed, or a leftover from an older build) silently disabling
        # the reap is exactly the leak this feature exists to prevent — log it.
        logger.warning("App-backend pidfile unreadable (%s); stale-reap skipped this start", exc)
        return {}


def _read_pidfile_for_update(
    *, set_aside_corrupt: bool = False
) -> dict[str, dict[str, Any]] | None:
    """The pidfile's rows for a rewrite, or None when it must not be rewritten.

    Only a missing file reads as empty. A file that cannot be opened (a transient
    EACCES or EIO) returns None: the caller holds rows the write would replace with
    its own, so writing over an unread file drops every other app's row, and the
    backends those rows name are then never reaped.

    A file that opens but is cut off or holds the wrong shape reads as None as well,
    unless *set_aside_corrupt* is set. Then its bytes move to a ``.corrupt-<utc>`` file
    and the rewrite starts from empty, so one damaged file does not stop every later
    spawn from being recorded.
    """
    path = _pidfile_path()
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        logger.warning("App-backend pidfile unreadable (%s); left as it is", exc)
        return None
    except ValueError:
        data = None
    if isinstance(data, dict):
        return data
    logger.warning("App-backend pidfile is not a readable table of rows")
    if not set_aside_corrupt:
        return None
    # Imported here: the backend facade re-exports every name this module binds.
    from datetime import datetime, timezone

    from kiro_crew.atomic_write import replace_with_retry

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        replace_with_retry(path, path.with_name(f"{path.name}.corrupt-{stamp}"))
    except OSError as exc:
        logger.warning("Could not set the damaged app-backend pidfile aside (%s)", exc)
        return None
    return {}


def _write_pidfile(data: dict[str, dict[str, Any]]) -> None:
    # Atomic temp-file + rename (fsync): the whole point of the pidfile is to
    # survive a gateway SIGKILL, so a non-atomic open("w") that truncates first
    # would leave an empty/partial file if the kill lands mid-write.
    try:
        atomic_write(_pidfile_path(), json.dumps(data), fsync=True)
    except OSError as exc:
        logger.debug("Could not write app-backend pidfile: %s", exc)


def _record_app_pid(
    app_name: str, pid: int, port: int, spawn_instance: str | None = None
) -> str | None:
    """Persist a spawned backend's identity for the startup stale-reap. Never raises.

    *spawn_instance* is the per-spawn ``KIROCREW_SPAWN_INSTANCE`` stamped on the
    backend's environment and inherited by its whole tree. It is what lets the
    reap vouch the group's MEMBERS once the leader itself is gone; a row written
    by an older build carries none, and the reap then declines to touch that
    group rather than aim a signal at a bare (possibly recycled) group number.
    """
    if pid <= 0:
        return None
    start_time: str | None = None
    try:
        # Compute start_time BEFORE taking the lock: the probe is slow on the
        # platforms that cannot answer from memory (a `ps` spawn on macOS, an
        # OpenProcess round trip on Windows), and holding _pidfile_lock across
        # that IO would serialize concurrent enable/stop/uninstall ops behind
        # it. Mirrors the reap path's validate-lock-free / store-under-lock
        # discipline.
        start_time = _proc_start_time(pid)
        with _pidfile_lock:
            data = _read_pidfile_for_update(set_aside_corrupt=True)
            if data is None:
                logger.warning("Backend of %s not recorded: pidfile could not be read", app_name)
                return start_time
            entry: dict[str, Any] = {"pid": pid, "start_time": start_time, "port": port}
            if spawn_instance:
                entry["spawn_instance"] = spawn_instance
            data[app_name] = entry
            _write_pidfile(data)
    except Exception as exc:  # noqa: BLE001 — persistence must never break a spawn
        logger.debug("Could not record app pid for %s: %s", app_name, exc)
    return start_time


def _spawn_is_recorded(app_name: str, pids: list[int], start_times: dict[int, str]) -> bool:
    """Whether *pids* include a live child this gateway itself spawned for *app_name*.

    Answered from the in-process spawn table (the memory behind
    :func:`~kiro_crew.apps.backend_runtime.tracking.running_spawned_backend_pids`), never
    from ``app_backends.pids.json``: that file sits in the data home a backend can write,
    so a row there would vouch for any pid. The spawn root's start time recorded at
    spawn must equal the one captured now; for a forking sandbox launcher the root's
    direct children (the real server) are claimed too, matched on their live start time.
    shortcut: a backend an earlier gateway generation spawned is not in the table and is
    refused, as is a stopped one; widen only with a memory that outlives the record.
    """
    with _lock:
        ap = _processes.get(app_name)
        proc = ap.proc if ap is not None else None
        root_start = ap.pid_start_time if ap is not None else None
        forks = ap.forking_sandbox_launcher if ap is not None else False
    if proc is None or proc.poll() is not None:
        return False
    claimed: dict[int, str | None] = {proc.pid: root_start}
    if forks:
        try:
            claimed.update(
                (c, _proc_start_time(c)) for c in platform_compat._proc_children(proc.pid)
            )
        except OSError:
            pass  # the root stays claimed on its own
    return any(
        start is not None and pid in pids and start == start_times.get(pid)
        for pid, start in claimed.items()
    )


def _adoption_attributed(app_name: str, pids: list[int], start_times: dict[int, str]) -> bool:
    """Whether a healthy listener may be adopted (or re-bound) as *app_name*'s backend.

    Any healthy listener is, unless ``agent.apps_require_recorded_backend_spawn`` is on;
    then only a live child this gateway spawned (:func:`_spawn_is_recorded`). A setting
    that cannot be read keeps main's behaviour (adopt) and logs one warning.
    """
    try:
        from kiro_crew.config.loader import KiroCrewConfig

        cfg = KiroCrewConfig.load()
        if cfg._base_unreadable or cfg._overlay_unreadable or "agent" in cfg.degraded_sections:
            raise ValueError("a config source is unreadable")
        required = cfg.agent.apps_require_recorded_backend_spawn is True
    except Exception as exc:  # noqa: BLE001 - an unreadable setting keeps the default
        logger.warning(
            "Could not read apps_require_recorded_backend_spawn (%s); adopting as main does", exc
        )
        required = False
    return not required or _spawn_is_recorded(app_name, pids, start_times)


def _forget_app_pid(app_name: str) -> None:
    """Drop an app's pidfile entry (called when no process identity is tracked)."""
    try:
        with _pidfile_lock:
            data = _read_pidfile()
            if data.pop(app_name, None) is not None:
                _write_pidfile(data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not forget app pid for %s: %s", app_name, exc)


def _forget_app_pid_if(app_name: str, pid: int, start_time: str | None) -> None:
    """Drop a pidfile row only if it still identifies the expected process."""
    try:
        with _pidfile_lock:
            data = _read_pidfile()
            entry = data.get(app_name)
            if (
                isinstance(entry, dict)
                and entry.get("pid") == pid
                and entry.get("start_time") == start_time
            ):
                data.pop(app_name, None)
                _write_pidfile(data)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Could not conditionally forget app pid for %s: %s", app_name, exc)


def retire_windows_app_tracking(pid: int, creation: int) -> None:
    """Retire only this incarnation's app rows, while its cleanup pin is held.

    This mandatory writer does not use the best-effort readers/writers: an
    unreadable file or failed atomic write must leave the cleanup receipt owed.
    No app name or caller callback is retained by the cleanup registry.
    """
    with _pidfile_lock:
        try:
            with open(_pidfile_path(), encoding="utf-8") as stream:
                data = json.load(stream)
        except FileNotFoundError:
            return
        if not isinstance(data, dict):
            raise OSError("Windows app tracking file is malformed")
        remove = [
            name
            for name, entry in data.items()
            if isinstance(entry, dict)
            and entry.get("pid") == pid
            and entry.get("start_time") == str(creation)
        ]
        if remove:
            for name in remove:
                del data[name]
            atomic_write(_pidfile_path(), json.dumps(data), fsync=True)
