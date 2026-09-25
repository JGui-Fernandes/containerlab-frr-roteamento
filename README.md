# Containerlab + FRR — Laboratório de Roteamento (RIP, OSPF e STTP)

Este repositório contém um laboratório de rede (Containerlab + Docker) com **5 roteadores (FRRouting/FRR)** e **5 hosts**, pronto para:

- configurar e comparar **RIPv2** e **OSPFv2** na mesma topologia (**executados separadamente**);
- executar um algoritmo próprio de roteamento: **STTP — Shortest Trip Time Protocol** (roteamento baseado em RTT medido via `ping`, com estabilidade anti-flap).

> Ambiente recomendado: Ubuntu (incluindo WSL2 no Windows 11) com Docker e Containerlab instalados.

---

## Estrutura do projeto

```text
.
├── README.md
├── .gitignore
├── .gitattributes
│
├── topologies/
│   ├── topology-rip.clab.yml        # Sobe a topologia com RIP (ripd ativo + config RIP)
│   ├── topology-ospf.clab.yml       # Sobe a topologia com OSPF (ospfd ativo + config OSPF)
│   └── topology-sttp.clab.yml       # Sobe a topologia base para o STTP (sem RIP/OSPF)
│
├── configs/
│   ├── common/
│   │   └── vtysh.conf               # Arquivo mínimo (evita warning do vtysh)
│   ├── rip/
│   │   └── r1..r5/{daemons,frr.conf}
│   ├── ospf/
│   │   └── r1..r5/{daemons,frr.conf}
│   └── sttp/
│       └── r1..r5/{daemons,frr.conf}  # apenas zebra/staticd (sem ripd/ospfd)
│
├── sttp/
│   └── sttp_controller.py            # Controller do STTP (mede RTT, calcula rotas e instala rotas estáticas)
│
└── results/
    └── sttp/
        ├── link_costs.csv            # histórico de custos (RTT) por enlace
        └── route_changes.csv         # trocas de rota efetuadas pelo STTP
```

### Como as configurações funcionam

- Os **IPs das interfaces** são configurados pelo **`exec:`** dentro do YAML (com **`ip addr add ...`**).
- O roteamento depende do cenário:
  - **RIP**: **`configs/rip/rX/{daemons,frr.conf}`** (ripd ligado e configurado)
  - **OSPF**: **`configs/ospf/rX/{daemons,frr.conf}`** (ospfd ligado e configurado)
  - **STTP**: **`configs/sttp/rX/{daemons,frr.conf}`** (sem ripd/ospfd; STTP instala rotas estáticas via **`vtysh`**)
- Cada lab usa um nome diferente:
  - RIP: **`name: lab1-rip`**
  - OSPF: **`name: lab1-ospf`**
  - STTP: **`name: lab1-sttp`**

> ***Observação: todos os nós também possuem uma interface `eth0` de gerenciamento criada pelo containerlab/Docker. Ela não faz parte do experimento de roteamento (RIP/OSPF/STTP).***

> ***Observação 2: como os YAMLs estão em `topologies/`, os `binds:` devem referenciar `../configs/...` (caminho relativo a partir do YAML).***

---

## Topologia

### Conexões entre roteadores

- R1–R2
- R1–R3
- R1–R5
- R2–R5
- R3–R4
- R4–R5

### Hosts (LANs)

- h1 atrás do R1 (LAN1)
- h2 atrás do R2 (LAN2)
- h3 atrás do R3 (LAN3)
- h4 atrás do R4 (LAN4)
- h5 atrás do R5 (LAN5)

---

## Endereçamento (resumo)

### Links roteador↔roteador (/30)

| **Link** | **Rede** | **IPs** |
|---|---|---|
| R1–R2 | 10.0.12.0/30 | R1=10.0.12.1, R2=10.0.12.2 |
| R1–R3 | 10.0.13.0/30 | R1=10.0.13.1, R3=10.0.13.2 |
| R1–R5 | 10.0.15.0/30 | R1=10.0.15.1, R5=10.0.15.2 |
| R2–R5 | 10.0.25.0/30 | R2=10.0.25.1, R5=10.0.25.2 |
| R3–R4 | 10.0.34.0/30 | R3=10.0.34.1, R4=10.0.34.2 |
| R4–R5 | 10.0.45.0/30 | R4=10.0.45.1, R5=10.0.45.2 |

### LANs (/24)

| **LAN** | **Rede** | **Roteador** | **Host** |
|---|---|---|---|
| LAN1 | 192.168.1.0/24 | R1=192.168.1.1 | h1=192.168.1.10 |
| LAN2 | 192.168.2.0/24 | R2=192.168.2.1 | h2=192.168.2.10 |
| LAN3 | 192.168.3.0/24 | R3=192.168.3.1 | h3=192.168.3.10 |
| LAN4 | 192.168.4.0/24 | R4=192.168.4.1 | h4=192.168.4.10 |
| LAN5 | 192.168.5.0/24 | R5=192.168.5.1 | h5=192.168.5.10 |

---

## Pré-requisitos

- Docker funcionando: **`docker ps`**
- Containerlab funcionando: **`clab version`**
- Acesso com privilégios (na prática, muitos comandos usam **`sudo`**)

---

## Como subir e derrubar a rede

> ***Execute apenas um lab por vez (RIP ou OSPF ou STTP).***

### Subir o lab com RIP

Sobe o cenário com **RIPv2** (**`ripd`** ativo e configurado).

```bash
sudo clab deploy -t topologies/topology-rip.clab.yml
```

Validar:

```bash
sudo docker exec -it clab-lab1-rip-r1 vtysh -c "show daemons"
sudo docker exec -it clab-lab1-rip-r1 vtysh -c "show ip route rip"
sudo docker exec -it clab-lab1-rip-h2 ping -c 3 192.168.4.10
```

Derrubar:

```bash
sudo clab destroy -t topologies/topology-rip.clab.yml
```

---

### Subir o lab com OSPF

Sobe o cenário com **OSPFv2** (**`ospfd`** ativo e configurado).

```bash
sudo clab deploy -t topologies/topology-ospf.clab.yml
```

Validar:

```bash
sudo docker exec -it clab-lab1-ospf-r1 vtysh -c "show daemons"
sudo docker exec -it clab-lab1-ospf-r1 vtysh -c "show ip ospf neighbor"
sudo docker exec -it clab-lab1-ospf-r1 vtysh -c "show ip route ospf"
sudo docker exec -it clab-lab1-ospf-h2 ping -c 3 192.168.4.10
```

Derrubar:

```bash
sudo clab destroy -t topologies/topology-ospf.clab.yml
```

---

## STTP — Shortest Trip Time Protocol

O STTP é um algoritmo próprio de roteamento que:

1. mede o **RTT** (**`ping`**) de cada enlace roteador↔roteador;
2. constrói um grafo ponderado por atraso;
3. calcula os melhores caminhos (menor “trip time”) e instala **rotas estáticas** nos roteadores via **`vtysh`**;
4. só troca rotas quando a melhoria é significativa (anti-flap) e respeita hold-down.

### Subir o lab base do STTP

```bash
sudo clab deploy -t topologies/topology-sttp.clab.yml
```

Confirme que não há RIP/OSPF:

```bash
sudo docker exec -it clab-lab1-sttp-r1 vtysh -c "show daemons"
```

### Rodar o controller do STTP

Em um terminal:

```bash
chmod +x sttp/sttp_controller.py
./sttp/sttp_controller.py --lab lab1-sttp --interval 10 --count 3 --abs-th-ms 0.05 --rel-th 0.15 --hold-down 10
```

> ***Dica: em laboratório com RTTs muito baixos, `--abs-th-ms` deve ser pequeno (ex.: 0.05 ou 0.01), senão o STTP pode demorar para “voltar” ao melhor caminho após uma falha.***

### Validar rotas instaladas (exemplo no R2)

```bash
sudo docker exec -it clab-lab1-sttp-r2 vtysh -c "show ip route"
sudo docker exec -it clab-lab1-sttp-h2 ping -c 3 192.168.4.10
sudo docker exec -it clab-lab1-sttp-h2 traceroute -n 192.168.4.10
```

### Teste de falha (exemplo: derrubar link R2–R5)

```bash
sudo docker exec -it clab-lab1-sttp-h2 traceroute -n 192.168.5.10
sudo docker exec -it clab-lab1-sttp-r2 ip link set eth2 down
sudo docker exec -it clab-lab1-sttp-h2 traceroute -n 192.168.5.10
sudo docker exec -it clab-lab1-sttp-r2 ip link set eth2 up
```

### Ver os resultados (CSVs)

```bash
tail -n 10 results/sttp/link_costs.csv
tail -n 20 results/sttp/route_changes.csv
```

### Derrubar o STTP

1. Pare o controller (no terminal dele): **`Ctrl+C`**
2. Derrube o lab:

```bash
sudo clab destroy -t topologies/topology-sttp.clab.yml
```

---

## Observações importantes / Troubleshooting

### Limpeza (resíduos de labs anteriores)

Se ocorrer erro por conflitos de containers/redes:

```bash
docker rm -f $(docker ps -aq --filter "name=clab-lab1") 2>/dev/null || true
docker network rm clab 2>/dev/null || true
```

### Permissões

Os roteadores usam **`privileged: true`** para evitar problemas de permissões (comuns em WSL2/Docker Desktop).
