"""Gera pi_package.zip com os arquivos necessários no Raspberry Pi.

    python pi/build_pi_package.py"""

import pathlib
import zipfile

RAIZ = pathlib.Path(__file__).resolve().parent.parent

ARQUIVOS_DO_PI = [
    # Núcleo compartilhado com a simulação: NEWS2, tarefa e políticas.
    "fogsched/__init__.py",
    "fogsched/news2.py",        # estágio 1: cálculo do NEWS2 e classe de prioridade
    "fogsched/model.py",        # estrutura da tarefa (TarefaSpec / Tarefa)
    "fogsched/policies.py",     # FIFO, Prioridade NP, Prioridade P, Proposta
    # O nó fog propriamente dito.
    "emulation/__init__.py",
    "emulation/common.py",      # tópicos MQTT e transporte
    "emulation/stage2.py",      # estágio 2 em pedaços (preempção cooperativa)
    "emulation/fog_node.py",    # o escalonador (programa principal do Pi)
    # Preparação do Pi.
    "pi/install_pi.sh",
]


def main():
    destino = RAIZ / "pi_package.zip"
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in ARQUIVOS_DO_PI:
            z.write(RAIZ / rel, rel)
    print(f"{len(ARQUIVOS_DO_PI)} arquivos em {destino.name}:")
    for rel in ARQUIVOS_DO_PI:
        print("  ", rel)


if __name__ == "__main__":
    main()
