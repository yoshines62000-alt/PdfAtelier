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
from pypdf.errors import PyPdfError
from PIL import Image


class PdfOpsError(Exception):
    """Erreur metier (mot de passe incorrect, plage de pages invalide...),
    distincte d'une exception technique inattendue."""


def _open_reader(input_path: Path, password: Optional[str] = None) -> PdfReader:
    input_path = Path(input_path)
    reader = PdfReader(str(input_path))
    if reader.is_encrypted:
        if not password:
            # PDF protege uniquement par un mot de passe "proprietaire"
            # (permissions restreintes : copier/imprimer...) mais sans mot
            # de passe d'ouverture reel - categorie tres courante (exports
            # administratifs/bancaires). Ce type de fichier s'ouvre
            # nativement avec un mot de passe utilisateur vide, exactement
            # comme le ferait Adobe Acrobat ou un navigateur, qui n'affichent
            # jamais de demande de mot de passe pour ce cas. On tente donc
            # une chaine vide avant d'exiger quoi que ce soit de
            # l'utilisateur (bug trouve a l'audit : un mot de passe qui
            # n'existe pas etait systematiquement reclame, bloquant tout
            # l'outil sur ce type de PDF pourtant lisible partout ailleurs).
            if reader.decrypt("") == 0:
                raise PdfOpsError(f"Le fichier {input_path.name} est protege par un mot de passe.")
        elif reader.decrypt(password) == 0:
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
    cotes. Renvoie la liste des chemins generes.

    Relancer une division vers le meme dossier (meme fichier redivise, ou
    deux fichiers source de meme nom depuis des dossiers differents)
    produirait sinon les memes noms de fichiers de sortie que la fois
    precedente, la nouvelle execution ecrasant silencieusement les resultats
    d'avant - bug trouve a l'audit. On applique ici le meme mecanisme de
    contournement de collision (verifier `.exists()` sur le disque, pas
    seulement les noms deja generes pendant cet appel) que celui deja en
    place dans extract_attachments/extract_embedded_images."""
    reader = _open_reader(input_path, password=password)
    page_count = len(reader.pages)
    output_dir = Path(output_dir)
    output_paths = []
    used_paths = set()
    for index, (start, end) in enumerate(ranges, start=1):
        if start < 1 or end > page_count or start > end:
            raise PdfOpsError(f"Plage de pages invalide : {start}-{end} (document de {page_count} pages).")
        writer = PdfWriter()
        for page_number in range(start, end + 1):
            writer.add_page(reader.pages[page_number - 1])
        stem = f"{base_name}_{index:02d}_p{start}-{end}"
        output_path = output_dir / f"{stem}.pdf"
        counter = 1
        while output_path.exists() or output_path in used_paths:
            output_path = output_dir / f"{stem} ({counter}).pdf"
            counter += 1
        used_paths.add(output_path)
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
        # Indexation manuelle (pas `for img in page.images`) : acceder a
        # `page.images[i]` est ce qui declenche reellement le decodage de
        # l'image (via pypdf), et c'est exactement ce qui peut lever une
        # exception - notamment pypdf.errors.LimitReachedError, la
        # protection anti-bombe-de-decompression native de pypdf, qui se
        # declenche des qu'une image declare des dimensions demesurees
        # (flux minuscule sur disque, bitmap theorique enorme). Boucler
        # directement sur l'iterateur de page.images ferait lever cette
        # exception PENDANT l'evaluation de la boucle for elle-meme, avant
        # meme d'atteindre le `try:` ci-dessous - abandonnant alors la
        # compression de TOUT le document au lieu de sauter uniquement
        # l'image fautive (bug trouve a l'audit : meme classe de probleme
        # que celui deja corrige dans extract_embedded_images, jamais
        # applique ici jusqu'a present).
        image_count = len(page.images)
        for image_index in range(image_count):
            images_total += 1
            try:
                img = page.images[image_index]
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
                # transparence particuliers, image corrompue, bombe de
                # decompression) ne se laissent pas toujours decoder/
                # remplacer proprement : on garde alors l'image d'origine
                # plutot que de faire echouer toute la compression pour une
                # seule image problematique. On comptabilise l'echec plutot
                # que de le passer sous silence, pour que l'utilisateur
                # comprenne un taux de reduction plus faible que prevu.
                continue
        page.compress_content_streams()
    _write_output(writer, output_path)
    compressed_size = Path(output_path).stat().st_size
    return CompressionResult(original_size, compressed_size, images_recompressed, images_total)


# -- conversion image <-> PDF ---------------------------------------------------

# Au-dela de ce nombre de pixels, le bitmap resultant risque a lui seul
# d'epuiser plusieurs Go de RAM avant meme d'avoir fini d'etre alloue. Un
# /MediaBox demesure (parfaitement valide au sens de la specification PDF,
# et ne pesant que quelques centaines d'octets sur disque) combine a un DPI
# eleve peut demander un bitmap non compresse de plusieurs Go - vecteur de
# deni de service trouve a l'audit, qui ne necessite meme pas d'image
# embarquee (juste une page qui se declare tres grande). 100 megapixels
# correspond a une image d'environ 10000x10000 px (ex : une page A4 a plus
# de 1700 DPI, ou une page de 66x66 pouces a 150 DPI) - tres largement
# au-dela de tout usage documentaire raisonnable.
MAX_RENDER_PIXELS = 100_000_000


def pdf_to_images(
    input_path: Path, output_dir: Path, base_name: str, dpi: int = 150, fmt: str = "png",
    quality: int = 90, progress_callback: Optional[callable] = None, password: str = "",
) -> list:
    """password : mot de passe du PDF source, s'il est protege - pdfium (a la
    difference de pypdf/_open_reader utilise par le reste du module) leve une
    PdfiumError technique brute si le mot de passe manque ou est incorrect ;
    on la convertit ici en PdfOpsError avec un message clair (bug trouve a
    l'audit : la conversion echouait avec une erreur technique non traduite,
    sans indiquer qu'un mot de passe etait necessaire).

    quality : qualite JPEG (1 = tres compresse, 95 = quasi sans perte),
    utilisee uniquement quand `fmt` est "jpg"/"jpeg" (ignoree par Pillow pour
    le PNG, format sans perte). progress_callback(done, total), si fourni,
    est appele apres chaque page rendue - utilise par le GUI pour afficher
    une progression sur les conversions haute resolution/nombreuses pages.

    Relancer une conversion vers le meme dossier (meme PDF reconverti, ou
    deux PDF de meme nom depuis des dossiers differents) produirait sinon les
    memes noms de fichiers de sortie que la fois precedente, la nouvelle
    execution ecrasant silencieusement les images d'avant - bug trouve a
    l'audit, corrige avec le meme mecanisme de contournement de collision
    (verifier `.exists()` sur le disque) que celui deja en place dans
    extract_attachments/extract_embedded_images/split_pdf_by_ranges."""
    import pypdfium2 as pdfium

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scale = dpi / 72.0
    try:
        pdf = pdfium.PdfDocument(str(input_path), password=password or None)
    except pdfium.PdfiumError as exc:
        if "password" in str(exc).lower():
            if password:
                raise PdfOpsError(f"Mot de passe incorrect pour {Path(input_path).name}.") from exc
            raise PdfOpsError(f"Le fichier {Path(input_path).name} est protege par un mot de passe.") from exc
        raise
    output_paths = []
    used_paths = set()
    try:
        total = len(pdf)
        for index, page in enumerate(pdf, start=1):
            # Verifie la taille attendue du bitmap AVANT de tenter l'allocation
            # (page.render) : une page a /MediaBox demesure combinee a un DPI
            # eleve peut demander plusieurs Go de RAM pour une seule image, y
            # compris sur un fichier PDF de quelques centaines d'octets sur
            # disque (voir MAX_RENDER_PIXELS ci-dessus).
            width_pt, height_pt = page.get_size()
            expected_pixels = (width_pt * scale) * (height_pt * scale)
            if expected_pixels > MAX_RENDER_PIXELS:
                page.close()
                raise PdfOpsError(
                    f"La page {index} de {Path(input_path).name} produirait une image "
                    f"d'environ {expected_pixels / 1_000_000:.0f} megapixels a {dpi} DPI "
                    f"(page de {width_pt / 72:.0f}x{height_pt / 72:.0f} pouces), ce qui "
                    f"depasse la limite de {MAX_RENDER_PIXELS // 1_000_000} megapixels et "
                    "risquerait d'epuiser la memoire disponible. Reduisez la resolution (DPI) "
                    "ou verifiez que ce PDF n'est pas corrompu ou malveillant."
                )
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            bitmap.close()
            page.close()
            stem = f"{base_name}_p{index:03d}"
            output_path = output_dir / f"{stem}.{fmt}"
            counter = 1
            while output_path.exists() or output_path in used_paths:
                output_path = output_dir / f"{stem} ({counter}).{fmt}"
                counter += 1
            used_paths.add(output_path)
            save_kwargs = {}
            if fmt.lower() in ("jpg", "jpeg"):
                save_kwargs["quality"] = quality
                if image.mode not in ("RGB", "L"):
                    image = image.convert("RGB")
            image.save(output_path, **save_kwargs)
            output_paths.append(output_path)
            if progress_callback:
                progress_callback(index, total)
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


# -- proprietes / metadonnees -------------------------------------------------------

_METADATA_FIELDS = {
    "title": "/Title",
    "author": "/Author",
    "subject": "/Subject",
    "keywords": "/Keywords",
}


def read_metadata(input_path: Path, password: Optional[str] = None) -> dict:
    """Lit les metadonnees courantes du PDF (titre, auteur, sujet,
    mots-cles) - une chaine vide si le champ est absent, jamais None, pour
    que l'appelant puisse toujours pre-remplir un formulaire directement."""
    reader = _open_reader(input_path, password=password)
    meta = reader.metadata or {}
    return {field: (meta.get(key) or "") for field, key in _METADATA_FIELDS.items()}


def set_metadata(
    input_path: Path, output_path: Path, metadata: dict, password: Optional[str] = None,
) -> None:
    """Remplace les metadonnees du PDF par exactement celles fournies dans
    `metadata` (memes cles que read_metadata) - un dict vide (ou avec
    uniquement des valeurs vides) purge completement le document. Comme
    set_password/remove_password, reconstruit un PdfWriter neuf avec
    uniquement les pages (jamais reader.metadata, jamais le /Root complet
    du reader) : ni le dictionnaire d'informations (docinfo) ni le flux XMP
    eventuel de la source ne sont jamais copies vers la sortie, purge ou
    non - seul un appel explicite a add_metadata() en reintroduit. Le
    /Producer devient "pypdf" a l'ecriture (comportement de la
    bibliotheque) : la purge ne peut pas l'effacer, seulement le remplacer.

    Si la source etait protegee par mot de passe, la sortie l'est A
    NOUVEAU avec ce meme mot de passe : sans cela, editer/purger les
    metadonnees d'un PDF confidentiel produirait silencieusement une copie
    NON protegee (regression de confidentialite trouvee a l'audit - le
    motif "nouveau writer, jamais reader.metadata" qui protege bien contre
    la fuite de metadonnees fait aussi disparaitre le chiffrement,
    puisqu'un writer neuf n'est jamais chiffre par defaut)."""
    reader = _open_reader(input_path, password=password)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    non_empty = {
        key: str(metadata[field]) for field, key in _METADATA_FIELDS.items()
        if metadata.get(field)
    }
    if non_empty:
        writer.add_metadata(non_empty)
    if reader.is_encrypted:
        writer.encrypt(password, password)
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
    writer.encrypt(user_password, owner_password or user_password, algorithm="AES-256")
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


def extract_attachments(input_path: Path, output_dir: Path, password: Optional[str] = None) -> list:
    """Extrait les pieces jointes embarquees dans le PDF (ex: XML Factur-X/
    ZUGFeRD, images, autres PDF) - invisibles dans un lecteur basique.
    Les noms de pieces jointes proviennent du document lui-meme, une
    donnee non fiable : Path(name).name neutralise toute tentative de
    traversee de repertoire ou de separateur (ex: "..\\evil.txt" devient
    "evil.txt", toujours ecrit A L'INTERIEUR de output_dir), et un nom vide
    apres nettoyage retombe sur un nom generique plutot que d'echouer.

    Une piece jointe individuelle corrompue/malformee (reference indirecte
    invalide dans le PDF, flux tronque...) est ignoree plutot que de faire
    echouer toute l'extraction - meme philosophie que extract_embedded_images.
    C'est indispensable ici, pas juste une precaution : reader.attachments
    est un dict paresseux (pypdf LazyDict) dont l'iteration sur `.keys()`
    ne decode rien, mais chaque acces reader.attachments[nom] declenche
    reellement la resolution du contenu - une piece jointe malformee y leve
    une exception (AttributeError constate a l'audit sur une reference
    /EF indirecte cassee, mais d'autres types sont plausibles vu la
    variete de structures PDF malformees possibles)."""
    reader = _open_reader(input_path, password=password)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_paths = []
    used_paths = set()
    # Iterer sur .keys() (jamais .items()) : .items() decoderait le
    # contenu DE CHAQUE piece jointe pendant l'iteration elle-meme, avant
    # meme d'atteindre le try/except ci-dessous - abandonnant alors toute
    # l'extraction des la premiere piece jointe corrompue rencontree, y
    # compris celles qui suivent et sont parfaitement saines (meme classe
    # de bug que le `try` place une ligne trop tard, deja corrige une fois
    # dans extract_embedded_images).
    for raw_name in reader.attachments.keys():
        try:
            contents_list = reader.attachments[raw_name]
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            continue
        safe_name = Path(str(raw_name)).name.strip() or "piece_jointe"
        stem = Path(safe_name).stem or "piece_jointe"
        suffix = Path(safe_name).suffix
        for contents in contents_list:
            try:
                output_path = output_dir / safe_name
                counter = 1
                while output_path.exists() or output_path in used_paths:
                    output_path = output_dir / f"{stem} ({counter}){suffix}"
                    counter += 1
                used_paths.add(output_path)
                output_path.write_bytes(contents)
                output_paths.append(output_path)
            except (OSError, ValueError, TypeError):
                continue
    return output_paths


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
    # Deux appels vers le meme dossier (meme PDF extrait deux fois, ou deux
    # PDF de meme nom depuis des dossiers differents) produiraient sinon des
    # noms de fichier identiques et le second ecraserait silencieusement le
    # premier - bug trouve a l'audit (contrairement a _resolve_batch_outputs
    # cote GUI, qui protege deja Filigrane/Protection/Numeroter contre
    # exactement ce cas, jamais applique ici).
    used_paths = set()
    for page_index, page in enumerate(reader.pages, start=1):
        # Indexation manuelle (pas `for image_file in page.images`) : accéder
        # a `page.images[i]` est ce qui declenche reellement le decodage de
        # l'image (via pypdf), et c'est exactement ce qui peut lever une
        # exception sur un flux corrompu. Boucler directement sur l'iterateur
        # de page.images ferait lever cette exception PENDANT l'evaluation de
        # la boucle for elle-meme, avant meme d'atteindre le `try:` ci-dessous
        # - abandonnant alors l'extraction de TOUTE la page (voire du
        # document, l'exception remontant hors de la fonction), au lieu de
        # sauter uniquement l'image fautive (bug trouve a l'audit : le
        # `try` commencait une ligne trop tard pour proteger contre ca).
        image_count = len(page.images)
        for image_index in range(1, image_count + 1):
            try:
                image_file = page.images[image_index - 1]
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
                stem = f"{base_name}_p{page_index:03d}_img{image_index:02d}"
                output_path = output_dir / f"{stem}.{ext}"
                counter = 1
                while output_path.exists() or output_path in used_paths:
                    output_path = output_dir / f"{stem} ({counter}).{ext}"
                    counter += 1
                used_paths.add(output_path)
                save_image.save(output_path)
                output_paths.append(output_path)
            except (OSError, ValueError, KeyError, PyPdfError):
                # PyPdfError couvre notamment LimitReachedError, la
                # protection anti-bombe-de-decompression native de pypdf
                # (image declarant des dimensions demesurees pour un flux
                # minuscule) - absente jusqu'a present de cet except, elle
                # remontait telle quelle et faisait echouer toute
                # l'extraction du document au lieu de sauter uniquement
                # l'image fautive (bug trouve a l'audit).
                continue
    return output_paths
