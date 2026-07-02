# YouTube source-acquisition preflight

This file is a diagnostic recipe for the failure modes that block YouTube downloads in restricted-network or out-of-date environments. Read it before debugging from scratch — the past failures here are repeatable and the fixes are concrete.

The recipe assumes the host has yt-dlp and a Python venv reachable. It is environment-specific by nature; do not bake user-specific paths or proxy addresses into general SKILL.md text.

## Three independent gates

A YouTube source acquisition failure is almost always one of these three gates, and they fail with characteristic symptoms. Diagnose them in the order below, because gate 1 makes gate 2 untestable, and gate 2 makes the storyboards-only situation in gate 3 hard to interpret.

### Gate 1: Network egress

Symptoms (any of):

- `tls handshake eof`
- `[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol`
- `HTTPSConnectionPool(host='www.youtube.com', port=443): Read timed out`
- The same SSL/EOF error against `files.pythonhosted.org` when `uv pip install` runs

Cause: direct egress to YouTube and PyPI is unreliable from the current host (common in CN dev environments behind a local Clash/Mihomo/V2Ray proxy).

Fix: route through the local HTTP proxy. Apply it everywhere — both yt-dlp invocations and `uv pip install`:

```
export HTTPS_PROXY=http://127.0.0.1:7897
export HTTP_PROXY=http://127.0.0.1:7897
# yt-dlp also wants the explicit flag
python -m yt_dlp --proxy http://127.0.0.1:7897 ...
```

If the proxy address is unknown, ask the user once rather than retrying without it. The proxy address is host-specific; do not hardcode it in the skill, but a memory entry for a returning user is appropriate.

If the proxy itself is unreachable, do not retry the same egress in a sleep loop — escalate to the user.

### Gate 2: yt-dlp version + EJS plugin

Symptoms (after gate 1 is fixed):

- `--list-formats` returns only `sb0..sb3` storyboards and no real video formats
- `WARNING: [youtube] ...: n challenge solving failed: Some formats may be missing. Ensure you have a supported JavaScript runtime and challenge solver script distribution installed.`
- `WARNING: Only images are available for download.`

Cause: YouTube wraps signature and `n` parameters in obfuscated JavaScript. Modern yt-dlp delegates that to the `yt-dlp-ejs` Python plugin, which in turn calls out to a JS runtime. Three things must align:

1. **yt-dlp version** must be nightly **2026.04 or later**. The stable 2026.03.17 predates the EJS hooks and will keep failing even with the plugin installed. Upgrade with:

   ```
   uv pip install --python <venv-python> --pre -U yt-dlp
   ```

   (Run this through the proxy too if you are behind one.)

2. **`yt-dlp-ejs` package** must be installed into the **same Python env as yt-dlp**:

   ```
   uv pip install --python <venv-python> yt-dlp-ejs
   ```

   You can confirm it loaded by looking for `yt_dlp_ejs-X.Y.Z` in the `[debug] Optional libraries:` line of `python -m yt_dlp -v ...`.

3. **A working JS runtime** must be on PATH and yt-dlp must be told to use it. yt-dlp accepts `bun`, `deno`, `node`, or `quickjs`. Important caveats from observed runs:

   - **Node 18 is reported as "unsupported"** by yt-dlp's runtime check. If only Node 18 is present, prefer bun.
   - **Bun ≥ 1.3** works on Windows out of the box.
   - Pass it explicitly with `--js-runtimes bun` (or `deno` / `node` if you have ≥ 20).

   Confirm by looking for this in `-v` output:

   ```
   [debug] [youtube] [jsc] JS Challenge Providers: bun, deno (unavailable), node (unavailable), ...
   [youtube] [jsc:bun] Solving JS challenges using bun
   ```

When all three align, `--list-formats` will list mp4/webm formats up to 1080p60.

### Gate 3: Plugin discovery — do not use the standalone exe

Symptoms: gate 2 plugin and runtime are installed but the n-challenge warning still fires.

Cause: a standalone `yt-dlp.exe` does **not** auto-load Python packages from any venv. `yt-dlp-ejs` is a Python plugin, so it has no effect on the standalone binary.

Fix: drive yt-dlp through the venv, not the exe:

```
<venv>/Scripts/python.exe -m yt_dlp --proxy ... --js-runtimes bun ...
```

If you only have the standalone exe, you must instead point it at an `~/.config/yt-dlp/jsinterp/` script distribution per the upstream wiki — but the venv route is far simpler when a venv with `yt-dlp-ejs` already exists.

## A working command, end to end

For the verified environment (Windows + uv venv + bun 1.3 + Clash on 7897), this single line lists formats successfully:

```
HTTPS_PROXY=http://127.0.0.1:7897 \
<venv-python> -m yt_dlp \
  --proxy http://127.0.0.1:7897 \
  --cookies <cookies.txt> \
  --js-runtimes bun \
  --list-formats <youtube-url>
```

For actual download, replace `--list-formats` with `-f "bestvideo[height<=1080]+bestaudio/best" --merge-output-format mp4 -o <output-template>` and add `--write-info-json --write-auto-subs --write-subs --sub-langs "<preferred>,en"` to capture metadata, manual subs, and auto-subs in one go.

## What not to do

These are dead ends observed in the failed run; do not repeat them:

- Cycling through `--extractor-args "youtube:player_client=..."` permutations (web, android, ios, mweb, tv_simply, web_safari, mediaconnect). The n-challenge is independent of the player client; rotating clients does not help.
- Adding `--no-check-certificate` or `--legacy-server-connect`. The SSL errors here are caused by network egress, not certificate validation.
- Retrying the same failing egress repeatedly in hopes of a transient fix. SSL EOF + read-timeout against `youtube.com` consistently means the egress route is wrong, not flaky.
- Trusting that an existing venv yt-dlp is recent enough. The version stamped at `__version__` may lag months behind nightly even if `yt-dlp.exe -U` shows a newer build — these are independent installs.

## Probing without losing the main agent's context

When the main agent is responsible for planning and writing, do not let it absorb the trial-and-error of preflight. Spawn a dedicated preflight subagent whose only job is to:

1. Run the three-gate diagnosis above.
2. Report back the exact working command line, including which proxy, which Python interpreter, which JS runtime, and which yt-dlp version succeeded.
3. Attach the captured `info.json`, downloaded subtitle files, and downloaded cover image.

The main agent then uses the returned command line for any further yt-dlp calls (cover thumb at higher resolution, alternate subtitle language, etc.) and never needs to re-derive the recipe.
