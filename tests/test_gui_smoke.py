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

import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pypdf import PdfReader
from reportlab.pdfgen import canvas

import gui
import pdf_ops as ops
from test_pdf_ops import make_pdf  # reutilise le generateur de PDF de test deja etabli

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


if __name__ == "__main__":
    unittest.main()
