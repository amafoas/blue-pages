"""Persistence: server data in JSON, passwords in the GNOME keyring."""
import json
import os
import shutil
import uuid

import gi

gi.require_version("Secret", "1")
from gi.repository import Secret

from . import APP_ID, APP_NAME

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
CONFIG_DIR = os.path.join(CONFIG_HOME, "blue-pages")
DATA_FILE = os.path.join(CONFIG_DIR, "servers.json")

PROTOCOLS = ["smb", "sftp", "ftp", "dav", "davs", "afp", "nfs"]
EXPORT_VERSION = 1


def _schema(name):
    return Secret.Schema.new(
        name, Secret.SchemaFlags.NONE, {"id": Secret.SchemaAttributeType.STRING}
    )


SCHEMA = _schema(APP_ID)

# Pre-release name ("yp"); its data is migrated on first run.
LEGACY_DATA_FILE = os.path.join(CONFIG_HOME, "yp", "servers.json")
LEGACY_SCHEMA = _schema("ar.yp.Servers")


class Store:
    def __init__(self):
        self.folders = []  # {id, name, parent_id}
        self.servers = []  # {id, name, protocol, address, user, folder_id}
        self._migrate_legacy()
        self.load()

    # --- persistence ----------------------------------------------------
    def load(self):
        try:
            with open(DATA_FILE) as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        self.folders = data.get("folders", [])
        self.servers = data.get("servers", [])

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        tmp = DATA_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(
                {"folders": self.folders, "servers": self.servers}, f, indent=2
            )
        os.chmod(tmp, 0o600)
        os.replace(tmp, DATA_FILE)

    def _migrate_legacy(self):
        if os.path.exists(DATA_FILE) or not os.path.exists(LEGACY_DATA_FILE):
            return
        os.makedirs(CONFIG_DIR, exist_ok=True)
        shutil.copy2(LEGACY_DATA_FILE, DATA_FILE)
        self.load()
        for server in self.servers:
            attrs = {"id": server["id"]}
            password = Secret.password_lookup_sync(LEGACY_SCHEMA, attrs, None)
            if password:
                self.set_password(server, password)
                Secret.password_clear_sync(LEGACY_SCHEMA, attrs, None)

    # --- passwords ------------------------------------------------------
    def get_password(self, server_id):
        return Secret.password_lookup_sync(SCHEMA, {"id": server_id}, None) or ""

    def set_password(self, server, password):
        if password:
            Secret.password_store_sync(
                SCHEMA,
                {"id": server["id"]},
                Secret.COLLECTION_DEFAULT,
                f"{APP_NAME}: {server['name']}",
                password,
                None,
            )
        else:
            Secret.password_clear_sync(SCHEMA, {"id": server["id"]}, None)

    # --- queries --------------------------------------------------------
    def get_folder(self, folder_id):
        return next((f for f in self.folders if f["id"] == folder_id), None)

    def ancestors(self, folder_id):
        """folder_id and all of its parent folders."""
        out = []
        while folder_id and (folder := self.get_folder(folder_id)) and folder_id not in out:
            out.append(folder_id)
            folder_id = folder["parent_id"]
        return out

    def children(self, folder_id):
        """Folders and servers directly inside folder_id (None = root)."""
        folders = sorted(
            (f for f in self.folders if f["parent_id"] == folder_id),
            key=lambda f: f["name"].lower(),
        )
        servers = sorted(
            (s for s in self.servers if s["folder_id"] == folder_id),
            key=lambda s: s["name"].lower(),
        )
        return folders, servers

    def folder_paths(self, exclude=None):
        """[(id, 'A / B / C')] for every folder, skipping `exclude` and its descendants."""
        out = []

        def walk(parent, prefix):
            for f in self.children(parent)[0]:
                if f["id"] == exclude:
                    continue
                path = f"{prefix}{f['name']}"
                out.append((f["id"], path))
                walk(f["id"], path + " / ")

        walk(None, "")
        return out

    # --- changes --------------------------------------------------------
    def add_folder(self, name, parent_id):
        folder = {"id": uuid.uuid4().hex, "name": name, "parent_id": parent_id}
        self.folders.append(folder)
        self.save()
        return folder

    def update_folder(self, folder, name, parent_id):
        folder["name"] = name
        folder["parent_id"] = parent_id
        self.save()

    def subtree(self, folder_id):
        """IDs of folder_id and every folder nested inside it."""
        ids = {folder_id}
        for f in self.children(folder_id)[0]:
            ids |= self.subtree(f["id"])
        return ids

    def delete_folder(self, folder, delete_contents=False, move_to=None):
        """Delete the folder, either with everything inside it or moving its contents to move_to."""
        if delete_contents:
            ids = self.subtree(folder["id"])
            for s in [s for s in self.servers if s["folder_id"] in ids]:
                Secret.password_clear_sync(SCHEMA, {"id": s["id"]}, None)
                self.servers.remove(s)
            self.folders = [f for f in self.folders if f["id"] not in ids]
        else:
            for f in self.folders:
                if f["parent_id"] == folder["id"]:
                    f["parent_id"] = move_to
            for s in self.servers:
                if s["folder_id"] == folder["id"]:
                    s["folder_id"] = move_to
            self.folders.remove(folder)
        self.save()

    def add_server(self, data, password):
        server = {"id": uuid.uuid4().hex, **data}
        self.servers.append(server)
        self.set_password(server, password)
        self.save()
        return server

    def update_server(self, server, data, password):
        server.update(data)
        self.set_password(server, password)
        self.save()

    def delete_server(self, server):
        Secret.password_clear_sync(SCHEMA, {"id": server["id"]}, None)
        self.servers.remove(server)
        self.save()

    # --- export / import ------------------------------------------------
    def export(self, folder_id=None, server=None):
        """Everything, a folder with its contents, or one server, as a dict. Never passwords."""
        if server:
            folders, servers = [], [dict(server, folder_id=None)]
        elif folder_id:
            ids = self.subtree(folder_id)
            folders = [dict(f, parent_id=None) if f["id"] == folder_id else dict(f)
                       for f in self.folders if f["id"] in ids]
            servers = [dict(s) for s in self.servers if s["folder_id"] in ids]
        else:
            folders = [dict(f) for f in self.folders]
            servers = [dict(s) for s in self.servers]
        return {"app": APP_ID, "version": EXPORT_VERSION, "folders": folders, "servers": servers}

    def import_data(self, data, parent_id=None):
        """Add the folders and servers of an export inside parent_id (None = root), with new IDs.

        Returns (n_folders, n_servers).
        """
        if not isinstance(data, dict) or not isinstance(data.get("folders"), list) \
                or not isinstance(data.get("servers"), list):
            raise ValueError("Not a Blue Pages export")

        def text(d, key):
            value = d.get(key)
            return value.strip() if isinstance(value, str) else ""

        new_ids = {}
        folders = []
        for f in data["folders"]:
            if isinstance(f, dict) and text(f, "name") and isinstance(f.get("id"), str):
                new_ids[f["id"]] = uuid.uuid4().hex
                folders.append(f)
        folders = [{"id": new_ids[f["id"]], "name": text(f, "name"),
                    "parent_id": new_ids.get(text(f, "parent_id"))} for f in folders]
        # A folder that is its own ancestor would never be shown: move it to the root.
        parents = {f["id"]: f for f in folders}
        for f in folders:
            seen = set()
            node = f
            while node and node["parent_id"]:
                if node["id"] in seen:
                    node["parent_id"] = None
                    break
                seen.add(node["id"])
                node = parents.get(node["parent_id"])
        for f in folders:
            f["parent_id"] = f["parent_id"] or parent_id

        servers = []
        for s in data["servers"]:
            if not isinstance(s, dict) or not text(s, "name") or not text(s, "address"):
                continue
            protocol = text(s, "protocol")
            servers.append({
                "id": uuid.uuid4().hex,
                "name": text(s, "name"),
                "protocol": protocol if protocol in PROTOCOLS else "smb",
                "address": text(s, "address"),
                "user": text(s, "user"),
                "folder_id": new_ids.get(text(s, "folder_id")) or parent_id,
            })
        self.folders.extend(folders)
        self.servers.extend(servers)
        self.save()
        return len(folders), len(servers)
