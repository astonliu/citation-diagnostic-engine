"""Transports, rate limits, launchers, wiring and sandboxes.

Nothing here decides a taxonomy question. This is the machinery that gets a
prompt to a provider, keeps a run inside its rate and cost budget, verifies that
a launch is governed, and records what was actually spent.

``completer.make_completer`` is the one seam a call site touches: it turns a
model id into a provider-appropriate ``Callable[[str], str]``, and nothing
downstream of it can tell which vendor answered. That is the requirement -- a
taxonomy's behaviour must never be conditional on the transport underneath it.
"""
