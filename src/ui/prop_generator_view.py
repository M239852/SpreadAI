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
    generate_prop_slips, GeneratedPropSlip, MODES as PROP_MODES,
)
from ..analysis.probability import LegAnalysis
from ..api.prizepicks_api import PlayerProp, PP_LEAGUE_IDS, POWER_PAYOUTS
from ..api.underdog_api import (
    UD_SPORT_TAGS, BOOK_CHOICES, fetch_props_from_books, book_from_ui_choice,
)
from ..api.demo_props import demo_props
from ..utils.formatters import format_pct, format_money
from . import theme as T
from .widgets import Card, Pill, StatBlock, make_scroll
from .state import AppState


# Per-book accent used for the source pill on a leg card. Keeps books
# visually distinct without leaning on logos (which we don't ship).
_BOOK_COLORS = {
    "prizepicks": T.ACCENT,
    "underdog":   "#E8553C",
    "demo":       T.TEXT_MUTED,
}

# Multi-slip cap — see generator_view for the rationale (5 keeps render time
# bounded and prop pools rarely support more than 4 deduplicated slips).
SLIP_COUNT_CHOICES = (1, 2, 3, 4, 5)


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
        self.book_choice = BOOK_CHOICES[0]   # "All books"
        self.use_network = True
        self.slip_count = 1
        self.dedupe_legs = True
        self._current_slips: list[GeneratedPropSlip] = []
        # Cache keyed by (sport_key, books-tuple) so switching the book
        # filter doesn't reuse a pool that was fetched with only one book.
        self._props_cache: list[PlayerProp] = []
        self._props_cache_key: tuple | None = None

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

        # SLIPS — generate N independent slips in one click. Dedup toggle
        # excludes each slip's prop ids from the next slip's pool so the
        # user can stake several non-overlapping slates on the same slate.
        ctk.CTkLabel(inner, text="SLIPS", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(14, 4))
        slips_row = ctk.CTkFrame(inner, fg_color="transparent")
        slips_row.pack(fill="x")
        self.slip_buttons: dict[int, ctk.CTkButton] = {}
        for n in SLIP_COUNT_CHOICES:
            b = ctk.CTkButton(
                slips_row, text=str(n), width=44, height=32,
                fg_color=T.BG_ELEV_2, hover_color=T.BG_ELEV_3, text_color=T.TEXT,
                font=T.FONT_BOLD, corner_radius=8,
                command=lambda k=n: self._pick_slip_count(k),
            )
            b.pack(side="left", padx=(0, 6))
            self.slip_buttons[n] = b
        self._highlight_slip_count()
        self.dedupe_var = ctk.BooleanVar(value=self.dedupe_legs)
        ctk.CTkSwitch(
            slips_row, text="Unique legs across slips",
            variable=self.dedupe_var, font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
            progress_color=T.ACCENT, button_color=T.TEXT, button_hover_color=T.TEXT,
            command=self._on_dedupe_toggle,
        ).pack(side="left", padx=(16, 0))

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

        # FILTERS (book / stat / team)
        ctk.CTkLabel(inner, text="FILTERS", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(14, 4))
        fr = ctk.CTkFrame(inner, fg_color="transparent")
        fr.pack(fill="x")

        # BOOK dropdown — restricts the pool to a specific DFS book so every
        # leg we emit is actually placeable there. Default is "All books",
        # which fetches from every supported book and merges.
        ctk.CTkLabel(fr, text="Book", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
        self.book_var = ctk.StringVar(value=self.book_choice)
        ctk.CTkOptionMenu(
            fr, values=list(BOOK_CHOICES), variable=self.book_var,
            width=150, height=28,
            fg_color=T.BG_ELEV_2, button_color=T.BG_ELEV_3,
            button_hover_color=T.ACCENT, dropdown_fg_color=T.BG_ELEV_1,
            dropdown_hover_color=T.BG_ELEV_3, text_color=T.TEXT,
            dropdown_text_color=T.TEXT, font=T.FONT_SMALL,
            command=self._on_book_change,
        ).pack(side="left", padx=(6, 14))

        ctk.CTkLabel(fr, text="Stat", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
        self.stat_var = ctk.StringVar()
        ctk.CTkEntry(
            fr, textvariable=self.stat_var, width=140, height=28,
            fg_color=T.BG_ELEV_2, border_width=0, text_color=T.TEXT,
            placeholder_text="Any stat",
        ).pack(side="left", padx=(6, 12))
        self.stat_var.trace_add("write", lambda *_: self._on_stat_change())

        ctk.CTkLabel(fr, text="Team", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(side="left")
        self.team_var = ctk.StringVar()
        ctk.CTkEntry(
            fr, textvariable=self.team_var, width=110, height=28,
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

    def _pick_slip_count(self, n: int):
        self.slip_count = n
        self._highlight_slip_count()

    def _highlight_slip_count(self):
        for n, b in self.slip_buttons.items():
            if n == self.slip_count:
                b.configure(fg_color=T.ACCENT, text_color=T.BG)
            else:
                b.configure(fg_color=T.BG_ELEV_2, text_color=T.TEXT)

    def _on_dedupe_toggle(self):
        self.dedupe_legs = bool(self.dedupe_var.get())

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

    def _on_book_change(self, choice: str):
        self.book_choice = choice
        # Changing book changes which books we fetch from, so force a refetch
        # on the next Generate.
        self._props_cache = []
        self._props_cache_key = None

    def _on_network_toggle(self):
        self.use_network = bool(self.net_var.get())

    def _on_state_event(self, event: str):
        if event == "sport":
            # Invalidate the cached prop pool — the next Generate will refetch.
            self._props_cache = []
            self._props_cache_key = None

    # ---------------- Generation ----------------

    def _generate(self):
        sport_key = self.state.sport_key
        # Sport-coverage check: both books share the same set of supported
        # sports. If neither book covers this sport we can't verify, so bail
        # early rather than silently falling through to demo data.
        if sport_key not in PP_LEAGUE_IDS and sport_key not in UD_SPORT_TAGS:
            self.progress_lbl.configure(
                text=f"PrizePicks/Underdog don't cover {sport_key} in SpreadAI.",
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
        book_choice = self.book_choice
        book_list = book_from_ui_choice(book_choice)          # None | ["PrizePicks"] | ["Underdog"]
        book_filter_for_generator = None if not book_list else book_list[0]
        skip_network = not self.use_network
        slip_count = self.slip_count
        dedupe = self.dedupe_legs

        # Cache key encodes sport + selected books — swapping books forces a
        # refetch so every returned prop actually belongs to an allowed book.
        cache_key = (sport_key, tuple(book_list) if book_list else ("all",))

        def progress_cb(done: int, total: int, note: str):
            self.after(0, lambda: self.progress_lbl.configure(
                text=f"{done}/{total} — {note}", text_color=T.TEXT_MUTED,
            ))

        def work():
            err = ""
            slips: list[GeneratedPropSlip] = []
            try:
                # Reuse the cached pool when the sport+book selection matches.
                props: list[PlayerProp] = []
                if self._props_cache and self._props_cache_key == cache_key:
                    props = self._props_cache
                else:
                    self.after(0, lambda: self.progress_lbl.configure(
                        text=f"Fetching {book_choice}…", text_color=T.TEXT_MUTED,
                    ))
                    try:
                        props = fetch_props_from_books(sport_key, book_list)
                    except Exception:
                        props = []
                    if not props:
                        # Offline / rate-limited — fall back to our curated
                        # demo slate. Demo props carry source="Demo" so the
                        # per-leg badge tells the user they're offline samples.
                        props = demo_props(sport_key)
                    self._props_cache = props
                    self._props_cache_key = cache_key

                slips = generate_prop_slips(
                    props=props,
                    mode=mode,
                    max_legs=legs,
                    min_legs=legs,       # force exactly the chosen tier
                    stake=stake,
                    count=slip_count,
                    dedupe_legs=dedupe,
                    progress_cb=progress_cb,
                    skip_network=skip_network,
                    stat_filter=stat_f,
                    team_filter=team_f,
                    book_filter=book_filter_for_generator,
                )
            except Exception as e:
                err = f"Generation failed: {e}"

            self.after(0, lambda: self._on_generated(slips, err, loading))

        threading.Thread(target=work, daemon=True).start()

    def _on_generated(self, slips: list[GeneratedPropSlip], err: str, loading_widget):
        loading_widget.destroy()
        self.generate_btn.configure(state="normal", text="Generate prop slip")
        self._current_slips = slips

        if err:
            self.progress_lbl.configure(text=err, text_color=T.NEGATIVE)
            return
        if not slips:
            self.progress_lbl.configure(
                text="No qualifying props found. Loosen filters or try a different mode.",
                text_color=T.WARNING,
            )
            return
        # Tell the user when the prop pool ran out before we hit the requested
        # slip count — usually means dedup is on and the slate is thin.
        if len(slips) < self.slip_count:
            short_note = f" (asked for {self.slip_count}, pool only supported {len(slips)})"
            color = T.WARNING
        else:
            short_note = ""
            color = T.POSITIVE
        if len(slips) == 1:
            text = f"Built a {len(slips[0].legs)}-leg {slips[0].mode_label} prop slip{short_note}."
        else:
            total_legs = sum(len(s.legs) for s in slips)
            text = (
                f"Built {len(slips)} {slips[0].mode_label} prop slips · "
                f"{total_legs} legs total{short_note}."
            )
        self.progress_lbl.configure(text=text, text_color=color)
        for idx, slip in enumerate(slips, start=1):
            self._render_slip(slip, slip_index=idx, total_slips=len(slips))

    # ---------------- Render ----------------

    def _render_slip(self, slip: GeneratedPropSlip, slip_index: int = 1, total_slips: int = 1):
        # Slip-N divider when multi
        if total_slips > 1:
            divider = ctk.CTkFrame(
                self.results_frame, fg_color=T.BORDER, height=1,
            )
            divider.pack(fill="x", pady=(18 if slip_index > 1 else 6, 4))
            ctk.CTkLabel(
                self.results_frame, text=f"SLIP {slip_index} OF {total_slips}",
                font=T.FONT_TINY, text_color=T.TEXT_MUTED, anchor="w",
            ).pack(anchor="w", padx=8, pady=(0, 2))

        # --- Summary card ---
        head = Card(self.results_frame)
        head.pack(fill="x", pady=8)
        inner = ctk.CTkFrame(head, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        title = (
            f"{slip.mode_label}  ·  {len(slip.legs)}-leg prop slip"
            if total_slips == 1
            else f"Slip {slip_index}  ·  {slip.mode_label}  ·  {len(slip.legs)}-leg prop slip"
        )
        ctk.CTkLabel(
            top, text=title,
            font=T.FONT_TITLE, text_color=T.TEXT,
        ).pack(side="left")
        if slip.power_multiplier > 0:
            Pill(
                top, f"{slip.power_multiplier:.0f}x power play",
                color=T.BG_ELEV_3, text_color=T.ACCENT,
            ).pack(side="right")
        Pill(top, slip.mode_subtitle, color=T.BG_ELEV_3, text_color=T.TEXT_MUTED).pack(side="right", padx=6)

        # Verified-books pill — shows at a glance which DFS books every leg
        # is placeable on. Verification is implicit: each leg's prop was
        # fetched from its tagged book's live projections feed.
        book_counts: dict[str, int] = {}
        for pk in slip.picks:
            src = (pk.prop.source or "?")
            book_counts[src] = book_counts.get(src, 0) + 1
        books_text = "  ·  ".join(f"{n}× {src}" for src, n in book_counts.items())
        Pill(
            top, f"✓ {books_text}",
            color=T.BG_ELEV_3, text_color=T.POSITIVE,
        ).pack(side="right", padx=6)

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

        # --- Action row --- per-slip Apply, captures `slip` via default arg
        # to dodge the late-binding closure trap when multi-slip rendering.
        action = ctk.CTkFrame(self.results_frame, fg_color="transparent")
        action.pack(fill="x", pady=(4, 8))
        apply_label = (
            "Apply slip to bet slip" if total_slips == 1
            else f"Apply slip {slip_index} to bet slip"
        )
        ctk.CTkButton(
            action, text=apply_label, height=36, width=240,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.BG,
            font=T.FONT_BOLD, corner_radius=8,
            command=lambda s=slip: self._apply_to_slip(s),
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

        header_row = ctk.CTkFrame(left, fg_color="transparent")
        header_row.pack(fill="x", anchor="w")
        ctk.CTkLabel(
            header_row, text=f"Leg {idx}  ·  {pick.prop.player_name}",
            font=T.FONT_HEAD, text_color=T.TEXT,
        ).pack(side="left")
        # Source pill — tells the user which DFS book this leg was verified
        # against. The prop came directly from that book's projections feed,
        # so seeing the badge here means the leg is placeable there.
        src = pick.prop.source or "?"
        src_color = _BOOK_COLORS.get(src.lower(), T.TEXT)
        Pill(
            header_row, f"✓ {src}",
            color=T.BG_ELEV_3, text_color=src_color,
        ).pack(side="left", padx=(10, 0))

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
