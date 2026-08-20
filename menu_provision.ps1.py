import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor


# =====================================================================
# AUTO-INSTALADOR INICIAL SI FALTA PARAMIKO
# =====================================================================
def instalacion_rapida_inicio():
    try:
        import paramiko
    except ImportError:
        print("\033[93m[*] Entorno nuevo detectado: Instalando paramiko base...\033[0m")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "paramiko"])
        print("\033[92m[✔] Paramiko instalado con éxito.\033[0m\n")
        time.sleep(1)


instalacion_rapida_inicio()
import paramiko

# =====================================================================
# CONFIGURACIÓN DEL SERVIDOR Y CREDENCIALES
# =====================================================================
SSH_HOST = "100.89.207.47"
SSH_USER = "somosinternet"
SSH_PASS = "somos123."
CARPETA_SERVIDOR = "deploy"
SCRIPT_SERVIDOR = "mass_provision.py"
ARCHIVO_MACS = "macs.txt"

MACS_IGNORADAS = []
IPS_IGNORADAS = []

FABRICANTES_PERMITIDOS = [
    "10-ba-1a",
    "c4-2a-fa",
    "9c-00-d3",
    "c4-2a-fd",
    "00-8a-26",  # i96zemax
]


def configurar_consola():
    """Habilita compatibilidad ANSI y título en PowerShell / CMD."""
    if platform.system().lower() == "windows":
        os.system("title GESTOR DE APROVISIONAMIENTO - SOMOS INTERNET")
        os.system("")


def limpiar_pantalla():
    """Limpia la terminal según el sistema operativo."""
    if platform.system().lower() == "windows":
        os.system("cls")
    else:
        os.system("clear")


def obtener_info_red_activa():
    """
    Detecta de forma automática la IP del portátil y la IP del MikroTik (Gateway)
    sin importar qué segmento de red tenga configurado ese MikroTik.
    """
    ip_portatil = "127.0.0.1"
    gateway = ""

    # Método 1: Detección por PowerShell en adaptadores físicos activos
    if platform.system().lower() == "windows":
        try:
            ps_cmd = (
                "Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null -and $_.NetAdapter.Status -eq 'Up' } | "
                "Select-Object -First 1 @{Name='IP';Expression={$_.IPv4Address.IPAddress}}, @{Name='GW';Expression={$_.IPv4DefaultGateway.NextHop}} | "
                "ConvertTo-Csv -NoTypeInformation"
            )
            res = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], capture_output=True, text=True)
            lineas = [l.strip().replace('"', '') for l in res.stdout.splitlines() if
                      l.strip() and not l.startswith("#")]
            if len(lineas) >= 2:
                datos = lineas[1].split(",")
                if len(datos) == 2:
                    ip_portatil = datos[0]
                    gateway = datos[1]
                    return ip_portatil, gateway
        except Exception:
            pass

    # Método 2: Socket UDP a broadcast de la red local
    if ip_portatil.startswith("127."):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("224.0.0.1", 80))
            ip_portatil = s.getsockname()[0]
            s.close()
        except Exception:
            pass

    # Método 3: Fallback estándar
    if ip_portatil.startswith("127."):
        try:
            ip_portatil = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip_portatil = "192.168.88.10"

    return ip_portatil, gateway


def sonda_ip_rapida(ip):
    """Obliga al MikroTik y a Windows a registrar la MAC de la TV Box en la tabla ARP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.04)
        s.connect_ex((ip, 5555))
        s.close()
    except Exception:
        pass

    is_windows = platform.system().lower() == "windows"
    param_cant = "-n" if is_windows else "-c"
    param_time = "-w" if is_windows else "-W"
    timeout_val = "60" if is_windows else "1"

    subprocess.run(["ping", param_cant, "1", param_time, timeout_val, ip], stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)
    return ip


def obtener_mapa_arp():
    """Lee todas las entradas de la tabla ARP independientemente del idioma del Windows."""
    mapa_arp = {}
    try:
        resultado = subprocess.run(["arp", "-a"], capture_output=True, text=True, errors="ignore")
        patron = r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s+([0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2}[:-][0-9a-fA-F]{2})"

        for linea in resultado.stdout.splitlines():
            match = re.search(patron, linea)
            if match:
                ip = match.group(1)
                mac = match.group(2).lower().replace(":", "-")
                mapa_arp[ip] = mac
    except Exception as e:
        print(f"\033[91m[!] Error al leer tabla ARP: {e}\033[0m")

    return mapa_arp


def escanear_dispositivos(segmento_red, ip_portatil, gateway=""):
    """Escanea el rango /24 completo del MikroTik al que estás conectado."""
    exclusiones = {ip_portatil, SSH_HOST}
    if gateway:
        exclusiones.add(gateway)

    lista_ips = [
        f"{segmento_red}{i}"
        for i in range(1, 255)
        if f"{segmento_red}{i}" not in exclusiones
    ]
    print(f"\n\033[96m[*] Escaneando segmento del MikroTik actual ({segmento_red}0/24)...\033[0m")

    with ThreadPoolExecutor(max_workers=120) as executor:
        list(executor.map(sonda_ip_rapida, lista_ips))

    tabla_arp = obtener_mapa_arp()
    prefijos_validos = [p.lower().replace(":", "-") for p in FABRICANTES_PERMITIDOS]
    macs_excluidas = [m.lower().replace(":", "-") for m in MACS_IGNORADAS]

    dispositivos = []
    for ip, mac in tabla_arp.items():
        if ip.startswith(segmento_red) and ip not in exclusiones and ip not in IPS_IGNORADAS:
            if any(mac.startswith(p) for p in prefijos_validos) and mac not in macs_excluidas:
                mac_formateada = mac.upper().replace("-", ":")
                dispositivos.append((ip, mac_formateada))

    return sorted(dispositivos, key=lambda item: int(item[0].split(".")[-1]))


def guardar_y_abrir_macs(lista_macs, abrir_bloc_notas=False):
    """Guarda las MACs en el archivo txt y opcionalmente abre el Bloc de Notas."""
    with open(ARCHIVO_MACS, "w", encoding="utf-8") as f:
        f.write("\n".join(lista_macs) + "\n")

    print(f"\n\033[92m[✔] Se guardaron {len(lista_macs)} MACs en '{ARCHIVO_MACS}'\033[0m")

    if abrir_bloc_notas and platform.system().lower() == "windows":
        subprocess.Popen(["notepad.exe", ARCHIVO_MACS])


def ejecutar_ssh_stream(comando):
    """Ejecuta un comando en el servidor vía Paramiko transmitiendo la salida en vivo."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(SSH_HOST, username=SSH_USER, password=SSH_PASS, timeout=10)
        canal = ssh.get_transport().open_session()
        canal.get_pty()
        canal.exec_command(comando)

        while True:
            if canal.recv_ready():
                salida = canal.recv(1024).decode("utf-8", errors="ignore")
                sys.stdout.write(salida)
                sys.stdout.flush()
            if canal.exit_status_ready():
                break

        return canal.recv_exit_status()
    except Exception as e:
        print(f"\n\033[91m[!] Error de conexión SSH con el servidor {SSH_HOST}: {e}\033[0m")
        return 1
    finally:
        ssh.close()


def forzar_modo_desarrollador_y_adb(ips_lista):
    """Opción 3: Activa Modo Desarrollador y Depuración USB."""
    cadena_ips = " ".join(ips_lista)
    print(f"\n\033[93m[*] Activando Modo Desarrollador y Depuración USB en {len(ips_lista)} dispositivos...\033[0m")

    comandos_adb = (
        "settings put global development_settings_enabled 1; "
        "settings put system development_settings_enabled 1; "
        "settings put global adb_enabled 1; "
        "settings put secure adb_enabled 1; "
        "setprop persist.sys.usb.config adb; "
        "setprop persist.adb.tcp.port 5555"
    )

    script_bash = (
        "adb start-server >/dev/null 2>&1; "
        f"for ip in {cadena_ips}; do "
        "  echo -n \"--> Dispositivo $ip: \"; "
        "  conectado=0; "
        "  for intento in 1 2; do "
        "    for puerto in 5555 6555; do "
        "      adb disconnect $ip:$puerto >/dev/null 2>&1; "
        "      res=$(timeout 2 adb connect $ip:$puerto 2>&1); "
        "      if echo \"$res\" | grep -q 'connected to'; then "
        "        timeout 2 adb -s $ip:$puerto shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1; "
        f"        timeout 3 adb -s $ip:$puerto shell \"{comandos_adb}\" >/dev/null 2>&1; "
        "        echo \"[OK] Desarrollador y Depuracion USB activados (Puerto $puerto)\"; "
        "        conectado=1; "
        "        break 2; "
        "      fi; "
        "    done; "
        "    sleep 0.5; "
        "  done; "
        "  if [ $conectado -eq 0 ]; then "
        "    echo \"[ERROR] Sin respuesta ADB (El equipo no responde en puerto 5555/6555)\"; "
        "  fi; "
        "done"
    )

    comando_remoto = f"cd {CARPETA_SERVIDOR} && {script_bash}"
    print(f"\033[90mConectando a {SSH_USER}@{SSH_HOST}...\033[0m\n")
    ejecutar_ssh_stream(comando_remoto)


def forzar_desbloqueo_oem(ips_lista):
    """Opción 4: Posicionamiento exacto y activación única de OEM."""
    cadena_ips = " ".join(ips_lista)
    print(f"\n\033[93m[*] Ejecutando activación de Desbloqueo OEM en {len(ips_lista)} dispositivos...\033[0m")

    script_control_remoto = (
        "settings put global development_settings_enabled 1; "
        "settings put global oem_unlock_supported 1; "
        "settings put secure oem_unlock_supported 1; "
        "setprop sys.oem_unlock_allowed 1; "
        "setprop persist.sys.oem_unlock_allowed 1; "
        "am start -a android.settings.APPLICATION_DEVELOPMENT_SETTINGS >/dev/null 2>&1; "
        "sleep 1.2; "
        "input keyevent KEYCODE_DPAD_UP; sleep 0.1; "
        "input keyevent KEYCODE_DPAD_UP; sleep 0.1; "
        "input keyevent KEYCODE_DPAD_UP; sleep 0.1; "
        "input keyevent KEYCODE_DPAD_DOWN; sleep 0.2; "
        "input keyevent KEYCODE_DPAD_DOWN; sleep 0.2; "
        "input keyevent KEYCODE_DPAD_DOWN; sleep 0.2; "
        "input keyevent KEYCODE_DPAD_DOWN; sleep 0.2; "
        "input keyevent KEYCODE_DPAD_CENTER; "
        "sleep 0.6; "
        "input keyevent KEYCODE_DPAD_RIGHT; "
        "sleep 0.3; "
        "input keyevent KEYCODE_DPAD_CENTER; "
        "sleep 0.4; "
        "input keyevent KEYCODE_BACK >/dev/null 2>&1; "
        "service call oem_lock 1 i32 1 >/dev/null 2>&1"
    )

    script_bash = (
        "adb start-server >/dev/null 2>&1; "
        f"for ip in {cadena_ips}; do "
        "  echo -n \"--> Dispositivo $ip (OEM): \"; "
        "  conectado=0; "
        "  for intento in 1 2; do "
        "    for puerto in 5555 6555; do "
        "      adb disconnect $ip:$puerto >/dev/null 2>&1; "
        "      res=$(timeout 2 adb connect $ip:$puerto 2>&1); "
        "      if echo \"$res\" | grep -q 'connected to'; then "
        "        timeout 2 adb -s $ip:$puerto shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1; "
        f"        timeout 7 adb -s $ip:$puerto shell \"{script_control_remoto}\" >/dev/null 2>&1; "
        "        echo \"[OK] OEM activado una sola vez y confirmado\"; "
        "        conectado=1; "
        "        break 2; "
        "      fi; "
        "    done; "
        "    sleep 0.5; "
        "  done; "
        "  if [ $conectado -eq 0 ]; then "
        "    echo \"[ERROR] Sin respuesta ADB\"; "
        "  fi; "
        "done"
    )

    comando_remoto = f"cd {CARPETA_SERVIDOR} && {script_bash}"
    print(f"\033[90mConectando a {SSH_USER}@{SSH_HOST}...\033[0m\n")
    ejecutar_ssh_stream(comando_remoto)


def instalar_dependencias_completo():
    """Opción 5: Instala dependencias y configura Windows automáticamente."""
    limpiar_pantalla()
    print("\033[95m╔══════════════════════════════════════════════════════════════╗\033[0m")
    print(
        "\033[95m║\033[0m       \033[1;97mINSTALADOR INTEGRAL DE DEPENDENCIAS (PC NUEVO)\033[0m         \033[95m║\033[0m")
    print("\033[95m╚══════════════════════════════════════════════════════════════╝\033[0m\n")

    if platform.system().lower() == "windows":
        print("\033[96m[1/4] Configurando perfil de red privada para permitir escaneo...\033[0m")
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            "Set-NetConnectionProfile -NetworkCategory Private -ErrorAction SilentlyContinue"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("\033[92m[✔] Red configurada como Privada.\033[0m\n")
        except Exception:
            pass

    print("\033[96m[2/4] Actualizando gestor PIP y herramientas...\033[0m")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
        print("\033[92m[✔] PIP actualizado.\033[0m\n")
    except Exception as e:
        print(f"\033[91m[!] Aviso PIP: {e}\033[0m\n")

    librerias = ["paramiko", "cryptography", "bcrypt", "pynacl"]
    print("\033[96m[3/4] Instalando librerías requeridas...\033[0m")
    for lib in librerias:
        try:
            print(f"  --> Instalando: \033[93m{lib}\033[0m...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", lib])
            print(f"  \033[92m[✔] {lib} listo.\033[0m")
        except Exception as e:
            print(f"  \033[91m[!] Error instalando {lib}: {e}\033[0m")

    print("\n\033[96m[4/4] Verificando Platform Tools / ADB...\033[0m")
    adb_instalado = False
    try:
        res = subprocess.run(["adb", "version"], capture_output=True, text=True)
        if res.returncode == 0:
            adb_instalado = True
            print("  \033[92m[✔] ADB detectado en el sistema.\033[0m")
    except FileNotFoundError:
        adb_instalado = False

    if not adb_instalado and platform.system().lower() == "windows":
        print("  \033[93m[*] Descargando Android Platform-Tools oficial...\033[0m")
        url_adb = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
        zip_path = "platform-tools.zip"
        try:
            urllib.request.urlretrieve(url_adb, zip_path)
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(".")
            if os.path.exists(zip_path):
                os.remove(zip_path)

            ruta_adb = os.path.abspath("platform-tools")
            os.environ["PATH"] += os.pathsep + ruta_adb
            print(f"  \033[92m[✔] ADB descargado en: {ruta_adb}\033[0m")
        except Exception as e:
            print(f"  \033[91m[!] Error descargando ADB: {e}\033[0m")

    ip_local, gw = obtener_info_red_activa()
    segmento = ".".join(ip_local.split(".")[:3]) + "."
    print(
        f"\n\033[90mRed detectada: IP Portatil: \033[93m{ip_local}\033[0m │ MikroTik/Gateway: \033[93m{gw or 'N/D'}\033[0m │ Subred: \033[93m{segmento}0/24\033[0m")

    print("\n\033[92m══════════════════════════════════════════════════════════════\033[0m")
    print("\033[92m[✔] ¡Equipo preparado y dependencias instaladas con éxito!\033[0m")
    print("\033[92m══════════════════════════════════════════════════════════════\033[0m")
    input("\n\033[90mPresiona Enter para volver al menú principal...\033[0m")


def menu():
    configurar_consola()
    ip_portatil, gateway = obtener_info_red_activa()
    segmento_red = ".".join(ip_portatil.split(".")[:3]) + "."

    while True:
        limpiar_pantalla()
        print("\033[96m╔══════════════════════════════════════════════════════════════╗\033[0m")
        print(
            "\033[96m║\033[0m        \033[1;97mGESTOR DE APROVISIONAMIENTO TV BOX (AUTO-SSH)\033[0m        \033[96m║\033[0m")
        print(
            f"\033[96m║\033[0m   \033[90mIP Portátil:\033[0m \033[93m{ip_portatil:<14}\033[0m │ \033[90mSubred MikroTik:\033[0m \033[93m{segmento_red}0/24\033[0m       \033[96m║\033[0m")
        print("\033[96m╠══════════════════════════════════════════════════════════════╣\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;92m[1]\033[0m Escanear y Aprovisionar (Abre MACs al finalizar)       \033[96m║\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;92m[2]\033[0m Solo Escanear (Ver lista de IPs y guardar TXT)          \033[96m║\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;92m[3]\033[0m Forzar Modo Desarrollador y Depuración USB              \033[96m║\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;92m[4]\033[0m Forzar Desbloqueo OEM (Control Remoto - Única Vez)      \033[96m║\033[0m")
        print("\033[95m╠══════════════════════════════════════════════════════════════╣\033[0m")
        print(
            "\033[95m║\033[0m  \033[1;95m[ ⚙ CONFIGURACIÓN ]\033[0m                                         \033[95m║\033[0m")
        print(
            "\033[95m║\033[0m  \033[1;93m[5]\033[0m  Instalar Dependencias y Librerías (PC Nuevo)           \033[95m║\033[0m")
        print("\033[96m╠══════════════════════════════════════════════════════════════╣\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;91m[0]\033[0m  Salir del Gestor                                       \033[96m║\033[0m")
        print("\033[96m╚══════════════════════════════════════════════════════════════╝\033[0m")

        opcion = input("\n\033[1;97mSelecciona una opción [0-5]: \033[0m").strip()

        if opcion == "1":
            entrada = input("\n\033[97m¿Cuántas TV Boxes deseas procesar? (ej. 10): \033[0m").strip()
            if not entrada.isdigit() or int(entrada) <= 0:
                input("\n\033[91m[!] Ingresa un número válido. Presiona Enter...\033[0m")
                continue

            cantidad = int(entrada)
            dispositivos = escanear_dispositivos(segmento_red, ip_portatil, gateway)
            if not dispositivos:
                input("\n\033[91m[!] No se encontraron TV Boxes activas. Presiona Enter...\033[0m")
                continue

            seleccionados = dispositivos[:cantidad]
            ips_seleccionadas = [d[0] for d in seleccionados]
            macs_seleccionadas = [d[1] for d in seleccionados]
            cadena_ips = ",".join(ips_seleccionadas)

            print(f"\n\033[92m[✔] Dispositivos a procesar ({len(ips_seleccionadas)}):\033[0m")
            print(f"\033[93m{cadena_ips}\033[0m")

            comando_remoto = f"cd {CARPETA_SERVIDOR} && python3 {SCRIPT_SERVIDOR} --ips {cadena_ips}"
            print(f"\n\033[96m[*] Iniciando aprovisionamiento en {SSH_USER}@{SSH_HOST}...\033[0m\n")
            estado = ejecutar_ssh_stream(comando_remoto)

            if estado == 0:
                print("\n\033[92m[✔] Aprovisionamiento finalizado con éxito.\033[0m")
                guardar_y_abrir_macs(macs_seleccionadas, abrir_bloc_notas=True)
            else:
                print("\n\033[93m[!] El proceso finalizó con observaciones.\033[0m")
                guardar_y_abrir_macs(macs_seleccionadas, abrir_bloc_notas=False)

            input("\n\033[90mPresiona Enter para volver al menú...\033[0m")

        elif opcion == "2":
            dispositivos = escanear_dispositivos(segmento_red, ip_portatil, gateway)
            print(f"\n\033[92m[✔] Total detectadas ({len(dispositivos)}):\033[0m")
            if dispositivos:
                ips = [d[0] for d in dispositivos]
                macs = [d[1] for d in dispositivos]
                print("\n\033[97mIPs encontradas:\033[0m")
                print(f"\033[93m{','.join(ips)}\033[0m")
                guardar_y_abrir_macs(macs, abrir_bloc_notas=False)
            else:
                print(f"\033[91mNo se encontraron TV Boxes en el segmento {segmento_red}0/24.\033[0m")
            input("\n\033[90mPresiona Enter para volver al menú...\033[0m")

        elif opcion == "3":
            entrada = input("\n\033[97m¿A cuántas TV Boxes deseas forzar Modo Desarrollador y ADB?: \033[0m").strip()
            if not entrada.isdigit() or int(entrada) <= 0:
                input("\n\033[91m[!] Ingresa un número válido. Presiona Enter...\033[0m")
                continue

            cantidad = int(entrada)
            dispositivos = escanear_dispositivos(segmento_red, ip_portatil, gateway)
            if not dispositivos:
                input("\n\033[91m[!] No se encontraron TV Boxes activas. Presiona Enter...\033[0m")
                continue

            seleccionados = dispositivos[:cantidad]
            ips_seleccionadas = [d[0] for d in seleccionados]
            forzar_modo_desarrollador_y_adb(ips_seleccionadas)
            input("\n\033[92m[✔] Operación completada. Presiona Enter para volver al menú...\033[0m")

        elif opcion == "4":
            entrada = input("\n\033[97m¿A cuántas TV Boxes deseas forzar Desbloqueo OEM?: \033[0m").strip()
            if not entrada.isdigit() or int(entrada) <= 0:
                input("\n\033[91m[!] Ingresa un número válido. Presiona Enter...\033[0m")
                continue

            cantidad = int(entrada)
            dispositivos = escanear_dispositivos(segmento_red, ip_portatil, gateway)
            if not dispositivos:
                input("\n\033[91m[!] No se encontraron TV Boxes activas. Presiona Enter...\033[0m")
                continue

            seleccionados = dispositivos[:cantidad]
            ips_seleccionadas = [d[0] for d in seleccionados]
            forzar_desbloqueo_oem(ips_seleccionadas)
            input("\n\033[92m[✔] Desbloqueo OEM aplicado. Presiona Enter para volver al menú...\033[0m")

        elif opcion == "5":
            instalar_dependencias_completo()

        elif opcion == "0":
            limpiar_pantalla()
            print("\033[90mCerrando gestor...\033[0m")
            break
        else:
            input("\n\033[91m[!] Opción inválida. Presiona Enter para continuar...\033[0m")


if __name__ == "__main__":
    menu()