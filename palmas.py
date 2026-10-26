"""Ativação do Jarvis por palmas.

Fica ouvindo o microfone; quando ouve duas palmas seguidas, abre uma janela
do Terminal com `jarvis chat --voice`.

Uso (de dentro de ~/jarvis):
    uv run python palmas.py              # escuta e abre o Jarvis
    uv run python palmas.py --calibrar   # mostra o volume ao vivo, pra ajustar --limiar
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import numpy as np

TAXA = 16000
BLOCO = 320  # 20 ms
JARVIS_DIR = Path(__file__).resolve().parent
SOM_OK = "/System/Library/Sounds/Glass.aiff"
URL_INTERFACE = "http://127.0.0.1:8000/"


class DetectorPalmas:
    """Detecta palmas: pico alto e súbito que some rápido (não é fala/música contínua)."""

    def __init__(
        self,
        limiar: float = 0.25,
        contraste: float = 8.0,
        decaimento: float = 0.2,
        intervalo_min: float = 0.15,
        intervalo_max: float = 0.8,
        palmas: int = 2,
    ) -> None:
        self.limiar = limiar
        self.contraste = contraste
        self.decaimento = decaimento
        self.intervalo_min = intervalo_min
        self.intervalo_max = intervalo_max
        self.palmas = palmas
        self.fundo = 0.01  # RMS do ruído ambiente (média móvel)
        self._pendente: tuple[float, float] | None = None  # (instante, pico)
        self._ocupado = False  # som longo em andamento: espera silêncio
        self._confirmadas: list[float] = []

    def alimentar(self, bloco: np.ndarray, agora: float) -> bool:
        """Processa um bloco de áudio; retorna True quando a sequência de palmas fecha."""
        pico = float(np.max(np.abs(bloco)))
        rms = float(np.sqrt(np.mean(bloco.astype(np.float64) ** 2)))

        if self._ocupado:
            if pico < self.limiar * 0.3:
                self._ocupado = False
        elif self._pendente is not None:
            inicio, pico_inicial = self._pendente
            if pico < pico_inicial * 0.3:
                self._pendente = None
                self._confirmadas.append(inicio)
            elif agora - inicio > self.decaimento:
                self._pendente = None  # som longo demais: não é palma
                self._ocupado = True
        elif pico >= self.limiar and pico >= self.fundo * self.contraste:
            self._pendente = (agora, pico)
        else:
            self.fundo = 0.95 * self.fundo + 0.05 * max(rms, 1e-4)

        self._confirmadas = [t for t in self._confirmadas if agora - t <= self.intervalo_max * self.palmas]
        if len(self._confirmadas) >= self.palmas:
            ultimas = self._confirmadas[-self.palmas :]
            gaps = np.diff(ultimas)
            self._confirmadas.clear()
            if all(self.intervalo_min <= g <= self.intervalo_max for g in gaps):
                return True
        return False


def jarvis_aberto() -> bool:
    r = subprocess.run(["pgrep", "-f", "jarvis chat --voice"], capture_output=True)
    return r.returncode == 0


def osa(script: str) -> str:
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    return r.stdout.strip()


# Janela do Terminal que roda o chat (o título mostra o comando em execução).
JANELA_CHAT = 'first window whose name contains "jarvis chat"'


def mandar_enter() -> bool:
    """Aperta Enter na janela do chat (faz o Jarvis começar a ouvir) e a traz pra frente."""
    ok = osa(
        'tell application "Terminal"\n'
        f"  if not (exists ({JANELA_CHAT})) then return \"nao\"\n"
        f"  set w to {JANELA_CHAT}\n"
        '  do script "" in w\n'
        "  set miniaturized of w to false\n"
        "  set index of w to 1\n"
        "  activate\n"
        '  return "ok"\n'
        "end tell"
    )
    return ok == "ok"


def esperar_chat_e_ouvir(limite: float = 60.0) -> None:
    """Espera o chat recém-aberto mostrar o prompt `You>` e já começa a ouvir."""
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        time.sleep(1)
        texto = osa(
            'tell application "Terminal"\n'
            f"  if exists ({JANELA_CHAT}) then return contents of selected tab of ({JANELA_CHAT})\n"
            "end tell"
        )
        if "You>" in texto:
            mandar_enter()
            print("Jarvis ouvindo — pode falar.")
            return
    print("O chat demorou demais pra abrir; aperte Enter nele pra falar.")


def interface_no_ar() -> bool:
    try:
        urllib.request.urlopen(URL_INTERFACE + "health", timeout=2)
        return True
    except OSError:
        return False


def abrir_interface() -> None:
    """Sobe o servidor web do Jarvis (se preciso) e abre o dashboard no navegador."""
    if not interface_no_ar():
        subprocess.run(["uv", "run", "jarvis", "start"], cwd=JARVIS_DIR, capture_output=True)
        for _ in range(60):
            if interface_no_ar():
                break
            time.sleep(1)
        else:
            print("A interface visual não subiu; rode `uv run jarvis start` em ~/jarvis.")
            return
    # No Chrome, reaproveita a aba do dashboard se já existir (sem abrir duplicada).
    achou = osa(
        'tell application "Google Chrome"\n'
        "  repeat with w in windows\n"
        "    set i to 0\n"
        "    repeat with t in tabs of w\n"
        "      set i to i + 1\n"
        f'      if URL of t starts with "{URL_INTERFACE}" then\n'
        "        set active tab index of w to i\n"
        "        set index of w to 1\n"
        "        activate\n"
        '        return "ok"\n'
        "      end if\n"
        "    end repeat\n"
        "  end repeat\n"
        "end tell"
    ) if subprocess.run(["pgrep", "-x", "Google Chrome"], capture_output=True).returncode == 0 else ""
    if achou != "ok":
        subprocess.run(["open", URL_INTERFACE], check=False)


def abrir_jarvis() -> None:
    subprocess.Popen(["afplay", SOM_OK])
    servidor_no_ar = interface_no_ar()
    if servidor_no_ar:
        abrir_interface()  # rápido: só foca/abre a aba, antes de trazer o chat pra frente
    else:
        threading.Thread(target=abrir_interface, daemon=True).start()
    if jarvis_aberto():
        if mandar_enter():
            print("Jarvis ouvindo — pode falar.")
        else:
            print("Jarvis já está aberto fora do Terminal (feche-o com Ctrl+C para abrir um novo).")
        return
    cmd = f"cd '{JARVIS_DIR}' && uv run jarvis chat --voice --skip-runtime-panel"
    cmd = cmd.replace("\\", "\\\\").replace('"', '\\"')
    osa(f'tell application "Terminal"\n  do script "{cmd}"\n  activate\nend tell')
    threading.Thread(target=esperar_chat_e_ouvir, daemon=True).start()


def calibrar(sd) -> None:
    print("Bata palmas e fale normalmente. Ctrl+C para sair.\n")
    with sd.InputStream(samplerate=TAXA, blocksize=BLOCO, channels=1, dtype="float32") as s:
        maior = 0.0
        while True:
            dados, _ = s.read(BLOCO)
            pico = float(np.max(np.abs(dados)))
            maior = max(maior * 0.98, pico)
            barra = "█" * int(min(pico, 1.0) * 50)
            print(f"\rpico {pico:5.2f}  (máx recente {maior:4.2f})  {barra:<50}", end="", flush=True)


def escutar(sd, det: DetectorPalmas, espera: float) -> None:
    print(f"Ouvindo... bata {det.palmas} palmas para chamar o Jarvis. Ctrl+C para sair.")
    bloqueado_ate = 0.0
    with sd.InputStream(samplerate=TAXA, blocksize=BLOCO, channels=1, dtype="float32") as s:
        while True:
            dados, _ = s.read(BLOCO)
            agora = time.monotonic()
            if det.alimentar(dados[:, 0], agora) and agora >= bloqueado_ate:
                print(time.strftime("%H:%M:%S"), "👏👏 palmas detectadas — abrindo o Jarvis")
                abrir_jarvis()
                bloqueado_ate = agora + espera


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--calibrar", action="store_true", help="mostra o volume do microfone ao vivo")
    p.add_argument("--limiar", type=float, default=0.25, help="pico mínimo de uma palma (0–1, padrão 0.25)")
    p.add_argument("--palmas", type=int, default=2, help="quantas palmas seguidas (padrão 2)")
    p.add_argument("--espera", type=float, default=5.0, help="segundos ignorando palmas após abrir")
    a = p.parse_args()

    import sounddevice as sd

    try:
        if a.calibrar:
            calibrar(sd)
        else:
            escutar(sd, DetectorPalmas(limiar=a.limiar, palmas=a.palmas), a.espera)
    except KeyboardInterrupt:
        print("\nAté mais.")
    except sd.PortAudioError as e:
        sys.exit(f"Erro no microfone: {e}\nVerifique a permissão em Ajustes > Privacidade e Segurança > Microfone.")


if __name__ == "__main__":
    main()
