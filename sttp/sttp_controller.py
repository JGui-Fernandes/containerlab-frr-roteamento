#!/usr/bin/env python3
import argparse, subprocess, re, time, math, csv, os
from heapq import heappush, heappop

PING_AVG_RE = re.compile(r"rtt .* = [0-9.]+/([0-9.]+)/[0-9.]+/[0-9.]+ ms")

def sh(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout.strip(), p.stderr.strip()

def docker_exec(container, argv):
    return sh(["docker", "exec", container] + argv)

def ping_avg_ms(container, iface, dst_ip, count=3, deadline=2):
    rc, out, err = docker_exec(container, ["ping", "-n", "-q", "-I", iface, "-c", str(count), "-w", str(deadline), dst_ip])
    if rc != 0:
        return None
    m = PING_AVG_RE.search(out)
    return float(m.group(1)) if m else None

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

def vtysh_set_route(container, prefix, old_nh, new_nh):
    cmds = ["conf t"]
    if old_nh:
        cmds.append(f"no ip route {prefix} {old_nh}")
    cmds.append(f"ip route {prefix} {new_nh}")
    cmds.append("end")
    argv = ["vtysh"]
    for c in cmds:
        argv += ["-c", c]
    return docker_exec(container, argv)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lab", default="lab1-sttp")
    ap.add_argument("--interval", type=int, default=10)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--abs-th-ms", type=float, default=1.0)
    ap.add_argument("--rel-th", type=float, default=0.15)
    ap.add_argument("--max-cost", type=float, default=1e6)
    args = ap.parse_args()

    routers = ["r1","r2","r3","r4","r5"]

    lans = {
        "r1": "192.168.1.0/24",
        "r2": "192.168.2.0/24",
        "r3": "192.168.3.0/24",
        "r4": "192.168.4.0/24",
        "r5": "192.168.5.0/24",
    }

    # (a, if_a, ip_a, b, if_b, ip_b)
    links = [
        ("r1","eth1","10.0.12.1", "r2","eth1","10.0.12.2"),
        ("r1","eth2","10.0.13.1", "r3","eth1","10.0.13.2"),
        ("r1","eth3","10.0.15.1", "r5","eth1","10.0.15.2"),
        ("r2","eth2","10.0.25.1", "r5","eth2","10.0.25.2"),
        ("r3","eth2","10.0.34.1", "r4","eth1","10.0.34.2"),
        ("r4","eth2","10.0.45.1", "r5","eth3","10.0.45.2"),
    ]

    def ctn(n): return f"clab-{args.lab}-{n}"

    nh_ip = {}
    for a, _, ipa, b, _, ipb in links:
        nh_ip[(a,b)] = ipb
        nh_ip[(b,a)] = ipa

    os.makedirs("results/sttp", exist_ok=True)
    link_csv = "results/sttp/link_costs.csv"
    route_csv = "results/sttp/route_changes.csv"

    if not os.path.exists(link_csv):
        with open(link_csv, "w", newline="") as f:
            csv.writer(f).writerow(["ts","link","cost_ms"])
    if not os.path.exists(route_csv):
        with open(route_csv, "w", newline="") as f:
            csv.writer(f).writerow(["ts","src","dst_prefix","next_hop","path_cost"])

    current_nh = {}   # (src, prefix) -> neighbor
    current_cost = {} # (src, prefix) -> cost

    print(f"[STTP] lab={args.lab} interval={args.interval}s count={args.count} abs_th={args.abs_th_ms}ms rel_th={args.rel_th}")

    while True:
        ts = time.time()

        # Measure link costs
        cost = {}
        for a, ifa, ipa, b, ifb, ipb in links:
            rtt_ab = ping_avg_ms(ctn(a), ifa, ipb, count=args.count)
            rtt_ba = ping_avg_ms(ctn(b), ifb, ipa, count=args.count)
            w = args.max_cost if (rtt_ab is None or rtt_ba is None) else (rtt_ab + rtt_ba)/2.0
            cost[frozenset((a,b))] = w
            with open(link_csv, "a", newline="") as f:
                csv.writer(f).writerow([ts, f"{a}<->{b}", round(w,3)])

        # Build adjacency
        adj = {r:{} for r in routers}
        for a, _, _, b, _, _ in links:
            w = cost[frozenset((a,b))]
            adj[a][b] = w
            adj[b][a] = w

        # Compute and apply routes
        for src in routers:
            dist, prev = dijkstra(routers, adj, src)
            for dst in routers:
                if dst == src:
                    continue
                prefix = lans[dst]
                nh = next_hop(prev, src, dst)
                if nh is None:
                    continue
                new_cost = dist[dst]

                k = (src, prefix)
                old_nh = current_nh.get(k)
                old_cost = current_cost.get(k)

                change = False
                if old_nh is None:
                    change = True
                elif nh != old_nh and old_cost is not None:
                    abs_improve = old_cost - new_cost
                    rel_improve = abs_improve / max(old_cost, 0.001)
                    if abs_improve > args.abs_th_ms and rel_improve > args.rel_th:
                        change = True

                if change:
                    rc, out, err = vtysh_set_route(ctn(src), prefix,
                                                   nh_ip[(src, old_nh)] if old_nh else None,
                                                   nh_ip[(src, nh)])
                    if rc == 0:
                        current_nh[k] = nh
                        current_cost[k] = new_cost
                        with open(route_csv, "a", newline="") as f:
                            csv.writer(f).writerow([ts, src, prefix, nh, round(new_cost,3)])
                    else:
                        print(f"[STTP][ERR] {src} {prefix}: {err or out}")

        time.sleep(args.interval)

if __name__ == "__main__":
    main()