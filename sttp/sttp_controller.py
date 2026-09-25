#!/usr/bin/env python3
"""
STTP Controller (Shortest Trip Time Protocol) - versão com:
- cálculo correto de troca comparando o melhor caminho atual vs. custo ATUAL do caminho antigo
- hold-down (anti-flap adicional)
- CSV com motivo e valores de comparação
- opção --dry-run (não instala rotas, só calcula e registra)
"""

import argparse
import subprocess
import re
import time
import math
import csv
import os
from heapq import heappush, heappop

PING_AVG_RE = re.compile(
    r"(?:rtt|round-trip).* = [0-9.]+/([0-9.]+)/[0-9.]+/[0-9.]+ ms"
)

# ----------------------------
# Helpers de execução
# ----------------------------
def sh(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()

def docker_exec(container, argv):
    return sh(["docker", "exec", container] + argv)

# ----------------------------
# Medição (RTT por enlace)
# ----------------------------
def ping_avg_ms(container, iface, dst_ip, count=3, deadline=2):
    # ping -q imprime apenas o sumário final, de onde extraímos avg RTT
    rc, out, err = docker_exec(
        container,
        ["ping", "-n", "-q", "-I", iface, "-c", str(count), "-w", str(deadline), dst_ip],
    )
    if rc != 0:
        return None
    m = PING_AVG_RE.search(out)
    return float(m.group(1)) if m else None

# ----------------------------
# Dijkstra
# ----------------------------
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

# ----------------------------
# Instalação de rotas estáticas via FRR
# ----------------------------
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

# ----------------------------
# Main
# ----------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", default="lab1-sttp")
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--abs-th-ms", type=float, default=1.0)
    ap.add_argument("--rel-th", type=float, default=0.15)
    ap.add_argument("--hold-down", type=int, default=30, help="segundos mínimos entre trocas para o mesmo prefixo (anti-flap)")
    ap.add_argument("--max-cost", type=float, default=1e6)
    ap.add_argument("--dry-run", action="store_true", help="não instala rotas; apenas calcula e grava CSV")
    args = ap.parse_args()

    routers = ["r1", "r2", "r3", "r4", "r5"]

    # LANs (prefixos destino)
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

    # Para instalar rota: (src_router, neighbor_router) -> IP do neighbor no link (next-hop IP)
    nh_ip = {}
    for a, _, ipa, b, _, ipb in links:
        nh_ip[(a, b)] = ipb
        nh_ip[(b, a)] = ipa

    # Arquivos de resultados
    os.makedirs("results/sttp", exist_ok=True)
    link_csv = "results/sttp/link_costs.csv"
    route_csv = "results/sttp/route_changes.csv"

    if not os.path.exists(link_csv):
        with open(link_csv, "w", newline="") as f:
            csv.writer(f).writerow(["ts", "link", "cost_ms"])
    if not os.path.exists(route_csv):
        with open(route_csv, "w", newline="") as f:
            csv.writer(f).writerow([
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
            ])

    # Estado atual (o que foi instalado)
    current_nh = {}        # (src, prefix) -> neighbor_router
    last_change_ts = {}    # (src, prefix) -> timestamp da última troca

    print(
        "[STTP] "
        f"lab={args.lab} interval={args.interval}s count={args.count} "
        f"abs_th={args.abs_th_ms}ms rel_th={args.rel_th} hold_down={args.hold_down}s "
        f"dry_run={args.dry_run}"
    )

    while True:
        ts = time.time()

        # ----------------------------
        # 1) Medir custos dos enlaces (RTT médio simétrico)
        # ----------------------------
        link_cost = {}  # frozenset({a,b}) -> cost
        for a, ifa, ipa, b, ifb, ipb in links:
            rtt_ab = ping_avg_ms(ctn(a), ifa, ipb, count=args.count)
            rtt_ba = ping_avg_ms(ctn(b), ifb, ipa, count=args.count)

            w = args.max_cost if (rtt_ab is None or rtt_ba is None) else (rtt_ab + rtt_ba) / 2.0
            link_cost[frozenset((a, b))] = w

            with open(link_csv, "a", newline="") as f:
                csv.writer(f).writerow([ts, f"{a}<->{b}", round(w, 3)])

        # ----------------------------
        # 2) Montar grafo
        # ----------------------------
        adj = {r: {} for r in routers}
        for a, _, _, b, _, _ in links:
            w = link_cost[frozenset((a, b))]
            adj[a][b] = w
            adj[b][a] = w

        # Pré-calcular Dijkstra a partir de todos (pequeno: 5 nós)
        dist_map = {}
        prev_map = {}
        for n in routers:
            dist_map[n], prev_map[n] = dijkstra(routers, adj, n)

        # ----------------------------
        # 3) Para cada (src, LAN_dst), decidir e aplicar rota
        # ----------------------------
        for src in routers:
            for dst in routers:
                if dst == src:
                    continue

                prefix = lans[dst]
                best_cost = dist_map[src][dst]
                nh = next_hop(prev_map[src], src, dst)

                if nh is None or best_cost >= args.max_cost:
                    # destino inalcançável neste momento
                    continue

                k = (src, prefix)
                old_nh = current_nh.get(k)

                # Hold-down: evita trocar frequentemente
                last_ts = last_change_ts.get(k, 0)
                in_hold = (ts - last_ts) < args.hold_down

                change = False
                reason = ""

                if old_nh is None:
                    change = True
                    reason = "initial_install"
                    old_path_cost_now = math.inf
                elif nh == old_nh:
                    # Continua no mesmo próximo salto; não faz nada
                    continue
                else:
                    # custo ATUAL do caminho antigo, forçando 1º salto = old_nh
                    if old_nh not in adj[src]:
                        old_path_cost_now = args.max_cost
                    else:
                        old_path_cost_now = adj[src][old_nh] + dist_map[old_nh][dst]

                    # Se o antigo ficou inviável e o novo é viável
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
                            change = False
                            reason = "not_significant"

                # Se está em hold-down, não troca (exceto se o caminho antigo morreu)
                if change and in_hold and reason != "old_path_unreachable":
                    change = False
                    reason = "hold_down_blocked"

                # Registrar decisão (somente quando houver troca)
                if change:
                    # next-hop IPs
                    old_nh_ip = nh_ip.get((src, old_nh)) if old_nh else None
                    new_nh_ip = nh_ip.get((src, nh))

                    if new_nh_ip is None:
                        continue  # não deveria acontecer na topologia fixa

                    # escreve CSV com informações de comparação
                    abs_improve_ms = (old_path_cost_now - best_cost) if (old_nh is not None) else math.nan
                    rel_improve = (abs_improve_ms / max(old_path_cost_now, 0.001)) if (old_nh is not None) else math.nan

                    with open(route_csv, "a", newline="") as f:
                        csv.writer(f).writerow([
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
                        ])

                    # aplica rota (ou não, no dry-run)
                    if not args.dry_run:
                        rc, out, err = vtysh_set_route(ctn(src), prefix, old_nh_ip, new_nh_ip)
                        if rc != 0:
                            print(f"[STTP][ERR] {src} {prefix}: {err or out}")
                            continue

                    current_nh[k] = nh
                    last_change_ts[k] = ts

        time.sleep(args.interval)

if __name__ == "__main__":
    main()