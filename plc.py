
#!/usr/bin/env python3
"""op.py için PLC okuyucu (Modbus TCP / Ethernet, sadece okur).

Kurulum:
    Python standart kütüphaneleri kullanılır. Ek paket gerekmez.

Haberleşme:
    Modbus TCP
    Ethernet
    TCP Port: 502
"""

import threading
import time
import socket
import struct

# ---------------- AYARLAR ----------------

ENABLED = False        # Bağlantıyı test ettikten sonra True yap

PLC_IP = "192.168.8.50"  # PLC'nin gerçek IP adresini gir
PLC_PORT = 502           # Modbus TCP standart portu

STATION = 1              # Modbus Unit ID
INTERVAL = 0.5           # Okuma aralığı (saniye)
TIMEOUT = 2              # Bağlantı zaman aşımı

# Ekran alanı: (register adresi, bölen)
REGISTERS = {
    "side":    (0, 10.0),
    "toolpos": (1, 10.0),
    "ang":     (2, 10.0),
}

SIGNED = True             # 16 bit işaretli değer

# ------------------------------------------


class PlcReader:

    def __init__(self):
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

        self._vals = {}
        self._status = "PLC başlatılıyor..."
        self._ok = False

        self._transaction_id = 0

    def start(self):
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()

        self._thread = threading.Thread(
            target=self._run,
            daemon=True
        )

        self._thread.start()

    def stop(self):
        self._stop.set()

    def snapshot(self):
        """(değerler, durum metni, bağlantı tamam mı)"""

        with self._lock:
            return dict(self._vals), self._status, self._ok

    def _set(self, vals, status, ok):

        with self._lock:

            if vals is not None:
                self._vals = vals

            self._status = status
            self._ok = ok

    def _recv_exact(self, sock, size):

        data = bytearray()

        while len(data) < size:

            part = sock.recv(size - len(data))

            if not part:
                raise IOError("PLC bağlantısı kapandı")

            data.extend(part)

        return bytes(data)

    def _read_block(self, sock):

        addrs = [a for a, _ in REGISTERS.values()]

        start = min(addrs)
        count = max(addrs) - start + 1

        self._transaction_id = (
            self._transaction_id + 1
        ) & 0xFFFF

        tid = self._transaction_id

        # Modbus TCP PDU:
        # Function 03 + başlangıç adresi + register sayısı

        pdu = struct.pack(
            ">BHH",
            0x03,
            start,
            count
        )

        # MBAP Header:
        # Transaction ID
        # Protocol ID = 0
        # Length = Unit ID + PDU uzunluğu
        # Unit ID

        header = struct.pack(
            ">HHHB",
            tid,
            0,
            len(pdu) + 1,
            STATION
        )

        request = header + pdu

        sock.sendall(request)

        # Önce MBAP başlığını oku
        mbap = self._recv_exact(sock, 7)

        r_tid, protocol, length, unit = struct.unpack(
            ">HHHB",
            mbap
        )

        if r_tid != tid:
            raise IOError("Transaction ID hatası")

        if protocol != 0:
            raise IOError("Modbus TCP Protocol ID hatası")

        if unit != STATION:
            raise IOError("Modbus Unit ID hatası")

        if length < 3:
            raise IOError("Geçersiz Modbus cevap uzunluğu")

        # Kalan cevap verisini oku
        response = self._recv_exact(sock, length - 1)

        function = response[0]

        # PLC hata cevabı
        if function & 0x80:

            error_code = response[1]

            raise IOError(
                f"PLC Modbus hata kodu: {error_code}"
            )

        if function != 0x03:
            raise IOError("Beklenmeyen fonksiyon kodu")

        byte_count = response[1]

        if byte_count != count * 2:
            raise IOError("Register veri uzunluğu hatalı")

        vals = {}

        for key, (addr, div) in REGISTERS.items():

            i = addr - start

            pos = 2 + (i * 2)

            v = (
                response[pos] << 8
            ) | response[pos + 1]

            # Signed 16-bit dönüşümü
            if SIGNED and v >= 0x8000:
                v -= 0x10000

            vals[key] = v / div

        return vals

    def _run(self):

        sock = None

        while not self._stop.is_set():

            try:

                if sock is None:

                    self._set(
                        None,
                        f"PLC bağlantısı kuruluyor: {PLC_IP}",
                        False
                    )

                    sock = socket.create_connection(
                        (PLC_IP, PLC_PORT),
                        timeout=TIMEOUT
                    )

                    sock.settimeout(TIMEOUT)

                vals = self._read_block(sock)

                self._set(
                    vals,
                    f"PLC Ethernet bağlı ({PLC_IP})",
                    True
                )

            except Exception as e:

                self._set(
                    None,
                    f"PLC Ethernet hatası: {e}",
                    False
                )

                if sock is not None:

                    try:
                        sock.close()
                    except Exception:
                        pass

                    sock = None

                self._stop.wait(2)
                continue

            self._stop.wait(INTERVAL)

        if sock is not None:

            try:
                sock.close()
            except Exception:
                pass

