"""Small reusable widget helpers built on customtkinter."""
from __future__ import annotations
import customtkinter as ctk
from . import theme as T


class Card(ctk.CTkFrame):
    """Elevated card container with consistent padding."""

    def __init__(self, master, *, padding: int = 16, **kwargs):
        super().__init__(
            master,
            fg_color=kwargs.pop("fg_color", T.BG_ELEV_1),
            corner_radius=kwargs.pop("corner_radius", 12),
            border_width=kwargs.pop("border_width", 1),
            border_color=kwargs.pop("border_color", T.BORDER),
            **kwargs,
        )
        self._pad = padding


class Pill(ctk.CTkLabel):
    """Small rounded badge label."""

    def __init__(self, master, text: str, *, color: str = T.BG_ELEV_3, text_color: str = T.TEXT, **kwargs):
        super().__init__(
            master,
            text=f"  {text}  ",
            fg_color=color,
            text_color=text_color,
            corner_radius=999,
            font=T.FONT_SMALL,
            **kwargs,
        )


class StatBlock(ctk.CTkFrame):
    """A label + value block used in the summary bars."""

    def __init__(self, master, label: str, value: str, *, value_color: str = T.TEXT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.label = ctk.CTkLabel(self, text=label.upper(), font=T.FONT_TINY, text_color=T.TEXT_MUTED)
        self.value = ctk.CTkLabel(self, text=value, font=T.FONT_HEAD, text_color=value_color)
        self.label.pack(anchor="w")
        self.value.pack(anchor="w")

    def set(self, value: str, color: str | None = None):
        self.value.configure(text=value)
        if color is not None:
            self.value.configure(text_color=color)


def make_scroll(master, **kwargs) -> ctk.CTkScrollableFrame:
    return ctk.CTkScrollableFrame(
        master,
        fg_color=kwargs.pop("fg_color", T.BG),
        scrollbar_button_color=T.BORDER,
        scrollbar_button_hover_color=T.ACCENT,
        **kwargs,
    )


def hsep(master, pad_y: int = 8):
    f = ctk.CTkFrame(master, height=1, fg_color=T.BORDER)
    f.pack(fill="x", pady=pad_y)
    return f
