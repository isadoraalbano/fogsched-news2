"""Executa cenários × políticas × replicações descritos em um YAML.

    python -m fogsched.experiments --config configs/experiments.yaml [--processos N]

Gera raw.csv, config_used.yaml e metadata.json. Em cada replicação, todas as
políticas processam a mesma carga (números aleatórios comuns)."""

import argparse
import copy
import json
import os
import platform
import sys
import time
from dataclasses import fields
from datetime import datetime
from multiprocessing import Pool

import numpy as np
import pandas as pd
import yaml

from .workload import ConfigCarga, gerar_carga
from .metrics import calcular_metricas
from .policies import criar_politica
from .simulator import simular

CAMPOS_CARGA = {f.name for f in fields(ConfigCarga)}
GRUPOS_POLITICA = {"fifo", "prioridade_np", "prioridade_p", "proposta", "preemptivas"}


def carregar_config(caminho: str) -> dict:
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _aplicar_parametro(nome: str, valor, carga: dict, sim: dict, politicas: list) -> None:
    """Coloca 'valor' no lugar certo (carga, simulação ou políticas)."""
    if nome in CAMPOS_CARGA:
        carga[nome] = valor
    elif nome == "custo_preempcao_rel":
        sim[nome] = valor
    elif "." in nome and nome.split(".", 1)[0] in GRUPOS_POLITICA:
        grupo, param = nome.split(".", 1)
        tipos = {"prioridade_p", "proposta"} if grupo == "preemptivas" else {grupo}
        alterou = False
        for p in politicas:
            if p["tipo"] in tipos:
                p[param] = valor
                alterou = True
        if not alterou:
            raise ValueError(f"nenhuma política do tipo {tipos} para receber '{nome}'")
    else:
        raise ValueError(f"parâmetro desconhecido em 'variar': {nome}")


def montar_unidades(cfg: dict, replicacoes=None, filtro_cenarios=None) -> list:
    """Cria a lista de "unidades de trabalho".

    Uma unidade = (cenário, valor, replicação). Ela gera a carga uma vez e
    roda todas as políticas sobre essa carga.
    """
    reps = replicacoes or cfg.get("replicacoes", 30)
    semente_base = cfg.get("semente_base", 2026)
    unidades = []
    for i_cen, cen in enumerate(cfg["cenarios"]):
        if filtro_cenarios and cen["id"] not in filtro_cenarios:
            continue
        nomes_pol = cen.get("politicas")
        if nomes_pol:
            pols_base = [p for p in cfg["politicas"] if p["nome"] in nomes_pol]
        else:
            pols_base = [p for p in cfg["politicas"] if p.get("padrao", True)]
        valores = cen.get("valores", [None])
        for valor in valores:
            carga = copy.deepcopy(cfg.get("carga", {}))
            sim = copy.deepcopy(cfg.get("simulacao", {}))
            pols = copy.deepcopy(pols_base)
            for chave, v in (cen.get("sobrescrever") or {}).items():
                _aplicar_parametro(chave, v, carga, sim, pols)
            if cen.get("variar") is not None:
                _aplicar_parametro(cen["variar"], valor, carga, sim, pols)
            for rep in range(reps):
                unidades.append(dict(
                    cenario=cen["id"], parametro=cen.get("variar") or "-",
                    valor=valor, replicacao=rep, carga=carga, sim=sim, politicas=pols,
                    # Mesma semente para todos os valores do cenário (ver regra 2).
                    semente=[semente_base, i_cen, rep]))
    return unidades


def executar_unidade(u: dict) -> list:
    """Gera a carga de uma replicação e roda todas as políticas sobre ela."""
    carga_dict = dict(u["carga"])
    cfg = ConfigCarga(**carga_dict)
    trace = gerar_carga(cfg, np.random.SeedSequence(u["semente"]))
    es = cfg.servico_medio
    custo = float(u["sim"].get("custo_preempcao_rel", 0.0)) * es

    linhas = []
    for pcfg in u["politicas"]:
        politica = criar_politica(pcfg, es)        # objeto novo e vazio
        resultado = simular(trace, politica, custo_preempcao=custo)
        m = calcular_metricas(resultado, cfg.aquecimento, cfg.duracao)
        linhas.append({
            "cenario": u["cenario"], "parametro": u["parametro"],
            "valor": u["valor"] if not isinstance(u["valor"], (list, dict)) else str(u["valor"]),
            "replicacao": u["replicacao"], "politica": pcfg["nome"],
            "politica_tipo": pcfg["tipo"], "servico_medio": es,
            "utilizacao_alvo": cfg.utilizacao, **m,
        })
    return linhas


def main(argv=None):
    ap = argparse.ArgumentParser(description="Executa os experimentos do TCC.")
    ap.add_argument("--config", required=True, help="arquivo YAML de experimentos")
    ap.add_argument("--saida", help="pasta de saída (sobrescreve a do YAML)")
    ap.add_argument("--processos", type=int, default=os.cpu_count() or 1,
                    help="quantos processos em paralelo (padrão: todos os núcleos)")
    ap.add_argument("--replicacoes", type=int, help="sobrescreve o número de replicações")
    ap.add_argument("--cenarios", nargs="*", help="roda só estes cenários (ids)")
    args = ap.parse_args(argv)

    cfg = carregar_config(args.config)
    saida = args.saida or cfg.get("saida", "results")
    os.makedirs(saida, exist_ok=True)
    unidades = montar_unidades(cfg, args.replicacoes, args.cenarios)
    print(f"{len(unidades)} unidades (cenário × valor × replicação) em {args.processos} processo(s)")

    inicio = time.time()
    linhas = []
    total = len(unidades)
    with Pool(processes=max(1, args.processos)) as pool:
        for k, resultado in enumerate(pool.imap_unordered(executar_unidade, unidades, chunksize=4), 1):
            linhas.extend(resultado)
            if k % max(1, total // 20) == 0 or k == total:
                print(f"  {k}/{total} ({100 * k / total:.0f}%) — {time.time() - inicio:.0f}s", flush=True)

    df = pd.DataFrame(linhas).sort_values(["cenario", "valor", "replicacao", "politica"],
                                          key=lambda s: s.astype(str))
    df.to_csv(os.path.join(saida, "raw.csv"), index=False)
    with open(os.path.join(saida, "config_used.yaml"), "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)
    meta = {"data": datetime.now().isoformat(timespec="seconds"),
            "duracao_s": round(time.time() - inicio, 1),
            "python": sys.version.split()[0], "numpy": np.__version__, "pandas": pd.__version__,
            "plataforma": platform.platform(), "n_linhas": len(df)}
    with open(os.path.join(saida, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"Pronto: {len(df)} linhas em {os.path.join(saida, 'raw.csv')}")


if __name__ == "__main__":
    main()
