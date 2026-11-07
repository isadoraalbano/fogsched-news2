"""Gera o roteiro de envios (trace) da emulação com o mesmo gerador e semente da
simulação, incluindo os sinais vitais de cada amostra.

    python -m emulation.make_trace --pacientes 150 --intervalo 6 --duracao 90 --utilizacao 0.95 --saida trace.json"""

import argparse
import json
from dataclasses import asdict, fields

import numpy as np

from fogsched.workload import ConfigCarga, gerar_carga, sortear_sinais_vitais
from fogsched.model import TarefaSpec


def config_carga(dados: dict) -> ConfigCarga:
    """dict (vindo do YAML) -> ConfigCarga, ignorando chaves desconhecidas."""
    validos = {f.name for f in fields(ConfigCarga)}
    dados = dict(dados)
    return ConfigCarga(**{k: v for k, v in dados.items() if k in validos})


def _sinais_para_dict(sv) -> dict:
    """SinaisVitais -> dict com tipos simples (JSON não aceita tipos do NumPy)."""
    d = asdict(sv)
    return {k: (bool(v) if isinstance(v, (bool, np.bool_)) else float(v)) for k, v in d.items()}


def gerar_trace(cfg: ConfigCarga, semente: int) -> dict:
    """Gera um trace completo (dicionário pronto para salvar em JSON)."""
    if cfg.modo_chegada != "pacientes":
        raise ValueError("a emulação usa o modo de chegada 'pacientes'")
    specs = gerar_carga(cfg, semente)
    # Recria o fluxo de números aleatórios dos sinais vitais exatamente como
    # carga.gerar_carga faz: SeedSequence(semente).spawn(3) e o 2º fluxo é o
    # dos sinais. Os sinais são sorteados na ordem de chegada das tarefas.
    _, seq_vitais, _ = np.random.SeedSequence(semente).spawn(3)
    rng_vitais = np.random.default_rng(seq_vitais)
    amostras = []
    for s in specs:
        sv = sortear_sinais_vitais(s.estado_paciente, rng_vitais)
        amostras.append({
            "id": s.id, "paciente": s.paciente, "t_plan": round(s.chegada, 6),
            "servico": s.servico,
            "classe_esperada": s.classe, "news2_esperado": s.news2,
            "estado": s.estado_paciente, "sinais": _sinais_para_dict(sv),
        })
    return {"semente": int(semente), "config": asdict(cfg),
            "servico_medio": cfg.servico_medio, "taxa_chegada": cfg.taxa_chegada,
            "amostras": amostras}


def trace_para_specs(trace: dict) -> list:
    """Trace (dict) -> lista de TarefaSpec, para rodar a simulação no mesmo trace."""
    return [TarefaSpec(id=a["id"], paciente=a["paciente"], chegada=a["t_plan"],
                       servico=a["servico"], classe=a["classe_esperada"],
                       news2=a["news2_esperado"], estado_paciente=a["estado"])
            for a in trace["amostras"]]


def main():
    ap = argparse.ArgumentParser(description="Gera um trace para a emulação.")
    ap.add_argument("--pacientes", type=int, default=150)
    ap.add_argument("--intervalo", type=float, default=6.0)
    ap.add_argument("--duracao", type=float, default=120.0)
    ap.add_argument("--aquecimento", type=float, default=20.0)
    ap.add_argument("--utilizacao", type=float, default=0.95)
    ap.add_argument("--semente", type=int, default=2026)
    ap.add_argument("--saida", default="trace.json")
    a = ap.parse_args()
    cfg = ConfigCarga(n_pacientes=a.pacientes, intervalo_amostragem=a.intervalo,
                      duracao=a.duracao, aquecimento=a.aquecimento, utilizacao=a.utilizacao)
    trace = gerar_trace(cfg, a.semente)
    with open(a.saida, "w", encoding="utf-8") as f:
        json.dump(trace, f)
    n = len(trace["amostras"])
    print(f"{n} amostras em {a.duracao:.0f} s ({trace['taxa_chegada']:.1f} amostras/s); "
          f"E[S] = {1e3 * trace['servico_medio']:.1f} ms; salvo em {a.saida}")


if __name__ == "__main__":
    main()
