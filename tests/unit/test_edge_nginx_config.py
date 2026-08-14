"""Text/structure guards on docker/edge.nginx.conf, plus one `nginx -t`
integration check.

`edge` is the sole thing `cloudflared` forwards to (see
tests/unit/test_compose_port_exposure.py::test_the_edge_is_the_only_ingress),
so every security property this platform claims for its public surface — the
real-client-IP trust boundary, per-route rate limits, upload size caps, and
SigV4-valid presigned downloads — lives entirely inside this one nginx
config. A missing directive here is invisible at the Python/FastAPI layer:
`api/services/idempotency.py` trusts whatever `X-Forwarded-For` arrives, and
nothing downstream of nginx can tell a correctly-signed presigned URL from
one that will 403 because `Host` was proxied as `$proxy_host` instead of
`$host`. Hence: guard the text directly.

Most of these are substring/regex guards rather than a real config parse
(nginx.conf has no standard library parser in Python) — deliberately strict
about the ONE guard that must not be a substring match
(`client_max_body_size`, compared numerically against the app's own
`MAX_SEED_PDF_BYTES`, not "does '32m' appear somewhere"), because a
substring match there would pass even if nginx's cap silently fell below the
app's own upload limit.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from ai_engine.data_gen.constants import MAX_SEED_PDF_BYTES

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONF_PATH = _REPO_ROOT / "docker" / "edge.nginx.conf"
_CONF_TEXT = _CONF_PATH.read_text(encoding="utf-8")


def _strip_comments(text: str) -> str:
    """Return `text` with every `#`-to-end-of-line comment removed.

    Every "this directive must NOT appear" guard below has to run against
    this, not the raw file. The directives we forbid are precisely the ones
    a careful author names in a comment explaining why they are forbidden —
    `# NOT $proxy_host`, `# NO rewrite`, `# OVERWRITE, not
    $proxy_add_x_forwarded_for` — so a raw-text `not in` fails on a config
    that is completely correct, and the only way to "fix" it is to delete
    the explanation. That is the same trap `WORKING_LOG.md` records from the
    /ready round, where a guard asserted on `inspect.getsource(...)` and
    matched the comment describing the old bug rather than the code itself.
    A guard that a comment can satisfy, or break, is not guarding anything.

    Simple line-wise strip: nginx string literals (`log_format`'s quoted
    format) contain no `#` in this file, and a guard that quietly mangled
    one would be caught by the `nginx -t` test at the bottom.
    """
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


# ---------------------------------------------------------------------------
# Brace-matching helpers — nginx.conf is not JSON/YAML, so we walk `{`/`}`
# ourselves rather than pretending a regex can find the end of a block.
# ---------------------------------------------------------------------------


def _block_from(text: str, open_brace_index: int) -> str:
    """Given the index of an opening `{`, return the text through its
    matching `}` (inclusive)."""
    depth = 0
    j = open_brace_index
    while j < len(text):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace_index : j + 1]
        j += 1
    raise AssertionError("unbalanced braces in docker/edge.nginx.conf")


def _server_blocks(text: str) -> list[str]:
    blocks = []
    for m in re.finditer(r"(?m)^server\s*\{", text):
        blocks.append(_block_from(text, m.end() - 1))
    return blocks


def _location_block(text: str, selector: str) -> str:
    """`selector` is everything between `location` and the opening `{`,
    e.g. `/api/v1/datasets/` or `= /health`."""
    pattern = re.compile(r"(?m)^\s*location\s+" + re.escape(selector) + r"\s*\{")
    m = pattern.search(text)
    assert m, f"location {selector!r} not found in the given block"
    return _block_from(text, m.end() - 1)


# Parse the comment-free form throughout: brace matching is also safer
# against a `{` or `}` that only ever appears inside a comment.
_CONF_CODE = _strip_comments(_CONF_TEXT)

_SERVERS = _server_blocks(_CONF_CODE)
assert len(_SERVERS) == 3, (
    f"expected exactly 3 `server` blocks (catch-all, app, storage), found {len(_SERVERS)}"
)
# Select by what each block DOES, not by position — a reordering must not
# silently repoint these constants at the wrong block.
def _pick(marker: str, what: str) -> str:
    """`next(...)` with a message.

    A bare generator here raises `StopIteration` at import time, which pytest
    reports as a collection ERROR for the whole file with no explanation —
    the entire suite goes red and the reader learns nothing. Deleting
    `default_server` is the likeliest real regression in this file, so its
    failure has to name itself.
    """
    for block in _SERVERS:
        if marker in block:
            return block
    raise AssertionError(
        f"no `server` block in docker/edge.nginx.conf contains {marker!r} "
        f"({what}). If that block was removed or renamed deliberately, update "
        "this file's guards to match — do not leave them selecting nothing."
    )


_STORAGE_SERVER = _pick("proxy_pass http://minio:9000", "the storage vhost")
_APP_SERVER = _pick("proxy_pass http://api:8000", "the app vhost")
_CATCHALL_SERVER = _pick(
    "default_server",
    "the catch-all that rejects unknown Hosts; without it nginx promotes the "
    "first block and unknown Hosts are answered with the SPA at status 200",
)
assert _CATCHALL_SERVER is not _APP_SERVER and _CATCHALL_SERVER is not _STORAGE_SERVER


# ---------------------------------------------------------------------------
# Storage server — SigV4 correctness
# ---------------------------------------------------------------------------


def test_storage_host_header_is_dollar_host_not_proxy_host() -> None:
    """nginx's own default for `proxy_set_header Host` (when unset) is
    `$proxy_host`, which for `proxy_pass http://minio:9000` resolves to the
    literal string `minio:9000` — MinIO signs against the Host the CLIENT
    sent, so every presigned URL would 403 with a signature mismatch.
    Mutate `$host` -> `$proxy_host` here and this must go red."""
    hosts = re.findall(r"proxy_set_header\s+Host\s+(\S+);", _STORAGE_SERVER)
    assert hosts, "storage server never sets a Host header"
    assert all(h == "$host" for h in hosts), f"storage server sets Host to {hosts!r}, not $host"
    assert "$proxy_host" not in _STORAGE_SERVER


def test_storage_proxy_pass_has_no_path_and_no_rewrite() -> None:
    """SigV4 signs the canonical URI. A trailing slash or path component on
    `proxy_pass`, or any `rewrite` directive, changes the URI nginx forwards
    relative to what the client signed — either breaks every signature."""
    m = re.search(r"proxy_pass\s+(\S+)\s*;", _STORAGE_SERVER)
    assert m, "storage server has no proxy_pass"
    assert m.group(1) == "http://minio:9000", (
        f"storage proxy_pass is {m.group(1)!r} — must be exactly 'http://minio:9000' "
        "with no trailing slash and no path"
    )
    assert "rewrite" not in _STORAGE_SERVER


def test_storage_access_log_format_excludes_query_string() -> None:
    """Presigned URLs carry the signature itself
    (`X-Amz-Signature`/`X-Amz-Credential`) as query parameters. Any log
    format built from `$request`, `$request_uri`, `$args`, or
    `$query_string` would write a live, still-valid signature to disk —
    reconciling with ADR-009's rejection of `?token=` on the same grounds
    for the WebSocket auth handshake."""
    m = re.search(r"access_log\s+\S+\s+(\S+?);", _STORAGE_SERVER)
    assert m, "storage server has no access_log directive"
    format_name = m.group(1)
    assert format_name != "combined", "storage access_log uses nginx's default format (logs $request)"

    fmt_m = re.search(
        r"log_format\s+" + re.escape(format_name) + r"\s+(.*?);",
        _CONF_CODE,
        re.DOTALL,
    )
    assert fmt_m, f"no log_format definition found for {format_name!r}"
    tokens = {t.lstrip("$") for t in re.findall(r"\$[A-Za-z_]+", fmt_m.group(1))}
    forbidden = {"request", "request_uri", "args", "query_string"}
    hit = tokens & forbidden
    assert not hit, f"log_format {format_name!r} includes forbidden variable(s): {sorted(hit)}"


def test_storage_disables_response_buffering_to_disk() -> None:
    """Without both of these, a multi-GB GGUF download would spool through
    this container's own disk before reaching the client — `edge` is not
    provisioned as a cache."""
    assert "proxy_buffering off;" in _STORAGE_SERVER
    assert "proxy_max_temp_file_size 0;" in _STORAGE_SERVER


def test_storage_server_proxies_nothing_to_api() -> None:
    assert "api:8000" not in _STORAGE_SERVER


def test_app_server_proxies_nothing_to_minio() -> None:
    assert "minio:9000" not in _APP_SERVER


# ---------------------------------------------------------------------------
# real_ip trust boundary
# ---------------------------------------------------------------------------


def test_real_ip_trusts_cloudflare_header() -> None:
    # `\s+`, not a literal single space: the directive is column-aligned with
    # its neighbours in the conf, and a guard that a re-indent can break is a
    # guard someone will eventually delete rather than debug.
    assert re.search(r"(?m)^\s*real_ip_header\s+CF-Connecting-IP\s*;", _CONF_CODE), (
        "no `real_ip_header CF-Connecting-IP` — behind cloudflared every "
        "request arrives from one container IP, so without this every "
        "anonymous caller collapses into a single idempotency bucket "
        "(see api/services/idempotency.py::client_host)"
    )
    assert re.search(r"(?m)^set_real_ip_from\s+\S+;", _CONF_CODE), (
        "no set_real_ip_from directive — real_ip_header alone does nothing "
        "without at least one trusted source range"
    )


# ---------------------------------------------------------------------------
# XFF overwrite (not append) — see api/services/idempotency.py:54-83
# ---------------------------------------------------------------------------


def test_app_locations_overwrite_x_forwarded_for() -> None:
    """`$proxy_add_x_forwarded_for` APPENDS the client's own (forgeable)
    X-Forwarded-For to whatever it already sent. Overwriting with
    `$remote_addr` instead makes the header a statement from nginx, not the
    client — required for `idempotency.client_host()`'s "first hop is the
    real client" assumption to hold with exactly one hop, not an
    attacker-controlled chain."""
    assert "$proxy_add_x_forwarded_for" not in _APP_SERVER
    xff = re.findall(r"proxy_set_header\s+X-Forwarded-For\s+(\S+);", _APP_SERVER)
    assert xff, "app server never sets X-Forwarded-For"
    assert all(v == "$remote_addr" for v in xff), f"X-Forwarded-For set to {xff!r}, not $remote_addr"


def _app_proxy_locations() -> list[tuple[str, str]]:
    """Every `location` in the app server that proxies to the API.

    Enumerated from the config rather than listed by hand: the point of the
    two guards below is to catch a location nobody remembered to add to a
    list.
    """
    found: list[tuple[str, str]] = []
    for m in re.finditer(r"(?m)^\s*location\s+([^\{]+?)\s*\{", _APP_SERVER):
        block = _block_from(_APP_SERVER, m.end() - 1)
        if "proxy_pass http://api:8000" in block:
            found.append((m.group(1).strip(), block))
    assert found, "no location in the app server proxies to api:8000"
    return found


@pytest.mark.parametrize(
    "selector,block", _app_proxy_locations(), ids=[s for s, _ in _app_proxy_locations()]
)
def test_every_api_proxy_location_overwrites_x_forwarded_for(selector: str, block: str) -> None:
    """Completeness, not just correctness — the guard above cannot see a
    location that omits the directive entirely.

    nginx forwards a client's own `X-Forwarded-For` verbatim when a location
    does not override it. So a new `location /admin/ { proxy_pass
    http://api:8000; }` with no override hands attacker-controlled input
    straight to `api/services/idempotency.py::client_host` and to the
    `client_ip` field in every request log — while
    `test_app_locations_overwrite_x_forwarded_for` stays green, because it
    only inspects the directives that ARE present.

    Both mutations that motivated this test passed the old guard: deleting
    the XFF line from `/api/`, and adding a new proxy location without one.
    """
    m = re.search(r"proxy_set_header\s+X-Forwarded-For\s+(\S+);", block)
    assert m, (
        f"location {selector!r} proxies to the API but never sets "
        "X-Forwarded-For. nginx will forward the client's own header "
        "verbatim, making idempotency.client_host() attacker-controlled."
    )
    assert m.group(1) == "$remote_addr", (
        f"location {selector!r} sets X-Forwarded-For to {m.group(1)!r}. It "
        "must OVERWRITE with $remote_addr; appending preserves the "
        "forgeable client-supplied chain."
    )


@pytest.mark.parametrize(
    "selector,block", _app_proxy_locations(), ids=[s for s, _ in _app_proxy_locations()]
)
def test_every_api_proxy_location_is_rate_limited(selector: str, block: str) -> None:
    """`_EXPECTED_LOCATION_ZONES` is an allowlist and therefore cannot catch
    a route nobody added to it. This is the completeness half.

    `= /health` is the one deliberate exception — uptime monitors poll it
    and it touches no dependency (`api/main.py`'s /health is
    dependency-free by design, unlike /ready).
    """
    if selector == "= /health":
        pytest.skip("/health is deliberately unlimited for uptime monitors")
    # The two directives do NOT share a syntax: `limit_req zone=name burst=N`
    # takes a key=value, `limit_conn name number` takes bare positionals.
    # Matching only the first form silently exempts every WebSocket location.
    assert re.search(r"limit_req\s+zone=\w+|limit_conn\s+\w+\s+\d+\s*;", block), (
        f"location {selector!r} proxies to the API with no limit_req/limit_conn "
        "zone. Every route reachable from the public internet needs one — the "
        "app-level quota only counts jobs, not requests."
    )


# ---------------------------------------------------------------------------
# Upload cap — numeric comparison against the app's own constant, NOT a
# substring match. Raising MAX_SEED_PDF_BYTES without raising nginx's cap in
# lockstep must fail this test.
# ---------------------------------------------------------------------------

_SIZE_UNITS = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3}


def _parse_nginx_size(literal: str) -> int:
    m = re.match(r"^(\d+)([kKmMgG]?)$", literal.strip())
    assert m, f"unparseable nginx size literal: {literal!r}"
    number, unit = m.groups()
    return int(number) * _SIZE_UNITS[unit.lower()]


def test_dataset_upload_body_size_covers_the_app_pdf_cap() -> None:
    block = _location_block(_APP_SERVER, "/api/v1/datasets/")
    m = re.search(r"client_max_body_size\s+(\S+);", block)
    assert m, "no client_max_body_size override on the dataset-upload location"
    nginx_cap_bytes = _parse_nginx_size(m.group(1))
    assert nginx_cap_bytes >= MAX_SEED_PDF_BYTES, (
        f"nginx client_max_body_size on /api/v1/datasets/ is {nginx_cap_bytes} bytes, "
        f"which is below MAX_SEED_PDF_BYTES ({MAX_SEED_PDF_BYTES}) from "
        "ai_engine/data_gen/constants.py — a valid PDF upload the app accepts "
        "would be rejected by nginx before it ever reaches the app"
    )


# ---------------------------------------------------------------------------
# WebSocket location
# ---------------------------------------------------------------------------


def test_ws_location_upgrade_and_timeout() -> None:
    block = _location_block(_APP_SERVER, "/ws/")
    assert "proxy_http_version 1.1;" in block
    assert re.search(r"proxy_set_header\s+Upgrade\s+\$http_upgrade;", block)
    assert re.search(r"""proxy_set_header\s+Connection\s+["']upgrade["'];""", block)

    m = re.search(r"proxy_read_timeout\s+(\d+)s;", block)
    assert m, "no proxy_read_timeout on /ws/"
    assert int(m.group(1)) >= 3600, (
        f"/ws/ proxy_read_timeout is {m.group(1)}s — training/SDG jobs can idle "
        "longer than that between progress frames"
    )


# ---------------------------------------------------------------------------
# Rate-limit zone wiring — every route carries the zone(s) the top-of-file
# limit_req_zone/limit_conn_zone declarations exist to protect it with.
# ---------------------------------------------------------------------------

_EXPECTED_LOCATION_ZONES = [
    # The exact-match collection route must ride the general api zones, NOT
    # `uploads` — the SPA polls the list, and the uploads zone's burst=5
    # would 429 it. See the comment above that location in the conf.
    ("= /api/v1/datasets", ["api_read", "api_write"]),
    ("/api/v1/datasets/", ["uploads"]),
    ("/api/v1/inference/", ["inference"]),
    ("/api/", ["api_read", "api_write"]),
]


@pytest.mark.parametrize("selector,zones", _EXPECTED_LOCATION_ZONES, ids=[s for s, _ in _EXPECTED_LOCATION_ZONES])
def test_location_carries_expected_limit_req_zones(selector: str, zones: list[str]) -> None:
    block = _location_block(_APP_SERVER, selector)
    found = set(re.findall(r"limit_req\s+zone=(\w+)", block))
    missing = set(zones) - found
    assert not missing, f"location {selector!r} is missing limit_req zone(s) {sorted(missing)}"


def test_datasets_collection_has_exact_match_location() -> None:
    """GET /api/v1/datasets (the list route) must be proxied, not 301'd.

    nginx auto-redirects a request whose URI equals a slash-terminated proxy
    location's prefix minus the slash (301 to the slashed URI, built from the
    container's own listen port — so the public port is dropped). With only
    `location /api/v1/datasets/ { proxy_pass ... }` present, the collection
    route is exactly that URI and every list call bounces to
    `http://<host>/api/v1/datasets/` on the wrong port. Found live on
    2026-08-14 by the embedded SPA — the first client to list datasets
    through the edge. The exact-match location below is the fix; this guard
    keeps it from being "simplified" away.
    """
    # Non-vacuity: the slashed upload location this one protects against
    # must still exist — if it is ever renamed, this whole test needs a
    # fresh look rather than a silent pass.
    _location_block(_APP_SERVER, "/api/v1/datasets/")
    block = _location_block(_APP_SERVER, "= /api/v1/datasets")
    assert re.search(r"proxy_pass\s+http://api:8000;", block), (
        "the exact-match /api/v1/datasets location must proxy to the API"
    )


def test_health_location_has_no_rate_limit() -> None:
    block = _location_block(_APP_SERVER, "= /health")
    assert "limit_req" not in block, "/health must stay unlimited for uptime monitors"


def test_ws_location_carries_conn_limit() -> None:
    block = _location_block(_APP_SERVER, "/ws/")
    assert re.search(r"limit_conn\s+wsconn\s+\d+;", block), "/ws/ is missing limit_conn zone=wsconn"


def test_storage_server_carries_its_own_zone() -> None:
    assert re.search(r"limit_req\s+zone=storage\b", _STORAGE_SERVER)


def test_limit_req_and_conn_status_are_429() -> None:
    assert re.search(r"limit_req_status\s+429\s*;", _CONF_CODE)
    assert re.search(r"limit_conn_status\s+429\s*;", _CONF_CODE)


# ---------------------------------------------------------------------------
# Meta: the guards above must be blind to comments.
# ---------------------------------------------------------------------------


def test_the_forbidden_directive_guards_ignore_comments() -> None:
    """The "must NOT appear" guards run on `_strip_comments(...)` output, and
    this is what keeps them there.

    Written because the first draft of this file did NOT: it asserted
    `"$proxy_host" not in <raw storage server>` while the config carried the
    comment `# NOT $proxy_host (= minio:9000) — that breaks every signature`,
    so a fully correct config failed its own guard and the obvious "fix" was
    to delete the sentence explaining the hazard. Same class of bug as the
    /ready guard in WORKING_LOG.md that matched a comment instead of code.

    Asserting the helper works on a sample is not enough — that is an
    assertion sitting where it passes. So this checks the real file: every
    forbidden token IS present in the raw text (i.e. the comments genuinely
    do name them, so the guards would be false-positive without stripping)
    and is absent from the stripped text.
    """
    for token, raw, code in (
        ("$proxy_host", _CONF_TEXT, _CONF_CODE),
        ("rewrite", _CONF_TEXT, _CONF_CODE),
        ("$proxy_add_x_forwarded_for", _CONF_TEXT, _CONF_CODE),
    ):
        assert token in raw, (
            f"{token!r} no longer appears in any comment. If the explanatory "
            "comments were removed, restore them — they are why this file's "
            "forbidden-directive guards have to strip comments at all."
        )
        assert token not in code, (
            f"{token!r} survived comment stripping — it is a real directive "
            "in docker/edge.nginx.conf, not just prose about one"
        )


# ---------------------------------------------------------------------------
# `nginx -t` — text guards above cannot catch a missing semicolon or an
# otherwise-invalid-but-textually-plausible directive; this can.
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(shutil.which("docker") is None, reason="docker is not available")
def test_nginx_config_is_syntactically_valid() -> None:
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{_CONF_PATH}:/etc/nginx/conf.d/default.conf:ro",
            "nginx:1.27-alpine",
            "nginx",
            "-t",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"nginx -t failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ---------------------------------------------------------------------------
# The hostname seam, and the catch-all that makes a mismatch visible.
# ---------------------------------------------------------------------------


def _server_names(block: str) -> set[str]:
    m = re.search(r"server_name\s+([^;]+);", block)
    assert m, "block has no server_name"
    return {n.strip().strip('"') for n in m.group(1).split() if n.strip().strip('"')}


def test_an_unknown_host_is_rejected_rather_than_served_the_spa() -> None:
    """The storage hostname lives in three unlinked places: this file's
    `server_name`, `docker/cloudflared/config.yml`'s `hostname:`, and
    `MINIO_PUBLIC_URL` in the environment. Nothing can statically tie the
    third one, so the design has to make a mismatch loud instead of relying
    on all three being typed identically.

    Without a `default_server`, nginx promotes the FIRST server block, and
    the app block answers `try_files $uri /index.html` — so a misrouted
    presigned fetch returns **200 with an HTML page**, and the browser saves
    `index.html` under the name `model.gguf`. A 4xx is recoverable; a
    self-consistent-looking 200 is the one that wastes an afternoon.
    """
    assert re.search(r"listen\s+80\s+default_server\s*;", _CATCHALL_SERVER), (
        "no server block is marked default_server, so nginx will use the "
        "first one (the app) and answer unknown Hosts with the SPA"
    )
    assert re.search(r"return\s+4\d\d", _CATCHALL_SERVER), (
        "the catch-all does not return a 4xx — it must reject, not serve"
    )
    assert "try_files" not in _CATCHALL_SERVER
    assert "proxy_pass" not in _CATCHALL_SERVER

    # And the two real vhosts must NOT claim default_server themselves,
    # which would put the catch-all back to never matching.
    for name, block in (("app", _APP_SERVER), ("storage", _STORAGE_SERVER)):
        assert "default_server" not in block, f"the {name} vhost claims default_server"


def test_the_edge_and_the_tunnel_agree_on_every_hostname() -> None:
    """The two in-repo halves of the seam. cloudflared forwards only the
    hostnames listed in its ingress rules, and nginx serves only the ones in
    a `server_name`; a value in one and not the other is a route that 421s
    (or, before the catch-all existed, silently returned the SPA).

    `localhost`/`127.0.0.1`/`edge` are local-only names that deliberately
    have no tunnel route, so the comparison is one-directional: every
    tunnel hostname must be served, not every served name must be tunnelled.
    """
    cf = yaml.safe_load(
        (_CONF_PATH.parent / "cloudflared" / "config.yml").read_text(encoding="utf-8")
    )
    tunnel_hosts = {r["hostname"] for r in cf["ingress"] if "hostname" in r}
    served = _server_names(_APP_SERVER) | _server_names(_STORAGE_SERVER)

    unserved = tunnel_hosts - served
    assert not unserved, (
        f"cloudflared routes {sorted(unserved)} to the edge, but no server_name "
        "in docker/edge.nginx.conf matches — those requests hit the catch-all "
        "and 421."
    )


_LOCAL_NAMES = {"localhost", "127.0.0.1", "edge"}


def test_the_app_vhost_still_answers_the_local_names() -> None:
    """These three became load-bearing the moment the catch-all landed.

    Before it existed, dropping them was harmless — nginx fell back to the
    only server block. Now an unmatched Host gets 421, so removing any of
    them breaks:

      * `scripts/deploy_pasaflow_vm.sh`'s Phase 7 sanity check, which curls
        `http://localhost:${EDGE_PORT}/health` and `fail`s the whole deploy
        on a non-200;
      * the Phase 8 banner's loopback debugging recipes;
      * in-network probes addressing the container as `http://edge/...`.

    The requirement was written into the docstring of the test above and
    encoded nowhere, which is how it survived a mutation that stripped all
    three and left the suite green. nginx strips the port before matching
    `server_name`, so `Host: localhost:8088` from the EDGE_PORT curl is
    covered by the bare `localhost` entry.
    """
    missing = _LOCAL_NAMES - _server_names(_APP_SERVER)
    assert not missing, (
        f"the app vhost no longer answers {sorted(missing)}. With a "
        "default_server catch-all in place those Hosts now get 421, which "
        "fails the deploy script's own health check and looks like an outage."
    )


def test_the_storage_vhost_has_its_own_dedicated_hostname() -> None:
    """SigV4 signs the Host header, so storage cannot share a hostname with
    the app: the two vhosts must be selectable by Host alone."""
    app_names = _server_names(_APP_SERVER)
    storage_names = _server_names(_STORAGE_SERVER)
    assert storage_names, "the storage vhost has no server_name"
    assert not (app_names & storage_names), (
        f"app and storage vhosts share hostname(s) {sorted(app_names & storage_names)} — "
        "nginx would resolve them by block order, not intent"
    )


# ---------------------------------------------------------------------------
# Plan decision #12: the interactive API docs are not exposed publicly.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/ready", "/metrics"])
def test_the_api_docs_and_ready_are_not_proxied(path: str) -> None:
    """`api/main.py` serves these without auth. Under a public-internet
    threat model the spec is a map of every endpoint including the ones just
    added, so decision #12 keeps them off the edge — reachable via
    `docker compose exec` and from the committed `openapi.json` instead.
    `/metrics` (new this round) is internal-only for the same reason: it is
    unauthenticated and exposes internal counters to anyone on the public
    internet.

    This existed only as a comment in the conf. A comment does not fail CI
    when someone adds `location /docs { proxy_pass http://api:8000; }`,
    which is a one-line change that looks helpful.
    """
    for selector, block in _app_proxy_locations():
        assert not selector.rstrip("/").endswith(path.rstrip("/")), (
            f"{path} is proxied through the edge (location {selector!r}). "
            "Decision #12 keeps the interactive docs, /ready, and /metrics "
            "off the public surface; if that changed, update ADR-011 and "
            "this test together."
        )
