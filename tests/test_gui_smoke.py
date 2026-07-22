"""Smoke tests de bout en bout pilotant la VRAIE GUI Tkinter (un vrai
`Tk()`, les vrais widgets construits par `PdfAtelierApp`, les vrais boutons
retrouves dans l'arbre de widgets puis actionnes via `.invoke()`) - pas de
mock de gui.py lui-meme.

Objet de ce fichier : verifier que le passage des traitements fichier
unique (Fusionner, Diviser, Pages, Compresser/Filigrane/Numeroter/
Protection en mode un seul fichier, Proprietes) par l'infrastructure de
threading deja existante (`_run_in_background_with_progress`) :
  1) produit toujours exactement le meme resultat qu'avant (verifie sur
     disque, avec pypdf, pas seulement "aucune exception") ;
  2) ne bloque plus le thread principal Tk le temps du traitement (mesure
     empiriquement, meme methode que l'audit : le clic sur le bouton doit
     rendre la main quasi instantanement, le traitement se poursuit ensuite
     en arriere-plan pendant que la boucle d'evenements Tk continue de
     battre) ;
  3) remonte toujours les erreurs a l'utilisateur (mot de passe incorrect,
     etc.) via le meme mecanisme que le mode lot.

`gui.py` n'a historiquement aucune suite de tests dediee dans ce projet :
ce fichier est le premier, construit sur le meme principe que
tests/test_pdf_ops.py (aucun mock de la logique metier, de vrais fichiers
PDF generes sur disque)."""

import gc
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pypdf import PdfReader, PdfWriter
from PIL import Image
from reportlab.pdfgen import canvas

import gui
import pdf_ops as ops
from test_pdf_ops import make_pdf, make_pdf_with_image  # reutilise les generateurs de PDF de test deja etablis

try:
    from tkinter import Tk

    _root = Tk()
    _root.withdraw()
    TK_AVAILABLE = True
except Exception:
    TK_AVAILABLE = False
else:
    _root.destroy()


def make_big_pdf(path: Path, num_pages: int) -> Path:
    """PDF de nombreuses pages sans texte par page (juste assez de contenu
    pour etre un PDF valide) - utilise pour reproduire a echelle reduite le
    scenario de l'audit (filigrane sur un PDF de 3000 pages, 32.7s en
    synchrone) sans faire durer la suite de tests plusieurs dizaines de
    secondes."""
    c = canvas.Canvas(str(path), pagesize=(200, 200))
    for i in range(num_pages):
        c.drawString(20, 100, f"Page {i + 1}")
        c.showPage()
    c.save()
    return path


def find_button(widget, text):
    """Retrouve un ttk.Button par son texte dans l'arbre de widgets - la
    plupart des boutons de gui.py ne sont pas conserves comme attribut
    (seuls listbox/labels/entries le sont), donc les retrouver ainsi puis
    les actionner via `.invoke()` est la seule facon de vraiment "cliquer"
    dessus plutot que d'appeler directement la methode Python sous-jacente."""
    for child in widget.winfo_children():
        try:
            if child.winfo_class() in ("TButton", "Button") and child.cget("text") == text:
                return child
        except Exception:
            pass
        found = find_button(child, text)
        if found is not None:
            return found
    return None


def find_label_containing(widget, substring):
    """Retrouve un ttk.Label dont le texte contient `substring`, meme
    principe que find_button (aucun de ces labels n'est conserve comme
    attribut de PdfAtelierApp)."""
    for child in widget.winfo_children():
        try:
            if child.winfo_class() in ("TLabel", "Label") and substring in child.cget("text"):
                return child
        except Exception:
            pass
        found = find_label_containing(child, substring)
        if found is not None:
            return found
    return None


def pump(root, predicate, timeout=30.0):
    """Fait tourner manuellement la boucle d'evenements Tk (root.update())
    jusqu'a ce que `predicate()` devienne vrai ou que `timeout` (secondes)
    soit ecoule. Utilise a la place de root.mainloop() pour garder le
    controle du test. Renvoie le nombre d'appels a root.update() effectues -
    un traitement encore synchrone sur le thread principal bloquerait
    l'appel du bouton lui-meme (voir test_watermark_on_huge_pdf_does_not_
    block_ui) ; celui-ci mesure plutot que la boucle continue de tourner
    normalement PENDANT le traitement en arriere-plan."""
    deadline = time.time() + timeout
    ticks = 0
    while time.time() < deadline:
        root.update()
        ticks += 1
        if predicate():
            return ticks
        time.sleep(0.005)
    raise AssertionError(f"timeout apres {timeout}s en attendant la fin du traitement (predicate jamais vrai)")


@unittest.skipUnless(TK_AVAILABLE, "environnement sans affichage Tk disponible")
class GuiSmokeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self.tmp_dir.name)
        self.root = Tk()
        self.root.withdraw()  # pas d'affichage a l'ecran necessaire pour piloter les widgets

        # PdfAtelierApp.__init__ lance normalement une vraie verification de
        # mise a jour en ligne (update_checker.start_update_check, thread +
        # appel reseau vers l'API GitHub) et se replanifie indefiniment
        # toutes les 500ms via root.after(_poll_update_check) - sans mock,
        # chaque test ferait un appel reseau reel (lent, flaky, sujet a
        # rate-limit) et laisserait un after() planifie survivre a
        # root.destroy() (bruit "invalid command name ..._poll_update_check"
        # dans la sortie des tests). Hors-sujet par rapport a cette
        # optimisation (threading des traitements PDF) : neutralise pour que
        # les tests restent rapides, deterministes, silencieux et hors-ligne.
        patchers = [
            mock.patch("gui.update_checker.start_update_check"),
            mock.patch.object(gui.PdfAtelierApp, "_poll_update_check"),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

        self.app = gui.PdfAtelierApp(self.root)

        # Les messagebox Tk reelles ouvriraient une fenetre modale bloquante
        # (wait_window) en environnement graphique - on les intercepte pour
        # eviter que la suite de tests ne reste figee en attente d'un clic
        # humain, tout en gardant une trace des messages pour les assertions.
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []
        patches = [
            mock.patch("gui.messagebox.showinfo", side_effect=lambda *a, **k: self.info_messages.append(a[1] if len(a) > 1 else "")),
            mock.patch("gui.messagebox.showwarning", side_effect=lambda *a, **k: self.warning_messages.append(a[1] if len(a) > 1 else "")),
            mock.patch("gui.messagebox.showerror", side_effect=lambda *a, **k: self.error_messages.append(a[1] if len(a) > 1 else "")),
            mock.patch("gui.messagebox.askyesno", return_value=True),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        # Annule tout `after()` encore planifie (ex : le pas-de-temps 500ms
        # de _poll_update_check, neutralise mais deja enregistre aupres de
        # Tcl avant que le mock ne prenne effet) - sans cela, root.destroy()
        # laisse une commande Tcl fantome qui declenche un message "invalid
        # command name" (bruit cosmetique dans les tests, sans rapport avec
        # le code teste) des que Tcl tente de l'invoquer apres coup.
        for after_id in self.root.tk.call("after", "info"):
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass
        # Ferme explicitement le document pdfium eventuellement mis en
        # cache par un test d'apercu (voir _get_cached_pdfium_document,
        # dimension 37 de l'audit) AVANT `self.tmp_dir.cleanup()` : compter
        # sur le ramasse-miettes seul (comme pour les StringVar ci-dessous)
        # n'est PAS suffisant ici - contrairement a une StringVar, un
        # PdfDocument pdfium retient un vrai verrou de fichier Windows, et
        # son moment de liberation par le GC n'est pas assez deterministe
        # pour garantir qu'il ait deja eu lieu au moment ou `tmp_dir.
        # cleanup()` tente de supprimer le meme fichier juste apres (observe
        # empiriquement : PermissionError intermittente sur `shutil.rmtree`
        # sans cet appel explicite).
        if hasattr(self, "app"):
            self.app._close_cached_pdfium_document()
        # `self.app` retient des dizaines de StringVar/IntVar/BooleanVar (une
        # par onglet/champ) qui gardent chacune une reference a l'interprete
        # Tcl de `self.root`. Sans ce `del` + `gc.collect()` explicites AVANT
        # `root.destroy()`, ces variables ne sont desallouees que plus tard,
        # au gre du ramasse-miettes Python - potentiellement pendant un TEST
        # SUIVANT, une fois l'interprete Tcl de CE root deja detruit. Leur
        # `__del__` tente alors d'appeler Tcl sur un interprete mort, ce qui
        # echoue avec "main thread is not in main loop" et peut perturber
        # l'etat Tcl global du processus (observe a l'audit : plantages/
        # blocages intermittents de la suite complete via `unittest discover`,
        # absents quand ce fichier de test tourne seul). Les liberer ici,
        # pendant que l'interprete est encore vivant, les fait disparaitre
        # proprement et immediatement plutot que de laisser trainer des
        # `__del__` differes qui visent un interprete deja mort.
        del self.app
        gc.collect()
        self.root.destroy()
        self.tmp_dir.cleanup()

    def _done(self):
        return bool(self.info_messages or self.warning_messages or self.error_messages)

    # -- Fusionner ----------------------------------------------------------------

    def test_merge_run_in_background_produces_identical_result(self):
        pdf_a = make_pdf(self.tmp / "a.pdf", num_pages=2, labels=["A1", "A2"])
        pdf_b = make_pdf(self.tmp / "b.pdf", num_pages=3, labels=["B1", "B2", "B3"])
        self.app.merge_files = [pdf_a, pdf_b]
        gui.PdfAtelierApp._reload_listbox(self.app.merge_listbox, self.app.merge_files)

        output = self.tmp / "fusion.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(output)):
            button = find_button(self.app.merge_tab, "Fusionner en un seul PDF...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)

        # Le clic doit rendre la main quasi immediatement : le traitement se
        # poursuit sur un thread separe, pas dans le callback du bouton lui-
        # meme (c'etait le cas avant : la fusion s'executait entierement,
        # de facon synchrone, avant que le callback ne rende la main).
        self.assertLess(click_duration, 1.0)
        self.assertTrue(output.exists())
        self.assertEqual(len(PdfReader(str(output)).pages), 5)
        self.assertEqual(self.info_messages, [f"PDF fusionne enregistre : {output.name}"])
        self.assertFalse(self.warning_messages or self.error_messages)

    # -- Diviser --------------------------------------------------------------

    def test_split_run_in_background_produces_identical_result(self):
        src = make_pdf(self.tmp / "source.pdf", num_pages=5)
        self.app.split_source_path = src
        self.app.split_source_password = None
        self.app.split_source_var.set(f"{src.name} (5 pages)")
        self.app.split_mode_var.set("ranges")
        self.app.split_ranges_var.set("1-2,3-5")

        output_dir = self.tmp / "out"
        output_dir.mkdir()
        with mock.patch("gui.filedialog.askdirectory", return_value=str(output_dir)):
            button = find_button(self.app.split_tab, "Diviser...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)

        self.assertLess(click_duration, 1.0)
        generated = sorted(output_dir.glob("*.pdf"))
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(PdfReader(str(generated[0])).pages), 2)
        self.assertEqual(len(PdfReader(str(generated[1])).pages), 3)
        self.assertEqual(len(self.info_messages), 1)
        self.assertIn("2 fichier(s) genere(s)", self.info_messages[0])

    # -- Pages ------------------------------------------------------------------

    def test_pages_save_in_background_applies_reorder_and_rotation(self):
        src = make_pdf(self.tmp / "pages.pdf", num_pages=3, labels=["P1", "P2", "P3"])
        self.app.pages_source_path = src
        self.app.pages_source_password = None
        self.app.page_state = [
            {"page": 3, "rotation": 90},
            {"page": 1, "rotation": 0},
        ]  # page 2 supprimee, ordre inverse, page 3 pivotee
        self.app._pages_reload_listbox()

        output = self.tmp / "pages_modifiees.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(output)):
            button = find_button(self.app.pages_tab, "Enregistrer sous...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)

        self.assertLess(click_duration, 1.0)
        reader = PdfReader(str(output))
        self.assertEqual(len(reader.pages), 2)
        self.assertEqual(reader.pages[0].rotation % 360, 90)
        self.assertEqual(self.info_messages, [f"Document enregistre : {output.name}"])

    # -- Compresser (fichier unique) ---------------------------------------------

    def test_compress_run_single_file_in_background(self):
        src = make_pdf(self.tmp / "c.pdf", num_pages=2)
        self.app.compress_sources = [src]
        gui.PdfAtelierApp._reload_listbox(self.app.compress_listbox, self.app.compress_sources)
        self.app.compress_quality_var.set(40)
        self.app.compress_max_dim_var.set(800)

        output = self.tmp / "c_compresse.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(output)):
            button = find_button(self.app.compress_tab, "Compresser...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)

        self.assertLess(click_duration, 1.0)
        self.assertTrue(output.exists())
        self.assertEqual(len(PdfReader(str(output)).pages), 2)
        self.assertEqual(len(self.info_messages), 1)
        self.assertIn("1 fichier(s)", self.info_messages[0])
        self.assertIn("compresse", self.info_messages[0])

    # -- Convertir (PDF vers images) -----------------------------------------------

    def test_p2i_run_rejects_a_zero_dpi_without_starting_a_background_thread(self):
        # Point 14 de l'audit : un DPI a 0 (faute de frappe plausible) doit
        # etre refuse immediatement, avec le meme message "Reglages
        # invalides" que les autres champs numeriques mal saisis - jamais en
        # ouvrant la fenetre de progression pour echouer seulement une fois
        # le traitement lance en arriere-plan.
        src = make_pdf(self.tmp / "p.pdf", num_pages=1)
        self.app.p2i_source_path = src
        self.app.p2i_source_var.set(src.name)
        self.app.p2i_dpi_var.set(0)

        output_dir = self.tmp / "out"
        output_dir.mkdir()
        with mock.patch("gui.filedialog.askdirectory", return_value=str(output_dir)):
            button = find_button(self.app.convert_tab, "Convertir en images...")
            self.assertIsNotNone(button)
            button.invoke()

        self.assertEqual(len(self.warning_messages), 1)
        self.assertIn("Reglages invalides", self.warning_messages[0])
        self.assertIn("resolution", self.warning_messages[0].lower())
        self.assertFalse(self.info_messages or self.error_messages)
        # Aucune image ne doit avoir ete produite : le traitement n'a jamais
        # ete lance.
        self.assertEqual(list(output_dir.iterdir()), [])

    def test_p2i_run_rejects_a_negative_dpi_without_starting_a_background_thread(self):
        src = make_pdf(self.tmp / "p.pdf", num_pages=1)
        self.app.p2i_source_path = src
        self.app.p2i_source_var.set(src.name)
        self.app.p2i_dpi_var.set(-50)

        output_dir = self.tmp / "out"
        output_dir.mkdir()
        with mock.patch("gui.filedialog.askdirectory", return_value=str(output_dir)):
            button = find_button(self.app.convert_tab, "Convertir en images...")
            self.assertIsNotNone(button)
            button.invoke()

        self.assertEqual(len(self.warning_messages), 1)
        self.assertIn("Reglages invalides", self.warning_messages[0])
        self.assertFalse(self.info_messages or self.error_messages)

    def test_p2i_run_with_a_valid_dpi_still_converts_in_the_background(self):
        # Non-regression : la nouvelle validation ne doit pas bloquer un DPI
        # valide.
        src = make_pdf(self.tmp / "p.pdf", num_pages=1)
        self.app.p2i_source_path = src
        self.app.p2i_source_var.set(src.name)
        self.app.p2i_dpi_var.set(72)

        output_dir = self.tmp / "out"
        output_dir.mkdir()
        with mock.patch("gui.filedialog.askdirectory", return_value=str(output_dir)):
            button = find_button(self.app.convert_tab, "Convertir en images...")
            self.assertIsNotNone(button)
            button.invoke()

        pump(self.root, self._done)

        self.assertEqual(len(self.info_messages), 1)
        self.assertFalse(self.warning_messages or self.error_messages)
        self.assertEqual(len(list(output_dir.glob("*.png"))), 1)

    # -- Numeroter (fichier unique) -----------------------------------------------

    def test_page_numbers_run_single_file_in_background(self):
        src = make_pdf(self.tmp / "n.pdf", num_pages=3)
        self.app.page_numbers_sources = [src]
        gui.PdfAtelierApp._reload_listbox(self.app.page_numbers_listbox, self.app.page_numbers_sources)

        output = self.tmp / "n_numerote.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(output)):
            button = find_button(self.app.page_numbers_tab, "Numeroter les pages...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)

        self.assertLess(click_duration, 1.0)
        self.assertTrue(output.exists())
        self.assertEqual(len(PdfReader(str(output)).pages), 3)
        self.assertEqual(len(self.info_messages), 1)
        self.assertIn("1 fichier(s)", self.info_messages[0])

    # -- Protection (fichier unique) -----------------------------------------------

    def test_protect_run_single_file_add_then_remove_roundtrip(self):
        src = make_pdf(self.tmp / "p.pdf", num_pages=2)
        self.app.protect_sources = [src]
        gui.PdfAtelierApp._reload_listbox(self.app.protect_listbox, self.app.protect_sources)
        self.app.protect_mode_var.set("add")
        self.app.protect_password_var.set("secret123")
        self.app.protect_confirm_var.set("secret123")

        protected = self.tmp / "p_protege.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(protected)):
            button = find_button(self.app.protect_tab, "Appliquer...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)
        self.assertLess(click_duration, 1.0)
        self.assertTrue(protected.exists())
        reader = PdfReader(str(protected))
        self.assertTrue(reader.is_encrypted)
        self.assertEqual(len(self.info_messages), 1)
        self.assertIn("1 fichier(s)", self.info_messages[0])

        # Round-trip : retirer le mot de passe qui vient d'etre applique.
        self.info_messages.clear()
        self.app.protect_sources = [protected]
        gui.PdfAtelierApp._reload_listbox(self.app.protect_listbox, self.app.protect_sources)
        self.app.protect_mode_var.set("remove")
        self.app.protect_password_var.set("secret123")

        unprotected = self.tmp / "p_sans_mot_de_passe.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(unprotected)):
            button = find_button(self.app.protect_tab, "Appliquer...")
            button.invoke()

        pump(self.root, self._done)
        self.assertTrue(unprotected.exists())
        self.assertFalse(PdfReader(str(unprotected)).is_encrypted)

    def test_protect_run_single_file_reports_wrong_password_error(self):
        """Verifie que l'erreur (mot de passe incorrect) remonte toujours a
        l'utilisateur via messagebox une fois le traitement passe en
        arriere-plan, exactement comme pour le mode lot."""
        src = make_pdf(self.tmp / "wrong.pdf", num_pages=1)
        protected = self.tmp / "wrong_protege.pdf"
        ops.set_password(src, protected, "correct-password")

        self.app.protect_sources = [protected]
        gui.PdfAtelierApp._reload_listbox(self.app.protect_listbox, self.app.protect_sources)
        self.app.protect_mode_var.set("remove")
        self.app.protect_password_var.set("mot-de-passe-invalide")

        output = self.tmp / "wrong_sans_mot_de_passe.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(output)):
            button = find_button(self.app.protect_tab, "Appliquer...")
            button.invoke()

        pump(self.root, self._done)

        # _run_batch capture l'echec par fichier (n'interrompt jamais les
        # autres) : le resume s'affiche donc via showinfo (0 succes, 1
        # echec detaille dans le texte), pas via showerror/showwarning.
        self.assertEqual(len(self.info_messages), 1)
        self.assertIn("0 fichier(s)", self.info_messages[0])
        self.assertIn("1 echec(s)", self.info_messages[0])

    # -- Proprietes ---------------------------------------------------------------

    def test_properties_save_in_background(self):
        src = make_pdf(self.tmp / "meta.pdf", num_pages=1)
        self.app.properties_source_path = src
        self.app.properties_source_password = None
        self.app.properties_title_var.set("Mon titre")
        self.app.properties_author_var.set("Un auteur")

        output = self.tmp / "meta_proprietes.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(output)):
            button = find_button(self.app.properties_tab, "Enregistrer sous...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)

        self.assertLess(click_duration, 1.0)
        self.assertTrue(output.exists())
        meta = ops.read_metadata(output)
        self.assertEqual(meta["title"], "Mon titre")
        self.assertEqual(meta["author"], "Un auteur")
        self.assertEqual(self.info_messages, [f"Proprietes enregistrees : {output.name}"])

    def test_properties_purge_in_background_clears_fields_on_success(self):
        src = make_pdf(self.tmp / "meta2.pdf", num_pages=1)
        self.app.properties_source_path = src
        self.app.properties_source_password = None
        self.app.properties_title_var.set("A purger")
        self.app.properties_author_var.set("Aussi")

        output = self.tmp / "meta2_purge.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(output)):
            button = find_button(self.app.properties_tab, "Purger les metadonnees...")
            self.assertIsNotNone(button)
            button.invoke()

        pump(self.root, self._done)

        self.assertTrue(output.exists())
        meta = ops.read_metadata(output)
        self.assertEqual(meta["title"], "")
        self.assertEqual(meta["author"], "")
        # Les champs de l'onglet ne doivent etre vides QUE parce que la
        # purge a reellement reussi (bug historique corrige avant cette
        # optimisation : le nettoyage ne se declenchait jamais).
        self.assertEqual(self.app.properties_title_var.get(), "")
        self.assertEqual(self.app.properties_author_var.get(), "")

    # -- Extraire images/pieces jointes, Texte, Images->PDF (point 9 de l'audit) --
    # Ces quatre traitements restaient synchrones sur le thread principal Tk,
    # contrairement au reste de l'application deja migre (Fusionner/Diviser/
    # Pages/Proprietes/mode fichier unique de Compresser/Filigrane/
    # Numeroter/Protection) - cas residuels trouves a l'audit, jamais
    # couverts par ce fichier de tests avant ce round de correctifs.

    def test_extract_embedded_images_run_in_background(self):
        source_image = self.tmp / "photo.png"
        Image.new("RGB", (40, 30), color=(10, 20, 30)).save(source_image)
        pdf = make_pdf_with_image(self.tmp / "doc.pdf", source_image)
        self.app.eei_source_path = pdf
        self.app.eei_source_var.set(pdf.name)

        output_dir = self.tmp / "out_images"
        output_dir.mkdir()
        with mock.patch("gui.filedialog.askdirectory", return_value=str(output_dir)):
            button = find_button(self.app.convert_tab, "Extraire les images...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)

        self.assertLess(click_duration, 1.0)
        self.assertEqual(len(list(output_dir.glob("*.png"))), 1)
        self.assertEqual(len(self.info_messages), 1)
        self.assertIn("1 image(s)", self.info_messages[0])

    def test_extract_attachments_run_in_background(self):
        writer = PdfWriter()
        writer.add_blank_page(200, 200)
        writer.add_attachment("facture.xml", b"<xml>contenu</xml>")
        pdf = self.tmp / "avec_pj.pdf"
        with open(pdf, "wb") as f:
            writer.write(f)
        self.app.eea_source_path = pdf
        self.app.eea_source_var.set(pdf.name)

        output_dir = self.tmp / "out_attachments"
        output_dir.mkdir()
        with mock.patch("gui.filedialog.askdirectory", return_value=str(output_dir)):
            button = find_button(self.app.convert_tab, "Extraire les pieces jointes...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)

        self.assertLess(click_duration, 1.0)
        extracted = list(output_dir.glob("*.xml"))
        self.assertEqual(len(extracted), 1)
        self.assertEqual(extracted[0].read_bytes(), b"<xml>contenu</xml>")
        self.assertEqual(len(self.info_messages), 1)
        self.assertIn("1 piece(s) jointe(s)", self.info_messages[0])

    def test_attachments_tab_warns_before_opening_extracted_files(self):
        # Point 12 de l'audit : PdfAtelier n'ouvre jamais lui-meme les
        # pieces jointes extraites, mais rien n'avertissait l'utilisateur
        # qui les ouvrirait ensuite MANUELLEMENT (executable deguise,
        # macro...) - un rappel explicite doit etre visible dans l'onglet
        # avant meme de lancer l'extraction.
        label = find_label_containing(self.app.convert_tab, "Verifiez la provenance du PDF")
        self.assertIsNotNone(label)
        self.assertIn("contenu actif", label.cget("text"))

    def test_text_extraction_run_in_background(self):
        src = make_pdf(self.tmp / "texte.pdf", num_pages=2, labels=["Bonjour", "Au revoir"])
        self.app.text_source_path = src
        self.app.text_source_var.set(src.name)

        button = find_button(self.app.text_tab, "Extraire")
        self.assertIsNotNone(button)
        t0 = time.perf_counter()
        button.invoke()
        click_duration = time.perf_counter() - t0

        # A la difference des autres onglets migres, une extraction de texte
        # reussie n'affiche aucune messagebox (le resultat va directement
        # dans le widget Text) : on attend donc que le texte apparaisse
        # plutot que self._done(), qui ne deviendrait jamais vrai ici.
        pump(self.root, lambda: self.app.text_output.get("1.0", "end").strip() != "")

        self.assertLess(click_duration, 1.0)
        content = self.app.text_output.get("1.0", "end")
        self.assertIn("Bonjour", content)
        self.assertIn("Au revoir", content)
        self.assertFalse(self.warning_messages or self.error_messages)

    def test_images_to_pdf_run_in_background(self):
        image_path = self.tmp / "img1.png"
        Image.new("RGB", (30, 20), color=(50, 60, 70)).save(image_path)
        self.app.i2p_files = [image_path]
        gui.PdfAtelierApp._reload_listbox(self.app.i2p_listbox, self.app.i2p_files)

        output = self.tmp / "images.pdf"
        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(output)):
            button = find_button(self.app.convert_tab, "Assembler en PDF...")
            self.assertIsNotNone(button)
            t0 = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - t0

        pump(self.root, self._done)

        self.assertLess(click_duration, 1.0)
        self.assertTrue(output.exists())
        self.assertEqual(len(PdfReader(str(output)).pages), 1)
        self.assertEqual(self.info_messages, [f"PDF genere : {output.name}"])

    # -- Taille minimale de fenetre (point 16 de l'audit) --------------------------

    def test_window_has_a_minimum_size_preventing_clipped_tabs_and_buttons(self):
        # Sans minsize, reduire la fenetre bien en dessous de sa taille de
        # contenu naturelle rendait certains boutons d'action totalement
        # inaccessibles (ex: onglet Compresser a 480x320) - bug trouve a
        # l'audit, reproductible a 100% sur un simple redimensionnement.
        self.assertEqual(self.root.minsize(), (1020, 680))

    # -- Coherence de mise en page entre onglets (point 17 de l'audit) -------------

    def test_batch_listboxes_expand_to_fill_available_vertical_space(self):
        # Contrairement a Fusionner/Pages (fill=BOTH, expand=True), les
        # listes de Compresser/Filigrane/Numeroter/Protection restaient a
        # une hauteur fixe de 5 lignes meme sur une grande fenetre -
        # incoherence purement visuelle corrigee en alignant leur
        # configuration de pack() sur celle de Fusionner/Pages.
        for listbox in (
            self.app.compress_listbox,
            self.app.watermark_listbox,
            self.app.page_numbers_listbox,
            self.app.protect_listbox,
        ):
            info = listbox.pack_info()
            self.assertEqual(info["fill"], "both")
            self.assertEqual(int(info["expand"]), 1)

    # -- Cache du document pdfium pour l'apercu (point 37 de l'audit) --------------

    def test_render_pdf_page_image_reuses_the_cached_pdfium_document(self):
        src = make_pdf(self.tmp / "apercu.pdf", num_pages=2)

        self.app._render_pdf_page_image(src, None, 1)
        first_doc = self.app._pdfium_cache_doc
        self.assertIsNotNone(first_doc)

        # Meme fichier/mot de passe : le document pdfium ne doit pas etre
        # rouvert (meme objet reutilise), contrairement au comportement
        # d'avant le correctif (nouveau PdfDocument a CHAQUE appel).
        self.app._render_pdf_page_image(src, None, 2)
        self.assertIs(self.app._pdfium_cache_doc, first_doc)

    def test_render_pdf_page_image_invalidates_cache_on_file_change(self):
        src_a = make_pdf(self.tmp / "a.pdf", num_pages=1)
        src_b = make_pdf(self.tmp / "b.pdf", num_pages=1)

        self.app._render_pdf_page_image(src_a, None, 1)
        doc_a = self.app._pdfium_cache_doc

        self.app._render_pdf_page_image(src_b, None, 1)
        doc_b = self.app._pdfium_cache_doc

        self.assertIsNot(doc_a, doc_b)

    def test_close_cached_pdfium_document_releases_the_file_handle(self):
        src = make_pdf(self.tmp / "apercu.pdf", num_pages=1)
        self.app._render_pdf_page_image(src, None, 1)
        self.assertIsNotNone(self.app._pdfium_cache_doc)

        self.app._close_cached_pdfium_document()

        self.assertIsNone(self.app._pdfium_cache_doc)
        self.assertIsNone(self.app._pdfium_cache_key)
        # Aucun handle pdfium residuel : le fichier doit pouvoir etre
        # supprime immediatement (preuve indirecte mais concrete sur
        # Windows, ou un fichier encore ouvert ne peut pas etre supprime).
        src.unlink()

    def test_closing_the_main_window_releases_the_cached_pdfium_document(self):
        src = make_pdf(self.tmp / "apercu.pdf", num_pages=1)
        self.app._render_pdf_page_image(src, None, 1)
        self.assertIsNotNone(self.app._pdfium_cache_doc)

        self.app._on_close()

        self.assertIsNone(self.app._pdfium_cache_doc)
        src.unlink()
        # _on_close a deja detruit root - evite un double root.destroy()
        # dans tearDown.
        self.root = Tk()
        self.root.withdraw()

    # -- Lot avec echecs systemiques repetes (point 44 de l'audit) -----------------

    def test_run_batch_stops_early_after_repeated_identical_failures(self):
        pairs = [(self.tmp / f"f{i}.pdf", self.tmp / f"f{i}_out.pdf") for i in range(10)]

        def always_same_failure(source, output):
            raise ops.PdfOpsError("Espace disque insuffisant.")

        successes, failures = self.app._run_batch(pairs, always_same_failure)

        self.assertEqual(successes, [])
        # 3 echecs individuels identiques, puis une ligne de synthese
        # d'interruption (pas 10 lignes quasi identiques).
        self.assertEqual(len(failures), 4)
        for _source, message in failures[:3]:
            self.assertEqual(message, "Espace disque insuffisant.")
        self.assertIn("interrompu", failures[-1][1])
        self.assertIn("7 fichier(s)", failures[-1][1])

    def test_run_batch_does_not_stop_early_when_failures_differ(self):
        # Non-regression : des echecs distincts (pas de panne systemique)
        # ne doivent jamais interrompre le lot prematurement.
        pairs = [(self.tmp / f"f{i}.pdf", self.tmp / f"f{i}_out.pdf") for i in range(5)]

        def varying_failure(source, output):
            raise ops.PdfOpsError(f"Erreur specifique a {source.name}")

        successes, failures = self.app._run_batch(pairs, varying_failure)

        self.assertEqual(successes, [])
        self.assertEqual(len(failures), 5)

    def test_run_batch_resets_the_streak_after_an_intervening_success(self):
        # Non-regression : 2 echecs identiques, un succes, puis a nouveau
        # des echecs identiques ne doivent pas cumuler le compteur a
        # travers le succes intercale.
        pairs = [(self.tmp / f"f{i}.pdf", self.tmp / f"f{i}_out.pdf") for i in range(6)]

        def action(source, output):
            if source.name == "f2.pdf":
                return "ok"
            raise ops.PdfOpsError("Erreur recurrente.")

        successes, failures = self.app._run_batch(pairs, action)

        self.assertEqual(len(successes), 1)
        # 2 echecs (f0, f1), le succes intercale (f2) remet le compteur a
        # zero, puis 3 nouveaux echecs identiques (f3, f4, f5) atteignent le
        # seuil exactement sur le DERNIER fichier du lot : aucun fichier ne
        # reste a traiter, donc pas de ligne de synthese d'interruption (le
        # lot se termine naturellement en meme temps) - seulement les 5
        # echecs individuels.
        self.assertEqual(len(failures), 5)

    # -- Avertissement avant un traitement volumineux (point 45 de l'audit) --------

    def test_resolve_batch_outputs_warns_before_a_large_batch(self):
        sources = [self.tmp / f"f{i}.pdf" for i in range(gui.LARGE_BATCH_FILE_THRESHOLD + 1)]
        for src in sources:
            make_pdf(src, num_pages=1)

        with mock.patch("gui.filedialog.askdirectory", return_value=str(self.tmp)) as mock_askdir, \
                mock.patch("gui.messagebox.askyesno", return_value=True) as mock_confirm:
            pairs = self.app._resolve_batch_outputs(sources, "resultat.pdf", "_suffixe")

        mock_confirm.assert_called_once()
        self.assertIn(str(len(sources)), mock_confirm.call_args[0][1])
        mock_askdir.assert_called_once()
        self.assertEqual(len(pairs), len(sources))

    def test_resolve_batch_outputs_aborts_when_the_large_batch_warning_is_declined(self):
        sources = [self.tmp / f"f{i}.pdf" for i in range(gui.LARGE_BATCH_FILE_THRESHOLD + 1)]
        for src in sources:
            make_pdf(src, num_pages=1)

        with mock.patch("gui.filedialog.askdirectory") as mock_askdir, \
                mock.patch("gui.messagebox.askyesno", return_value=False):
            pairs = self.app._resolve_batch_outputs(sources, "resultat.pdf", "_suffixe")

        self.assertIsNone(pairs)
        # Refuse avant meme de demander le dossier de destination.
        mock_askdir.assert_not_called()

    def test_resolve_batch_outputs_does_not_warn_below_the_threshold(self):
        sources = [self.tmp / f"f{i}.pdf" for i in range(3)]
        for src in sources:
            make_pdf(src, num_pages=1)

        with mock.patch("gui.filedialog.askdirectory", return_value=str(self.tmp)), \
                mock.patch("gui.messagebox.askyesno") as mock_confirm:
            pairs = self.app._resolve_batch_outputs(sources, "resultat.pdf", "_suffixe")

        mock_confirm.assert_not_called()
        self.assertEqual(len(pairs), 3)

    def test_p2i_run_warns_before_converting_a_very_large_document(self):
        src = make_pdf(self.tmp / "gros.pdf", num_pages=gui.LARGE_CONVERSION_PAGE_THRESHOLD + 1)
        self.app.p2i_source_path = src
        self.app.p2i_source_var.set(src.name)
        self.app.p2i_dpi_var.set(72)

        output_dir = self.tmp / "out"
        output_dir.mkdir()
        with mock.patch("gui.filedialog.askdirectory", return_value=str(output_dir)), \
                mock.patch("gui.messagebox.askyesno", return_value=False) as mock_confirm:
            button = find_button(self.app.convert_tab, "Convertir en images...")
            self.assertIsNotNone(button)
            button.invoke()

        mock_confirm.assert_called_once()
        self.assertIn(str(gui.LARGE_CONVERSION_PAGE_THRESHOLD + 1), mock_confirm.call_args[0][1])
        # Refuse : aucune image ne doit avoir ete produite.
        self.assertEqual(list(output_dir.iterdir()), [])
        self.assertFalse(self.info_messages or self.warning_messages or self.error_messages)

    def test_p2i_run_does_not_warn_below_the_page_threshold(self):
        src = make_pdf(self.tmp / "petit.pdf", num_pages=1)
        self.app.p2i_source_path = src
        self.app.p2i_source_var.set(src.name)
        self.app.p2i_dpi_var.set(72)

        output_dir = self.tmp / "out"
        output_dir.mkdir()
        with mock.patch("gui.filedialog.askdirectory", return_value=str(output_dir)), \
                mock.patch("gui.messagebox.askyesno") as mock_confirm:
            button = find_button(self.app.convert_tab, "Convertir en images...")
            self.assertIsNotNone(button)
            button.invoke()

        pump(self.root, self._done)

        mock_confirm.assert_not_called()
        self.assertEqual(len(self.info_messages), 1)

    # -- Mesure empirique de reactivite (methode de l'audit) -----------------------

    def test_watermark_on_huge_pdf_does_not_block_ui(self):
        """Reproduit a echelle reduite la mesure de l'audit : un filigrane
        appele en synchrone sur un PDF de 3000 pages bloquait l'UI 32.7s.
        Ici, sur un PDF de taille reduite (le temps de traitement suffit
        deja a distinguer de facon flagrante synchrone/threade sans faire
        durer la suite de tests des dizaines de secondes) : on verifie que
        le CLIC lui-meme rend la main quasi instantanement (le traitement
        se termine bien plus tard, en arriere-plan) - la preuve directe que
        ce n'est plus le thread principal Tk qui execute add_text_watermark."""
        src = make_big_pdf(self.tmp / "huge.pdf", num_pages=1200)
        self.app.watermark_sources = [src]
        gui.PdfAtelierApp._reload_listbox(self.app.watermark_listbox, self.app.watermark_sources)
        self.app.watermark_text_var.set("CONFIDENTIEL")

        output = self.tmp / "huge_filigrane.pdf"

        # Mesure de reference (hors GUI) : combien de temps prend vraiment
        # ops.add_text_watermark sur ce fichier - pour verifier que le
        # traitement en arriere-plan met bien un temps du meme ordre de
        # grandeur (et n'est donc pas trivialement rapide/vide).
        reference_start = time.perf_counter()
        ops.add_text_watermark(src, self.tmp / "reference.pdf", "CONFIDENTIEL")
        reference_duration = time.perf_counter() - reference_start

        with mock.patch("gui.filedialog.asksaveasfilename", return_value=str(output)):
            button = find_button(self.app.watermark_tab, "Appliquer le filigrane...")
            self.assertIsNotNone(button)

            click_start = time.perf_counter()
            button.invoke()
            click_duration = time.perf_counter() - click_start

            # Pendant que le traitement tourne en arriere-plan, la boucle
            # d'evenements Tk doit continuer de battre : on programme un
            # compteur independant (root.after) et on verifie qu'il progresse
            # normalement plutot que de rester fige jusqu'a la fin.
            tk_ticks = {"count": 0}

            def tk_tick():
                tk_ticks["count"] += 1
                self.root.after(20, tk_tick)

            self.root.after(20, tk_tick)
            total_start = time.perf_counter()
            pump(self.root, self._done, timeout=60.0)
            total_duration = time.perf_counter() - total_start

        # Le clic doit rendre la main bien avant la fin du traitement complet
        # (avant, la fonction du bouton executait add_text_watermark en
        # ligne : click_duration aurait ete ~= reference_duration).
        self.assertLess(click_duration, 0.5)
        self.assertLess(click_duration, reference_duration / 4)

        # Le traitement a bel et bien eu lieu (pas juste un no-op) : la duree
        # totale (clic + attente) est du meme ordre de grandeur que la
        # mesure de reference synchrone.
        self.assertGreater(total_duration, reference_duration / 3)

        # La boucle Tk a continue de traiter ses propres evenements
        # planifies (root.after) PENDANT le traitement en arriere-plan :
        # preuve directe que le thread principal n'etait pas bloque a
        # l'interieur de add_text_watermark.
        self.assertGreater(tk_ticks["count"], 5)

        self.assertTrue(output.exists())
        self.assertEqual(len(PdfReader(str(output)).pages), 1200)
        self.assertEqual(len(self.info_messages), 1)
        self.assertIn("1 fichier(s)", self.info_messages[0])


class DpiAwarenessTestCase(unittest.TestCase):
    """Audit, dimension 18 : le processus doit etre rendu explicitement
    Per-Monitor V2 DPI Aware avant toute fenetre Tk, pour eviter un rendu
    flou sur les ecrans a mise a l'echelle superieure a 100% (125%/150%/
    200%, tres courant sur portables/ecrans modernes). Meme pattern deja
    applique et verifie sur les projets GuideExpress et Enveloppe."""

    def test_configure_dpi_awareness_is_idempotent_and_does_not_raise(self):
        # Deja appele une fois a l'import de gui.py (niveau module) : un
        # second appel explicite ne doit rien refaire ni lever.
        gui._configure_dpi_awareness()
        gui._configure_dpi_awareness()
        self.assertTrue(gui._dpi_awareness_configured)

    @unittest.skipUnless(sys.platform == "win32", "verification specifique a l'API Win32")
    def test_process_is_actually_per_monitor_v2_dpi_aware_on_windows(self):
        import ctypes
        gui._configure_dpi_awareness()
        user32 = ctypes.windll.user32
        if not hasattr(user32, "GetThreadDpiAwarenessContext"):
            self.skipTest("GetThreadDpiAwarenessContext indisponible sur ce Windows (trop ancien)")
        current_context = user32.GetThreadDpiAwarenessContext()
        per_monitor_v2 = ctypes.c_void_p(-4)
        is_pm_v2 = bool(user32.AreDpiAwarenessContextsEqual(current_context, per_monitor_v2))
        self.assertTrue(is_pm_v2, "le processus devrait etre Per-Monitor V2 DPI Aware apres _configure_dpi_awareness()")


class CompressionRatioPhraseTestCase(unittest.TestCase):
    """Point 15 de l'audit : CompressionResult.ratio_percent n'a pas de
    plancher a zero - un ratio negatif (fichier recompresse plus gros que
    l'original) doit etre reformule explicitement plutot que colle tel quel
    a cote du mot "reduction" (ex: "-12.3 % de reduction", confus)."""

    def test_positive_ratio_uses_the_reduction_phrasing(self):
        phrase = gui._compression_ratio_phrase(42.5)
        self.assertEqual(phrase, "42.5 % de reduction au total")

    def test_zero_ratio_uses_the_reduction_phrasing(self):
        phrase = gui._compression_ratio_phrase(0.0)
        self.assertEqual(phrase, "0 % de reduction au total")

    def test_negative_ratio_is_reformulated_as_a_growth_instead_of_a_negative_reduction(self):
        phrase = gui._compression_ratio_phrase(-12.3)
        self.assertNotIn("-12.3 % de reduction", phrase)
        self.assertIn("grossi", phrase)
        self.assertIn("+12.3 %", phrase)


class IconTestCase(unittest.TestCase):
    """Point 19 de l'audit : icon.ico ne contenait qu'une seule resolution
    (16x16), rendue floue/pixelisee des qu'agrandie (Explorateur en grandes
    icones, raccourci bureau...). Regenere en multi-resolution via Pillow
    (voir generate_icon.py)."""

    def test_icon_embeds_the_standard_windows_icon_sizes(self):
        from PIL import IcoImagePlugin

        icon_path = Path(__file__).resolve().parent.parent / "icon.ico"
        with open(icon_path, "rb") as f:
            ico = IcoImagePlugin.IcoFile(f)
            sizes = set(ico.sizes())

        for expected in ((16, 16), (32, 32), (48, 48), (256, 256)):
            self.assertIn(expected, sizes)


class VersionInfoTestCase(unittest.TestCase):
    """Point 20 de l'audit : l'executable PyInstaller n'embarquait aucune
    ressource de version Windows (onglet "Details" des proprietes vide).
    version_info.txt (reference par PdfAtelier.spec) doit rester syntaxiquement
    valide et sa version synchronisee avec APP_VERSION a chaque release."""

    def test_version_info_matches_app_version(self):
        version_info_path = Path(__file__).resolve().parent.parent / "version_info.txt"
        text = version_info_path.read_text(encoding="utf-8")
        expected_short = gui.APP_VERSION
        # Les chaines FileVersion/ProductVersion doivent correspondre a
        # APP_VERSION - une desynchronisation ne casserait rien techniquement,
        # mais afficherait un numero de version trompeur dans l'Explorateur.
        self.assertIn(f"u'FileVersion', u'{expected_short}'", text)
        self.assertIn(f"u'ProductVersion', u'{expected_short}'", text)

    def test_version_info_parses_as_a_valid_pyinstaller_version_resource(self):
        try:
            import PyInstaller.utils.win32.versioninfo as vi
        except ImportError:
            self.skipTest("PyInstaller non installe dans cet environnement")

        version_info_path = Path(__file__).resolve().parent.parent / "version_info.txt"
        text = version_info_path.read_text(encoding="utf-8")
        info = eval(text, vars(vi))  # meme mecanisme que PyInstaller lui-meme (voir spec)
        self.assertIsInstance(info, vi.VSVersionInfo)


if __name__ == "__main__":
    unittest.main()
