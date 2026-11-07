# fogsched-news2

**Escalonamento Preemptivo Baseado no NEWS2 para Redução da Latência de Alertas Críticos em Ambientes de Fog Computing**

Um nó fog recebe amostras de sinais vitais, calcula o NEWS2 de cada uma e
decide a ordem de processamento. O algoritmo proposto combina **preempção por
alertas críticos** com **envelhecimento limitado por classe**, e é comparado
com FIFO e prioridade não preemptiva em duas frentes:

- **Simulação** de eventos discretos (`fogsched/`), validada contra a teoria de filas;
- **Emulação** em Raspberry Pi 4 com MQTT (`emulation/`), com rede, relógio e preempção reais.

| Política | Preempção | Envelhecimento | Papel |
|---|---|---|---|
| FIFO | não | não | linha de base |
| Prioridade NP | não | não | linha de base |
| Prioridade P | só por críticas | não | ablação (efeito da preempção) |
| **Proposta** | só por críticas | teto por classe (1,9 / 2,5 / 2,9) | algoritmo proposto |
| Proposta (teto único) | só por críticas | teto 2,9 | ablação (efeito do teto) |

Prioridade efetiva da Proposta: `p(t) = min(c + α·(t − t_chegada), θ_c)`, com
`p = 3` para críticas e `θ_c < 3`, de modo que nenhuma amostra envelhecida
ultrapassa um alerta crítico.

## Requisitos

Python 3.10 a 3.13 (os resultados foram gerados com o 3.13).

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Simulação

```bash
pytest                                                    # 39 testes
python -m fogsched.validation                             # simulado × teoria -> results/validation.csv
python -m fogsched.experiments --config configs/experiments.yaml
python -m fogsched.analysis results/full                  # summary.csv, tests.csv, report.md, plots/
```

## Emulação (Raspberry Pi 4)

| Onde | Comando |
|---|---|
| notebook | `python pi/build_pi_package.py` (gera `pi_package.zip`, copiado para o Pi) |
| Pi, uma vez | `sudo bash pi/install_pi.sh` (Mosquitto, NumPy, paho-mqtt) |
| Pi | `taskset -c 2,3 python3 -m emulation.fog_node --broker localhost` |
| notebook | `python -m emulation.diagnostics --broker <IP do Pi>` |
| notebook | `python -m emulation.testbench --config emulation/configs/short.yaml --broker <IP do Pi>` |
| notebook | `python -m emulation.analyze results_emulation/short` |

`emulation/configs/short.yaml` (ρ = 0,95, 3 políticas, 8 replicações, ~40 min)
é a configuração usada no trabalho; `quick.yaml` é um teste de ~5 min para
conferir a bancada antes.

A análise compara três visões da latência: **simulada** (simulador sobre o
mesmo roteiro), **interna** (dentro do Pi) e **ponta a ponta** (do envio ao
alerta, medida por um único relógio no notebook). Na emulação, os testes
estatísticos usam a P95 dos alertas críticos (cerca de 150 por execução); a
P99 é apresentada de forma descritiva.

## Modelo

- Um servidor; preempção do tipo retomar, avaliada nas chegadas, apenas por
  alertas críticos.
- Cada preempção custa um tempo fixo de troca de contexto (δ), pago antes de
  a tarefa que preempta começar.
- Encerradas as chegadas, a fila é drenada até a última amostra; as pendências
  no fim das chegadas são registradas.
- No Pi, o nó fog roda em dois processos (rede e trabalho), um em cada núcleo,
  para que o GIL do Python não atrase o registro das chegadas.

## Estrutura

```
fogsched/            simulação
  news2.py             escore NEWS2 e classes de prioridade
  workload.py          gerador de carga (pacientes, estados clínicos, sinais vitais)
  model.py             estruturas da tarefa
  policies.py          políticas de escalonamento
  simulator.py         motor de eventos discretos com preempção
  metrics.py           métricas por replicação
  queueing_theory.py   fórmulas M/M/1, Cobham e prioridade preemptiva
  validation.py        simulado × teoria
  experiments.py       execução dos cenários
  analysis.py          estatística, gráficos e relatório
configs/             cenários da simulação (YAML)
emulation/           emulação em Raspberry Pi (MQTT)
  fog_node.py          nó fog (roda no Pi)
  stage2.py            estágio 2 em pedaços (preempção cooperativa)
  common.py            tópicos MQTT e transportes
  testbench.py         monitores e posto de enfermagem (roda no notebook)
  make_trace.py        roteiro de envios
  analyze.py           análise das três visões
  diagnostics.py       diagnóstico da bancada
  configs/             short (usada no trabalho) e quick (teste)
pi/                  instalação e pacote do Raspberry Pi
tests/               testes automatizados
```

Os resultados são gerados em `results/` (simulação) e `results_emulation/`
(emulação), fora do controle de versão.

## Reprodutibilidade

Todas as cargas derivam da semente-base 2026. Em cada replicação, todas as
políticas processam a mesma carga (números aleatórios comuns). Cada execução
grava a configuração usada e os metadados do ambiente.
