"""Offline checks for the paid-run notebook's concurrency adapter."""
from __future__ import annotations

import ast
import concurrent.futures
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import time


NOTEBOOK = (Path(__file__).resolve().parents[3]
            / "notebooks" / "CRE_MASS_ERROR_HUNT.ipynb")
REAL_FAILURES = json.loads((
    Path(__file__).with_name("testdata") / "natural_run_failures_20260821.json"
).read_text(encoding="utf-8"))["cases"]


def _notebook():
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))


def _cell_containing(text):
    for cell in _notebook()["cells"]:
        source = "".join(cell.get("source") or [])
        if text in source:
            return source
    raise AssertionError(f"notebook cell containing {text!r} was not found")


def _function(source, name, globals_):
    tree = ast.parse(source)
    node = next(n for n in tree.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == name)
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    exec(compile(module, str(NOTEBOOK), "exec"), globals_)  # noqa: S102
    return globals_[name]


def test_notebook_cells_compile_and_wire_four_bounded_workers():
    notebook = _notebook()
    for index, cell in enumerate(notebook["cells"]):
        if cell.get("cell_type") == "code":
            compile("".join(cell.get("source") or []),
                    f"{NOTEBOOK}:cell-{index}", "exec")
    bootstrap = _cell_containing("EXPECTED_COMMIT =")
    setup = _cell_containing("BAND2_MAX_WORKERS = 4")
    paid = _cell_containing("def make_logged_anthropic_call")
    assert "BAND2_MAX_WORKERS = 4" in setup
    assert "merge-base\", \"--is-ancestor" in bootstrap
    assert "remote_commit == EXPECTED_COMMIT" not in bootstrap
    assert "max_workers=BAND2_MAX_WORKERS" in paid
    assert "requests.Session, TRANSPORT_EVENTS_PATH" in paid


def test_cached_provider_adapter_warms_once_then_allows_overlap():
    source = _cell_containing("def make_logged_anthropic_call")
    events = []
    requests_seen = []
    guard = threading.Lock()
    active = 0
    peak = 0
    call_number = 0

    class Messages:
        def create(self, **kwargs):
            nonlocal active, peak, call_number
            with guard:
                call_number += 1
                number = call_number
                active += 1
                peak = max(peak, active)
                requests_seen.append(kwargs)
            # The cache-creating request is deliberately slower. Other requests
            # must not begin until it completes, then should overlap each other.
            time.sleep(0.04 if number == 1 else 0.015)
            with guard:
                active -= 1
            usage = SimpleNamespace(
                input_tokens=1, output_tokens=1,
                cache_creation_input_tokens=1 if number == 1 else 0,
                cache_read_input_tokens=0 if number == 1 else 1)
            return SimpleNamespace(
                usage=usage,
                content=[SimpleNamespace(type="text", text="ok")])

    client = SimpleNamespace(messages=Messages())

    def cached_text_content(prompt, prefix, ttl):
        return [
            {"type": "text", "text": prefix,
             "cache_control": {"type": "ephemeral", "ttl": ttl}},
            {"type": "text", "text": prompt[len(prefix):]},
        ]

    def join_text_content(content):
        return "".join(block["text"] for block in content)

    namespace = {
        "threading": threading,
        "time": time,
        "MODEL": "test-model",
        "BAND2_MAX_TOKENS": 64,
        "BAND2_RETRY_MAX_TOKENS": 128,
        "PROMPT_CACHE_TTL": "5m",
        "MODEL_CALL_EVENTS_PATH": Path("unused.jsonl"),
        "sha256_bytes": lambda data: hashlib.sha256(data).hexdigest(),
        "cached_text_content": cached_text_content,
        "join_text_content": join_text_content,
        "usage_dict": lambda response: {
            key: int(getattr(response.usage, key, 0) or 0) for key in (
                "input_tokens", "output_tokens", "cache_creation_input_tokens",
                "cache_read_input_tokens")},
        "utc_now": lambda: "now",
        "append_jsonl": lambda _path, event: events.append(dict(event)),
    }
    make_call = _function(
        source, "make_logged_anthropic_call", namespace)
    call = make_call(lambda: client, "coverage_fulltext_v3",
                     cache_prefix="stable-prefix:")
    prompts = [f"stable-prefix:claim-{i}" for i in range(4)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(call, prompts))

    assert results == ["ok"] * 4
    assert len(events) == len(prompts)
    assert sum(e["prompt_cache_warm_request"] is True for e in events) == 1
    assert peak >= 2
    sent = [join_text_content(row["messages"][0]["content"])
            for row in requests_seen]
    assert sorted(sent) == sorted(prompts)


def test_provider_adapter_covers_the_real_empty_claim_response():
    source = _cell_containing("def make_logged_anthropic_call")
    event = REAL_FAILURES["empty_claim_extraction"]["provider_event"]
    assert event["stage"] == "claim_extraction"
    assert event["output_chars"] == 0
    assert event["output_sha256"] == hashlib.sha256(b"").hexdigest()
    assert '"empty_response"' in source
    assert '"max_tokens"' in source
    assert "BAND2_RETRY_MAX_TOKENS" in source
    assert '"stop_reason"' in source
    assert '"content_types"' in source
