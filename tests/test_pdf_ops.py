"""Tests pour pdf_ops.py : chaque operation est verifiee sur de vrais
fichiers PDF/PNG generes sur disque (pas de mocks) - fusion, division,
gestion des pages, compression, conversion, filigrane, mot de passe,
extraction de texte."""

import sys
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
