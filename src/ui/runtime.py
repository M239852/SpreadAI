"""UI runtime helpers: main-thread marshalling, lazy rendering, chunked rendering.

Tk is single-threaded. Worker threads must never touch a widget — not even
`widget.after` — because on Tcl builds without thread support that raises
inside the worker and the UI is left waiting forever. Instead every worker
pushes a callable onto one queue that the Tk main loop drains every few
milliseconds (`start_pump`). Use `ui_call` from any thread.

`LazyRenderMixin` lets a view skip re-rendering while it is hidden and catch
up the next time it is shown, so a data refresh only rebuilds the screen the
user is looking at. `render_chunked` builds long card lists a few items per
event-loop tick so a large live slate never freezes the window.
"""
from __future__ import annotations
import logging
import queue
import threading
from typing import Callable, Iterable

log = logging.getLogger("spreadai.ui")

_QUEUE: "queue.SimpleQueue[tuple[Callable, tuple, dict]]" = queue.SimpleQueue()
_PUMP_MS = 30
_MAX_PER_TICK = 200
_pump_started = False


def ui_call(fn: Callable, *args, **kwargs) -> None:
    """Schedule `fn(*args, **kwargs)` on the Tk main thread. Safe from any thread."""
    _QUEUE.put((fn, args, kwargs))


def start_pump(root) -> None:
    """Start draining the UI queue on `root`'s event loop. Idempotent."""
    global _pump_started
    if _pump_started:
        return
    _pump_started = True

    def pump():
        for _ in range(_MAX_PER_TICK):
            try:
                fn, args, kwargs = _QUEUE.get_nowait()
            except queue.Empty:
                break
            try:
                fn(*args, **kwargs)
            except Exception:
                log.exception("UI callback failed: %s", getattr(fn, "__name__", fn))
        try:
            root.after(_PUMP_MS, pump)
        except Exception:
            # Root destroyed — stop pumping.
            pass

    pump()


def run_in_thread(work: Callable[[], object], on_done: Callable[[object], None] | None = None,
                  on_error: Callable[[BaseException], None] | None = None, *, name: str = "worker") -> threading.Thread:
    """Run `work()` on a daemon thread; deliver its result (or exception) on the UI thread."""

    def runner():
        try:
            result = work()
        except Exception as e:  # noqa: BLE001 — surfaced to the UI
            log.exception("worker '%s' failed", name)
            if on_error is not None:
                ui_call(on_error, e)
            return
        if on_done is not None:
            ui_call(on_done, result)

    t = threading.Thread(target=runner, daemon=True, name=name)
    t.start()
    return t


class LazyRenderMixin:
    """Defer `render()` while the view is hidden; catch up when shown.

    Views call `request_render()` from their state handlers instead of
    `render()`. The app shell calls `on_shown()` / `on_hidden()` as it swaps
    views.
    """

    _lazy_shown: bool = False
    _lazy_dirty: bool = True    # a lazily-built view has never rendered yet

    def request_render(self) -> None:
        if self._lazy_shown:
            self.render()          # type: ignore[attr-defined]
        else:
            self._lazy_dirty = True

    def on_shown(self) -> None:
        self._lazy_shown = True
        if self._lazy_dirty:
            self._lazy_dirty = False
            self.render()          # type: ignore[attr-defined]

    def on_hidden(self) -> None:
        self._lazy_shown = False


def render_chunked(widget, items: Iterable, build: Callable[[object], None], *, chunk: int = 4,
                   on_finished: Callable[[], None] | None = None) -> None:
    """Call `build(item)` for each item, `chunk` per event-loop tick.

    A fresh call cancels any chunked render still running on the same widget,
    so callers can simply destroy their children and call this again.
    """
    token = object()
    widget._render_token = token           # type: ignore[attr-defined]
    it = iter(items)

    def step():
        if getattr(widget, "_render_token", None) is not token:
            return
        for _ in range(chunk):
            try:
                item = next(it)
            except StopIteration:
                if on_finished:
                    on_finished()
                return
            build(item)
        try:
            widget.after(1, step)
        except Exception:
            pass

    step()
