
import socket

PLC_IP = "192.168.8.50"
PORTS = [502, 102, 80, 443]

for port in PORTS:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3)

    try:
        sock.connect((PLC_IP, port))
        print(f"[ACIK] {port} portu bağlantı kabul ediyor.")
    except ConnectionRefusedError:
        print(f"[KAPALI] {port} portu bağlantıyı reddetti.")
    except socket.timeout:
        print(f"[ZAMAN AŞIMI] {port} portuna yanıt gelmedi.")
    except OSError as e:
        print(f"[HATA] {port}: {e}")
    finally:
        sock.close()
