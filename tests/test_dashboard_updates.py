from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import pytest

from anchor_downloader import engine
from anchor_downloader.app import DownloaderApp


@pytest.mark.asyncio
async def test_unchanged_file_rows_are_not_rewritten():
    app = DownloaderApp(demo=True, offline=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#files")

        with patch.object(table, "update_cell", wraps=table.update_cell) as update_cell:
            app._update_files()
            assert update_cell.call_count == 0

            app.state["downloads"][0].current_bytes += 1
            app._update_files()
            assert update_cell.call_count == len(table.columns)

            app.filter_name = "done"
            app.row_signature = None
            app._update_files()
            assert len(table.rows) == 1


@pytest.mark.asyncio
async def test_first_incremental_row_becomes_selected():
    app = DownloaderApp(demo=False, offline=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert app.selected_id is None
        download = engine.Download(
            1, SimpleNamespace(id=1, chat_id=1), "novo.bin", Path("/tmp/novo.bin"),
            "Documentos", 10, "", {"title": "Teste", "reference": "", "id": "1", "link": ""},
        )
        app.state["downloads"].append(download)
        app._update_files()
        assert app.selected_id == 1


@pytest.mark.asyncio
async def test_empty_filter_clears_rows_before_incremental_match():
    app = DownloaderApp(demo=True, offline=True)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#files")
        assert len(table.rows) == len(app.state["downloads"])

        app.filter_name = "errors"
        app.row_signature = None
        app._update_files()

        assert len(table.rows) == 0
        assert app.row_signature == ()

        first = app.state["downloads"][0]
        first.status = "error"
        app._update_files()

        assert tuple(row_key.value for row_key in table.rows) == (str(first.index),)


@pytest.mark.asyncio
async def test_table_cache_divergence_forces_safe_rebuild():
    app = DownloaderApp(demo=True, offline=True)
    async with app.run_test(size=(160, 40)) as pilot:
        await pilot.pause()
        table = app.query_one("#files")
        expected_keys = tuple(str(item.index) for item in app.state["downloads"])

        app.row_signature = ()
        app._update_files()

        assert tuple(row_key.value for row_key in table.rows) == expected_keys
