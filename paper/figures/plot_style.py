"""Shared typography for figures embedded in the USENIX paper."""
from __future__ import annotations

from pathlib import Path


def apply_paper_style(matplotlib) -> None:
    """Match the paper's Times-compatible body face in vector outputs."""
    from matplotlib import font_manager

    # Liberation Serif is metrically compatible with the Times/Nimbus Roman
    # family used by the paper and embeds cleanly as searchable TrueType text.
    font_root = Path("/usr/share/fonts/truetype/liberation")
    for style in ("Regular", "Bold", "Italic", "BoldItalic"):
        font_path = font_root / f"LiberationSerif-{style}.ttf"
        if font_path.is_file():
            font_manager.fontManager.addfont(font_path)

    matplotlib.rcParams.update({
        "font.family": "serif",
        "font.serif": [
            "Liberation Serif",
            "Times New Roman",
            "Times",
            "Nimbus Roman",
            "DejaVu Serif",
        ],
        "mathtext.fontset": "stix",
        "axes.unicode_minus": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })
