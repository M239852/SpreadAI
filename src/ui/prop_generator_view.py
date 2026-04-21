"""Prop Generator view — auto-build a PrizePicks player-prop slip.

User picks:
  * Mode (Safest / Balanced / Upside)
  * Number of legs (2–6)
  * Stake (for payout/profit preview)
  * Optional stat and team filters
  * Whether to use live ESPN history ("model from player history") or
    sport-default volatility (fast, offline-friendly)

On Generate, props for the current sport are fetched, analyzed in a worker
thread, scored by mode, and the top N are assembled into a slip. The view
renders combined hit probability, power-play multiplier, projected payout
and profit, and a per-leg reasoning breakdown. One click pushes the slip
to the shared bet slip where the power-play math shows up automatically.
"""
from __future__ import annotations
import threading
from typing import Callable
import customtkinter as ctk

from ..analysis.prop_generator import (
    generate_prop_slip, GeneratedPropSlip, MODES as PROP_MODES,
)
from ..analysis.probability import LegAnalysis
from ..api.prizepicks_api import PrizePicksAPI, PlayerProp, PP_LEAGUE_IDS, POWER_PAYOUTS
from ..api.demo_props import demo_props
from ..utils.formatters import format_pct, format_money
from . import theme as T
from .widgets import Card, Pill, StatBlock, make_scroll
from .state import AppState


class PropGeneratorView(ctk.CTkFrame):
    """PrizePicks-style prop-slip generator."""

    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg

        self.mode = "balanced"
        self.leg_count = 3
        self.stake = 10.0
        self.stat_filter = ""
        self.team_filter = ""
        self.use_network = True
        self._current_slip: GeneratedPropSlip | None = None
        self._props_cache: list[PlayerProp] = []
        self._props_sport_key: str | None = None

        state.subscribe(self._on_state_event)

        self.scroll = make_scroll(self)
        self.scroll.pack(fill="both", expand=True, padx=16, pady=16)

        self._build_header()
        self._build_controls()

        self.results_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.results_frame.pack(fill="x", pady=(10, 0))

        self.placeholder = ctk.CTkLabel(
            self.results_frame,
            text="Pick a mode and leg count, then Generate — SpreadAI will pull PrizePicks\n"
                 "projections, model each player's history, and build a scored slip.",
            font=T.FONT, text_color=T.TEXT_MUTED, justify="left",
        )
        self.placeholder.pack(pady=60)

    # ---------------- Layout ----------------

    def _build_header(self):
        head = ctk.CTkFrame(self.scroll, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=(4, 10))
        ctk.CTkLabel(head, text="Prop Generator", font=T.FONT_TITLE, text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text="Auto-build a PrizePicks player-prop slip — pick a style and leg count, we handle the math.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

    def _build_controls(self):
        card = Card(self.scroll)
        card.pack(fill="x", pady=6)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        # MODE
        ctk.CTkLabel(inner, text="MODE", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        mode_row = ctk.CTkFrame(inner, fg_color="transparent")
        mode_row.pack(fill="x", pady=(6, 0))
        self.mode_buttons: dict[str, ctk.CTkButton] = {}
        for key, cfg in PROP_MODES.items():
            b = ctk.CTkButton(
                mode_row,
                text=f"  {cfg['label']}\n  {cfg['subtitle']}",
                anchor="w", height=60, width=240,
                fg_color=T.BG_ELEV_2, hover_color=T.BG_ELEV_3,
                text_color=T.TEXT, font=T.FONT_BOLD,
                corner_radius=10,
                command=lambda k=key: self._pick_mode(k),
            )
            b.pack(side="left", padx=(0, 10))
            self.mode_buttons[key] = b
        self._highlight_mode()

        # LEGS
        ctk.CTkLabel(inner, text="LEGS", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(14, 4))
        legs_row = ctk.CTkFrame(inner, fg_color="transparent")
        legs_row.pack(fill="x")

        # PrizePicks pays out 2x3x5x10x20x25 for 2..6 legs; button-per-tier is
        # much clearer than a slider because the payout changes by tier.
        self.leg_buttons: dict[int, ctk.CTkButton] = {}
        for n in sorted(POWER_PAYOUTS.keys()):
            mult = POWER_PAYOUTS[n]
            b = ctk.CTkButton(
                legs_row,
                text=f"{n} legs\n{mult:.0f}x",
                width=80, height=54,
                fg_color=T.BG_ELEV_2, hover_color=T.BG_ELEV_3,
                text_color=T.TEXT, font=T.FONT_BOLD,
                corner_radius=8,
                command=lambda k=n: self._pick_legs(k),
            )
            b.pack(side="left", padx=(0, 6))
            self.leg_buttons[n] = b
        self._highlight_legs()

        # STAKE
        ctk.CTkLabel(inner, text="STAKE ($)", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(14, 4))
        stake_row = ctk.CTkFrame(inner, fg_color="transparent")
        stake_row.pack(fill="x")
        self.stake_var = ctk.StringVar(value=str(int(self.stake)))
        stake_entry = ctk.CTkEntry(
            stake_row, textvariable=self.stake_var, width=100, height=32,
            fg_color=T.BG_ELEV_2, border_width=0, text_color=T.TEXT,
            font=T.FONT_BOLD,
        )
        stake_entry.pack(side="left")
        self.stake_var.trace_add("write", lambda *_: self._on_stake_change())
        for amt in (5, 10, 25, 50, 100):
            ctk.CTkButton(
                stake_row, text=f"${amt}", width=48, height=26,
                fg_color=T.BG_ELEV_3, hover_color=T.ACCENT, text_color=T.TEXT,
                font=T.FONT_TINY, corner_radius=6,
                command=lambda a=amt: self._set_stake(a),
            ).pack(side="left", padx=(6, 0))

        # FILTERS (stat / team)
        ctk.CTkLabel(inner, text="FILTERS", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(14, 4))
        fr = ctk.CTkFrame(inner, fg_color="transparent")
        fr.pack(fill="x")
        ctk.CTkLabel(fr, text="Stat", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
        self.stat_var = ctk.StringVar()
        ctk.CTkEntry(
            fr, textvariable=self.stat_var, width=160, height=28,
            fg_color=T.BG_ELEV_2, border_width=0, text_color=T.TEXT,
            placeholder_text="Any stat",
        ).pack(side="left", padx=(6, 12))
        self.stat_var.trace_add("write", lambda *_: self._on_stat_change())

        ctk.CTkLabel(fr, text="Team", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
        self.team_var = ctk.StringVar()
        ctk.CTkEntry(
            fr, textvariable=self.team_var, width=120, height=28,
            fg_color=T.BG_ELEV_2, border_width=0, text_color=T.TEXT,
            placeholder_text="Any team",
        ).pack(side="left", padx=(6, 12))
        self.team_var.trace_add("write", lambda *_: self._on_team_change())

        self.net_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(
            fr, text="Model from player history (slower)",
            variable=self.net_var, font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            fg_color=T.ACCENT, border_color=T.BORDER, hover_color=T.ACCENT_HOVER,
            command=self._on_network_toggle,
        ).pack(side="right")

        # Generate button + progress
        btn_row = ctk.CTkFrame(inner, fg_color="transparent")
        btn_row.pack(fill="x", pady=(14, 0))
        self.generate_btn = ctk.CTkButton(
            btn_row, text="Generate prop slip", height=40, width=220,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.BG,
            font=T.FONT_BOLD, corner_radius=10,
            command=self._generate,
        )
        self.generate_btn.pack(side="left")
        self.progress_lbl = ctk.CTkLabel(btn_row, text="", font=T.FONT_TINY, text_color=T.TEXT_MUTED)
        self.progress_lbl.pack(side="left", padx=14)

    # ---------------- Control handlers ----------------

    def _pick_mode(self, key: str):
        self.mode = key
        self._highlight_mode()

    def _highlight_mode(self):
        for k, b in self.mode_buttons.items():
            if k == self.mode:
                b.configure(fg_color=T.ACCENT, text_color=T.BG)
            else:
                b.configure(fg_color=T.BG_ELEV_2, text_color=T.TEXT)

    def _pick_legs(self, n: int):
        self.leg_count = n
        self._highlight_legs()

    def _highlight_legs(self):
        for n, b in self.leg_buttons.items():
            if n == self.leg_count:
                b.configure(fg_color=T.ACCENT, text_color=T.BG)
            else:
                b.configure(fg_color=T.BG_ELEV_2, text_color=T.TEXT)

    def _on_stake_change(self):
        try:
            v = float(self.stake_var.get() or 0)
        except ValueError:
            return
        self.stake = max(0.0, v)

    def _set_stake(self, amt: float):
        self.stake_var.set(str(int(amt)))
        self.stake = float(amt)

    def _on_stat_change(self):
        self.stat_filter = (self.stat_var.get() or "").strip()

    def _on_team_change(self):
        self.team_filter = (self.team_var.get() or "").strip()

    def _on_network_toggle(self):
        self.use_network = bool(self.net_var.get())

    def _on_state_event(self, event: str):
        if event == "sport":
            # Invalidate the cached prop pool — the next Generate will refetch.
            self._props_cache = []
            self._props_sport_key = None

    # ---------------- Generation ----------------

    def _generate(self):
        sport_key = self.state.sport_key
        if sport_key not in PP_LEAGUE_IDS:
            self.progress_lbl.configure(
                text=f"PrizePicks doesn't cover {sport_key} in SpreadAI.",
                text_color=T.WARNING,
            )
            return

        self.generate_btn.configure(state="disabled", text="Working…")
        self.progress_lbl.configure(text="Starting…", text_color=T.TEXT_MUTED)

        for w in self.results_frame.winfo_children():
            w.destroy()
        loading = ctk.CTkLabel(
            self.results_frame, text="Fetching projections & analyzing…",
            font=T.FONT, text_color=T.TEXT_MUTED,
        )
        loading.pack(pady=40)

        mode = self.mode
        legs = self.leg_count
        stake = self.stake
        stat_f = self.stat_filter or None
        team_f = self.team_filter or None
        skip_network = not self.use_network

        def progress_cb(done: int, total: int, note: str):
            self.after(0, lambda: self.progress_lbl.configure(
                text=f"{done}/{total} — {note}", text_color=T.TEXT_MUTED,
            ))

        def work():
            err = ""
            slip: GeneratedPropSlip | None = None
            try:
                # Use cached props when the sport hasn't changed — avoids
                # hammering the PrizePicks API when the user is iterating on
                # mode/leg count.
                props: list[PlayerProp] = []
                if self._props_cache and self._props_sport_key == sport_key:
                    props = self._props_cache
                else:
                    try:
                        client = PrizePicksAPI()
                        props = client.fetch_props(sport_key)
                    except Exception:
                        props = []
                    if not props:
                        props = demo_props(sport_key)
                    self._props_cache = props
                    self._props_sport_key = sport_key

                slip = generate_prop_slip(
                    props=props,
                    mode=mode,
                    max_legs=legs,
                    min_legs=legs,       # force exactly the chosen tier
                    stake=stake,
                    progress_cb=progress_cb,
                    skip_network=skip_network,
                    stat_filter=stat_f,
                    team_filter=team_f,
                )
            except Exception as e:
                err = f"Generation failed: {e}"

            self.after(0, lambda: self._on_generated(slip, err, loading))

        threading.Thread(target=work, daemon=True).start()

    def _on_generated(self, slip: GeneratedPropSlip | None, err: str, loading_widget):
        loading_widget.destroy()
        self.generate_btn.configure(state="normal", text="Generate prop slip")
        self._current_slip = slip

        if err:
            self.progress_lbl.configure(text=err, text_color=T.NEGATIVE)
            return
        if slip is None:
            self.progress_lbl.configure(
                text="No qualifying props found. Loosen filters or try a different mode.",
                text_color=T.WARNING,
            )
            return
        self.progress_lbl.configure(
            text=f"Built a {len(slip.legs)}-leg {slip.mode_label} prop slip.",
            text_color=T.POSITIVE,
        )
        self._render_slip(slip)

    # ---------------- Render ----------------

    def _render_slip(self, slip: GeneratedPropSlip):
        # --- Summary card ---
        head = Card(self.results_frame)
        head.pack(fill="x", pady=8)
        inner = ctk.CTkFrame(head, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        ctk.CTkLabel(
            top, text=f"{slip.mode_label}  ·  {len(slip.legs)}-leg prop slip",
            font=T.FONT_TITLE, text_color=T.TEXT,
        ).pack(side="left")
        if slip.power_multiplier > 0:
            Pill(
                top, f"{slip.power_multiplier:.0f}x power play",
                color=T.BG_ELEV_3, text_color=T.ACCENT,
            ).pack(side="right")
        Pill(top, slip.mode_subtitle, color=T.BG_ELEV_3, text_color=T.TEXT_MUTED).pack(side="right", padx=6)

        stats = ctk.CTkFrame(inner, fg_color="transparent")
        stats.pack(fill="x", pady=(12, 0))

        combined_pct = slip.parlay.combined_prob * 100
        be_pct = slip.break_even_prob * 100
        gap = slip.parlay.combined_prob - slip.break_even_prob
        prob_color = T.POSITIVE if gap >= 0.03 else (T.WARNING if gap >= 0 else T.NEGATIVE)
        ev_color = T.POSITIVE if slip.ev_dollars > 0 else T.NEGATIVE

        StatBlock(stats, "Hit probability", f"{combined_pct:.1f}%", value_color=prob_color).pack(side="left", padx=(0, 24))
        StatBlock(stats, "Break-even", f"{be_pct:.1f}%" if slip.power_multiplier > 0 else "—").pack(side="left", padx=(0, 24))
        StatBlock(
            stats, "Edge vs BE",
            f"{gap*100:+.1f}%" if slip.power_multiplier > 0 else "—",
            value_color=prob_color,
        ).pack(side="left", padx=(0, 24))
        StatBlock(
            stats, "Projected payout",
            format_money(slip.projected_payout) if slip.power_multiplier > 0 else "—",
        ).pack(side="left", padx=(0, 24))
        StatBlock(
            stats, "Projected profit",
            format_money(slip.projected_profit) if slip.power_multiplier > 0 else "—",
            value_color=(T.POSITIVE if slip.projected_profit > 0 else T.TEXT),
        ).pack(side="left", padx=(0, 24))
        StatBlock(
            stats, "Expected value",
            format_money(slip.ev_dollars) if slip.power_multiplier > 0 else "—",
            value_color=ev_color,
        ).pack(side="left")

        # --- Reasoning card ---
        reasoning_card = Card(self.results_frame, fg_color=T.BG_ELEV_2, corner_radius=10, border_width=0)
        reasoning_card.pack(fill="x", pady=6)
        rinner = ctk.CTkFrame(reasoning_card, fg_color="transparent")
        rinner.pack(fill="x", padx=20, pady=14)
        ctk.CTkLabel(rinner, text="WHY THIS SLIP", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(
            rinner, text=slip.overall_reasoning,
            font=T.FONT_SMALL, text_color=T.TEXT, justify="left", wraplength=860,
        ).pack(anchor="w", pady=(6, 0))

        # --- Action row ---
        action = ctk.CTkFrame(self.results_frame, fg_color="transparent")
        action.pack(fill="x", pady=(4, 8))
        ctk.CTkButton(
            action, text="Apply slip to bet slip", height=36, width=220,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.BG,
            font=T.FONT_BOLD, corner_radius=8,
            command=lambda: self._apply_to_slip(slip),
        ).pack(side="left")

        # --- Per-leg breakdown ---
        for i, (leg, pick) in enumerate(zip(slip.legs, slip.picks)):
            self._render_leg(leg, pick, i + 1)

    def _render_leg(self, leg: LegAnalysis, pick, idx: int):
        card = Card(self.results_frame)
        card.pack(fill="x", pady=6)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=14)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)

        side_color = T.POSITIVE if pick.side == "Over" else T.NEGATIVE
        ctk.CTkLabel(
            left, text=f"Leg {idx}  ·  {pick.prop.player_name}",
            font=T.FONT_HEAD, text_color=T.TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            left,
            text=f"{pick.side} {pick.prop.line} {pick.prop.stat_type}   ·   "
                 f"{pick.prop.team or '?'} vs {pick.prop.opponent or '?'}",
            font=T.FONT_SMALL, text_color=side_color,
        ).pack(anchor="w", pady=(2, 0))

        stats = ctk.CTkFrame(top, fg_color="transparent")
        stats.pack(side="right")
        model_pct = pick.prob * 100
        prob_color = T.POSITIVE if pick.prob >= 0.60 else (
            T.WARNING if pick.prob >= 0.50 else T.NEGATIVE
        )
        StatBlock(stats, "Model", f"{model_pct:.1f}%", value_color=prob_color).pack(side="left", padx=6)
        if pick.analysis.samples:
            StatBlock(stats, f"Last {len(pick.analysis.samples)} avg", f"{pick.analysis.season_avg:.1f}").pack(side="left", padx=6)
        else:
            StatBlock(stats, "Samples", "none").pack(side="left", padx=6)

        if pick.reasoning:
            ctk.CTkLabel(
                inner, text=pick.reasoning,
                font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
                justify="left", wraplength=860,
            ).pack(anchor="w", pady=(10, 0), fill="x")

    def _apply_to_slip(self, slip: GeneratedPropSlip):
        self.state.clear_slip()
        for leg in slip.legs:
            self.on_add_leg(leg)
