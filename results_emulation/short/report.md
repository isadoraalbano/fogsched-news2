# Relatório da emulação (Raspberry Pi)

Valores: média entre replicações ± meia-largura do IC95. Latências em **ms**.

## rho_0.95

### Latência P95 dos alertas críticos (ms) — métrica dos testes

| Política | Simulado | Pi (interno) | Ponta a ponta |
|---|---|---|---|
| Proposta | 120.0 ± 11.9 | 122.6 ± 12.4 | 207.5 ± 26.8 |
| Prioridade NP | 168.3 ± 9.2 | 174.9 ± 17.0 | 294.8 ± 35.1 |
| FIFO | 1022.6 ± 333.6 | 1129.3 ± 369.5 | 1175.8 ± 371.1 |


### Latência P99 dos alertas críticos (ms) — descritiva

| Política | Simulado | Pi (interno) | Ponta a ponta |
|---|---|---|---|
| Proposta | 173.7 ± 25.1 | 177.3 ± 24.5 | 309.9 ± 72.2 |
| Prioridade NP | 228.2 ± 22.2 | 259.9 ± 40.4 | 468.5 ± 102.5 |
| FIFO | 1222.3 ± 330.8 | 1368.9 ± 357.7 | 1416.7 ± 354.2 |

### Outras métricas (ponta a ponta)

| Política | p99 média (ms) | espera máx. estáveis (ms) | preempções/tarefa | rede média (ms) | P95 da rede, críticas (ms) | registro no worker, média (ms) | início dos críticos que preemptam, média / P99 (ms) | alertas perdidos | entrega (%) |
|---|---|---|---|---|---|---|---|---|---|
| Proposta | 331.4 ± 50.4 | 2309.7 ± 440.6 | 0.065 | 52.81 | 132.63 | 0.728 | 0.767 / 1.176 | 0 | 100.00 |
| Prioridade NP | 396.4 ± 106.4 | 2603.6 ± 553.9 | 0.000 | 68.23 | 172.66 | 0.726 | 37.497 / 156.981 | 0 | 100.00 |
| FIFO | 1307.0 ± 438.3 | 1507.6 ± 347.7 | 0.000 | 55.44 | 128.81 | 0.722 | 296.319 / 974.789 | 0 | 100.00 |

### Wilcoxon pareado (Proposta × outras), P95 das críticas, ponta a ponta

| Comparação | p (Holm) | efeito r |
|---|---|---|
| Proposta × Prioridade NP | 0.0156 | -1.00 |
| Proposta × FIFO | 0.0156 | -1.00 |

## Verificações

- Amostras em que o escore NEWS2 calculado no Pi diferiu do esperado: **0** (deve ser 0).
- Amostras em que a classe de prioridade calculada no Pi diferiu da esperada: **0** (deve ser 0).
- Alertas que não chegaram ao posto (falhas de entrega): **0**.
- Amostras recebidas em duplicidade pelo nó fog (QoS 1) e descartadas: 0.
- RTT mediano da rede sem carga: 90.14 ms.
- Duração média de um pedaço do estágio 2 no Pi: 0.468 ms.
- Amostras entregues ao Pi logo após o término de outra tarefa (até 1 ms): 11%.
