"""Settings: data source, bankroll & staking, model controls, about."""
from __future__ import annotations
import webbrowser
import customtkinter as ctk

from ..analysis import model as M
from ..utils.storage import save_config
from . import theme as T
from .widgets import Card, PageHeader, PrimaryButton, GhostButton, entry, switch, checkbox, make_scroll, section_label
from .state import AppState


class SettingsView(ctk.CTkFrame):
    def __init__(self, master, state: AppState, on_saved):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_saved = on_saved

        self.header = PageHeader(self, "Settings", "Data sources, bankroll, and how the model behaves.", show_source=False)
        self.header.pack(fill="x")
        self.saved_lbl = ctk.CTkLabel(self.header.actions, text="", font=T.FONT_SMALL, text_color=T.POSITIVE)
        self.saved_lbl.pack(side="right", padx=(0, T.SP_3))
        PrimaryButton(self.header.actions, "Save changes", command=self._save, width=130, height=30).pack(side="right", padx=(0, T.SP_2))

        self.scroll = make_scroll(self)
        self.scroll.pack(fill="both", expand=True, padx=T.SP_4, pady=(0, T.SP_4))
        self._build_api_section()
        self._build_bankroll_section()
        self._build_model_section()
        self._build_about_section()

    def _section(self, title: str, blurb: str) -> ctk.CTkFrame:
        card = Card(self.scroll)
        card.pack(fill="x", pady=(0, T.SP_3))
        inner = card.body(padx=T.SP_5, pady=T.SP_4)
        section_label(inner, title)
        ctk.CTkLabel(inner, text=blurb, font=T.FONT_SMALL, text_color=T.TEXT_MUTED, justify="left", anchor="w",
                     wraplength=760).pack(anchor="w", pady=(4, T.SP_3))
        return inner

    def _field(self, parent, label: str, var, width: int = 160, hint: str = "", show: str | None = None) -> ctk.CTkEntry:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, T.SP_2))
        ctk.CTkLabel(row, text=label, font=T.FONT_SMALL, text_color=T.TEXT, width=200, anchor="w").pack(side="left")
        e = entry(row, var, width=width)
        if show is not None:
            e.configure(show=show)
        e.pack(side="left", padx=(0, T.SP_2))
        if hint:
            ctk.CTkLabel(row, text=hint, font=T.FONT_TINY, text_color=T.TEXT_DIM, anchor="w").pack(side="left")
        return e

    # ---------------------------------------------------------------- sections

    def _build_api_section(self):
        inner = self._section("Odds data source",
                              "SpreadAI queries the-odds-api.com for live moneylines, spreads and totals across DraftKings, "
                              "FanDuel, BetMGM, Caesars, Pinnacle and more. A free key gives 500 requests/month.")
        self.key_var = ctk.StringVar(value=self.state.config.get("odds_api_key", ""))
        self.key_entry = self._field(inner, "API key", self.key_var, width=420, show="•")
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")
        self.show_var = ctk.BooleanVar(value=False)
        checkbox(row, "Show key", self.show_var, command=self._toggle_show).pack(side="left")
        GhostButton(row, "Get a free key ↗", command=lambda: webbrowser.open("https://the-odds-api.com/"),
                    width=140, height=28, text_color=T.ACCENT).pack(side="left", padx=T.SP_3)
        self.demo_var = ctk.BooleanVar(value=bool(self.state.config.get("use_demo_data_when_no_key", True)))
        switch(inner, "Fall back to demo data when no key is configured", self.demo_var).pack(anchor="w", pady=(T.SP_3, 0))

    def _build_bankroll_section(self):
        inner = self._section("Bankroll & staking",
                              "Drives the Kelly stake suggestions in the bet slip. Conservative sizing uses the low end of the "
                              "model's 80% interval instead of the point estimate, which is the right call when the model is uncertain.")
        self.bank_var = ctk.StringVar(value=str(self.state.config.get("bankroll", 1000)))
        self._field(inner, "Bankroll ($)", self.bank_var, width=140)
        self.kelly_var = ctk.StringVar(value=str(self.state.config.get("kelly_fraction", 0.25)))
        self._field(inner, "Kelly fraction", self.kelly_var, width=140, hint="0.25 = quarter-Kelly (recommended)")
        m = self.state.config.get("model") or {}
        self.kelly_cons_var = ctk.BooleanVar(value=bool(m.get("kelly_conservative", True)))
        switch(inner, "Size stakes from the interval's low end (conservative Kelly)", self.kelly_cons_var).pack(anchor="w", pady=(T.SP_2, 0))

    def _build_model_section(self):
        inner = self._section("Probability model",
                              f"Model v{M.MODEL_VERSION}: per-book de-vig → sharpness-weighted consensus → cross-market fusion → "
                              "line shift → research evidence in logit space with an efficiency discount → 80% interval. "
                              "These switches change how legs are priced everywhere in the app.")
        m = self.state.config.get("model") or {}
        self.cross_var = ctk.BooleanVar(value=bool(m.get("use_cross_market", True)))
        switch(inner, "Cross-market fusion (derive spreads from moneylines and vice-versa)", self.cross_var).pack(anchor="w", pady=(0, T.SP_2))
        self.corr_var = ctk.BooleanVar(value=bool(m.get("use_correlation", True)))
        switch(inner, "Correlated parlay math (Gaussian copula for same-game legs)", self.corr_var).pack(anchor="w", pady=(0, T.SP_3))
        self.temp_var = ctk.StringVar(value=str(m.get("calibration_temperature", 1.0)))
        self._field(inner, "Calibration temperature", self.temp_var, width=100,
                    hint="1.0 = off · >1 shrinks every probability toward 50% (use after a bad calibration audit)")
        self.shift_var = ctk.StringVar(value=str(m.get("calibration_shift", 0.0)))
        self._field(inner, "Calibration shift (logit)", self.shift_var, width=100, hint="0 = off · negative pushes all probabilities up")
        self.disc_var = ctk.StringVar(value="" if m.get("evidence_discount") in (None, "") else str(m.get("evidence_discount")))
        self._field(inner, "Evidence discount override", self.disc_var, width=100,
                    hint="blank = per-sport default (1 − market efficiency) · 0 ignores research entirely · 1 trusts it fully")

        sports = ctk.CTkFrame(inner, fg_color=T.BG_ELEV_2, corner_radius=T.R_MD)
        sports.pack(fill="x", pady=(T.SP_3, 0))
        grid = ctk.CTkFrame(sports, fg_color="transparent")
        grid.pack(fill="x", padx=T.SP_3, pady=T.SP_2)
        heads = ("Sport", "Margin σ", "Total σ", "Efficiency", "Fav–Over ρ", "Prop prior n", "Cross-market")
        for c, h in enumerate(heads):
            ctk.CTkLabel(grid, text=h.upper(), font=T.FONT_LABEL, text_color=T.TEXT_MUTED, anchor="w", width=110).grid(row=0, column=c, sticky="w", padx=4)
        for r, sp in enumerate(M.SPORTS.values(), start=1):
            vals = (sp.label, f"{sp.margin_sd:g}", f"{sp.total_sd:g}", f"{sp.efficiency*100:.0f}%", f"{sp.fav_over_corr:+.2f}",
                    f"{sp.prop_prior_n:g}", "yes" if sp.cross_market else "no")
            for c, v in enumerate(vals):
                ctk.CTkLabel(grid, text=v, font=(T.FONT_MONO_SMALL if c else T.FONT_SMALL), text_color=T.TEXT, anchor="w", width=110).grid(row=r, column=c, sticky="w", padx=4)

    def _build_about_section(self):
        inner = self._section("About",
                              "SpreadAI aggregates real-time sportsbook markets (The Odds API), DFS projections (PrizePicks, Underdog) "
                              "and public team, player and injury data (ESPN). Every probability is explainable: each leg shows its "
                              "consensus, cross-market check, line shift and research factors.\n\n"
                              "Bet responsibly. For entertainment and research only.")
        ctk.CTkLabel(inner, text=f"SpreadAI {T.APP_VERSION}  ·  model v{M.MODEL_VERSION}", font=T.FONT_TINY, text_color=T.TEXT_DIM).pack(anchor="w")

    # ---------------------------------------------------------------- actions

    def _toggle_show(self):
        self.key_entry.configure(show="" if self.show_var.get() else "•")

    def _save(self):
        cfg = self.state.config
        cfg["odds_api_key"] = self.key_var.get().strip()
        cfg["use_demo_data_when_no_key"] = bool(self.demo_var.get())
        try:
            cfg["bankroll"] = float(self.bank_var.get())
        except ValueError:
            pass
        try:
            cfg["kelly_fraction"] = max(0.0, min(1.0, float(self.kelly_var.get())))
        except ValueError:
            pass
        model = dict(cfg.get("model") or {})
        model["use_cross_market"] = bool(self.cross_var.get())
        model["use_correlation"] = bool(self.corr_var.get())
        model["kelly_conservative"] = bool(self.kelly_cons_var.get())
        try:
            model["calibration_temperature"] = max(0.25, float(self.temp_var.get()))
        except ValueError:
            pass
        try:
            model["calibration_shift"] = float(self.shift_var.get())
        except ValueError:
            pass
        disc = self.disc_var.get().strip()
        if disc == "":
            model["evidence_discount"] = None
        else:
            try:
                model["evidence_discount"] = max(0.0, min(1.0, float(disc)))
            except ValueError:
                pass
        cfg["model"] = model
        save_config(cfg)
        self.saved_lbl.configure(text="Saved ✓")
        self.after(2500, lambda: self.saved_lbl.configure(text=""))
        self.on_saved()
