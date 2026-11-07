"""Escore NEWS2 (RCP, 2017; escala 1 de SpO2) e conversão em classe de prioridade."""

from dataclasses import dataclass
from enum import IntEnum


class Classe(IntEnum):
    """Classes de prioridade usadas pelo escalonador.

    IntEnum permite comparar classes como números (CRITICA > MEDIA, etc.),
    o que simplifica a lógica das políticas de escalonamento.
    """

    ESTAVEL = 0   # NEWS2 = 0
    BAIXA = 1     # NEWS2 de 1 a 4, sem nenhum parâmetro com 3 pontos
    MEDIA = 2     # NEWS2 de 5 a 6, OU algum parâmetro isolado com 3 pontos
    CRITICA = 3   # NEWS2 >= 7


# Nomes legíveis, usados em tabelas e gráficos.
NOMES_CLASSES = {
    Classe.ESTAVEL: "estavel",
    Classe.BAIXA: "baixa",
    Classe.MEDIA: "media",
    Classe.CRITICA: "critica",
}


@dataclass(frozen=True)
class SinaisVitais:
    """Uma amostra de sinais vitais de um paciente.

    'frozen=True' torna o objeto imutável: depois de criado, ninguém altera
    os valores sem querer em outra parte do código.
    """

    freq_respiratoria: float      # incursões respiratórias por minuto
    spo2: float                   # saturação de oxigênio (%)
    oxigenio_suplementar: bool    # True = paciente recebe oxigênio
    pa_sistolica: float           # pressão arterial sistólica (mmHg)
    pulso: float                  # batimentos por minuto
    consciencia_alerta: bool      # True = "Alerta"; False = CVPU (confuso,
                                  # responde à voz, à dor ou não responde)
    temperatura: float            # graus Celsius


# ---------------------------------------------------------------------------
# Pontuação de cada parâmetro (tabela NEWS2, escala 1 de SpO2)
# Cada função recebe um valor e devolve 0, 1, 2 ou 3 pontos.
# Os limites usam "<=" para funcionar também com valores decimais.
# ---------------------------------------------------------------------------

def pontos_freq_respiratoria(fr: float) -> int:
    # <=8: 3 | 9–11: 1 | 12–20: 0 | 21–24: 2 | >=25: 3
    if fr <= 8:
        return 3
    if fr <= 11:
        return 1
    if fr <= 20:
        return 0
    if fr <= 24:
        return 2
    return 3


def pontos_spo2_escala1(spo2: float) -> int:
    # <=91: 3 | 92–93: 2 | 94–95: 1 | >=96: 0
    if spo2 <= 91:
        return 3
    if spo2 <= 93:
        return 2
    if spo2 <= 95:
        return 1
    return 0


def pontos_oxigenio(oxigenio_suplementar: bool) -> int:
    # Ar ambiente: 0 | Oxigênio suplementar: 2
    return 2 if oxigenio_suplementar else 0


def pontos_pa_sistolica(pas: float) -> int:
    # <=90: 3 | 91–100: 2 | 101–110: 1 | 111–219: 0 | >=220: 3
    if pas <= 90:
        return 3
    if pas <= 100:
        return 2
    if pas <= 110:
        return 1
    if pas <= 219:
        return 0
    return 3


def pontos_pulso(pulso: float) -> int:
    # <=40: 3 | 41–50: 1 | 51–90: 0 | 91–110: 1 | 111–130: 2 | >=131: 3
    if pulso <= 40:
        return 3
    if pulso <= 50:
        return 1
    if pulso <= 90:
        return 0
    if pulso <= 110:
        return 1
    if pulso <= 130:
        return 2
    return 3


def pontos_consciencia(alerta: bool) -> int:
    # Alerta: 0 | CVPU (qualquer alteração nova de consciência): 3
    return 0 if alerta else 3


def pontos_temperatura(temp: float) -> int:
    # <=35,0: 3 | 35,1–36,0: 1 | 36,1–38,0: 0 | 38,1–39,0: 1 | >=39,1: 2
    if temp <= 35.0:
        return 3
    if temp <= 36.0:
        return 1
    if temp <= 38.0:
        return 0
    if temp <= 39.0:
        return 1
    return 2


@dataclass(frozen=True)
class ResultadoNEWS2:
    """Resultado do cálculo: escore total e detalhamento por parâmetro."""

    total: int
    pontos: dict                 # ex.: {"freq_respiratoria": 2, "spo2": 0, ...}
    tem_parametro_vermelho: bool # True se algum parâmetro isolado tem 3 pontos


def calcular_news2(sv: SinaisVitais) -> ResultadoNEWS2:
    """Calcula o NEWS2 de uma amostra de sinais vitais."""
    pontos = {
        "freq_respiratoria": pontos_freq_respiratoria(sv.freq_respiratoria),
        "spo2": pontos_spo2_escala1(sv.spo2),
        "oxigenio": pontos_oxigenio(sv.oxigenio_suplementar),
        "pa_sistolica": pontos_pa_sistolica(sv.pa_sistolica),
        "pulso": pontos_pulso(sv.pulso),
        "consciencia": pontos_consciencia(sv.consciencia_alerta),
        "temperatura": pontos_temperatura(sv.temperatura),
    }
    total = sum(pontos.values())
    # No NEWS2, um único parâmetro com 3 pontos ("red score") já pede resposta
    # clínica urgente, mesmo que o total seja baixo.
    vermelho = any(p == 3 for p in pontos.values())
    return ResultadoNEWS2(total=total, pontos=pontos, tem_parametro_vermelho=vermelho)


def classificar(resultado: ResultadoNEWS2,
                limiar_critico: int = 7,
                limiar_medio: int = 5,
                vermelho_eleva_para_media: bool = True) -> Classe:
    """Converte o resultado do NEWS2 em uma classe de prioridade.

    Mapeamento padrão (baseado nas faixas de risco do RCP, 2017):
      - total >= 7                         -> CRITICA (risco alto)
      - total 5–6                          -> MEDIA   (risco médio)
      - algum parâmetro isolado com 3      -> MEDIA   (RCP: risco "baixo-médio")
      - total 1–4                          -> BAIXA
      - total 0                            -> ESTAVEL
    """
    if resultado.total >= limiar_critico:
        return Classe.CRITICA
    if resultado.total >= limiar_medio:
        return Classe.MEDIA
    if vermelho_eleva_para_media and resultado.tem_parametro_vermelho:
        return Classe.MEDIA
    if resultado.total >= 1:
        return Classe.BAIXA
    return Classe.ESTAVEL
