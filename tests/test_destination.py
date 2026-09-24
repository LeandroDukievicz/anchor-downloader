"""Validacao do destino, incluindo o caso em que quem barra e o snap.

Fora de um snap nada pode mudar: quem instalou por pipx ou pelo codigo-fonte
tem acesso total ao disco, e uma validacao a mais so criaria recusa inventada.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from anchor_downloader.dialogs import _destination_path


def test_caminho_relativo_e_recusado():
    with pytest.raises(ValueError, match="caminho absoluto"):
        _destination_path("Downloads/Telegram")


def test_destino_comum_passa(tmp_path):
    assert _destination_path(str(tmp_path)) == tmp_path


def test_fora_do_snap_o_disco_externo_nao_e_barrado(monkeypatch):
    """Sem as variaveis do snap, `/mnt/...` e um caminho como qualquer outro."""
    monkeypatch.delenv("SNAP_INSTANCE_NAME", raising=False)
    monkeypatch.delenv("SNAP_NAME", raising=False)
    assert _destination_path("/mnt/hd/telegram") == Path("/mnt/hd/telegram")


def test_dentro_do_snap_o_disco_bloqueado_ensina_o_comando(monkeypatch):
    """O que a pessoa precisa nao e saber que falhou: e a linha que resolve."""
    monkeypatch.setenv("SNAP_INSTANCE_NAME", "anchor-downloader")
    monkeypatch.setattr("anchor_downloader.dialogs.os.access", lambda *a, **k: False)
    with pytest.raises(ValueError) as erro:
        _destination_path("/run/media/eu/NETAC/telegram")
    assert "sudo snap connect anchor-downloader:removable-media" in str(erro.value)


def test_dentro_do_snap_o_disco_ja_liberado_passa(monkeypatch):
    """Com a interface conectada a escrita funciona, e o aviso nao aparece."""
    monkeypatch.setenv("SNAP_INSTANCE_NAME", "anchor-downloader")
    monkeypatch.setattr("anchor_downloader.dialogs.os.access", lambda *a, **k: True)
    assert _destination_path("/mnt/hd/telegram") == Path("/mnt/hd/telegram")


def test_dentro_do_snap_a_home_nao_dispara_o_aviso(monkeypatch):
    """`~/Downloads` e coberto pela interface `home`, que conecta sozinha: o
    caminho padrao nunca pode esbarrar na mensagem de disco externo."""
    monkeypatch.setenv("SNAP_INSTANCE_NAME", "anchor-downloader")
    monkeypatch.setattr("anchor_downloader.dialogs.os.access", lambda *a, **k: False)
    destino = Path.home() / "Downloads" / "Telegram"
    assert _destination_path(str(destino)) == destino
