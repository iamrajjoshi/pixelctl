# pixelctl

A local-first CLI and extensible runtime for pixel clocks. **Ulanzi TC002 is the first supported device.** The CLI, page/provider plugins, and themes are independent of any company or account.

The native TC002 receiver owns the LEDs, knob, buttons, rotation, and cached frames. A compact device-side worker fetches GitHub, Vercel, and API health data directly over verified HTTPS and renders 17 built-in pages. The clock can run with your computer off. A host collector remains available for Python page/provider plugins. No desktop UI or vendor cloud account is required.

## Status

This is an early hardware-tested release, not a universal firmware installer. The native display, physical controls, standalone rendering, and direct verified HTTPS have been tested on a TC002. Both native and Python providers support GitHub PRs and contributions, Vercel production deployments, and strict HTTP API health checks.

The TC002 has approximately 36 MB of RAM, so standalone mode uses compact C providers/renderers rather than installing Python on the device. Built-in pages and configuration-only themes work in standalone mode. Arbitrary Python plugins run in host mode. Cloud tokens are explicitly provisioned into private device files; desktop login stores are never copied automatically.

## Install the CLI

Python 3.11 or later is required. From a source checkout:

```sh
git clone https://github.com/iamrajjoshi/pixelctl.git
cd pixelctl
uv tool install .
pixelctl --help
pixelctl init --device 192.0.2.10
pixelctl plugins list
```

The address above is a documentation placeholder. Use your clock's LAN address.

The receiver must be installed separately before `status`, `page`, or `run` can communicate with the clock. See [TC002 runtime and recovery](native/README.md). Native source, build scripts, and tests live in `native/`; no vendor firmware image, account token, pairing key, company asset, or personal configuration is included in this repository.

## Configure pages and providers

The default configuration lives outside the checkout: `~/Library/Application Support/Pixelctl/config.json` on macOS, or `$XDG_STATE_HOME/pixelctl/config.json` (default `~/.local/state/pixelctl/config.json`) on Linux. `PIXELCTL_HOME` overrides this location.

Start with [examples/minimal.json](examples/minimal.json), or [examples/developer.json](examples/developer.json) for GitHub, Vercel, and API checks. Host mode reads the existing `gh` and Vercel CLI login stores. Standalone mode uses separate tokens provisioned below. No credentials are embedded in examples.

```sh
pixelctl --config /path/to/config.json collect
pixelctl --config /path/to/config.json render --output preview
pixelctl --config /path/to/config.json run
pixelctl status
pixelctl page prs
pixelctl pause
```

`pause` toggles automatic rotation. On the clock, turn the knob to change pages and press it to pause/resume rotation. Left/right select pages, and the middle button returns to the clock.

Built-in pages:

| Page | Data |
| --- | --- |
| `clock` | Local time and date, with a configurable brand mark |
| `prs` | Token-visible authored open PRs in the configured scope, with draft/review/CI states |
| `garden` | Nineteen weeks of the GitHub contribution calendar |
| `deploy` | Latest production deployment and separately verified serving alias |
| `health` | Strict endpoint health predicate and observed response latency |
| `uptime` | Success percentage of observed API checks, with its measurement window |
| `identity` | Configurable mark and animated pixel wordmark |
| `date` | Local weekday and calendar date |
| `dayprogress` | Local wall-clock day progress |
| `weekprogress` | Local week progress, Monday first |
| `yearprogress` | Calendar-year progress, including leap years |
| `worldclock` | A labeled clock at an explicitly fixed UTC offset |
| `text` | Configured label and scrolling text |
| `countdown` | Time until a configured target |
| `system` | Local device uptime and free/available RAM |
| `network` | The device's local IPv4 address |
| `binaryclock` | Hours, minutes, and seconds as binary columns |

Deployment `LIVE` requires a successful serving deployment matching the production alias. Otherwise a successful build is only `READY`. API transport errors are unknown rather than asserted production outages. Availability excludes unknown checks and unobserved gaps; it is not a historical service SLO. Stale data is marked explicitly. Cached pages stay available when the collector disconnects, and the clock continues keeping time locally.

## Standalone setup

Configure Wi-Fi first, install the native receiver, and edit a config based on `examples/developer.json`. Keep the pairing key and recovery backup outside the repository. Create separate tokens rather than reusing a desktop CLI login:

- **GitHub:** a fine-grained token restricted to the needed repositories, with read access for Pull requests, Contents, Actions, Commit statuses, and Deployments. Metadata access is automatic. Only repositories visible to that token can appear; include every repository you want monitored. Organization approval may be required. Fine-grained tokens do not expose the Checks REST API; when GraphQL check-rollup access is denied, pixelctl retries PR metadata without it and shows CI as unknown.
- **Vercel:** an access token restricted to the needed project. Project restriction is not a promise of read-only privileges; pixelctl only performs read requests. Team and full-account tokens are not required by the device.

The stock TC002 firmware exposes unauthenticated root ADB on TCP port 5555.
Other devices on the same reachable network can read provider token files despite
private file permissions. Use a trusted, isolated network and narrowly scoped,
short-lived tokens; permissions on the token files do not secure this ADB service.
The current setup/update workflow uses it, so pixelctl leaves ADB enabled.

Use hidden terminal input; do not put tokens into commands, shell history, config JSON, or source control:

```sh
pixelctl credentials set github
pixelctl credentials set vercel
pixelctl credentials status
pixelctl standalone deploy --adb /path/to/adb
pixelctl standalone status --adb /path/to/adb
```

Deployment copies explicitly configured tokens into private files on the clock, installs the standalone worker, exports branding as raw pixel frames, and restarts the existing boot hook without reflashing `res`. On macOS, it disables and archives the host LaunchAgent to prevent competing renderers, including at the next login. Missing required credentials stop deployment before device changes. Health-only configurations need no cloud tokens.

Standalone health and cloud refreshes run independently, so a slow GitHub/Vercel request cannot block health sampling or the display. Availability counters persist on the clock and represent observations made there; they are separate from a previous host monitor's history. Token expiration or permission errors are shown as unavailable data. Replace expired tokens with `credentials set` and deploy again.

Supported extra settings:

```json
{
  "settings": {
    "text_label": "NOTE",
    "text": "HELLO WORLD",
    "world_offset_minutes": 0,
    "world_label": "UTC",
    "countdown_label": "NEW YEAR",
    "countdown_target": "2027-01-01T00:00:00Z"
  }
}
```

World-clock offsets are fixed and do not automatically follow daylight saving changes. The main clock uses its configured timezone rules. Countdown timestamps are exported to an epoch value for the embedded runtime. Device/system/network pages report the machine doing the rendering; host previews therefore show host statistics or unavailable data where that host lacks Linux `/proc` data.

## Plugins and themes

A plugin is ordinary Python code with a `register(registry)` function. Load a local file explicitly in configuration:

```json
{
  "plugins": ["./my_plugin.py"],
  "theme": "mint",
  "pages": ["clock", "heartbeat", "identity"],
  "providers": []
}
```

[examples/sample_plugin.py](examples/sample_plugin.py) demonstrates a separate theme and page. Plugins may register:

```python
def register(registry):
    registry.register_theme("mint", {
        "name": "My team", "wordmark": "HELLO", "accent": "#78DCB4"
    })
    registry.register_page("my_page", render_page)
    registry.register_provider("my_source", collect_data,
                               interval=120, sections=("my_data",))
    registry.register_preset("my_setup", {"theme": "mint"})
```

Page functions receive `(data, now, phase)` and return a Pillow RGB image exactly 52 × 16 pixels. Providers receive `(config, previous_state)` and return named data sections, each with `updated_at` and an optional `error`. Providers run independently from display updates. Installed packages can register the same function in the `pixelctl.plugins` entry-point group and be selected by name.

Themes support `name`, `wordmark`, `mark_path` (transparent PNG), `accent`, `foreground`, and `secondary`. Configuration uses an IANA `timezone` for host rendering and `timezone_posix` for the embedded fallback clock. Company themes and user presets can remain in a completely separate plugin directory or repository. Only explicitly configured plugins execute; treat plugin code as code you install on your own machine.

## Background collection

On macOS, install the CLI in a persistent environment and run:

```sh
pixelctl service install
pixelctl service status
pixelctl service restart
pixelctl service stop
pixelctl service start
```

The per-user LaunchAgent starts at login in host mode. Standalone deployment disables it. Local plugin files and sibling assets are copied into the private application-support directory. Linux users can run `pixelctl run` under their normal process manager; automatic Linux service installation is not included.

## Development

```sh
uv venv --python 3.12
uv pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

Native protocol and hardware simulation tests are documented in `native/README.md`. HTTP writes require a device pairing key. Never expose the LAN receiver or the vendor's ADB port to the public internet.

## License

GPL-3.0-or-later. Hardware interface work is based on Ulanzi's published GPL source; its attribution is retained. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party trademarks and any separately installed brand assets retain their existing ownership.
