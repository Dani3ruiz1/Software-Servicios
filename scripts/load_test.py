"""Prueba de carga contra una API EN MARCHA (uvicorn real, con todo el middleware). Mide latencia y errores, no solo "funciona".

  python scripts/load_test.py --base http://127.0.0.1:8001 --slug mi-negocio --service <uuid> --concurrency 30 --requests 600

Escenarios (los tres corren en paralelo, como pasaría en producción):
  · disponibilidad pública (lectura, la más frecuente)
  · reservas públicas en horarios distintos (escritura con candados y restricción anti doble reserva)
  · login (argon2: costoso a propósito)
Arranca el servidor con  python scripts/loadtest_server.py  (desactiva el límite de peticiones por IP SOLO dentro de ese proceso: en la API
real no existe ningún interruptor para apagarlo). Si aun así aparecen 429, se informan.
"""
import argparse
import asyncio
import statistics
import time
from datetime import date, timedelta

import httpx


def pct(xs: list[float], p: float) -> float:
    return sorted(xs)[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


async def worker(name: str, n: int, fn, res: dict) -> None:
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            code = await fn()
        except Exception as e:                      # noqa: BLE001
            code = type(e).__name__
        res[name]["lat"].append((time.perf_counter() - t0) * 1000)
        res[name]["codes"][code] = res[name]["codes"].get(code, 0) + 1


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8001")
    ap.add_argument("--slug", required=True)
    ap.add_argument("--service", required=True)
    ap.add_argument("--tenant-slug")
    ap.add_argument("--email")
    ap.add_argument("--password")
    ap.add_argument("--concurrency", type=int, default=30)
    ap.add_argument("--requests", type=int, default=600)
    ap.add_argument("--max-p95-ms", type=float, default=1500)
    a = ap.parse_args()
    day = (date.today() + timedelta(days=5)).isoformat()
    res = {k: {"lat": [], "codes": {}} for k in ("disponibilidad", "reserva", "login")}
    async with httpx.AsyncClient(base_url=a.base, timeout=60, limits=httpx.Limits(max_connections=a.concurrency * 3)) as c:
        av = (await c.get(f"/api/v1/public/{a.slug}/availability", params={"fecha_desde": day, "service_ids": a.service})).json()
        slots = av["dias"][0]["horarios"]
        counter = iter(range(10**6))

        async def avail() -> int:
            return (await c.get(f"/api/v1/public/{a.slug}/availability", params={"fecha_desde": day, "service_ids": a.service})).status_code

        async def book() -> int:
            i = next(counter)
            if i >= len(slots):
                return 0
            r = await c.post(f"/api/v1/public/{a.slug}/bookings", json={
                "service_ids": [a.service], "inicio": slots[i], "cliente": {"nombre": f"Carga {i}", "telefono": f"3{i:09d}"[:10]},
                "consentimiento_datos": True})
            return r.status_code

        async def login() -> int:
            return (await c.post("/api/v1/auth/login", json={"tenant_slug": a.tenant_slug, "email": a.email, "password": a.password})).status_code

        per = max(a.requests // a.concurrency, 1)
        jobs = [worker("disponibilidad", per, avail, res) for _ in range(a.concurrency)]
        jobs += [worker("reserva", max(per // 6, 1), book, res) for _ in range(max(a.concurrency // 3, 1))]
        if a.tenant_slug and a.email and a.password:
            jobs += [worker("login", max(per // 10, 1), login, res) for _ in range(max(a.concurrency // 6, 1))]
        t0 = time.perf_counter()
        await asyncio.gather(*jobs)
        total = time.perf_counter() - t0
    print(f"{'escenario':16} {'n':>5} {'req/s':>7} {'p50':>7} {'p95':>7} {'p99':>7} {'máx':>7}  códigos HTTP")
    bad = False
    for k, v in res.items():
        if not v["lat"]:
            continue
        print(f"{k:16} {len(v['lat']):5} {len(v['lat']) / total:7.1f} {statistics.median(v['lat']):7.0f} {pct(v['lat'], .95):7.0f} {pct(v['lat'], .99):7.0f} {max(v['lat']):7.0f}  {v['codes']}")
        errs = {c: n for c, n in v["codes"].items() if not (isinstance(c, int) and (c < 400 or c in (409, 429)))}
        bad |= bool(errs) or pct(v["lat"], .95) > a.max_p95_ms
        if errs:
            print(f"   ERRORES inesperados: {errs}")
    print(f"duración total {total:.1f}s · {'FALLÓ el umbral/errores' if bad else 'OK'} (p95 máx {a.max_p95_ms:.0f} ms)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
