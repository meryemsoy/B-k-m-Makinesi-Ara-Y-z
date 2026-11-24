#!/usr/bin/env python3
"""PLC seri port (UART / RS-232 / RS-485) ve Ethernet (Modbus TCP) test aracı.

Kurulum (bir kez):  pip install pyserial pymodbus

Kullanım:
  python plc_test.py ports
      Bilgisayardaki COM portlarını listeler.

  python plc_test.py raw COM3 9600 [parity] [saniye]
      PLC seri porttan kendiliğinden veri yolluyorsa gelen ham baytları gösterir.
      parity: N (yok, varsayılan), E (even), O (odd)

  python plc_test.py modbus COM3 9600 istasyon baslangic adet [parity]
      Seri port (Modbus RTU) ile holding register okur (fonksiyon 03).
      Örnek: python plc_test.py modbus COM3 9600 1 0 4 N

  python plc_test.py tcp 192.168.1.5 502 istasyon baslangic adet
      Ethernet üzerinden (Modbus TCP) holding register okur.
      Örnek: python plc_test.py tcp 192.168.1.5 502 1 0 4

Bu araç sadece OKUR; PLC'ye hiçbir değer yazmaz.
"""
import sys
import time


def crc16(data: bytes) -> int:
    """Modbus RTU CRC-16."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def build_read_request(station, start, count):
    """Fonksiyon 03 (holding register oku) isteği, CRC ile."""
    frame = bytes([station, 0x03, start >> 8, start & 0xFF, count >> 8, count & 0xFF])
    c = crc16(frame)
    return frame + bytes([c & 0xFF, c >> 8])


def open_port(port, baud, parity="N"):
    import serial
    return serial.Serial(port, baudrate=baud, bytesize=8, parity=parity.upper(),
                         stopbits=1, timeout=1)


def cmd_ports():
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    if not ports:
        print("COM portu bulunamadı. Kablo/sürücü ve Aygıt Yöneticisi'ni kontrol edin.")
    for p in ports:
        print(f"{p.device:8} {p.description}")


def cmd_raw(port, baud, parity="N", seconds=10):
    ser = open_port(port, baud, parity)
    print(f"{port} {baud} baud dinleniyor ({seconds} sn)...")
    end = time.time() + seconds
    got = False
    while time.time() < end:
        data = ser.read(64)
        if data:
            got = True
            print(data.hex(" "), "|", data.decode("ascii", "replace").strip())
    if not got:
        print("Hiç veri gelmedi. PLC kendiliğinden yollamıyor olabilir (soru-cevap "
              "protokolü) ya da baud/parity/kablo yanlış. 'modbus' komutunu deneyin.")
    ser.close()


def cmd_modbus(port, baud, station, start, count, parity="N"):
    ser = open_port(port, baud, parity)
    req = build_read_request(station, start, count)
    print("Giden :", req.hex(" "))
    ser.reset_input_buffer()
    ser.write(req)
    expected = 5 + 2 * count
    resp = ser.read(expected)
    ser.close()
    print("Gelen :", resp.hex(" ") if resp else "(cevap yok)")
    if len(resp) < expected:
        print("Cevap eksik/yok: istasyon no, baud, parity, A/B hat sırası veya "
              "protokol (Modbus olmayabilir) yanlış olabilir.")
        return
    body, got_crc = resp[:-2], resp[-2] | (resp[-1] << 8)
    if crc16(body) != got_crc:
        print("CRC hatası.")
        return
    if resp[1] & 0x80:
        print(f"PLC hata kodu döndü: {resp[2]} (adres/fonksiyon desteklenmiyor olabilir)")
        return
    for i in range(count):
        v = (resp[3 + 2 * i] << 8) | resp[4 + 2 * i]
        print(f"Register {start + i}: {v}")


def cmd_tcp(ip, port, station, start, count):
    """Ethernet üzerinden Modbus TCP okuma fonksiyonu."""
    try:
        from pymodbus.client import ModbusTcpClient
    except ImportError:
        print("pymodbus kütüphanesi kurulu değil. Kurmak için: pip install pymodbus")
        return

    print(f"Bağlanılıyor: {ip}:{port} (İstasyon: {station})...")
    client = ModbusTcpClient(ip, port=int(port))
    
    if not client.connect():
        print("PLC'ye TCP üzerinden bağlanılamadı. IP adresini ve portu kontrol edin.")
        return

    try:
        result = client.read_holding_registers(start, count, slave=int(station))
        if result.isError():
            print(f"PLC hata döndürdü: {result}")
        else:
            print("Bağlantı başarılı!")
            for i, val in enumerate(result.registers):
                print(f"Register {start + i}: {val}")
    except Exception as e:
        print("Okuma sırasında hata oluştu:", e)
    finally:
        client.close()


if __name__ == "__main__":
    a = sys.argv[1:]
    try:
        if a and a[0] == "ports":
            cmd_ports()
        elif a and a[0] == "raw":
            cmd_raw(a[1], int(a[2]), a[3] if len(a) > 3 else "N",
                    int(a[4]) if len(a) > 4 else 10)
        elif a and a[0] == "modbus":
            cmd_modbus(a[1], int(a[2]), int(a[3]), int(a[4]), int(a[5]),
                       a[6] if len(a) > 6 else "N")
        elif a and a[0] == "tcp":
            # Kullanım: python plc_test.py tcp <IP> <PORT> <İstasyon> <Başlangıç> <Adet>
            cmd_tcp(a[1], a[2], a[3], int(a[4]), int(a[5]))
        else:
            print(__doc__)
    except ImportError as e:
        print("Eksik kütüphane:", e)
    except Exception as e:
        print("Hata:", e)