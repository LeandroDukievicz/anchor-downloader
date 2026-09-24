"""As defesas que protegem credencial e pasta de destino.

Tudo aqui ja quebrou ou quase quebrou em algum momento: a pasta `instances/`
ficou em 775 numa instalacao real, e nome de arquivo chega de fora, escolhido
por quem publicou a midia no canal. Sao justamente os pontos que um teste
precisa segurar, porque a falha e silenciosa: nada quebra, so fica aberto.
"""

from __future__ import annotations

import os
import stat

import pytest

from anchor_downloader import engine


@pytest.fixture
def umask_permissiva():
    """O pior caso: umask que nao restringe nada.

    Se o codigo depender da umask para fechar um arquivo, e aqui que aparece.
    """
    anterior = os.umask(0o000)
    try:
        yield
    finally:
        os.umask(anterior)


def modo(caminho) -> int:
    return stat.S_IMODE(caminho.stat().st_mode)


# ── Permissoes ──────────────────────────────────────────────────────────────

def test_pasta_aberta_por_versao_antiga_e_consertada(tmp_path, umask_permissiva):
    aberta = tmp_path / "instances"
    aberta.mkdir()
    aberta.chmod(0o777)
    engine.secure_dir(aberta)
    assert modo(aberta) == 0o700


def test_pasta_nova_nasce_fechada(tmp_path, umask_permissiva):
    assert modo(engine.secure_dir(tmp_path / "nova")) == 0o700


def test_lock_de_instancia_nasce_fechado(tmp_path, umask_permissiva, monkeypatch):
    monkeypatch.setattr(engine, "INSTANCE_DIR", tmp_path / "instances")
    slot, handle = engine.acquire_slot()
    try:
        assert modo(tmp_path / "instances" / f"slot_{slot}.lock") == 0o600
    finally:
        handle.close()


def test_estado_da_instancia_nasce_fechado(tmp_path, umask_permissiva, monkeypatch):
    monkeypatch.setattr(engine, "INSTANCE_DIR", tmp_path / "instances")
    monkeypatch.setattr(engine, "instance_state_path",
                        lambda slot: tmp_path / "instances" / f"state_slot_{slot}.json")
    engine.publish_instance_state(1, {"chat": "qualquer"})
    assert modo(engine.instance_state_path(1)) == 0o600


def test_lock_do_destino_nasce_fechado(tmp_path, umask_permissiva):
    destino = tmp_path / "destino"
    with engine.destination_lock(destino):
        pass
    assert modo(destino / ".anchor-downloader.lock") == 0o600


# ── Nome de arquivo vindo do Telegram ───────────────────────────────────────

@pytest.mark.parametrize("hostil", [
    "../../../../etc/passwd",
    "/etc/shadow",
    "a/../../b",
    "..\\..\\windows\\system32",
    ".ssh/authorized_keys",
])
def test_nome_de_arquivo_nao_escapa_da_pasta(hostil):
    """Separador de caminho vira `_`: o nome sempre fica num unico componente."""
    limpo = engine.sanitize_filename(hostil)
    assert "/" not in limpo and "\\" not in limpo
    assert not os.path.isabs(limpo)


@pytest.mark.parametrize("so_pontos", ["..", ".", "...", "  ..  "])
def test_nome_que_e_so_ponto_vira_nome_comum(so_pontos):
    """`..` sozinho seria o diretorio pai; nao pode sobreviver como nome."""
    assert engine.sanitize_filename(so_pontos) == "arquivo"


def test_byte_nulo_nao_passa():
    assert "\x00" not in engine.sanitize_filename("evil\x00.txt")


def test_nome_longo_demais_e_cortado_sem_quebrar_acento():
    """Nome gigante faria a gravacao falhar com ENAMETOOLONG."""
    cortado = engine.sanitize_filename("á" * 400)
    assert len(cortado.encode("utf-8")) <= engine.MAX_FILENAME_BYTES
    assert cortado == "á" * (engine.MAX_FILENAME_BYTES // 2)  # nada partido ao meio


# ── Manifesto adulterado ────────────────────────────────────────────────────

@pytest.mark.parametrize("fuga", ["../fora.txt", "/etc/passwd", "sub/../../fora.txt", "", "."])
def test_manifesto_nao_aponta_para_fora_do_destino(tmp_path, fuga):
    """O manifesto fica na pasta de destino e pode ter sido adulterado."""
    with pytest.raises(RuntimeError):
        engine.safe_manifest_target(tmp_path, fuga)


def test_manifesto_aceita_caminho_legitimo(tmp_path):
    alvo = engine.safe_manifest_target(tmp_path, "Documentos/arquivo.bin")
    assert alvo.parent.name == "Documentos"
    assert alvo.is_relative_to(tmp_path)
