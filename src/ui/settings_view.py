from __future__ import annotations
import webbrowser
import customtkinter as ctk

from ..utils.storage import save_config
from . import theme as T
from .widgets import Card, make_scroll, hsep
from .state import AppState


class SettingsView(ctk.CTkFrame):
    def __init__(self, master, state: AppState, on_saved):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_saved = on_saved

        self.scroll = make_scroll(self)
        self.scroll.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            self.scroll, text="Settings", font=T.FONT_TITLE, text_color=T.TEXT
        ).pack(anchor="w", padx=8, pady=(4, 14))

        self._build_api_section()
        self._build_bankroll_section()
        self._build_about_section()

    def _build_api_section(self):
        card = Card(self.scroll)
        card.pack(fill="x", pady=8)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        ctk.CTkLabel(inner, text="ODDS DATA SOURCE", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(
            inner,
            text="SpreadAI queries the-odds-api.com for live spreads & moneylines across DraftKings,\n"
                 "FanDuel, BetMGM, Caesars, PointsBet, BetRivers and more. A free key gives 500\n"
                 "requests/month.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, justify="left",
        ).pack(anchor="w", pady=(4, 10))

        key_row = ctk.CTkFrame(inner, fg_color="transparent")
        key_row.pack(fill="x")
        ctk.CTkLabel(key_row, text="API key", font=T.FONT_SMALL, text_color=T.TEXT, width=90).pack(side="left")
        self.key_var = ctk.StringVar(value=self.state.config.get("odds_api_key", ""))
        self.key_entry = ctk.CTkEntry(
            key_row, textvariable=self.key_var, width=420, height=34,
            fg_color=T.BG_ELEV_2, border_width=0, text_color=T.TEXT, show="•",
        )
        self.key_entry.pack(side="left", padx=8)
        self.show_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            key_row, text="Show", variable=self.show_var,
            command=self._toggle_show, font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            fg_color=T.ACCENT, border_color=T.BORDER, hover_color=T.ACCENT_HOVER,
        ).pack(side="left", padx=6)

        btns = ctk.CTkFrame(inner, fg_color="transparent")
        btns.pack(fill="x", pady=(14, 0))
        ctk.CTkButton(
            btns, text="Save", width=100, height=32,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.BG,
            font=T.FONT_BOLD, command=self._save,
        ).pack(side="left")
        ctk.CTkButton(
            btns, text="Get a free key ↗", width=140, height=32,
            fg_color=T.BG_ELEV_3, hover_color=T.BORDER, text_color=T.ACCENT,
            font=T.FONT_BOLD,
            command=lambda: webbrowser.open("https://the-odds-api.com/"),
        ).pack(side="left", padx=8)

        self.demo_var = ctk.BooleanVar(value=bool(self.state.config.get("use_demo_data_when_no_key", True)))
        ctk.CTkCheckBox(
            inner, text="Fall back to demo data when no key is configured",
            variable=self.demo_var, font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            fg_color=T.ACCENT, border_color=T.BORDER, hover_color=T.ACCENT_HOVER,
            command=self._save_checkbox,
        ).pack(anchor="w", pady=(12, 0))

    def _build_bankroll_section(self):
        card = Card(self.scroll)
        card.pack(fill="x", pady=8)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        ctk.CTkLabel(inner, text="BANKROLL", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(
            inner,
            text="Drives Kelly-criterion stake suggestions in the bet slip.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        ).pack(anchor="w", pady=(4, 10))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkLabel(row, text="Bankroll ($)", font=T.FONT_SMALL, text_color=T.TEXT, width=140).pack(side="left")
        self.bank_var = ctk.StringVar(value=str(self.state.config.get("bankroll", 1000)))
        ctk.CTkEntry(
            row, textvariable=self.bank_var, width=140, height=32,
            fg_color=T.BG_ELEV_2, border_width=0, text_color=T.TEXT,
        ).pack(side="left", padx=6)

        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(10, 0))
        ctk.CTkLabel(row2, text="Kelly fraction", font=T.FONT_SMALL, text_color=T.TEXT, width=140).pack(side="left")
        self.kelly_var = ctk.StringVar(value=str(self.state.config.get("kelly_fraction", 0.25)))
        ctk.CTkEntry(
            row2, textvariable=self.kelly_var, width=140, height=32,
            fg_color=T.BG_ELEV_2, border_width=0, text_color=T.TEXT,
        ).pack(side="left", padx=6)
        ctk.CTkLabel(
            row2, text="(e.g. 0.25 = quarter-Kelly for safety)",
            font=T.FONT_TINY, text_color=T.TEXT_MUTED,
        ).pack(side="left", padx=6)

        ctk.CTkButton(
            inner, text="Save", width=100, height=32,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.BG,
            font=T.FONT_BOLD, command=self._save,
        ).pack(anchor="w", pady=(14, 0))

    def _build_about_section(self):
        card = Card(self.scroll)
        card.pack(fill="x", pady=8)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        ctk.CTkLabel(inner, text="ABOUT", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(
            inner,
            text=(
                "SpreadAI aggregates real-time sportsbook markets (The Odds API) and public team &\n"
                "injury data (ESPN). It de-vigs book prices to produce a fair baseline probability,\n"
                "then layers in adjustments for injuries, recent form, and home-field. All math is\n"
                "transparent — each leg shows its factors and resulting edge.\n\n"
                "Bet responsibly. For entertainment and research only."
            ),
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED, justify="left",
        ).pack(anchor="w", pady=(4, 0))

    # ----------------

    def _toggle_show(self):
        self.key_entry.configure(show="" if self.show_var.get() else "•")

    def _save_checkbox(self):
        self.state.config["use_demo_data_when_no_key"] = bool(self.demo_var.get())
        save_config(self.state.config)

    def _save(self):
        self.state.config["odds_api_key"] = self.key_var.get().strip()
        try:
            self.state.config["bankroll"] = float(self.bank_var.get())
        except ValueError:
            pass
        try:
            k = float(self.kelly_var.get())
            self.state.config["kelly_fraction"] = max(0.0, min(1.0, k))
        except ValueError:
            pass
        save_config(self.state.config)
        self.on_saved()
