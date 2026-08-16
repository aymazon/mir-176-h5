# Mir 1.76 H5 Design System

Vertical-screen (375×667) browser port of the classic 传奇 1.76. This file codifies
the token system extracted from the W0 scaffold (`src/index.css`) and the game-surface
tokens locked by the W1T7 plan (D8 procedural placeholder art).

## 1. Atmosphere & Identity

A dim lantern-lit 2D world. Dark umber ground, parchment-warm text, one gold accent
for anything interactive. The signature is the **old-MMORPG HUD frame**: a soft
gradient veil over the world at the top carrying status, chunky tactile d-pad buttons
bottom-right, and a 32px checker floor under a simple class-colored hero. The world
itself is placeholder art (D8): two-shade floor tiles and 32px class-color bodies.
No glass, no gradients-on-white — warmth comes from the umber/gold palette, not glow.

## 2. Color

### Palette

| Role | Token | Value | Usage |
|------|-------|-------|-------|
| Surface/app | --color-bg | #120e0b | Page background |
| Surface/app soft | --color-bg-soft | #1c1611 | Panels, cards, inputs-on-dark |
| Surface/well | --color-well | #0c0906 | World void behind floor tiles (canvas background) |
| Surface/panel | --color-panel | rgba(28,22,17,0.88) | HUD veil, d-pad, banners |
| Text/primary | --color-fg | #ece4d4 | Body text, labels |
| Text/secondary | --color-muted | #9d8f79 | Captions, meta, hints |
| Accent/primary | --color-accent | #d2a24c | CTAs, focus rings, version mark |
| Accent/strong | --color-accent-strong | #e3b866 | CTA hover |
| Border/default | --color-border | #3a2f23 | Cards, inputs, bars |
| Status/gold | --color-gold | #e8c15a | Gold readout |
| Status/hp | --color-hp | #c0392b | HP bar fill |
| Status/mp | --color-mp | #2980ff | MP bar fill |
| Status/danger | --color-danger | #e74c3c | Destructive, errors, kick banner |
| Status/success | --color-success | #27ae60 | Confirmed/success lines |
| Class/warrior | --class-warrior | #c0392b | Warrior sprite body (also HP hue) |
| Class/mage | --class-mage | #2980ff | Mage sprite body (also MP hue) |
| Class/taoist | --class-taoist | #27ae60 | Taoist sprite body |
| Class/other | --class-other | #b08d57 | Other players (W1 wire carries no class) |
| World/floor A | --floor-a | #2b221a | Checker floor shade (even tiles) |
| World/floor B | --floor-b | #251d16 | Checker floor shade (odd tiles) |
| World/sprite shadow | --sprite-shadow | rgba(0,0,0,0.35) | Sprite ground shadow |
| World/sprite skin | --sprite-skin | #e8c39e | Sprite head |
| World/sprite face | --sprite-face | #1a120a | Sprite eyes |

### Rules
- Accent gold is reserved for interactive elements (CTAs, focus, version mark).
  Never decorative.
- Class colors are the ONLY identity for player sprites in W1 (no real art yet);
  they double as HP/MP hues so the HUD and world read as one system.
- Never introduce a color not in this table. Extend the table first. Canvas
  2D drawing (game/assets.ts) mirrors these tokens as named constants because
  canvas contexts cannot read CSS custom properties.

## 3. Typography

### Scale

| Level | Token | Size | Weight | Usage |
|-------|-------|------|--------|-------|
| Display | --text-display | 40px | 700 | App title (auth screen) |
| H1 | --text-xl | 24px | 700 | Screen titles (角色选择, HUD-free) |
| H2/card | --text-lg | 18px | 600 | Character name, d-pad glyph context |
| Body | --text-md | 15px | 400 | Default text, inputs, buttons |
| Small | --text-sm | 13px | 400 | Meta lines, HUD stats, banners |
| Caption | --text-xs | 11px | 500 | Bar labels, fine print |

### Font Stack
- Display: `Georgia, 'Times New Roman', 'Songti SC', SimSun, serif` (--font-display)
  — the classic-game serif identity carried from the W0 scaffold.
- Body/UI: `system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif`
  (--font-body) — legible at 13px on a 375px viewport.

### Rules
- Body text never below 13px (mobile floor, Chinese glyphs).
- Chinese-first copy: 注册/登录/创建/删除/进入 throughout the UI.

## 4. Spacing & Layout

### Base Unit
All spacing derives from **4px**.

| Token | Value | Usage |
|-------|-------|-------|
| --space-1 | 4px | Tight: hud row gaps, d-pad cell gap |
| --space-2 | 8px | Compact: list gap, inline groups, input padding-y |
| --space-3 | 16px | Default: card padding, panel padding, form row gap |
| --space-4 | 24px | Generous: screen padding, modal padding |
| --space-5 | 32px | D-pad button size, section separation |
| --space-6 | 48px | Major separation |

### Layout
- Fixed mobile-first: the app is designed at 375×667 (plan-locked). No horizontal
  scroll of primary content at that width; auth/charselect screens center a
  320–420px column.
- Game screen: `position: fixed; inset: 0` stack — canvas fills the play area,
  HUD veils the top (pointer-events: none), d-pad floats bottom-right
  (pointer-events: none wrapper, auto on buttons), banner above the d-pad.
- World math: 1 tile = 32px (--tile-px). Camera centers the character; screen
  delta → cell uses the 32px constant.

## 5. Components

### Button
- **Structure**: `<button class="btn [btn--primary|btn--danger]">label</button>`
- **Variants**: default (outline), primary (gold fill), danger (danger border/text)
- **Spacing**: padding 8px 16px (--space-2/--space-3)
- **States**: default; hover (border → accent, primary → accent-strong); active
  (`transform: translateY(1px)`); focus (2px accent ring, offset 1px); disabled
  (opacity .5, no pointer)
- **Accessibility**: real `<button>`, focus ring visible, contrast ≥ 4.5:1 (gold on
  #1c1611 ≈ 7:1; dark text on gold fill)
- **Motion**: 120ms ease-out on transform/background/border

### Field (Input / Select)
- **Structure**: `<label class="field">中文标签 <input|select></label>` (wrapped
  label so `getByLabel` works in tests)
- **States**: default, focus (accent border + soft gold ring), disabled
- **Spacing**: padding 8px 12px; row gap 16px

### Tab (auth mode switch 登录/注册)
- **Structure**: two `<button role="tab">` in `.auth-tabs`
- **States**: selected (gold underline + fg text), unselected (muted), hover
- **Accessibility**: `role="tablist"`/`role="tab"` + `aria-selected`

### CharacterCard
- **Structure**: name (display serif, lg) + meta line (class · Lv) left,
  进入 (primary) + 删除 (danger) right
- **States**: default; empty list shows a muted placeholder line

### Modal (delete confirm)
- **Structure**: fixed veil + panel: prompt, confirm-name input, 取消/删除
- **States**: open; delete disabled until typed name matches exactly
- **Accessibility**: focus goes to the input on open (autoFocus); Escape not
  required (W1); 取消 is the escape hatch

### HUD
- **Structure**: top veil — stats row (等级/地图/金币), HP bar, MP bar, `经验: n`
- **States**: re-renders on a 200ms throttle reading ViewStore; bar width =
  value / level-1 base max, clamped 0–100% (no server max fields in W1)
- **Rules**: pointer-events: none — never blocks world clicks

### DPad
- **Structure**: 3×2 grid of 48px buttons (up/left/down/right) bottom-right,
  inline SVG chevrons
- **States**: default, :active (gold tint + scale .94)
- **Interaction**: pointerdown starts a 200ms move-interval to the adjacent cell
  (from the current interpolated int cell); pointerup/leave/cancel stops it
- **Accessibility**: `touch-action: none`, user-select none; labels via aria-label

### Panel
- **Structure**: `--color-bg-soft` fill, 1px --color-border, 8px radius
- **Usage**: auth form, char cards, create form, modal panel

### ChatPanel
- **Structure**: bottom-left column — `.chat__log` (latest 50 lines of global chat,
  `{name}: {text}`, newest at the bottom, auto-scrolls, 13px) + `.chat__form` row
  (`.chat__input` maxLength 100, aria-label 聊天, Enter or 发送 submits)
- **Placement**: absolute, left/bottom --space-3 (16px), 224px wide — clears the d-pad
  (bottom-right) and the HUD veil (top)
- **Pointer events**: wrapper is `pointer-events: none`; only the log, input, and send
  button re-enable them — the panel never blocks world clicks or the d-pad
- **States**: empty (muted 暂无消息 line); lines in --color-fg; input focus = accent
  border + soft gold ring (same as .field); empty submit is a no-op (form guard)
- **Tokens**: --color-panel log background, --color-border, --radius-sm, --text-sm
  (13px Chinese floor), --space-1/2/3

## 6. Motion & Interaction

### Timing

| Type | Duration | Easing | Usage |
|------|----------|--------|-------|
| Micro | 100–120ms | ease-out | Button press, d-pad press |
| Standard | 200ms | linear | HUD bar width catch-up |
| World | 250ms | 2-frame step | Sprite float (y: 0 → −2px, 125ms per frame) |

### Rules
- DOM motion: only `transform` and `opacity` (plus bar `width` as a 200ms-throttled
  state readout, not continuous animation).
- World motion is driven by the server-interpolated position (300ms trailing
  lerp in ViewStore); the only local animation is the 2-frame float.
- No decorative animation. The float communicates "alive"; the d-pad press
  communicates input.

## 7. Depth & Surface

### Strategy
**borders + tonal-shift** (dark theme): panels sit one shade lighter than the page
on a 1px `--color-border`; the HUD veil is a vertical gradient from the panel color
to transparent so the world stays visible beneath it. No drop shadows (the canvas
world provides its own depth via tile shading and sprite shadows).

| Type | Value | Usage |
|------|-------|-------|
| Border/default | 1px solid var(--color-border) | Cards, inputs, bars, d-pad |
| Veil | linear-gradient(180deg, rgba(12,9,6,.92) → rgba(12,9,6,0)) | HUD, bottom d-pad scrim |

## 8. Accessibility Constraints & Accepted Debt

### Constraints
- WCAG 2.2 AA target for the DOM surfaces (auth, charselect, HUD readouts):
  contrast floor 4.5:1 body / 3:1 large text; visible focus on every interactive
  element; full keyboard reachability for auth + charselect (the game itself is
  pointer/touch-driven by design).
- `prefers-reduced-motion`: disable the sprite float (render at frame 0).

### Accepted Debt
| Item | Location | Why accepted | Owner / Exit |
|------|----------|--------------|--------------|
| Placeholder procedural art (2-shade floor, 32px class-color bodies) | game/assets.ts | D8 decision: real art pipeline lands in W2 with `loadAtlas()` real assets | W2T1 content/art task |
| HP/MP bars use level-1 base max as denominator | ui/HUD.tsx | CharacterState has no max_hp/max_mp in the W1 protocol; exp curve data also absent (W2T2) | W2 when stat/level data ships |
| No wall/tilemap rendering | game/mapLayer.ts | W1 protocol carries no map data; server is path authority. Reconstructing walls client-side is out of contract | W2 map stream, if ever |
| DPad not keyboard-operable (no arrow-key binding) | ui/GameView.tsx | W1 input contract is click + virtual d-pad; keyboard binding is not in the task scope | W2 UX pass |
