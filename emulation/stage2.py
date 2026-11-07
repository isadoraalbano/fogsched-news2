"""Estágio 2 (média móvel, artefatos, tendência, ΔNEWS2 e montagem do alerta)
executado em pedaços por um gerador, até consumir o tempo de serviço da amostra."""

import time

import numpy as np

NUCLEO_MEDIA_MOVEL = np.ones(5) / 5.0
COLUNAS = ("freq_respiratoria", "spo2", "pa_sistolica", "pulso", "temperatura")


def janela_para_matriz(historico) -> np.ndarray:
    """Lista de amostras do paciente -> matriz W linhas × 5 sinais.

    Se o paciente ainda tem poucas amostras (início da execução), repete as
    existentes até ter 6 linhas: a média móvel de 5 pontos precisa de 5 e a
    reta da tendência precisa de pelo menos 2 pontos suavizados (5 + 1).
    """
    m = np.array([[a[c] for c in COLUNAS] for a in historico], dtype=float)
    while m.shape[0] < 6:
        m = np.vstack([m, m])
    return m


def nucleo_estagio2(janela: np.ndarray, news2_anterior: int, news2_atual: int,
                    paciente: int) -> dict:
    """Uma execução completa do processamento de uma amostra."""
    suavizado = np.vstack([np.convolve(janela[:, j], NUCLEO_MEDIA_MOVEL, mode="valid")
                           for j in range(janela.shape[1])]).T
    media = janela.mean(axis=0)
    desvio = janela.std(axis=0) + 1e-9
    artefatos = int((np.abs((janela - media) / desvio) > 3.0).sum())
    x = np.arange(len(suavizado))
    inclinacoes = np.polyfit(x, suavizado, deg=1)[0]
    return {
        "paciente": paciente,
        "news2": news2_atual,
        "delta_news2": news2_atual - news2_anterior,
        "tendencias": [round(float(v), 4) for v in inclinacoes],
        "artefatos": artefatos,
    }


def executar_em_pedacos(tarefa):
    """Gerador do estágio 2 de UMA tarefa.

    Usa (e atualiza) os atributos da tarefa:
      tarefa.janela, tarefa.news2_anterior, tarefa.spec.news2, tarefa.spec.paciente
      tarefa.orcamento  -> tempo de processador a consumir (s)
      tarefa.base       -> tempo executado até a última interrupção (s)
      tarefa.retomada   -> instante (perf_counter) em que ela voltou ao processador
      tarefa.executado  -> tempo já executado (s)
      tarefa.resultado  -> o alerta, preenchido no último pedaço
    O tempo executado conta todo o período em que a tarefa ocupa o processador,
    inclusive as consultas à caixa entre os pedaços, como na simulação.
    Cada next() executa um pedaço; StopIteration significa "terminou".
    """
    while True:
        resultado = nucleo_estagio2(tarefa.janela, tarefa.news2_anterior,
                                    tarefa.spec.news2, tarefa.spec.paciente)
        tarefa.executado = tarefa.base + (time.perf_counter() - tarefa.retomada)
        if tarefa.executado >= tarefa.orcamento:
            tarefa.resultado = resultado
            return                  # fim: o próximo next() gera StopIteration
        yield                       # PONTO DE PAUSA: o escalonador decide o que vem agora


def calibrar(repeticoes: int = 300) -> dict:
    """Mede quanto dura UM pedaço (uma execução do núcleo) nesta máquina.

    O tempo do pedaço é a "granularidade" da preempção: no pior caso, um
    alerta crítico espera um pedaço terminar antes de ser atendido.
    """
    rng = np.random.default_rng(0)
    janela = rng.normal([16, 97, 120, 80, 36.8], [2, 1.2, 10, 8, 0.3], size=(60, 5))
    for _ in range(50):                               # aquecimento
        nucleo_estagio2(janela, 1, 2, 0)
    tempos = []
    for _ in range(repeticoes):
        t0 = time.perf_counter()
        nucleo_estagio2(janela, 1, 2, 0)
        tempos.append(time.perf_counter() - t0)
    t = np.array(tempos)
    return {"pedaco_media_ms": float(t.mean() * 1e3),
            "pedaco_p99_ms": float(np.percentile(t, 99) * 1e3),
            "pedaco_max_ms": float(t.max() * 1e3)}
