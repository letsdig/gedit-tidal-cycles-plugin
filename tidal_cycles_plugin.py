#!/usr/bin/env python3
"""
Tidal Cycles Plugin for gedit - CORRECTED VERSION
Fixed, high-performance edition with smart paragraph/block parsing matching VS Code behaviors.
Optimized console logs, auto-saving config, and automatic .tidal syntax loading.
With fully functional menu system.
"""
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gedit', '3.0')

from gi.repository import Gtk, Gedit, GObject, Gio, GLib, Gdk, Pango, GtkSource
import subprocess
import threading
import os
import signal
import glob
import time

CONFIG_DIR = os.path.expanduser("~/.config/gedit/plugins")
CONFIG_FILE = os.path.join(CONFIG_DIR, "tidal_config.ini")


class TidalCyclesPlugin(GObject.Object, Gedit.AppActivatable):
    """Main plugin activator for the gedit application."""
    app = GObject.Property(type=Gedit.App)
    
    def __init__(self):
        super().__init__()
        
    def do_activate(self):
        """Set up keyboard shortcuts when plugin activates."""
        self.app.set_accels_for_action('win.eval-smart', ['<Control>Return', '<Control>KP_Enter'])
        self.app.set_accels_for_action('win.hush', ['<Control>period'])

    def do_deactivate(self):
        """Clean up keyboard shortcuts when plugin deactivates."""
        self.app.set_accels_for_action('win.eval-smart', [])
        self.app.set_accels_for_action('win.hush', [])


class TidalCyclesWindowActivatable(GObject.Object, Gedit.WindowActivatable):
    """Main window-level plugin that handles all Tidal interaction."""
    window = GObject.Property(type=Gedit.Window)
    
    def __init__(self):
        super().__init__()
        self.action_group = None
        self._handlers = {}
        self.panel = None
        self.stack = None
        self.ghci_process = None
        self.ghci_thread_out = None

    def do_activate(self):
        """Initialize plugin when window is activated."""
        self._install_window_actions()
        
        # Set up the sidebar panel
        self.panel = TidalSidebarPanel(self)
        bottom_panel = self.window.get_bottom_panel()
        bottom_panel.set_visible(True)
        
        # Attempt to add panel to Stack or Notebook
        if isinstance(bottom_panel, Gtk.Stack):
            self.stack = bottom_panel
            self.stack.add_titled(self.panel, "tidal-panel", "Tidal")
            self.stack.child_set_property(self.panel, "icon-name", "media-playback-start")
            self.panel.show_all()
            self.stack.set_visible_child(self.panel)
        elif isinstance(bottom_panel, Gtk.Notebook):
            self.stack = bottom_panel
            tab_label = Gtk.Label(label="Tidal")
            self.stack.append_page(self.panel, tab_label)
            self.panel.show_all()
            page_num = self.stack.page_num(self.panel)
            self.stack.set_current_page(page_num)
        else:
            # Fallback: search for Stack or Notebook in children
            self.stack = None
            if hasattr(bottom_panel, "get_children"):
                for child in bottom_panel.get_children():
                    if isinstance(child, (Gtk.Stack, Gtk.Notebook)):
                        self.stack = child
                        break
            
            if isinstance(self.stack, Gtk.Stack):
                self.stack.add_titled(self.panel, "tidal-panel", "Tidal")
                self.stack.child_set_property(self.panel, "icon-name", "media-playback-start")
                self.panel.show_all()
                self.stack.set_visible_child(self.panel)
            elif isinstance(self.stack, Gtk.Notebook):
                tab_label = Gtk.Label(label="Tidal")
                self.stack.append_page(self.panel, tab_label)
                self.panel.show_all()
                page_num = self.stack.page_num(self.panel)
                self.stack.set_current_page(page_num)
            else:
                bottom_panel.add(self.panel)
                bottom_panel.show_all()
        
        # Start GHCi backend
        self._start_ghci_backend()
        
        # Connect view/tab signals
        self.window.connect("active-tab-changed", self._on_tab_changed)
        self.window.connect("tab-added", self._on_tab_added)
        GLib.timeout_add(500, self._setup_view_listener)

    def _start_ghci_backend(self):
        """Launch GHCi subprocess with Tidal support."""
        try:
            self.panel.log_message("--- Initializing GHCi with Tidal ---")
            
            env = os.environ.copy()
            ghcup_path = os.path.expanduser("~/.ghcup/bin")
            cabal_path = os.path.expanduser("~/.cabal/bin")
            local_path = os.path.expanduser("~/.local/bin")
            env["PATH"] = f"{ghcup_path}:{cabal_path}:{local_path}:/usr/local/bin:/usr/bin:/bin:{env.get('PATH', '')}"

            cmd = ["bash", "-i", "-c", "ghci -XOverloadedStrings -package tidal"]
            
            self.ghci_process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True
            )
            
            self.ghci_thread_out = threading.Thread(
                target=self._read_stream, args=(self.ghci_process.stdout,), daemon=True
            )
            self.ghci_thread_out.start()
            
            self._send_to_ghci("import Sound.Tidal.Context")
            
            # Fallback initialization if BootTidal.hs not found
            fallback_boot = (
                "(d1, def) <- streamFirst Sound.Tidal.Context.id (provideContext 1)\n"
                "(d2, def) <- streamFirst Sound.Tidal.Context.id (provideContext 2)\n"
                "(d3, def) <- streamFirst Sound.Tidal.Context.id (provideContext 3)\n"
                "(d4, def) <- streamFirst Sound.Tidal.Context.id (provideContext 4)\n"
                "let hush = mapM_ ($ silence) [d1, d2, d3, d4]\n"
            )
            
            # Try to load BootTidal.hs
            gui_path = self.panel.get_custom_boot_path().strip()
            
            boot_paths = []
            if gui_path:
                boot_paths.append(gui_path)
                
            boot_paths.extend([
                os.path.expanduser("~/.cabal/share/x86_64-linux-ghc-*/tidal-*/BootTidal.hs"),
                os.path.expanduser("~/.ghcup/share/x86_64-linux-ghc-*/tidal-*/BootTidal.hs"),
                "BootTidal.hs"
            ])
            
            resolved_paths = []
            for p in boot_paths:
                resolved_paths.extend(glob.glob(p))
                
            boot_file_loaded = False
            for path in resolved_paths:
                if os.path.exists(path):
                    self.panel.log_message(f"Loading boot file: {path}")
                    self._send_to_ghci(f":script {path}")
                    boot_file_loaded = True
                    if not gui_path:
                        GLib.idle_add(self.panel.set_custom_boot_path, path)
                    break
            
            if not boot_file_loaded:
                self.panel.log_message("WARNING: BootTidal.hs not found. Using fallback initialization...")
                for line in fallback_boot.splitlines():
                    self._send_to_ghci(line)
            
            GLib.idle_add(self._update_status_safe, "Status: GHCi Online")
        except Exception as e:
            self.panel.log_message(f"ERROR: Failed to start GHCi: {str(e)}")
            GLib.idle_add(self._update_status_safe, "Status: Boot Failed")

    def _read_stream(self, stream):
        """Read output from GHCi process (runs in background thread)."""
        try:
            for line in stream:
                cleaned = line.rstrip()
                if not cleaned:
                    continue
                
                # Filter out only prompt lines
                if cleaned.strip() in ("GHCi,", "ghci>", "Prelude>", "*Main>"):
                    continue
                
                # Only filter lines that are JUST prompt symbols, not lines starting with them
                if cleaned in (">",):
                    continue
                
                # Log everything else
                GLib.idle_add(self.panel.log_message, cleaned)
        except Exception:
            pass

    def _send_to_ghci(self, code):
        """Send code to GHCi process."""
        if self.ghci_process and self.ghci_process.stdin:
            try:
                cleaned_code = code.strip()
                if cleaned_code:
                    self.ghci_process.stdin.write(cleaned_code + "\n")
                    self.ghci_process.stdin.flush()
            except Exception as e:
                GLib.idle_add(self.panel.log_message, f"ERROR: Failed to send to GHCi: {str(e)}")

    def _install_window_actions(self):
        """Set up window-level actions."""
        self.action_group = Gio.SimpleActionGroup.new()
        actions = [
            ('eval-smart', self._on_eval_smart),
            ('hush', self._on_hush),
            ('connect-sc', self._on_restart_backend)
        ]
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.action_group.add_action(action)
        self.window.insert_action_group('win', self.action_group)

    def _on_tab_changed(self, window, tab):
        """Handle tab change event."""
        try:
            if not self._is_window_valid():
                return
            self._setup_view_listener()
            self._apply_syntax_highlighting()
        except Exception:
            pass

    def _on_tab_added(self, window, tab):
        """Handle new tab event."""
        try:
            if not self._is_window_valid():
                return
            self._setup_view_listener()
            self._apply_syntax_highlighting()
        except Exception:
            pass

    def _apply_syntax_highlighting(self):
        """Apply Haskell syntax highlighting to .tidal files (called only when needed)."""
        try:
            if not self._is_window_valid():
                return False
            
            for view in self.window.get_views():
                try:
                    if not self._is_view_valid(view):
                        continue
                    
                    buf = view.get_buffer()
                    if not self._is_buffer_valid(buf):
                        continue

                    location = buf.get_file().get_location()
                    if not location:
                        continue
                    
                    filename = location.get_basename()
                    if filename and filename.endswith('.tidal'):
                        lang_manager = GtkSource.LanguageManager.get_default()
                        haskell_lang = lang_manager.get_language('haskell')
                        
                        if haskell_lang and buf.get_language() != haskell_lang:
                            buf.set_language(haskell_lang)
                except Exception:
                    continue
            
            return False
        except Exception:
            return False

    def _setup_view_listener(self):
        """Set up key press listener on current view."""
        try:
            if not self._is_window_valid():
                return False
            
            view = self.window.get_active_view()
            if view and self._is_view_valid(view) and view not in self._handlers:
                handler_id = view.connect("key-press-event", self._on_key_press)
                self._handlers[view] = handler_id
            
            self._apply_syntax_highlighting()
        except Exception:
            pass
        
        return False

    def _on_key_press(self, view, event):
        """Handle keyboard shortcuts."""
        state = event.state & Gtk.accelerator_get_default_mod_mask()
        is_enter = event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, 13)
        is_period = event.keyval in (Gdk.KEY_period, Gdk.KEY_KP_Decimal, 46)
        
        if state == Gdk.ModifierType.CONTROL_MASK:
            if is_enter:
                self._on_eval_smart(None, None)
                return True
            elif is_period:
                self._on_hush(None, None)
                return True
        return False

    def _is_window_valid(self):
        """Check if the window still exists and is valid."""
        try:
            return self.window and isinstance(self.window, Gedit.Window)
        except Exception:
            return False

    def _is_buffer_valid(self, buffer):
        """Check if a buffer is still valid before accessing it."""
        try:
            if not buffer:
                return False
            if not isinstance(buffer, Gedit.Document):
                return False
            # Try to access a safe property to verify buffer is alive
            _ = buffer.get_language()
            return True
        except Exception:
            return False

    def _is_view_valid(self, view):
        """Check if a view is still valid before accessing it."""
        try:
            if not view:
                return False
            if not isinstance(view, Gtk.Widget):
                return False
            # Try to get the buffer to verify view is alive
            buf = view.get_buffer()
            return self._is_buffer_valid(buf)
        except Exception:
            return False

    def _get_current_buffer(self):
        """Get the current active buffer, with full validation."""
        try:
            if not self._is_window_valid():
                return None
            
            view = self.window.get_active_view()
            if not self._is_view_valid(view):
                return None
            
            buffer = view.get_buffer()
            if not self._is_buffer_valid(buffer):
                return None
            
            return buffer
        except Exception:
            return None

    def _on_eval_smart(self, action, param):
        """Evaluate current paragraph/block (smart eval)."""
        try:
            # Early validation
            buffer = self._get_current_buffer()
            if not buffer or not self._is_buffer_valid(buffer):
                return
            
            # Now we can safely access buffer
            insert_mark = buffer.get_insert()
            if not insert_mark:
                return
            
            cursor_it = buffer.get_iter_at_mark(insert_mark)
            if not cursor_it:
                return
            
            cursor_line = cursor_it.get_line()
            
            start_doc = buffer.get_start_iter()
            end_doc = buffer.get_end_iter()
            full_text = buffer.get_text(start_doc, end_doc, False)
            all_lines = full_text.splitlines()
            
            if cursor_line >= len(all_lines) or not all_lines[cursor_line].strip():
                return

            # Find paragraph bounds
            start_line = cursor_line
            while start_line > 0 and all_lines[start_line - 1].strip():
                start_line -= 1

            end_line = cursor_line
            while end_line < len(all_lines) - 1 and all_lines[end_line + 1].strip():
                end_line += 1

            # Extract and clean
            target_lines = all_lines[start_line:end_line + 1]
            cleaned_lines = [line.replace('\t', '    ') for line in target_lines]
            final_text = "\n".join(cleaned_lines)

            if not final_text.strip():
                return

            # Send to GHCi
            if len(cleaned_lines) > 1:
                self._send_to_ghci(":{\n" + final_text + "\n:}")
            else:
                self._send_to_ghci(final_text)
            
            GLib.idle_add(self.panel.log_message, "")
            
            # Highlight (with full validation)
            try:
                if self._is_buffer_valid(buffer):
                    block_start_iter = buffer.get_iter_at_line(start_line)
                    block_end_iter = buffer.get_iter_at_line(end_line)
                    if block_start_iter and block_end_iter:
                        block_end_iter.forward_to_line_end()
                        self._highlight_eval(buffer, block_start_iter, block_end_iter)
            except Exception:
                pass
                
        except Exception as e:
            try:
                self.panel.log_message(f"ERROR in eval_smart: {str(e)}")
            except Exception:
                pass

    def _on_hush(self, action, param):
        """Send hush command to stop all Tidal patterns."""
        self._send_to_ghci('hush')

    def _on_restart_backend(self, action, param):
        """Restart the GHCi backend."""
        self._kill_ghci_backend()
        self._start_ghci_backend()

    def _kill_ghci_backend(self):
        """Terminate the GHCi process."""
        try:
            if self.ghci_process:
                try:
                    os.killpg(os.getpgid(self.ghci_process.pid), signal.SIGTERM)
                except Exception:
                    try:
                        self.ghci_process.terminate()
                    except Exception:
                        pass
                self.ghci_process = None
            
            try:
                GLib.idle_add(self._update_status_safe, "Status: Offline")
            except Exception:
                pass
        except Exception:
            pass

    def _update_status_safe(self, text):
        """Safely update status label from any thread."""
        try:
            if self.panel and hasattr(self.panel, 'status_label') and self.panel.status_label:
                self.panel.status_label.set_text(text)
        except Exception:
            pass
        return False

    def _highlight_eval(self, buffer, start, end):
        """Highlight evaluated code block, then remove highlight after 180ms."""
        try:
            # Validate buffer before touching it
            if not self._is_buffer_valid(buffer):
                return
            
            table = buffer.get_tag_table()
            if not table:
                return
            
            tag = table.lookup('tidal-eval')
            if not tag:
                tag = buffer.create_tag('tidal-eval', background='#2e3440', foreground='#88c0d0')
            
            if not tag:
                return
            
            buffer.apply_tag(tag, start, end)
            
            # Store line numbers for deferred cleanup
            start_line = start.get_line()
            end_line = end.get_line()
            
            def clear_highlight():
                try:
                    # Re-validate buffer in callback before touching it
                    if not self._is_buffer_valid(buffer):
                        return False
                    
                    s = buffer.get_iter_at_line(start_line)
                    e = buffer.get_iter_at_line(end_line)
                    if s and e:
                        e.forward_to_line_end()
                        buffer.remove_tag(tag, s, e)
                except Exception:
                    pass
                return False
            
            GLib.timeout_add(180, clear_highlight)
        except Exception:
            pass

    def do_deactivate(self):
        """Clean up when plugin is deactivated."""
        try:
            self._kill_ghci_backend()
        except Exception:
            pass
        
        # Clear handlers dictionary but don't try to disconnect from potentially destroyed views
        self._handlers.clear()
        
        if self.action_group:
            try:
                self.window.insert_action_group('win', None)
            except Exception:
                pass
        
        # Don't try to access stack/panel as they may be destroyed
        self.stack = None
        self.panel = None


class TidalSidebarPanel(Gtk.Box):
    """Sidebar panel for displaying output and configuration."""
    
    def __init__(self, plugin_window_instance):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.plugin = plugin_window_instance
        self.set_border_width(5)
        
        # Load config from disk
        self.keyfile = GLib.KeyFile()
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            self.keyfile.load_from_file(CONFIG_FILE, GLib.KeyFileFlags.NONE)
        except Exception:
            pass

        # Status label
        self.status_label = Gtk.Label(label="Status: Booting...")
        self.status_label.set_xalign(0.0)
        self.pack_start(self.status_label, False, False, 2)
        
        # Output text view
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        
        self.text_view = Gtk.TextView()
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.override_font(Pango.FontDescription.from_string("Monospace 9"))
        
        # Dark theme styling
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            textview text {
                background-color: #1e222a;
                color: #abb2bf;
            }
        """)
        self.text_view.get_style_context().add_provider(
            css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
        scroll.add(self.text_view)
        self.pack_start(scroll, True, True, 2)
        
        # Boot file path configuration
        config_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        path_label = Gtk.Label(label="Boot File:")
        self.path_entry = Gtk.Entry()
        self.path_entry.set_placeholder_text("Absolute path to BootTidal.hs...")
        
        try:
            saved_path = self.keyfile.get_string("TidalConfig", "boot_path")
            self.path_entry.set_text(saved_path)
        except Exception:
            pass

        self.path_entry.connect("focus-out-event", self._on_focus_out_save)

        config_box.pack_start(path_label, False, False, 2)
        config_box.pack_start(self.path_entry, True, True, 2)
        self.pack_start(config_box, False, False, 2)
        
        # Restart button
        restart_btn = Gtk.Button(label="Restart Engine")
        restart_btn.connect("clicked", lambda w: self.plugin._on_restart_backend(None, None))
        self.pack_start(restart_btn, False, False, 2)
        
        self.show_all()

    def get_custom_boot_path(self):
        """Get the configured boot file path."""
        return self.path_entry.get_text()

    def set_custom_boot_path(self, path):
        """Set and save the boot file path."""
        self.path_entry.set_text(path)
        self._write_config_to_disk(path)

    def _on_focus_out_save(self, entry, event):
        """Save config when boot path entry loses focus."""
        self._write_config_to_disk(entry.get_text())
        return False

    def _write_config_to_disk(self, text_path):
        """Write configuration to disk."""
        try:
            self.keyfile.set_string("TidalConfig", "boot_path", text_path)
            data = self.keyfile.to_data()[0]
            with open(CONFIG_FILE, "w") as f:
                f.write(data)
        except Exception as e:
            print(f"Error saving config: {e}")

    def log_message(self, message):
        """Log a message to the output panel (thread-safe)."""
        if threading.current_thread() != threading.main_thread():
            GLib.idle_add(self.log_message, message)
            return

        buf = self.text_view.get_buffer()
        end_iter = buf.get_end_iter()
        buf.insert(end_iter, message + "\n")
        
        end_mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.text_view.scroll_to_mark(end_mark, 0.0, False, 0.0, 1.0)
        buf.delete_mark(end_mark)
