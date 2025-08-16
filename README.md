# CFSelection

Pick **unseen** Codeforces problems for your local contest.

- One problem per requested rating (order preserved).
- Excludes problems with **any submission** by any provided user.
- Inclusive year filter: `year_min ≤ year ≤ year_max`.
- Contest exclusion by **name pattern** or **ID** (e.g., exclude Kotlin rounds).
- Tag controls: `distinct_tags` or fine-grained `tag_caps` (e.g., `"strings": 2`).
- Resilient: global throttle, multi-host retries, IPv4 option, cookies/UA support.
- No API key required.

## Files

```
cf_pick.py     # the script
cf_pick.json   # configuration (edit this)
```

## Requirements

- Python 3.8+
- `requests` → `pip install --upgrade requests`

## Usage

1) Edit `cf_pick.json` (see example below).
2) Run:
```bash
python cf_pick.py --config cf_pick.json
```

Output:
```
Selected N problem(s):
- [1100] 1221B — Knights — https://codeforces.com/problemset/problem/1221/B
...
```

## Config (`cf_pick.json`)

### Required
- `handles`: list of CF handles (strings)
- `ratings`: list of ints (length = number of problems; order preserved)
- `year_min`, `year_max`: inclusive bounds on contest year

### Optional (common)
- `distinct_contest` (bool): forbid same contest twice
- `distinct_tags` (bool): forbid **any** tag repetition
- `tag_caps` (object): per-tag caps, e.g. `{ "strings": 2 }`
- `exclude_contest_name_patterns` (list): case-insensitive substrings, e.g. `["kotlin"]`
- `exclude_contest_ids` (list): explicit contest IDs to skip
- `seed` (int): deterministic RNG
- `prefer_ipv4` (bool), `cookie_file` (path to Netscape `cookies.txt`), `user_agent` (string)
- `verbose` (bool)

### Reliability / Performance
- `min_interval` (float): global throttle between calls (default 2.2s)
- `timeout` (sec): per-request timeout (default 45)
- `page_size` (100..1000): status page size (use 1000 to reduce calls)
- `max_pages_per_user` (int): cap for very large accounts
- `api_hosts` (list): API bases to try

### Example
```json
{
  "handles": ["alice", "bob"],
  "ratings": [1000, 1100, 1200, 1300, 1400, 1500],
  "year_min": 2015,
  "year_max": 2022,

  "distinct_contest": false,
  "distinct_tags": false,
  "tag_caps": { "strings": 2 },

  "exclude_contest_name_patterns": ["kotlin"],
  "exclude_contest_ids": [],

  "seed": 7,
  "prefer_ipv4": true,
  "cookie_file": "./cookies.txt",
  "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",

  "min_interval": 2.2,
  "timeout": 45,
  "page_size": 1000,
  "max_pages_per_user": 20,
  "api_hosts": ["https://codeforces.com/api", "https://www.codeforces.com/api"],
  "verbose": true
}
```

## Cookies (optional)
Only needed if your IP gets challenged. Export **Netscape** cookies for `codeforces.com` from your browser, save as `./cookies.txt`, and set both `cookie_file` and `user_agent` in the config.

---

## Troubleshooting (short)
- Stuck at `page=1`: waiting on server; it will retry after `timeout`. Lower `timeout`, set `page_size: 1000`, keep `verbose: true`.
- HTML/404 from API (even in browser): upstream/edge issue. Try later or another network; cookies/UA may help.
- Invalid handle: script exits with a clear message.
- Too many “strings” tasks: set `tag_caps: {"strings": 2}` (or another ceiling).

---
