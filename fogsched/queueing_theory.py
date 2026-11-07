"""Fórmulas analíticas usadas na validação do simulador: M/M/1, prioridade não
preemptiva (Cobham, 1954) e prioridade preemptiva com retomada."""


def mm1_tempo_resposta(lam: float, mu: float) -> float:
    """M/M/1 com FIFO: tempo médio no sistema W = 1 / (μ − λ)."""
    if lam >= mu:
        raise ValueError("sistema instável (ρ >= 1)")
    return 1.0 / (mu - lam)


def prioridade_nao_preemptiva(lams: list, mu: float) -> list:
    """Fórmula de Cobham (M/G/1 com prioridade não preemptiva), serviço exponencial.

    Espera média da classe k:
        W_k = W0 / ((1 − σ_{k−1}) (1 − σ_k))
    onde
        W0  = Σ_i λ_i E[S²] / 2      (trabalho residual médio no servidor)
        σ_k = Σ_{i<=k} ρ_i           (utilização acumulada até a classe k)
    Tempo de resposta = W_k + E[S]. Para serviço exponencial, E[S²] = 2/μ².
    """
    es = 1.0 / mu
    es2 = 2.0 / mu ** 2
    w0 = sum(lams) * es2 / 2.0
    respostas = []
    sigma_ant = 0.0
    for lam_k in lams:
        sigma_k = sigma_ant + lam_k * es
        wk = w0 / ((1.0 - sigma_ant) * (1.0 - sigma_k))
        respostas.append(wk + es)
        sigma_ant = sigma_k
    return respostas


def prioridade_preemptiva_resume(lams: list, mu: float) -> list:
    """M/G/1 com prioridade preemptiva do tipo "retomar", serviço exponencial.

    Tempo de resposta médio da classe k:
        T_k = E[S] / (1 − σ_{k−1})  +  R_k / ((1 − σ_{k−1}) (1 − σ_k))
    onde R_k = Σ_{i<=k} λ_i E[S²] / 2 considera só as classes de prioridade
    igual ou maior (as menores são "invisíveis" para a classe k).
    """
    es = 1.0 / mu
    es2 = 2.0 / mu ** 2
    respostas = []
    sigma_ant = 0.0
    rk = 0.0
    for lam_k in lams:
        sigma_k = sigma_ant + lam_k * es
        rk += lam_k * es2 / 2.0
        tk = es / (1.0 - sigma_ant) + rk / ((1.0 - sigma_ant) * (1.0 - sigma_k))
        respostas.append(tk)
        sigma_ant = sigma_k
    return respostas
