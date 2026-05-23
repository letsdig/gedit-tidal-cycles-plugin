# Tidal Cycles Plugin for Gedit

A merged, high-performance live coding environment for **Tidal Cycles** and **SuperCollider** directly inside **Gedit**. It features smart paragraph/block parsing matching VS Code behaviors, optimized console logging, auto-saving configurations, and automatic syntax loading.

---

Tested and developed on **Ubuntu 24.04 LTS**.

---

## Features

* **Smart Evaluation:** Evaluates the current block/paragraph of code automatically based on your cursor position (matching modern IDE behaviors).
* **Visual Evaluation Flash:** Briefly highlights evaluated blocks using a dedicated color scheme (`#2e3440` / `#88c0d0`) for immediate visual feedback.
* **Dedicated Log Console:** Includes a bottom panel UI with a custom dark theme (`#1e222a`) displaying only evaluation results and errors.
* **Automatic Syntax Detection:** Forces the Gedit buffer to use Haskell syntax highlighting automatically whenever a `.tidal` file is opened.
* **Persistent Configuration:** Auto-saves the custom path to your `BootTidal.hs` script using a local `.ini` configuration file.
* **Robust Process Management:** Spawns GHCi safely inside an interactive Bash session and handles full process group termination on exit to prevent orphaned background tasks.

---

## Requirements

To run this plugin, your system must meet the following software requirements:

* **Text Editor:** Gedit **3.0 or higher**.
* **UI Toolkit:** **GTK 3.0** (The plugin is specifically targeted for Gedit's GTK 3 architecture).
* **Language Runtime:** Python **3**.
* **Dependencies:**
  * Python GObject Introspection bindings (`python3-gi`) with support for `Gtk 3.0` and `Gedit 3.0`.
  * A functional installation of **Tidal Cycles** and **GHCi**.
  * Standard Unix utilities (`bash`).

---

## Keybindings

| Shortcut | Action |
| :--- | :--- |
| **`Ctrl + Return`** / **`Ctrl + KP_Enter`** | **Smart Eval:** Evaluates the current block or line of text. |
| **`Ctrl + .`** (Period) | **Hush:** Instantly silences all active audio streams. |

---

## Installation
* `~/.local/share/gedit/plugins/`
  * `├── tidal_cycles.plugin`
  * `└── tidal_cycles_plugin.py`

Activate the Plugin:

Open Gedit.

Go to Edit -> Preferences -> Plugins.

Look for Tidal Cycles in the list and check the box to enable it.

## Configuration
Upon activation, the plugin will look into standard directories (~/.ghcup/, ~/.cabal/, etc.) to automatically discover your BootTidal.hs file.

If your boot script is in a custom location:

- Open the Tidal panel at the bottom of Gedit.

Type or paste the absolute path into the Boot File input field.

The configuration will automatically save to ~/.config/gedit/plugins/tidal_config.ini when the input field loses focus.
1. **Create the Gedit local plugin directory** (if it doesn't exist yet):
   ```bash
   mkdir -p ~/.local/share/gedit/plugins/
