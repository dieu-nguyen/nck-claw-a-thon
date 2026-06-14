# test_engine.py
#
# The engine now runs an LLM agent loop (Anthropic Claude API).
# Per project constraints, no automated tests are written for the agent loop itself,
# as it requires a live Anthropic API connection and prompt-file fixtures.
#
# Integration tests for engine.py should be run manually or in a CI environment
# with ANTHROPIC_API_KEY set and appropriate prompt files present.
