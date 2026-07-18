"""Interface Tkinter de PdfAtelier : fusion, division, gestion des pages,
compression, conversion image/PDF, filigrane, protection par mot de passe et
extraction de texte - tout se passe en local, aucun fichier n'est jamais
envoye a un service en ligne."""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path
from tkinter import (
    BOTH, END, HORIZONTAL, LEFT, RIGHT, TOP, X, Y, VERTICAL,
    BooleanVar, Canvas, IntVar, StringVar, Tk, ttk, messagebox, filedialog,
)

import pdf_ops as ops

APP_TITLE = "PdfAtelier"
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

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=BOTH, expand=True, padx=8, pady=8)

        self.merge_tab = ttk.Frame(notebook)
        self.split_tab = ttk.Frame(notebook)
        self.pages_tab = ttk.Frame(notebook)
        self.compress_tab = ttk.Frame(notebook)
        self.convert_tab = ttk.Frame(notebook)
        self.watermark_tab = ttk.Frame(notebook)
        self.protect_tab = ttk.Frame(notebook)
        self.text_tab = ttk.Frame(notebook)

        notebook.add(self.merge_tab, text="Fusionner")
        notebook.add(self.split_tab, text="Diviser")
        notebook.add(self.pages_tab, text="Pages")
        notebook.add(self.compress_tab, text="Compresser")
        notebook.add(self.convert_tab, text="Convertir")
        notebook.add(self.watermark_tab, text="Filigrane")
        notebook.add(self.protect_tab, text="Protection")
        notebook.add(self.text_tab, text="Texte")

        self._build_merge_tab()
        self._build_split_tab()
        self._build_pages_tab()
        self._build_compress_tab()
        self._build_convert_tab()
        self._build_watermark_tab()
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

    # -- onglet Fusionner -------------------------------------------------------

    def _build_merge_tab(self):
        frame = self.merge_tab
        self.merge_files: list = []

        ttk.Label(frame, text="Fichiers a fusionner, dans l'ordre :").pack(anchor="w", padx=10, pady=(10, 0))

        body = ttk.Frame(frame)
        body.pack(fill=BOTH, expand=True, padx=10, pady=5)
        self.merge_listbox = ttk_listbox(body)
        self.merge_listbox.pack(side=LEFT, fill=BOTH, expand=True)

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

    def _merge_remove_selected(self):
        selection = self.merge_listbox.curselection()
        if not selection:
            return
        del self.merge_files[selection[0]]
        self._reload_listbox(self.merge_listbox, self.merge_files)

    def _merge_clear(self):
        self.merge_files.clear()
        self._reload_listbox(self.merge_listbox, self.merge_files)

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
        ttk.Entry(frame, textvariable=self.split_ranges_var, width=40).pack(anchor="w", padx=30)

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
            import pypdfium2 as pdfium

            pdf = pdfium.PdfDocument(str(self.pages_source_path), password=self.pages_source_password)
            try:
                page = pdf[entry["page"] - 1]
                bitmap = page.render(scale=0.6)
                image = bitmap.to_pil().rotate(-entry["rotation"], expand=True)
                bitmap.close()
                page.close()
            finally:
                pdf.close()
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
        self.compress_source_path = None
        self.compress_source_password = None
        self.compress_source_var = StringVar(value="Aucun fichier choisi")
        self.compress_quality_var = IntVar(value=60)
        self.compress_max_dim_var = IntVar(value=1600)
        self.compress_result_var = StringVar(value="")

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        ttk.Button(top, text="Choisir un PDF...", command=self._compress_pick_source).pack(side=LEFT)
        ttk.Label(top, textvariable=self.compress_source_var).pack(side=LEFT, padx=10)

        ttk.Label(frame, text="Qualite des images (1 = tres compresse, 95 = quasi sans perte)").pack(anchor="w", padx=10, pady=(15, 0))
        ttk.Scale(frame, from_=1, to=95, orient=HORIZONTAL, variable=self.compress_quality_var, length=300).pack(anchor="w", padx=10)

        ttk.Label(frame, text="Dimension maximale des images (pixels)").pack(anchor="w", padx=10, pady=(15, 0))
        ttk.Entry(frame, textvariable=self.compress_max_dim_var, width=10).pack(anchor="w", padx=10)

        ttk.Button(frame, text="Compresser...", command=self._compress_run).pack(anchor="w", padx=10, pady=15)
        ttk.Label(frame, textvariable=self.compress_result_var).pack(anchor="w", padx=10)

    def _compress_pick_source(self):
        path = self._pick_pdf()
        if not path:
            return
        count, password = self._load_page_count_with_password_prompt(path)
        if count is None:
            return
        self.compress_source_path = path
        self.compress_source_password = password
        self.compress_source_var.set(path.name)
        self.compress_result_var.set("")

    def _compress_run(self):
        if not self.compress_source_path:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord un fichier PDF.")
            return
        output = self._save_pdf_as("compresse.pdf")
        if not output:
            return
        if not self._warn_if_output_overwrites_source(self.compress_source_path, output):
            return

        def action():
            # Lues ici (dans action(), execute par _run_safely) et non avant :
            # un champ non numerique leverait TclError, qui doit etre
            # capturee par _run_safely plutot que de faire planter le
            # callback silencieusement.
            quality = self.compress_quality_var.get()
            max_dim = self.compress_max_dim_var.get()
            return ops.compress_pdf(
                self.compress_source_path, output, image_quality=quality,
                max_dimension=max_dim, password=self.compress_source_password,
            )

        result = self._run_safely(action)
        if result is not None:
            self.compress_result_var.set(
                f"{_format_size(result.original_size)} -> {_format_size(result.compressed_size)} "
                f"({result.ratio_percent:g} % de reduction)"
            )
            messagebox.showinfo(APP_TITLE, f"PDF compresse enregistre : {output.name}")

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

        img_to_pdf = ttk.LabelFrame(frame, text="Images vers PDF")
        img_to_pdf.pack(fill=BOTH, expand=True, padx=10, pady=10)
        self.i2p_files: list = []

        body = ttk.Frame(img_to_pdf)
        body.pack(fill=BOTH, expand=True, padx=5, pady=5)
        self.i2p_listbox = ttk_listbox(body)
        self.i2p_listbox.pack(side=LEFT, fill=BOTH, expand=True)

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
        self.watermark_source_path = None
        self.watermark_source_password = None
        self.watermark_source_var = StringVar(value="Aucun fichier choisi")
        self.watermark_text_var = StringVar(value="CONFIDENTIEL")
        self.watermark_opacity_var = IntVar(value=30)
        self.watermark_angle_var = IntVar(value=45)
        self.watermark_size_var = IntVar(value=40)

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        ttk.Button(top, text="Choisir un PDF...", command=self._watermark_pick_source).pack(side=LEFT)
        ttk.Label(top, textvariable=self.watermark_source_var).pack(side=LEFT, padx=10)

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

    def _watermark_pick_source(self):
        path = self._pick_pdf()
        if not path:
            return
        count, password = self._load_page_count_with_password_prompt(path)
        if count is None:
            return
        self.watermark_source_path = path
        self.watermark_source_password = password
        self.watermark_source_var.set(path.name)

    def _watermark_run(self):
        if not self.watermark_source_path:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord un fichier PDF.")
            return
        text = self.watermark_text_var.get().strip()
        if not text:
            messagebox.showwarning(APP_TITLE, "Le texte du filigrane ne peut pas etre vide.")
            return
        output = self._save_pdf_as("filigrane.pdf")
        if not output:
            return
        if not self._warn_if_output_overwrites_source(self.watermark_source_path, output):
            return

        def action():
            opacity = self.watermark_opacity_var.get() / 100.0
            return ops.add_text_watermark(
                self.watermark_source_path, output, text,
                opacity=opacity, font_size=self.watermark_size_var.get(), angle=self.watermark_angle_var.get(),
                password=self.watermark_source_password,
            )

        self._run_safely(action, f"Filigrane applique : {output.name}")

    # -- onglet Protection ------------------------------------------------------------

    def _build_protect_tab(self):
        frame = self.protect_tab
        self.protect_source_path = None
        self.protect_source_var = StringVar(value="Aucun fichier choisi")
        self.protect_mode_var = StringVar(value="add")
        self.protect_password_var = StringVar()
        self.protect_confirm_var = StringVar()

        top = ttk.Frame(frame)
        top.pack(fill=X, padx=10, pady=10)
        ttk.Button(top, text="Choisir un PDF...", command=self._protect_pick_source).pack(side=LEFT)
        ttk.Label(top, textvariable=self.protect_source_var).pack(side=LEFT, padx=10)

        ttk.Radiobutton(frame, text="Ajouter un mot de passe", variable=self.protect_mode_var, value="add").pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Radiobutton(frame, text="Retirer le mot de passe", variable=self.protect_mode_var, value="remove").pack(anchor="w", padx=10)

        ttk.Label(frame, text="Mot de passe").pack(anchor="w", padx=10, pady=(10, 0))
        ttk.Entry(frame, textvariable=self.protect_password_var, show="*", width=30).pack(anchor="w", padx=10)

        self.protect_confirm_label = ttk.Label(frame, text="Confirmer le mot de passe")
        self.protect_confirm_label.pack(anchor="w", padx=10, pady=(10, 0))
        self.protect_confirm_entry = ttk.Entry(frame, textvariable=self.protect_confirm_var, show="*", width=30)
        self.protect_confirm_entry.pack(anchor="w", padx=10)

        ttk.Button(frame, text="Appliquer...", command=self._protect_run).pack(anchor="w", padx=10, pady=15)

    def _protect_pick_source(self):
        path = self._pick_pdf()
        if not path:
            return
        self.protect_source_path = path
        self.protect_source_var.set(path.name)

    def _protect_run(self):
        if not self.protect_source_path:
            messagebox.showwarning(APP_TITLE, "Choisissez d'abord un fichier PDF.")
            return
        password = self.protect_password_var.get()
        mode = self.protect_mode_var.get()

        if mode == "add":
            if password != self.protect_confirm_var.get():
                messagebox.showwarning(APP_TITLE, "Les deux mots de passe ne correspondent pas.")
                return
            output = self._save_pdf_as("protege.pdf")
            if not output:
                return
            if not self._warn_if_output_overwrites_source(self.protect_source_path, output):
                return
            self._run_safely(
                lambda: ops.set_password(self.protect_source_path, output, password),
                f"PDF protege enregistre : {output.name}",
            )
        else:
            output = self._save_pdf_as("sans_mot_de_passe.pdf")
            if not output:
                return
            if not self._warn_if_output_overwrites_source(self.protect_source_path, output):
                return
            self._run_safely(
                lambda: ops.remove_password(self.protect_source_path, output, password),
                f"Protection retiree : {output.name}",
            )

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

        body = ttk.Frame(frame)
        body.pack(fill=BOTH, expand=True, padx=10, pady=5)
        scrollbar = ttk.Scrollbar(body, orient=VERTICAL)
        from tkinter import Text
        self.text_output = Text(body, wrap="word", yscrollcommand=scrollbar.set)
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


def ttk_listbox(parent, height=12):
    from tkinter import Listbox

    listbox = Listbox(parent, height=height, exportselection=False)
    return listbox


def main():
    root = Tk()
    PdfAtelierApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
