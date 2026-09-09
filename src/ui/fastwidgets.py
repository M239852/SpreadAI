"""Plain-Tk primitives that look like the CustomTkinter kit but cost far less.

Why this exists
---------------
Every CustomTkinter widget owns a `tk.Canvas` and re-draws a rounded rectangle
(via `draw_rounded_rect_with_border`) on creation, on theme changes and on
every resize. That is fine for a handful of chrome widgets and ruinous for the
dense, repeated content in the Board / Markets / Team Slip / bet-slip lists,
where a single screen creates well over a thousand of them. On an Apple-silicon
Mac or any low-end machine the Tcl round-trips dominate the frame budget.

The widgets here are ordinary `tk.Frame` / `tk.Label`, which create no canvas
and issue no drawing commands. Where the design needs a rounded fill (pills,
small buttons) we blit a **cached** rounded-rectangle image instead of asking
Tk to redraw the shape: the cache is keyed by (width, height, radius, color),
so the second pill of a given size and color costs one `PhotoImage`
reference and nothing else.

Everything takes explicit `bg` because plain Tk has no notion of a transparent
fill: pass the color of the surface the widget sits on, and the result is
pixel-identical to the CustomTkinter version.

Rounded *containers* stay on CustomTkinter (`widgets.Card`): a plain frame
cannot mask its own square corners, and a card shell is created once per game
rather than once per cell, so the handful that remain cost little.
"""
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from typing import Callable

from . import theme as T

try:                                    # Pillow ships in requirements.txt.
    from PIL import Image, ImageDraw, ImageTk
    _HAVE_PIL = True
except Exception:                       # pragma: no cover - degraded but working
    _HAVE_PIL = False


# --------------------------------------------------------------------------
# Caches
# --------------------------------------------------------------------------

_font_cache: dict[tuple, tkfont.Font] = {}
_image_cache: dict[tuple, object] = {}
_SS = 4                                 # supersampling factor for smooth edges


def measure(text: str, font: tuple) -> int:
    """Width of `text` in pixels, without laying anything out."""
    f = _font_cache.get(font)
    if f is None:
        try:
            f = tkfont.Font(font=font)
        except Exception:
            return max(8, len(text) * 7)
        _font_cache[font] = f
    try:
        return f.measure(text)
    except Exception:
        return max(8, len(text) * 7)


def rounded_image(w: int, h: int, radius: int, color: str):
    """A cached rounded-rectangle RGBA image, transparent outside the shape.

    Tk composites the alpha against the hosting label's `bg`, so the corners
    blend with whatever surface the widget sits on.
    """
    if not _HAVE_PIL:
        return None
    w, h = max(1, int(w)), max(1, int(h))
    radius = max(0, min(int(radius), w // 2, h // 2))
    key = (w, h, radius, color)
    img = _image_cache.get(key)
    if img is not None:
        return img
    try:
        big = Image.new("RGBA", (w * _SS, h * _SS), (0, 0, 0, 0))
        ImageDraw.Draw(big).rounded_rectangle(
            (0, 0, w * _SS - 1, h * _SS - 1), radius=radius * _SS, fill=color,
        )
        img = ImageTk.PhotoImage(big.resize((w, h), Image.LANCZOS))
    except Exception:
        return None
    _image_cache[key] = img
    return img


def clear_image_cache() -> None:
    _image_cache.clear()


# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------

def frame(master, bg: str = T.BG_ELEV_1, **kw) -> tk.Frame:
    """A flat container. `bg` must match the surface behind it."""
    return tk.Frame(master, bg=bg, highlightthickness=0, bd=0, **kw)


def label(master, text: str = "", *, bg: str = T.BG_ELEV_1, fg: str = T.TEXT,
          font: tuple = T.FONT, anchor: str = "w", **kw) -> tk.Label:
    return tk.Label(master, text=text, bg=bg, fg=fg, font=font, anchor=anchor,
                    highlightthickness=0, bd=0, **kw)


class Pill(tk.Label):
    """Rounded badge drawn from a cached image — the CTk look, none of the cost."""

    def __init__(self, master, text: str, *, bg: str = T.BG_ELEV_1, variant: str = "neutral",
                 fg: str | None = None, fill: str | None = None, font: tuple = T.FONT_SMALL,
                 height: int = 20, padx: int = 9, radius: int | None = None, **kw):
        v_fill, v_fg = T.variant_colors(variant)
        self._fill = fill or v_fill
        self._fg = fg or v_fg
        self._font = font
        self._h = height
        self._padx = padx
        self._radius = height // 2 if radius is None else radius
        self._surface = bg
        super().__init__(master, text=text, bg=bg, fg=self._fg, font=font,
                         compound="center", highlightthickness=0, bd=0, **kw)
        self._apply()

    def _apply(self):
        w = measure(self.cget("text"), self._font) + self._padx * 2
        img = rounded_image(w, self._h, self._radius, self._fill)
        if img is not None:
            self.configure(image=img, width=w, height=self._h)
            self._img_ref = img
        else:                            # no Pillow: flat chip, same colors
            self.configure(bg=self._fill, padx=self._padx)

    def set(self, text: str | None = None, variant: str | None = None, fg: str | None = None):
        if variant is not None:
            self._fill, self._fg = T.variant_colors(variant)
        if fg is not None:
            self._fg = fg
        if text is not None:
            self.configure(text=text)
        self.configure(fg=self._fg)
        self._apply()


class Button(tk.Label):
    """A label that behaves like a button.

    Plain `tk.Button` is unusable here: on macOS the Aqua theme ignores `bg`,
    so a themed button turns into a grey system button. A label with bindings
    looks and behaves identically on every platform.
    """

    def __init__(self, master, text: str, command: Callable[[], None] | None = None, *,
                 bg: str = T.BG_ELEV_1, fill: str = T.BG_ELEV_3, hover: str = T.BG_ELEV_4,
                 fg: str = T.TEXT, font: tuple = T.FONT_BOLD, width: int = 0, height: int = 26,
                 radius: int = T.R_SM, padx: int = 10, state: str = "normal", **kw):
        self._command = command
        self._fill, self._hover, self._fg = fill, hover, fg
        self._font, self._h, self._padx, self._radius = font, height, padx, radius
        self._fixed_w = width          # note: `_w` is reserved by tkinter.Misc
        self._state = state
        super().__init__(master, text=text, bg=bg, fg=fg, font=font, compound="center",
                         cursor="hand2", highlightthickness=0, bd=0, **kw)
        self._paint(self._fill)
        self.bind("<Enter>", self._on_enter, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<Button-1>", self._on_click, add="+")

    def _paint(self, color: str):
        w = self._fixed_w or (measure(self.cget("text"), self._font) + self._padx * 2)
        img = rounded_image(w, self._h, self._radius, color)
        if img is not None:
            self.configure(image=img, width=w, height=self._h)
            self._img_ref = img
        else:
            self.configure(bg=color, padx=self._padx)

    def _on_enter(self, _e=None):
        if self._state == "normal":
            self._paint(self._hover)

    def _on_leave(self, _e=None):
        self._paint(self._fill)

    def _on_click(self, _e=None):
        if self._state == "normal" and self._command:
            self._command()

    def configure_state(self, state: str):
        self._state = state
        self.configure(fg=(self._fg if state == "normal" else T.TEXT_DIM),
                       cursor=("hand2" if state == "normal" else ""))

    def set_text(self, text: str):
        self.configure(text=text)
        self._paint(self._fill)


class StatBlock(tk.Frame):
    """Uppercase label, value, optional caption — the flat twin of the kit's."""

    def __init__(self, master, label_text: str, value: str = "—", *, bg: str = T.BG_ELEV_1,
                 value_color: str = T.TEXT, sub: str = "", font: tuple = T.FONT_HEAD, **kw):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, **kw)
        self._bg = bg
        self.label = label(self, label_text.upper(), bg=bg, fg=T.TEXT_MUTED, font=T.FONT_LABEL)
        self.value = label(self, value, bg=bg, fg=value_color, font=font)
        self.sub = label(self, sub, bg=bg, fg=T.TEXT_DIM, font=T.FONT_TINY)
        self.label.pack(anchor="w")
        self.value.pack(anchor="w")
        if sub:
            self.sub.pack(anchor="w")

    def set(self, value: str, color: str | None = None, sub: str | None = None):
        self.value.configure(text=value)
        if color is not None:
            self.value.configure(fg=color)
        if sub is not None:
            self.sub.configure(text=sub)
            if sub and not self.sub.winfo_manager():
                self.sub.pack(anchor="w")
            elif not sub and self.sub.winfo_manager():
                self.sub.pack_forget()


def separator(master, bg: str = T.BG_ELEV_1, color: str = T.BORDER, pady: int = 8) -> tk.Frame:
    f = tk.Frame(master, bg=color, height=1, highlightthickness=0, bd=0)
    f.pack(fill="x", pady=pady)
    return f


# --------------------------------------------------------------------------
# Hover tooltip (shared, single Toplevel)
# --------------------------------------------------------------------------

class _TooltipManager:
    """One reusable popup for the whole app.

    The kit's `Tooltip` created a `Toplevel` per hover; binding hundreds of
    them across a dense list is measurable on its own. This keeps a single
    window and swaps its text.
    """

    def __init__(self):
        self._tip: tk.Toplevel | None = None
        self._lbl: tk.Label | None = None
        self._after: str | None = None
        self._widget = None

    def attach(self, widget, text: str, delay: int = 400, wraplength: int = 380):
        widget._tip_text = text
        widget.bind("<Enter>", lambda e, w=widget: self._schedule(w, delay, wraplength), add="+")
        widget.bind("<Leave>", lambda e: self.hide(), add="+")
        widget.bind("<Button-1>", lambda e: self.hide(), add="+")

    def _schedule(self, widget, delay: int, wraplength: int):
        self.hide()
        self._widget = widget
        try:
            self._after = widget.after(delay, lambda: self._show(widget, wraplength))
        except Exception:
            self._after = None

    def _show(self, widget, wraplength: int):
        text = getattr(widget, "_tip_text", "")
        if not text:
            return
        try:
            x = widget.winfo_rootx() + 12
            y = widget.winfo_rooty() + widget.winfo_height() + 6
        except Exception:
            return
        if self._tip is None:
            self._tip = tk.Toplevel(widget)
            self._tip.wm_overrideredirect(True)
            self._tip.configure(bg=T.BORDER_STRONG)
            self._lbl = tk.Label(self._tip, justify="left", bg=T.BG_ELEV_3, fg=T.TEXT,
                                 font=T.FONT_SMALL, padx=10, pady=8)
            self._lbl.pack(padx=1, pady=1)
        self._lbl.configure(text=text, wraplength=wraplength)
        self._tip.wm_geometry(f"+{x}+{y}")
        try:
            self._tip.deiconify()
            self._tip.lift()
        except Exception:
            pass

    def hide(self):
        if self._after and self._widget is not None:
            try:
                self._widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None
        if self._tip is not None:
            try:
                self._tip.withdraw()
            except Exception:
                pass


tooltips = _TooltipManager()


def tip(widget, text: str, **kw):
    """Attach hover text using the shared popup."""
    if text:
        tooltips.attach(widget, text, **kw)


# --------------------------------------------------------------------------
# Probability bar (flat canvas, no CTk)
# --------------------------------------------------------------------------

class ProbBar(tk.Canvas):
    """Interval band, model marker and book marker on a 0–100% track.

    A canvas is unavoidable here, but this one draws four primitives and
    redraws only when the values or the width actually change.
    """

    def __init__(self, master, *, width: int = 200, height: int = 14, bg: str = T.BG_ELEV_2):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._bg = bg
        self._vals = (0.5, 0.5, 0.5, None)
        self._px_w = width             # note: `_w` is reserved by tkinter.Misc
        self.bind("<Configure>", self._on_configure, add="+")

    def _on_configure(self, e):
        if abs(e.width - self._px_w) < 2:
            return
        self._px_w = e.width
        self._draw()

    def set(self, prob: float, low: float | None = None, high: float | None = None,
            book: float | None = None):
        vals = (prob, prob if low is None else low, prob if high is None else high, book)
        if vals == self._vals:
            return
        self._vals = vals
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(self._px_w, 40)
        h = int(self["height"])
        prob, low, high, book = self._vals
        pad = 6
        y0, y1 = h / 2 - 3, h / 2 + 3
        x = lambda p: pad + (w - 2 * pad) * max(0.0, min(1.0, p))
        self.create_rectangle(pad, y0, w - pad, y1, fill=T.BG_ELEV_4, outline="")
        self.create_line(x(0.5), y0 - 2, x(0.5), y1 + 2, fill=T.BORDER_STRONG)
        if high > low:
            self.create_rectangle(x(low), y0, x(high), y1,
                                  fill=T.mix(T.MODEL_SOFT, T.MODEL, 0.35), outline="")
        if book is not None:
            self.create_line(x(book), y0 - 4, x(book), y1 + 4, fill=T.TEXT_MUTED, width=2)
        color = T.MODEL
        if book is not None:
            d = prob - book
            color = T.POSITIVE if d > 0.03 else (T.NEGATIVE if d < -0.03 else T.MODEL)
        r = 5
        self.create_oval(x(prob) - r, h / 2 - r, x(prob) + r, h / 2 + r,
                         fill=color, outline=self._bg, width=1)
