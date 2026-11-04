#!/usr/bin/env bash
# Install Phone Cam for the current user.
#   ./install.sh               install missing dependencies (asks first), then the app
#   ./install.sh --no-deps     install only the app
#   ./install.sh --uninstall   remove the app (dependencies are left alone)
set -e
BIN="$HOME/.local/bin/phonecam-gui"
DESKTOP="$HOME/.local/share/applications/phonecam-gui.desktop"
HERE="$(cd "$(dirname "$0")" && pwd)"

if [ "$1" = "--uninstall" ]; then
  rm -f "$BIN" "$DESKTOP"
  echo "Removed Phone Cam."
  exit 0
fi

# ---------------------------------------------------------------- dependencies
# What the app needs, and nothing else:
#   scrcpy        streams the phone camera/mic (version 3.0 or newer)
#   adb           talks to the phone over USB / Wi-Fi
#   v4l2loopback  kernel module that creates the "Phone Cam" virtual webcam
#   pactl         creates the "PhoneMic" virtual microphone (PipeWire/PulseAudio)
#   PySide6       Qt for Python, draws the window
missing() {
  local m=()
  command -v scrcpy >/dev/null || m+=(scrcpy)
  command -v adb    >/dev/null || m+=(adb)
  command -v pactl  >/dev/null || m+=(pactl)
  modinfo v4l2loopback >/dev/null 2>&1 || m+=(v4l2loopback)
  python3 -c "import PySide6.QtWidgets" 2>/dev/null || m+=(PySide6)
  echo "${m[@]}"
}

install_deps() {
  local pm cmd
  if   command -v apt-get >/dev/null; then pm=apt
  elif command -v dnf     >/dev/null; then pm=dnf
  elif command -v pacman  >/dev/null; then pm=pacman
  elif command -v zypper  >/dev/null; then pm=zypper
  fi
  case $pm in
    apt)    cmd="sudo apt-get install -y scrcpy adb v4l2loopback-dkms pulseaudio-utils python3-pyside6.qtwidgets" ;;
    dnf)    cmd="sudo dnf install -y scrcpy android-tools v4l2loopback pulseaudio-utils python3-pyside6" ;;
    pacman) cmd="sudo pacman -S --needed scrcpy android-tools v4l2loopback-dkms libpulse pyside6" ;;
    zypper) cmd="sudo zypper install -y scrcpy android-tools v4l2loopback-kmp-default pulseaudio-utils python3-pyside6" ;;
    *) echo "Unknown package manager - please install the packages listed in README.md yourself."; return 1 ;;
  esac
  echo "Missing: $*"
  echo "This will run:"
  echo "  $cmd"
  read -rp "Install them now? [Y/n] " ok
  case $ok in [nN]*) return 1 ;; esac
  $cmd
  if [ "$pm" = dnf ] && ! command -v scrcpy >/dev/null; then
    echo "On Fedora, scrcpy and v4l2loopback come from COPR / RPM Fusion - see README.md."
  fi
}

if [ "$1" != "--no-deps" ]; then
  need=$(missing)
  if [ -n "$need" ]; then
    install_deps $need || echo "Skipping dependencies."
    need=$(missing)
    [ -n "$need" ] && echo "Still missing: $need (see README.md). The app will tell you what's wrong."
  fi
  if command -v scrcpy >/dev/null; then
    v=$(scrcpy --version 2>/dev/null | head -1 | grep -oE '[0-9]+(\.[0-9]+)*' | head -1)
    major=${v%%.*}
    if [ -n "$major" ] && [ "$major" -lt 3 ]; then
      echo "Warning: scrcpy $v is too old (need 3.0+). Get a newer one from https://github.com/Genymobile/scrcpy/releases"
    fi
  fi
fi

# ---------------------------------------------------------------- the app
install -Dm755 "$HERE/phonecam.py" "$BIN"
install -Dm644 "$HERE/phonecam-gui.desktop" "$DESKTOP"
sed -i "s|^Exec=.*|Exec=$BIN|" "$DESKTOP"
command -v update-desktop-database >/dev/null && update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo
echo "Installed. Open 'Phone Cam' from your app menu, or run: $BIN"
