"""Tests pour pdf_ops.py : chaque operation est verifiee sur de vrais
fichiers PDF/PNG generes sur disque (pas de mocks) - fusion, division,
gestion des pages, compression, conversion, filigrane, mot de passe,
extraction de texte."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pypdf import PdfReader
from PIL import Image
from reportlab.pdfgen import canvas

import pdf_ops as ops


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


if __name__ == "__main__":
    unittest.main()
