#!/bin/bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "usage: $0 <destination-dir>" >&2
  exit 1
fi

DEST="$1"
VERSION="${PYTHON_VERSION:-3.12.10}"
MAJOR_MINOR_VERSION="${VERSION%.*}"
PKG_URL="https://www.python.org/ftp/python/${VERSION}/python-${VERSION}-macos11.pkg"
TMP_DIR="$(mktemp -d)"
PKG_PATH="$TMP_DIR/python.pkg"
EXPANDED_DIR="$TMP_DIR/expanded"
PAYLOAD_ROOT="$TMP_DIR/payload"

cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

mkdir -p "$DEST" "$PAYLOAD_ROOT"

if [[ -n "${PYTHON_PKG_PATH:-}" ]]; then
  PKG_PATH="$(cd "$(dirname "$PYTHON_PKG_PATH")" && pwd)/$(basename "$PYTHON_PKG_PATH")"
  echo "Using Python ${VERSION} installer at $PKG_PATH..." >&2
else
  echo "Downloading Python ${VERSION} installer from python.org..." >&2
  curl -L "$PKG_URL" -o "$PKG_PATH"
fi

pkgutil --expand-full "$PKG_PATH" "$EXPANDED_DIR"

PAYLOAD_PATH="$(find "$EXPANDED_DIR" -path '*Python_Framework.pkg/Payload' | head -n 1)"
if [[ -z "$PAYLOAD_PATH" ]]; then
  echo "Could not locate Python framework payload inside the installer." >&2
  exit 1
fi

if [[ -d "$PAYLOAD_PATH" ]]; then
  /usr/bin/ditto "$PAYLOAD_PATH" "$PAYLOAD_ROOT/Python.framework"
elif file "$PAYLOAD_PATH" | grep -qi 'gzip compressed'; then
  (cd "$PAYLOAD_ROOT" && gzip -dc "$PAYLOAD_PATH" | cpio -idm >/dev/null 2>&1)
else
  (cd "$PAYLOAD_ROOT" && cat "$PAYLOAD_PATH" | cpio -idm >/dev/null 2>&1)
fi

FRAMEWORK_PATH="$(find "$PAYLOAD_ROOT" -path '*Python.framework' -type d | head -n 1)"
if [[ -z "$FRAMEWORK_PATH" ]]; then
  echo "Python.framework was not found after unpacking the installer payload." >&2
  exit 1
fi

rm -rf "$DEST/Python.framework" "$DEST/bin"
/usr/bin/ditto "$FRAMEWORK_PATH" "$DEST/Python.framework"
mkdir -p "$DEST/bin"
ln -sfn "../Python.framework/Versions/${MAJOR_MINOR_VERSION}/bin/python3.12" "$DEST/bin/python3.12"
ln -sfn "../Python.framework/Versions/${MAJOR_MINOR_VERSION}/bin/python3" "$DEST/bin/python3"

VERSION_ROOT="$DEST/Python.framework/Versions/${MAJOR_MINOR_VERSION}"
PYTHON_APP_EXECUTABLE="$VERSION_ROOT/Resources/Python.app/Contents/MacOS/Python"

if [[ ! -e "$VERSION_ROOT/Headers" ]]; then
  echo "The staged Python.framework is missing Versions/${MAJOR_MINOR_VERSION}/Headers." >&2
  exit 1
fi

if [[ ! -x "$PYTHON_APP_EXECUTABLE" ]]; then
  echo "The staged Python.framework is missing the embedded Python.app launcher." >&2
  exit 1
fi

rewrite_dependency() {
  local binary_path="$1"
  local source_path="$2"
  local target_path="$3"

  if [[ -e "$binary_path" ]]; then
    install_name_tool -change "$source_path" "$target_path" "$binary_path"
  fi
}

ad_hoc_sign() {
  local path="$1"

  if [[ -e "$path" ]]; then
    codesign --force --sign - "$path" >/dev/null 2>&1
  fi
}

install_name_tool -id "@rpath/Python.framework/Versions/${MAJOR_MINOR_VERSION}/Python" "$VERSION_ROOT/Python"
rewrite_dependency \
  "$VERSION_ROOT/bin/python3" \
  "/Library/Frameworks/Python.framework/Versions/${MAJOR_MINOR_VERSION}/Python" \
  "@loader_path/../Python"
rewrite_dependency \
  "$VERSION_ROOT/bin/python3.12" \
  "/Library/Frameworks/Python.framework/Versions/${MAJOR_MINOR_VERSION}/Python" \
  "@loader_path/../Python"
rewrite_dependency \
  "$VERSION_ROOT/bin/python3-intel64" \
  "/Library/Frameworks/Python.framework/Versions/${MAJOR_MINOR_VERSION}/Python" \
  "@loader_path/../Python"
rewrite_dependency \
  "$VERSION_ROOT/bin/python3.12-intel64" \
  "/Library/Frameworks/Python.framework/Versions/${MAJOR_MINOR_VERSION}/Python" \
  "@loader_path/../Python"
rewrite_dependency \
  "$PYTHON_APP_EXECUTABLE" \
  "/Library/Frameworks/Python.framework/Versions/${MAJOR_MINOR_VERSION}/Python" \
  "@executable_path/../../../../Python"

ad_hoc_sign "$VERSION_ROOT/Python"
ad_hoc_sign "$VERSION_ROOT/bin/python3"
ad_hoc_sign "$VERSION_ROOT/bin/python3.12"
ad_hoc_sign "$VERSION_ROOT/bin/python3-intel64"
ad_hoc_sign "$VERSION_ROOT/bin/python3.12-intel64"
ad_hoc_sign "$VERSION_ROOT/Resources/Python.app"

echo "Embedded Python staged at $DEST" >&2
