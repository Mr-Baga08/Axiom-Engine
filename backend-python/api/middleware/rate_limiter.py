# Alias module — rate_limit.py is the canonical location.
# This file exists so that `from middleware.rate_limiter import limiter` resolves.
from middleware.rate_limit import limiter  # noqa: F401
