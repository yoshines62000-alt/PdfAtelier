# PdfAtelier

[![Dernière version](https://img.shields.io/github/v/release/yoshines62000-alt/PdfAtelier?label=derni%C3%A8re%20version)](https://github.com/yoshines62000-alt/PdfAtelier/releases/latest)
[![Téléchargements](https://img.shields.io/github/downloads/yoshines62000-alt/PdfAtelier/total?label=t%C3%A9l%C3%A9chargements)](https://github.com/yoshines62000-alt/PdfAtelier/releases/latest)

**[⬇️ Télécharger l'exécutable (.exe) — aucune installation requise](https://github.com/yoshines62000-alt/PdfAtelier/releases/latest)**

Boîte à outils PDF complète — fusionner, diviser, réorganiser, compresser,
convertir, protéger — gratuite, open source, et **100 % locale**. Alternative
libre à [Adobe Acrobat Pro](https://www.adobe.com/acrobat/pricing.html)
(environ 240 $/an) ou aux convertisseurs en ligne payants comme
[Smallpdf](https://smallpdf.com/pricing) / [iLovePDF](https://www.ilovepdf.com/)
(environ 108-120 $/an), qui en plus d'être payants pour un usage régulier,
obligent à **envoyer vos documents sur un serveur tiers** — un vrai risque
pour des contrats, pièces d'identité, factures ou tout document sensible.
Avec PdfAtelier, aucun fichier ne quitte jamais votre ordinateur.

## Fonctionnalités

- **Fusionner** plusieurs PDF en un seul, dans l'ordre de votre choix.
- **Diviser** un PDF par plages de pages (ex : `1-3,5,7-9`) ou toutes les N
  pages.
- **Gérer les pages** : réorganiser, faire pivoter, supprimer des pages
  individuellement, avec aperçu miniature de chaque page.
- **Compresser** : réduit la taille en recompressant les images embarquées
  (qualité et dimension maximale réglables), utile pour l'envoi par email.
- **Convertir** : PDF vers images (PNG/JPG, résolution réglable) et images
  vers PDF (assemblage d'une série de photos/scans en un seul document).
- **Filigrane** : appliquer un texte en surimpression sur toutes les pages
  (ex : "CONFIDENTIEL", "BROUILLON"), avec opacité/angle/taille réglables.
- **Numéroter les pages** : ajouter un numéro de page (position, format et
  point de départ réglables) sur toutes les pages.
- **Protection par mot de passe** : chiffrer ou déchiffrer un PDF.
- **Extraction de texte** : récupérer le texte brut de chaque page pour le
  copier ou le rechercher.
- **Extraire les images embarquées** : récupérer les photos/logos tels
  qu'intégrés dans le PDF, sans avoir à rastériser la page entière.
- **Extraire les pièces jointes** : récupérer les fichiers embarqués dans le
  PDF (XML de facture électronique, images, autres PDF...).
- **Métadonnées / Propriétés** : consulter, modifier ou purger le titre,
  l'auteur, le sujet et les mots-clés d'un PDF.
- **100 % local, zéro cloud** : chaque operation se fait entièrement sur
  votre machine. Aucun compte, aucune connexion internet requise, aucune
  limite d'usage.
- **Gratuit et open source, pour toujours** : pas de version payante, pas de
  fonctionnalité verrouillée derrière un abonnement.

## Démarrage rapide

1. [**Téléchargez `PdfAtelier.exe`**](https://github.com/yoshines62000-alt/PdfAtelier/releases/latest)
   depuis la dernière release.
2. Double-cliquez dessus : la fenêtre de l'application s'ouvre directement,
   sans installation, sans Python.

L'exécutable n'étant pas signé numériquement, Windows SmartScreen peut
afficher un avertissement au premier lancement : cliquez sur **Informations
complémentaires** puis **Exécuter quand même**.

## Lancer depuis le code source

Alternative à l'exécutable, pour les développeurs ou par souci de
transparence (voir [Installation](#installation) pour les dépendances) :
double-cliquez sur **[`Lancer.vbs`](Lancer.vbs)** — la fenêtre s'ouvre
directement, sans console. Vous pouvez créer un raccourci sur le Bureau (clic
droit sur `Lancer.vbs` → Envoyer vers → Bureau) pour un accès en un clic.

## Installation

Nécessite Python 3.9+ avec Tkinter (inclus dans les installations standard de
Python sous Windows), plus quelques dépendances légères :

```bash
python -m pip install -r requirements.txt
```

- **[pypdf](https://pypdf.readthedocs.io/)** : fusion, division, gestion des
  pages, compression, filigrane, protection par mot de passe.
- **[pypdfium2](https://pypdfium2.readthedocs.io/)** : rendu des pages PDF en
  images (aperçus miniatures, export PDF → images).
- **[reportlab](https://www.reportlab.com/opensource/)** : génération du
  texte de filigrane superposé sur chaque page.
- **[Pillow](https://python-pillow.org/)** : manipulation d'images (export,
  assemblage images → PDF, recompression lors de la compression).

## Utilisation

Chaque fonctionnalité a son propre onglet : choisissez un ou plusieurs
fichiers via le bouton dédié, réglez les options si besoin, puis cliquez sur
le bouton d'action (Fusionner, Diviser, Compresser...) et choisissez où
enregistrer le résultat. L'onglet **Pages** affiche un aperçu miniature de la
page sélectionnée pour vérifier avant d'enregistrer.

## Confidentialité

- Aucune donnée ne quitte votre machine : pas de compte, pas de serveur, pas
  de télémétrie, pas d'upload vers un service en ligne.
- Toutes les opérations (y compris la compression et la conversion) sont
  effectuées par des bibliothèques Python locales, jamais par un appel
  réseau.

## Limites connues

### Robustesse face à un PDF malveillant

« 100 % local, zéro cloud » garantit la confidentialité de vos fichiers, mais
ne signifie pas que PdfAtelier est totalement à l'abri de n'importe quel PDF.
L'application ne fait ni analyse antivirus ni bac à sable (sandboxing) du
contenu ouvert : la robustesse face à un PDF réellement malveillant dépend
entièrement des bibliothèques tierces utilisées pour l'analyser et le rendre
(`pypdf`, `pypdfium2`, `Pillow`, `reportlab`). Aucune exécution de contenu
actif embarqué (JavaScript, macros) n'a été identifiée dans le périmètre de
PdfAtelier — ni `pypdf` (manipulation structurelle du PDF) ni le moteur de
rendu `pypdfium2` n'exécutent de scripts embarqués. Des protections contre
les fichiers piégés sont en place (limite anti-bombe-de-décompression sur les
images embarquées, plafond de mégapixels avant le rendu d'une page à
`/MediaBox` démesuré), mais aucun logiciel ne peut garantir l'absence
totale de vulnérabilité de déni de service dans ses dépendances. Par
précaution, gardez la même vigilance qu'avec tout fichier téléchargé :
évitez d'ouvrir un PDF de provenance totalement inconnue sans un minimum de
prudence, et gardez vos dépendances à jour si vous lancez PdfAtelier depuis
le code source.

## Créer un exécutable autonome (.exe)

Pour distribuer l'outil sans que le destinataire ait besoin d'installer
Python ni les dépendances, un exécutable Windows autonome peut être généré
avec [PyInstaller](https://pyinstaller.org/) :

```bash
python -m pip install pyinstaller
python -m PyInstaller PdfAtelier.spec
```

L'exécutable est produit dans `dist/PdfAtelier.exe` (fichier unique, sans
console). Le fichier `.spec` du dépôt fixe la configuration de build pour un
résultat reproductible. Les dossiers `build/` et `dist/` ne sont pas suivis
par Git.

## Tests

Une suite de tests automatisés couvre toute la logique de `pdf_ops.py` sur de
vrais fichiers PDF/images générés sur disque (fusion, division, gestion des
pages, compression avec réduction de taille vérifiée, conversion, filigrane,
protection par mot de passe, extraction de texte).

```bash
python -m unittest discover tests -v
```

## Structure du projet

```
pdf_ops.py            # toute la logique PDF (pure, testable sans GUI)
gui.py                 # interface graphique Tkinter (10 onglets)
tests/                 # tests automatises
requirements.txt      # dependances (pypdf, pypdfium2, reportlab, Pillow)
Lancer.vbs            # raccourci de lancement double-clic (sans console)
Lancer.bat            # raccourci de lancement double-clic (avec console, pour debug)
PdfAtelier.spec       # configuration de build PyInstaller (.exe autonome)
icon.ico              # icone de l'application et de l'executable
.gitignore
LICENSE               # licence MIT
README.md
```

## Licence

Ce projet est publié sous licence [MIT](LICENSE) : gratuit, open source, et
libre de réutilisation, modification et redistribution.

## Soutenir le projet

<div align="center">

**Cet outil est gratuit, open source, et le restera toujours.**
Pas de version payante, pas de fonctionnalité cachée derrière un paywall.

Si PdfAtelier vous evite un abonnement Acrobat ou Smallpdf, un petit café
est toujours très apprécié. 🙌

[![Offrez-moi un café sur Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/yoshines62000)

</div>
