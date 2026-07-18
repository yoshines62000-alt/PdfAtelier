"""Operations PDF pures (aucun etat GUI ici) : fusion, division, gestion des
pages, compression, conversion image<->PDF, filigrane, mot de passe, extraction
de texte. Tout se passe en local sur le disque de l'utilisateur - aucun fichier
n'est jamais envoye a un service tiers, contrairement aux convertisseurs PDF en
ligne."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pypdf import PdfReader, PdfWriter
from PIL import Image


class PdfOpsError(Exception):
    """Erreur metier (mot de passe incorrect, plage de pages invalide...),
    distincte d'une exception technique inattendue."""


def _open_reader(input_path: Path, password: Optional[str] = None) -> PdfReader:
    input_path = Path(input_path)
    reader = PdfReader(str(input_path))
    if reader.is_encrypted:
        if not password:
            raise PdfOpsError(f"Le fichier {input_path.name} est protege par un mot de passe.")
        if reader.decrypt(password) == 0:
            raise PdfOpsError(f"Mot de passe incorrect pour {input_path.name}.")
    return reader


def get_page_count(input_path: Path, password: Optional[str] = None) -> int:
    reader = _open_reader(input_path, password=password)
    return len(reader.pages)


def _write_output(writer: PdfWriter, output_path: Path) -> None:
    """Ecrit le resultat de maniere atomique : sur un fichier temporaire dans
    le meme dossier, puis remplace la destination d'un seul coup (os.replace).
    Indispensable pour le cas ou l'utilisateur enregistre par-dessus le
    fichier source lui-meme : ouvrir la destination directement en ecriture
    la tronquerait immediatement a zero octet, alors que le PdfReader source
    peut encore avoir besoin d'y lire des objets non materialises pendant
    l'ecriture - corrompant l'unique copie du document."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(output_path.parent), suffix=".pdfatelier.tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            writer.write(f)
        os.replace(tmp_name, output_path)
    except Exception:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


def _latin1_safe(text: str) -> str:
    """Remplace tout caractere non representable par la police de base
    Helvetica (Latin-1 uniquement) par '?', pour degrader proprement plutot
    que de lever une exception sur un texte de filigrane contenant des
    emoji/tirets longs/guillemets courbes."""
    return text.encode("latin-1", errors="replace").decode("latin-1")


# -- fusion / division -------------------------------------------------------

def merge_pdfs(input_paths: list, output_path: Path, passwords: Optional[list] = None) -> None:
    if not input_paths:
        raise PdfOpsError("Aucun fichier a fusionner.")
    passwords = passwords or [None] * len(input_paths)
    writer = PdfWriter()
    for path, password in zip(input_paths, passwords):
        reader = _open_reader(Path(path), password=password)
        for page in reader.pages:
            writer.add_page(page)
    _write_output(writer, output_path)


def split_pdf_by_ranges(input_path: Path, ranges: list, output_dir: Path, base_name: str, password: Optional[str] = None) -> list:
    """ranges : liste de tuples (debut, fin) en 1-indexe, inclusifs des deux
    cotes. Renvoie la liste des chemins generes."""
    reader = _open_reader(input_path, password=password)
    page_count = len(reader.pages)
    output_paths = []
    for index, (start, end) in enumerate(ranges, start=1):
        if start < 1 or end > page_count or start > end:
            raise PdfOpsError(f"Plage de pages invalide : {start}-{end} (document de {page_count} pages).")
        writer = PdfWriter()
        for page_number in range(start, end + 1):
            writer.add_page(reader.pages[page_number - 1])
        output_path = Path(output_dir) / f"{base_name}_{index:02d}_p{start}-{end}.pdf"
        _write_output(writer, output_path)
        output_paths.append(output_path)
    return output_paths


def split_pdf_every_n_pages(input_path: Path, n: int, output_dir: Path, base_name: str, password: Optional[str] = None) -> list:
    if n < 1:
        raise PdfOpsError("Le nombre de pages par fichier doit etre positif.")
    reader = _open_reader(input_path, password=password)
    page_count = len(reader.pages)
    ranges = [(start, min(start + n - 1, page_count)) for start in range(1, page_count + 1, n)]
    return split_pdf_by_ranges(input_path, ranges, output_dir, base_name, password=password)


# -- gestion des pages --------------------------------------------------------

def reorder_and_filter_pages(
    input_path: Path, output_path: Path, page_order: list,
    rotations: Optional[dict] = None, password: Optional[str] = None,
) -> None:
    """page_order : liste des numeros de page (1-indexe, dans le document
    source) a conserver, dans l'ordre souhaite - les pages absentes de la
    liste sont supprimees. rotations : dict {numero_de_page (1-indexe, dans
    le document source): degres a ajouter (multiple de 90)}."""
    reader = _open_reader(input_path, password=password)
    page_count = len(reader.pages)
    rotations = rotations or {}
    for page_number in page_order:
        if page_number < 1 or page_number > page_count:
            raise PdfOpsError(f"Numero de page invalide : {page_number} (document de {page_count} pages).")

    # On attache d'abord tout le document au writer (append), puis on
    # travaille sur les pages du writer : modifier une page encore
    # rattachee au reader seul est deconseille par pypdf (deprecation
    # prevue en 7.0, comportement juge peu fiable).
    source_writer = PdfWriter()
    source_writer.append(reader)
    for page_number, extra_rotation in rotations.items():
        if extra_rotation:
            source_writer.pages[page_number - 1].rotate(extra_rotation)

    writer = PdfWriter()
    for page_number in page_order:
        writer.add_page(source_writer.pages[page_number - 1])
    _write_output(writer, output_path)


# -- compression ---------------------------------------------------------------

@dataclass
class CompressionResult:
    original_size: int
    compressed_size: int
    images_recompressed: int = 0
    images_total: int = 0

    @property
    def ratio_percent(self) -> float:
        if self.original_size == 0:
            return 0.0
        return round(100 * (1 - self.compressed_size / self.original_size), 1)

    @property
    def images_failed(self) -> int:
        return self.images_total - self.images_recompressed


def compress_pdf(
    input_path: Path, output_path: Path, image_quality: int = 60,
    max_dimension: int = 1600, password: Optional[str] = None,
) -> CompressionResult:
    """Recompresse les images integrees (JPEG, qualite et dimension max
    reglables) et les flux de contenu. Un document sans image embarquee ne
    beneficiera que de la compression des flux (marginale)."""
    original_size = Path(input_path).stat().st_size
    reader = _open_reader(input_path, password=password)
    writer = PdfWriter()
    # writer.append() clone le document entier dans le writer, en attachant
    # correctement chaque page a celui-ci - necessaire pour que
    # compress_content_streams() (qui exige une page rattachee a un writer)
    # et le remplacement d'image fonctionnent tous deux sur les memes objets.
    writer.append(reader)
    images_total = 0
    images_recompressed = 0
    for page in writer.pages:
        for img in page.images:
            images_total += 1
            try:
                image = img.image
                if image is None:
                    continue
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
                if max(image.size) > max_dimension:
                    image.thumbnail((max_dimension, max_dimension))
                img.replace(image, quality=image_quality)
                images_recompressed += 1
            except Exception:
                # Certains formats d'image embarques (ex: CMYK, masques de
                # transparence particuliers, image corrompue) ne se laissent
                # pas toujours decoder/remplacer proprement : on garde alors
                # l'image d'origine plutot que de faire echouer toute la
                # compression pour une seule image problematique. On
                # comptabilise l'echec plutot que de le passer sous silence,
                # pour que l'utilisateur comprenne un taux de reduction
                # plus faible que prevu.
                continue
        page.compress_content_streams()
    _write_output(writer, output_path)
    compressed_size = Path(output_path).stat().st_size
    return CompressionResult(original_size, compressed_size, images_recompressed, images_total)


# -- conversion image <-> PDF ---------------------------------------------------

def pdf_to_images(input_path: Path, output_dir: Path, base_name: str, dpi: int = 150, fmt: str = "png") -> list:
    import pypdfium2 as pdfium

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72.0
    pdf = pdfium.PdfDocument(str(input_path))
    output_paths = []
    try:
        for index, page in enumerate(pdf, start=1):
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            bitmap.close()
            page.close()
            output_path = output_dir / f"{base_name}_p{index:03d}.{fmt}"
            image.save(output_path)
            output_paths.append(output_path)
    finally:
        pdf.close()
    return output_paths


def images_to_pdf(image_paths: list, output_path: Path) -> None:
    if not image_paths:
        raise PdfOpsError("Aucune image a assembler.")
    images = []
    for path in image_paths:
        image = Image.open(path)
        if image.mode != "RGB":
            image = image.convert("RGB")
        images.append(image)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    images[0].save(output_path, save_all=True, append_images=images[1:])


# -- filigrane -------------------------------------------------------------------

def add_text_watermark(
    input_path: Path, output_path: Path, text: str, opacity: float = 0.3,
    font_size: int = 40, angle: float = 45.0, password: Optional[str] = None,
) -> None:
    import io

    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    text = _latin1_safe(text)
    reader = _open_reader(input_path, password=password)
    writer = PdfWriter()
    # Comme pour compress_pdf : on attache d'abord tout le document au
    # writer (append), puis on modifie ses pages - modifier une page encore
    # rattachee au reader seul est deconseille par pypdf (deprecation prevue
    # en 7.0, comportement juge peu fiable).
    writer.append(reader)
    for page in writer.pages:
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(width, height))
        c.setFont("Helvetica", font_size)
        c.setFillColor(Color(0.5, 0.5, 0.5, alpha=max(0.0, min(1.0, opacity))))
        c.saveState()
        c.translate(width / 2, height / 2)
        c.rotate(angle)
        c.drawCentredString(0, 0, text)
        c.restoreState()
        c.save()
        buffer.seek(0)

        overlay_reader = PdfReader(buffer)
        page.merge_page(overlay_reader.pages[0])
    _write_output(writer, output_path)


_PAGE_NUMBER_POSITIONS = ("bas-centre", "bas-droite", "bas-gauche", "haut-centre", "haut-droite", "haut-gauche")


def add_page_numbers(
    input_path: Path, output_path: Path, position: str = "bas-centre",
    start_at: int = 1, font_size: int = 10, fmt: str = "{page} / {total}",
    password: Optional[str] = None,
) -> None:
    """Superpose un numero de page sur chaque page, selon le meme principe de
    calque que add_text_watermark (un mini-PDF genere par reportlab, fusionne
    par-dessus la page existante via merge_page). `start_at` permet de
    demarrer la numerotation a une valeur autre que 1 (ex : un document dont
    la page 1 est une couverture non numerotee, mais que l'on souhaite quand
    meme voir "2 / 12" sur la deuxieme page)."""
    import io

    from reportlab.pdfgen import canvas

    if position not in _PAGE_NUMBER_POSITIONS:
        raise PdfOpsError(f"Position de numerotation invalide : {position}")
    fmt = _latin1_safe(fmt)
    reader = _open_reader(input_path, password=password)
    writer = PdfWriter()
    writer.append(reader)
    total = len(writer.pages)
    margin = 20

    for index, page in enumerate(writer.pages):
        width = float(page.mediabox.width)
        height = float(page.mediabox.height)
        label = fmt.format(page=start_at + index, total=total)

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=(width, height))
        c.setFont("Helvetica", font_size)
        if "centre" in position:
            x, align = width / 2, "center"
        elif "droite" in position:
            x, align = width - margin, "right"
        else:
            x, align = margin, "left"
        y = height - margin if position.startswith("haut") else margin

        if align == "center":
            c.drawCentredString(x, y, label)
        elif align == "right":
            c.drawRightString(x, y, label)
        else:
            c.drawString(x, y, label)
        c.save()
        buffer.seek(0)

        overlay_reader = PdfReader(buffer)
        page.merge_page(overlay_reader.pages[0])
    _write_output(writer, output_path)


# -- protection par mot de passe --------------------------------------------------

def set_password(
    input_path: Path, output_path: Path, user_password: str,
    owner_password: Optional[str] = None, password: Optional[str] = None,
) -> None:
    """password : mot de passe actuel du fichier source, s'il est deja
    protege. user_password/owner_password : le nouveau mot de passe a
    appliquer."""
    if not user_password:
        raise PdfOpsError("Le mot de passe ne peut pas etre vide.")
    reader = _open_reader(input_path, password=password)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(user_password, owner_password or user_password)
    _write_output(writer, output_path)


def remove_password(input_path: Path, output_path: Path, password: str) -> None:
    reader = _open_reader(input_path, password=password)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    _write_output(writer, output_path)


# -- extraction de texte -----------------------------------------------------------

def extract_text(input_path: Path, password: Optional[str] = None) -> list:
    """Renvoie une liste de chaines, une par page."""
    reader = _open_reader(input_path, password=password)
    return [page.extract_text() or "" for page in reader.pages]


def extract_embedded_images(
    input_path: Path, output_dir: Path, base_name: str, password: Optional[str] = None
) -> list:
    """Extrait les images embarquees telles quelles (logos, photos...), sans
    jamais rasteriser la page entiere - contrairement a pdf_to_images qui
    rend chaque page complete en image, ceci recupere directement les objets
    image du PDF (page.images de pypdf), a leur resolution d'origine.
    Une image individuelle corrompue/non decodable est ignoree plutot que de
    faire echouer toute l'extraction - meme philosophie que compress_pdf
    (voir CompressionResult) : un probleme localise ne doit jamais empecher
    de recuperer le reste."""
    reader = _open_reader(input_path, password=password)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = []
    for page_index, page in enumerate(reader.pages, start=1):
        for image_index, image_file in enumerate(page.images, start=1):
            try:
                image = image_file.image
                if image is None:
                    continue
                ext = Path(image_file.name).suffix.lstrip(".").lower() or "png"
                if ext in ("jpg", "jpeg"):
                    ext = "jpg"
                    save_image = image.convert("RGB") if image.mode not in ("RGB", "L") else image
                else:
                    ext = "png"
                    save_image = image
                output_path = output_dir / f"{base_name}_p{page_index:03d}_img{image_index:02d}.{ext}"
                save_image.save(output_path)
                output_paths.append(output_path)
            except (OSError, ValueError):
                continue
    return output_paths
