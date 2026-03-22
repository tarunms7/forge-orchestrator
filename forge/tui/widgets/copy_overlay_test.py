"""Tests for CopyOverlay widget."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from forge.tui.widgets.copy_overlay import CopyOverlay, copy_to_clipboard


class TestCopyToClipboard:
    """Tests for the copy_to_clipboard helper function."""

    def test_pbcopy_success(self):
        """copy_to_clipboard succeeds with pbcopy on macOS."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")
        with patch("forge.tui.widgets.copy_overlay.platform") as mock_platform:
            mock_platform.system.return_value = "Darwin"
            with patch(
                "forge.tui.widgets.copy_overlay.subprocess.Popen", return_value=mock_proc
            ) as mock_popen:
                result = copy_to_clipboard("hello")
                assert result is True
                mock_popen.assert_called_once()
                args = mock_popen.call_args[0][0]
                assert args == ["pbcopy"]

    def test_xclip_success(self):
        """copy_to_clipboard succeeds with xclip on Linux."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")
        with patch("forge.tui.widgets.copy_overlay.platform") as mock_platform:
            mock_platform.system.return_value = "Linux"
            with patch("forge.tui.widgets.copy_overlay.subprocess.Popen", return_value=mock_proc):
                result = copy_to_clipboard("hello")
                assert result is True

    def test_clip_success_windows(self):
        """copy_to_clipboard succeeds with clip on Windows."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"", b"")
        with patch("forge.tui.widgets.copy_overlay.platform") as mock_platform:
            mock_platform.system.return_value = "Windows"
            with patch("forge.tui.widgets.copy_overlay.subprocess.Popen", return_value=mock_proc):
                result = copy_to_clipboard("hello")
                assert result is True

    def test_subprocess_not_found(self):
        """copy_to_clipboard returns False when subprocess not available."""
        with patch("forge.tui.widgets.copy_overlay.platform") as mock_platform:
            mock_platform.system.return_value = "Darwin"
            with patch(
                "forge.tui.widgets.copy_overlay.subprocess.Popen",
                side_effect=FileNotFoundError,
            ):
                result = copy_to_clipboard("hello")
                assert result is False

    def test_subprocess_failure(self):
        """copy_to_clipboard returns False when subprocess returns non-zero."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate.return_value = (b"", b"error")
        with patch("forge.tui.widgets.copy_overlay.platform") as mock_platform:
            mock_platform.system.return_value = "Darwin"
            with patch("forge.tui.widgets.copy_overlay.subprocess.Popen", return_value=mock_proc):
                result = copy_to_clipboard("hello")
                assert result is False


class TestCopyOverlayWidget:
    """Tests for the CopyOverlay widget logic."""

    def test_init_empty(self):
        overlay = CopyOverlay()
        assert overlay._lines == []
        assert overlay._cursor == 0
        assert overlay._selected == set()

    def test_init_with_lines(self):
        overlay = CopyOverlay(lines=["line1", "line2", "line3"])
        assert overlay._lines == ["line1", "line2", "line3"]

    def test_cursor_down(self):
        overlay = CopyOverlay(lines=["a", "b", "c"])
        overlay.action_cursor_down()
        assert overlay._cursor == 1
        overlay.action_cursor_down()
        assert overlay._cursor == 2
        overlay.action_cursor_down()
        assert overlay._cursor == 2  # Stays at end

    def test_cursor_up(self):
        overlay = CopyOverlay(lines=["a", "b", "c"])
        overlay._cursor = 2
        overlay.action_cursor_up()
        assert overlay._cursor == 1
        overlay.action_cursor_up()
        assert overlay._cursor == 0
        overlay.action_cursor_up()
        assert overlay._cursor == 0  # Stays at start

    def test_toggle_line(self):
        overlay = CopyOverlay(lines=["a", "b", "c"])
        overlay._cursor = 1
        overlay.action_toggle_line()
        assert 1 in overlay._selected
        assert overlay.selected_count == 1

        overlay.action_toggle_line()
        assert 1 not in overlay._selected
        assert overlay.selected_count == 0

    def test_toggle_line_empty(self):
        overlay = CopyOverlay()
        overlay.action_toggle_line()  # Should not crash
        assert overlay.selected_count == 0

    def test_selected_count(self):
        overlay = CopyOverlay(lines=["a", "b", "c", "d"])
        overlay._selected = {0, 2, 3}
        assert overlay.selected_count == 3

    def test_render_empty(self):
        overlay = CopyOverlay()
        rendered = overlay.render()
        assert "No lines" in rendered

    def test_render_with_lines(self):
        overlay = CopyOverlay(lines=["first", "second"])
        rendered = overlay.render()
        assert "COPY MODE" in rendered
        assert "first" in rendered
        assert "second" in rendered

    def test_render_selected_line(self):
        overlay = CopyOverlay(lines=["a", "b"])
        overlay._selected.add(0)
        rendered = overlay.render()
        assert "●" in rendered  # Selected marker

    def test_copy_selected_posts_message(self):
        """action_copy_selected should call copy_to_clipboard."""
        overlay = CopyOverlay(lines=["line1", "line2", "line3"])
        overlay._selected = {0, 2}
        messages = []
        overlay.post_message = lambda m: messages.append(m)

        with patch("forge.tui.widgets.copy_overlay.copy_to_clipboard", return_value=True):
            overlay.action_copy_selected()

        assert len(messages) == 1
        assert messages[0].success is True
        assert "line1" in messages[0].text
        assert "line3" in messages[0].text

    def test_copy_no_selection_copies_current_line(self):
        """If nothing selected, copies current cursor line."""
        overlay = CopyOverlay(lines=["aaa", "bbb"])
        overlay._cursor = 1
        messages = []
        overlay.post_message = lambda m: messages.append(m)

        with patch("forge.tui.widgets.copy_overlay.copy_to_clipboard", return_value=True):
            overlay.action_copy_selected()

        assert len(messages) == 1
        assert messages[0].text == "bbb"

    def test_cancel_posts_cancelled(self):
        overlay = CopyOverlay(lines=["a"])
        messages = []
        overlay.post_message = lambda m: messages.append(m)
        overlay.action_cancel()
        assert len(messages) == 1
        assert isinstance(messages[0], CopyOverlay.Cancelled)
