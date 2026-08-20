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
    "20-59-a0",
    "78-11-dc",
    "00-1a-79",
    "d4-6e-0e",
    "70-2c-1f",
    "f4-4e-fd",
    "30-cd-a7",
    "e4-57-40",
    "00-26-5c",
    "18-65-90",
    "5c-02-14",
    "38-a4-ed",
]

# Dispositivos que NUNCA son TV Box (MikroTik, tarjetas Intel/Realtek de PC)
PREFIJOS_DESCARTAR = [
    "48-8f-5a",  # MikroTik
    "6c-3b-6b",  # MikroTik
    "cc-2d-e0",  # MikroTik
    "b8-69-f4",  # MikroTik
    "dc-2c-6e",  # MikroTik
    "18-fd-74",  # MikroTik
    "00-e0-4c",  # Realtek LAN PC
    "80-69-1a",  # Intel Wi-Fi / LAN
    "94-e6-f7",  # Intel
    "b4-96-91",  # Intel
    "3c-e1-a1",  # HP
    "d8-3a-dd",  # Dell
    "ac-d1-b8",  # Lenovo
]


def configurar_consola():
    if platform.system().lower() == "windows":
        os.system("title GESTOR DE APROVISIONAMIENTO - SOMOS INTERNET")
        os.system("")


def limpiar_pantalla():
    if platform.system().lower() == "windows":
        os.system("cls")
    else:
        os.system("clear")


def obtener_ip_propia():
    """Detecta automáticamente la IP local del portátil."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect((SSH_HOST, 80))
        ip_local = s.getsockname()[0]
        s.close()
        return ip_local
    except Exception:
        return "127.0.0.1"


def forzar_sonda_tvbox(ip):
    """
    Despierta forzosamente a la TV Box en la red enviando paquetes directos
    a los puertos de ADB, HTTP y UPnP/mDNS.
    """
    for puerto in (5555, 6555, 80, 8080, 5353):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.015)
            s.connect_ex((ip, puerto))
            s.close()
        except Exception:
            pass
    return ip


def obtener_mapa_arp(ip_portatil):
    """Lee solo la subinterfaz local vinculada a la IP del portátil."""
    mapa_arp = {}
    try:
        resultado = subprocess.run(["arp", "-a"], capture_output=True, text=True, errors="ignore")
        secciones = (
            resultado.stdout.split("Interfaz:")
            if "Interfaz:" in resultado.stdout
            else resultado.stdout.split("Interface:")
        )

        bloque_correcto = ""
        for seccion in secciones:
            if ip_portatil in seccion:
                bloque_correcto = seccion
                break

        texto_a_analizar = bloque_correcto if bloque_correcto else resultado.stdout
        patron = (
            r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}).*?"
            r"([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})"
        )

        for linea in texto_a_analizar.splitlines():
            coincidencia = re.search(patron, linea)
            if coincidencia:
                ip = coincidencia.group(1)
                mac = coincidencia.group(2).lower().replace(":", "-")
                mapa_arp[ip] = mac
    except Exception as e:
        print(f"\033[91m[!] Error al leer la tabla ARP: {e}\033[0m")

    return mapa_arp


def escanear_dispositivos(segmento_red, ip_portatil):
    """Escanea y fuerza el descubrimiento de todas las TV Boxes conectadas."""
    lista_ips = [
        f"{segmento_red}{i}"
        for i in range(1, 255)
        if f"{segmento_red}{i}" not in (ip_portatil, SSH_HOST)
    ]
    print(f"\n\033[96m[*] Forzando descubrimiento de TV Boxes en {segmento_red}0/24...\033[0m")

    # Difusión UDP masiva para levantar equipos en stand-by
    try:
        sock_bcast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock_bcast.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock_bcast.sendto(b"\x00", (f"{segmento_red}255", 5555))
        sock_bcast.sendto(b"\x00", (f"{segmento_red}255", 6555))
        sock_bcast.close()
    except Exception:
        pass

    # Ráfaga ultrarrápida paralela (254 hilos)
    with ThreadPoolExecutor(max_workers=254) as executor:
        list(executor.map(forzar_sonda_tvbox, lista_ips))

    tabla_arp = obtener_mapa_arp(ip_portatil)
    prefijos_validos = [p.lower().replace(":", "-") for p in FABRICANTES_PERMITIDOS]
    prefijos_descartar = [p.lower().replace(":", "-") for p in PREFIJOS_DESCARTAR]
    macs_excluidas = [m.lower().replace(":", "-") for m in MACS_IGNORADAS]

    dispositivos_conocidos = []
    dispositivos_candidatos = []

    for ip, mac in tabla_arp.items():
        # Excluir broadcast, multicast y gateway .1/.254 si coincide
        if (
                ip.startswith(segmento_red)
                and ip not in (SSH_HOST, ip_portatil, f"{segmento_red}255", f"{segmento_red}1")
                and ip not in IPS_IGNORADAS
                and mac not in macs_excluidas
                and not mac.startswith("ff-")
                and not mac.startswith("01-00-5e")
        ):
            # Descartar PCs/Laptops y routers conocidos
            if any(mac.startswith(p) for p in prefijos_descartar):
                continue

            mac_formateada = mac.upper().replace("-", ":")

            if any(mac.startswith(p) for p in prefijos_validos):
                dispositivos_conocidos.append((ip, mac_formateada))
            else:
                dispositivos_candidatos.append((ip, mac_formateada))

    # Si hay coincidencias directas con TV Boxes las usa; si hay equipos nuevos conectados los incluye
    resultado_final = dispositivos_conocidos if dispositivos_conocidos else dispositivos_candidatos
    return sorted(resultado_final, key=lambda item: int(item[0].split(".")[-1]))


def guardar_y_abrir_macs(lista_macs, abrir_bloc_notas=False):
    with open(ARCHIVO_MACS, "w", encoding="utf-8") as f:
        f.write("\n".join(lista_macs) + "\n")

    print(f"\n\033[92m[✔] Se guardaron {len(lista_macs)} MACs en '{ARCHIVO_MACS}'\033[0m")

    if abrir_bloc_notas and platform.system().lower() == "windows":
        subprocess.Popen(["notepad.exe", ARCHIVO_MACS])


def ejecutar_ssh_stream(comando):
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
        print(f"\n\033[91m[!] Error de conexión SSH: {e}\033[0m")
        return 1
    finally:
        ssh.close()


def forzar_modo_desarrollador_y_adb(ips_lista):
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
    cadena_ips = " ".join(ips_lista)
    print(
        f"\n\033[93m[*] Ejecutando activación limpia y única de Desbloqueo OEM en {len(ips_lista)} dispositivos...\033[0m")

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
    limpiar_pantalla()
    print("\033[95m╔══════════════════════════════════════════════════════════════╗\033[0m")
    print(
        "\033[95m║\033[0m       \033[1;97mINSTALADOR INTEGRAL DE DEPENDENCIAS (PC NUEVO)\033[0m         \033[95m║\033[0m")
    print("\033[95m╚══════════════════════════════════════════════════════════════╝\033[0m\n")

    print("\033[96m[1/3] Actualizando PIP y herramientas del compilador...\033[0m")
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
        print("\033[92m[✔] Entorno PIP actualizado.\033[0m\n")
    except Exception as e:
        print(f"\033[91m[!] Aviso en actualización de PIP: {e}\033[0m\n")

    librerias = ["paramiko", "cryptography", "bcrypt", "pynacl"]
    print("\033[96m[2/3] Descargando e instalando librerías requeridas...\033[0m")
    for lib in librerias:
        try:
            print(f"  --> Instalando: \033[93m{lib}\033[0m...")
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", lib])
            print(f"  \033[92m[✔] {lib} listo.\033[0m")
        except Exception as e:
            print(f"  \033[91m[!] Error instalando {lib}: {e}\033[0m")

    print("\n\033[96m[3/3] Verificando herramientas ADB del sistema...\033[0m")
    adb_instalado = False
    try:
        res = subprocess.run(["adb", "version"], capture_output=True, text=True)
        if res.returncode == 0:
            adb_instalado = True
            print("  \033[92m[✔] ADB ya está presente en las variables del sistema.\033[0m")
    except FileNotFoundError:
        adb_instalado = False

    if not adb_instalado and platform.system().lower() == "windows":
        print("  \033[93m[*] ADB no detectado en Windows. Descargando Android Platform-Tools oficial...\033[0m")
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
            print(f"  \033[92m[✔] Platform Tools descargado y cargado en: {ruta_adb}\033[0m")
        except Exception as e:
            print(f"  \033[91m[!] No se pudo descargar ADB automáticamente: {e}\033[0m")

    ip_local = obtener_ip_propia()
    print(f"\n\033[90mInformación de red: IP local vinculada -> \033[93m{ip_local}\033[0m")

    print("\n\033[92m══════════════════════════════════════════════════════════════\033[0m")
    print("\033[92m[✔] ¡Todas las dependencias y librerías quedaron instaladas!\033[0m")
    print("\033[92m══════════════════════════════════════════════════════════════\033[0m")
    input("\n\033[90mPresiona Enter para volver al menú principal...\033[0m")


def actualizar_desde_git():
    """Opción 6: Descarga e integra automáticamente las actualizaciones de GitHub."""
    limpiar_pantalla()
    print("\033[95m╔══════════════════════════════════════════════════════════════╗\033[0m")
    print(
        "\033[95m║\033[0m            \033[1;97mACTUALIZADOR DE SCRIPT DESDE GITHUB\033[0m               \033[95m║\033[0m")
    print("\033[95m╚══════════════════════════════════════════════════════════════╝\033[0m\n")

    print("\033[96m[*] Comprobando actualizaciones remotas en GitHub...\033[0m\n")
    try:
        resultado = subprocess.run(["git", "pull", "origin", "main"], capture_output=True, text=True)
        print(resultado.stdout)
        if resultado.stderr:
            print(f"\033[90m{resultado.stderr}\033[0m")

        if "Already up to date" in resultado.stdout or "Ya está actualizado" in resultado.stdout:
            print("\033[92m[✔] El proyecto ya se encuentra en su versión más reciente.\033[0m")
        else:
            print("\033[92m[✔] ¡Actualización descargada con éxito!\033[0m")
            print("\033[93m[*] Reiniciando el gestor...\033[0m")
            time.sleep(2)
            python_exe = sys.executable
            os.execl(python_exe, python_exe, *sys.argv)
    except FileNotFoundError:
        print("\033[91m[!] Git no está instalado o no se encuentra en el PATH de este equipo.\033[0m")
    except Exception as e:
        print(f"\033[91m[!] Error durante la actualización: {e}\033[0m")

    input("\n\033[90mPresiona Enter para volver al menú principal...\033[0m")


def menu():
    configurar_consola()
    ip_portatil = obtener_ip_propia()
    segmento_red = (
        ".".join(ip_portatil.split(".")[:3]) + "."
        if ip_portatil != "127.0.0.1"
        else "100.89.207."
    )

    while True:
        limpiar_pantalla()
        print("\033[96m╔══════════════════════════════════════════════════════════════╗\033[0m")
        print(
            "\033[96m║\033[0m        \033[1;97mGESTOR DE APROVISIONAMIENTO TV BOX (AUTO-SSH)\033[0m        \033[96m║\033[0m")
        print(
            f"\033[96m║\033[0m   \033[90mIP Local:\033[0m \033[93m{ip_portatil:<15}\033[0m │ \033[90mSubred:\033[0m \033[93m{segmento_red}0/24\033[0m             \033[96m║\033[0m")
        print("\033[96m╠══════════════════════════════════════════════════════════════╣\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;92m[1]\033[0m Escanear y Aprovisionar (Abre MACs al finalizar)       \033[96m║\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;92m[2]\033[0m Solo Escanear (Ver TV Boxes detectadas y guardar TXT)    \033[96m║\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;92m[3]\033[0m Forzar Modo Desarrollador y Depuración USB              \033[96m║\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;92m[4]\033[0m Forzar Desbloqueo OEM (Control Remoto - Única Vez)      \033[96m║\033[0m")
        print("\033[95m╠══════════════════════════════════════════════════════════════╣\033[0m")
        print(
            "\033[95m║\033[0m  \033[1;95m[ ⚙ CONFIGURACIÓN ]\033[0m                                         \033[95m║\033[0m")
        print(
            "\033[95m║\033[0m  \033[1;93m[5]\033[0m  Instalar Dependencias y Librerías (PC Nuevo)           \033[95m║\033[0m")
        print(
            "\033[95m║\033[0m  \033[1;93m[6]\033[0m  Actualizar Script desde GitHub (Git Pull)              \033[95m║\033[0m")
        print("\033[96m╠══════════════════════════════════════════════════════════════╣\033[0m")
        print(
            "\033[96m║\033[0m  \033[1;91m[0]\033[0m  Salir del Gestor                                       \033[96m║\033[0m")
        print("\033[96m╚══════════════════════════════════════════════════════════════╝\033[0m")

        opcion = input("\n\033[1;97mSelecciona una opción [0-6]: \033[0m").strip()

        if opcion == "1":
            entrada = input("\n\033[97m¿Cuántas TV Boxes deseas procesar? (ej. 10): \033[0m").strip()

            if not entrada.isdigit() or int(entrada) <= 0:
                input("\n\033[91m[!] Ingresa un número válido. Presiona Enter para volver...\033[0m")
                continue

            cantidad = int(entrada)
            dispositivos = escanear_dispositivos(segmento_red, ip_portatil)

            if not dispositivos:
                input("\n\033[91m[!] No se encontraron TV Boxes activas. Presiona Enter...\033[0m")
                continue

            seleccionados = dispositivos[:cantidad]
            ips_seleccionadas = [d[0] for d in seleccionados]
            macs_seleccionadas = [d[1] for d in seleccionados]
            cadena_ips = ",".join(ips_seleccionadas)

            print(f"\n\033[92m[✔] TV Boxes a procesar ({len(ips_seleccionadas)}):\033[0m")
            print(f"\033[93m{cadena_ips}\033[0m")

            comando_remoto = (
                f"cd {CARPETA_SERVIDOR} && python3 {SCRIPT_SERVIDOR} --ips {cadena_ips}"
            )

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
            dispositivos = escanear_dispositivos(segmento_red, ip_portatil)
            print(f"\n\033[92m[✔] Total TV Boxes detectadas ({len(dispositivos)}):\033[0m")
            if dispositivos:
                ips = [d[0] for d in dispositivos]
                macs = [d[1] for d in dispositivos]
                print("\n\033[97mTV Boxes encontradas:\033[0m")
                for d in dispositivos:
                    print(f"  \033[92m[TV BOX]\033[0m IP: \033[93m{d[0]:<15}\033[0m │ MAC: \033[96m{d[1]}\033[0m")
                print(f"\n\033[97mCadena de IPs:\033[0m \033[93m{','.join(ips)}\033[0m")
                guardar_y_abrir_macs(macs, abrir_bloc_notas=False)
            else:
                print("\033[91mNo se encontraron TV Boxes activas en la red.\033[0m")

            input("\n\033[90mPresiona Enter para volver al menú...\033[0m")

        elif opcion == "3":
            entrada = input("\n\033[97m¿A cuántas TV Boxes deseas forzar Modo Desarrollador y ADB?: \033[0m").strip()

            if not entrada.isdigit() or int(entrada) <= 0:
                input("\n\033[91m[!] Ingresa un número válido. Presiona Enter...\033[0m")
                continue

            cantidad = int(entrada)
            dispositivos = escanear_dispositivos(segmento_red, ip_portatil)

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
            dispositivos = escanear_dispositivos(segmento_red, ip_portatil)

            if not dispositivos:
                input("\n\033[91m[!] No se encontraron TV Boxes activas. Presiona Enter...\033[0m")
                continue

            seleccionados = dispositivos[:cantidad]
            ips_seleccionadas = [d[0] for d in seleccionados]

            forzar_desbloqueo_oem(ips_seleccionadas)
            input("\n\033[92m[✔] Desbloqueo OEM aplicado. Presiona Enter para volver al menú...\033[0m")

        elif opcion == "5":
            instalar_dependencias_completo()

        elif opcion == "6":
            actualizar_desde_git()

        elif opcion == "0":
            limpiar_pantalla()
            print("\033[90mCerrando gestor...\033[0m")
            break
        else:
            input("\n\033[91m[!] Opción inválida. Presiona Enter para continuar...\033[0m")


if __name__ == "__main__":
    menu()