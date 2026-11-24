#!/usr/bin/env bash
set -u

# Read-only suitability check for a prospective Everything Hub Cloud host.
# Does not install packages, change settings, inspect user files, or use the network.

section() { printf '\n== %s ==\n' "$1"; }

section "Operating system"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  printf 'OS: %s\n' "${PRETTY_NAME:-unknown}"
else
  printf 'OS: unknown (no /etc/os-release)\n'
fi
printf 'Kernel: %s\n' "$(uname -srmo 2>/dev/null || uname -a)"

section "CPU"
if command -v lscpu >/dev/null 2>&1; then
  lscpu | awk -F: '/^(Architecture|CPU\(s\)|Model name|Virtualization):/ {gsub(/^[ \t]+/, "", $2); print $1 ": " $2}'
else
  printf 'Logical CPUs: %s\n' "$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo unknown)"
fi
if grep -qm1 -E '(^| )(vmx|svm)( |$)' /proc/cpuinfo 2>/dev/null; then
  echo "Hardware virtualization flag: present"
else
  echo "Hardware virtualization flag: not detected (may be hidden by firmware or VM)"
fi

section "Memory"
if command -v free >/dev/null 2>&1; then
  free -h | awk 'NR==1 || /^Mem:/ {print}'
else
  awk '/MemTotal/ {printf "Total RAM: %.1f GiB\n", $2/1048576}' /proc/meminfo 2>/dev/null || true
fi

section "Block devices (names, sizes, types only)"
if command -v lsblk >/dev/null 2>&1; then
  lsblk -d -o NAME,SIZE,ROTA,TYPE,MODEL 2>/dev/null || lsblk -d 2>/dev/null || true
else
  echo "lsblk not installed"
fi

section "Filesystem capacity"
df -hT / 2>/dev/null || true

section "Network interfaces (no addresses)"
if command -v ip >/dev/null 2>&1; then
  ip -brief link 2>/dev/null || true
else
  echo "ip command not installed"
fi

section "KVM availability"
if [ -e /dev/kvm ]; then
  ls -l /dev/kvm
else
  echo "/dev/kvm not present"
fi
if lsmod 2>/dev/null | grep -E '^(kvm|kvm_intel|kvm_amd)\b'; then
  :
else
  echo "KVM kernel module not currently loaded (not necessarily a blocker)"
fi

section "Read-only result"
echo "This report is informational only. Do not install a hypervisor until disks, backups, firmware settings, and physical access are reviewed."
