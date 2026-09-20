# Containerlab + FRR — Laboratório de Roteamento (RIP e OSPF)

Este repositório contém um laboratório de rede (Containerlab + Docker) com **5 roteadores (FRRouting/FRR)** e **5 hosts**, pronto para experimentar e comparar **RIPv2** e **OSPFv2** na mesma topologia (executados **separadamente**, não simultaneamente).

> Ambiente recomendado: Ubuntu (incluindo WSL2 no Windows 11) com Docker e Containerlab instalados.

---

## Estrutura do projeto

```text
.
├── topology-rip.clab.yml        # Sobe a topologia com RIP (ripd ativo + config RIP)
├── topology-ospf.clab.yml       # Sobe a topologia com OSPF (ospfd ativo + config OSPF)
├── configs/
│   ├── common/
│   │   └── vtysh.conf           # Arquivo mínimo (evita warning do vtysh)
│   ├── rip/
│   │   ├── r1/{daemons,frr.conf}
│   │   ├── r2/{daemons,frr.conf}
│   │   ├── r3/{daemons,frr.conf}
│   │   ├── r4/{daemons,frr.conf}
│   │   └── r5/{daemons,frr.conf}
│   └── ospf/
│       ├── r1/{daemons,frr.conf}
│       ├── r2/{daemons,frr.conf}
│       ├── r3/{daemons,frr.conf}
│       ├── r4/{daemons,frr.conf}
│       └── r5/{daemons,frr.conf}
└── .gitignore
```

### Como as configurações funcionam

- Os **IPs das interfaces** são configurados pelo `exec:` dentro do YAML (com `ip addr add ...`).
- O **roteamento dinâmico** é configurado pelo FRR via:
  - `configs/<rip|ospf>/rX/daemons` (quais daemons iniciam)
  - `configs/<rip|ospf>/rX/frr.conf` (config do protocolo)
- Cada lab usa um nome diferente:
  - RIP: `name: lab1-rip`
  - OSPF: `name: lab1-ospf`

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

## Endereçamento (resumo)

### Links roteador↔roteador (/30)

| Link | Rede | IPs |
|---|---|---|
| R1–R2 | 10.0.12.0/30 | R1=10.0.12.1, R2=10.0.12.2 |
| R1–R3 | 10.0.13.0/30 | R1=10.0.13.1, R3=10.0.13.2 |
| R1–R5 | 10.0.15.0/30 | R1=10.0.15.1, R5=10.0.15.2 |
| R2–R5 | 10.0.25.0/30 | R2=10.0.25.1, R5=10.0.25.2 |
| R3–R4 | 10.0.34.0/30 | R3=10.0.34.1, R4=10.0.34.2 |
| R4–R5 | 10.0.45.0/30 | R4=10.0.45.1, R5=10.0.45.2 |

### LANs (/24)

| LAN | Rede | Roteador | Host |
|---|---|---|---|
| LAN1 | 192.168.1.0/24 | R1=192.168.1.1 | h1=192.168.1.10 |
| LAN2 | 192.168.2.0/24 | R2=192.168.2.1 | h2=192.168.2.10 |
| LAN3 | 192.168.3.0/24 | R3=192.168.3.1 | h3=192.168.3.10 |
| LAN4 | 192.168.4.0/24 | R4=192.168.4.1 | h4=192.168.4.10 |
| LAN5 | 192.168.5.0/24 | R5=192.168.5.1 | h5=192.168.5.10 |

---

## Pré-requisitos

- Docker funcionando (`docker ps`)
- Containerlab funcionando (`clab version`)
- Acesso com privilégios (na prática, muitos comandos usam `sudo`)

---

## Como subir e derrubar a rede

### Subir o lab com RIP

Sobe o cenário com RIPv2 (`ripd` ativo e configurado).

```bash
sudo clab deploy -t topology-rip.clab.yml
```

**Validar:**

```bash
sudo docker exec -it clab-lab1-rip-r1 vtysh -c "show daemons"
sudo docker exec -it clab-lab1-rip-r1 vtysh -c "show ip route rip"
sudo docker exec -it clab-lab1-rip-h2 ping -c 3 192.168.4.10
```

**Derrubar:**

```bash
sudo clab destroy -t topology-rip.clab.yml
```

### Subir o lab com OSPF

Sobe o cenário com OSPFv2 (`ospfd` ativo e configurado).

```bash
sudo clab deploy -t topology-ospf.clab.yml
```

**Validar:**

```bash
sudo docker exec -it clab-lab1-ospf-r1 vtysh -c "show daemons"
sudo docker exec -it clab-lab1-ospf-r1 vtysh -c "show ip ospf neighbor"
sudo docker exec -it clab-lab1-ospf-r1 vtysh -c "show ip route ospf"
sudo docker exec -it clab-lab1-ospf-h2 ping -c 3 192.168.4.10
```

**Derrubar:**

```bash
sudo clab destroy -t topology-ospf.clab.yml
```

---

## Observações importantes

- Execute apenas **um lab por vez** (RIP ou OSPF), para cumprir o requisito de não executar simultaneamente.
- Se ocorrer erro por "resíduos" de labs anteriores, faça limpeza:

```bash
docker rm -f $(docker ps -aq --filter "name=clab-lab1") 2>/dev/null || true
docker network rm clab 2>/dev/null || true
```

- Os roteadores usam `privileged: true` para evitar problemas de permissões (comuns em WSL2/Docker Desktop).