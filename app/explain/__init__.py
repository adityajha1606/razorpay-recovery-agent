# This package is intentionally kept read-only: it may import from
# app.core.case_store and verify_chain, but never from app.core.state_machine
# or app.core.commit_backend. See .importlinter for enforcement.