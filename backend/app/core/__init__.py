"""Sport-agnostic building blocks shared by every sport this project prices
(AFL today, NBA being added — see docs/MULTI_SPORT_ARCHITECTURE.md).

Deliberately small. A module only belongs here when BOTH of these hold:

1. It encodes no sport's rules — no stat names, no season structure, no
   ORM model owned by one sport.
2. A second sport actually needs it now, not hypothetically.

What lives here today:

- settlement.py  — "did this value clear this line" result vocabulary and
                   arithmetic (lifted out of AFL's prop_settlement.py, which
                   now delegates here).
- prospective.py — the prospective-integrity primitives: UTC normalisation,
                   the information-cutoff check, and the write-once ORM
                   guard that makes "freeze now, settle once, never
                   overwrite" a mechanical guarantee rather than a
                   convention.
- clv.py         — closing-line-value arithmetic.

Several other modules are ALSO sport-agnostic pure maths but pre-date this
package and are imported from where they already live rather than moved
(moving them would touch dozens of AFL imports for no behavioural gain):
app/edges/overround.py, app/edges/fair_odds.py, app/modelling/metrics.py,
app/modelling/bootstrap.py, app/modelling/calibration_methods.py.
"""
