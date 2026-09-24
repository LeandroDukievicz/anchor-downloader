"""A abertura: geometria dos quadros, e o contrato com a conexao.

O que importa aqui nao e a beleza — isso se confere com os olhos. E que a
abertura nunca prenda quem esta esperando o painel: ela sai assim que a conexao
resolve, sai no teto quando a conexao demora, e some inteira em terminal
pequeno em vez de desenhar algo espremido.
"""

from __future__ import annotations

import asyncio

import pytest

from anchor_downloader import splash


def _linhas(elapsed: float, width: int, height: int) -> list[str]:
    return splash.render_frame(elapsed, width, height).plain.split("\n")


def test_o_quadro_tem_exatamente_o_tamanho_do_terminal():
    linhas = _linhas(1.0, 80, 24)
    assert len(linhas) == 24
    assert {len(linha) for linha in linhas} == {80}


def test_o_quadro_e_deterministico():
    """Sem `random`: o mesmo instante desenha sempre o mesmo quadro."""
    assert _linhas(2.0, 80, 24) == _linhas(2.0, 80, 24)


def test_a_ancora_aparece_no_primeiro_quadro():
    """Um primeiro segundo de tela vazia faria a abertura parecer travada."""
    assert "█" in splash.render_frame(0.05, 100, 30).plain


def test_a_ancora_para_no_fundo_e_nao_afunda_mais():
    cena = splash.Scene(100, 30)
    no_impacto, pousou = splash._anchor_row(splash.DESCENT_SECONDS, cena)
    depois, ainda = splash._anchor_row(splash.DESCENT_SECONDS + 3, cena)
    assert pousou and ainda
    assert no_impacto == depois == cena.rest_row
    assert cena.rest_row + splash.ANCHOR_HEIGHT - 1 == cena.bed_row


def test_a_marca_so_entra_depois_do_impacto():
    antes, _, _ = splash._brand_progress(splash.DESCENT_SECONDS)
    assert antes == ""
    primeira, _, _ = splash._brand_progress(
        splash.DESCENT_SECONDS + splash.IMPACT_SECONDS + splash.BRAND_SECONDS * 0.5
    )
    assert primeira and "ANCHOR".startswith(primeira)


def test_a_marca_inteira_cabe_dentro_do_piso_de_tempo():
    """O piso existe para a marca terminar de escrever antes da abertura sair."""
    primeira, segunda, _ = splash._brand_progress(splash.FLOOR_SECONDS)
    assert (primeira, segunda) == ("ANCHOR", "DOWNLOADER")


@pytest.mark.parametrize(
    "width, height, cabe",
    [(100, 30, True), (splash.MIN_WIDTH, splash.MIN_HEIGHT, True),
     (splash.MIN_WIDTH - 1, 30, False), (100, splash.MIN_HEIGHT - 1, False)],
)
def test_terminal_pequeno_nao_ganha_abertura(width, height, cabe):
    assert splash.fits(width, height) is cabe


def test_o_piso_cabe_dentro_do_teto():
    assert splash.FLOOR_SECONDS < splash.CEILING_SECONDS


@pytest.mark.asyncio
async def test_a_abertura_espera_a_conexao_e_sai_quando_ela_resolve():
    """Com a conexao pendente, a abertura segura mesmo passado o piso."""
    conexao = asyncio.get_running_loop().create_future()
    tela = splash.SplashScreen(conexao)
    tela.elapsed = splash.FLOOR_SECONDS + 0.5
    assert not tela.should_close()
    conexao.set_result(None)
    assert tela.should_close()


@pytest.mark.asyncio
async def test_a_abertura_desiste_de_esperar_no_teto():
    """Rede ruim nao pode transformar a abertura em sala de espera."""
    conexao = asyncio.get_running_loop().create_future()
    tela = splash.SplashScreen(conexao)
    tela.elapsed = splash.CEILING_SECONDS
    assert tela.should_close()


def test_sem_nada_para_esperar_a_abertura_sai_no_piso():
    tela = splash.SplashScreen(None)
    tela.elapsed = splash.FLOOR_SECONDS - 0.1
    assert not tela.should_close()
    tela.elapsed = splash.FLOOR_SECONDS
    assert tela.should_close()


# ── A vida em volta da ancora ───────────────────────────────────────────────

def _desenho(pintor, elapsed: float, width: int = 100, height: int = 30) -> list[str]:
    """Roda um pintor sozinho numa cena vazia e devolve so o que ele desenhou."""
    cena = splash.Scene(width, height)
    pintor(cena, elapsed)
    return ["".join(celula.glyph for celula in linha) for linha in cena.cells]


@pytest.mark.parametrize("width", [splash.MIN_WIDTH, 80, 150])
def test_o_recife_fica_nos_cantos_e_nao_atras_da_ancora(width):
    """Coral desenhado no miolo some assim que a ancora fundeia em cima dele."""
    cena = splash.Scene(width, 30)
    splash._paint_reef(cena, 0.0)
    splash._paint_octopus(cena, 0.0)
    ocupadas = {
        coluna
        for linha in cena.cells
        for coluna, celula in enumerate(linha)
        if celula.glyph != " "
    }
    esquerda = cena.axis - splash.ANCHOR_WIDTH // 2
    ancora = {
        esquerda + offset
        for linha in splash.ANCHOR
        for offset, glyph in enumerate(linha)
        if glyph != " "
    }
    assert ocupadas and not ocupadas & ancora


def test_o_recife_nasce_do_leito():
    """Coral e estrela apoiados no fundo; soltos na agua viravam enfeite."""
    linhas = _desenho(splash._paint_reef, 0.0)
    cena = splash.Scene(100, 30)
    assert linhas[cena.bed_row - 1].strip()


def test_o_polvo_cabe_no_menor_terminal_aceito():
    cena = splash.Scene(splash.MIN_WIDTH, splash.MIN_HEIGHT)
    splash._paint_octopus(cena, 0.0)
    colunas = [
        coluna
        for linha in cena.cells
        for coluna, celula in enumerate(linha)
        if celula.glyph != " "
    ]
    assert colunas, "o polvo sumiu justamente no tamanho mais apertado"
    assert min(colunas) > cena.axis + splash.ANCHOR_WIDTH // 2


def test_o_cardume_atravessa_a_agua_sem_entrar_no_recife():
    """Peixe desenhado em cima de coral vira rabisco, entao ele nao desce ate la."""
    cena = splash.Scene(100, 30)
    for quadro in range(96):
        splash._paint_fish(cena, quadro * splash.FRAME_SECONDS)
    fundo = cena.cells[cena.bed_row - 2] + cena.cells[cena.bed_row - 1]
    assert {celula.glyph for celula in fundo} == {" "}


def test_o_cardume_se_move_e_repete_o_mesmo_caminho():
    assert _desenho(splash._paint_fish, 0.0) != _desenho(splash._paint_fish, 1.0)
    assert _desenho(splash._paint_fish, 1.0) == _desenho(splash._paint_fish, 1.0)


def test_a_ancora_solta_bolhas_enquanto_desce_e_para_ao_fundear():
    """O rastro e o que faz a queda parecer rapida; parada, ela nao bolha mais."""
    descendo = splash.Scene(100, 30)
    splash._paint_wake(descendo, 2.6, 12, landed=False)
    assert any(celula.glyph != " " for linha in descendo.cells for celula in linha)

    fundeada = splash.Scene(100, 30)
    splash._paint_wake(fundeada, 6.0, fundeada.rest_row, landed=True)
    assert all(celula.glyph == " " for linha in fundeada.cells for celula in linha)


def test_o_quadro_inteiro_mostra_o_cardume_o_recife_e_o_polvo():
    quadro = splash.render_frame(2.0, 120, 34).plain
    assert "><" in quadro, "cardume"
    assert "▄█▄█▄" in quadro, "coral de leque"
    assert "▀▄█▄▀" in quadro, "estrela do mar"
    assert "│" in quadro, "tentaculos do polvo"


# ── Integracao com o app ────────────────────────────────────────────────────

def _app(**kwargs):
    from anchor_downloader.app import DownloaderApp
    return DownloaderApp(**kwargs)


def test_as_instrucoes_aparecem_para_quem_nao_tem_sessao(tmp_path, monkeypatch):
    from anchor_downloader import engine
    monkeypatch.setattr(engine, "STRING_SESSION_FILE", tmp_path / "ausente")
    assert _app(demo=False, offline=False).needs_welcome()


def test_as_instrucoes_somem_depois_do_primeiro_login(tmp_path, monkeypatch):
    from anchor_downloader import engine
    sessao = tmp_path / "session_string"
    sessao.write_text("qualquer coisa", encoding="utf-8")
    monkeypatch.setattr(engine, "STRING_SESSION_FILE", sessao)
    assert not _app(demo=False, offline=False).needs_welcome()


def test_demonstracao_e_modo_offline_nao_pedem_conexao(tmp_path, monkeypatch):
    """Quem pediu --demo ou --offline nao quer conectar agora."""
    from anchor_downloader import engine
    monkeypatch.setattr(engine, "STRING_SESSION_FILE", tmp_path / "ausente")
    assert not _app(demo=True, offline=False).needs_welcome()
    assert not _app(demo=False, offline=True).needs_welcome()
