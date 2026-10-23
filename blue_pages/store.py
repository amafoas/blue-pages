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

    def delete_folder(self, folder):
        """Delete the folder; its contents move to the parent folder."""
        parent = folder["parent_id"]
        for f in self.folders:
            if f["parent_id"] == folder["id"]:
                f["parent_id"] = parent
        for s in self.servers:
            if s["folder_id"] == folder["id"]:
                s["folder_id"] = parent
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
