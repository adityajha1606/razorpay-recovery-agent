"""
Shared fixtures.

`prod_config` and `real_clock` are what every §8 invariant test should be
built on — CLAUDE.md rule 3 and the builder doc's §8 preamble both say the
same thing: invariants run against `prod`, regardless of which Clock the
live app happens to be wired to. There is deliberately no `demo_config`
fixture here that an invariant test could reach for by mistake.
"""

from __future__ import annotations

import pytest

from app.core.clock import RealClock
from app.core.config import AppConfig, load_config


@pytest.fixture
def prod_config() -> AppConfig:
    return load_config(profile_name="prod")


@pytest.fixture
def real_clock() -> RealClock:
    return RealClock()
