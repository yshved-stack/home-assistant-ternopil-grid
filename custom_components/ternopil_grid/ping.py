from __future__ import annotations

import asyncio


async def icmp_ping(host: str, timeout_s: float = 1.0) -> bool:
    """ICMP ping via system ping."""
    timeout = max(1, int(round(timeout_s)))
    try:
        proc = await asyncio.create_subprocess_exec(
            "ping",
            "-c",
            "1",
            "-W",
            str(timeout),
            "-n",
            host,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=timeout_s + 1.0)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


async def tcp_ping(host: str, port: int, timeout_s: float = 1.0) -> bool:
    """TCP connect check."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, int(port)),
            timeout=timeout_s,
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


async def ping(host: str, timeout_s: float, method: str = "icmp", port: int = 0) -> bool:
    method = (method or "icmp").lower().strip()
    if method == "tcp":
        return await tcp_ping(host, port, timeout_s)
    return await icmp_ping(host, timeout_s)
