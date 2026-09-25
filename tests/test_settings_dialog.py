"""API ID e API Hash sao requisito para o programa funcionar.

Nao existe credencial embutida: `engine.load_config` recusa a conexao sem os
dois. Durante um tempo o formulario os chamou de opcionais e so o botao
Conectar os cobrava — quem apertava Salvar com os campos vazios saia da tela
sem aviso nenhum, achando que estava configurado, e so descobria depois. Estes
testes seguram os dois lados: o formulario cobra, e nada e gravado sem eles.
"""

from __future__ import annotations

import json

import pytest
from textual.app import App
from textual.widgets import Button, Input, Static

from anchor_downloader import dialogs


class Hospedeira(App):
    """App minima que so existe para abrir o dialogo."""

    def __init__(self, tela) -> None:
        super().__init__()
        self.tela = tela
        self.resultado: object = "nao-fechou"

    def on_mount(self) -> None:
        self.push_screen(self.tela, lambda valor: setattr(self, "resultado", valor))


async def abrir(monkeypatch, tmp_path):
    config = tmp_path / "config.json"
    monkeypatch.setattr(dialogs, "CONFIG_FILE", config)
    return config, Hospedeira(dialogs.SettingsScreen(4))


def erro(tela) -> str:
    return str(tela.query_one("#config-error", Static).content)


def preencher(tela, api_id: str, api_hash: str) -> None:
    tela.query_one("#config-api-id", Input).value = api_id
    tela.query_one("#config-api-hash", Input).value = api_hash


@pytest.mark.parametrize("botao", ["#config-save", "#config-connect"])
@pytest.mark.asyncio
async def test_nenhum_botao_aceita_api_id_vazio(tmp_path, monkeypatch, botao):
    config, app = await abrir(monkeypatch, tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        tela = app.screen
        preencher(tela, "", "a" * 32)
        tela.query_one(botao, Button).press()
        await pilot.pause()

        assert "API ID" in erro(tela)
        assert not config.exists(), "nada pode ser gravado sem as credenciais"
        assert app.resultado == "nao-fechou", "o dialogo nao pode fechar no erro"


@pytest.mark.parametrize("botao", ["#config-save", "#config-connect"])
@pytest.mark.asyncio
async def test_nenhum_botao_aceita_api_hash_vazio(tmp_path, monkeypatch, botao):
    config, app = await abrir(monkeypatch, tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        tela = app.screen
        preencher(tela, "12345", "")
        tela.query_one(botao, Button).press()
        await pilot.pause()

        assert "API Hash" in erro(tela)
        assert not config.exists()
        assert app.resultado == "nao-fechou"


@pytest.mark.asyncio
async def test_com_os_dois_preenchidos_grava_e_fecha(tmp_path, monkeypatch):
    config, app = await abrir(monkeypatch, tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        tela = app.screen
        preencher(tela, "12345", "0123456789abcdef0123456789ABCDEF")
        tela.query_one("#config-save", Button).press()
        await pilot.pause()

    gravado = json.loads(config.read_text(encoding="utf-8"))
    assert gravado["api_id"] == 12345
    assert gravado["api_hash"] == "0123456789abcdef0123456789ABCDEF"
    assert gravado["concurrent_downloads"] == 4
    assert app.resultado == {"concurrency": 4}


@pytest.mark.asyncio
async def test_conectar_devolve_o_pedido_de_login(tmp_path, monkeypatch):
    config, app = await abrir(monkeypatch, tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        tela = app.screen
        preencher(tela, "12345", "a" * 32)
        tela.query_one("#config-connect", Button).press()
        await pilot.pause()

    assert app.resultado == {"concurrency": 4, "login": True}
    assert config.exists()


@pytest.mark.asyncio
async def test_a_tela_nao_chama_as_credenciais_de_opcionais(tmp_path, monkeypatch):
    """O rotulo e o que o usuario le antes de decidir se pode pular o campo."""
    _, app = await abrir(monkeypatch, tmp_path)
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        # Label herda de Static, entao esta consulta pega rotulo e corpo.
        texto = " ".join(str(no.content) for no in app.screen.query(Static))

        assert "opcional" not in texto.lower()
        assert "my.telegram.org" in texto, "a tela precisa dizer onde pegar"
