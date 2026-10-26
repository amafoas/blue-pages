# Blue Pages

A minimal address book for network servers on GNOME. Save your servers, organize them
in folders, and click one to open it in Files (Nautilus), just like
*Other Locations → Connect to Server*, but without typing the address and
credentials every time.

## Features

- Save servers with name, protocol, address, user and password.
- Protocols: SMB, SFTP, FTP, WebDAV (`dav`/`davs`), AFP and NFS.
- Nested folders to keep things organized.
- Search by name or address.
- Passwords are stored in the GNOME keyring, never in plain text.
- If a saved password is rejected, you are asked for a new one and can update it.

## Requirements

- Ubuntu 24.04 or newer (or any distro with GTK 4.12+ and libadwaita 1.5+)
- GNOME Files (Nautilus) and GVfs

```sh
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1 gvfs-backends
```

## Installation

```sh
git clone https://github.com/amafoas/blue-pages.git
cd blue-pages
./install.sh
```

This installs Blue Pages for your user in `~/.local`. It appears in the app menu
and runs from a terminal with `bp`. To install it for all users, run `sudo ./install.sh`
instead (it installs to `/usr/local`).

To run it without installing: `./bp`

### Uninstall

```sh
./install.sh --uninstall        # or: sudo ./install.sh --uninstall
```

Your saved servers and passwords are not deleted.

## Usage

- **+** adds a server or a folder.
- **Click** a server to mount it and open it in Files. Click a folder to expand it.
- **Right-click** a row to edit or delete it, or to create a server or subfolder inside a folder.
  On a server, **Open SSH Terminal** opens a terminal running `ssh user@host` (the port in the
  address is used only for SFTP servers; the password is typed in the terminal). The terminal
  is titled `folder · server · host`.
- **Export…** (right-click a server or folder, or **Export All…** in the main menu) saves the
  servers and folders to a JSON file. Passwords are never exported.
- **Import…** (main menu, or **Import Here…** when right-clicking a folder to import inside it)
  adds the servers and folders of an exported file, without replacing the ones you already have. Passwords have to be entered again.
- Start typing (or use the search button) to filter servers.
- **Preferences** (main menu or <kbd>Ctrl</kbd>+<kbd>,</kbd>): choose whether servers open
  in a new Files window or in a new tab of the window that is already open, and whether the
  SSH terminal keeps its title instead of letting the server rename it (off by default).

**Address** is a host, optionally followed by a path: `fileserver.local`,
`fileserver.local/shared`, `192.168.1.10:2222`. For SMB, the user can include a domain:
`DOMAIN\user`.

## Where data is stored

- Servers and folders: `~/.config/blue-pages/servers.json`
- Passwords: GNOME keyring, listed in *Passwords and Keys* (Seahorse) as `Blue Pages: <name>`

## Troubleshooting

**A wrong saved password still logs in.** GVfs tries credentials remembered by Files
(and SSH keys, for SFTP) before asking Blue Pages. If you once connected through Files
and chose to remember the password, that one is used. Remove it in *Passwords and Keys*
if you want the password from Blue Pages to be used.
