# Containerlab + FRR — Laboratório de Roteamento (RIP, OSPF e STTP)

Este repositório contém um laboratório de rede (Containerlab + Docker) com **5 roteadores (FRRouting/FRR)** e **5 hosts**, pronto para:

- configurar e comparar **RIPv2** e **OSPFv2** na mesma topologia (**executados separadamente**);
- executar um algoritmo próprio de roteamento: **STTP — Shortest Trip Time Protocol** (roteamento baseado em RTT medido via `ping`, com anti-flap, detecção de loop e reconciliação de estado).

> Ambiente recomendado: Ubuntu com Docker e Containerlab instalados.

# Tópicos
- [⚙️ Instalação de ferramentas](#️-instalação-de-ferramentas)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Topologia](#topologia)
- [Endereçamento](#endereçamento-resumo)
- [Como subir e derrubar a rede](#como-subir-e-derrubar-a-rede)
- [STTP — Shortest Trip Time Protocol](#sttp--shortest-trip-time-protocol)
- [Troubleshooting](#observações-importantes--troubleshooting)

# ⚙️ Instalação de ferramentas

- Docker: https://docs.docker.com/
- Containerlab: https://containerlab.dev/

Depois de instalar, confirme que ambos estão funcionando:

```bash
docker ps
clab version
```

> Na prática, muitos comandos deste guia usam `sudo`.

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
│   │   └── vtysh.conf                # Arquivo mínimo (evita warning do vtysh)
│   ├── rip/
│   │   └── r1..r5/{daemons,frr.conf}
│   ├── ospf/
│   │   └── r1..r5/{daemons,frr.conf}
│   └── sttp/
│       └── r1..r5/{daemons,frr.conf} # apenas zebra/staticd (sem ripd/ospfd)
│
├── sttp/
│   └── sttp_controller.py            # Controller do STTP (mede RTT, calcula rotas, detecta loop e instala rotas estáticas)
│
└── results/
    └── sttp/
        ├── link_costs.csv            # histórico de custos (RTT) por enlace — gerado em runtime, não versionado
        └── route_changes.csv         # trocas de rota efetuadas pelo STTP — gerado em runtime, não versionado
```

> **Nota:** os arquivos dentro de `results/` são gerados quando o controller roda; eles não fazem parte do repositório (ver seção "Sobre os arquivos de resultado" no final).

### Como as configurações funcionam

- Os **IPs das interfaces** são configurados pelo **`exec:`** dentro do YAML (com **`ip addr add ...`**).
- O roteamento depende do cenário:
  - **RIP**: `configs/rip/rX/{daemons,frr.conf}` (ripd ligado e configurado)
  - **OSPF**: `configs/ospf/rX/{daemons,frr.conf}` (ospfd ligado e configurado)
  - **STTP**: `configs/sttp/rX/{daemons,frr.conf}` (sem ripd/ospfd; o STTP instala rotas estáticas via `vtysh`)
- Cada lab usa um nome diferente:
  - RIP: `name: lab1-rip`
  - OSPF: `name: lab1-ospf`
  - STTP: `name: lab1-sttp`
- Os containers dos roteadores já sobem com `net.ipv4.conf.all.rp_filter: 0` e `net.ipv4.conf.default.rp_filter: 0`, desabilitando o filtro de caminho reverso estrito do kernel. Isso é necessário porque, sob roteamento dinâmico por menor latência (STTP), é normal o caminho de ida e o de volta entre dois hosts serem assimétricos (cada roteador escolhe seu próprio melhor caminho de forma independente) — com `rp_filter` estrito, pacotes legítimos nesse cenário seriam descartados pelo kernel.

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

## Como subir e derrubar a rede

> ***Execute apenas um lab por vez (RIP ou OSPF ou STTP).***

### Subir o lab com RIP

Sobe o cenário com **RIPv2** (`ripd` ativo e configurado).

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

Sobe o cenário com **OSPFv2** (`ospfd` ativo e configurado).

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

O STTP é um controlador de roteamento centralizado (roda fora dos roteadores, com visão de toda a topologia) que:

1. mede o **RTT real** (`ping`) de cada um dos 6 enlaces roteador↔roteador, em paralelo, a cada ciclo;
2. constrói um grafo não-direcionado ponderado por esse RTT e roda **Dijkstra a partir de cada roteador**;
3. decide, para cada par (origem, destino), se vale a pena trocar a rota atual pela rota de menor custo, usando uma política de anti-flap (limiares + hold-down);
4. **detecta e corrige automaticamente loops de roteamento** que possam surgir da combinação de decisões independentes de roteadores vizinhos;
5. instala as rotas estáticas resultantes via `vtysh`, com verificação opcional de que a instalação realmente funcionou;
6. registra tudo (custos medidos e trocas de rota) em CSV para análise posterior.

### Ciclo de controle, em detalhe

Cada iteração do controller tenta caber dentro de `--interval` segundos e segue estas fases:

1. **Medição** — dispara pings em paralelo nos dois sentidos de cada enlace; se um dos lados falhar, o enlace inteiro recebe custo `--max-cost` (tratado como praticamente inalcançável, sem precisar remover o enlace do grafo).
2. **Cálculo** — roda Dijkstra a partir de cada um dos 5 roteadores, usando o mesmo grafo, o que garante consistência entre as decisões de todos eles.
3. **Decisão** — para cada par (origem, destino), compara o custo do caminho atualmente instalado com o do melhor caminho encontrado, e só marca a rota para troca se:
   - não havia rota instalada ainda (`initial_install`); ou
   - o caminho antigo ficou inalcançável (`old_path_unreachable` — ignora limiares e hold-down, pois precisa reagir imediatamente); ou
   - a melhoria supera **ao mesmo tempo** o limiar absoluto (`--abs-th-ms`) e o relativo (`--rel-th`) (`significant_improvement`); e, além disso, não está bloqueada por hold-down (`--hold-down`) recente.
4. **Detecção de loop** — antes de aplicar qualquer coisa na rede, o controller simula a cadeia de próximos-saltos que seria efetivamente instalada em cada par (origem, destino). Se essa cadeia não converge para o destino (ou seja, volta a visitar um roteador já percorrido), um loop foi identificado — geralmente porque um roteador reagiu rápido a uma falha (`old_path_unreachable`) escolhendo como novo caminho um vizinho cuja própria rota ainda não mudou, por não ter passado nos limiares isoladamente. Nesse caso, os roteadores envolvidos no ciclo são forçados a adotar o próximo-salto realmente ótimo do Dijkstra, ignorando limiares e hold-down (motivo `loop_break`). Essa checagem é repetida em rodadas sucessivas até estabilizar.
5. **Aplicação** — para cada rota marcada para troca, remove a rota estática antiga e instala a nova via `vtysh`, detectando erros mesmo quando o `vtysh` retorna código de saída 0 (o que ele faz mesmo em caso de falha). Com `--verify-routes`, ainda confirma via `show ip route` que o próximo-salto esperado está de fato instalado antes de considerar a troca bem-sucedida.
6. **Espera** — dorme o tempo restante até completar `--interval` segundos desde o início do ciclo; se o ciclo demorou mais que isso, começa o próximo imediatamente e loga um aviso.

No início da execução, em vez de assumir que nenhuma rota está instalada, o controller consulta o estado real de rotas estáticas em cada roteador (`show ip route static`) e usa isso para inicializar sua memória interna — evitando rotas duplicadas se o script for reiniciado no meio de um experimento (comportamento padrão; desative com `--no-reconcile` se quiser voltar ao comportamento antigo de partir sempre de estado vazio).

### Subir o lab base do STTP

```bash
sudo clab deploy -t topologies/topology-sttp.clab.yml
```

Confirme que não há RIP/OSPF:

```bash
sudo docker exec -it clab-lab1-sttp-r1 vtysh -c "show daemons"
```

### Rodar o controller do STTP

```bash
chmod +x sttp/sttp_controller.py
./sttp/sttp_controller.py \
  --lab lab1-sttp \
  --interval 10 \
  --count 3 \
  --ping-deadline 2 \
  --abs-th-ms 0.3 \
  --rel-th 0.15 \
  --hold-down 20 \
  --verify-routes \
  --log-level INFO
```

> **Dica de calibração:** antes de rodar de verdade, vale rodar uma vez com `--dry-run --log-level DEBUG` por 1–2 minutos, sem derrubar nenhum link, só para observar a faixa real de RTT/jitter do seu ambiente em `results/sttp/link_costs.csv`. Ajuste `--abs-th-ms` a partir desses números — usar um valor pequeno demais (ex.: 0.05) faz o algoritmo reagir a ruído de medição normal como se fosse uma mudança real de rota.
>
> **Dica sobre hold-down:** `--hold-down` deve ser maior que `--interval` (idealmente 2–3×) para ter efeito de anti-flap real. Um `--hold-down` igual ou menor que o `--interval` praticamente não bloqueia nada, porque o tempo entre ciclos já ultrapassa essa janela.

### Parâmetros da linha de comando

| Flag | Padrão | O que faz |
|---|---|---|
| `--lab` | `lab1-sttp` | Nome do lab do containerlab; usado para montar o nome dos containers (`clab-{lab}-{roteador}`). |
| `--interval` | `10` | Tempo-alvo, em segundos, entre o início de um ciclo completo (medir + calcular + aplicar) e o início do próximo. |
| `--count` | `3` | Quantos pacotes ICMP são enviados por rajada de `ping`, em cada sentido de cada enlace. |
| `--ping-deadline` | `2` | Timeout, em segundos, de cada rajada de ping. Se estourar, aquele lado do enlace é considerado inalcançável naquele ciclo. |
| `--abs-th-ms` | `1.0` | Limiar de melhoria absoluta (em ms) exigido, junto com `--rel-th`, para trocar de rota por `significant_improvement`. |
| `--rel-th` | `0.15` | Limiar de melhoria relativa (fração do custo antigo) exigido, junto com `--abs-th-ms`, para a mesma troca. |
| `--hold-down` | `30` | Tempo mínimo, em segundos, entre trocas sucessivas da mesma rota (anti-flap). Não se aplica ao caso `old_path_unreachable`. |
| `--max-cost` | `1e6` | Valor usado como "custo infinito" para um enlace cuja medição falhou nos dois sentidos. |
| `--dry-run` | desligado | Calcula e grava os CSVs normalmente, mas não instala nenhuma rota de verdade. |
| `--no-reconcile` | desligado | Pula a leitura do estado real de rotas no startup; parte sempre de estado vazio (comportamento antigo, não recomendado). |
| `--verify-routes` | desligado | Depois de instalar uma rota, confirma via `show ip route` que o next-hop esperado está presente antes de considerar a troca bem-sucedida. |
| `--log-level` | `INFO` | Nível de verbosidade do log (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |

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

# acompanhe o log do controller; espere ver "old_path_unreachable" e,
# se um loop chegar a se formar mesmo que por uma fração de ciclo,
# "[WARNING] Loop detectado" seguido de uma troca com motivo "loop_break"

sudo docker exec -it clab-lab1-sttp-h2 traceroute -n 192.168.5.10
sudo docker exec -it clab-lab1-sttp-r2 ip link set eth2 up
```

Para um teste mais rigoroso (mais chance de expor loops de 3+ nós), derrube dois links não-adjacentes ao mesmo tempo, por exemplo R2–R5 e R3–R4, e confira `show ip route` em todos os 5 roteadores para os prefixos afetados.

### Roteiro de validação sugerido

1. Ambiente limpo (sem links derrubados, sem rotas estáticas residuais de testes anteriores).
2. `--dry-run --log-level DEBUG` por 1–2 min, sem falhas — confirmar que só aparece `initial_install`/`Reconciliado`, sem nenhum `Loop detectado` falso-positivo.
3. Rodar de verdade (sem `--dry-run`) em topologia estável, e confirmar que `show ip route static` bate com o que o dry-run tinha proposto; testar ping entre todos os pares de hosts.
4. Derrubar um link único (R2–R5) e confirmar reação (`old_path_unreachable`), ausência de loop persistente e conectividade fim-a-fim.
5. Restaurar o link e confirmar reconvergência sem flapping (sem trocas repetidas em menos de `--hold-down` segundos).
6. Reiniciar o controller no meio de uma falha ativa (Ctrl+C, subir de novo) e confirmar que a reconciliação de estado encontra as rotas já instaladas, sem duplicá-las.
7. Derrubar dois links simultaneamente (estresse) e confirmar que o mecanismo de detecção de loop lida com ciclos maiores.
8. Auditar os CSVs ao final, conferindo se cada `reason` faz sentido no contexto em que apareceu.

### Ver os resultados (CSVs)

```bash
tail -n 10 results/sttp/link_costs.csv
tail -n 20 results/sttp/route_changes.csv
```

**Schema de `link_costs.csv`:** `ts, link, cost_ms` — uma linha por enlace por ciclo (6 linhas por iteração), independentemente de ter havido mudança de rota ou não.

**Schema de `route_changes.csv`:** `ts, src, dst_prefix, old_next_hop, new_next_hop, old_path_cost_now, best_path_cost_now, abs_improve_ms, rel_improve, reason` — uma linha só é gravada quando uma rota efetivamente muda. Os valores possíveis de `reason` são:

| `reason` | Significado |
|---|---|
| `initial_install` | Primeira rota instalada para aquele prefixo. |
| `old_path_unreachable` | O caminho antigo ficou com custo ≥ `--max-cost`; troca imediata, ignora limiares e hold-down. |
| `significant_improvement` | A melhoria passou nos dois limiares (`--abs-th-ms` e `--rel-th`) e não está em hold-down. |
| `not_significant` | Existe um caminho melhor, mas a melhoria não passou nos limiares — rota mantida (não gera linha no CSV, aparece só nos logs em `--log-level DEBUG`/`INFO`). |
| `hold_down_blocked` | A troca seria válida, mas foi bloqueada por ainda estar dentro da janela de `--hold-down`. |
| `loop_break` | O next-hop proposto formaria um loop de roteamento com outro roteador; o controller forçou a adoção do next-hop realmente ótimo do Dijkstra, ignorando limiares e hold-down. |

### Derrubar o STTP

1. Pare o controller (no terminal dele): `Ctrl+C` (o controller trata o sinal, termina o ciclo em andamento e encerra sem corromper os CSVs).
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

Os roteadores usam `privileged: true` para evitar problemas de permissões (comuns em WSL2/Docker Desktop).

### Sobre os arquivos de resultado

Os CSVs em `results/sttp/` são gerados pelo `sttp_controller.py` em runtime e crescem a cada execução (o script só cria o cabeçalho se o arquivo ainda não existir). Se quiser manter o histórico de um experimento específico, copie os CSVs para outro nome/pasta antes de rodar o próximo teste. Recomenda-se adicionar `results/` ao `.gitignore` caso ainda não esteja, para não versionar acidentalmente dados de execução local.