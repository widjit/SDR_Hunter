#!/usr/bin/env bash
#
# SDR Hunter — SoapySDR + SDR driver installer
#
# Installs the SoapySDR abstraction layer and per-device driver modules for:
#   RTL-SDR / NooElec, HackRF, BladeRF, LimeSDR, PlutoSDR, USRP (UHD),
#   SDRplay, Airspy
# plus the SoapySDR Python bindings and USB udev rules.
#
# Supports: Ubuntu/Debian (apt), Fedora (dnf), Arch (pacman), macOS (brew).
# Re-run safely; existing packages are skipped by the package manager.
#
set -euo pipefail

log()  { printf '\033[1;36m[*]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[+]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[!]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[x]\033[0m %s\n' "$*" >&2; }

detect_os() {
  if [[ "$(uname -s)" == "Darwin" ]]; then echo "macos"; return; fi
  if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    case "${ID:-}${ID_LIKE:-}" in
      *debian*|*ubuntu*) echo "debian" ;;
      *fedora*|*rhel*)   echo "fedora" ;;
      *arch*)            echo "arch" ;;
      *)                 echo "unknown" ;;
    esac
  else
    echo "unknown"
  fi
}

install_debian() {
  log "Installing SoapySDR + drivers via apt (sudo required)…"
  sudo apt-get update
  sudo apt-get install -y \
    soapysdr-tools libsoapysdr-dev python3-soapysdr \
    soapysdr-module-rtlsdr soapysdr-module-hackrf soapysdr-module-bladerf \
    soapysdr-module-lms7 soapysdr-module-plutosdr soapysdr-module-uhd \
    soapysdr-module-airspy soapysdr-module-audio \
    rtl-sdr hackrf bladerf limesuite \
    libusb-1.0-0-dev python3-pip || warn "Some apt packages failed; continuing."
  # SDRplay driver (SoapySDRPlay3) is not in apt — see note below.
  warn "SDRplay: install the API from https://www.sdrplay.com then build SoapySDRPlay3."
}

install_fedora() {
  log "Installing SoapySDR + drivers via dnf (sudo required)…"
  sudo dnf install -y \
    SoapySDR SoapySDR-devel python3-SoapySDR \
    SoapyRTLSDR SoapyHackRF SoapyUHD SoapyPlutoSDR SoapyAirspy \
    rtl-sdr hackrf uhd python3-pip libusbx-devel || warn "Some dnf packages failed."
  warn "BladeRF/LimeSDR/SDRplay Soapy modules may need building from source on Fedora."
}

install_arch() {
  log "Installing SoapySDR + drivers via pacman (sudo required)…"
  # Arch/CachyOS package names differ from Debian:
  #   uhd            -> libuhd                    (USRP host libs; Soapy module is soapyuhd)
  #   pyqt6-webengine-> python-pyqt6-webengine    (optional; powers the in-app map)
  #   portaudio      MUST be present before pip builds PyAudio
  sudo pacman -S --needed --noconfirm \
    soapysdr soapyrtlsdr soapyhackrf soapybladerf soapyuhd libuhd \
    rtl-sdr hackrf libusb portaudio \
    python-pyqt6 python-pyqt6-webengine || warn "Some pacman packages failed; continuing."
  warn "LimeSDR / PlutoSDR / SDRplay / Airspy Soapy modules are AUR-only (not in the"
  warn "official repos). Optional — if you use an AUR helper such as paru or yay:"
  warn "    paru -S soapylms7 soapysdrplay3 soapyairspy limesuite"
  warn "(exact AUR names vary; safe to skip if you don't have those SDRs)."
}

install_macos() {
  log "Installing SoapySDR + drivers via Homebrew…"
  brew update
  brew install soapysdr soapyrtlsdr soapyhackrf soapybladerf soapyremote \
    rtl-sdr hackrf || warn "Some brew packages failed."
  warn "LimeSDR/UHD/SDRplay/Airspy: install vendor SDKs then build Soapy modules."
}

install_udev() {
  [[ "$(uname -s)" == "Darwin" ]] && return 0
  log "Installing USB udev rules…"
  local rules="/etc/udev/rules.d/99-sdr-hunter.rules"
  sudo tee "$rules" >/dev/null <<'RULES'
# SDR Hunter USB device rules — grant plugdev access to common SDRs
# RTL-SDR / NooElec
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2838", GROUP="plugdev", MODE="0666"
SUBSYSTEM=="usb", ATTRS{idVendor}=="0bda", ATTRS{idProduct}=="2832", GROUP="plugdev", MODE="0666"
# HackRF
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6089", GROUP="plugdev", MODE="0666"
# BladeRF
SUBSYSTEM=="usb", ATTRS{idVendor}=="2cf0", GROUP="plugdev", MODE="0666"
# LimeSDR
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="6108", GROUP="plugdev", MODE="0666"
# Airspy
SUBSYSTEM=="usb", ATTRS{idVendor}=="1d50", ATTRS{idProduct}=="60a1", GROUP="plugdev", MODE="0666"
# SDRplay
SUBSYSTEM=="usb", ATTRS{idVendor}=="1df7", GROUP="plugdev", MODE="0666"
# ADALM-Pluto
SUBSYSTEM=="usb", ATTRS{idVendor}=="0456", ATTRS{idProduct}=="b673", GROUP="plugdev", MODE="0666"
RULES
  sudo udevadm control --reload-rules && sudo udevadm trigger || \
    warn "Could not reload udev rules; a reboot/replug may be needed."
  ok "udev rules installed at $rules"
}

print_venv_activation_hint() {
  local venv_dir="$1"
  echo
  ok "Python deps installed into virtualenv: $venv_dir"
  log "Activate it before running SDR Hunter — the command depends on your shell:"
  printf '    # bash / zsh:\n'
  printf '    source %s/bin/activate\n' "$venv_dir"
  printf '    # fish (CachyOS default shell):\n'
  printf '    source %s/bin/activate.fish\n' "$venv_dir"
  printf '    # ...or skip activation and call the venv Python directly:\n'
  printf '    %s/bin/python main.py --gui\n' "$venv_dir"
  warn "Seeing \"'case' builtin not inside of switch block\"? You are in fish but sourced"
  warn "the bash 'activate' script — use 'activate.fish' instead."
  warn "Do NOT use 'pip install --break-system-packages'; use this virtualenv instead."
}

install_python_deps() {
  local os="${1:-unknown}"
  local reqs; reqs="$(dirname "$0")/requirements.txt"

  if [[ "$os" == "arch" ]]; then
    # Arch/CachyOS enforce PEP 668: a system-wide `pip install` is blocked. Use a venv.
    log "Arch/CachyOS enforce PEP 668 — installing Python deps into a virtualenv…"
    local venv_dir; venv_dir="$(dirname "$0")/.venv"
    if [[ ! -d "$venv_dir" ]]; then
      # --system-site-packages so the venv can SEE the pacman-installed SoapySDR / PyQt6
      # bindings (those are system packages, not on PyPI).
      python -m venv --system-site-packages "$venv_dir" || {
        err "Failed to create virtualenv at $venv_dir"; return 1; }
      ok "Created virtualenv (with --system-site-packages) at $venv_dir"
    else
      log "Reusing existing virtualenv at $venv_dir"
    fi
    # This script runs under bash/sh, so activate with the POSIX script for its own installs.
    # shellcheck disable=SC1091
    source "$venv_dir/bin/activate"
    python -m pip install --upgrade pip
    python -m pip install -r "$reqs" || warn "Some Python requirements failed to install."
    deactivate 2>/dev/null || true
    print_venv_activation_hint "$venv_dir"
    return 0
  fi

  log "Installing Python package requirements…"
  local pip_bin="pip3"
  command -v pip3 >/dev/null 2>&1 || pip_bin="pip"
  "$pip_bin" install --upgrade pip
  "$pip_bin" install -r "$reqs" || \
    warn "Some Python requirements failed to install."
}

test_install() {
  log "Testing installation…"
  if command -v SoapySDRUtil >/dev/null 2>&1; then
    ok "SoapySDRUtil found. Probing available modules/devices:"
    SoapySDRUtil --info || true
    SoapySDRUtil --find || true
  else
    warn "SoapySDRUtil not found on PATH. SoapySDR may not have installed correctly."
    warn "SDR Hunter will still run using its synthetic mock device."
  fi
  python3 - <<'PY' || true
try:
    import SoapySDR
    print("[+] Python SoapySDR bindings import OK:", SoapySDR.getABIVersion())
except Exception as e:
    print("[!] Python SoapySDR bindings not importable:", e)
PY
}

main() {
  local os; os="$(detect_os)"
  log "Detected OS family: $os"
  case "$os" in
    debian) install_debian ;;
    fedora) install_fedora ;;
    arch)   install_arch ;;
    macos)  install_macos ;;
    *) err "Unsupported/unknown OS. Please install SoapySDR + drivers manually."; ;;
  esac
  install_udev || warn "udev rule installation skipped/failed."
  install_python_deps "$os"
  test_install
  ok "Done. If you just added udev rules, unplug/replug your SDR (or reboot)."
  ok "Add yourself to the 'plugdev' group if needed:  sudo usermod -aG plugdev \$USER"
  if [[ "$os" == "arch" ]]; then
    warn "Reminder (Arch/CachyOS): activate the .venv before running — fish users:"
    warn "    source .venv/bin/activate.fish   (bash/zsh: source .venv/bin/activate)"
  fi
}

main "$@"
