import ipaddress

from fastapi import Request


def client_ip(r: Request) -> str | None:
    """IP del cliente para auditoría. Devuelve None si no es una IP válida: la columna es `inet` y un
    valor raro (socket Unix, proxy mal configurado) no debe tumbar el login con un 500.
    # ponytail: sin X-Forwarded-For; tras un proxy inverso usar uvicorn --proxy-headers + IPs de confianza."""
    host = r.client.host if r.client else None
    try:
        return str(ipaddress.ip_address(host)) if host else None
    except ValueError:
        return None
