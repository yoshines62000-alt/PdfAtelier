"""Tests pour pdf_ops.py : chaque operation est verifiee sur de vrais
fichiers PDF/PNG generes sur disque (pas de mocks) - fusion, division,
gestion des pages, compression, conversion, filigrane, mot de passe,
extraction de texte."""

import os
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, IndirectObject, NameObject, NumberObject, StreamObject
from PIL import Image
from reportlab.pdfgen import canvas

import pdf_ops as ops


def _add_dct_image_xobject(writer: PdfWriter, page, name: str, jpeg_bytes: bytes, width: int, height: int):
    """Ajoute un XObject image brut (flux DCTDecode) a `page` - utilise pour
    construire une image sciemment corrompue (flux illisible) et une image
    valide (vrais octets JPEG) via le meme mecanisme bas niveau, sans
    dependre d'une API haut niveau de pypdf pour l'insertion d'images."""
    image_obj = StreamObject()
    image_obj.set_data(jpeg_bytes)
    image_obj[NameObject("/Type")] = NameObject("/XObject")
    image_obj[NameObject("/Subtype")] = NameObject("/Image")
    image_obj[NameObject("/Width")] = NumberObject(width)
    image_obj[NameObject("/Height")] = NumberObject(height)
    image_obj[NameObject("/ColorSpace")] = NameObject("/DeviceRGB")
    image_obj[NameObject("/BitsPerComponent")] = NumberObject(8)
    image_obj[NameObject("/Filter")] = NameObject("/DCTDecode")
    ref = writer._add_object(image_obj)
    if "/Resources" not in page:
        page[NameObject("/Resources")] = DictionaryObject()
    resources = page["/Resources"].get_object()
    if "/XObject" not in resources:
        resources[NameObject("/XObject")] = DictionaryObject()
    resources["/XObject"].get_object()[NameObject(name)] = ref


def make_pdf_with_corrupt_and_valid_images(path: Path) -> Path:
    """Cree un PDF de 2 pages : la page 1 contient une image XObject
    deliberement corrompue (flux DCTDecode illisible), la page 2 une vraie
    image JPEG valide - pour verifier qu'une image corrompue n'empeche
    jamais l'extraction des images valides qui la suivent."""
    import io

    writer = PdfWriter()

    corrupt_page = writer.add_blank_page(width=200, height=200)
    _add_dct_image_xobject(
        writer, corrupt_page, "/CorruptImg",
        b"pas un vrai flux JPEG, juste des octets quelconques 1234567890", 10, 10,
    )

    valid_page = writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color=(200, 20, 20)).save(buffer, format="JPEG")
    _add_dct_image_xobject(writer, valid_page, "/ValidImg", buffer.getvalue(), 10, 10)

    with open(path, "wb") as f:
        writer.write(f)
    return path


def _add_bomb_image_xobject(writer: PdfWriter, page, name: str):
    """Ajoute un XObject image "bombe de decompression" : dimensions
    declarees enormes (20000x20000, DeviceGray 8 bits) pour un flux
    /FlateDecode minuscule sur disque. Reproduit le mecanisme reel de
    protection anti-bombe de pypdf (pypdf.errors.LimitReachedError, levee
    car le bitmap declare depasse FLATE_MAX_BUFFER_SIZE), utilise a l'audit
    pour prouver qu'une seule image de ce type faisait jusque-la avorter
    toute la compression/extraction du document."""
    image_obj = StreamObject()
    image_obj.set_data(zlib.compress(b"\x00" * 100))
    image_obj[NameObject("/Type")] = NameObject("/XObject")
    image_obj[NameObject("/Subtype")] = NameObject("/Image")
    image_obj[NameObject("/Width")] = NumberObject(20000)
    image_obj[NameObject("/Height")] = NumberObject(20000)
    image_obj[NameObject("/ColorSpace")] = NameObject("/DeviceGray")
    image_obj[NameObject("/BitsPerComponent")] = NumberObject(8)
    image_obj[NameObject("/Filter")] = NameObject("/FlateDecode")
    ref = writer._add_object(image_obj)
    if "/Resources" not in page:
        page[NameObject("/Resources")] = DictionaryObject()
    resources = page["/Resources"].get_object()
    if "/XObject" not in resources:
        resources[NameObject("/XObject")] = DictionaryObject()
    resources["/XObject"].get_object()[NameObject(name)] = ref


def make_pdf_with_bomb_and_valid_images(path: Path) -> Path:
    """PDF de 2 pages : la page 1 contient une image "bombe de
    decompression" (voir _add_bomb_image_xobject), la page 2 une vraie image
    JPEG valide - pour verifier qu'une image-bombe n'empeche jamais de
    traiter les images valides qui la suivent."""
    import io

    writer = PdfWriter()

    bomb_page = writer.add_blank_page(width=200, height=200)
    _add_bomb_image_xobject(writer, bomb_page, "/BombImg")

    valid_page = writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), color=(200, 20, 20)).save(buffer, format="JPEG")
    _add_dct_image_xobject(writer, valid_page, "/ValidImg", buffer.getvalue(), 10, 10)

    with open(path, "wb") as f:
        writer.write(f)
    return path


def make_pdf(path: Path, num_pages: int = 1, labels=None) -> Path:
    """Cree un PDF de test avec un texte distinct par page, pour verifier
    l'ordre/le contenu apres une operation."""
    c = canvas.Canvas(str(path), pagesize=(200, 200))
    for i in range(num_pages):
        label = labels[i] if labels else f"Page {i + 1}"
        c.drawString(20, 100, label)
        c.showPage()
    c.save()
    return path


def make_pdf_with_image(path: Path, image_path: Path) -> Path:
    c = canvas.Canvas(str(path), pagesize=(400, 400))
    c.drawImage(str(image_path), 0, 0, width=400, height=400)
    c.showPage()
    c.save()
    return path


def make_pdf_with_outline_and_form_field(path: Path, num_pages: int = 2) -> Path:
    """PDF avec un signet (pointant sur la page 1) et un champ de
    formulaire texte /AcroForm rempli, porte par un widget /Annots sur la
    page 1 - reproduit les deux structures que add_page() perd
    silencieusement (regression trouvee a l'audit, points 7/8), pour
    verifier qu'elles survivent bien a merge/split/proprietes/protection.

    Construction bas niveau (pypdf.generic) : PdfWriter n'expose pas d'API
    haut niveau simple pour creer un champ de formulaire dans cette version
    (meme limitation deja notee dans le rapport d'audit)."""
    from pypdf.generic import ArrayObject, BooleanObject, RectangleObject, TextStringObject

    writer = PdfWriter()
    for _ in range(num_pages):
        writer.add_blank_page(width=200, height=200)
    writer.add_outline_item("Mon signet", 0)

    page = writer.pages[0]
    field = DictionaryObject()
    field.update({
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject("champ_texte"),
        NameObject("/V"): TextStringObject("valeur remplie"),
        NameObject("/Rect"): RectangleObject([20, 20, 180, 40]),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/P"): page.indirect_reference,
        NameObject("/F"): NumberObject(4),
    })
    field_ref = writer._add_object(field)
    if "/Annots" in page:
        page["/Annots"].append(field_ref)
    else:
        page[NameObject("/Annots")] = ArrayObject([field_ref])

    acroform = DictionaryObject()
    acroform.update({
        NameObject("/Fields"): ArrayObject([field_ref]),
        NameObject("/NeedAppearances"): BooleanObject(True),
    })
    writer._root_object[NameObject("/AcroForm")] = writer._add_object(acroform)

    with open(path, "wb") as f:
        writer.write(f)
    return path


class PdfOpsTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_get_page_count(self):
        pdf = make_pdf(self.tmp / "a.pdf", num_pages=3)
        self.assertEqual(ops.get_page_count(pdf), 3)

    def test_get_page_count_on_encrypted_pdf_requires_password(self):
        pdf = make_pdf(self.tmp / "a.pdf", num_pages=2)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")
        with self.assertRaises(ops.PdfOpsError):
            ops.get_page_count(protected)
        self.assertEqual(ops.get_page_count(protected, password="secret"), 2)

    def test_owner_only_protected_pdf_opens_without_a_password(self):
        # Regression trouvee a l'audit : un PDF protege uniquement par un
        # mot de passe "proprietaire" (permissions restreintes copier/
        # imprimer...) mais sans mot de passe d'ouverture reel (mot de passe
        # utilisateur vide) - categorie tres courante (exports
        # administratifs/bancaires) - se voyait pourtant systematiquement
        # reclamer un mot de passe qui n'existe pas, bloquant tout l'outil
        # sur ce fichier alors qu'il s'ouvre nativement partout ailleurs
        # (Adobe Acrobat, navigateurs...).
        pdf = make_pdf(self.tmp / "a.pdf", num_pages=2, labels=["Un", "Deux"])
        writer = PdfWriter()
        writer.append(PdfReader(str(pdf)))
        writer.encrypt(user_password="", owner_password="secretowner", algorithm="AES-256")
        owner_only = self.tmp / "owner_only.pdf"
        with open(owner_only, "wb") as f:
            writer.write(f)

        reader = PdfReader(str(owner_only))
        self.assertTrue(reader.is_encrypted)

        # Aucun mot de passe fourni : doit s'ouvrir directement, sans lever
        # PdfOpsError ni exiger quoi que ce soit de l'utilisateur.
        self.assertEqual(ops.get_page_count(owner_only), 2)
        texts = ops.extract_text(owner_only)
        self.assertIn("Un", texts[0])
        self.assertIn("Deux", texts[1])

    def test_owner_only_protected_pdf_still_rejects_a_genuinely_wrong_password(self):
        # L'essai automatique du mot de passe vide ne doit pas masquer un
        # vrai mot de passe utilisateur incorrect sur un PDF qui, lui, en
        # possede reellement un.
        pdf = make_pdf(self.tmp / "a.pdf", num_pages=1)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")
        with self.assertRaises(ops.PdfOpsError):
            ops.get_page_count(protected, password="wrong")

    def test_saving_output_over_the_source_file_does_not_corrupt_it(self):
        # Scenario reel : l'utilisateur choisit d'ecraser le fichier
        # source lui-meme (ex : "pivoter et enregistrer sous le meme nom").
        # L'ecriture doit passer par un fichier temporaire puis un
        # remplacement atomique, sinon la source est tronquee avant que
        # l'ecriture ne soit terminee.
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=2, labels=["Un", "Deux"])
        ops.reorder_and_filter_pages(pdf, pdf, page_order=[2, 1])
        self.assertEqual(ops.get_page_count(pdf), 2)
        texts = ops.extract_text(pdf)
        self.assertIn("Deux", texts[0])
        self.assertIn("Un", texts[1])

    def test_merge_pdfs_preserves_page_order_and_content(self):
        pdf_a = make_pdf(self.tmp / "a.pdf", num_pages=1, labels=["A1"])
        pdf_b = make_pdf(self.tmp / "b.pdf", num_pages=2, labels=["B1", "B2"])
        output = self.tmp / "merged.pdf"
        ops.merge_pdfs([pdf_a, pdf_b], output)

        self.assertEqual(ops.get_page_count(output), 3)
        texts = ops.extract_text(output)
        self.assertIn("A1", texts[0])
        self.assertIn("B1", texts[1])
        self.assertIn("B2", texts[2])

    def test_merge_pdfs_rejects_empty_list(self):
        with self.assertRaises(ops.PdfOpsError):
            ops.merge_pdfs([], self.tmp / "out.pdf")

    def test_split_pdf_by_ranges(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=5, labels=[f"P{i}" for i in range(1, 6)])
        out_dir = self.tmp / "split"
        paths = ops.split_pdf_by_ranges(pdf, [(1, 2), (3, 5)], out_dir, "doc")

        self.assertEqual(len(paths), 2)
        self.assertEqual(ops.get_page_count(paths[0]), 2)
        self.assertEqual(ops.get_page_count(paths[1]), 3)
        self.assertIn("P1", ops.extract_text(paths[0])[0])
        self.assertIn("P3", ops.extract_text(paths[1])[0])

    def test_split_pdf_by_ranges_rejects_out_of_bounds_range(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=2)
        with self.assertRaises(ops.PdfOpsError):
            ops.split_pdf_by_ranges(pdf, [(1, 5)], self.tmp / "split", "doc")

    def test_split_pdf_every_n_pages(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=5)
        paths = ops.split_pdf_every_n_pages(pdf, 2, self.tmp / "split", "doc")
        page_counts = [ops.get_page_count(p) for p in paths]
        self.assertEqual(page_counts, [2, 2, 1])

    def test_split_pdf_by_ranges_avoids_overwriting_pre_existing_files_on_disk(self):
        # Regression trouvee a l'audit : relancer une division vers le meme
        # dossier (meme fichier redivise, ou deux fichiers de meme nom
        # depuis des dossiers differents) generait exactement les memes noms
        # de sortie que la fois precedente, la seconde execution ecrasant
        # silencieusement les resultats de la premiere.
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=5, labels=[f"P{i}" for i in range(1, 6)])
        out_dir = self.tmp / "split"
        first = ops.split_pdf_by_ranges(pdf, [(1, 2), (3, 5)], out_dir, "doc")
        second = ops.split_pdf_by_ranges(pdf, [(1, 2), (3, 5)], out_dir, "doc")

        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertEqual(len(set(first) | set(second)), 4)  # 4 fichiers distincts, aucune collision
        for p in first + second:
            self.assertTrue(p.exists())
        # Le contenu de la premiere execution n'a pas ete ecrase.
        self.assertIn("P1", ops.extract_text(first[0])[0])

    def test_split_pdf_by_ranges_avoids_overwriting_a_file_not_produced_by_a_prior_split(self):
        # Le fichier deja present sur disque n'a meme pas besoin de venir
        # d'une precedente division : n'importe quel fichier de meme nom
        # (deplace la, cree manuellement...) doit etre respecte.
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=3)
        out_dir = self.tmp / "split"
        out_dir.mkdir()
        pre_existing = out_dir / "doc_01_p1-2.pdf"
        pre_existing.write_bytes(b"contenu preexistant, ne doit jamais etre efface")

        paths = ops.split_pdf_by_ranges(pdf, [(1, 2)], out_dir, "doc")
        self.assertEqual(len(paths), 1)
        self.assertNotEqual(paths[0], pre_existing)
        self.assertEqual(pre_existing.read_bytes(), b"contenu preexistant, ne doit jamais etre efface")

    def test_reorder_and_filter_pages_reverses_and_drops_a_page(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=3, labels=["P1", "P2", "P3"])
        output = self.tmp / "reordered.pdf"
        ops.reorder_and_filter_pages(pdf, output, page_order=[3, 1])  # P2 supprimee, ordre inverse

        self.assertEqual(ops.get_page_count(output), 2)
        texts = ops.extract_text(output)
        self.assertIn("P3", texts[0])
        self.assertIn("P1", texts[1])

    def test_reorder_and_filter_pages_applies_rotation(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        output = self.tmp / "rotated.pdf"
        ops.reorder_and_filter_pages(pdf, output, page_order=[1], rotations={1: 90})
        reader = PdfReader(str(output))
        self.assertEqual(reader.pages[0].rotation, 90)

    def test_reorder_and_filter_pages_rejects_invalid_page_number(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        with self.assertRaises(ops.PdfOpsError):
            ops.reorder_and_filter_pages(pdf, self.tmp / "out.pdf", page_order=[7])

    def test_reorder_and_filter_pages_on_encrypted_pdf_with_password(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=2, labels=["Un", "Deux"])
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")
        output = self.tmp / "out.pdf"
        ops.reorder_and_filter_pages(protected, output, page_order=[2], password="secret")
        self.assertEqual(ops.get_page_count(output), 1)
        self.assertIn("Deux", ops.extract_text(output)[0])

    def test_compress_pdf_reduces_size_with_embedded_image(self):
        # Une image bruitee (haute entropie, comme une vraie photo) plutot
        # qu'une couleur unie : une couleur unie se compresse deja quasi
        # parfaitement sans perte, ce qui masquerait l'effet du
        # sous-echantillonnage + de la recompression JPEG teste ici.
        import os
        image_path = self.tmp / "photo.png"
        size = (1200, 1200)
        noisy = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
        noisy.save(image_path)
        pdf = make_pdf_with_image(self.tmp / "with_image.pdf", image_path)
        output = self.tmp / "compressed.pdf"

        result = ops.compress_pdf(pdf, output, image_quality=40, max_dimension=300)

        self.assertTrue(output.exists())
        self.assertEqual(ops.get_page_count(output), 1)
        self.assertGreater(result.original_size, 0)
        self.assertGreater(result.compressed_size, 0)
        # Une image 1200x1200 ramenee a 300x300 en JPEG qualite 40 doit
        # produire un fichier nettement plus petit.
        self.assertLess(result.compressed_size, result.original_size)
        self.assertGreater(result.ratio_percent, 0)
        self.assertEqual(result.images_total, 1)
        self.assertEqual(result.images_recompressed, 1)
        self.assertEqual(result.images_failed, 0)

    def test_compress_pdf_counts_images_that_fail_to_recompress(self):
        # Simule un echec de remplacement d'image (format non supporte,
        # image corrompue...) pour verifier que compress_pdf continue sans
        # planter ET comptabilise l'echec au lieu de l'avaler silencieusement.
        import os
        from unittest.mock import patch
        image_path = self.tmp / "photo2.png"
        size = (400, 400)
        Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(image_path)
        pdf = make_pdf_with_image(self.tmp / "with_image2.pdf", image_path)
        output = self.tmp / "compressed2.pdf"

        from pypdf._page import ImageFile
        with patch.object(ImageFile, "replace", side_effect=RuntimeError("format non supporte")):
            result = ops.compress_pdf(pdf, output, image_quality=40, max_dimension=300)

        self.assertTrue(output.exists())
        self.assertEqual(result.images_total, 1)
        self.assertEqual(result.images_recompressed, 0)
        self.assertEqual(result.images_failed, 1)

    def test_compress_pdf_skips_a_decompression_bomb_image_without_aborting_the_file(self):
        # Regression trouvee a l'audit : compress_pdf bouclait directement
        # `for img in page.images`, ce qui declenche le decodage AVANT le
        # try/except qui suit - une seule image "bombe" (dimensions
        # declarees enormes, flux minuscule) faisait donc lever
        # pypdf.errors.LimitReachedError hors de toute protection,
        # avortant la compression de TOUT le fichier au lieu de continuer
        # sur les images/pages saines suivantes.
        pdf = make_pdf_with_bomb_and_valid_images(self.tmp / "bomb.pdf")
        output = self.tmp / "compressed.pdf"

        result = ops.compress_pdf(pdf, output, image_quality=40, max_dimension=300)

        self.assertTrue(output.exists())
        self.assertEqual(ops.get_page_count(output), 2)  # les 2 pages sont bien presentes
        self.assertEqual(result.images_total, 2)  # bombe + image valide, toutes deux comptees
        self.assertEqual(result.images_recompressed, 1)  # seule l'image valide a pu etre recompressee
        self.assertEqual(result.images_failed, 1)

    def test_compression_result_images_failed_counts_unrecompressed_images(self):
        result = ops.CompressionResult(original_size=100, compressed_size=90, images_recompressed=2, images_total=5)
        self.assertEqual(result.images_failed, 3)

    def test_compression_result_ratio_percent_with_zero_original_size(self):
        result = ops.CompressionResult(original_size=0, compressed_size=0)
        self.assertEqual(result.ratio_percent, 0.0)

    def test_pdf_to_images_and_back_roundtrip(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=2)
        image_paths = ops.pdf_to_images(pdf, self.tmp / "images", "doc", dpi=72, fmt="png")
        self.assertEqual(len(image_paths), 2)
        for p in image_paths:
            self.assertTrue(p.exists())

        rebuilt = self.tmp / "rebuilt.pdf"
        ops.images_to_pdf(image_paths, rebuilt)
        self.assertEqual(ops.get_page_count(rebuilt), 2)

    def test_images_to_pdf_rejects_empty_list(self):
        with self.assertRaises(ops.PdfOpsError):
            ops.images_to_pdf([], self.tmp / "out.pdf")

    def test_pdf_to_images_avoids_overwriting_pre_existing_files_on_disk(self):
        # Meme classe de regression que pour split_pdf_by_ranges : relancer
        # une conversion PDF->images vers le meme dossier ecrasait
        # silencieusement les images de la fois precedente.
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=2)
        out_dir = self.tmp / "images"
        first = ops.pdf_to_images(pdf, out_dir, "doc", dpi=72, fmt="png")
        second = ops.pdf_to_images(pdf, out_dir, "doc", dpi=72, fmt="png")

        self.assertEqual(len(first), 2)
        self.assertEqual(len(second), 2)
        self.assertEqual(len(set(first) | set(second)), 4)
        for p in first + second:
            self.assertTrue(p.exists())

    def test_pdf_to_images_respects_jpeg_quality(self):
        # Une image bruitee (haute entropie) pour que la qualite JPEG ait un
        # effet mesurable sur la taille du fichier - contrairement a une
        # page vide qui se compresserait deja au minimum quelle que soit la
        # qualite demandee.
        import os
        image_path = self.tmp / "photo.png"
        size = (300, 300)
        Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(image_path)
        pdf = make_pdf_with_image(self.tmp / "with_image.pdf", image_path)

        low_quality = ops.pdf_to_images(pdf, self.tmp / "low", "doc", dpi=100, fmt="jpg", quality=5)
        high_quality = ops.pdf_to_images(pdf, self.tmp / "high", "doc", dpi=100, fmt="jpg", quality=95)

        self.assertLess(low_quality[0].stat().st_size, high_quality[0].stat().st_size)

    def test_pdf_to_images_reports_progress_per_page(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=3)
        calls = []
        ops.pdf_to_images(
            pdf, self.tmp / "images", "doc", dpi=72, fmt="png",
            progress_callback=lambda done, total: calls.append((done, total)),
        )
        self.assertEqual(calls, [(1, 3), (2, 3), (3, 3)])

    def test_pdf_to_images_works_on_a_password_protected_pdf_with_correct_password(self):
        # Bug trouve a l'audit : pdf_to_images ouvrait le PDF via pdfium sans
        # jamais transmettre de mot de passe, faisant echouer toute
        # conversion d'un PDF protege meme avec le bon mot de passe.
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=2)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")

        image_paths = ops.pdf_to_images(protected, self.tmp / "images", "doc", dpi=72, fmt="png", password="secret")
        self.assertEqual(len(image_paths), 2)
        for p in image_paths:
            self.assertTrue(p.exists())

    def test_pdf_to_images_raises_clear_error_without_password(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")

        with self.assertRaises(ops.PdfOpsError):
            ops.pdf_to_images(protected, self.tmp / "images", "doc", dpi=72, fmt="png")

    def test_pdf_to_images_raises_clear_error_with_wrong_password(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")

        with self.assertRaises(ops.PdfOpsError):
            ops.pdf_to_images(protected, self.tmp / "images", "doc", dpi=72, fmt="png", password="wrong")

    def test_pdf_to_images_refuses_an_oversized_mediabox_before_rendering(self):
        # Regression trouvee a l'audit : pdf_to_images calculait le scale
        # puis appelait directement page.render() sans jamais verifier au
        # prealable la taille du bitmap resultant. Un /MediaBox demesure
        # (parfaitement valide au sens de la specification PDF, ne pesant
        # que quelques centaines d'octets sur disque) combine a un DPI
        # meme modere peut demander plusieurs Go de RAM pour une seule
        # image - vecteur de deni de service qui ne necessite aucune image
        # embarquee, juste une page qui se declare tres grande.
        writer = PdfWriter()
        writer.add_blank_page(width=100000, height=100000)  # ~1389x1389 pouces
        huge = self.tmp / "huge_mediabox.pdf"
        with open(huge, "wb") as f:
            writer.write(f)
        out_dir = self.tmp / "images"

        with self.assertRaises(ops.PdfOpsError):
            ops.pdf_to_images(huge, out_dir, "huge", dpi=72, fmt="png")
        # Le refus intervient avant tout rendu : aucune image n'est produite.
        self.assertEqual(list(out_dir.iterdir()), [])

    def test_pdf_to_images_accepts_a_reasonable_mediabox_and_dpi(self):
        # Garde-fou de non-regression : une page de taille standard a un DPI
        # eleve mais raisonnable ne doit pas etre refusee par la nouvelle
        # limite de pixels.
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)  # page 200x200 points (~2.8x2.8 pouces)
        image_paths = ops.pdf_to_images(pdf, self.tmp / "images", "doc", dpi=300, fmt="png")
        self.assertEqual(len(image_paths), 1)
        self.assertTrue(image_paths[0].exists())

    def test_extract_embedded_images_recovers_the_embedded_photo(self):
        source_image = self.tmp / "photo.png"
        Image.new("RGB", (40, 30), color=(10, 20, 30)).save(source_image)
        pdf = make_pdf_with_image(self.tmp / "doc.pdf", source_image)

        extracted = ops.extract_embedded_images(pdf, self.tmp / "out", "doc")
        self.assertEqual(len(extracted), 1)
        self.assertTrue(extracted[0].exists())
        with Image.open(extracted[0]) as img:
            self.assertEqual(img.size, (40, 30))

    def test_extract_embedded_images_on_pdf_without_images_returns_empty_list(self):
        pdf = make_pdf(self.tmp / "text_only.pdf", num_pages=1)
        extracted = ops.extract_embedded_images(pdf, self.tmp / "out", "text_only")
        self.assertEqual(extracted, [])

    def test_extract_embedded_images_names_files_per_page(self):
        source_image = self.tmp / "photo.png"
        Image.new("RGB", (10, 10), color=(1, 2, 3)).save(source_image)
        pdf = make_pdf_with_image(self.tmp / "doc.pdf", source_image)

        extracted = ops.extract_embedded_images(pdf, self.tmp / "out", "doc")
        self.assertIn("doc_p001_img01", extracted[0].name)

    def test_extract_embedded_images_skips_a_corrupt_image_without_aborting_the_rest(self):
        # Regression trouvee a l'audit : une image corrompue faisait
        # jusque-la echouer l'extraction de TOUTE l'image en cours
        # d'iteration (l'exception de decodage se levait pendant l'appel a
        # page.images lui-meme, avant meme le try:), abandonnant aussi les
        # images valides des pages suivantes.
        pdf = make_pdf_with_corrupt_and_valid_images(self.tmp / "doc.pdf")
        extracted = ops.extract_embedded_images(pdf, self.tmp / "out", "doc")
        self.assertEqual(len(extracted), 1)
        self.assertIn("p002", extracted[0].name)  # l'image valide de la page 2 est bien recuperee
        with Image.open(extracted[0]) as img:
            self.assertEqual(img.size, (10, 10))

    def test_extract_embedded_images_skips_a_decompression_bomb_image_without_aborting_the_rest(self):
        # Regression trouvee a l'audit : contrairement au cas "image
        # corrompue" ci-dessus, l'indexation manuelle etait deja correcte
        # ici, mais l'except (OSError, ValueError, KeyError) ne couvrait pas
        # pypdf.errors.LimitReachedError (la protection anti-bombe de
        # decompression native de pypdf, qui herite de PyPdfError -> Exception,
        # pas de ces trois-la) - une image "bombe" faisait donc quand meme
        # avorter toute l'extraction du document.
        pdf = make_pdf_with_bomb_and_valid_images(self.tmp / "bomb.pdf")
        extracted = ops.extract_embedded_images(pdf, self.tmp / "out", "doc")
        self.assertEqual(len(extracted), 1)
        self.assertIn("p002", extracted[0].name)  # l'image valide de la page 2 est bien recuperee
        with Image.open(extracted[0]) as img:
            self.assertEqual(img.size, (10, 10))

    def test_extract_embedded_images_avoids_overwriting_a_pre_existing_file(self):
        # Regression trouvee a l'audit : extraire deux fois vers le meme
        # dossier (ou deux PDF de meme nom depuis des dossiers differents)
        # produisait le meme nom de fichier de sortie, la seconde
        # extraction ecrasant silencieusement la premiere.
        source_image = self.tmp / "photo.png"
        Image.new("RGB", (10, 10), color=(1, 2, 3)).save(source_image)
        pdf = make_pdf_with_image(self.tmp / "doc.pdf", source_image)

        first = ops.extract_embedded_images(pdf, self.tmp / "out", "doc")
        second = ops.extract_embedded_images(pdf, self.tmp / "out", "doc")
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertNotEqual(first[0], second[0])
        self.assertTrue(first[0].exists())
        self.assertTrue(second[0].exists())

    def test_extract_embedded_images_from_encrypted_pdf_requires_password(self):
        source_image = self.tmp / "photo.png"
        Image.new("RGB", (10, 10), color=(1, 2, 3)).save(source_image)
        pdf = make_pdf_with_image(self.tmp / "doc.pdf", source_image)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")

        with self.assertRaises(ops.PdfOpsError):
            ops.extract_embedded_images(protected, self.tmp / "out", "doc")
        extracted = ops.extract_embedded_images(protected, self.tmp / "out", "doc", password="secret")
        self.assertEqual(len(extracted), 1)

    def test_merge_pdfs_with_one_encrypted_file_using_passwords_list(self):
        pdf_a = make_pdf(self.tmp / "a.pdf", num_pages=1, labels=["A1"])
        pdf_b = make_pdf(self.tmp / "b.pdf", num_pages=1, labels=["B1"])
        protected_b = self.tmp / "b_protected.pdf"
        ops.set_password(pdf_b, protected_b, user_password="secret")

        output = self.tmp / "merged.pdf"
        ops.merge_pdfs([pdf_a, protected_b], output, passwords=[None, "secret"])
        self.assertEqual(ops.get_page_count(output), 2)

    def test_add_text_watermark_with_non_latin1_characters_does_not_crash(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        output = self.tmp / "watermarked.pdf"
        ops.add_text_watermark(pdf, output, text="Confidentiel — “interne” ✨")
        self.assertEqual(ops.get_page_count(output), 1)

    def test_add_text_watermark_preserves_page_count_and_original_text(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=2, labels=["Contenu original", "Deuxieme page"])
        output = self.tmp / "watermarked.pdf"
        ops.add_text_watermark(pdf, output, text="CONFIDENTIEL", opacity=0.3)

        self.assertEqual(ops.get_page_count(output), 2)
        texts = ops.extract_text(output)
        self.assertIn("Contenu original", texts[0])

    def test_add_page_numbers_preserves_page_count_and_original_text(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=3, labels=["Un", "Deux", "Trois"])
        output = self.tmp / "numbered.pdf"
        ops.add_page_numbers(pdf, output)

        self.assertEqual(ops.get_page_count(output), 3)
        texts = ops.extract_text(output)
        self.assertIn("Un", texts[0])
        self.assertIn("1 / 3", texts[0])
        self.assertIn("2 / 3", texts[1])
        self.assertIn("3 / 3", texts[2])

    def test_add_page_numbers_respects_start_at(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=2)
        output = self.tmp / "numbered.pdf"
        ops.add_page_numbers(pdf, output, start_at=5)
        texts = ops.extract_text(output)
        self.assertIn("5 / 2", texts[0])
        self.assertIn("6 / 2", texts[1])

    def test_add_page_numbers_supports_custom_format(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        output = self.tmp / "numbered.pdf"
        ops.add_page_numbers(pdf, output, fmt="Page {page}")
        texts = ops.extract_text(output)
        self.assertIn("Page 1", texts[0])

    def test_add_page_numbers_rejects_invalid_position(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        with self.assertRaises(ops.PdfOpsError):
            ops.add_page_numbers(pdf, self.tmp / "out.pdf", position="milieu")

    def test_add_page_numbers_on_encrypted_pdf_requires_password(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")
        with self.assertRaises(ops.PdfOpsError):
            ops.add_page_numbers(protected, self.tmp / "out.pdf")
        ops.add_page_numbers(protected, self.tmp / "out.pdf", password="secret")
        self.assertEqual(ops.get_page_count(self.tmp / "out.pdf"), 1)

    def test_set_password_then_remove_password_roundtrip(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1, labels=["Secret"])
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="hunter2")

        reader = PdfReader(str(protected))
        self.assertTrue(reader.is_encrypted)

        with self.assertRaises(ops.PdfOpsError):
            ops.extract_text(protected)  # sans mot de passe

        unprotected = self.tmp / "unprotected.pdf"
        ops.remove_password(protected, unprotected, password="hunter2")
        reader2 = PdfReader(str(unprotected))
        self.assertFalse(reader2.is_encrypted)
        self.assertIn("Secret", ops.extract_text(unprotected)[0])

    def test_set_password_uses_aes_256_not_rc4(self):
        """L'algorithme de chiffrement doit etre AES-256 (V=5, R=6, Length=256)
        et non le RC4-128 par defaut de pypdf (V=2, R=3, Length=128) - RC4 se
        casse avec des outils grand public (John the Ripper, hashcat)."""
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1, labels=["Secret"])
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="hunter2")

        reader = PdfReader(str(protected))
        self.assertTrue(reader.is_encrypted)
        encrypt_dict = reader.trailer["/Encrypt"].get_object()
        self.assertEqual(int(encrypt_dict["/V"]), 5)
        self.assertEqual(int(encrypt_dict["/R"]), 6)
        self.assertEqual(int(encrypt_dict["/Length"]), 256)
        self.assertEqual(str(encrypt_dict["/Filter"]), "/Standard")

        # Le fichier reste pleinement exploitable par les fonctions du
        # projet une fois dechiffre (pas de regression de lecture AES-256).
        self.assertEqual(ops.get_page_count(protected, password="hunter2"), 1)
        self.assertIn("Secret", ops.extract_text(protected, password="hunter2")[0])

    def test_set_password_reprotects_an_already_protected_pdf(self):
        """L'onglet Protection permet de re-proteger un PDF deja protege
        (nouveau mot de passe) - `password=` est l'ancien mot de passe du
        fichier source, les deux nouveaux sont ceux passes en argument
        positionnel/owner_password. Le resultat doit lui aussi etre en
        AES-256."""
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1, labels=["Secret"])
        first = self.tmp / "first.pdf"
        ops.set_password(pdf, first, user_password="ancien-mdp")

        second = self.tmp / "second.pdf"
        ops.set_password(first, second, user_password="nouveau-mdp", password="ancien-mdp")

        reader = PdfReader(str(second))
        self.assertTrue(reader.is_encrypted)
        encrypt_dict = reader.trailer["/Encrypt"].get_object()
        self.assertEqual(int(encrypt_dict["/V"]), 5)
        self.assertEqual(int(encrypt_dict["/R"]), 6)
        self.assertEqual(int(encrypt_dict["/Length"]), 256)

        # L'ancien mot de passe ne doit plus fonctionner, seul le nouveau.
        with self.assertRaises(ops.PdfOpsError):
            ops.get_page_count(second, password="ancien-mdp")
        self.assertEqual(ops.get_page_count(second, password="nouveau-mdp"), 1)
        self.assertIn("Secret", ops.extract_text(second, password="nouveau-mdp")[0])

    def test_remove_password_rejects_wrong_password(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="correct-horse")
        with self.assertRaises(ops.PdfOpsError):
            ops.remove_password(protected, self.tmp / "out.pdf", password="wrong")

    def test_set_password_rejects_empty_password(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        with self.assertRaises(ops.PdfOpsError):
            ops.set_password(pdf, self.tmp / "out.pdf", user_password="")

    def test_extract_text_returns_one_entry_per_page(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=3, labels=["Un", "Deux", "Trois"])
        texts = ops.extract_text(pdf)
        self.assertEqual(len(texts), 3)
        self.assertIn("Deux", texts[1])

    # -- metadonnees --------------------------------------------------------------

    def test_read_metadata_returns_empty_strings_when_nothing_is_set(self):
        # reportlab (make_pdf) remplit des valeurs par defaut ("untitled",
        # "anonymous"...) dans le docinfo : ce test verifie donc plutot le
        # cas "vraiment vide" via un PdfWriter neuf, sans passer par
        # set_metadata (qui, lui, est deja teste separement pour la purge).
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        pdf = self.tmp / "vierge.pdf"
        with open(pdf, "wb") as f:
            writer.write(f)
        meta = ops.read_metadata(pdf)
        self.assertEqual(meta, {"title": "", "author": "", "subject": "", "keywords": ""})

    def test_set_then_read_metadata_round_trip(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1, labels=["Contenu"])
        output = self.tmp / "avec_metadonnees.pdf"
        ops.set_metadata(pdf, output, {
            "title": "Rapport été", "author": "Bob", "subject": "Sujet", "keywords": "mots,cles",
        })
        meta = ops.read_metadata(output)
        self.assertEqual(meta["title"], "Rapport été")
        self.assertEqual(meta["author"], "Bob")
        self.assertEqual(meta["subject"], "Sujet")
        self.assertEqual(meta["keywords"], "mots,cles")
        # Le contenu des pages est toujours preserve.
        self.assertIn("Contenu", ops.extract_text(output)[0])

    def test_set_metadata_with_empty_dict_purges_everything(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        with_meta = self.tmp / "avec.pdf"
        ops.set_metadata(pdf, with_meta, {"title": "A purger", "author": "Quelqu'un"})
        self.assertEqual(ops.read_metadata(with_meta)["title"], "A purger")

        purged = self.tmp / "purge.pdf"
        ops.set_metadata(with_meta, purged, {})
        meta = ops.read_metadata(purged)
        self.assertEqual(meta["title"], "")
        self.assertEqual(meta["author"], "")

    def test_set_metadata_strips_xmp_from_the_source(self):
        # Construit une source avec un flux XMP explicite au niveau racine
        # (comme le produirait Office/InDesign), pour verifier que
        # set_metadata ne le recopie jamais vers la sortie.
        from pypdf.generic import NameObject, DecodedStreamObject
        src_writer = PdfWriter()
        src_writer.add_blank_page(200, 200)
        xmp_stream = DecodedStreamObject()
        xmp_stream.set_data(b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><fake/></x:xmpmeta>')
        xmp_ref = src_writer._add_object(xmp_stream)
        src_writer._root_object[NameObject("/Metadata")] = xmp_ref
        source = self.tmp / "avec_xmp.pdf"
        with open(source, "wb") as f:
            src_writer.write(f)
        self.assertIn("/Metadata", PdfReader(str(source)).trailer["/Root"])

        output = self.tmp / "sans_xmp.pdf"
        ops.set_metadata(source, output, {})
        self.assertNotIn("/Metadata", PdfReader(str(output)).trailer["/Root"])

    def test_set_metadata_on_encrypted_pdf_requires_password(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")
        with self.assertRaises(ops.PdfOpsError):
            ops.set_metadata(protected, self.tmp / "out.pdf", {"title": "X"})
        ops.set_metadata(protected, self.tmp / "out.pdf", {"title": "X"}, password="secret")
        self.assertEqual(ops.read_metadata(self.tmp / "out.pdf", password="secret")["title"], "X")

    def test_set_metadata_preserves_password_protection_of_an_encrypted_source(self):
        # Regression trouvee a l'audit : editer/purger les metadonnees d'un
        # PDF protege produisait silencieusement une copie NON protegee (le
        # motif "nouveau writer, jamais reader.metadata" qui protege contre
        # la fuite de metadonnees faisait aussi disparaitre le chiffrement).
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1, labels=["Confidentiel"])
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")

        output = self.tmp / "out.pdf"
        ops.set_metadata(protected, output, {"title": "X"}, password="secret")
        self.assertTrue(PdfReader(str(output)).is_encrypted)
        with self.assertRaises(ops.PdfOpsError):
            ops.extract_text(output)  # sans mot de passe : toujours refuse
        self.assertIn("Confidentiel", ops.extract_text(output, password="secret")[0])

        # Meme garantie pour la purge (dict vide).
        purged = self.tmp / "purge.pdf"
        ops.set_metadata(protected, purged, {}, password="secret")
        self.assertTrue(PdfReader(str(purged)).is_encrypted)

    # -- pieces jointes -------------------------------------------------------------

    def test_extract_attachments_recovers_the_exact_content(self):
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        writer.add_attachment("facture.xml", b"<xml>contenu</xml>")
        pdf = self.tmp / "avec_pj.pdf"
        with open(pdf, "wb") as f:
            writer.write(f)

        extracted = ops.extract_attachments(pdf, self.tmp / "out")
        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0].name, "facture.xml")
        self.assertEqual(extracted[0].read_bytes(), b"<xml>contenu</xml>")

    def test_extract_attachments_on_pdf_without_any_returns_empty_list(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        self.assertEqual(ops.extract_attachments(pdf, self.tmp / "out"), [])

    def test_extract_attachments_with_the_same_name_twice_produces_two_files(self):
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        writer.add_attachment("piece.txt", b"premiere")
        writer.add_attachment("piece.txt", b"seconde")
        pdf = self.tmp / "avec_pj.pdf"
        with open(pdf, "wb") as f:
            writer.write(f)

        extracted = ops.extract_attachments(pdf, self.tmp / "out")
        self.assertEqual(len(extracted), 2)
        contents = {p.read_bytes() for p in extracted}
        self.assertEqual(contents, {b"premiere", b"seconde"})

    def test_extract_attachments_sanitizes_a_path_traversal_name(self):
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        writer.add_attachment("..\\..\\evil.txt", b"malveillant")
        pdf = self.tmp / "avec_pj.pdf"
        with open(pdf, "wb") as f:
            writer.write(f)

        output_dir = self.tmp / "out"
        extracted = ops.extract_attachments(pdf, output_dir)
        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0].name, "evil.txt")
        self.assertEqual(extracted[0].parent.resolve(), output_dir.resolve())

    def test_extract_attachments_on_encrypted_pdf_requires_password(self):
        pdf = make_pdf(self.tmp / "doc.pdf", num_pages=1)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")
        with self.assertRaises(ops.PdfOpsError):
            ops.extract_attachments(protected, self.tmp / "out")
        # Le mot de passe correct fonctionne (aucune piece jointe dans ce fichier).
        self.assertEqual(ops.extract_attachments(protected, self.tmp / "out", password="secret"), [])

    def test_extract_attachments_a_corrupt_attachment_never_aborts_extraction_of_the_others(self):
        # Regression trouvee a l'audit : reader.attachments est un dict
        # paresseux (pypdf LazyDict) qui decode le contenu d'une piece
        # jointe au moment de l'ACCES, pas de l'iteration des cles - iterer
        # via .items() decodait donc "bad.bin" pendant la boucle elle-meme,
        # avant tout try/except, et une reference /EF /F cassee (courante
        # sur un PDF malforme/tronque) faisait planter l'extraction de
        # TOUTES les pieces jointes, y compris "good.txt" qui la precede.
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        writer.add_attachment("good.txt", b"contenu valide")
        writer.add_attachment("bad.bin", b"sera corrompu")

        names = writer._root_object["/Names"]["/EmbeddedFiles"]["/Names"]
        filespec = names[names.index("bad.bin") + 1].get_object()
        filespec["/EF"][NameObject("/F")] = IndirectObject(99999, 0, writer)  # reference vers un objet inexistant

        pdf = self.tmp / "avec_pj_corrompue.pdf"
        with open(pdf, "wb") as f:
            writer.write(f)

        extracted = ops.extract_attachments(pdf, self.tmp / "out")
        names_extracted = {p.name for p in extracted}
        self.assertIn("good.txt", names_extracted)
        self.assertNotIn("bad.bin", names_extracted)
        good_path = next(p for p in extracted if p.name == "good.txt")
        self.assertEqual(good_path.read_bytes(), b"contenu valide")

    # -- preservation des signets/AcroForm (points 7/8 de l'audit) ---------------

    def test_merge_pdfs_preserves_outline_and_form_fields(self):
        # Regression trouvee a l'audit : merge_pdfs reconstruisait la sortie
        # via une boucle add_page() sur un PdfWriter neuf, qui ne clone que
        # l'objet page - jamais l'arbre /Outlines (signets) ni le /AcroForm
        # (formulaires) du document source.
        pdf_a = make_pdf_with_outline_and_form_field(self.tmp / "a.pdf", num_pages=2)
        pdf_b = make_pdf(self.tmp / "b.pdf", num_pages=1)
        output = self.tmp / "merged.pdf"
        ops.merge_pdfs([pdf_a, pdf_b], output)

        self.assertEqual(ops.get_page_count(output), 3)
        reader = PdfReader(str(output))
        self.assertEqual(len(reader.outline), 1)
        self.assertEqual(str(reader.outline[0].title), "Mon signet")
        fields = reader.get_fields()
        self.assertIn("champ_texte", fields)
        self.assertEqual(fields["champ_texte"]["/V"], "valeur remplie")

    def test_split_pdf_by_ranges_preserves_outline_and_form_fields_on_relevant_part(self):
        # Meme regression que merge_pdfs, pour split_pdf_by_ranges : diviser
        # un PDF avec signet/formulaire ne doit perdre ni l'un ni l'autre sur
        # la partie qui contient effectivement la page concernee, et ne doit
        # pas non plus les faire apparaitre par erreur sur l'autre partie.
        pdf = make_pdf_with_outline_and_form_field(self.tmp / "doc.pdf", num_pages=2)
        paths = ops.split_pdf_by_ranges(pdf, [(1, 1), (2, 2)], self.tmp / "split", "doc")

        reader_p1 = PdfReader(str(paths[0]))
        self.assertEqual(len(reader_p1.outline), 1)
        self.assertIn("champ_texte", reader_p1.get_fields() or {})

        reader_p2 = PdfReader(str(paths[1]))
        self.assertEqual(len(reader_p2.outline), 0)
        self.assertFalse(reader_p2.get_fields())

    def test_reorder_and_filter_pages_preserves_outline_and_form_fields(self):
        # reorder_and_filter_pages reconstruisait elle aussi la sortie via
        # add_page() sur un PdfWriter neuf, meme apres avoir correctement
        # attache le document complet (avec signets/formulaire) a une etape
        # intermediaire - la meme regression s'appliquait donc a l'onglet
        # Pages, non listee explicitement dans le rapport d'audit mais issue
        # de la meme cause racine.
        pdf = make_pdf_with_outline_and_form_field(self.tmp / "doc.pdf", num_pages=2)
        output = self.tmp / "reordered.pdf"
        ops.reorder_and_filter_pages(pdf, output, page_order=[2, 1])

        reader = PdfReader(str(output))
        self.assertEqual(len(reader.pages), 2)
        self.assertEqual(len(reader.outline), 1)
        fields = reader.get_fields()
        self.assertIn("champ_texte", fields)
        self.assertEqual(fields["champ_texte"]["/V"], "valeur remplie")

    def test_set_metadata_preserves_outline_and_form_fields(self):
        pdf = make_pdf_with_outline_and_form_field(self.tmp / "doc.pdf", num_pages=1)
        output = self.tmp / "meta.pdf"
        ops.set_metadata(pdf, output, {"title": "Un titre"})

        reader = PdfReader(str(output))
        self.assertEqual(len(reader.outline), 1)
        self.assertIn("champ_texte", reader.get_fields())
        self.assertEqual((reader.metadata or {}).get("/Title"), "Un titre")

    def test_set_metadata_purge_still_preserves_outline_and_form_fields(self):
        # La purge des metadonnees (dict vide) ne doit pas non plus faire
        # disparaitre les signets/formulaires : ce sont deux structures
        # distinctes du /Root, jamais touchees par set_metadata au-dela de
        # /Info (voir docstring de set_metadata).
        pdf = make_pdf_with_outline_and_form_field(self.tmp / "doc.pdf", num_pages=1)
        output = self.tmp / "meta_purge.pdf"
        ops.set_metadata(pdf, output, {})

        reader = PdfReader(str(output))
        self.assertEqual(len(reader.outline), 1)
        self.assertIn("champ_texte", reader.get_fields())

    def test_set_password_preserves_outline_and_form_fields(self):
        pdf = make_pdf_with_outline_and_form_field(self.tmp / "doc.pdf", num_pages=1)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")

        reader = PdfReader(str(protected))
        self.assertTrue(reader.is_encrypted)
        reader.decrypt("secret")
        self.assertEqual(len(reader.outline), 1)
        self.assertIn("champ_texte", reader.get_fields())

    def test_remove_password_preserves_outline_and_form_fields(self):
        pdf = make_pdf_with_outline_and_form_field(self.tmp / "doc.pdf", num_pages=1)
        protected = self.tmp / "protected.pdf"
        ops.set_password(pdf, protected, user_password="secret")

        unprotected = self.tmp / "unprotected.pdf"
        ops.remove_password(protected, unprotected, password="secret")

        reader = PdfReader(str(unprotected))
        self.assertFalse(reader.is_encrypted)
        self.assertEqual(len(reader.outline), 1)
        self.assertIn("champ_texte", reader.get_fields())

    # -- messages actionnables sur PDF invalide/corrompu (point 1 de l'audit) ----

    def test_get_page_count_on_empty_file_raises_a_clear_french_error(self):
        # Regression trouvee a l'audit : _open_reader n'enveloppait pas la
        # construction de PdfReader() dans un try/except, laissant remonter
        # l'exception technique brute de pypdf (EmptyFileError, en anglais)
        # jusqu'au filet generique du GUI plutot qu'un message actionnable.
        empty = self.tmp / "vide.pdf"
        empty.write_bytes(b"")
        with self.assertRaises(ops.PdfOpsError) as ctx:
            ops.get_page_count(empty)
        self.assertIn("vide.pdf", str(ctx.exception))
        self.assertIn("PDF valide", str(ctx.exception))

    def test_get_page_count_on_truncated_pdf_raises_a_clear_french_error(self):
        valid = make_pdf(self.tmp / "source.pdf", num_pages=3)
        truncated = self.tmp / "tronque.pdf"
        data = valid.read_bytes()
        truncated.write_bytes(data[: len(data) // 2])
        with self.assertRaises(ops.PdfOpsError) as ctx:
            ops.get_page_count(truncated)
        self.assertIn("tronque.pdf", str(ctx.exception))
        self.assertIn("PDF valide", str(ctx.exception))

    def test_get_page_count_on_non_pdf_file_raises_a_clear_french_error(self):
        fake = self.tmp / "notepad.pdf"
        fake.write_bytes(b"%PDF-1.4\n" + os.urandom(200))
        with self.assertRaises(ops.PdfOpsError) as ctx:
            ops.get_page_count(fake)
        self.assertIn("PDF valide", str(ctx.exception))

    # -- borne de version pypdf (point 24 de l'audit) -----------------------------

    def test_requirements_pins_pypdf_below_the_next_major_version(self):
        # requirements.txt declarait pypdf sans borne haute alors que
        # pdf_ops.py documente lui-meme un changement de comportement prevu
        # en 7.0 (modification de page encore rattachee a un reader seul) -
        # sans plafond, un futur `pip install -r requirements.txt` pourrait
        # installer 7.0 sans que le code n'ait ete valide contre elle.
        requirements_path = Path(__file__).resolve().parent.parent / "requirements.txt"
        content = requirements_path.read_text(encoding="utf-8")
        # "pypdf" seul, pas "pypdfium2" (dependance distincte, egalement
        # listee dans requirements.txt) : on ne garde que les lignes dont le
        # nom de paquet (avant tout extra "[...]" ou specificateur de
        # version) est exactement "pypdf".
        pypdf_lines = [
            line for line in content.splitlines()
            if not line.strip().startswith("#")
            and line.strip().lower().split("[")[0].split(">")[0].split("=")[0].strip() == "pypdf"
        ]
        self.assertEqual(len(pypdf_lines), 1)
        self.assertIn("<7.0", pypdf_lines[0])


if __name__ == "__main__":
    unittest.main()
