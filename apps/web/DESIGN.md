# Strategy Codebot Visual System

## Summary

Strategy Codebot uses an Apple-like product-gallery language adapted to an operational trading workspace. The product is the artifact: strategy code, workflow state, paper bot runtime evidence, and backtest charts. UI chrome must stay quiet so those artifacts carry the page.

Use low-density surfaces, near-invisible navigation, crisp typography, a single blue action color, and restrained utility cards. Do not turn workspace screens into marketing hero layouts.

## Tokens

- Canvas: pure white `#ffffff` for product surfaces and cards.
- Page canvas: parchment `#f5f5f7` for the default app background.
- Dark tile: `#272729`, with `#2a2a2c` and `#252527` for adjacent dark surfaces.
- Text: `#1d1d1f` on light surfaces and `#f5f5f7` on dark surfaces.
- Accent: Signal Blue `#0057d9`; hover `#006bff`; active `#0046b8`.
- Hairline: `#d2d2d7`; dividers `#e0e0e0`.
- Radius: 8px maximum for operational cards, drawers, charts, and panels. Buttons may be full pill.
- Typography: SF/system stack first, Inter fallback, no negative letter spacing.

## Component Rules

- Navigation is frosted and thin. It should feel present but visually secondary.
- Primary actions are blue pills. Secondary actions are white or frosted pills with hairline borders.
- Workspace cards use hairlines and no decorative shadows. Only product/chart preview surfaces may use the product shadow.
- Chat assistant output is mostly text. User messages use the blue bubble.
- Workflow rail is progress/info chrome. Blocking questions stay in typed task prompts.
- Artifacts, backtest dashboards, and paper bot cards are product tiles. Their preview, chart, or runtime evidence is the hero content.
- Search and composer controls use frosted pill or rounded 8px shells with visible focus rings.

## Page Mapping

- Signed-out home: full-viewport product tile with the prompt as the primary interaction and app preview as visual context.
- Chat workspace: parchment canvas, frosted sidebar, quiet composer, and artifact drawer as a product tile.
- Artifacts: utility-card grid; selected artifact opens into a product-tile drawer.
- Paper bots: utility-card runtime grid; selected runtime opens into a product-tile drawer.
- Backtest dashboard: dark tile, bright data color, minimal chart chrome.

## Do Not

- Do not add decorative gradients, blobs, bokeh, or marketing-card stacks.
- Do not introduce new backend, auth, runtime, or API contracts for visual work.
- Do not inline one-off hex values in components when a token exists.
- Do not use negative tracking or oversized type inside dense tool panels.
- Do not make nested cards; use section spacing, hairlines, or full-width bands instead.
