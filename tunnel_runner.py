import subprocess
import re
import sys
import os

def main():
    print("Iniciando servicio de tunel...")
    print("Por favor espera, conectando con Cloudflare...")
    
    # Expresion regular para buscar la URL
    url_regex = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    
    # Iniciar cloudflared interceptando su salida de error (donde imprime los logs)
    # y ocultando la ventana de consola extra
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    proc = subprocess.Popen(
        ["cloudflared.exe", "tunnel", "--url", "http://127.0.0.1:8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
        startupinfo=startupinfo
    )

    url_found = False

    try:
        # Leer linea por linea mientras el proceso corre
        for line in iter(proc.stdout.readline, ''):
            match = url_regex.search(line)
            if match:
                url = match.group(0)
                print("\n")
                print("*" * 70)
                print(" T U N E L   A C T I V O   Y   L I S T O ".center(70))
                print("*" * 70)
                print("")
                print(" ENLACE DE ACCESO REMOTO:".center(70))
                print(f" {url} ".center(70))
                print("")
                print(" (Puedes abrir este enlace desde cualquier celular o PC)".center(70))
                print("*" * 70)
                print("\n[NOTA] Manten esta ventana abierta mientras necesites el acceso remoto.")
                url_found = True
            
            # Si ocurre un error grave (no de red, sino de ejecucion de cloudflared)
            if "error" in line.lower() and not url_found:
                # Filtrar algunos errores comunes de cloudflared que no son criticos
                if "quic" not in line.lower() and "route" not in line.lower():
                    pass # Se podria imprimir el error si se desea, pero cloudflared es muy ruidoso
    except KeyboardInterrupt:
        pass
    finally:
        proc.terminate()

if __name__ == "__main__":
    main()
