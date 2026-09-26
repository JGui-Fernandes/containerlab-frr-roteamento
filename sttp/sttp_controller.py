#!/usr/bin/env python3
"""
STTP Controller (Shortest Trip Time Protocol) - versão com:
- cálculo correto de troca comparando o melhor caminho atual vs. custo ATUAL do caminho antigo
- hold-down (anti-flap adicional)
- CSV com motivo e valores de comparação
- opção --dry-run (não instala rotas, só calcula e registra)
"""

import argparse
import csv
import json
import logging
import math
import os
import re
import signal
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from heapq import heappush, heappop

PING_AVG_RE = re.compile(
    r"(?:rtt|round-trip).* = [0-9.]+/([0-9.]+)/[0-9.]+(?:/[0-9.]+)? ms"
)

FRR_ERROR_MARKERS = (
    "% ",
    "Unknown command",
    "unknown command",
    "Duplicate",
    "Cannot",
    "cannot",
    "not found",
    "Error",
)

log = logging.getLogger("sttp")


# Helpers de execução
def sh(cmd, timeout=10):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        log.error("Timeout executando comando: %s", " ".join(cmd))
        return -1, "", "timeout"
    except FileNotFoundError as e:
        log.error("Comando não encontrado: %s (%s)", " ".join(cmd), e)
        return -1, "", str(e)


def docker_exec(container, argv, timeout=10):
    return sh(["docker", "exec", container] + argv, timeout=timeout)


def frr_output_has_error(out, err):
    text = f"{out}\n{err}"
    return any(marker in text for marker in FRR_ERROR_MARKERS)


# Medição (RTT por enlace)
def ping_avg_ms(container, iface, dst_ip, count=3, deadline=2):
    rc, out, err = docker_exec(
        container,
        ["ping", "-n", "-q", "-I", iface, "-c", str(count), "-w", str(deadline), dst_ip],
        timeout=deadline + 3,
    )
    text = out + "\n" + err
    m = PING_AVG_RE.search(text)
    return float(m.group(1)) if m else None


def measure_all_links(links, ctn, count, deadline, max_cost, max_workers=16):
    tasks = {}  # future -> (link_key, direction)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for a, ifa, ipa, b, ifb, ipb in links:
            key = frozenset((a, b))
            fut_ab = ex.submit(ping_avg_ms, ctn(a), ifa, ipb, count, deadline)
            fut_ba = ex.submit(ping_avg_ms, ctn(b), ifb, ipa, count, deadline)
            tasks[fut_ab] = (key, "ab")
            tasks[fut_ba] = (key, "ba")

        partial = {}
        for fut in as_completed(tasks):
            key, direction = tasks[fut]
            try:
                partial.setdefault(key, {})[direction] = fut.result()
            except Exception as e:
                log.error("Falha medindo enlace %s (%s): %s", key, direction, e)
                partial.setdefault(key, {})[direction] = None

    link_cost = {}
    for key, vals in partial.items():
        rtt_ab = vals.get("ab")
        rtt_ba = vals.get("ba")
        if rtt_ab is None or rtt_ba is None:
            link_cost[key] = max_cost
        else:
            link_cost[key] = (rtt_ab + rtt_ba) / 2.0
    return link_cost


# Dijkstra
def dijkstra(nodes, adj, src):
    dist = {n: math.inf for n in nodes}
    prev = {n: None for n in nodes}
    dist[src] = 0.0
    pq = [(0.0, src)]
    while pq:
        d, u = heappop(pq)
        if d != dist[u]:
            continue
        for v, w in adj.get(u, {}).items():
            nd = d + w
            if nd < dist[v]:
                dist[v] = nd
                prev[v] = u
                heappush(pq, (nd, v))
    return dist, prev


def next_hop(prev, src, dst):
    """Retorna o vizinho imediato de src no caminho mínimo src->dst."""
    if dst == src:
        return None
    cur = dst
    if prev[cur] is None:
        return None
    while prev[cur] != src:
        cur = prev[cur]
        if cur is None:
            return None
    return cur


# Instalação / verificação de rotas estáticas via FRR
def vtysh_set_route(container, prefix, old_nh_ip, new_nh_ip):
    cmds = ["conf t"]
    if old_nh_ip:
        cmds.append(f"no ip route {prefix} {old_nh_ip}")
    cmds.append(f"ip route {prefix} {new_nh_ip}")
    cmds.append("end")

    argv = ["vtysh"]
    for c in cmds:
        argv += ["-c", c]
    return docker_exec(container, argv)


def vtysh_show_route_static_json(container):
    rc, out, err = docker_exec(container, ["vtysh", "-c", "show ip route static json"])
    if rc != 0 or not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        log.warning("Não foi possível decodificar JSON de rotas de %s: %r", container, out[:200])
        return None


def verify_route_installed(container, prefix, expected_nh_ip, timeout=10):
    rc, out, err = docker_exec(container, ["vtysh", "-c", f"show ip route {prefix} json"], timeout=timeout)
    if rc != 0:
        return False
    return expected_nh_ip in out


# Reconciliação de estado no startup
def reconcile_current_state(routers, lans, nh_ip, ctn):
    ip_to_neighbor = {}
    for (a, b), ip in nh_ip.items():
        ip_to_neighbor[(a, ip)] = b

    current_nh = {}
    prefix_to_router = {v: k for k, v in lans.items()}

    for r in routers:
        data = vtysh_show_route_static_json(ctn(r))
        if not data:
            log.warning("Sem estado de rotas estáticas para %s; presumindo vazio.", r)
            continue

        for prefix, entries in data.items():
            if prefix not in prefix_to_router:
                continue  # rota estática que não é gerenciada pelo STTP
            if not entries:
                continue
            # pega o primeiro nexthop reportado
            nexthops = entries[0].get("nexthops", [])
            if not nexthops:
                continue
            nh_addr = nexthops[0].get("ip")
            neighbor = ip_to_neighbor.get((r, nh_addr))
            if neighbor:
                current_nh[(r, prefix)] = neighbor
                log.info("Reconciliado: %s -> %s via %s (já instalado)", r, prefix, neighbor)

    return current_nh

# Main
def build_arg_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", default="lab1-sttp")
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--ping-deadline", type=int, default=2, help="timeout (s) de cada rajada de ping")
    ap.add_argument("--abs-th-ms", type=float, default=1.0)
    ap.add_argument("--rel-th", type=float, default=0.15)
    ap.add_argument(
        "--hold-down",
        type=int,
        default=30,
        help="segundos mínimos entre trocas para o mesmo prefixo (anti-flap)",
    )
    ap.add_argument("--max-cost", type=float, default=1e6)
    ap.add_argument("--dry-run", action="store_true", help="não instala rotas; apenas calcula e grava CSV")
    ap.add_argument(
        "--no-reconcile",
        action="store_true",
        help="pula a leitura do estado real das rotas no startup (assume tudo vazio, como na versão antiga)",
    )
    ap.add_argument(
        "--verify-routes",
        action="store_true",
        help="após instalar uma rota, confirma via 'show ip route' que o next-hop esperado está presente",
    )
    ap.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return ap


def main():
    args = build_arg_parser().parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    routers = ["r1", "r2", "r3", "r4", "r5"]

    lans = {
        "r1": "192.168.1.0/24",
        "r2": "192.168.2.0/24",
        "r3": "192.168.3.0/24",
        "r4": "192.168.4.0/24",
        "r5": "192.168.5.0/24",
    }

    # (a, if_a, ip_a, b, if_b, ip_b)
    links = [
        ("r1", "eth1", "10.0.12.1", "r2", "eth1", "10.0.12.2"),
        ("r1", "eth2", "10.0.13.1", "r3", "eth1", "10.0.13.2"),
        ("r1", "eth3", "10.0.15.1", "r5", "eth1", "10.0.15.2"),
        ("r2", "eth2", "10.0.25.1", "r5", "eth2", "10.0.25.2"),
        ("r3", "eth2", "10.0.34.1", "r4", "eth1", "10.0.34.2"),
        ("r4", "eth2", "10.0.45.1", "r5", "eth3", "10.0.45.2"),
    ]

    def ctn(n):
        return f"clab-{args.lab}-{n}"

    # Para instalar rota: (src_router, neighbor_router) -> IP do neighbor no link
    nh_ip = {}
    for a, _, ipa, b, _, ipb in links:
        nh_ip[(a, b)] = ipb
        nh_ip[(b, a)] = ipa

    os.makedirs("results/sttp", exist_ok=True)
    link_csv = "results/sttp/link_costs.csv"
    route_csv = "results/sttp/route_changes.csv"

    if not os.path.exists(link_csv):
        with open(link_csv, "w", newline="") as f:
            csv.writer(f).writerow(["ts", "link", "cost_ms"])
    if not os.path.exists(route_csv):
        with open(route_csv, "w", newline="") as f:
            csv.writer(f).writerow(
                [
                    "ts",
                    "src",
                    "dst_prefix",
                    "old_next_hop",
                    "new_next_hop",
                    "old_path_cost_now",
                    "best_path_cost_now",
                    "abs_improve_ms",
                    "rel_improve",
                    "reason",
                ]
            )

    # Estado atual (o que foi instalado) — reconciliado a partir da rede real,
    # em vez de assumido vazio, salvo se --no-reconcile for passado.
    if args.no_reconcile:
        current_nh = {}
        log.info("Reconciliação de estado desabilitada (--no-reconcile); partindo de estado vazio.")
    else:
        log.info("Lendo estado real das rotas estáticas para reconciliação...")
        current_nh = reconcile_current_state(routers, lans, nh_ip, ctn)

    last_change_ts = {}  # (src, prefix) -> timestamp da última troca

    log.info(
        "lab=%s interval=%ss count=%s abs_th=%sms rel_th=%s hold_down=%ss dry_run=%s verify=%s",
        args.lab,
        args.interval,
        args.count,
        args.abs_th_ms,
        args.rel_th,
        args.hold_down,
        args.dry_run,
        args.verify_routes,
    )

    stop = {"flag": False}

    def _handle_signal(signum, frame):
        log.info("Sinal %s recebido, encerrando após a iteração atual...", signum)
        stop["flag"] = True

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    while not stop["flag"]:
        loop_start = time.time()
        ts = loop_start

        # 1) Medir custos dos enlaces (RTT médio simétrico), em paralelo
        link_cost = measure_all_links(
            links,
            ctn,
            count=args.count,
            deadline=args.ping_deadline,
            max_cost=args.max_cost,
        )

        for a, _, _, b, _, _ in links:
            w = link_cost[frozenset((a, b))]
            with open(link_csv, "a", newline="") as f:
                csv.writer(f).writerow([ts, f"{a}<->{b}", round(w, 3)])

        # 2) Montar grafo
        adj = {r: {} for r in routers}
        for a, _, _, b, _, _ in links:
            w = link_cost[frozenset((a, b))]
            adj[a][b] = w
            adj[b][a] = w

        dist_map = {}
        prev_map = {}
        for n in routers:
            dist_map[n], prev_map[n] = dijkstra(routers, adj, n)

        # 3) Para cada (src, LAN_dst), decidir e aplicar rota
        for src in routers:
            for dst in routers:
                if dst == src:
                    continue

                prefix = lans[dst]
                best_cost = dist_map[src][dst]
                nh = next_hop(prev_map[src], src, dst)

                if nh is None or best_cost >= args.max_cost:
                    continue  # destino inalcançável neste momento

                k = (src, prefix)
                old_nh = current_nh.get(k)

                last_ts = last_change_ts.get(k, 0)
                in_hold = (ts - last_ts) < args.hold_down

                change = False
                reason = ""
                old_path_cost_now = math.inf

                if old_nh is None:
                    change = True
                    reason = "initial_install"
                elif nh == old_nh:
                    continue  # já está no melhor next-hop, nada a fazer
                else:
                    if old_nh not in adj[src]:
                        old_path_cost_now = args.max_cost
                    else:
                        old_path_cost_now = adj[src][old_nh] + dist_map[old_nh][dst]

                    if old_path_cost_now >= args.max_cost and best_cost < args.max_cost:
                        change = True
                        reason = "old_path_unreachable"
                    else:
                        abs_improve = old_path_cost_now - best_cost
                        rel_improve = abs_improve / max(old_path_cost_now, 0.001)

                        if abs_improve > args.abs_th_ms and rel_improve > args.rel_th:
                            change = True
                            reason = "significant_improvement"
                        else:
                            reason = "not_significant"

                if change and in_hold and reason != "old_path_unreachable":
                    change = False
                    reason = "hold_down_blocked"

                if not change:
                    continue

                old_nh_ip = nh_ip.get((src, old_nh)) if old_nh else None
                new_nh_ip = nh_ip.get((src, nh))
                if new_nh_ip is None:
                    log.error("Next-hop IP não encontrado para %s -> %s; pulando.", src, nh)
                    continue

                abs_improve_ms = (old_path_cost_now - best_cost) if (old_nh is not None) else math.nan
                rel_improve = (
                    (abs_improve_ms / max(old_path_cost_now, 0.001)) if (old_nh is not None) else math.nan
                )

                with open(route_csv, "a", newline="") as f:
                    csv.writer(f).writerow(
                        [
                            ts,
                            src,
                            prefix,
                            old_nh,
                            nh,
                            round(old_path_cost_now, 3) if old_nh is not None else "",
                            round(best_cost, 3),
                            round(abs_improve_ms, 3) if old_nh is not None else "",
                            round(rel_improve, 6) if old_nh is not None else "",
                            reason,
                        ]
                    )

                if not args.dry_run:
                    rc, out, err = vtysh_set_route(ctn(src), prefix, old_nh_ip, new_nh_ip)
                    if rc != 0 or frr_output_has_error(out, err):
                        log.error("Falha ao aplicar rota %s %s: rc=%s out=%r err=%r", src, prefix, rc, out, err)
                        continue  # não atualiza current_nh, tenta de novo na próxima iteração

                    if args.verify_routes:
                        ok = verify_route_installed(ctn(src), prefix, new_nh_ip)
                        if not ok:
                            log.error(
                                "Verificação falhou: %s %s não mostra next-hop %s após instalação.",
                                src,
                                prefix,
                                new_nh_ip,
                            )
                            continue  # não atualiza current_nh

                current_nh[k] = nh
                last_change_ts[k] = ts
                log.info("Rota alterada: %s -> %s via %s (era %s) [%s]", src, prefix, nh, old_nh, reason)

        elapsed = time.time() - loop_start
        sleep_for = max(0.0, args.interval - elapsed)
        if elapsed > args.interval:
            log.warning(
                "Iteração levou %.2fs, mais que o --interval de %ss; próxima iteração inicia imediatamente.",
                elapsed,
                args.interval,
            )
        if not stop["flag"]:
            time.sleep(sleep_for)

    log.info("Encerrado.")


if __name__ == "__main__":
    main()