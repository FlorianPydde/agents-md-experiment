"""Governed Service Request Runner.

A small back office system that turns incoming service requests into plans
of operations, runs those operations under policy control (some running on
their own, others requiring a named role's approval), and records every
meaningful occurrence to an append only log held in SQLite.
"""

__version__ = "0.1.0"
