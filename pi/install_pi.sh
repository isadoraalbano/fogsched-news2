#!/usr/bin/env bash
# =============================================================================
# install_pi.sh — Prepara o Raspberry Pi 4 para a emulação do TCC.
#
# Rodar UMA vez, no Pi, dentro da pasta do projeto:
#     sudo bash pi/install_pi.sh
#
# O que este script faz:
#   1. instala o broker MQTT (Mosquitto) e as bibliotecas Python pelo apt;
#   2. libera o Mosquitto para aceitar conexões vindas do notebook;
#   3. fixa a CPU na frequência máxima (evita medições que variam porque o
#      processador mudou de velocidade no meio do teste);
#   4. desliga a economia de energia do Wi-Fi (ela atrasa e agrupa mensagens);
#   5. mostra o IP do Pi e confere se está tudo funcionando.
# =============================================================================
set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "Rode com sudo:  sudo bash pi/install_pi.sh"
  exit 1
fi

echo "==> 1/5 Instalando pacotes (pode levar alguns minutos)"
apt-get update
apt-get install -y mosquitto mosquitto-clients \
                   python3-numpy python3-paho-mqtt

echo "==> 2/5 Configurando o Mosquitto para aceitar o notebook"
# A partir da versão 2, o Mosquitto só aceita conexões do próprio Pi, a não
# ser que um 'listener' seja declarado. Numa rede de laboratório isolada,
# aceitar conexões sem senha é aceitável; NÃO faça isso numa rede pública.
cat > /etc/mosquitto/conf.d/bancada_tcc.conf <<'EOF'
listener 1883
allow_anonymous true
# Fila maior por cliente (padrão 1000), para não descartar mensagens em rajadas.
max_queued_messages 100000
EOF
systemctl enable mosquitto
systemctl restart mosquitto

echo "==> 3/5 Fixando a CPU na frequência máxima (governor 'performance')"
# Vale até o Pi ser desligado: depois de reiniciar, rode este script de novo
# (ele pode ser repetido sem problema) ou só este laço.
for c in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_governor; do
  echo performance > "$c"
done
cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor

echo "==> 4/5 Desligando a economia de energia do Wi-Fi"
# Com a economia ligada (padrão do Raspberry Pi OS), o Wi-Fi "cochila" e
# segura as mensagens por dezenas a centenas de ms, de forma intermitente.
# A configuração abaixo é permanente (vale também depois de reiniciar).
if [ -d /etc/NetworkManager/conf.d ]; then
  cat > /etc/NetworkManager/conf.d/bancada_tcc_wifi.conf <<'EOF'
[connection]
wifi.powersave = 2
EOF
  systemctl reload NetworkManager 2>/dev/null || true
fi
if command -v iw >/dev/null && iw dev wlan0 info >/dev/null 2>&1; then
  iw dev wlan0 set power_save off || true
  iw dev wlan0 get power_save || true
else
  echo "sem Wi-Fi (wlan0) ativo: nada a fazer"
fi

echo "==> 5/5 Conferindo"
python3 -c "import numpy, paho.mqtt; print('Python OK | NumPy', numpy.__version__)"
systemctl is-active mosquitto && echo "Mosquitto rodando"
if command -v vcgencmd >/dev/null; then
  echo "Temperatura: $(vcgencmd measure_temp)  |  Throttling: $(vcgencmd get_throttled) (0x0 = nunca limitou)"
fi
echo
echo "IP deste Raspberry Pi (use na bancada com --broker):"
hostname -I
echo
echo "Pronto. Próximo passo, no Pi:"
echo "    taskset -c 2,3 python3 -m emulation.fog_node --broker localhost"
