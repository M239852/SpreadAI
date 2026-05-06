from __future__ import annotations
import threading
from typing import Callable
import customtkinter as ctk

from ..analysis.generator import generate_slips, GeneratedSlip, MODES
from ..analysis.probability import LegAnalysis
from ..utils.formatters import format_american, format_pct, format_money
from . import theme as T
from .widgets import Card, Pill, StatBlock, make_scroll, hsep
from .state import AppState


# Multi-slip cap. Five is enough for most slates and keeps UI render time
# bounded. Bumping this past ~5 also tends to push slip quality off a cliff
# because the per-game pool exhausts quickly.
SLIP_COUNT_CHOICES = (1, 2, 3, 4, 5)


class GeneratorView(ctk.CTkFrame):
    """Auto-parlay builder. Three modes, research-driven, with reasoning."""

    ALL_BOOKS_LABEL = "All books (best price)"

    def __init__(self, master, state: AppState, on_add_leg: Callable[[LegAnalysis], None]):
        super().__init__(master, fg_color=T.BG)
        self.state = state
        self.on_add_leg = on_add_leg
        self.mode = "balanced"
        self.max_legs = 4
        self.slip_count = 1
        self.dedupe_legs = True
        self.bookmaker_filter: str | None = None
        self._current_slips: list[GeneratedSlip] = []

        state.subscribe(self._on_state_event)

        self.scroll = make_scroll(self)
        self.scroll.pack(fill="both", expand=True, padx=16, pady=16)

        self._build_header()
        self._build_controls()

        self.results_frame = ctk.CTkFrame(self.scroll, fg_color="transparent")
        self.results_frame.pack(fill="x", pady=(10, 0))

        self.placeholder = ctk.CTkLabel(
            self.results_frame,
            text="Pick a mode and click Generate — SpreadAI will research each game,\n"
                 "score every available market, and assemble the best multi-leg slip.",
            font=T.FONT, text_color=T.TEXT_MUTED, justify="left",
        )
        self.placeholder.pack(pady=60)

    # ---------------- Layout ----------------

    def _build_header(self):
        head = ctk.CTkFrame(self.scroll, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=(4, 10))
        ctk.CTkLabel(head, text="Slip Generator", font=T.FONT_TITLE, text_color=T.TEXT).pack(anchor="w")
        ctk.CTkLabel(
            head,
            text="Research-backed parlays — pick a style, we handle the math.",
            font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

    def _build_controls(self):
        card = Card(self.scroll)
        card.pack(fill="x", pady=6)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        ctk.CTkLabel(inner, text="MODE", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        mode_row = ctk.CTkFrame(inner, fg_color="transparent")
        mode_row.pack(fill="x", pady=(6, 0))
        self.mode_buttons: dict[str, ctk.CTkButton] = {}
        for key, cfg in MODES.items():
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

        ctk.CTkLabel(inner, text="LEGS", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(14, 4))
        legs_row = ctk.CTkFrame(inner, fg_color="transparent")
        legs_row.pack(fill="x")
        self.legs_val = ctk.CTkLabel(legs_row, text=f"Max legs: {self.max_legs}", font=T.FONT_BOLD, text_color=T.TEXT, width=120)
        self.legs_val.pack(side="left")
        self.legs_slider = ctk.CTkSlider(
            legs_row, from_=2, to=8, number_of_steps=6,
            fg_color=T.BG_ELEV_3, progress_color=T.ACCENT, button_color=T.ACCENT, button_hover_color=T.ACCENT_HOVER,
            command=self._on_legs_change, width=280,
        )
        self.legs_slider.set(self.max_legs)
        self.legs_slider.pack(side="left", padx=12)

        # SLIPS — how many independent slips to generate in one click. With
        # the dedup toggle on (default), each slip's exact picks are excluded
        # from the next slip's candidate pool.
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

        ctk.CTkLabel(inner, text="SPORTSBOOK", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w", pady=(14, 4))
        book_row = ctk.CTkFrame(inner, fg_color="transparent")
        book_row.pack(fill="x")
        self.book_menu = ctk.CTkOptionMenu(
            book_row, values=[self.ALL_BOOKS_LABEL], width=260, height=32,
            fg_color=T.BG_ELEV_2, button_color=T.BG_ELEV_3, button_hover_color=T.BORDER,
            text_color=T.TEXT, dropdown_fg_color=T.BG_ELEV_2,
            command=self._on_book_change,
        )
        self.book_menu.pack(side="left")
        self.book_menu.set(self.ALL_BOOKS_LABEL)
        self.book_hint = ctk.CTkLabel(
            book_row,
            text="Pick a book to keep every leg on the same slip you could place.",
            font=T.FONT_TINY, text_color=T.TEXT_MUTED,
        )
        self.book_hint.pack(side="left", padx=12)

        self.generate_btn = ctk.CTkButton(
            inner, text="Generate slip", height=40, width=200,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color=T.BG,
            font=T.FONT_BOLD, corner_radius=10,
            command=self._generate,
        )
        self.generate_btn.pack(anchor="w", pady=(14, 0))

        self.progress_lbl = ctk.CTkLabel(inner, text="", font=T.FONT_TINY, text_color=T.TEXT_MUTED)
        self.progress_lbl.pack(anchor="w", pady=(6, 0))

        self._refresh_book_menu()

    # ---------------- Controls behavior ----------------

    def _pick_mode(self, key: str):
        self.mode = key
        self._highlight_mode()

    def _highlight_mode(self):
        for k, b in self.mode_buttons.items():
            if k == self.mode:
                b.configure(fg_color=T.ACCENT, text_color=T.BG)
            else:
                b.configure(fg_color=T.BG_ELEV_2, text_color=T.TEXT)

    def _on_legs_change(self, val: float):
        self.max_legs = int(round(val))
        self.legs_val.configure(text=f"Max legs: {self.max_legs}")

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

    def _on_book_change(self, label: str):
        self.bookmaker_filter = None if label == self.ALL_BOOKS_LABEL else label

    def _on_state_event(self, event: str):
        if event in ("games", "sport"):
            self._refresh_book_menu()

    def _refresh_book_menu(self):
        # Populate from the books actually present on today's slate. We use
        # titles (e.g. "DraftKings") since that's what LegAnalysis.bookmaker stores.
        titles: list[str] = []
        seen: set[str] = set()
        for g in self.state.games:
            for bk in g.bookmakers:
                if bk.title not in seen:
                    seen.add(bk.title)
                    titles.append(bk.title)
        titles.sort()
        values = [self.ALL_BOOKS_LABEL] + titles
        self.book_menu.configure(values=values)

        # Keep current selection if still valid, otherwise fall back to All.
        current = self.book_menu.get()
        if current not in values:
            self.book_menu.set(self.ALL_BOOKS_LABEL)
            self.bookmaker_filter = None

    # ---------------- Generation ----------------

    def _generate(self):
        games = self.state.games
        if not games:
            self.progress_lbl.configure(text="No games loaded. Refresh first.", text_color=T.NEGATIVE)
            return

        self.generate_btn.configure(state="disabled", text="Working…")
        self.progress_lbl.configure(text="Starting…", text_color=T.TEXT_MUTED)

        # Clear existing results
        for w in self.results_frame.winfo_children():
            w.destroy()
        loading = ctk.CTkLabel(self.results_frame, text="Analyzing games…", font=T.FONT, text_color=T.TEXT_MUTED)
        loading.pack(pady=40)

        def progress_cb(done: int, total: int, note: str):
            self.after(0, lambda: self.progress_lbl.configure(
                text=f"{done}/{total} — {note}", text_color=T.TEXT_MUTED
            ))

        book = self.bookmaker_filter
        slip_count = self.slip_count
        dedupe = self.dedupe_legs

        def work():
            slips: list[GeneratedSlip] = []
            err = ""
            try:
                slips = generate_slips(
                    games=games,
                    mode=self.mode,
                    max_legs=self.max_legs,
                    min_legs=2,
                    count=slip_count,
                    dedupe_legs=dedupe,
                    progress_cb=progress_cb,
                    bookmaker_filter=book,
                )
            except Exception as e:
                err = f"Generation failed: {e}"
            self.after(0, lambda: self._on_generated(slips, err, loading))

        threading.Thread(target=work, daemon=True).start()

    def _on_generated(self, slips: list[GeneratedSlip], err: str, loading_widget):
        loading_widget.destroy()
        self.generate_btn.configure(state="normal", text="Generate slip")
        self._current_slips = slips

        if err:
            self.progress_lbl.configure(text=err, text_color=T.NEGATIVE)
            return
        if not slips:
            hint = (
                f"No qualifying legs found on {self.bookmaker_filter}. "
                "Try All books or a different mode."
                if self.bookmaker_filter
                else "No qualifying legs found. Try a different mode."
            )
            self.progress_lbl.configure(text=hint, text_color=T.WARNING)
            return
        book_note = f" · {self.bookmaker_filter} only" if self.bookmaker_filter else ""
        if len(slips) < self.slip_count:
            # The pool ran out before producing every requested slip — tell the
            # user explicitly so they can flip dedup off or widen the slate.
            short_note = (
                f" (asked for {self.slip_count}, slate only supported {len(slips)})"
            )
            color = T.WARNING
        else:
            short_note = ""
            color = T.POSITIVE
        if len(slips) == 1:
            text = f"Built a {len(slips[0].legs)}-leg {slips[0].mode_label} slip{book_note}{short_note}."
        else:
            total_legs = sum(len(s.legs) for s in slips)
            text = (
                f"Built {len(slips)} {slips[0].mode_label} slips · {total_legs} legs total"
                f"{book_note}{short_note}."
            )
        self.progress_lbl.configure(text=text, text_color=color)
        for idx, slip in enumerate(slips, start=1):
            self._render_slip(slip, slip_index=idx, total_slips=len(slips))

    def _render_slip(self, slip: GeneratedSlip, slip_index: int = 1, total_slips: int = 1):
        # When multiple slips are present, lead with a divider + slip-N label
        # so the user can scan slip boundaries at a glance.
        if total_slips > 1:
            divider = ctk.CTkFrame(
                self.results_frame, fg_color=T.BORDER, height=1,
            )
            divider.pack(fill="x", pady=(18 if slip_index > 1 else 6, 4))
            ctk.CTkLabel(
                self.results_frame, text=f"SLIP {slip_index} OF {total_slips}",
                font=T.FONT_TINY, text_color=T.TEXT_MUTED, anchor="w",
            ).pack(anchor="w", padx=8, pady=(0, 2))

        # Header card — mode + stats
        head = Card(self.results_frame)
        head.pack(fill="x", pady=8)
        inner = ctk.CTkFrame(head, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        title = (
            f"{slip.mode_label}  ·  {len(slip.legs)} legs"
            if total_slips == 1
            else f"Slip {slip_index}  ·  {slip.mode_label}  ·  {len(slip.legs)} legs"
        )
        ctk.CTkLabel(top, text=title,
                     font=T.FONT_TITLE, text_color=T.TEXT).pack(side="left")
        Pill(top, slip.mode_subtitle, color=T.BG_ELEV_3, text_color=T.ACCENT).pack(side="right")
        if self.bookmaker_filter:
            Pill(top, f"{self.bookmaker_filter} only",
                 color=T.BG_ELEV_3, text_color=T.POSITIVE).pack(side="right", padx=(0, 6))

        stats = ctk.CTkFrame(inner, fg_color="transparent")
        stats.pack(fill="x", pady=(12, 0))
        p = slip.parlay
        StatBlock(
            stats, "Hit probability",
            format_pct(p.combined_prob),
            value_color=T.edge_color(p.edge),
        ).pack(side="left", padx=(0, 24))
        StatBlock(stats, "Combined odds", format_american(p.combined_american)).pack(side="left", padx=(0, 24))
        StatBlock(stats, "Risk : Reward", f"1 : {p.risk_reward:.2f}").pack(side="left", padx=(0, 24))
        StatBlock(
            stats, "Edge vs price", f"{p.edge * 100:+.1f}%",
            value_color=T.edge_color(p.edge),
        ).pack(side="left", padx=(0, 24))
        StatBlock(
            stats, "EV / $1", format_money(p.ev_per_dollar),
            value_color=T.edge_color(p.ev_per_dollar),
        ).pack(side="left")

        # Overall reasoning
        reasoning_card = Card(self.results_frame, fg_color=T.BG_ELEV_2, corner_radius=10, border_width=0)
        reasoning_card.pack(fill="x", pady=6)
        rinner = ctk.CTkFrame(reasoning_card, fg_color="transparent")
        rinner.pack(fill="x", padx=20, pady=14)
        ctk.CTkLabel(rinner, text="WHY THIS SLIP", font=T.FONT_TINY, text_color=T.TEXT_MUTED).pack(anchor="w")
        ctk.CTkLabel(
            rinner, text=slip.overall_reasoning,
            font=T.FONT_SMALL, text_color=T.TEXT, justify="left", wraplength=860,
        ).pack(anchor="w", pady=(6, 0))

        # Action row — Apply replaces the entire bet slip with this slip's
        # legs. Each rendered slip gets its own button so the user can compare
        # multi-slip output and pick whichever they want to load.
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

        # Per-leg breakdown
        for i, leg in enumerate(slip.legs):
            reasoning = slip.per_leg_reasoning[i] if i < len(slip.per_leg_reasoning) else ""
            self._render_leg(leg, reasoning, i + 1)

    def _render_leg(self, leg: LegAnalysis, reasoning: str, idx: int):
        card = Card(self.results_frame)
        card.pack(fill="x", pady=6)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=18, pady=14)

        top = ctk.CTkFrame(inner, fg_color="transparent")
        top.pack(fill="x")
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            left, text=f"Leg {idx}  ·  {leg.selection}",
            font=T.FONT_HEAD, text_color=T.TEXT,
        ).pack(anchor="w")
        ctk.CTkLabel(
            left, text=f"{leg.matchup}  ·  Best price {format_american(leg.price)} @ {leg.bookmaker}",
            font=T.FONT_TINY, text_color=T.TEXT_MUTED,
        ).pack(anchor="w", pady=(2, 0))

        stats = ctk.CTkFrame(top, fg_color="transparent")
        stats.pack(side="right")
        StatBlock(stats, "Model", format_pct(leg.model_prob), value_color=T.edge_color(leg.edge)).pack(side="left", padx=6)
        StatBlock(stats, "Book", format_pct(leg.book_implied)).pack(side="left", padx=6)
        StatBlock(stats, "Edge", f"{leg.edge * 100:+.1f}%", value_color=T.edge_color(leg.edge)).pack(side="left", padx=6)

        # Adjustment pills
        if leg.adjustments:
            adj = ctk.CTkFrame(inner, fg_color="transparent")
            adj.pack(fill="x", pady=(8, 0))
            for f in leg.adjustments:
                if abs(f.weight) < 0.02:
                    continue
                color = T.POSITIVE if f.weight > 0 else T.NEGATIVE
                Pill(adj, f"{f.name}: {f.weight:+.2f}", color=T.BG_ELEV_3, text_color=color).pack(side="left", padx=(0, 6))

        # Reasoning paragraph
        if reasoning:
            ctk.CTkLabel(
                inner, text=reasoning,
                font=T.FONT_SMALL, text_color=T.TEXT_MUTED,
                justify="left", wraplength=860,
            ).pack(anchor="w", pady=(10, 0), fill="x")

    def _apply_to_slip(self, slip: GeneratedSlip):
        self.state.clear_slip()
        for leg in slip.legs:
            self.on_add_leg(leg)
