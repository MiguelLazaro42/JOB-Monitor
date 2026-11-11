#!/usr/bin/env bash
# ==============================================================================
#  SITAI JOB-Monitor — Instalador Nativo macOS (.app + LaunchAgent + Permisos)
#  Construye ~/Applications/JOB-Monitor Agent.app firmada localmente (ad-hoc),
#  configura auto-inicio al encender el Mac y solicita permisos TCC nativos:
#    - Grabacion de pantalla y audio del sistema (evita pantalla gris)
#    - Accesibilidad (control remoto de mouse y teclado)
#    - Microfono / Audio
# ==============================================================================
set -e

cd "$(dirname "$0")"
SRC_DIR="$(pwd)"

# Limpiar atributos de cuarentena de Gatekeeper en los archivos fuente
xattr -cr "$SRC_DIR" 2>/dev/null || true

INSTALL_DIR="$HOME/.local/share/MonitoreoEmpresa"
APPS_DIR="$HOME/Applications"
APP_BUNDLE="$APPS_DIR/JOB-Monitor Agent.app"
APP_MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
APP_EXEC="$APP_MACOS_DIR/JOBMonitorAgent"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_LABEL="mx.sitai.jobmonitor.agent"
PLIST_PATH="$PLIST_DIR/$PLIST_LABEL.plist"
STATUS_JSON="$INSTALL_DIR/permissions_status.json"
CURRENT_UID=$(id -u)

echo "===================================================================="
echo "  SITAI JOB-Monitor — Instalador de Agente Nativo para macOS"
echo "===================================================================="
echo ""

if ! command -v python3 >/dev/null 2>&1; then
    echo "[ERROR] No se encontro Python 3 en este Mac."
    echo "Ejecuta 'xcode-select --install' o descarga Python 3 desde https://www.python.org/downloads/macos/"
    if [ -e /dev/tty ]; then
        read -r -p "Presiona Enter para salir..." </dev/tty || true
    fi
    exit 1
fi

# Detener instancia previa si existia
launchctl bootout "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true
launchctl unload "$PLIST_PATH" >/dev/null 2>&1 || true

echo "[1/5] Preparando directorio de trabajo en $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR" "$APPS_DIR" "$PLIST_DIR"

cp -f "$SRC_DIR/agent.py" "$INSTALL_DIR/agent.py"
cp -f "$SRC_DIR/requirements-agent.txt" "$INSTALL_DIR/requirements-agent.txt"
if [ -f "$SRC_DIR/config_agent.json" ]; then
    # Si ya habia un agent_id previo, conservarlo actualizando URL/token/tenant/alias nuevos
    if [ -f "$INSTALL_DIR/config_agent.json" ]; then
        python3 - "$INSTALL_DIR/config_agent.json" "$SRC_DIR/config_agent.json" <<'PYMERGE'
import json, sys
from pathlib import Path
old_p, new_p = Path(sys.argv[1]), Path(sys.argv[2])
try:
    old_cfg = json.loads(old_p.read_text(encoding="utf-8"))
except Exception:
    old_cfg = {}
new_cfg = json.loads(new_p.read_text(encoding="utf-8"))
if old_cfg.get("agent_id") and not new_cfg.get("agent_id"):
    new_cfg["agent_id"] = old_cfg["agent_id"]
if old_cfg.get("alias") and not new_cfg.get("alias"):
    new_cfg["alias"] = old_cfg["alias"]
old_p.write_text(json.dumps(new_cfg, indent=2, ensure_ascii=False), encoding="utf-8")
PYMERGE
    else
        cp -f "$SRC_DIR/config_agent.json" "$INSTALL_DIR/config_agent.json"
    fi
fi
if [ -f "$SRC_DIR/agent_client.crt" ]; then
    cp -f "$SRC_DIR/agent_client.crt" "$INSTALL_DIR/agent_client.crt"
fi
if [ -f "$SRC_DIR/agent_client.key" ]; then
    cp -f "$SRC_DIR/agent_client.key" "$INSTALL_DIR/agent_client.key"
    chmod 600 "$INSTALL_DIR/agent_client.key"
fi
xattr -cr "$INSTALL_DIR" 2>/dev/null || true

echo "[2/5] Configurando entorno virtual e instalando dependencias..."
if [ ! -d "$INSTALL_DIR/.venv" ]; then
    python3 -m venv "$INSTALL_DIR/.venv"
fi
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip -q
"$INSTALL_DIR/.venv/bin/pip" install -r "$INSTALL_DIR/requirements-agent.txt" -q

echo "[3/5] Construyendo aplicacion nativa '$APP_BUNDLE'..."
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_MACOS_DIR" "$APP_BUNDLE/Contents/Resources"

cat > "$APP_BUNDLE/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key>
    <string>$PLIST_LABEL</string>
    <key>CFBundleName</key>
    <string>JOB-Monitor Agent</string>
    <key>CFBundleDisplayName</key>
    <string>JOB-Monitor Agent</string>
    <key>CFBundleExecutable</key>
    <string>JOBMonitorAgent</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>2.0</string>
    <key>CFBundleVersion</key>
    <string>2.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSScreenCaptureUsageDescription</key>
    <string>JOB-Monitor requiere permiso de Grabacion de Pantalla y Audio del Sistema para transmitir imagen en tiempo real sin pantalla gris.</string>
    <key>NSMicrophoneUsageDescription</key>
    <string>JOB-Monitor utiliza funciones de audio para soporte y asistencia remota.</string>
    <key>NSAppleEventsUsageDescription</key>
    <string>JOB-Monitor supervisa la actividad del equipo corporativo.</string>
</dict>
</plist>
EOF

PYHOME=$(python3 -c 'import sys; print(sys.base_prefix)')
PYLIB=$(python3 -c '
import sys, sysconfig, pathlib
p = pathlib.Path(sys.base_prefix)
ld = sysconfig.get_config_var("LDLIBRARY") or ""
cands = [
    p / "Python3",
    p / "Python",
    p / "lib" / f"libpython{sys.version_info.major}.{sys.version_info.minor}.dylib",
    p / ld if ld else p / "nonexistent",
]
found = next((str(x.resolve()) for x in cands if x.exists()), "")
print(found)
')
SITEPKG=$("$INSTALL_DIR/.venv/bin/python" -c 'import site; print(site.getsitepackages()[0])')

BUILT_NATIVE=0
if [ -n "$PYLIB" ] && xcode-select -p >/dev/null 2>&1 && command -v clang >/dev/null 2>&1; then
    LAUNCHER_M="$INSTALL_DIR/_launcher_build.m"
    cat > "$LAUNCHER_M" <<EOF
#import <Foundation/Foundation.h>
#import <CoreGraphics/CoreGraphics.h>
#import <ApplicationServices/ApplicationServices.h>
#import <AVFoundation/AVFoundation.h>
#include <dlfcn.h>
#include <unistd.h>
#include <stdlib.h>
#include <string.h>

typedef int (*Py_BytesMain_t)(int, char **);

static void write_status_json(const char *path, BOOL screenOk, BOOL axOk, BOOL micOk) {
    NSDictionary *dict = @{
        @"screen": @(screenOk),
        @"accessibility": @(axOk),
        @"microphone": @(micOk)
    };
    NSData *data = [NSJSONSerialization dataWithJSONObject:dict options:NSJSONWritingPrettyPrinted error:nil];
    if (data) {
        [data writeToFile:[NSString stringWithUTF8String:path] atomically:YES];
    }
}

int main(int argc, char **argv) {
    @autoreleasepool {
        for (int i = 1; i < argc; i++) {
            if (strcmp(argv[i], "--check-screen-only") == 0) {
                return CGPreflightScreenCaptureAccess() ? 0 : 2;
            }
            if (strcmp(argv[i], "--check-permissions") == 0) {
                BOOL screenOk = CGPreflightScreenCaptureAccess();
                BOOL axOk = AXIsProcessTrusted();
                AVAuthorizationStatus micSt = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
                write_status_json("$STATUS_JSON", screenOk, axOk, micSt == AVAuthorizationStatusAuthorized);
                return 0;
            }
            if (strcmp(argv[i], "--request-permissions") == 0) {
                BOOL screenOk = CGPreflightScreenCaptureAccess();
                if (!screenOk) {
                    screenOk = CGRequestScreenCaptureAccess();
                }
                NSDictionary *axOpts = @{(__bridge NSString *)kAXTrustedCheckOptionPrompt: @YES};
                BOOL axOk = AXIsProcessTrustedWithOptions((__bridge CFDictionaryRef)axOpts);

                AVAuthorizationStatus micSt = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeAudio];
                __block BOOL micOk = (micSt == AVAuthorizationStatusAuthorized);
                if (micSt == AVAuthorizationStatusNotDetermined) {
                    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
                    [AVCaptureDevice requestAccessForMediaType:AVMediaTypeAudio completionHandler:^(BOOL granted) {
                        micOk = granted;
                        dispatch_semaphore_signal(sem);
                    }];
                    dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(12 * NSEC_PER_SEC)));
                }
                write_status_json("$STATUS_JSON", screenOk, axOk, micOk);
                return 0;
            }
        }

        chdir("$INSTALL_DIR");
        setenv("PYTHONHOME", "$PYHOME", 1);
        setenv("PYTHONPATH", "$SITEPKG", 1);
        setenv("PYTHONUNBUFFERED", "1", 1);

        void *handle = dlopen("$PYLIB", RTLD_LAZY | RTLD_GLOBAL);
        if (!handle) {
            fprintf(stderr, "[JOBMonitorAgent] dlopen fallo: %s\n", dlerror());
            return 1;
        }
        Py_BytesMain_t py_main = (Py_BytesMain_t)dlsym(handle, "Py_BytesMain");
        if (!py_main) {
            fprintf(stderr, "[JOBMonitorAgent] dlsym Py_BytesMain fallo: %s\n", dlerror());
            return 1;
        }

        char *py_argv[64];
        int n = 0;
        py_argv[n++] = argv[0];
        py_argv[n++] = "$INSTALL_DIR/agent.py";
        for (int i = 1; i < argc && n < 62; i++) {
            py_argv[n++] = argv[i];
        }
        py_argv[n] = NULL;
        return py_main(n, py_argv);
    }
}
EOF
    if clang -O2 -fobjc-arc "$LAUNCHER_M" \
        -framework Foundation \
        -framework CoreGraphics \
        -framework ApplicationServices \
        -framework AVFoundation \
        -o "$APP_EXEC" 2>/dev/null; then
        BUILT_NATIVE=1
    fi
    rm -f "$LAUNCHER_M"
fi

if [ "$BUILT_NATIVE" -eq 0 ]; then
    cat > "$APP_EXEC" <<EOF
#!/usr/bin/env bash
cd "$INSTALL_DIR"
exec "$INSTALL_DIR/.venv/bin/python" "$INSTALL_DIR/agent.py" "\$@"
EOF
    chmod 755 "$APP_EXEC"
fi

xattr -cr "$APP_BUNDLE" 2>/dev/null || true
codesign --force --deep --sign - --identifier "$PLIST_LABEL" "$APP_BUNDLE" >/dev/null 2>&1 || true
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister -f "$APP_BUNDLE" >/dev/null 2>&1 || true

echo "[4/5] Solicitando permisos nativos de Pantalla, Audio y Accesibilidad..."
rm -f "$STATUS_JSON"
open -W -n -a "$APP_BUNDLE" --args --request-permissions >/dev/null 2>&1 || true

SCREEN_OK=$(python3 -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('screen') else '0')")
AX_OK=$(python3 -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('accessibility') else '0')")

if [ "$SCREEN_OK" != "1" ] || [ "$AX_OK" != "1" ]; then
    echo ""
    echo "===================================================================="
    echo "  AUTORIZACION REQUERIDA EN AJUSTES DEL SISTEMA (Evita pantalla gris)"
    echo "===================================================================="
    if [ "$SCREEN_OK" != "1" ]; then
        echo "  -> Falta activar: 'Grabacion de pantalla y audio del sistema'"
        echo "     Busca 'JOB-Monitor Agent' en la lista y enciende el interruptor."
        open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" >/dev/null 2>&1 || true
    fi
    if [ "$AX_OK" != "1" ]; then
        echo "  -> Falta activar: 'Accesibilidad' (para control remoto de mouse/teclado)"
        echo "     En Privacidad y Seguridad -> Accesibilidad, activa 'JOB-Monitor Agent'."
        if [ "$SCREEN_OK" = "1" ]; then
            open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" >/dev/null 2>&1 || true
        fi
    fi
    echo "--------------------------------------------------------------------"
    echo "  Esperando confirmacion automatica (o presiona Enter para continuar)..."

    for _ in $(seq 1 25); do
        if [ -e /dev/tty ]; then
            if read -r -t 3 _ </dev/tty; then
                break
            fi
        else
            sleep 3
        fi
        open -W -n -a "$APP_BUNDLE" --args --check-permissions >/dev/null 2>&1 || true
        SCREEN_NOW=$(python3 -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('screen') else '0')")
        AX_NOW=$(python3 -c "import json, pathlib; p=pathlib.Path('$STATUS_JSON'); d=json.loads(p.read_text()) if p.exists() else {}; print('1' if d.get('accessibility') else '0')")
        if [ "$SCREEN_NOW" = "1" ] && [ "$AX_NOW" = "1" ]; then
            echo "  [OK] Permisos de Pantalla/Audio y Accesibilidad detectados activos."
            break
        elif [ "$SCREEN_NOW" = "1" ] && [ "$SCREEN_OK" != "1" ]; then
            echo "  [OK] Grabacion de pantalla y audio detectada activa."
            SCREEN_OK="1"
            open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" >/dev/null 2>&1 || true
        fi
    done
else
    echo "  [OK] Permisos de Pantalla/Audio y Accesibilidad ya estaban concedidos."
fi

echo "[5/5] Registrando auto-arranque permanente al encender el Mac (LaunchAgent + Login Item)..."
cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>
    <key>AssociatedBundleIdentifiers</key>
    <array>
        <string>$PLIST_LABEL</string>
    </array>
    <key>ProgramArguments</key>
    <array>
        <string>$APP_EXEC</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$INSTALL_DIR</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>ThrottleInterval</key>
    <integer>5</integer>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$INSTALL_DIR/agent.log</string>
    <key>StandardErrorPath</key>
    <string>$INSTALL_DIR/agent.err.log</string>
</dict>
</plist>
EOF

launchctl bootstrap "gui/$CURRENT_UID" "$PLIST_PATH" >/dev/null 2>&1 || launchctl load -w "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl enable "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true
launchctl kickstart -k "gui/$CURRENT_UID/$PLIST_LABEL" >/dev/null 2>&1 || true

echo ""
echo "===================================================================="
echo "  INSTALACION COMPLETADA EXITOSAMENTE EN MACOS"
echo "  - Aplicacion nativa : $APP_BUNDLE"
echo "  - Datos del agente  : $INSTALL_DIR"
echo "  - Auto-inicio Mac   : Activo al encender/iniciar sesion (KeepAlive)"
echo "  - Nota: Si activas el permiso de Pantalla despues, el video"
echo "          en vivo iniciara automaticamente en 3 segundos."
echo "===================================================================="
echo ""
if [ -e /dev/tty ]; then
    read -r -t 10 -p "Presiona Enter para finalizar (se cerrara en 10s)..." </dev/tty || true
fi
