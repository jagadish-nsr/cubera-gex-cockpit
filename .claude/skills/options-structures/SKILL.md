---
name: options-structures
description: Map the Nifty call−put book to an option structure and strike selection. Use when implementing the structures/ module or generating trade suggestions.
---

# Book balance → structure

The regime sign is call-gamma magnitude minus put-gamma magnitude. It does not
say to sell premium or to buy premium.

- Call-heavy (POSITIVE): more call gamma than put gamma. Strikes come from the
  call-gamma and put-gamma concentrations. Do not treat the zone above the flip
  as a pin.
- Put-heavy (NEGATIVE): more put gamma than call gamma. Do not treat that as a
  1.5–2x trend by itself.
- Conflict (aggregate book disagrees with the spot-versus-flip side): no new
  risk. De-size.
- Absolute GEX rank, when it is actually computed, is the volatility context.
  Do not infer it from the sign.

## Hard constraints on every suggestion
- No auto-execution. The user confirms.
- Defined max loss in rupees, using the index lot, before the suggestion is shown.
- Exit before expiry. State the exit date.
- Short strike, if any, sits beyond the gamma concentration, not on the magnet.
- A headline or macro that conflicts with the book → de-size.
- Strike step is the index step (Nifty 50, Bank Nifty 100, Sensex 100). Round to
  a listed strike.

## What not to copy in
Do not add an XSP or SPX execution split. Do not sell premium because the label
says positive gamma.
