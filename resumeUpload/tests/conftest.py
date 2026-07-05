import os
import io

os.environ.setdefault("NVIDIA_API_KEY", "test-nvidia-key")
os.environ.setdefault("OPENROUTER_API_KEY", "test-openrouter-key")
os.environ.setdefault("OPENAI_API_KEY", "test-openrouter-key")  # ChatOpenAI's underlying SDK checks this name too
os.environ.setdefault("MONGO_USERNAME", "test-user")
os.environ.setdefault("MONGO_PASSWORD", "test-pass")


def make_blank_pdf_bytes() -> bytes:
    """A minimal, structurally valid PDF with a page but zero extractable
    text — this is exactly what pypdf hands back for a scanned/image-only
    PDF, which is the real-world case app.py's empty-text check guards."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()