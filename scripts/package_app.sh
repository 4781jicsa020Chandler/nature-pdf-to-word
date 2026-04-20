#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PRODUCT_NAME="NaturePDFToWord"
APP_NAME="Nature PDF to Word"
DIST_DIR="$ROOT/dist"
BUILD_DIR="$ROOT/.build/release"
APP_DIR="$DIST_DIR/$APP_NAME.app"
RESOURCES_BUNDLE="$BUILD_DIR/${PRODUCT_NAME}_${PRODUCT_NAME}.bundle"
ZIP_PATH="$DIST_DIR/NaturePDFToWord-arm64.zip"

rm -rf "$APP_DIR" "$ZIP_PATH"
mkdir -p "$APP_DIR/Contents/MacOS" "$APP_DIR/Contents/Resources" "$APP_DIR/Contents/Frameworks" "$DIST_DIR"

swift build -c release

cp "$BUILD_DIR/$PRODUCT_NAME" "$APP_DIR/Contents/MacOS/$PRODUCT_NAME"
if [[ -d "$RESOURCES_BUNDLE" ]]; then
  cp -R "$RESOURCES_BUNDLE" "$APP_DIR/Contents/Resources/"
fi

cat > "$APP_DIR/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>en</string>
  <key>CFBundleExecutable</key>
  <string>NaturePDFToWord</string>
  <key>CFBundleIdentifier</key>
  <string>research.wku.NaturePDFToWord</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>Nature PDF to Word</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>14.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

if [[ "${STAGE_EMBEDDED_PYTHON:-1}" == "1" ]]; then
  "$ROOT/scripts/stage_python_runtime.sh" "$APP_DIR/Contents/Resources/EmbeddedPython"
fi

xattr -cr "$APP_DIR" 2>/dev/null || true

(
  cd "$DIST_DIR"
  COPYFILE_DISABLE=1 /usr/bin/ditto -c -k --norsrc --keepParent "$APP_NAME.app" "$(basename "$ZIP_PATH")"
)
echo "Created $ZIP_PATH"
