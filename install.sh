#!/usr/bin/env bash
# Install or uninstall Blue Pages.
#
#   ./install.sh               install for the current user (~/.local)
#   sudo ./install.sh          install system-wide (/usr/local)
#   ./install.sh --uninstall   remove it (use sudo for a system-wide install)
#
# PREFIX=/some/path ./install.sh overrides the install location.
set -euo pipefail

APP_ID="io.github.amafoas.BluePages"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${PREFIX:-}" ]]; then
    if [[ $EUID -eq 0 ]]; then PREFIX=/usr/local; else PREFIX="$HOME/.local"; fi
fi
BINDIR="$PREFIX/bin"
LIBDIR="$PREFIX/share/blue-pages"
APPSDIR="$PREFIX/share/applications"
DESKTOP_FILE="$APPSDIR/$APP_ID.desktop"

refresh_desktop_db() {
    if command -v update-desktop-database >/dev/null; then
        update-desktop-database -q "$APPSDIR" || true
    fi
}

do_uninstall() {
    rm -f "$BINDIR/bp" "$DESKTOP_FILE"
    rm -rf "$LIBDIR"
    refresh_desktop_db
    echo "Blue Pages removed from $PREFIX."
    echo "Your servers are kept in ~/.config/blue-pages and passwords in the GNOME keyring."
}

check_dependencies() {
    if ! python3 - <<'EOF'
import sys
try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    gi.require_version("Secret", "1")
    from gi.repository import Adw, Gtk, Secret
except (ImportError, ValueError) as e:
    sys.exit(f"Missing dependency: {e}")
if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 5):
    sys.exit("libadwaita 1.5 or newer is required (Ubuntu 24.04+).")
if (Gtk.get_major_version(), Gtk.get_minor_version()) < (4, 12):
    sys.exit("GTK 4.12 or newer is required (Ubuntu 24.04+).")
EOF
    then
        echo
        echo "Install the dependencies with:"
        echo "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-secret-1 gvfs-backends"
        exit 1
    fi
}

do_install() {
    check_dependencies

    mkdir -p "$BINDIR" "$LIBDIR" "$APPSDIR"
    rm -rf "$LIBDIR/blue_pages"
    cp -r "$SRC_DIR/blue_pages" "$LIBDIR/"
    find "$LIBDIR" -name __pycache__ -type d -prune -exec rm -rf {} +
    install -m 755 "$SRC_DIR/bp" "$LIBDIR/bp"
    ln -sf "$LIBDIR/bp" "$BINDIR/bp"

    sed "s|@BINDIR@|$BINDIR|" "$SRC_DIR/data/$APP_ID.desktop.in" > "$DESKTOP_FILE"
    chmod 644 "$DESKTOP_FILE"
    refresh_desktop_db

    echo "Blue Pages installed in $PREFIX."
    case ":$PATH:" in
        *":$BINDIR:"*) echo "Run it with 'bp' or from the app menu." ;;
        *) echo "Open it from the app menu. To use 'bp' from a terminal, add $BINDIR to your PATH (or log out and back in)." ;;
    esac
}

case "${1:-}" in
    "") do_install ;;
    --uninstall) do_uninstall ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//' ;;
    *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
esac
