"""Gerador de carga: pacientes com estados clínicos markovianos, sinais vitais
sintéticos, classe pelo NEWS2 e tempo de serviço definido pela utilização
(E[S] = ρ / λ). O modo "poisson" serve à validação analítica."""

from dataclasses import dataclass, field

import numpy as np

from .model import TarefaSpec
from .news2 import SinaisVitais, calcular_news2, classificar

ESTADOS = ("estavel", "deteriorando", "critico")


@dataclass
class ConfigCarga:
    """Todos os parâmetros que definem a carga de um experimento.

    Os valores padrão são um ponto de partida razoável; os cenários dos
    experimentos (arquivo YAML) sobrescrevem o que for necessário.
    """

    # --- Enfermaria ---
    n_pacientes: int = 150               # quantos pacientes monitorados
    intervalo_amostragem: float = 60.0   # T: um envio a cada T segundos
    jitter: float = 0.10                 # variação de ±10% no intervalo

    # --- Carga do servidor ---
    utilizacao: float = 0.9              # ρ (ver explicação no topo)
    distribuicao_servico: str = "exponencial"  # exponencial | lognormal | deterministica
    cv_servico: float = 1.0              # coef. de variação (só na lognormal)

    # --- Tempo de simulação ---
    duracao: float = 3600.0              # chegadas acontecem de 0 até 'duracao' (s)
    aquecimento: float = 300.0           # descartado nas métricas (sistema "frio")

    # --- Estados clínicos (cadeia de Markov) ---
    proporcao_critica: float = 0.05      # fração do tempo em estado crítico
    proporcao_deteriorando: float = 0.15 # fração do tempo deteriorando
    persistencia: float = 0.90           # chance de manter o estado na próxima amostra

    # --- Rajadas (picos de chegada) ---
    # Lista de dicionários {inicio, duracao, fator}. Durante a rajada, cada
    # paciente envia amostras 'fator' vezes mais rápido.
    rajadas: list = field(default_factory=list)

    # --- Modo de chegada ---
    modo_chegada: str = "pacientes"      # pacientes | poisson
    # Só no modo poisson: probabilidade de cada classe (0, 1, 2, 3).
    probs_classe_poisson: tuple = (0.50, 0.30, 0.15, 0.05)

    @property
    def taxa_chegada(self) -> float:
        """λ: amostras por segundo, sem contar as rajadas."""
        return self.n_pacientes / self.intervalo_amostragem

    @property
    def servico_medio(self) -> float:
        """E[S] calculado a partir da utilização desejada (E[S] = ρ / λ)."""
        return self.utilizacao / self.taxa_chegada

    def distribuicao_estados(self) -> np.ndarray:
        """Vetor com a fração de tempo em cada estado (estavel, deteriorando, critico)."""
        estavel = 1.0 - self.proporcao_critica - self.proporcao_deteriorando
        if estavel < 0:
            raise ValueError("proporcao_critica + proporcao_deteriorando não pode passar de 1")
        return np.array([estavel, self.proporcao_deteriorando, self.proporcao_critica])


# ---------------------------------------------------------------------------
# Sinais vitais por estado clínico
# ---------------------------------------------------------------------------
# Para cada estado, cada sinal vital é sorteado de uma distribuição normal
# (média, desvio-padrão). As probabilidades indicam a chance de o paciente
# estar com oxigênio suplementar ou com a consciência alterada.
# São valores ILUSTRATIVOS, escolhidos para que o NEWS2 resultante seja
# coerente com o estado (estáveis ~0–2, deteriorando ~3–7, críticos ~7+).
PERFIS_VITAIS = {
    "estavel": dict(fr=(16, 2.0), spo2=(97.0, 1.2), pas=(125, 12), pulso=(76, 9),
                    temp=(36.8, 0.35), p_oxigenio=0.03, p_alterado=0.002),
    "deteriorando": dict(fr=(20, 2.5), spo2=(94.5, 1.5), pas=(108, 10), pulso=(98, 10),
                         temp=(37.9, 0.6), p_oxigenio=0.30, p_alterado=0.02),
    "critico": dict(fr=(26, 3.0), spo2=(89.5, 2.5), pas=(92, 12), pulso=(124, 12),
                    temp=(38.8, 0.9), p_oxigenio=0.85, p_alterado=0.30),
}


def sortear_sinais_vitais(estado: str, rng: np.random.Generator) -> SinaisVitais:
    """Sorteia uma amostra de sinais vitais coerente com o estado do paciente."""
    p = PERFIS_VITAIS[estado]
    fr = max(4, round(rng.normal(*p["fr"])))
    spo2 = min(100, max(70, round(rng.normal(*p["spo2"]))))
    pas = max(50, round(rng.normal(*p["pas"])))
    pulso = max(25, round(rng.normal(*p["pulso"])))
    temp = round(float(rng.normal(*p["temp"])), 1)
    oxigenio = bool(rng.random() < p["p_oxigenio"])
    alerta = bool(rng.random() >= p["p_alterado"])
    return SinaisVitais(freq_respiratoria=fr, spo2=spo2, oxigenio_suplementar=oxigenio,
                        pa_sistolica=pas, pulso=pulso, consciencia_alerta=alerta,
                        temperatura=temp)


def sortear_servico(cfg: ConfigCarga, rng: np.random.Generator, n: int) -> np.ndarray:
    """Sorteia n tempos de serviço com média E[S] e a distribuição escolhida."""
    media = cfg.servico_medio
    if cfg.distribuicao_servico == "exponencial":
        # Variabilidade alta (CV = 1); é a hipótese das fórmulas clássicas.
        return rng.exponential(media, size=n)
    if cfg.distribuicao_servico == "lognormal":
        # Permite escolher a variabilidade (cv_servico). Com CV alto há
        # tarefas muito longas ocasionais ("cauda pesada").
        sigma2 = np.log(1.0 + cfg.cv_servico ** 2)
        mu = np.log(media) - sigma2 / 2.0      # garante que a média seja 'media'
        return rng.lognormal(mu, np.sqrt(sigma2), size=n)
    if cfg.distribuicao_servico == "deterministica":
        return np.full(n, media)
    raise ValueError(f"distribuição de serviço desconhecida: {cfg.distribuicao_servico}")


def _fator_rajada(cfg: ConfigCarga, t: float) -> float:
    """Devolve o fator de aceleração das chegadas no instante t (1 = normal)."""
    fator = 1.0
    for r in cfg.rajadas:
        if r["inicio"] <= t < r["inicio"] + r["duracao"]:
            fator = max(fator, float(r["fator"]))
    return fator


def gerar_carga(cfg: ConfigCarga, semente) -> list:
    """Gera a lista de tarefas (TarefaSpec) de UMA replicação, ordenada por chegada.

    'semente' pode ser um inteiro ou um np.random.SeedSequence. A mesma
    semente sempre produz exatamente a mesma carga (reprodutibilidade).
    """
    seq = semente if isinstance(semente, np.random.SeedSequence) else np.random.SeedSequence(semente)
    # Fluxos aleatórios independentes para chegadas/estados, sinais vitais e
    # tempos de serviço. Assim, mudar um aspecto não "embaralha" os outros.
    rng_chegada, rng_vitais, rng_servico = (np.random.default_rng(s) for s in seq.spawn(3))

    if cfg.modo_chegada == "poisson":
        return _gerar_poisson(cfg, rng_chegada, rng_servico)
    if cfg.modo_chegada != "pacientes":
        raise ValueError(f"modo de chegada desconhecido: {cfg.modo_chegada}")

    pi = cfg.distribuicao_estados()
    eventos = []  # (chegada, paciente, estado)
    for paciente in range(cfg.n_pacientes):
        # Estado inicial sorteado segundo a proporção de longo prazo.
        estado = rng_chegada.choice(3, p=pi)
        # Primeiro envio em instante aleatório, para os pacientes não
        # enviarem todos ao mesmo tempo.
        t = rng_chegada.uniform(0, cfg.intervalo_amostragem)
        while t < cfg.duracao:
            eventos.append((t, paciente, ESTADOS[estado]))
            # Cadeia de Markov simples: com probabilidade 'persistencia' o
            # paciente mantém o estado; senão, sorteia um novo estado pela
            # proporção de longo prazo. Isso garante que, em média, a fração
            # de tempo em cada estado seja exatamente 'pi'.
            if rng_chegada.random() >= cfg.persistencia:
                estado = rng_chegada.choice(3, p=pi)
            # Próximo envio: intervalo com jitter, acelerado durante rajadas.
            intervalo = cfg.intervalo_amostragem / _fator_rajada(cfg, t)
            t += intervalo * (1.0 + cfg.jitter * rng_chegada.uniform(-1, 1))

    eventos.sort(key=lambda e: e[0])
    servicos = sortear_servico(cfg, rng_servico, len(eventos))

    tarefas = []
    for i, ((t, paciente, estado), s) in enumerate(zip(eventos, servicos)):
        sv = sortear_sinais_vitais(estado, rng_vitais)
        res = calcular_news2(sv)
        classe = int(classificar(res))
        tarefas.append(TarefaSpec(id=i, paciente=paciente, chegada=float(t),
                                  servico=float(s), classe=classe, news2=res.total,
                                  estado_paciente=estado))
    return tarefas


def _gerar_poisson(cfg: ConfigCarga, rng_chegada, rng_servico) -> list:
    """Modo de validação: chegadas de Poisson e classe sorteada diretamente."""
    lam = cfg.taxa_chegada
    n_esperado = int(lam * cfg.duracao * 1.2) + 100
    # Intervalos entre chegadas de um processo de Poisson são exponenciais.
    intervalos = rng_chegada.exponential(1.0 / lam, size=n_esperado)
    chegadas = np.cumsum(intervalos)
    chegadas = chegadas[chegadas < cfg.duracao]
    classes = rng_chegada.choice(4, p=np.array(cfg.probs_classe_poisson), size=len(chegadas))
    servicos = sortear_servico(cfg, rng_servico, len(chegadas))
    return [TarefaSpec(id=i, paciente=-1, chegada=float(t), servico=float(s), classe=int(c),
                       news2=None, estado_paciente="n/a")
            for i, (t, c, s) in enumerate(zip(chegadas, classes, servicos))]


def resumo_carga(tarefas: list) -> dict:
    """Estatísticas rápidas da carga gerada (útil para conferir os cenários)."""
    n = len(tarefas)
    contagem = np.bincount([t.classe for t in tarefas], minlength=4)
    return {
        "n_tarefas": n,
        "fracao_por_classe": (contagem / max(n, 1)).round(4).tolist(),
        "servico_medio_observado": float(np.mean([t.servico for t in tarefas])) if n else 0.0,
    }
