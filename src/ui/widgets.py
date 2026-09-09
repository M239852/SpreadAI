"""Reusable widget kit built on customtkinter + a few raw Tk canvases.

Views compose these instead of hand-rolling frames so the whole app shares
one visual language: `PageHeader` for every screen, `Segmented` for mode
pickers, `Pill`/`StatBlock`/`MetricTile` for numbers, and the canvas widgets
(`ProbBar`, `FactorBar`, `Sparkline`) for the probability visuals the model
produces.
"""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence

import customtkinter as ctk

from . import theme as T


# ---------------------------------------------------------------- containers

class Card(ctk.CTkFrame):
    """Elevated card container with a hairline border."""

    def __init__(self, master, *, padding: int = 16, **kwargs):
        super().__init__(
            master,
            fg_color=kwargs.pop("fg_color", T.BG_ELEV_1),
            corner_radius=kwargs.pop("corner_radius", T.R_LG),
            border_width=kwargs.pop("border_width", 1),
            border_color=kwargs.pop("border_color", T.BORDER),
            **kwargs,
        )
        self._pad = padding

    def body(self, padx: int | None = None, pady: int | None = None, **pack) -> ctk.CTkFrame:
        """Transparent inner frame with the card's padding applied."""
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill=pack.pop("fill", "x"), expand=pack.pop("expand", False),
                   padx=self._pad if padx is None else padx,
                   pady=self._pad if pady is None else pady, **pack)
        return inner


def make_scroll(master, **kwargs) -> ctk.CTkScrollableFrame:
    return ctk.CTkScrollableFrame(
        master,
        fg_color=kwargs.pop("fg_color", T.BG),
        scrollbar_button_color=T.BORDER_STRONG,
        scrollbar_button_hover_color=T.ACCENT,
        **kwargs,
    )


def hsep(master, pad_y: int = 8, color: str = T.BORDER):
    f = ctk.CTkFrame(master, height=1, fg_color=color)
    f.pack(fill="x", pady=pad_y)
    return f


def section_label(master, text: str, **pack) -> ctk.CTkLabel:
    lbl = ctk.CTkLabel(master, text=text.upper(), font=T.FONT_LABEL, text_color=T.TEXT_MUTED, anchor="w")
    lbl.pack(anchor="w", **pack)
    return lbl


# ---------------------------------------------------------------- pills & numbers

class Pill(ctk.CTkLabel):
    """Small rounded badge. Use `variant` for semantic colors."""

    def __init__(self, master, text: str, *, variant: str | None = None,
                 color: str | None = None, text_color: str | None = None, font=None, **kwargs):
        bg, fg = T.variant_colors(variant or "neutral")
        super().__init__(
            master,
            text=f"  {text}  ",
            fg_color=color or bg,
            text_color=text_color or fg,
            corner_radius=T.R_PILL,
            font=font or T.FONT_SMALL,
            height=kwargs.pop("height", 22),
            **kwargs,
        )

    def set(self, text: str, variant: str | None = None, text_color: str | None = None):
        cfg = {"text": f"  {text}  "}
        if variant is not None:
            bg, fg = T.variant_colors(variant)
            cfg.update(fg_color=bg, text_color=fg)
        if text_color is not None:
            cfg["text_color"] = text_color
        self.configure(**cfg)


class StatBlock(ctk.CTkFrame):
    """Label + value (+ optional sub caption) used in summary strips."""

    def __init__(self, master, label: str, value: str, *, value_color: str = T.TEXT,
                 sub: str = "", font=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.label = ctk.CTkLabel(self, text=label.upper(), font=T.FONT_LABEL, text_color=T.TEXT_MUTED, anchor="w")
        self.value = ctk.CTkLabel(self, text=value, font=font or T.FONT_HEAD, text_color=value_color, anchor="w")
        self.sub = ctk.CTkLabel(self, text=sub, font=T.FONT_TINY, text_color=T.TEXT_DIM, anchor="w")
        self.label.pack(anchor="w")
        self.value.pack(anchor="w")
        if sub:
            self.sub.pack(anchor="w")

    def set(self, value: str, color: str | None = None, sub: str | None = None):
        self.value.configure(text=value)
        if color is not None:
            self.value.configure(text_color=color)
        if sub is not None:
            self.sub.configure(text=sub)
            if sub and not self.sub.winfo_manager():
                self.sub.pack(anchor="w")
            elif not sub and self.sub.winfo_manager():
                self.sub.pack_forget()


class MetricTile(ctk.CTkFrame):
    """A boxed metric: label, big value, small caption."""

    def __init__(self, master, label: str, value: str = "—", *, sub: str = "",
                 value_color: str = T.TEXT, **kwargs):
        super().__init__(master, fg_color=kwargs.pop("fg_color", T.BG_ELEV_2),
                         corner_radius=T.R_MD, **kwargs)
        inner = ctk.CTkFrame(self, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=T.SP_3, pady=T.SP_3)
        self.block = StatBlock(inner, label, value, value_color=value_color, sub=sub, font=T.FONT_TITLE)
        self.block.pack(anchor="w")

    def set(self, value: str, color: str | None = None, sub: str | None = None):
        self.block.set(value, color, sub)


# ---------------------------------------------------------------- controls

class Segmented(ctk.CTkFrame):
    """Single-choice segmented control.

    options: sequence of (key, label) or (key, label, subtitle). `command`
    receives the selected key. Renders as a rounded track with flat buttons.
    """

    def __init__(self, master, options: Sequence[tuple], value: str | None = None,
                 command: Callable[[str], None] | None = None, *, accent: str = T.ACCENT,
                 height: int = 30, width: int | None = None, font=None, tall: bool = False, **kwargs):
        super().__init__(master, fg_color=kwargs.pop("fg_color", T.BG_ELEV_2), corner_radius=T.R_MD, **kwargs)
        self._command = command
        self._accent = accent
        self._buttons: dict[str, ctk.CTkButton] = {}
        self._value: str | None = None
        for opt in options:
            key, label = opt[0], opt[1]
            sub = opt[2] if len(opt) > 2 else ""
            text = f"{label}\n{sub}" if (tall and sub) else label
            b = ctk.CTkButton(
                self, text=text, height=(54 if tall else height), width=width or 0,
                fg_color="transparent", hover_color=T.BG_ELEV_4,
                text_color=T.TEXT_MUTED, font=font or T.FONT_BOLD,
                corner_radius=T.R_SM, anchor=("w" if tall else "center"),
                command=lambda k=key: self._select(k, fire=True),
            )
            b.pack(side="left", padx=3, pady=3, fill=("x" if tall else None), expand=tall)
            self._buttons[key] = b
        if value is not None:
            self._select(value, fire=False)

    def _select(self, key: str, fire: bool):
        if key not in self._buttons:
            return
        self._value = key
        for k, b in self._buttons.items():
            if k == key:
                b.configure(fg_color=self._accent, text_color=T.ACCENT_TEXT)
            else:
                b.configure(fg_color="transparent", text_color=T.TEXT_MUTED)
        if fire and self._command:
            self._command(key)

    def set(self, key: str):
        self._select(key, fire=False)

    def get(self) -> str | None:
        return self._value

    def set_accent(self, color: str):
        self._accent = color
        if self._value:
            self._select(self._value, fire=False)


class IconButton(ctk.CTkButton):
    """Small ghost button for toolbars."""

    def __init__(self, master, text: str, command=None, *, width: int = 32, **kwargs):
        super().__init__(
            master, text=text, width=width, height=kwargs.pop("height", 30),
            fg_color=kwargs.pop("fg_color", "transparent"), hover_color=kwargs.pop("hover_color", T.BG_ELEV_3),
            text_color=kwargs.pop("text_color", T.TEXT_MUTED), font=kwargs.pop("font", T.FONT_BOLD),
            corner_radius=T.R_SM, command=command, **kwargs,
        )


class PrimaryButton(ctk.CTkButton):
    def __init__(self, master, text: str, command=None, **kwargs):
        super().__init__(
            master, text=text, height=kwargs.pop("height", 36),
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.ACCENT_TEXT,
            font=kwargs.pop("font", T.FONT_BOLD), corner_radius=kwargs.pop("corner_radius", T.R_SM),
            command=command, **kwargs,
        )


class GhostButton(ctk.CTkButton):
    def __init__(self, master, text: str, command=None, **kwargs):
        super().__init__(
            master, text=text, height=kwargs.pop("height", 32),
            fg_color=kwargs.pop("fg_color", T.BG_ELEV_3), hover_color=kwargs.pop("hover_color", T.BG_ELEV_4),
            text_color=kwargs.pop("text_color", T.TEXT), font=kwargs.pop("font", T.FONT_BOLD),
            corner_radius=kwargs.pop("corner_radius", T.R_SM), command=command, **kwargs,
        )


def option_menu(master, values: list[str], command=None, width: int = 180, **kwargs) -> ctk.CTkOptionMenu:
    return ctk.CTkOptionMenu(
        master, values=values, width=width, height=kwargs.pop("height", 30),
        fg_color=T.BG_ELEV_3, button_color=T.BG_ELEV_4, button_hover_color=T.BORDER_STRONG,
        text_color=T.TEXT, dropdown_fg_color=T.BG_ELEV_2, dropdown_hover_color=T.BG_ELEV_4,
        dropdown_text_color=T.TEXT, font=T.FONT_SMALL, corner_radius=T.R_SM,
        command=command, **kwargs,
    )


def entry(master, textvariable=None, width: int = 140, placeholder: str = "", **kwargs) -> ctk.CTkEntry:
    return ctk.CTkEntry(
        master, textvariable=textvariable, width=width, height=kwargs.pop("height", 30),
        fg_color=T.BG_ELEV_3, border_width=1, border_color=T.BORDER, text_color=T.TEXT,
        placeholder_text=placeholder or None, placeholder_text_color=T.TEXT_DIM,
        font=kwargs.pop("font", T.FONT), corner_radius=T.R_SM, **kwargs,
    )


def switch(master, text: str, variable, command=None, **kwargs) -> ctk.CTkSwitch:
    return ctk.CTkSwitch(
        master, text=text, variable=variable, font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        progress_color=T.ACCENT, button_color=T.TEXT, button_hover_color=T.TEXT,
        fg_color=T.BG_ELEV_4, command=command, **kwargs,
    )


def checkbox(master, text: str, variable, command=None, **kwargs) -> ctk.CTkCheckBox:
    return ctk.CTkCheckBox(
        master, text=text, variable=variable, font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        fg_color=T.ACCENT, border_color=T.BORDER_STRONG, hover_color=T.ACCENT_HOVER,
        checkmark_color=T.ACCENT_TEXT, command=command, **kwargs,
    )


def slider(master, from_: float, to: float, steps: int, command=None, width: int = 200, **kwargs) -> ctk.CTkSlider:
    return ctk.CTkSlider(
        master, from_=from_, to=to, number_of_steps=steps, width=width,
        fg_color=T.BG_ELEV_4, progress_color=T.ACCENT, button_color=T.ACCENT,
        button_hover_color=T.ACCENT_HOVER, command=command, **kwargs,
    )


# ---------------------------------------------------------------- page chrome

class PageHeader(ctk.CTkFrame):
    """Standard screen header: title, subtitle, right-hand actions, source pill,
    and an optional banner strip that always renders directly under the header.
    """

    def __init__(self, master, title: str, subtitle: str = "", *, show_source: bool = True):
        super().__init__(master, fg_color="transparent")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", padx=T.PAGE_PAD_X, pady=(T.SP_5, T.SP_2))

        left = ctk.CTkFrame(row, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        self.title = ctk.CTkLabel(left, text=title, font=T.FONT_TITLE, text_color=T.TEXT, anchor="w")
        self.title.pack(anchor="w")
        self.subtitle = ctk.CTkLabel(left, text=subtitle, font=T.FONT_SMALL, text_color=T.TEXT_MUTED, anchor="w")
        self.subtitle.pack(anchor="w", pady=(2, 0))

        self.actions = ctk.CTkFrame(row, fg_color="transparent", width=1, height=1)
        self.actions.pack(side="right")
        self.source = Pill(self.actions, "DEMO", variant="neutral")
        if show_source:
            self.source.pack(side="right", padx=(T.SP_2, 0))

        self._banner = ctk.CTkLabel(
            self, text="", font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            fg_color=T.BG_ELEV_2, corner_radius=T.R_SM, anchor="w", height=32,
        )

    def set_subtitle(self, text: str):
        self.subtitle.configure(text=text)

    def set_source(self, source: str, label: str | None = None):
        """source: 'live' | 'demo' | 'none'."""
        if source == "live":
            self.source.set(label or "LIVE", variant="positive")
        elif source == "demo":
            self.source.set(label or "DEMO", variant="neutral")
        else:
            self.source.set(label or "—", variant="neutral")

    def show_banner(self, text: str, variant: str = "neutral"):
        bg, fg = T.variant_colors(variant)
        if variant == "neutral":
            bg, fg = T.BG_ELEV_2, T.TEXT_MUTED
        self._banner.configure(text=f"   {text}", fg_color=bg, text_color=fg)
        if not self._banner.winfo_manager():
            self._banner.pack(fill="x", padx=T.PAGE_PAD_X, pady=(0, T.SP_2))

    def hide_banner(self):
        if self._banner.winfo_manager():
            self._banner.pack_forget()


class Toolbar(ctk.CTkFrame):
    """Compact horizontal bar for filters and controls."""

    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=kwargs.pop("fg_color", T.BG_ELEV_1), corner_radius=T.R_MD, **kwargs)
        self.inner = ctk.CTkFrame(self, fg_color="transparent")
        self.inner.pack(fill="x", padx=T.SP_3, pady=T.SP_2)

    def label(self, text: str, **pack) -> ctk.CTkLabel:
        lbl = ctk.CTkLabel(self.inner, text=text.upper(), font=T.FONT_LABEL, text_color=T.TEXT_MUTED)
        lbl.pack(side="left", padx=pack.pop("padx", (0, T.SP_2)), **pack)
        return lbl


class EmptyState(ctk.CTkFrame):
    def __init__(self, master, title: str, hint: str = "", *, icon: str = "◌",
                 action_text: str = "", action=None):
        super().__init__(master, fg_color="transparent")
        ctk.CTkLabel(self, text=icon, font=(T.FONT_FAMILY, 30), text_color=T.TEXT_DIM).pack(pady=(0, T.SP_2))
        ctk.CTkLabel(self, text=title, font=T.FONT_HEAD, text_color=T.TEXT_MUTED).pack()
        if hint:
            ctk.CTkLabel(self, text=hint, font=T.FONT_SMALL, text_color=T.TEXT_DIM, justify="center").pack(pady=(4, 0))
        if action_text and action:
            GhostButton(self, action_text, action).pack(pady=(T.SP_3, 0))


class Tooltip:
    """Hover tooltip for any widget (text can be multi-line)."""

    def __init__(self, widget, text: str, *, delay_ms: int = 350, wraplength: int = 360):
        self.widget = widget
        self.text = text
        self.delay = delay_ms
        self.wraplength = wraplength
        self._after: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after:
            try:
                self.widget.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    def _show(self):
        if self._tip or not self.text:
            return
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except Exception:
            return
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        self._tip.configure(bg=T.BORDER_STRONG)
        lbl = tk.Label(
            self._tip, text=self.text, justify="left", wraplength=self.wraplength,
            bg=T.BG_ELEV_3, fg=T.TEXT, font=T.FONT_SMALL, padx=10, pady=8,
        )
        lbl.pack(padx=1, pady=1)

    def _hide(self, _e=None):
        self._cancel()
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


# ---------------------------------------------------------------- canvas visuals

class ProbBar(tk.Canvas):
    """Horizontal probability bar: interval band, model marker and book marker.

    set(prob, low, high, book) — all in 0..1. Draws on a track spanning 0–100%.
    """

    def __init__(self, master, *, width: int = 220, height: int = 14, bg: str = T.BG_ELEV_2,
                 show_scale: bool = False):
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
        self._bg = bg
        self._show_scale = show_scale
        self._vals = (0.5, 0.5, 0.5, None)
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, prob: float, low: float | None = None, high: float | None = None, book: float | None = None):
        low = prob if low is None else low
        high = prob if high is None else high
        self._vals = (prob, low, high, book)
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 40)
        h = max(self.winfo_height(), 8)
        prob, low, high, book = self._vals
        pad = 6
        track_y0, track_y1 = h / 2 - 3, h / 2 + 3
        x = lambda p: pad + (w - 2 * pad) * max(0.0, min(1.0, p))
        self.create_rectangle(pad, track_y0, w - pad, track_y1, fill=T.BG_ELEV_4, outline="")
        # 50% tick
        self.create_line(x(0.5), track_y0 - 2, x(0.5), track_y1 + 2, fill=T.BORDER_STRONG)
        # interval band
        if high > low:
            self.create_rectangle(x(low), track_y0, x(high), track_y1, fill=T.MODEL_SOFT, outline="")
            self.create_rectangle(x(low), track_y0 + 1, x(high), track_y1 - 1, fill=T.mix(T.MODEL_SOFT, T.MODEL, 0.35), outline="")
        # book marker
        if book is not None:
            self.create_line(x(book), track_y0 - 4, x(book), track_y1 + 4, fill=T.TEXT_MUTED, width=2)
        # model marker
        color = T.POSITIVE if (book is not None and prob - book > 0.03) else (
            T.NEGATIVE if (book is not None and prob - book < -0.03) else T.MODEL)
        r = 5
        self.create_oval(x(prob) - r, h / 2 - r, x(prob) + r, h / 2 + r, fill=color, outline=self._bg, width=1)
        if self._show_scale:
            for p in (0.0, 0.5, 1.0):
                self.create_text(x(p), h - 2, text=f"{int(p*100)}", fill=T.TEXT_DIM, font=T.FONT_TINY, anchor="s")


class FactorBar(tk.Frame):
    """One research factor: name, a centered signed bar, and its value.

    Plain Tk on purpose — the Game Analysis screen builds a dozen of these per
    market row, and a canvas-backed frame per factor was pure overhead.
    """

    def __init__(self, master, name: str, value: float, *, max_abs: float = 0.08,
                 description: str = "", unit: str = "pp", bg: str = T.BG_ELEV_2,
                 name_width: int = 170, bar_width: int = 160, informational: bool = False):
        super().__init__(master, bg=bg, highlightthickness=0, bd=0)
        self._value, self._max = value, max(max_abs, 1e-6)
        self._informational = informational
        name_lbl = tk.Label(self, text=name, font=T.FONT_SMALL, bg=bg, anchor="w", bd=0,
                            highlightthickness=0,
                            fg=(T.TEXT_MUTED if informational else T.TEXT))
        name_lbl.configure(width=max(1, name_width // 7))
        name_lbl.pack(side="left")
        self.canvas = tk.Canvas(self, width=bar_width, height=14, bg=bg, highlightthickness=0,
                                bd=0, takefocus=0)
        self.canvas.pack(side="left", padx=T.SP_2)
        color = T.TEXT_MUTED if informational else (
            T.POSITIVE if value > 0 else T.NEGATIVE if value < 0 else T.TEXT_MUTED)
        text = "priced in" if informational else f"{value*100:+.1f} {unit}"
        val_lbl = tk.Label(self, text=text, font=T.FONT_MONO_SMALL, fg=color, bg=bg,
                           anchor="e", width=11, bd=0, highlightthickness=0)
        val_lbl.pack(side="left")
        if description:
            from . import fastwidgets as _fw
            _fw.tip(name_lbl, description)
            _fw.tip(val_lbl, description)
        self._render_bar()

    def _render_bar(self):
        c = self.canvas
        c.delete("all")
        w = int(c["width"])
        h = int(c["height"])
        mid = w / 2
        c.create_rectangle(0, h / 2 - 3, w, h / 2 + 3, fill=T.BG_ELEV_4, outline="")
        c.create_line(mid, 0, mid, h, fill=T.BORDER_STRONG)
        if self._informational:
            return
        frac = max(-1.0, min(1.0, self._value / self._max))
        color = T.POSITIVE if frac > 0 else T.NEGATIVE
        x1 = mid + frac * (w / 2 - 2)
        if abs(x1 - mid) < 1.5:
            return
        c.create_rectangle(min(mid, x1), h / 2 - 3, max(mid, x1), h / 2 + 3, fill=color, outline="")


class Sparkline(tk.Canvas):
    """Tiny line chart of a player's recent samples with the prop line drawn."""

    def __init__(self, master, samples: Sequence[float] = (), line: float | None = None, *,
                 width: int = 180, height: int = 44, bg: str = T.BG_ELEV_2):
        super().__init__(master, width=width, height=height, bg=bg, highlightthickness=0, bd=0)
        self._samples = list(samples)
        self._line = line
        self.bind("<Configure>", lambda _e: self._draw())

    def set(self, samples: Sequence[float], line: float | None = None):
        self._samples = list(samples)
        self._line = line
        self._draw()

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 40)
        h = max(self.winfo_height(), 20)
        xs = list(reversed(self._samples))     # oldest → newest
        if not xs:
            self.create_text(w / 2, h / 2, text="no samples", fill=T.TEXT_DIM, font=T.FONT_TINY)
            return
        vals = xs + ([self._line] if self._line is not None else [])
        lo, hi = min(vals), max(vals)
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        pad = 5
        n = len(xs)
        fx = lambda i: pad + (w - 2 * pad) * (i / max(1, n - 1))
        fy = lambda v: h - pad - (h - 2 * pad) * ((v - lo) / (hi - lo))
        if self._line is not None:
            y = fy(self._line)
            self.create_line(pad, y, w - pad, y, fill=T.TEXT_DIM, dash=(3, 3))
        pts = [(fx(i), fy(v)) for i, v in enumerate(xs)]
        if len(pts) > 1:
            self.create_line(*[c for p in pts for c in p], fill=T.MODEL, width=2, smooth=False)
        for i, (x, y) in enumerate(pts):
            v = xs[i]
            color = T.POSITIVE if (self._line is not None and v > self._line) else (
                T.NEGATIVE if self._line is not None else T.MODEL)
            self.create_oval(x - 2.5, y - 2.5, x + 2.5, y + 2.5, fill=color, outline="")


# ---------------------------------------------------------------- ttk table style

_TREE_STYLES_INSTALLED = False


def install_treeview_styles():
    """Dark-theme every ttk.Treeview the app uses. Idempotent."""
    global _TREE_STYLES_INSTALLED
    if _TREE_STYLES_INSTALLED:
        return
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    for name, bg, head_bg, rowh in (
        ("SpreadAI.Treeview", T.BG_ELEV_1, T.BG_ELEV_2, 26),
        ("Props.Treeview",    T.BG_ELEV_1, T.BG_ELEV_2, 26),
        ("Analyzer.Treeview", T.BG_ELEV_1, T.BG_ELEV_2, 26),
        ("Markets.Treeview",  T.BG_ELEV_2, T.BG_ELEV_3, 24),
    ):
        style.configure(name, background=bg, fieldbackground=bg, foreground=T.TEXT,
                        rowheight=rowh, borderwidth=0, font=T.FONT_SMALL)
        style.configure(f"{name}.Heading", background=head_bg, foreground=T.TEXT_MUTED,
                        relief="flat", font=T.FONT_LABEL, padding=(6, 4))
        style.map(name, background=[("selected", T.ACCENT_SOFT)], foreground=[("selected", T.TEXT)])
        style.map(f"{name}.Heading", background=[("active", T.BG_ELEV_4)])
    style.configure("Vertical.TScrollbar", background=T.BG_ELEV_3, troughcolor=T.BG_ELEV_1,
                    bordercolor=T.BG_ELEV_1, arrowcolor=T.TEXT_MUTED, relief="flat")
    _TREE_STYLES_INSTALLED = True


def make_tree(master, columns: dict[str, tuple[str, int, str]], *, style: str = "SpreadAI.Treeview",
              stretch_col: str | None = None, height: int | None = None,
              selectmode: str = "browse") -> tuple[ttk.Treeview, ctk.CTkFrame]:
    """Build a themed Treeview inside a rounded wrapper with a scrollbar.

    columns: {id: (heading, width, anchor)}. Returns (tree, wrapper).
    """
    install_treeview_styles()
    wrap = ctk.CTkFrame(master, fg_color=T.BG_ELEV_1, corner_radius=T.R_MD)
    kwargs = {"height": height} if height else {}
    tree = ttk.Treeview(wrap, columns=list(columns), show="headings", style=style, selectmode=selectmode, **kwargs)
    for cid, (label, width, anchor) in columns.items():
        tree.heading(cid, text=label)
        tree.column(cid, width=width, anchor=anchor, stretch=(cid == stretch_col))
    tree.tag_configure("hot", foreground=T.POSITIVE, font=T.FONT_BOLD)
    tree.tag_configure("warm", foreground=T.TEXT)
    tree.tag_configure("cold", foreground=T.NEGATIVE)
    tree.tag_configure("dim", foreground=T.TEXT_MUTED)
    tree.tag_configure("model", foreground=T.MODEL)
    vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
    vsb.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 4))
    wrap.grid_rowconfigure(0, weight=1)
    wrap.grid_columnconfigure(0, weight=1)
    return tree, wrap
