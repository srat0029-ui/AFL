"""NBA participation / rotation layer ("V1.5") around Expected Minutes V1.

V1 estimates E(minutes | player plays). This package adds, separately:

- P(play): the player records minutes in the game (enters it at all);
- P(rotation): the player plays at least ROTATION_MINUTES (10) - a genuine
  rotation role rather than an emergency / garbage-time cameo;
- a team-minutes reconciliation that adjusts V1's conditional minutes so
  the team's EXPECTED total (sum of P(play) x E(minutes | play)) respects the
  minutes actually available - adopted only if it improves unseen error.

The quantities are kept apart on purpose. A player prop is void if the player
does not play, so its pricing needs E(minutes | play) and P(play) as two
numbers, never their product.

Everything here uses ONLY historically reconstructable information, read
through the same as-of feature builder as V1 (app/nba/minutes/features.py).
Prospective injury/availability evidence is exposed next to predictions but
never changes them; a future Minutes V2 adds it as a separate, labelled
layer without redefining V1 or V1.5.

Modules: features, dataset, models, metrics, evaluation, allocation,
overtime, experiment, prospective, cli.
"""
