"""Interface Tkinter de PdfAtelier : fusion, division, gestion des pages,
compression, conversion image/PDF, filigrane, protection par mot de passe et
extraction de texte - tout se passe en local, aucun fichier n'est jamais
envoye a un service en ligne."""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from typing import Optional
from tkinter import (
    BOTH, END, HORIZONTAL, LEFT, RIGHT, TOP, X, Y, VERTICAL,
    BooleanVar, Canvas, IntVar, StringVar, Tk, Toplevel, ttk, messagebox, filedialog,
)

import pdf_ops as ops

APP_TITLE = "PdfAtelier"
DONATE_URL = "https://ko-fi.com/yoshines62000"
PDF_FILETYPES = [("Fichiers PDF", "*.pdf")]
IMAGE_FILETYPES = [("Images", "*.png *.jpg *.jpeg *.bmp *.tiff")]


def _resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def _format_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("o", "Ko", "Mo", "Go"):
        if size < 1024 or unit == "Go":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} Go"


class PdfAtelierApp:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1020x680")

        icon_path = _resource_path("icon.ico")
        if icon_path.exists():
            try:
                self.root.iconbitmap(str(icon_path))
            except Exception:
                pass

        bottom_bar = ttk.Frame(self.root)
        bottom_bar.pack(fill=X, side="bottom")
        donate_label = ttk.Label(bottom_bar, text="☕ Soutenir le projet", foreground="#0645AD", cursor="hand2")
        donate_label.pack(side=RIGHT, padx=8, pady=4)
        donate_label.bind("<Button-1>", lambda event: webbrowser.open(DONATE_URL))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=BOTH, expand=True, padx=8, pady=8)

        self.merge_tab = ttk.Frame(notebook)
        self.split_tab = ttk.Frame(notebook)
        self.pages_tab = ttk.Frame(notebook)
        self.compress_tab = ttk.Frame(notebook)
        self.convert_tab = ttk.Frame(notebook)
        self.watermark_tab = ttk.Frame(notebook)
        self.page_numbers_tab = ttk.Frame(notebook)
        self.protect_tab = ttk.Frame(notebook)
        self.text_tab = ttk.Frame(notebook)

        notebook.add(self.merge_tab, text="Fusionner")
        notebook.add(self.split_tab, text="Diviser")
        notebook.add(self.pages_tab, text="Pages")
        notebook.add(self.compress_tab, text="Compresser")
        notebook.add(self.convert_tab, text="Convertir")
        notebook.add(self.watermark_tab, text="Filigrane")
        notebook.add(self.page_numbers_tab, text="Numeroter")
        notebook.add(self.protect_tab, text="Protection")
        notebook.add(self.text_tab, text="Texte")

        self._build_merge_tab()
        self._build_split_tab()
        self._build_pages_tab()
        self._build_compress_tab()
        self._build_convert_tab()
        self._build_watermark_tab()
        self._build_page_numbers_tab()
        self._build_protect_tab()
        self._build_text_tab()

    # -- utilitaires communs --------------------------------------------------

    def _pick_pdf(self, title="Choisir un fichier PDF"):
        path = filedialog.askopenfilename(title=title, filetypes=PDF_FILETYPES)
        return Path(path) if path else None

    def _pick_pdfs(self, title="Choisir des fichiers PDF"):
        paths = filedialog.askopenfilenames(title=title, filetypes=PDF_FILETYPES)
        return [Path(p) for p in paths]

    def _save_pdf_as(self, initial_name="resultat.pdf"):
        path = filedialog.asksaveasfilename(
            title="Enregistrer sous", initialfile=initial_name, defaultextension=".pdf",
            filetypes=PDF_FILETYPES,
        )
        return Path(path) if path else None

    def _run_safely(self, action, success_message=None):
        """Execute une operation pdf_ops en capturant PdfOpsError (erreur
        metier, message deja clair pour l'utilisateur) separement de toute
        autre exception inattendue (bug, fichier verrouille, disque plein...).
        Toute lecture de champ (IntVar.get(), etc.) doit se faire A
        L'INTERIEUR de `action`, pas avant l'appel : un champ numerique
        contenant du texte non valide leve TclError, qui doit elle aussi
        etre capturee ici plutot que de faire planter le callback."""
        try:
            result = action()
        except ops.PdfOpsError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return None
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Une erreur inattendue s'est produite : {exc}")
            return None
        if success_message:
            messagebox.showinfo(APP_TITLE, success_message)
        return result

    def _prompt_for_password(self, filename: str):
        from tkinter import simpledialog

        return simpledialog.askstring(
            APP_TITLE, f"'{filename}' est protege par un mot de passe.\nMot de passe :", show="*", parent=self.root,
        )

    def _load_page_count_with_password_prompt(self, path: Path):
        """Tente de lire le nombre de pages ; si le fichier est protege,
        demande le mot de passe (une seule relance). Renvoie (nombre_de_pages,
        mot_de_passe_utilise) ou (None, None) si annule/echoue - dans ce cas
        un message a deja ete affiche a l'utilisateur."""
        try:
            return ops.get_page_count(path), None
        except ops.PdfOpsError:
            pass
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Impossible de lire ce PDF : {exc}")
            return None, None

        password = self._prompt_for_password(path.name)
        if not password:
            return None, None
        try:
            return ops.get_page_count(path, password=password), password
        except ops.PdfOpsError as exc:
            messagebox.showwarning(APP_TITLE, str(exc))
            return None, None
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Impossible de lire ce PDF : {exc}")
            return None, None

    @staticmethod
    def _resolve(path) -> Path:
        return Path(path).expanduser().resolve()

    def _warn_if_output_overwrites_source(self, sources, output: Path) -> bool:
        """Renvoie True si l'operation peut se poursuivre. Ecrire le resultat
        par-dessus l'un des fichiers source est desormais techniquement sans
        risque (ecriture atomique), mais reste presque toujours une erreur de
        frappe de l'utilisateur (perte du fichier original) : on demande
        confirmation plutot que de laisser faire silencieusement."""
        output_resolved = self._resolve(output)
        source_list = sources if isinstance(sources, (list, tuple)) else [sources]
        if any(self._resolve(s) == output_resolved for s in source_list if s):
            return messagebox.askyesno(
                APP_TITLE,
                "Le fichier de destination choisi est le meme que le fichier source.\n"
                "Le fichier d'origine sera remplace par le resultat. Continuer ?",
            )
        return True

    @staticmethod
    def _render_pdf_page_image(path, password, page_number: int, rotation: int = 0, scale: float = 0.6):
        """Rendu d'une page en image PIL, reutilise par l'apercu de l'onglet
        Pages et par l'apercu avant fusion."""
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(path), password=password)
        try:
            page = pdf[page_number - 1]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil().rotate(-rotation, expand=True)
            bitmap.close()
            page.close()
            return image
        finally:
            pdf.close()

    @staticmethod
    def _move_listbox_selection(listbox, items: list, delta: int):
        selection = listbox.curselection()
        if not selection:
            return
        index = selection[0]
        new_index = index + delta
        if new_index < 0 or new_index >= len(items):
            return
        items[index], items[new_index] = items[new_index], items[index]
        PdfAtelierApp._reload_listbox(listbox, items)
        listbox.selection_set(new_index)

    @staticmethod
    def _reload_listbox(listbox, items: list):
        listbox.delete(0, END)
        for item in items:
            listbox.insert(END, Path(item).name)

    def _add_pdfs_with_password_prompt(self, existing_paths: list, passwords: dict):
        """Ajoute des fichiers PDF choisis par l'utilisateur a `existing_paths`
        (modifie en place), en demandant un mot de passe immediatement pour
        tout fichier protege et en le stockant dans `passwords` (modifie en
        place, cle = chemin resolu). Un fichier dont le mot de passe est
        annule/incorrect n'est pas ajoute a la liste."""
        for path in self._pick_pdfs():
            count, password = self._load_page_count_with_password_prompt(path)
            if count is None:
                continue
            existing_paths.append(path)
            if password:
                passwords[self._resolve(path)] = password

    @staticmethod
    def _split_dnd_paths(raw_data: str) -> list:
        """Decoupe la chaine event.data fournie par tkinterdnd2 (chemins
        entoures d'accolades s'ils contiennent des espaces, separes par des
        espaces sinon) en une liste de Path.

        N'utilise PAS `widget.tk.splitlist()` : cette fonction Tcl interprete
        les anti-slashs comme des caracteres d'echappement (ex: "\\t" devient
        une tabulation), ce qui corromprait silencieusement un chemin Windows
        parfaitement normal contenant par exemple "...\\Temp\\..." (le "\\t"
        de "Temp\\" serait lu comme une tabulation). Un decoupage manuel,
        respectant uniquement le regroupement par accolades sans aucune
        interpretation des anti-slashs, evite ce risque."""
        paths = []
        i, n = 0, len(raw_data)
        while i < n:
            if raw_data[i] == " ":
                i += 1
                continue
            if raw_data[i] == "{":
                end = raw_data.find("}", i)
                if end == -1:
                    paths.append(raw_data[i + 1:])
                    i = n
                else:
                    paths.append(raw_data[i + 1:end])
                    i = end + 1
            else:
                end = raw_data.find(" ", i)
                if end == -1:
                    end = n
                paths.append(raw_data[i:end])
                i = end
        return [Path(p) for p in paths if p]

    def _handle_pdf_drop(
        self, raw_paths: list, target_list: list, passwords: Optional[dict] = None, prompt_password: bool = True,
    ) -> int:
        """Logique de traitement d'un depose de fichiers PDF, independante du
        widget/evenement : filtre sur l'extension .pdf, demande un mot de
        passe si `prompt_password` (memes regles que _add_pdfs_with_password_
        prompt), ajoute a `target_list`. Renvoie le nombre de fichiers ajoutes."""
        added = 0
        for path in raw_paths:
            if path.suffix.lower() != ".pdf":
                continue
            if prompt_password:
                count, password = self._load_page_count_with_password_prompt(path)
                if count is None:
                    continue
                if password and passwords is not None:
                    passwords[self._resolve(path)] = password
            target_list.append(path)
            added += 1
        return added

    @staticmethod
    def _handle_image_drop(raw_paths: list, target_list: list) -> int:
        """Meme principe que _handle_pdf_drop, pour une liste d'images."""
        image_extensions = {".png", ".jpg", ".jpeg", ".bmp", ".tiff"}
        added = 0
        for path in raw_paths:
            if path.suffix.lower() in image_extensions:
                target_list.append(path)
                added += 1
        return added

    def _register_pdf_drop(
        self, widget, target_list: list, reload_fn, passwords: Optional[dict] = None, prompt_password: bool = True,
    ):
        """Autorise le glisser-depose de fichiers PDF depuis l'Explorateur
        Windows directement sur `widget` (listbox), comme alternative au
        bouton "Ajouter...". Sans effet (ni erreur) si tkinterdnd2 n'est pas
        installe ou si le widget ne supporte pas le drop sur cette
        plateforme - le bouton Ajouter reste toujours disponible.

        prompt_password=False reproduit le comportement des onglets qui ne
        demandent pas le mot de passe a l'ajout (Fusionner, Protection) -
        un fichier protege y est simplement ajoute tel quel, l'echec eventuel
        etant gere plus tard au moment du traitement."""
        try:
            from tkinterdnd2 import DND_FILES
            widget.drop_target_register(DND_FILES)
        except Exception:
            return

        def on_drop(event):
            raw_paths = self._split_dnd_paths(event.data)
            self._handle_pdf_drop(raw_paths, target_list, passwords, prompt_password)
            reload_fn()

        widget.dnd_bind("<<Drop>>", on_drop)

    def _register_image_drop(self, widget, target_list: list, reload_fn):
        """Meme principe que _register_pdf_drop, pour une liste d'images
        (onglet Images vers PDF)."""
        try:
            from tkinterdnd2 import DND_FILES
            widget.drop_target_register(DND_FILES)
        except Exception:
            return

        def on_drop(event):
            raw_paths = self._split_dnd_paths(event.data)
            self._handle_image_drop(raw_paths, target_list)
            reload_fn()

        widget.dnd_bind("<<Drop>>", on_drop)

    def _resolve_batch_outputs(self, sources: list, single_output_initial_name: str, batch_suffix: str):
        """Determine ou enregistrer le(s) resultat(s) : un seul fichier
        choisi explicitement s'il n'y a qu'une seule source (comportement
        historique inchange), ou un dossier + un nom genere par fichier
        source s'il y en a plusieurs. Renvoie une liste de tuples
        (source, output) ou None si l'utilisateur annule."""
        if len(sources) == 1:
            output = self._save_pdf_as(single_output_initial_name)
            if not output:
                return None
            if not self._warn_if_output_overwrites_source(sources, output):
                return None
            return [(sources[0], output)]

        output_dir = filedialog.askdirectory(title="Dossier de destination")
        if not output_dir:
            return None
        # Deux sources peuvent produire le meme nom de fichier de sortie
        # (meme fichier ajoute deux fois via le glisser-depose, ou deux
        # fichiers de meme nom venant de dossiers differents) : sans
        # desambiguisation, la seconde ecraserait silencieusement le
        # resultat de la premiere tout en etant comptee comme un succes a
        # part entiere dans le resume (bug trouve a l'audit).
        pairs = []
        used_outputs = set()
        for src in sources:
            candidate = Path(output_dir) / f"{src.stem}{batch_suffix}.pdf"
            counter = 1
            while candidate in used_outputs:
                candidate = Path(output_dir) / f"{src.stem}{batch_suffix} ({counter}).pdf"
                counter += 1
            used_outputs.add(candidate)
            pairs.append((src, candidate))
        for src, out in pairs:
            if not self._warn_if_output_overwrites_source(sources, out):
                return None
        return pairs

    def _run_batch(self, pairs: list, action_for_pair):
        """Execute action_for_pair(source, output) pour chaque paire, sans
        jamais interrompre les autres fichiers sur l'echec de l'un (chaque
        erreur, attendue ou non, est capturee individuellement). Renvoie
        (succes, echecs) : succes est une liste de (source, output,
        resultat_de_l_action), echecs une liste de (source, message)."""
        successes, failures = [], []
        for source, output in pairs:
            try:
                result = action_for_pair(source, output)
            except ops.PdfOpsError as exc:
                failures.append((source, str(exc)))
            except Exception as exc:
                failures.append((source, f"erreur inattendue : {exc}"))
            else:
                successes.append((source, output, result))
        return successes, failures

    def _show_batch_summary(self, successes: list, failures: list, verb: str):
        lines = [f"{len(successes)} fichier(s) {verb} avec succes."]
        if failures:
            lines.append(f"{len(failures)} echec(s) :")
            for source, message in failures:
                lines.append(f"  - {source.name} : {message}")
        messagebox.showinfo(APP_TITLE, "\n".join(lines))

    # -- onglet Fusionner -------------------------------------------------------

    def _build_merge_tab(self):
        frame = self.merge_tab
        self.merge_files: list = []

        ttk.Label(frame, text="Fichiers a fusionner, dans l'ordre :").pack(anchor="w", padx=10, pady=(10, 0))

        body = ttk.Frame(frame)
        body.pack(fill=BOTH, expand=True, padx=10, pady=5)
        self.merge_listbox = ttk_listbox(body)
        self.merge_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        self.merge_listbox.bind("<<ListboxSelect>>", self._merge_on_select)
        self._register_pdf_drop(
            self.merge_listbox, self.merge_files,
            lambda: self._reload_listbox(self.merge_listbox, self.merge_files), prompt_password=False,
        )

        self.merge_preview_label = ttk.Label(body, text="(apercu)")
        self.merge_preview_label.pack(side=LEFT, padx=10, fill=Y)
        self._merge_preview_image = None  # garde une reference, sinon Tkinter la libere

        buttons = ttk.Frame(body)
        buttons.pack(side=LEFT, fill=Y, padx=(10, 0))
        ttk.Button(buttons, text="Ajouter...", command=self._merge_add_files).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Monter", command=lambda: self._merge_move(-1)).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Descendre", command=lambda: self._merge_move(1)).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Retirer", command=self._merge_remove_selected).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Vider la liste", command=self._merge_clear).pack(fill=X, pady=2)

        ttk.Button(frame, text="Fusionner en un seul PDF...", command=self._merge_run).pack(anchor="w", padx=10, pady=10)

    def _merge_add_files(self):
        paths = self._pick_pdfs()
        self.merge_files.extend(paths)
        self._reload_listbox(self.merge_listbox, self.merge_files)

    def _merge_move(self, delta):
        self._move_listbox_selection(self.merge_listbox, self.merge_files, delta)
        self._merge_on_select()

    def _merge_remove_selected(self):
        selection = self.merge_listbox.curselection()
        if not selection:
            return
        del self.merge_files[selection[0]]
        self._reload_listbox(self.merge_listbox, self.merge_files)
        self.merge_preview_label.configure(text="(apercu)", image="")

    def _merge_clear(self):
        self.merge_files.clear()
        self._reload_listbox(self.merge_listbox, self.merge_files)
        self.merge_preview_label.configure(text="(apercu)", image="")

    def _merge_on_select(self, event=None):
        selection = self.merge_listbox.curselection()
        if not selection or selection[0] >= len(self.merge_files):
            return
        path = self.merge_files[selection[0]]
        try:
            image = self._render_pdf_page_image(path, None, 1)
            from PIL import ImageTk

            image.thumbnail((220, 300))
            photo = ImageTk.PhotoImage(image)
            self._merge_preview_image = photo
            self.merge_preview_label.configure(image=photo, text="")
        except Exception:
            self.merge_preview_label.configure(text="(apercu indisponible)", image="")

    def _merge_run(self):
        if len(self.merge_files) < 2:
            messagebox.showwarning(APP_TITLE, "Ajoutez au moins deux fichiers PDF a fusionner.")
            return
        output = self._save_pdf_as("fusion.pdf")
        if not output:
            return
        if not self._warn_if_output_overwrites_source(self.merge_files, output):
            return
        self._run_safely(lambda: ops.merge_pdfs(self.merge_files, output), f"PDF fusionne enregistre : {output.name}")

    # -- onglet Diviser ---------------------------------------------------------

    def _build_split_tab(self):
        frame = self.split_tab
        self.split_source_var = StringVar(value="Aucun fichier choisi")
        self.split_source_path = None
        self.split_source_password = None
        self.split_mode_var = StringVar(value="ranges")
        self.split_ranges_var = StringVar()
        self.split_every_n_var = StringVar(value="1")

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        ttk.Button(top, text="Choisir un PDF...", command=self._split_pick_source).pack(side=LEFT)
        ttk.Label(top, textvariable=self.split_source_var).pack(side=LEFT, padx=10)

        ttk.Radiobutton(frame, text="Par plages de pages (ex : 1-3,5,7-9)", variable=self.split_mode_var, value="ranges").pack(anchor="w", padx=10, pady=(10, 0))
        ranges_row = ttk.Frame(frame)
        ranges_row.pack(anchor="w", padx=30, fill=X)
        ttk.Entry(ranges_row, textvariable=self.split_ranges_var, width=40).pack(side=LEFT)
        ttk.Button(ranges_row, text="Choisir les pages visuellement...", command=self._split_open_visual_picker).pack(side=LEFT, padx=(10, 0))

        ttk.Radiobutton(frame, text="Toutes les N pages :", variable=self.split_mode_var, value="every_n").pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Entry(frame, textvariable=self.split_every_n_var, width=6).pack(anchor="w", padx=30)

        ttk.Button(frame, text="Diviser...", command=self._split_run).pack(anchor="w", padx=10, pady=15)

    def _split_pick_source(self):
        path = self._pick_pdf()
        if not path:
            return
        count, password = self._load_page_count_with_password_prompt(path)
        if count is None:
            self.split_source_path = None
            return
        self.split_source_path = path
        self.split_source_password = password
        self.split_source_var.set(f"{path.name} ({count} pages)")

    def _parse_ranges(self, text: str, page_count: int):
        ranges = []
        for chunk in text.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "-" in chunk:
                start_text, end_text = chunk.split("-", 1)
                start, end = int(start_text), int(end_text)
            else:
                start = end = int(chunk)
            ranges.append((start, end))
        if not ranges:
            raise ops.PdfOpsError("Indiquez au moins une plage de pages (ex : 1-3,5).")
        return ranges

    @staticmethod
    def _pages_to_range_string(pages) -> str:
        """Compresse une liste de numeros de page (1-indexes, pas forcement
        triee ni sans doublons) vers le format texte attendu par
        `_parse_ranges` (ex : [1, 2, 3, 5, 7, 8] -> "1-3,5,7-8")."""
        ordered = sorted(set(pages))
        if not ordered:
            return ""
        chunks = []
        start = previous = ordered[0]
        for page in ordered[1:]:
            if page == previous + 1:
                previous = page
                continue
            chunks.append(f"{start}-{previous}" if start != previous else str(start))
            start = previous = page
        chunks.append(f"{start}-{previous}" if start != previous else str(start))
        return ",".join(chunks)

    def _split_open_visual_picker(self):
        if not self.split_source_path:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord un fichier PDF.")
            return
        try:
            page_count = ops.get_page_count(self.split_source_path, password=self.split_source_password)
        except Exception as exc:
            messagebox.showerror(APP_TITLE, f"Impossible de lire ce PDF : {exc}")
            return

        dialog = Toplevel(self.root)
        dialog.title("Choisir les pages")
        dialog.transient(self.root)

        body = ttk.Frame(dialog)
        body.pack(fill=BOTH, expand=True, padx=10, pady=10)

        listbox = ttk_listbox(body, height=16, selectmode="extended")
        for page in range(1, page_count + 1):
            listbox.insert(END, f"Page {page}")
        listbox.pack(side=LEFT, fill=BOTH, expand=True)

        preview_label = ttk.Label(body, text="(apercu)")
        preview_label.pack(side=LEFT, padx=10, fill=Y)
        preview_holder = {"image": None}

        def on_select(event=None):
            active = listbox.index("active")
            if active is None or not (0 <= active < page_count):
                return
            try:
                image = self._render_pdf_page_image(self.split_source_path, self.split_source_password, active + 1)
                from PIL import ImageTk

                image.thumbnail((220, 300))
                photo = ImageTk.PhotoImage(image)
                preview_holder["image"] = photo
                preview_label.configure(image=photo, text="")
            except Exception:
                preview_label.configure(text="(apercu indisponible)", image="")

        listbox.bind("<<ListboxSelect>>", on_select)

        def validate():
            selection = listbox.curselection()
            if not selection:
                messagebox.showwarning(APP_TITLE, "Selectionnez au moins une page.", parent=dialog)
                return
            self.split_ranges_var.set(self._pages_to_range_string(index + 1 for index in selection))
            self.split_mode_var.set("ranges")
            dialog.destroy()

        actions = ttk.Frame(dialog)
        actions.pack(fill=X, padx=10, pady=(0, 10))
        ttk.Button(actions, text="Valider la selection", command=validate).pack(side=LEFT)
        ttk.Button(actions, text="Annuler", command=dialog.destroy).pack(side=LEFT, padx=(10, 0))

    def _split_run(self):
        if not self.split_source_path:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord un fichier PDF.")
            return
        output_dir = filedialog.askdirectory(title="Dossier de destination")
        if not output_dir:
            return
        base_name = self.split_source_path.stem

        def action():
            password = self.split_source_password
            if self.split_mode_var.get() == "ranges":
                page_count = ops.get_page_count(self.split_source_path, password=password)
                try:
                    ranges = self._parse_ranges(self.split_ranges_var.get(), page_count)
                except ValueError:
                    raise ops.PdfOpsError("Format de plages invalide. Exemple attendu : 1-3,5,7-9")
                return ops.split_pdf_by_ranges(self.split_source_path, ranges, output_dir, base_name, password=password)
            else:
                try:
                    n = int(self.split_every_n_var.get())
                except ValueError:
                    raise ops.PdfOpsError("Le nombre de pages par fichier doit etre un entier.")
                return ops.split_pdf_every_n_pages(self.split_source_path, n, output_dir, base_name, password=password)

        result = self._run_safely(action)
        if result is not None:
            messagebox.showinfo(APP_TITLE, f"{len(result)} fichier(s) genere(s) dans {output_dir}")

    # -- onglet Pages (reorganiser / pivoter / supprimer) ------------------------

    def _build_pages_tab(self):
        frame = self.pages_tab
        self.pages_source_path = None
        self.pages_source_password = None
        self.page_state: list = []  # [{"page": int, "rotation": int}]

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        ttk.Button(top, text="Choisir un PDF...", command=self._pages_load).pack(side=LEFT)
        self.pages_source_var = StringVar(value="Aucun fichier choisi")
        ttk.Label(top, textvariable=self.pages_source_var).pack(side=LEFT, padx=10)

        body = ttk.Frame(frame)
        body.pack(fill=BOTH, expand=True, padx=10, pady=5)

        self.pages_listbox = ttk_listbox(body, height=20)
        self.pages_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        self.pages_listbox.bind("<<ListboxSelect>>", self._pages_on_select)

        self.pages_preview_label = ttk.Label(body, text="(apercu)")
        self.pages_preview_label.pack(side=LEFT, padx=10, fill=Y)
        self._pages_preview_image = None  # garde une reference, sinon Tkinter la libere

        buttons = ttk.Frame(body)
        buttons.pack(side=LEFT, fill=Y, padx=(10, 0))
        ttk.Button(buttons, text="Monter", command=lambda: self._pages_move(-1)).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Descendre", command=lambda: self._pages_move(1)).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Pivoter 90 deg", command=self._pages_rotate).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Supprimer", command=self._pages_delete).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Tout restaurer", command=self._pages_reset).pack(fill=X, pady=2)

        ttk.Button(frame, text="Enregistrer sous...", command=self._pages_save).pack(anchor="w", padx=10, pady=10)

    def _pages_load(self):
        path = self._pick_pdf()
        if not path:
            return
        count, password = self._load_page_count_with_password_prompt(path)
        if count is None:
            return
        self.pages_source_path = path
        self.pages_source_password = password
        self.pages_source_var.set(f"{path.name} ({count} pages)")
        self.page_state = [{"page": i, "rotation": 0} for i in range(1, count + 1)]
        self._pages_reload_listbox()

    def _pages_reload_listbox(self):
        self.pages_listbox.delete(0, END)
        for entry in self.page_state:
            suffix = f" (pivotee {entry['rotation']} deg)" if entry["rotation"] else ""
            self.pages_listbox.insert(END, f"Page {entry['page']}{suffix}")

    def _pages_on_select(self, event=None):
        selection = self.pages_listbox.curselection()
        if not selection or not self.pages_source_path:
            return
        entry = self.page_state[selection[0]]
        try:
            image = self._render_pdf_page_image(
                self.pages_source_path, self.pages_source_password, entry["page"], entry["rotation"],
            )
            from PIL import ImageTk

            image.thumbnail((260, 340))
            photo = ImageTk.PhotoImage(image)
            self._pages_preview_image = photo
            self.pages_preview_label.configure(image=photo, text="")
        except Exception:
            self.pages_preview_label.configure(text="(apercu indisponible)", image="")

    def _pages_move(self, delta):
        selection = self.pages_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        new_index = index + delta
        if new_index < 0 or new_index >= len(self.page_state):
            return
        self.page_state[index], self.page_state[new_index] = self.page_state[new_index], self.page_state[index]
        self._pages_reload_listbox()
        self.pages_listbox.selection_set(new_index)
        # selection_set() ne declenche pas <<ListboxSelect>> : sans cet appel
        # explicite, l'apercu resterait celui de la derniere ligne reellement
        # cliquee jusqu'au prochain clic (bug trouve a l'audit, deja evite
        # dans _pages_rotate/_pages_delete/_merge_move).
        self._pages_on_select()

    def _pages_rotate(self):
        selection = self.pages_listbox.curselection()
        if not selection:
            return
        entry = self.page_state[selection[0]]
        entry["rotation"] = (entry["rotation"] + 90) % 360
        self._pages_reload_listbox()
        self.pages_listbox.selection_set(selection[0])
        self._pages_on_select()

    def _pages_delete(self):
        selection = self.pages_listbox.curselection()
        if not selection:
            return
        index = selection[0]
        del self.page_state[index]
        self._pages_reload_listbox()
        if self.page_state:
            # Sans reselection, la miniature affichee resterait celle de la
            # page supprimee jusqu'au prochain clic - on pointe donc sur
            # l'element qui occupe maintenant cet index (ou le precedent, en
            # fin de liste).
            new_index = min(index, len(self.page_state) - 1)
            self.pages_listbox.selection_set(new_index)
            self._pages_on_select()
        else:
            self.pages_preview_label.configure(text="(apercu)", image="")

    def _pages_reset(self):
        if not self.pages_source_path:
            return
        count, _ = self._load_page_count_with_password_prompt(self.pages_source_path)
        if count is None:
            return
        self.page_state = [{"page": i, "rotation": 0} for i in range(1, count + 1)]
        self._pages_reload_listbox()

    def _pages_save(self):
        if not self.pages_source_path:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord un fichier PDF.")
            return
        if not self.page_state:
            messagebox.showwarning(APP_TITLE, "Il ne reste plus aucune page a enregistrer.")
            return
        output = self._save_pdf_as("pages_modifiees.pdf")
        if not output:
            return
        if not self._warn_if_output_overwrites_source(self.pages_source_path, output):
            return
        page_order = [entry["page"] for entry in self.page_state]
        rotations = {entry["page"]: entry["rotation"] for entry in self.page_state if entry["rotation"]}
        self._run_safely(
            lambda: ops.reorder_and_filter_pages(
                self.pages_source_path, output, page_order, rotations, password=self.pages_source_password
            ),
            f"Document enregistre : {output.name}",
        )

    # -- onglet Compresser --------------------------------------------------------

    def _build_compress_tab(self):
        frame = self.compress_tab
        self.compress_sources: list = []
        self.compress_passwords: dict = {}
        self.compress_quality_var = IntVar(value=60)
        self.compress_max_dim_var = IntVar(value=1600)
        self.compress_result_var = StringVar(value="")

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        self.compress_listbox = ttk_listbox(top, height=5)
        self.compress_listbox.pack(side=LEFT, fill=X, expand=True)
        self._register_pdf_drop(
            self.compress_listbox, self.compress_sources,
            lambda: self._reload_listbox(self.compress_listbox, self.compress_sources), self.compress_passwords,
        )
        buttons = ttk.Frame(top)
        buttons.pack(side=LEFT, padx=(10, 0))
        ttk.Button(buttons, text="Ajouter...", command=self._compress_add_files).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Retirer", command=self._compress_remove_selected).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Vider", command=self._compress_clear).pack(fill=X, pady=2)

        ttk.Label(frame, text="Qualite des images (1 = tres compresse, 95 = quasi sans perte)").pack(anchor="w", padx=10, pady=(15, 0))
        ttk.Scale(frame, from_=1, to=95, orient=HORIZONTAL, variable=self.compress_quality_var, length=300).pack(anchor="w", padx=10)

        ttk.Label(frame, text="Dimension maximale des images (pixels)").pack(anchor="w", padx=10, pady=(15, 0))
        ttk.Entry(frame, textvariable=self.compress_max_dim_var, width=10).pack(anchor="w", padx=10)

        ttk.Button(frame, text="Compresser...", command=self._compress_run).pack(anchor="w", padx=10, pady=15)
        ttk.Label(frame, textvariable=self.compress_result_var).pack(anchor="w", padx=10)

    def _compress_add_files(self):
        self._add_pdfs_with_password_prompt(self.compress_sources, self.compress_passwords)
        self._reload_listbox(self.compress_listbox, self.compress_sources)
        self.compress_result_var.set("")

    def _compress_remove_selected(self):
        selection = self.compress_listbox.curselection()
        if not selection:
            return
        del self.compress_sources[selection[0]]
        self._reload_listbox(self.compress_listbox, self.compress_sources)

    def _compress_clear(self):
        self.compress_sources.clear()
        self.compress_passwords.clear()
        self._reload_listbox(self.compress_listbox, self.compress_sources)

    def _compress_run(self):
        if not self.compress_sources:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord au moins un fichier PDF.")
            return
        pairs = self._resolve_batch_outputs(self.compress_sources, "compresse.pdf", "_compresse")
        if pairs is None:
            return

        try:
            # Lues une seule fois ici (pas par fichier) et non avant l'appel :
            # un champ non numerique leverait TclError.
            quality = self.compress_quality_var.get()
            max_dim = self.compress_max_dim_var.get()
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, f"Reglages invalides : {exc}")
            return

        if len(pairs) == 1:
            source, output = pairs[0]
            result = self._run_safely(
                lambda: ops.compress_pdf(
                    source, output, image_quality=quality, max_dimension=max_dim,
                    password=self.compress_passwords.get(self._resolve(source)),
                )
            )
            if result is not None:
                summary = (
                    f"{_format_size(result.original_size)} -> {_format_size(result.compressed_size)} "
                    f"({result.ratio_percent:g} % de reduction)"
                )
                if result.images_failed:
                    summary += (
                        f" - {result.images_failed}/{result.images_total} image(s) n'ont pas pu etre "
                        "recompressees (format non supporte) et ont ete conservees telles quelles"
                    )
                self.compress_result_var.set(summary)
                messagebox.showinfo(APP_TITLE, f"PDF compresse enregistre : {output.name}")
            return

        successes, failures = self._run_batch(
            pairs,
            lambda source, output: ops.compress_pdf(
                source, output, image_quality=quality, max_dimension=max_dim,
                password=self.compress_passwords.get(self._resolve(source)),
            ),
        )
        if successes:
            total_original = sum(r.original_size for _, _, r in successes)
            total_compressed = sum(r.compressed_size for _, _, r in successes)
            ratio = round(100 * (1 - total_compressed / total_original), 1) if total_original else 0.0
            self.compress_result_var.set(
                f"{_format_size(total_original)} -> {_format_size(total_compressed)} ({ratio:g} % de reduction au total)"
            )
        self._show_batch_summary(successes, failures, "compresse(s)")

    # -- onglet Convertir -----------------------------------------------------------

    def _build_convert_tab(self):
        frame = self.convert_tab

        pdf_to_img = ttk.LabelFrame(frame, text="PDF vers images")
        pdf_to_img.pack(fill=X, padx=10, pady=10)
        self.p2i_source_path = None
        self.p2i_source_var = StringVar(value="Aucun fichier choisi")
        self.p2i_dpi_var = IntVar(value=150)
        self.p2i_format_var = StringVar(value="png")

        top = ttk.Frame(pdf_to_img)
        top.pack(fill=X, padx=5, pady=5)
        ttk.Button(top, text="Choisir un PDF...", command=self._p2i_pick_source).pack(side=LEFT)
        ttk.Label(top, textvariable=self.p2i_source_var).pack(side=LEFT, padx=10)

        options = ttk.Frame(pdf_to_img)
        options.pack(fill=X, padx=5, pady=5)
        ttk.Label(options, text="Resolution (DPI)").pack(side=LEFT)
        ttk.Entry(options, textvariable=self.p2i_dpi_var, width=6).pack(side=LEFT, padx=5)
        ttk.Label(options, text="Format").pack(side=LEFT, padx=(15, 0))
        ttk.Combobox(options, textvariable=self.p2i_format_var, values=["png", "jpg"], width=6, state="readonly").pack(side=LEFT, padx=5)
        ttk.Button(pdf_to_img, text="Convertir en images...", command=self._p2i_run).pack(anchor="w", padx=5, pady=5)

        extract_img = ttk.LabelFrame(frame, text="Extraire les images embarquees")
        extract_img.pack(fill=X, padx=10, pady=(0, 10))
        self.eei_source_path = None
        self.eei_source_var = StringVar(value="Aucun fichier choisi")
        self.eei_password_var = StringVar()

        eei_top = ttk.Frame(extract_img)
        eei_top.pack(fill=X, padx=5, pady=5)
        ttk.Button(eei_top, text="Choisir un PDF...", command=self._eei_pick_source).pack(side=LEFT)
        ttk.Label(eei_top, textvariable=self.eei_source_var).pack(side=LEFT, padx=10)
        ttk.Label(eei_top, text="Mot de passe (si protege)").pack(side=LEFT, padx=(15, 0))
        ttk.Entry(eei_top, textvariable=self.eei_password_var, show="*", width=16).pack(side=LEFT, padx=5)
        ttk.Label(
            extract_img,
            text="Recupere les photos/logos tels qu'embarques dans le PDF, sans rasteriser la page entiere.",
            foreground="#666",
        ).pack(anchor="w", padx=5)
        ttk.Button(extract_img, text="Extraire les images...", command=self._eei_run).pack(anchor="w", padx=5, pady=5)

        img_to_pdf = ttk.LabelFrame(frame, text="Images vers PDF")
        img_to_pdf.pack(fill=BOTH, expand=True, padx=10, pady=10)
        self.i2p_files: list = []

        body = ttk.Frame(img_to_pdf)
        body.pack(fill=BOTH, expand=True, padx=5, pady=5)
        self.i2p_listbox = ttk_listbox(body)
        self.i2p_listbox.pack(side=LEFT, fill=BOTH, expand=True)
        self._register_image_drop(
            self.i2p_listbox, self.i2p_files, lambda: self._reload_listbox(self.i2p_listbox, self.i2p_files),
        )

        buttons = ttk.Frame(body)
        buttons.pack(side=LEFT, fill=Y, padx=(10, 0))
        ttk.Button(buttons, text="Ajouter...", command=self._i2p_add_files).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Monter", command=lambda: self._i2p_move(-1)).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Descendre", command=lambda: self._i2p_move(1)).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Retirer", command=self._i2p_remove_selected).pack(fill=X, pady=2)

        ttk.Button(img_to_pdf, text="Assembler en PDF...", command=self._i2p_run).pack(anchor="w", padx=5, pady=5)

    def _p2i_pick_source(self):
        path = self._pick_pdf()
        if not path:
            return
        self.p2i_source_path = path
        self.p2i_source_var.set(path.name)

    def _p2i_run(self):
        if not self.p2i_source_path:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord un fichier PDF.")
            return
        output_dir = filedialog.askdirectory(title="Dossier de destination")
        if not output_dir:
            return
        base_name = self.p2i_source_path.stem

        def action():
            # Lus ici, pas avant : un DPI non numerique leverait TclError,
            # capturee par _run_safely plutot que de faire planter le
            # callback silencieusement.
            dpi = self.p2i_dpi_var.get()
            fmt = self.p2i_format_var.get()
            return ops.pdf_to_images(self.p2i_source_path, output_dir, base_name, dpi=dpi, fmt=fmt)

        result = self._run_safely(action)
        if result is not None:
            messagebox.showinfo(APP_TITLE, f"{len(result)} image(s) generee(s) dans {output_dir}")

    def _eei_pick_source(self):
        path = self._pick_pdf()
        if not path:
            return
        self.eei_source_path = path
        self.eei_source_var.set(path.name)

    def _eei_run(self):
        if not self.eei_source_path:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord un fichier PDF.")
            return
        output_dir = filedialog.askdirectory(title="Dossier de destination")
        if not output_dir:
            return
        base_name = self.eei_source_path.stem
        password = self.eei_password_var.get() or None

        result = self._run_safely(
            lambda: ops.extract_embedded_images(self.eei_source_path, output_dir, base_name, password=password)
        )
        if result is not None:
            if not result:
                messagebox.showinfo(APP_TITLE, "Aucune image embarquee trouvee dans ce PDF.")
            else:
                messagebox.showinfo(APP_TITLE, f"{len(result)} image(s) extraite(s) dans {output_dir}")

    def _i2p_add_files(self):
        paths = filedialog.askopenfilenames(title="Choisir des images", filetypes=IMAGE_FILETYPES)
        self.i2p_files.extend(Path(p) for p in paths)
        self._reload_listbox(self.i2p_listbox, self.i2p_files)

    def _i2p_move(self, delta):
        self._move_listbox_selection(self.i2p_listbox, self.i2p_files, delta)

    def _i2p_remove_selected(self):
        selection = self.i2p_listbox.curselection()
        if not selection:
            return
        del self.i2p_files[selection[0]]
        self._reload_listbox(self.i2p_listbox, self.i2p_files)

    def _i2p_run(self):
        if not self.i2p_files:
            messagebox.showwarning(APP_TITLE, "Ajoutez au moins une image.")
            return
        output = self._save_pdf_as("images.pdf")
        if not output:
            return
        self._run_safely(lambda: ops.images_to_pdf(self.i2p_files, output), f"PDF genere : {output.name}")

    # -- onglet Filigrane -----------------------------------------------------------

    def _build_watermark_tab(self):
        frame = self.watermark_tab
        self.watermark_sources: list = []
        self.watermark_passwords: dict = {}
        self.watermark_text_var = StringVar(value="CONFIDENTIEL")
        self.watermark_opacity_var = IntVar(value=30)
        self.watermark_angle_var = IntVar(value=45)
        self.watermark_size_var = IntVar(value=40)

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        self.watermark_listbox = ttk_listbox(top, height=5)
        self.watermark_listbox.pack(side=LEFT, fill=X, expand=True)
        self._register_pdf_drop(
            self.watermark_listbox, self.watermark_sources,
            lambda: self._reload_listbox(self.watermark_listbox, self.watermark_sources), self.watermark_passwords,
        )
        buttons = ttk.Frame(top)
        buttons.pack(side=LEFT, padx=(10, 0))
        ttk.Button(buttons, text="Ajouter...", command=self._watermark_add_files).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Retirer", command=self._watermark_remove_selected).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Vider", command=self._watermark_clear).pack(fill=X, pady=2)

        ttk.Label(frame, text="Texte du filigrane").pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Entry(frame, textvariable=self.watermark_text_var, width=40).pack(anchor="w", padx=10)

        ttk.Label(frame, text="Opacite (%)").pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Scale(frame, from_=5, to=100, orient=HORIZONTAL, variable=self.watermark_opacity_var, length=300).pack(anchor="w", padx=10)

        row = ttk.Frame(frame)
        row.pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Label(row, text="Angle (deg)").pack(side=LEFT)
        ttk.Entry(row, textvariable=self.watermark_angle_var, width=6).pack(side=LEFT, padx=5)
        ttk.Label(row, text="Taille de police").pack(side=LEFT, padx=(15, 0))
        ttk.Entry(row, textvariable=self.watermark_size_var, width=6).pack(side=LEFT, padx=5)

        ttk.Button(frame, text="Appliquer le filigrane...", command=self._watermark_run).pack(anchor="w", padx=10, pady=15)

    def _watermark_add_files(self):
        self._add_pdfs_with_password_prompt(self.watermark_sources, self.watermark_passwords)
        self._reload_listbox(self.watermark_listbox, self.watermark_sources)

    def _watermark_remove_selected(self):
        selection = self.watermark_listbox.curselection()
        if not selection:
            return
        del self.watermark_sources[selection[0]]
        self._reload_listbox(self.watermark_listbox, self.watermark_sources)

    def _watermark_clear(self):
        self.watermark_sources.clear()
        self.watermark_passwords.clear()
        self._reload_listbox(self.watermark_listbox, self.watermark_sources)

    def _watermark_run(self):
        if not self.watermark_sources:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord au moins un fichier PDF.")
            return
        text = self.watermark_text_var.get().strip()
        if not text:
            messagebox.showwarning(APP_TITLE, "Le texte du filigrane ne peut pas etre vide.")
            return
        pairs = self._resolve_batch_outputs(self.watermark_sources, "filigrane.pdf", "_filigrane")
        if pairs is None:
            return

        try:
            opacity = self.watermark_opacity_var.get() / 100.0
            font_size = self.watermark_size_var.get()
            angle = self.watermark_angle_var.get()
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, f"Reglages invalides : {exc}")
            return

        def make_action(source, output):
            return ops.add_text_watermark(
                source, output, text, opacity=opacity, font_size=font_size, angle=angle,
                password=self.watermark_passwords.get(self._resolve(source)),
            )

        if len(pairs) == 1:
            source, output = pairs[0]
            self._run_safely(lambda: make_action(source, output), f"Filigrane applique : {output.name}")
            return

        successes, failures = self._run_batch(pairs, make_action)
        self._show_batch_summary(successes, failures, "traite(s) (filigrane applique)")

    # -- onglet Numeroter ---------------------------------------------------------------

    def _build_page_numbers_tab(self):
        frame = self.page_numbers_tab
        self.page_numbers_sources: list = []
        self.page_numbers_passwords: dict = {}
        self.page_numbers_position_var = StringVar(value="bas-centre")
        self.page_numbers_start_var = IntVar(value=1)
        self.page_numbers_format_var = StringVar(value="{page} / {total}")
        self.page_numbers_size_var = IntVar(value=10)

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        self.page_numbers_listbox = ttk_listbox(top, height=5)
        self.page_numbers_listbox.pack(side=LEFT, fill=X, expand=True)
        self._register_pdf_drop(
            self.page_numbers_listbox, self.page_numbers_sources,
            lambda: self._reload_listbox(self.page_numbers_listbox, self.page_numbers_sources),
            self.page_numbers_passwords,
        )
        buttons = ttk.Frame(top)
        buttons.pack(side=LEFT, padx=(10, 0))
        ttk.Button(buttons, text="Ajouter...", command=self._page_numbers_add_files).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Retirer", command=self._page_numbers_remove_selected).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Vider", command=self._page_numbers_clear).pack(fill=X, pady=2)

        row1 = ttk.Frame(frame)
        row1.pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Label(row1, text="Position").pack(side=LEFT)
        ttk.Combobox(
            row1, textvariable=self.page_numbers_position_var, state="readonly", width=14,
            values=["bas-centre", "bas-droite", "bas-gauche", "haut-centre", "haut-droite", "haut-gauche"],
        ).pack(side=LEFT, padx=5)
        ttk.Label(row1, text="Commencer a").pack(side=LEFT, padx=(15, 0))
        ttk.Entry(row1, textvariable=self.page_numbers_start_var, width=6).pack(side=LEFT, padx=5)
        ttk.Label(row1, text="Taille de police").pack(side=LEFT, padx=(15, 0))
        ttk.Entry(row1, textvariable=self.page_numbers_size_var, width=6).pack(side=LEFT, padx=5)

        row2 = ttk.Frame(frame)
        row2.pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Label(row2, text="Format ({page} et {total} disponibles)").pack(side=LEFT)
        ttk.Entry(row2, textvariable=self.page_numbers_format_var, width=20).pack(side=LEFT, padx=5)

        ttk.Button(frame, text="Numeroter les pages...", command=self._page_numbers_run).pack(anchor="w", padx=10, pady=15)

    def _page_numbers_add_files(self):
        self._add_pdfs_with_password_prompt(self.page_numbers_sources, self.page_numbers_passwords)
        self._reload_listbox(self.page_numbers_listbox, self.page_numbers_sources)

    def _page_numbers_remove_selected(self):
        selection = self.page_numbers_listbox.curselection()
        if not selection:
            return
        del self.page_numbers_sources[selection[0]]
        self._reload_listbox(self.page_numbers_listbox, self.page_numbers_sources)

    def _page_numbers_clear(self):
        self.page_numbers_sources.clear()
        self.page_numbers_passwords.clear()
        self._reload_listbox(self.page_numbers_listbox, self.page_numbers_sources)

    def _page_numbers_run(self):
        if not self.page_numbers_sources:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord au moins un fichier PDF.")
            return
        fmt = self.page_numbers_format_var.get().strip()
        if not fmt:
            messagebox.showwarning(APP_TITLE, "Le format ne peut pas etre vide.")
            return
        try:
            fmt.format(page=1, total=1)
        except (KeyError, ValueError, IndexError, TypeError, AttributeError) as exc:
            messagebox.showwarning(APP_TITLE, f"Format invalide : {exc}")
            return
        pairs = self._resolve_batch_outputs(self.page_numbers_sources, "numerote.pdf", "_numerote")
        if pairs is None:
            return

        try:
            start_at = self.page_numbers_start_var.get()
            font_size = self.page_numbers_size_var.get()
        except Exception as exc:
            messagebox.showwarning(APP_TITLE, f"Reglages invalides : {exc}")
            return
        position = self.page_numbers_position_var.get()

        def make_action(source, output):
            return ops.add_page_numbers(
                source, output, position=position, start_at=start_at, font_size=font_size, fmt=fmt,
                password=self.page_numbers_passwords.get(self._resolve(source)),
            )

        if len(pairs) == 1:
            source, output = pairs[0]
            self._run_safely(lambda: make_action(source, output), f"Pages numerotees : {output.name}")
            return

        successes, failures = self._run_batch(pairs, make_action)
        self._show_batch_summary(successes, failures, "traite(s) (pages numerotees)")

    # -- onglet Protection ------------------------------------------------------------

    def _build_protect_tab(self):
        frame = self.protect_tab
        self.protect_sources: list = []
        self.protect_mode_var = StringVar(value="add")
        self.protect_password_var = StringVar()
        self.protect_confirm_var = StringVar()

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        self.protect_listbox = ttk_listbox(top, height=5)
        self.protect_listbox.pack(side=LEFT, fill=X, expand=True)
        self._register_pdf_drop(
            self.protect_listbox, self.protect_sources,
            lambda: self._reload_listbox(self.protect_listbox, self.protect_sources), prompt_password=False,
        )
        buttons = ttk.Frame(top)
        buttons.pack(side=LEFT, padx=(10, 0))
        ttk.Button(buttons, text="Ajouter...", command=self._protect_add_files).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Retirer", command=self._protect_remove_selected).pack(fill=X, pady=2)
        ttk.Button(buttons, text="Vider", command=self._protect_clear).pack(fill=X, pady=2)

        ttk.Radiobutton(frame, text="Ajouter un mot de passe", variable=self.protect_mode_var, value="add").pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Radiobutton(frame, text="Retirer le mot de passe", variable=self.protect_mode_var, value="remove").pack(anchor="w", padx=10)

        ttk.Label(frame, text="Mot de passe").pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Entry(frame, textvariable=self.protect_password_var, show="*", width=30).pack(anchor="w", padx=10)

        self.protect_confirm_label = ttk.Label(frame, text="Confirmer le mot de passe")
        self.protect_confirm_label.pack(anchor="w", padx=10, pady=(10, 0))
        self.protect_confirm_entry = ttk.Entry(frame, textvariable=self.protect_confirm_var, show="*", width=30)
        self.protect_confirm_entry.pack(anchor="w", padx=10)

        ttk.Label(
            frame, text="En mode lot (plusieurs fichiers), le meme mot de passe est applique/retire sur chacun.",
            foreground="#666",
        ).pack(anchor="w", padx=10, pady=(10, 0))

        ttk.Button(frame, text="Appliquer...", command=self._protect_run).pack(anchor="w", padx=10, pady=15)

    def _protect_add_files(self):
        self.protect_sources.extend(self._pick_pdfs())
        self._reload_listbox(self.protect_listbox, self.protect_sources)

    def _protect_remove_selected(self):
        selection = self.protect_listbox.curselection()
        if not selection:
            return
        del self.protect_sources[selection[0]]
        self._reload_listbox(self.protect_listbox, self.protect_sources)

    def _protect_clear(self):
        self.protect_sources.clear()
        self._reload_listbox(self.protect_listbox, self.protect_sources)

    def _protect_run(self):
        if not self.protect_sources:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord au moins un fichier PDF.")
            return
        password = self.protect_password_var.get()
        mode = self.protect_mode_var.get()

        if mode == "add":
            if password != self.protect_confirm_var.get():
                messagebox.showwarning(APP_TITLE, "Les deux mots de passe ne correspondent pas.")
                return
            pairs = self._resolve_batch_outputs(self.protect_sources, "protege.pdf", "_protege")
            if pairs is None:
                return
            make_action = lambda source, output: ops.set_password(source, output, password)
            success_verb = "protege(s)"
            single_message = lambda output: f"PDF protege enregistre : {output.name}"
        else:
            pairs = self._resolve_batch_outputs(self.protect_sources, "sans_mot_de_passe.pdf", "_sans_mot_de_passe")
            if pairs is None:
                return
            make_action = lambda source, output: ops.remove_password(source, output, password)
            success_verb = "deprotege(s)"
            single_message = lambda output: f"Protection retiree : {output.name}"

        if len(pairs) == 1:
            source, output = pairs[0]
            self._run_safely(lambda: make_action(source, output), single_message(output))
            return

        successes, failures = self._run_batch(pairs, make_action)
        self._show_batch_summary(successes, failures, success_verb)

    # -- onglet Texte -------------------------------------------------------------------

    def _build_text_tab(self):
        frame = self.text_tab
        self.text_source_path = None
        self.text_source_var = StringVar(value="Aucun fichier choisi")
        self.text_password_var = StringVar()

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        ttk.Button(top, text="Choisir un PDF...", command=self._text_pick_source).pack(side=LEFT)
        ttk.Label(top, textvariable=self.text_source_var).pack(side=LEFT, padx=10)
        ttk.Label(top, text="Mot de passe (si protege)").pack(side=LEFT, padx=(15, 0))
        ttk.Entry(top, textvariable=self.text_password_var, show="*", width=20).pack(side=LEFT, padx=5)
        ttk.Button(top, text="Extraire", command=self._text_run).pack(side=LEFT, padx=10)

        self.text_search_var = StringVar()
        self.text_search_status_var = StringVar(value="")
        self._text_search_matches: list = []
        self._text_search_index = -1
        self._text_search_query_length = 0

        search_row = ttk.Frame(frame)
        search_row.pack(fill=X, padx=10, pady=(0, 5))
        ttk.Label(search_row, text="Rechercher :").pack(side=LEFT)
        search_entry = ttk.Entry(search_row, textvariable=self.text_search_var, width=30)
        search_entry.pack(side=LEFT, padx=5)
        search_entry.bind("<Return>", lambda event: self._text_search_run())
        ttk.Button(search_row, text="Chercher", command=self._text_search_run).pack(side=LEFT)
        ttk.Button(search_row, text="Precedent", command=lambda: self._text_search_step(-1)).pack(side=LEFT, padx=(10, 0))
        ttk.Button(search_row, text="Suivant", command=lambda: self._text_search_step(1)).pack(side=LEFT, padx=(5, 0))
        ttk.Label(search_row, textvariable=self.text_search_status_var).pack(side=LEFT, padx=10)

        body = ttk.Frame(frame)
        body.pack(fill=BOTH, expand=True, padx=10, pady=5)
        scrollbar = ttk.Scrollbar(body, orient=VERTICAL)
        from tkinter import Text
        self.text_output = Text(body, wrap="word", yscrollcommand=scrollbar.set)
        self.text_output.tag_configure("search_match", background="#fff59d")
        self.text_output.tag_configure("search_current", background="#ffb300")
        scrollbar.config(command=self.text_output.yview)
        self.text_output.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        ttk.Button(frame, text="Enregistrer en .txt...", command=self._text_save).pack(anchor="w", padx=10, pady=10)

    def _text_pick_source(self):
        path = self._pick_pdf()
        if not path:
            return
        self.text_source_path = path
        self.text_source_var.set(path.name)

    def _text_run(self):
        if not self.text_source_path:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord un fichier PDF.")
            return
        password = self.text_password_var.get() or None
        result = self._run_safely(lambda: ops.extract_text(self.text_source_path, password=password))
        if result is not None:
            self.text_output.delete("1.0", END)
            for index, page_text in enumerate(result, start=1):
                self.text_output.insert(END, f"--- Page {index} ---\n{page_text}\n\n")
            self._text_search_matches = []
            self._text_search_index = -1
            self.text_search_status_var.set("")

    def _text_search_run(self):
        self.text_output.tag_remove("search_match", "1.0", END)
        self.text_output.tag_remove("search_current", "1.0", END)
        self._text_search_matches = []
        self._text_search_index = -1
        query = self.text_search_var.get()
        if not query:
            self.text_search_status_var.set("")
            return
        # Fige la longueur de la requete au moment de la recherche : si
        # l'utilisateur retape autre chose dans le champ SANS relancer la
        # recherche (ex: avant de cliquer Suivant/Precedent), le surlignage
        # ne doit pas se mettre a utiliser cette nouvelle longueur pour des
        # positions trouvees avec l'ancienne requete (bug trouve a l'audit).
        self._text_search_query_length = len(query)
        start = "1.0"
        while True:
            pos = self.text_output.search(query, start, stopindex=END, nocase=True)
            if not pos:
                break
            end = f"{pos}+{len(query)}c"
            self.text_output.tag_add("search_match", pos, end)
            self._text_search_matches.append(pos)
            start = end
        if self._text_search_matches:
            self._text_search_index = 0
            self._text_search_highlight_current()
        else:
            self.text_search_status_var.set("Aucun resultat")

    def _text_search_highlight_current(self):
        self.text_output.tag_remove("search_current", "1.0", END)
        if not (0 <= self._text_search_index < len(self._text_search_matches)):
            return
        pos = self._text_search_matches[self._text_search_index]
        end = f"{pos}+{self._text_search_query_length}c"
        self.text_output.tag_add("search_current", pos, end)
        self.text_output.see(pos)
        self.text_search_status_var.set(f"{self._text_search_index + 1}/{len(self._text_search_matches)}")

    def _text_search_step(self, delta: int):
        if not self._text_search_matches:
            self._text_search_run()
            if not self._text_search_matches:
                return
            return
        self._text_search_index = (self._text_search_index + delta) % len(self._text_search_matches)
        self._text_search_highlight_current()

    def _text_save(self):
        content = self.text_output.get("1.0", END).strip()
        if not content:
            messagebox.showinfo(APP_TITLE, "Rien a enregistrer : extrayez d'abord le texte.")
            return
        output = filedialog.asksaveasfilename(
            title="Enregistrer le texte", initialfile="texte_extrait.txt", defaultextension=".txt",
            filetypes=[("Fichier texte", "*.txt")],
        )
        if not output:
            return
        Path(output).write_text(content, encoding="utf-8")
        messagebox.showinfo(APP_TITLE, f"Texte enregistre : {Path(output).name}")


def ttk_listbox(parent, height=12, selectmode="browse"):
    from tkinter import Listbox

    listbox = Listbox(parent, height=height, exportselection=False, selectmode=selectmode)
    return listbox


def main():
    try:
        # TkinterDnD.Tk() est un Tk normal auquel s'ajoute le support du
        # glisser-depose de fichiers (drop_target_register/dnd_bind) : sans
        # lui, ces methodes n'existent pas sur les widgets et le glisser-
        # depose est silencieusement ignore (voir _register_pdf_drop). Si
        # le paquet n'est pas installe, on retombe sur un Tk standard - seul
        # le glisser-depose est indisponible, tout le reste fonctionne.
        from tkinterdnd2 import TkinterDnD
        root = TkinterDnD.Tk()
    except ImportError:
        root = Tk()
    PdfAtelierApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
