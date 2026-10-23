"""Mounts a server through GVfs (like Nautilus > Other Locations) and opens it."""
from urllib.parse import quote

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib

# Keeps MountOperations alive while the mount is in progress.
_pending = set()


def build_uri(server):
    address = server["address"].strip().lstrip("/")
    if "://" in address:
        return address
    user = server.get("user", "").strip()
    # For SMB the user (and domain) go through the MountOperation; otherwise in the URI.
    if user and server["protocol"] != "smb" and "@" not in address.split("/")[0]:
        address = f"{quote(user, safe='')}@{address}"
    return f"{server['protocol']}://{address}"


def _split_domain(user):
    if "\\" in user:
        domain, user = user.split("\\", 1)
        return domain, user
    return None, user


def _launch_default(uri):
    app = Gio.AppInfo.get_default_for_type("inode/directory", False)
    if app is not None:
        app.launch_uris([uri], None)
    else:
        Gio.AppInfo.launch_default_for_uri(uri, None)


def open_in_files(uri, new_window=True):
    """Open uri in Nautilus, as a new window or as a new tab of an open window.

    Nautilus' .desktop file always passes --new-window, so we talk to it over
    D-Bus instead: without the "new-window" hint it opens a tab in the active
    window. Falls back to the default file manager if Nautilus isn't available.
    """
    platform_data = {}
    token = Gdk.Display.get_default().get_app_launch_context().get_startup_notify_id(None, [])
    if token:
        # Lets Nautilus raise its window instead of being blocked by focus-stealing prevention.
        platform_data["activation-token"] = GLib.Variant("s", token)
        platform_data["desktop-startup-id"] = GLib.Variant("s", token)

    def on_reply(bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error:
            _launch_default(uri)

    Gio.bus_get_sync(Gio.BusType.SESSION).call(
        "org.gnome.Nautilus",
        "/org/gnome/Nautilus",
        "org.gtk.Application",
        "Open",
        GLib.Variant("(assa{sv})", ([uri], "new-window" if new_window else "", platform_data)),
        None,
        Gio.DBusCallFlags.NONE,
        -1,
        None,
        on_reply,
    )


def _ask_password_dialog(parent, name, message, on_reply):
    """Ask for the password when the saved one fails. on_reply(password | None, save)."""
    dialog = Adw.AlertDialog(heading=f"Could not log in to {name}", body=message.strip())
    entry = Adw.PasswordEntryRow(title="Password")
    save = Adw.SwitchRow(title="Save this password", active=True)
    group = Adw.PreferencesGroup()
    group.add(entry)
    group.add(save)
    dialog.set_extra_child(group)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("connect", "Connect")
    dialog.set_response_appearance("connect", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("connect")
    dialog.set_close_response("cancel")
    replied = []

    def on_reply_once(response):
        if replied:
            return
        replied.append(response)
        if response == "connect":
            on_reply(entry.get_text(), save.get_active())
        else:
            on_reply(None, False)

    def on_activated(*_):
        on_reply_once("connect")
        dialog.close()

    entry.connect("entry-activated", on_activated)
    dialog.connect("response", lambda _d, r: on_reply_once(r))
    dialog.present(parent)
    entry.grab_focus()


def _ask_question_dialog(parent, message, choices, on_reply):
    """GVfs questions (e.g. unknown SSH host key). on_reply(index | None)."""
    lines = message.strip().split("\n", 1)
    dialog = Adw.AlertDialog(heading=lines[0], body=lines[1].strip() if len(lines) > 1 else "")
    for i, choice in enumerate(choices):
        dialog.add_response(str(i), choice)
    dialog.set_close_response("close")
    dialog.connect("response", lambda _d, r: on_reply(None if r == "close" else int(r)))
    dialog.present(parent)


def open_server(server, password, parent, on_done, on_new_password=None, new_window=True):
    """Mount and open the server. on_done(error_msg | None) is called when finished."""
    uri = build_uri(server)
    domain, user = _split_domain(server.get("user", "").strip())
    op = Gio.MountOperation()
    _pending.add(op)
    attempts = {"n": 0}

    def fill(op, flags, default_user, default_domain, pw):
        if flags & Gio.AskPasswordFlags.NEED_USERNAME:
            op.set_username(user or default_user)
        if flags & Gio.AskPasswordFlags.NEED_DOMAIN:
            op.set_domain(domain or default_domain or "")
        if flags & Gio.AskPasswordFlags.NEED_PASSWORD:
            op.set_password(pw)
        op.set_password_save(Gio.PasswordSave.NEVER)
        op.reply(Gio.MountOperationResult.HANDLED)

    def on_ask_password(op, message, default_user, default_domain, flags):
        attempts["n"] += 1
        if attempts["n"] == 1 and (password or not flags & Gio.AskPasswordFlags.NEED_PASSWORD):
            if flags & Gio.AskPasswordFlags.ANONYMOUS_SUPPORTED and not user:
                op.set_anonymous(True)
            fill(op, flags, default_user, default_domain, password)
            return

        def on_reply(new_password, save):
            if new_password is None:
                op.reply(Gio.MountOperationResult.ABORTED)
                return
            if save and on_new_password:
                on_new_password(new_password)
            fill(op, flags, default_user, default_domain, new_password)

        _ask_password_dialog(parent, server["name"], message, on_reply)

    def on_ask_question(op, message, choices):
        def on_reply(choice):
            if choice is None:
                op.reply(Gio.MountOperationResult.ABORTED)
            else:
                op.set_choice(choice)
                op.reply(Gio.MountOperationResult.HANDLED)

        _ask_question_dialog(parent, message, choices, on_reply)

    op.connect("ask-password", on_ask_password)
    op.connect("ask-question", on_ask_question)

    def on_mounted(file, result):
        _pending.discard(op)
        try:
            file.mount_enclosing_volume_finish(result)
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.ALREADY_MOUNTED):
                pass
            elif e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.FAILED_HANDLED):
                on_done("Connection cancelled")
                return
            else:
                on_done(e.message)
                return
        try:
            open_in_files(uri, new_window)
        except GLib.Error as e:
            on_done(e.message)
            return
        on_done(None)

    Gio.File.new_for_uri(uri).mount_enclosing_volume(
        Gio.MountMountFlags.NONE, op, None, on_mounted
    )
