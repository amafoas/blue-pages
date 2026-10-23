"""Main window and dialogs."""
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GObject, Gtk, Pango

from . import APP_ID, APP_NAME, connect
from .settings import Settings
from .store import PROTOCOLS, Store


class Item(GObject.Object):
    def __init__(self, kind, data, subtitle=None):
        super().__init__()
        self.kind = kind  # "folder" | "server"
        self.data = data
        self.subtitle = subtitle


# --- dialogs ---------------------------------------------------------------


class FormDialog(Adw.Dialog):
    """Base dialog: header with Cancel/Save and a group of rows."""

    def __init__(self, title, on_save):
        super().__init__(title=title, content_width=420)
        self._on_save = on_save

        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        self.save_btn = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self.save_btn.connect("clicked", lambda *_: self._save())
        header.pack_start(cancel)
        header.pack_end(self.save_btn)

        self.group = Adw.PreferencesGroup()
        page = Adw.PreferencesPage()
        page.add(self.group)

        view = Adw.ToolbarView(content=page)
        view.add_top_bar(header)
        self.set_child(view)
        self.set_default_widget(self.save_btn)

    def entry(self, title, text="", password=False):
        row = (Adw.PasswordEntryRow if password else Adw.EntryRow)(title=title)
        row.set_text(text)
        row.connect("entry-activated", lambda *_: self._save())
        self.group.add(row)
        return row

    def combo(self, title, labels, selected=0):
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
        row.set_selected(selected)
        self.group.add(row)
        return row

    def _save(self):
        if self._on_save():
            self.close()


def folder_combo(dialog, store, selected_id, exclude=None):
    paths = [(None, "(Root)")] + store.folder_paths(exclude=exclude)
    ids = [p[0] for p in paths]
    idx = ids.index(selected_id) if selected_id in ids else 0
    row = dialog.combo("Folder", [p[1] for p in paths], idx)
    return row, ids


class ServerDialog(FormDialog):
    def __init__(self, store, server=None, folder_id=None, on_saved=None):
        super().__init__("Edit Server" if server else "New Server", self.save)
        self.store, self.server, self.on_saved = store, server, on_saved
        s = server or {}
        self.name = self.entry("Name", s.get("name", ""))
        proto = s.get("protocol", "smb")
        self.proto = self.combo("Protocol", PROTOCOLS, PROTOCOLS.index(proto))
        self.address = self.entry("Address (host or host/share)", s.get("address", ""))
        self.user = self.entry("User (DOMAIN\\user for SMB)", s.get("user", ""))
        self.password = self.entry(
            "Password", store.get_password(s["id"]) if server else "", password=True
        )
        self.folder, self.folder_ids = folder_combo(
            self, store, s.get("folder_id", folder_id)
        )

    def save(self):
        name = self.name.get_text().strip()
        address = self.address.get_text().strip()
        if not name or not address:
            for row in (self.name, self.address):
                if not row.get_text().strip():
                    row.add_css_class("error")
            return False
        data = {
            "name": name,
            "protocol": PROTOCOLS[self.proto.get_selected()],
            "address": address,
            "user": self.user.get_text().strip(),
            "folder_id": self.folder_ids[self.folder.get_selected()],
        }
        password = self.password.get_text()
        if self.server:
            self.store.update_server(self.server, data, password)
            server = self.server
        else:
            server = self.store.add_server(data, password)
        self.on_saved(server)
        return True


class FolderDialog(FormDialog):
    def __init__(self, store, folder=None, parent_id=None, on_saved=None):
        super().__init__("Edit Folder" if folder else "New Folder", self.save)
        self.store, self.folder_obj, self.on_saved = store, folder, on_saved
        f = folder or {}
        self.name = self.entry("Name", f.get("name", ""))
        self.parent, self.parent_ids = folder_combo(
            self, store, f.get("parent_id", parent_id), exclude=f.get("id")
        )
        self.parent.set_title("Inside")

    def save(self):
        name = self.name.get_text().strip()
        if not name:
            self.name.add_css_class("error")
            return False
        parent_id = self.parent_ids[self.parent.get_selected()]
        if self.folder_obj:
            self.store.update_folder(self.folder_obj, name, parent_id)
            folder = self.folder_obj
        else:
            folder = self.store.add_folder(name, parent_id)
        self.on_saved(folder)
        return True


class PreferencesDialog(Adw.PreferencesDialog):
    OPEN_IN = [("window", "New Window"), ("tab", "New Tab")]

    def __init__(self, settings):
        super().__init__()
        self.settings = settings

        open_in = Adw.ComboRow(
            title="Open Servers In",
            subtitle="A new tab is added to the Files window that is already open",
            model=Gtk.StringList.new([label for _, label in self.OPEN_IN]),
        )
        keys = [key for key, _ in self.OPEN_IN]
        open_in.set_selected(keys.index(settings["open_in"]))
        open_in.connect("notify::selected",
                        lambda row, _: settings.__setitem__("open_in", keys[row.get_selected()]))

        group = Adw.PreferencesGroup(title="Files")
        group.add(open_in)
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")
        page.add(group)
        self.add(page)


# --- main window ----------------------------------------------------------


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title=APP_NAME, default_width=420, default_height=560)
        self.store = Store()
        self.settings = Settings()
        self.expanded = set()
        self._refreshing = False
        self.context_item = None
        self._setup_actions()

        # Header
        header = Adw.HeaderBar()
        add_menu = Gio.Menu()
        add_menu.append("New Server", "win.new-server")
        add_menu.append("New Folder", "win.new-folder")
        header.pack_start(Gtk.MenuButton(icon_name="list-add-symbolic", menu_model=add_menu,
                                         tooltip_text="Add"))
        main_menu = Gio.Menu()
        main_menu.append("Preferences", "win.preferences")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=main_menu,
                                       primary=True, tooltip_text="Main Menu"))
        search_btn = Gtk.ToggleButton(icon_name="system-search-symbolic", tooltip_text="Search")
        header.pack_end(search_btn)

        # Search
        self.search_entry = Gtk.SearchEntry(hexpand=True)
        self.search_entry.connect("search-changed", lambda *_: self.refresh())
        search_bar = Gtk.SearchBar(child=self.search_entry, key_capture_widget=self)
        search_bar.connect_entry(self.search_entry)
        search_btn.bind_property("active", search_bar, "search-mode-enabled",
                                 GObject.BindingFlags.BIDIRECTIONAL)

        # List
        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._setup_row)
        factory.connect("bind", self._bind_row)
        factory.connect("unbind", self._unbind_row)
        self.selection = Gtk.SingleSelection(autoselect=False, can_unselect=True)
        self.list_view = Gtk.ListView(model=self.selection, factory=factory,
                                      single_click_activate=True, css_classes=["navigation-sidebar"])
        self.list_view.connect("activate", self._on_activate)

        # A single context menu shared by all rows.
        self.menu_model = Gio.Menu()
        self.popover = Gtk.PopoverMenu(menu_model=self.menu_model, has_arrow=False,
                                       halign=Gtk.Align.START)
        self.popover.set_parent(self.list_view)
        self.connect("destroy", lambda *_: self.popover.unparent())
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click)
        self.list_view.add_controller(click)

        self.empty = Adw.StatusPage(icon_name="network-server-symbolic", title="No Servers",
                                    description="Use + to add a server or a folder")
        self.stack = Gtk.Stack()
        self.stack.add_named(Gtk.ScrolledWindow(child=self.list_view, vexpand=True), "list")
        self.stack.add_named(self.empty, "empty")

        self.toasts = Adw.ToastOverlay(child=self.stack)
        view = Adw.ToolbarView(content=self.toasts)
        view.add_top_bar(header)
        view.add_top_bar(search_bar)
        self.set_content(view)
        self.refresh()

    # --- actions ---
    def _setup_actions(self):
        actions = {
            "new-server": lambda: self._new_server(None),
            "new-folder": lambda: self._new_folder(None),
            "ctx-open": lambda: self.open_server(self.context_item.data),
            "ctx-new-server": lambda: self._new_server(self.context_item.data["id"]),
            "ctx-new-folder": lambda: self._new_folder(self.context_item.data["id"]),
            "ctx-edit": self._edit,
            "ctx-delete": self._delete,
            "preferences": lambda: PreferencesDialog(self.settings).present(self),
        }
        for name, cb in actions.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", lambda _a, _p, cb=cb: cb())
            self.add_action(action)

    def _new_server(self, folder_id):
        ServerDialog(self.store, folder_id=folder_id, on_saved=self._focus).present(self)

    def _new_folder(self, parent_id):
        if parent_id:
            self.expanded.add(parent_id)
        FolderDialog(self.store, parent_id=parent_id, on_saved=self._focus).present(self)

    def _edit(self):
        item = self.context_item
        if item.kind == "server":
            ServerDialog(self.store, server=item.data, on_saved=self._focus).present(self)
        else:
            FolderDialog(self.store, folder=item.data, on_saved=self._focus).present(self)

    def _delete(self):
        item = self.context_item
        if item.kind == "folder":
            self._delete_folder(item.data)
            return
        dialog = Adw.AlertDialog(heading=f"Delete “{item.data['name']}”?",
                                 body="Its password will also be removed from the keyring.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response == "delete":
                self.store.delete_server(item.data)
                self.refresh()

        dialog.connect("response", on_response)
        dialog.present(self)

    def _delete_folder(self, folder):
        subtree = self.store.subtree(folder["id"])
        n_servers = sum(s["folder_id"] in subtree for s in self.store.servers)
        n_folders = len(subtree) - 1
        dialog = Adw.AlertDialog(heading=f"Delete “{folder['name']}”?")
        dialog.add_response("cancel", "Cancel")

        if not n_servers and not n_folders:
            dialog.set_body("The folder is empty.")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        else:
            parts = [f"{n} {word}{'' if n == 1 else 's'}"
                     for n, word in ((n_servers, "server"), (n_folders, "folder")) if n]
            dialog.set_body(f"It contains {' and '.join(parts)}. Move them to another folder "
                            "or delete them all, including saved passwords.")
            paths = [(None, "(Root)")] + self.store.folder_paths(exclude=folder["id"])
            ids = [p[0] for p in paths]
            target = Adw.ComboRow(title="Move Contents To",
                                  model=Gtk.StringList.new([p[1] for p in paths]))
            target.set_selected(ids.index(folder["parent_id"]) if folder["parent_id"] in ids else 0)
            group = Adw.PreferencesGroup()
            group.add(target)
            dialog.set_extra_child(group)
            dialog.add_response("move", "Move")
            dialog.add_response("delete-all", "Delete All")
            dialog.set_response_appearance("delete-all", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_response_appearance("move", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("move")

        def on_response(_d, response):
            if response == "cancel":
                return
            if response == "move":
                move_to = ids[target.get_selected()]
                self.store.delete_folder(folder, move_to=move_to)
                self.expanded.discard(folder["id"])
                self.expanded.update(self.store.ancestors(move_to))
            else:
                self.store.delete_folder(folder, delete_contents=True)
                self.expanded -= subtree
            self.refresh()

        dialog.connect("response", on_response)
        dialog.present(self)

    # --- model ---
    def _focus(self, obj):
        """Refresh and reveal obj (server or folder), expanding the folders that contain it."""
        parent = obj.get("folder_id", obj.get("parent_id"))
        self.expanded.update(self.store.ancestors(parent))
        self.search_entry.set_text("")
        self.refresh(focus_id=obj["id"])

    def refresh(self, focus_id=None):
        query = self.search_entry.get_text().strip().lower()
        # Replacing the model makes old rows notify expanded=False: ignore it.
        expanded = set(self.expanded)
        self._refreshing = True
        root = Gio.ListStore(item_type=Item)
        if query:
            paths = dict(self.store.folder_paths())
            for s in sorted(self.store.servers, key=lambda s: s["name"].lower()):
                if query in s["name"].lower() or query in s["address"].lower():
                    folder = paths.get(s["folder_id"])
                    sub = f"{connect.build_uri(s)}" + (f"  ·  {folder}" if folder else "")
                    root.append(Item("server", s, sub))
            tree = Gtk.TreeListModel.new(root, False, False, lambda *_: None)
        else:
            self._fill(root, None)
            tree = Gtk.TreeListModel.new(root, False, False, self._children)
        self.selection.set_model(tree)
        self.expanded = expanded
        self._refreshing = False

        # Restore expanded folders (expanding inserts rows, hence the while loop).
        focus_pos = None
        i = 0
        while i < tree.get_n_items():
            row = tree.get_item(i)
            item = row.get_item()
            if not query and item.kind == "folder" and item.data["id"] in self.expanded:
                row.set_expanded(True)
            if item.data["id"] == focus_id:
                focus_pos = i
            i += 1
        if focus_pos is not None:
            self.list_view.scroll_to(focus_pos, Gtk.ListScrollFlags.FOCUS | Gtk.ListScrollFlags.SELECT, None)

        empty = tree.get_n_items() == 0
        self.empty.set_title("No Results" if query else "No Servers")
        self.empty.set_description(None if query else "Use + to add a server or a folder")
        self.stack.set_visible_child_name("empty" if empty else "list")

    def _fill(self, store, folder_id):
        folders, servers = self.store.children(folder_id)
        for f in folders:
            store.append(Item("folder", f))
        for s in servers:
            store.append(Item("server", s, connect.build_uri(s)))

    def _children(self, item):
        if item.kind != "folder":
            return None
        store = Gio.ListStore(item_type=Item)
        self._fill(store, item.data["id"])
        return store

    # --- rows ---
    def _setup_row(self, _factory, list_item):
        icon = Gtk.Image()
        title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
        subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["dim-label", "caption"])
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        labels.append(title)
        labels.append(subtitle)
        box = Gtk.Box(spacing=10, margin_top=6, margin_bottom=6)
        box.append(icon)
        box.append(labels)
        expander = Gtk.TreeExpander(child=box)
        expander.parts = (icon, title, subtitle)
        expander.handler = None
        list_item.set_child(expander)

    def _bind_row(self, _factory, list_item):
        expander = list_item.get_child()
        row = list_item.get_item()
        item = row.get_item()
        icon, title, subtitle = expander.parts
        expander.set_list_row(row)
        title.set_text(item.data["name"])
        if item.kind == "folder":
            icon.set_from_icon_name("folder-symbolic")
            subtitle.set_visible(False)
            expander.handler = row.connect("notify::expanded", self._on_expanded, item)
        else:
            icon.set_from_icon_name("network-server-symbolic")
            subtitle.set_text(item.subtitle)
            subtitle.set_visible(True)
            expander.handler = None

    def _unbind_row(self, _factory, list_item):
        expander = list_item.get_child()
        if expander.handler:
            list_item.get_item().disconnect(expander.handler)
            expander.handler = None
        expander.set_list_row(None)

    def _on_expanded(self, row, _pspec, item):
        if self._refreshing:
            return
        if row.get_expanded():
            self.expanded.add(item.data["id"])
        else:
            self.expanded.discard(item.data["id"])

    def _on_right_click(self, _gesture, _n, x, y):
        picked = self.list_view.pick(x, y, Gtk.PickFlags.DEFAULT)
        expander = picked and (picked if isinstance(picked, Gtk.TreeExpander)
                               else picked.get_ancestor(Gtk.TreeExpander))
        if not expander or not expander.get_list_row():
            return
        item = expander.get_list_row().get_item()
        self.context_item = item
        self.menu_model.remove_all()
        if item.kind == "server":
            self.menu_model.append("Open", "win.ctx-open")
        else:
            self.menu_model.append("New Server Here", "win.ctx-new-server")
            self.menu_model.append("New Subfolder", "win.ctx-new-folder")
        self.menu_model.append("Edit", "win.ctx-edit")
        self.menu_model.append("Delete", "win.ctx-delete")
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        self.popover.set_pointing_to(rect)
        self.popover.popup()

    def _on_activate(self, _view, position):
        row = self.selection.get_model().get_item(position)
        item = row.get_item()
        if item.kind == "folder":
            row.set_expanded(not row.get_expanded())
        else:
            self.open_server(item.data)

    # --- connection ---
    def open_server(self, server):
        toast = Adw.Toast(title=f"Connecting to {server['name']}…", timeout=0)
        self.toasts.add_toast(toast)

        def done(error):
            toast.dismiss()
            if error:
                self.toasts.add_toast(Adw.Toast(title=f"{server['name']}: {error}", timeout=6))

        connect.open_server(server, self.store.get_password(server["id"]), self, done,
                            on_new_password=lambda pw: self.store.set_password(server, pw),
                            new_window=self.settings["open_in"] == "window")


class App(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.set_accels_for_action("win.preferences", ["<Control>comma"])

    def do_activate(self):
        win = self.get_active_window() or Window(self)
        win.present()


def main():
    return App().run(sys.argv)
