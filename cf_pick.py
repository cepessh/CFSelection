#!/usr/bin/env python3
import argparse, time, random, sys, json, socket
from datetime import datetime, timezone
import requests
from http.cookiejar import MozillaCookieJar

# Defaults (overridable via config)
API_HOSTS = ["https://codeforces.com/api", "https://www.codeforces.com/api"]
TIMEOUT = 45          # seconds
PAGE_SIZE = 500       # 100..1000
RATE_MIN_INTERVAL = 2.2
_LAST_CALL_T = 0.0

S = requests.Session()
S.headers.update({
    "User-Agent": "cf-picker/1.4 (+no-key-required)",
    "Accept": "application/json",
})

def _throttle(verbose=False):
    global _LAST_CALL_T
    now = time.monotonic()
    wait = RATE_MIN_INTERVAL - (now - _LAST_CALL_T)
    if wait > 0:
        if verbose:
            print(f"[throttle] sleeping {wait:.2f}s", file=sys.stderr)
        time.sleep(wait)
    _LAST_CALL_T = time.monotonic()

def die(msg: str, code: int = 1):
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)

def prefer_ipv4():
    orig_getaddrinfo = socket.getaddrinfo
    def gai(host, port, family=0, type=0, proto=0, flags=0):
        res = orig_getaddrinfo(host, port, family, type, proto, flags)
        return [x for x in res if x[0] == socket.AF_INET] or res
    socket.getaddrinfo = gai

def attach_cookie_file(path, verbose=False):
    jar = MozillaCookieJar()
    jar.load(path, ignore_discard=True, ignore_expires=True)
    S.cookies.update(jar)
    if verbose:
        print(f"[cookies] loaded {len(jar)} cookies", file=sys.stderr)

def _looks_like_html(text: str) -> bool:
    t = (text or "").lstrip().lower()
    return t.startswith("<!doctype html") or t.startswith("<html")

def cf_get(path, params=None, retries=4, backoff=0.5, timeout=None, verbose=False):
    """GET wrapper: global throttle, multi-host retry, WAF/HTML detection."""
    if timeout is None:
        timeout = TIMEOUT
    last_err = None
    for i in range(retries):
        _throttle(verbose=verbose)
        for base in API_HOSTS:
            url = f"{base}/{path}"
            try:
                r = S.get(url, params=params, timeout=timeout, allow_redirects=True)
                ctype = (r.headers.get("content-type") or "").lower()
                if "application/json" not in ctype and _looks_like_html(r.text):
                    raise RuntimeError("Non-JSON HTML from CF (likely WAF/challenge).")
                j = r.json()
                if j.get("status") == "OK":
                    return j["result"]
                comment = (j.get("comment") or "").strip()
                transient = any(x in comment.lower() for x in (
                    "limit exceeded", "service unavailable", "please try again later"
                ))
                if not transient:
                    raise RuntimeError(f"{path}: {comment or 'FAILED'}")
            except (requests.RequestException, json.JSONDecodeError, RuntimeError) as e:
                last_err = e
                if verbose:
                    print(f"[cf_get] {path} host={base} try {i+1}/{retries}: {e}", file=sys.stderr)
                continue
        time.sleep(backoff * (2 ** i))
    raise RuntimeError(f"{path}: exhausted retries; last error: {last_err}")

def load_user_attempted(handles, verbose=False, max_pages_per_user=None):
    """Set of (contestId, index) with any submission by ANY given user."""
    attempted = set()
    for h in handles:
        if verbose:
            print(f"[user.status] {h}", file=sys.stderr)
        start = 1
        page = 0
        while True:
            page += 1
            if verbose:
                print(f"[user.status] {h} page={page} from={start}", file=sys.stderr)
            try:
                batch = cf_get("user.status", {
                    "handle": h, "from": start, "count": PAGE_SIZE
                }, verbose=verbose)
            except Exception as e:
                msg = str(e)
                if "not found" in msg.lower() or "handles:" in msg.lower():
                    die(f"Handle '{h}' is invalid: {msg}")
                raise
            if not batch:
                break
            for sub in batch:
                p = sub.get("problem", {})
                cid, idx = p.get("contestId"), p.get("index")
                if cid and idx:
                    attempted.add((cid, idx))
            if len(batch) < PAGE_SIZE:
                break
            if max_pages_per_user and page >= max_pages_per_user:
                if verbose:
                    print(f"[user.status] {h} reached max_pages_per_user={max_pages_per_user}", file=sys.stderr)
                break
            start += PAGE_SIZE
            time.sleep(0.2)  # small courtesy pause
    return attempted

def load_contests_meta(verbose=False):
    """Return {contestId: {'year': int, 'name': str}} for non-gym contests."""
    contests = cf_get("contest.list", {"gym": "false"}, verbose=verbose)
    meta = {}
    for c in contests:
        if c.get("gym"):
            continue
        cid = c.get("id")
        ts = c.get("startTimeSeconds")
        name = c.get("name") or ""
        if cid and ts:
            meta[cid] = {
                "year": datetime.fromtimestamp(ts, tz=timezone.utc).year,
                "name": name
            }
    return meta

def load_problemset_filtered(ratings_set, year_min, year_max,
                             exclude_name_patterns=None, exclude_contest_ids=None,
                             verbose=False):
    """
    Keep problems with rating in set AND year_min ≤ year ≤ year_max,
    excluding contests by name pattern (case-insensitive) or explicit IDs.
    """
    ps = cf_get("problemset.problems", verbose=verbose)
    problems = ps["problems"]
    meta = load_contests_meta(verbose=verbose)

    # Build exclusion set
    excl_ids = set(int(x) for x in (exclude_contest_ids or []))
    pats = [p.lower() for p in (exclude_name_patterns or []) if p]

    if pats:
        for cid, m in meta.items():
            name_lc = (m.get("name") or "").lower()
            if any(p in name_lc for p in pats):
                excl_ids.add(cid)
        if verbose:
            print(f"[filter] exclude by name patterns {pats}: {len(excl_ids)} contests", file=sys.stderr)

    out = []
    for p in problems:
        cid, idx, rating = p.get("contestId"), p.get("index"), p.get("rating")
        if not cid or not idx or rating is None:
            continue
        if rating not in ratings_set:
            continue
        m = meta.get(cid)
        if not m:
            continue
        if m["year"] < year_min or m["year"] > year_max:
            continue
        if cid in excl_ids:
            continue
        out.append(p)
    return out

def pick_strict_order(
    candidates, attempted_set, ratings_ordered,
    distinct_contest=False, distinct_tags=False, tag_caps=None, seed=None
):
    """
    One problem per rating (order preserved), unseen by any handle.
    - distinct_contest: forbid same contest twice
    - distinct_tags: forbid reusing *any* tag (strict: cap=1 on all tags)
    - tag_caps: dict {tag_lower: max_allowed_occurrences} (e.g., {"strings": 2})
    """
    if seed is not None:
        random.seed(seed)

    tag_caps = {str(k).lower(): int(v) for (k, v) in (tag_caps or {}).items() if int(v) >= 1}

    fresh = [p for p in candidates if (p["contestId"], p["index"]) not in attempted_set]
    buckets = {}
    for p in fresh:
        buckets.setdefault(p["rating"], []).append(p)
    for b in buckets.values():
        random.shuffle(b)

    picked = []
    used_keys = set()
    used_contests = set()
    tag_counts = {}  # lower_tag -> count so far

    def violates_tag_rules(tags_lower):
        if distinct_tags and any(tag_counts.get(t, 0) >= 1 for t in tags_lower):
            return True
        for t in tags_lower:
            cap = tag_caps.get(t)
            if cap is not None and tag_counts.get(t, 0) >= cap:
                return True
        return False

    for r in ratings_ordered:
        pool = buckets.get(r, [])
        chosen = None
        while pool:
            cand = pool.pop()
            key = (cand["contestId"], cand["index"])
            if key in used_keys:
                continue
            if distinct_contest and cand["contestId"] in used_contests:
                continue
            tags_lower = set(map(str.lower, cand.get("tags", [])))
            if violates_tag_rules(tags_lower):
                continue
            chosen = cand
            used_keys.add(key)
            if distinct_contest:
                used_contests.add(cand["contestId"])
            for t in tags_lower:
                tag_counts[t] = tag_counts.get(t, 0) + 1
            break
        if chosen is None:
            detail = []
            if distinct_contest: detail.append("distinct contest")
            if distinct_tags: detail.append("no tag repetition")
            if tag_caps: detail.append(f"tag caps={tag_caps}")
            raise RuntimeError(f"No available problem for rating {r} under constraints: {', '.join(detail) or 'none'}.")
        picked.append(chosen)
    return picked

def to_url(p):
    return f"https://codeforces.com/problemset/problem/{p['contestId']}/{p['index']}"

def load_config(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        die(f"Failed to read config '{path}': {e}", 2)

    # Required
    handles = cfg.get("handles")
    ratings_list = cfg.get("ratings")
    year_min = cfg.get("year_min")
    year_max = cfg.get("year_max")
    if not isinstance(handles, list) or not handles:
        die("Config: 'handles' must be a non-empty list.", 2)
    if not isinstance(ratings_list, list) or not ratings_list:
        die("Config: 'ratings' must be a non-empty list of ints.", 2)
    if not isinstance(year_min, int) or not isinstance(year_max, int):
        die("Config: 'year_min' and 'year_max' must be integers.", 2)
    if year_min > year_max:
        die("Config: 'year_min' cannot be greater than 'year_max'.", 2)

    # Optional
    distinct_contest = bool(cfg.get("distinct_contest", False))
    distinct_tags = bool(cfg.get("distinct_tags", False))
    tag_caps = cfg.get("tag_caps", {})
    seed = cfg.get("seed", None)
    prefer_v4 = bool(cfg.get("prefer_ipv4", False))
    cookie_file = cfg.get("cookie_file")
    min_interval = float(cfg.get("min_interval", RATE_MIN_INTERVAL))
    verbose = bool(cfg.get("verbose", False))
    user_agent = cfg.get("user_agent")
    timeout = int(cfg.get("timeout", TIMEOUT))
    page_size = int(cfg.get("page_size", PAGE_SIZE))
    api_hosts = cfg.get("api_hosts")
    max_pages_per_user = cfg.get("max_pages_per_user", None)

    # NEW: contest exclusions
    exclude_name_patterns = cfg.get("exclude_contest_name_patterns", [])
    exclude_contest_ids = cfg.get("exclude_contest_ids", [])

    if user_agent:
        S.headers.update({"User-Agent": user_agent})

    # validate tag_caps
    if not isinstance(tag_caps, dict):
        die("Config: 'tag_caps' must be an object/dict of {tag: max_allowed}.", 2)
    _caps = {}
    for k, v in tag_caps.items():
        try:
            cap = int(v)
        except Exception:
            die(f"Config: tag_caps['{k}'] must be an integer >= 1.", 2)
        if cap < 1:
            die(f"Config: tag_caps['{k}'] must be >= 1.", 2)
        _caps[str(k).lower()] = cap

    if max_pages_per_user is not None:
        try:
            max_pages_per_user = int(max_pages_per_user)
            if max_pages_per_user < 1:
                die("Config: 'max_pages_per_user' must be >= 1.", 2)
        except Exception:
            die("Config: 'max_pages_per_user' must be an integer.", 2)

    # validate exclusions
    if not isinstance(exclude_name_patterns, list):
        die("Config: 'exclude_contest_name_patterns' must be a list of strings.", 2)
    _pats = [str(s).strip() for s in exclude_name_patterns if str(s).strip()]

    if not isinstance(exclude_contest_ids, list):
        die("Config: 'exclude_contest_ids' must be a list of integers.", 2)
    _ids = []
    for x in exclude_contest_ids:
        try: _ids.append(int(x))
        except Exception: die("Config: 'exclude_contest_ids' must contain integers.", 2)

    return {
        "handles": [str(h).strip() for h in handles if str(h).strip()],
        "ratings_list": [int(x) for x in ratings_list],
        "year_min": year_min,
        "year_max": year_max,
        "distinct_contest": distinct_contest,
        "distinct_tags": distinct_tags,
        "tag_caps": _caps,
        "seed": seed,
        "prefer_ipv4": prefer_v4,
        "cookie_file": cookie_file,
        "min_interval": max(0.0, min_interval),
        "verbose": verbose,
        "timeout": max(5, timeout),
        "page_size": max(100, min(1000, page_size)),
        "api_hosts": api_hosts if isinstance(api_hosts, list) and api_hosts else None,
        "max_pages_per_user": max_pages_per_user,
        "exclude_name_patterns": _pats,
        "exclude_contest_ids": _ids
    }

def main():
    global RATE_MIN_INTERVAL, TIMEOUT, API_HOSTS, PAGE_SIZE
    ap = argparse.ArgumentParser(description="Pick CF problems unseen by given users, one per rating (config-driven).")
    ap.add_argument("--config", default="cf_pick.json", help="Path to JSON config (default: cf_pick.json)")
    args = ap.parse_args()

    cfg = load_config(args.config)

    if cfg["prefer_ipv4"]:
        prefer_ipv4()
    if cfg["cookie_file"]:
        attach_cookie_file(cfg["cookie_file"], verbose=cfg["verbose"])

    RATE_MIN_INTERVAL = cfg["min_interval"]
    TIMEOUT = cfg["timeout"]
    PAGE_SIZE = cfg["page_size"]
    if cfg["api_hosts"]:
        API_HOSTS = cfg["api_hosts"]

    handles = cfg["handles"]
    ratings_list = cfg["ratings_list"]

    try:
        attempted = load_user_attempted(
            handles,
            verbose=cfg["verbose"],
            max_pages_per_user=cfg["max_pages_per_user"]
        )
        candidates = load_problemset_filtered(
            set(ratings_list),
            cfg["year_min"], cfg["year_max"],
            exclude_name_patterns=cfg["exclude_name_patterns"],
            exclude_contest_ids=cfg["exclude_contest_ids"],
            verbose=cfg["verbose"]
        )
        picked = pick_strict_order(
            candidates, attempted, ratings_list,
            distinct_contest=cfg["distinct_contest"],
            distinct_tags=cfg["distinct_tags"],
            tag_caps=cfg["tag_caps"],
            seed=cfg["seed"]
        )
    except Exception as e:
        die(str(e))

    print(f"Selected {len(picked)} problem(s):")
    for r, p in zip(ratings_list, picked):
        print(f"- [{r}] {p['contestId']}{p['index']} — {p['name']} — {to_url(p)}")

if __name__ == "__main__":
    main()

