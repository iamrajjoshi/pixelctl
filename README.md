# pixelctl

A local-first CLI and extensible runtime for pixel clocks. **Ulanzi TC002 is the first supported device.** The CLI, page/provider plugins, and themes are independent of any company or account.

The native TC002 receiver owns the LEDs, knob, buttons, page rotation, cached frames, and an offline clock. An optional collector runs on a Mac, Linux machine, or always-on host and supplies live page frames. No desktop UI or vendor cloud account is required.

## Status

This is an early hardware-tested release, not a universal firmware installer. The native display and physical controls have been tested on a TC002. The Python collector supports GitHub PRs and contributions, Vercel production deployments, and strict HTTP API health checks. These providers currently run on the collector host using its existing CLI authentication.

The native HTTPS utility is a building block for future device-side providers; it does not make the current Python providers run on the clock. The TC002 has approximately 36 MB of RAM. Local clock/navigation work without a collector; fresh authenticated API data currently needs the collector online. An always-on host can replace a laptop. Device-side API credentials should be narrowly scoped, rather than copying a desktop account's login store.

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

Start with [examples/minimal.json](examples/minimal.json), or [examples/developer.json](examples/developer.json) for GitHub, Vercel, and API checks. Credentials remain in the existing `gh` and Vercel CLI stores. No credentials are embedded in examples.

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
| `prs` | Complete authored open-PR list, draft/review/CI states |
| `garden` | Nineteen weeks of the GitHub contribution calendar |
| `deploy` | Latest production deployment and separately verified serving alias |
| `health` | Strict endpoint health predicate and observed response latency |
| `uptime` | Success percentage of observed API checks, with its measurement window |
| `identity` | Configurable mark and animated pixel wordmark |

Deployment `LIVE` requires a successful serving deployment matching the production alias. Otherwise a successful build is only `READY`. API transport errors are unknown rather than asserted production outages. Availability excludes unknown checks and unobserved gaps; it is not a historical service SLO. Stale data is marked explicitly. Cached pages stay available when the collector disconnects, and the clock continues keeping time locally.

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

The per-user LaunchAgent starts at login. Local plugin files and sibling assets are copied into the private application-support directory. Linux users can run `pixelctl run` under their normal process manager; automatic Linux service installation is not included.

## Development

```sh
uv venv --python 3.12
uv pip install -e .
.venv/bin/python -m unittest discover -s tests -v
```

Native protocol and hardware simulation tests are documented in `native/README.md`. HTTP writes require a device pairing key. Never expose the LAN receiver or the vendor's ADB port to the public internet.

## License

GPL-3.0-or-later. Hardware interface work is based on Ulanzi's published GPL source; its attribution is retained. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Third-party trademarks and any separately installed brand assets retain their existing ownership.
