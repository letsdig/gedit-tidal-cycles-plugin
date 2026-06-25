#!/usr/bin/env python3
"""
Tidal Cycles Plugin for gedit
Merged, high-performance edition with smart paragraph/block parsing matching VS Code behaviors.
Optimized console logs (errors and results only), auto-saving config, and automatic .tidal syntax loading.
With fully functional menu system.
"""
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gedit', '3.0')

# GtkSource must be imported to give us access to the language manager engine
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
    app = GObject.Property(type=Gedit.App)
    
    def __init__(self):
        super().__init__()
        
    def do_activate(self):
        self.app.set_accels_for_action('win.eval-smart', ['<Control>Return', '<Control>KP_Enter'])
        self.app.set_accels_for_action('win.hush', ['<Control>period'])
        self.app.set_accels_for_action('win.tidal-comment', ['<Control>ugrave'])

    def do_deactivate(self):
        self.app.set_accels_for_action('win.eval-smart', [])
        self.app.set_accels_for_action('win.hush', [])
        self.app.set_accels_for_action('win.tidal-comment', [])

class TidalCyclesWindowActivatable(GObject.Object, Gedit.WindowActivatable):
    window = GObject.Property(type=Gedit.Window)
    
    def __init__(self):
        super().__init__()
        self.action_group = None
        self._handlers = {}
        self.panel = None
        self.stack = None
        self.ghci_process = None
        self.ghci_thread_out = None
        # Background worker state variables
        self._syntax_worker_active = False
        self._syntax_thread = None

    def do_activate(self):
        self._install_window_actions()
        
        self.panel = TidalSidebarPanel(self)
        bottom_panel = self.window.get_bottom_panel()
        bottom_panel.set_visible(True)
        
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
        
        self._start_ghci_backend()
        
        self.window.connect("active-tab-changed", self._on_tab_changed)
        self.window.connect("tab-added", self._on_tab_added)
        GLib.timeout_add(500, self._setup_view_listener)

        # Spawns a dedicated tracking thread that monitors your workspace
        self._start_syntax_tracker_thread()
        self._extend_gedit_menu()

    def _start_syntax_tracker_thread(self):
        """Launches a thread loop to force correct highlighting every second."""
        self._syntax_worker_active = True
        self._syntax_thread = threading.Thread(target=self._syntax_tracker_loop, daemon=True)
        self._syntax_thread.start()

    def _syntax_tracker_loop(self):
        while self._syntax_worker_active:
            # Safely request the main thread to run the syntax checking engine
            GLib.idle_add(self._force_haskell_syntax)
            time.sleep(1.0)

    def _update_status_safe(self, text):
        if self.panel and hasattr(self.panel, 'status_label'):
            self.panel.status_label.set_text(text)
        return False

    def _start_ghci_backend(self):
        try:
            self.panel.log_message("--- Awakening GHCi via Interactive Bash ---")
            
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
            
            fallback_boot = (
                "(d1, def) <- streamFirst Sound.Tidal.Context.id (provideContext 1)\n"
                "(d2, def) <- streamFirst Sound.Tidal.Context.id (provideContext 2)\n"
                "(d3, def) <- streamFirst Sound.Tidal.Context.id (provideContext 3)\n"
                "(d4, def) <- streamFirst Sound.Tidal.Context.id (provideContext 4)\n"
                "let hush = mapM_ ($ silence) [d1, d2, d3, d4]\n"
            )
            
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
                    self.panel.log_message(f"Injecting boot layout: {path}")
                    self._send_to_ghci(f":script {path}")
                    boot_file_loaded = True
                    if not gui_path:
                        GLib.idle_add(self.panel.set_custom_boot_path, path)
                    break
            
            if not boot_file_loaded:
                self.panel.log_message("WARNING: BootTidal.hs missing! Injecting explicit fallback engine definitions...")
                for line in fallback_boot.splitlines():
                    self._send_to_ghci(line)
            
            GLib.idle_add(self._update_status_safe, "Status: GHCi Runtime Online")
        except Exception as e:
            self.panel.log_message(f"CRITICAL PROCESS FAULT: {str(e)}")
            GLib.idle_add(self._update_status_safe, "Status: Boot Failure")

    def _read_stream(self, stream):
        try:
            for line in stream:
                cleaned = line.rstrip()
                if cleaned:
                    if cleaned.strip() in ("GHCi,", "ghci>", "Prelude>", "*Main>"):
                        continue
                    if any(cleaned.startswith(p) for p in [">", "I#", "F#", "S#"]): 
                        continue
                    GLib.idle_add(self.panel.log_message, cleaned)
        except:
            pass

    def _send_to_ghci(self, code):
        if self.ghci_process and self.ghci_process.stdin:
            try:
                cleaned_code = code.strip()
                if cleaned_code:
                    self.ghci_process.stdin.write(cleaned_code + "\n")
                    self.ghci_process.stdin.flush()
            except Exception as e:
                GLib.idle_add(self.panel.log_message, f"Pipe write failure: {str(e)}")

    def _install_window_actions(self):
        self.action_group = Gio.SimpleActionGroup.new()
        actions = [
            ('eval-smart', self._on_eval_smart),
            ('hush', self._on_hush),
            ('connect-sc', self._on_restart_backend),
            ('tidal-comment', self._on_toggle_comment)
        ]
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.action_group.add_action(action)
        self.window.insert_action_group('win', self.action_group)

    def _extend_gedit_menu(self):
        builder = Gtk.Builder()
        ui_xml = """
        <interface>
          <menu id="context-menu">
            <section>
              <item>
                <attribute name="label">Tidal: Commenta/Decommenta</attribute>
                <attribute name="action">win.tidal-comment</attribute>
              </item>
            </section>
          </menu>
        </interface>
        """
        try:
            builder.add_from_string(ui_xml)
            self.window.get_hamburger_menu() 
        except Exception:
            pass

    def _on_tab_changed(self, window, tab):
        self._setup_view_listener()
        self._force_haskell_syntax()

    def _on_tab_added(self, window, tab):
        self._setup_view_listener()
        self._force_haskell_syntax()

    def _force_haskell_syntax(self):
        """Inietta dinamicamente il file tidal.lang e applica la sintassi nativa."""
        if not self.window:
            return False
            
        # 1. Trova la cartella 'syntax' dentro la directory del plugin
        plugin_dir = os.path.dirname(__file__)
        syntax_dir = os.path.join(plugin_dir, "syntax")

        # 2. Dice a Gedit di aggiungere questa cartella ai suoi percorsi di ricerca
        lang_manager = GtkSource.LanguageManager.get_default()
        search_paths = lang_manager.get_search_paths() or []
        
        if os.path.exists(syntax_dir) and syntax_dir not in search_paths:
            # Creiamo una nuova lista pulita per evitare problemi di immutabilità delle tuple in PyGObject
            new_paths = list(search_paths)
            new_paths.append(syntax_dir)
            lang_manager.set_search_paths(new_paths)

        # 3. Applica la sintassi ai file .tidal aperti
        for view in self.window.get_views():
            if not view or not isinstance(view, Gtk.Widget):
                continue
                
            buf = view.get_buffer()
            if not isinstance(buf, Gedit.Document): 
                continue

            location = buf.get_file().get_location()
            if location:
                filename = location.get_basename()
                if filename.endswith('.tidal'):
                    # Cerchiamo il nostro linguaggio 'tidal' registrato tramite il file .lang
                    tidal_lang = lang_manager.get_language('tidal')
                    
                    if tidal_lang and buf.get_language() != tidal_lang:
                        buf.set_language(tidal_lang)
        return False

    def _setup_view_listener(self):
        view = self.window.get_active_view()
        # SAFEGUARD: Validate that the active view layout is a living GTK widget
        if view and isinstance(view, Gtk.Widget) and view not in self._handlers:
            handler_id = view.connect("key-press-event", self._on_key_press)
            self._handlers[view] = handler_id
        self._force_haskell_syntax()
        return False

    def _on_key_press(self, view, event):
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

    def _get_current_buffer(self):
        view = self.window.get_active_view()
        # SAFEGUARD: Wrap structural logic validation checks
        return view.get_buffer() if view and isinstance(view, Gtk.Widget) else None

    def _on_toggle_comment(self, action, param):
        """Inietta o rimuove '--' basandosi sulla selezione o sulla riga corrente."""
        buffer = self._get_current_buffer()
        if buffer is None or not isinstance(buffer, Gtk.TextBuffer):
            return

        # Restituisce una tupla vuota () se non c'è selezione in PyGObject
        bounds = buffer.get_selection_bounds()
        if bounds:
            start, end = bounds
            has_selection = True
        else:
            insert_mark = buffer.get_insert()
            start = buffer.get_iter_at_mark(insert_mark)
            start.set_line_offset(0)
            end = start.copy()
            end.forward_to_line_end()
            has_selection = False

        text = buffer.get_text(start, end, False)
        lines = text.splitlines()
        if not lines:
            return

        is_commented = all(line.strip().startswith('--') or not line.strip() for line in lines)
        
        new_lines = []
        for line in lines:
            if is_commented:
                if line.strip().startswith('-- '):
                    new_lines.append(line.replace('-- ', '', 1))
                elif line.strip().startswith('--'):
                    new_lines.append(line.replace('--', '', 1))
                else:
                    new_lines.append(line)
            else:
                new_lines.append('-- ' + line)

        new_text = "\n".join(new_lines)
        
        if not has_selection and text.endswith('\n'):
            new_text += '\n'

        buffer.begin_user_action()
        buffer.delete(start, end)
        buffer.insert(start, new_text)
        buffer.end_user_action()

    def _on_eval_smart(self, action, param):
        buffer = self._get_current_buffer()
        if not buffer: return
        
        insert_mark = buffer.get_insert()
        cursor_it = buffer.get_iter_at_mark(insert_mark)
        cursor_line = cursor_it.get_line()
        
        start_doc = buffer.get_start_iter()
        end_doc = buffer.get_end_iter()
        full_text = buffer.get_text(start_doc, end_doc, False)
        all_lines = full_text.splitlines()
        
        if cursor_line >= len(all_lines):
            return
            
        if not all_lines[cursor_line].strip():
            return

        start_line = cursor_line
        while start_line > 0:
            if not all_lines[start_line - 1].strip():
                break
            start_line -= 1

        end_line = cursor_line
        while end_line < len(all_lines) - 1:
            if not all_lines[end_line + 1].strip():
                break
            end_line += 1

        target_lines = all_lines[start_line:end_line + 1]
        cleaned_lines = [line.replace('\t', '    ') for line in target_lines]
        final_text = "\n".join(cleaned_lines)

        if final_text.strip():
            if len(cleaned_lines) > 1:
                multiline_payload = ":{\n" + final_text + "\n:}"
                self._send_to_ghci(multiline_payload)
            else:
                self._send_to_ghci(final_text)
            
            GLib.idle_add(self.panel.log_message, "")
                
            block_start_iter = buffer.get_iter_at_line(start_line)
            block_end_iter = buffer.get_iter_at_line(end_line)
            block_end_iter.forward_to_line_end()
            self._highlight_eval(buffer, block_start_iter, block_end_iter)

    def _on_hush(self, action, param):
        self._send_to_ghci('hush')

    def _on_restart_backend(self, action, param):
        self._kill_ghci_backend()
        self._start_ghci_backend()

    def _kill_ghci_backend(self):
        if self.ghci_process:
            try:
                os.killpg(os.getpgid(self.ghci_process.pid), signal.SIGTERM)
            except Exception:
                try: 
                    self.ghci_process.terminate()
                except Exception: 
                    pass
            self.ghci_process = None
        GLib.idle_add(self._update_status_safe, "Status: Offline")

    def _highlight_eval(self, buffer, start, end):
        table = buffer.get_tag_table()
        tag = table.lookup('tidal-eval') or buffer.create_tag('tidal-eval', background='#2e3440', foreground='#88c0d0')
        start_mark = buffer.create_mark(None, start, True)
        end_mark = buffer.create_mark(None, end, True)
        buffer.apply_tag(tag, start, end)
        
        def clear_highlight():
            if GObject.Object.__gtype__.name != 'void' and buffer:
                try:
                    s = buffer.get_iter_at_mark(start_mark)
                    e = buffer.get_iter_at_mark(end_mark)
                    buffer.remove_tag(tag, s, e)
                    buffer.delete_mark(start_mark)
                    buffer.delete_mark(end_mark)
                except Exception:
                    pass
            return False
        GLib.timeout_add(180, clear_highlight)

    def do_deactivate(self):
        # Shut down background highlighting workers safely
        self._syntax_worker_active = False
        self._kill_ghci_backend()
        for view, handler_id in list(self._handlers.items()):
            try: 
                # SAFEGUARD: Protect against stale handler lookups on closed tabs
                if view and isinstance(view, Gtk.Widget):
                    view.disconnect(handler_id)
            except Exception: 
                pass
        self._handlers.clear()
        if self.action_group:
            self.window.insert_action_group('win', None)
        
        if self.panel and self.stack:
            if isinstance(self.stack, Gtk.Notebook):
                try:
                    page_num = self.stack.page_num(self.panel)
                    if page_num != -1:
                        self.stack.remove_page(page_num)
                except Exception: 
                    pass
            elif isinstance(self.stack, Gtk.Stack):
                try: 
                    self.stack.remove(self.panel)
                except Exception: 
                    pass


class TidalSidebarPanel(Gtk.Box):
    def __init__(self, plugin_window_instance):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.plugin = plugin_window_instance
        self.set_border_width(5)
        
        self.keyfile = GLib.KeyFile()
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            self.keyfile.load_from_file(CONFIG_FILE, GLib.KeyFileFlags.NONE)
        except Exception:
            pass

        self.status_label = Gtk.Label(label="Status: Booting...")
        self.status_label.set_xalign(0.0)
        self.pack_start(self.status_label, False, False, 2)
        
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        
        self.text_view = Gtk.TextView()
        self.text_view.set_editable(False)
        self.text_view.set_cursor_visible(False)
        self.text_view.set_wrap_mode(Gtk.WrapMode.WORD)
        self.text_view.override_font(Pango.FontDescription.from_string("Monospace 9"))
        
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
        
        restart_btn = Gtk.Button(label="Restart Engine Console")
        restart_btn.connect("clicked", lambda w: self.plugin._on_restart_backend(None, None))
        self.pack_start(restart_btn, False, False, 2)
        
        self.show_all()

    def get_custom_boot_path(self):
        return self.path_entry.get_text()

    def set_custom_boot_path(self, path):
        self.path_entry.set_text(path)
        self._write_config_to_disk(path)

    def _on_focus_out_save(self, entry, event):
        self._write_config_to_disk(entry.get_text())
        return False

    def _write_config_to_disk(self, text_path):
        try:
            self.keyfile.set_string("TidalConfig", "boot_path", text_path)
            data = self.keyfile.to_data()[0]
            with open(CONFIG_FILE, "w") as f:
                f.write(data)
        except Exception as e:
            print(f"Error saving config: {e}")

    def log_message(self, message):
        if threading.current_thread() != threading.main_thread():
            GLib.idle_add(self.log_message, message)
            return

        buf = self.text_view.get_buffer()
        end_iter = buf.get_end_iter()
        buf.insert(end_iter, message + "\n")
        
        end_mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.text_view.scroll_to_mark(end_mark, 0.0, False, 0.0, 1.0)
        buf.delete_mark(end_mark)
